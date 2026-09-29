"""Bounded local document campaigns; oracle contents stay outside extraction.

Schema 0.2 adds explicit case/repeat identity, complete planned-run accounting,
and denominators. Repeat success describes document handling, never ERP entry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import Field, TypeAdapter

from brain.runtime.storage import RunLog, atomic_write_json
from models.factory import create_model_client

from .accounting import read_model_accounting
from .evaluate import evaluate
from .extract import PipelineCallBudget
from .runner import PipelineResult, load_inputs, prepare_case
from .schemas import FIELD_NAMES, ExtractionBundle, FrozenModel, PipelineSettings


class EvaluationCase(FrozenModel):
    scenario: str = Field(min_length=1)
    manifest: Path
    truth: Path


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize(rows: list[dict[str, Any]], *, repeats: int) -> dict[str, Any]:
    """Keep missing outcomes in planned denominators, with coverage reported.

    Canonical fields are the public ten-field contract for every planned run.
    Document-field and anomaly totals need oracle data and therefore report
    evaluated-only rates separately when a campaign is incomplete.
    """
    evaluated = [row for row in rows if row.get("evaluated")]
    correction_evaluable = [row for row in evaluated if row.get("correction_evaluable")]
    matched_fields = sum(row.get("matched_fields", 0) for row in evaluated)
    document_fields = sum(len(row["document_field_matches"]) for row in evaluated)
    matched_document_fields = sum(sum(row["document_field_matches"].values()) for row in evaluated)
    expected_anomalies = sum(row["expected_anomalies"] for row in evaluated)
    detected_anomalies = sum(row["detected_anomalies"] for row in evaluated)
    correction_matches = sum(row["correction_decision_matches"] is True for row in evaluated)
    cases = sorted({row["index"] for row in rows})
    groups = [[row for row in rows if row["index"] == index] for index in cases]
    repeat_metrics = {}
    for metric in ("extraction_success", "validation_success", "expected_outcome_success"):
        all_repeats = sum(
            len(group) == repeats and all(row.get(metric) is True for row in group)
            for group in groups
        )
        first_three = (
            sum(
                all(row.get(metric) is True for row in group[:3]) and len(group[:3]) == 3
                for group in groups
            )
            if repeats >= 3
            else None
        )
        repeat_metrics[metric] = {
            "case_count": len(cases),
            "all_repeats_successes": all_repeats,
            "all_repeats_success_rate": _rate(all_repeats, len(cases)),
            "three_consecutive_successes": first_three,
            "three_consecutive_success_rate": (
                _rate(first_three, len(cases)) if first_three is not None else None
            ),
        }
    all_evaluated = bool(rows) and len(evaluated) == len(rows)
    return {
        "planned_cases": len(cases),
        "repeats": repeats,
        "planned_runs": len(rows),
        "terminal_runs": sum(row["status"] not in {"planned", "running"} for row in rows),
        "attempted_runs": sum(row["attempted"] for row in rows),
        "evaluated_runs": len(evaluated),
        "evaluation_coverage": _rate(len(evaluated), len(rows)),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "field_count": len(rows) * len(FIELD_NAMES),
        "matched_fields": matched_fields,
        "field_accuracy": _rate(matched_fields, len(rows) * len(FIELD_NAMES)),
        "evaluated_field_count": len(evaluated) * len(FIELD_NAMES),
        "field_accuracy_evaluated": _rate(matched_fields, len(evaluated) * len(FIELD_NAMES)),
        "evaluated_document_field_count": document_fields,
        "matched_document_fields": matched_document_fields,
        "document_field_accuracy": (
            _rate(matched_document_fields, document_fields) if all_evaluated else None
        ),
        "document_field_accuracy_evaluated": _rate(matched_document_fields, document_fields),
        "expected_anomalies_evaluated": expected_anomalies,
        "detected_anomalies": detected_anomalies,
        "unexpected_anomalies": sum(row["unexpected_anomalies"] for row in evaluated),
        "anomaly_recall": _rate(detected_anomalies, expected_anomalies) if all_evaluated else None,
        "anomaly_recall_evaluated": _rate(detected_anomalies, expected_anomalies),
        "correction_decision_count": len(rows),
        "correct_correction_decisions": correction_matches,
        "correction_decision_accuracy": _rate(correction_matches, len(rows)),
        "correction_evaluable_runs": len(correction_evaluable),
        "correction_decision_accuracy_evaluable": _rate(
            correction_matches, len(correction_evaluable)
        ),
        "repeat_success": repeat_metrics,
        "documents_to_erp_success": None,
        "metric_definitions": {
            "field_accuracy": "Matched canonical fields / (10 * all planned runs).",
            "correction_decision_accuracy": "Correct completed decisions / all planned runs; "
            "processing errors and unevaluated runs receive no credit.",
            "evaluated_rates": "Document-field/anomaly denominators require oracle data. "
            "Evaluated-only rates must be read with evaluation_coverage.",
            "extraction_success": "All ten canonical fields match and processing completed.",
            "validation_success": "Processing completed, correction decision matches, and "
            "all expected anomaly kinds/severities match with no unexpected anomalies.",
            "expected_outcome_success": "Validation succeeds and the expected review gate "
            "is respected; ready cases also require all ten canonical fields to match. "
            "An expected missing-document halt can succeed without an extracted record.",
            "three_consecutive_success_rate": "Cases whose first three planned repeats "
            "all succeed / all planned cases; null when fewer than three repeats were planned.",
            "all_repeats_success_rate": "Cases with every planned repeat successful / "
            "all planned cases. This does not measure ERP entry or milestone completion.",
        },
    }


def _account_cost(row: dict[str, Any], folder: Path, result: PipelineResult | None) -> None:
    """Recover measured costs even when preparation raises or is interrupted."""
    if not row.get("preparation_started"):
        return
    try:
        if not (folder / "run_log.jsonl").is_file():
            raise ValueError("missing_run_log")
        accounting = read_model_accounting(
            RunLog(folder, case_id=row["case_id"], agent_id="pipeline")
        )
        row["known_cost_usd"] = float(accounting.known_usd)
        row["unknown_cost"] = accounting.unknown
        row["model_calls"] = accounting.model_calls
        if result is not None and (
            result.known_cost_usd != row["known_cost_usd"]
            or result.unknown_cost != row["unknown_cost"]
        ):
            row["known_cost_usd"] = max(row["known_cost_usd"], result.known_cost_usd)
            row["unknown_cost"] = True
            row["cost_error_type"] = "ModelAccountingMismatch"
    except Exception as exc:
        row["known_cost_usd"] = result.known_cost_usd if result else 0.0
        row["unknown_cost"] = True
        row["cost_error_type"] = type(exc).__name__


def _evaluate_result(row: dict[str, Any], item: EvaluationCase, result: PipelineResult) -> None:
    # Only called after prepare_case returns. Neither truth path nor contents
    # is supplied to OCR, extraction, or model_factory.
    truth_bytes = item.truth.read_bytes()
    truth = json.loads(truth_bytes)
    if not isinstance(truth["expected_needs_correction"], bool):
        raise ValueError("invalid_expected_correction_decision")
    extracted = ExtractionBundle.model_validate_json(
        (result.case_dir / "extracted.json").read_bytes()
    )
    metrics = evaluate(result.record, result.checks, truth, extracted=extracted)
    row.update(metrics)
    row["schema_version"] = "0.2"
    row["truth_sha256"] = hashlib.sha256(truth_bytes).hexdigest()
    row["evaluated"] = True
    completed = result.error_type is None and not row["unknown_cost"]
    row["correction_evaluable"] = completed
    row["raw_correction_decision_matches"] = metrics["correction_decision_matches"]
    row["correction_decision_matches"] = (
        metrics["correction_decision_matches"] if completed else None
    )
    row["extraction_success"] = completed and metrics["matched_fields"] == len(FIELD_NAMES)
    row["validation_success"] = (
        completed
        and metrics["correction_decision_matches"]
        and metrics["missed_anomalies"] == 0
        and metrics["unexpected_anomalies"] == 0
    )
    row["expected_outcome_success"] = row["validation_success"] and (
        not metrics["ready"]
        if truth["expected_needs_correction"]
        else row["extraction_success"] and metrics["ready"]
    )


def run_campaign(
    *,
    evaluation_manifest: Path,
    output: Path,
    config: Path,
    model_config: Path,
    env_path: Path | None,
    repeats: int = 3,
    max_cost_usd: float = 1.0,
) -> dict[str, Any]:
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 100:
        raise ValueError("repeats_must_be_an_integer_between_one_and_100")
    if not math.isfinite(max_cost_usd) or not 0 < max_cost_usd <= 10:
        raise ValueError("campaign_budget_must_be_between_zero_and_ten_dollars")
    evaluation_manifest = evaluation_manifest.resolve()
    manifest_bytes = evaluation_manifest.read_bytes()
    items = TypeAdapter(list[EvaluationCase]).validate_json(manifest_bytes)
    if not items:
        raise ValueError("empty_evaluation_manifest")
    items = [
        item.model_copy(
            update={
                "manifest": (evaluation_manifest.parent / item.manifest).resolve(),
                "truth": (evaluation_manifest.parent / item.truth).resolve(),
            }
        )
        for item in items
    ]
    settings = PipelineSettings.load(config)
    reservation = PipelineCallBudget.from_config(model_config)
    metadata = {
        "schema_version": "0.2",
        "evaluation_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "model_config_sha256": hashlib.sha256(model_config.read_bytes()).hexdigest(),
        "max_cost_usd": max_cost_usd,
        "per_run_cost_reservation_usd": settings.max_cost_usd,
        "run_order": "case_then_repeat",
    }
    output.mkdir(parents=True, exist_ok=False)
    rows = [
        {
            "schema_version": "0.2",
            "index": index,
            "repeat": repeat,
            "scenario": item.scenario,
            "directory": f"case-{index:03d}/repeat-{repeat:03d}",
            "manifest": str(item.manifest),
            "status": "planned",
            "attempted": False,
            "evaluated": False,
            "known_cost_usd": 0.0,
            "unknown_cost": False,
            "model_calls": 0,
        }
        for index, item in enumerate(items)
        for repeat in range(1, repeats + 1)
    ]
    started = perf_counter()
    stop_reason = None
    summary: dict[str, Any] = {}

    def persist() -> None:
        nonlocal summary
        summary = {
            **metadata,
            **summarize(rows, repeats=repeats),
            "known_cost_usd": float(
                sum((Decimal(str(row["known_cost_usd"])) for row in rows), start=Decimal(0))
            ),
            "unknown_cost": any(row["unknown_cost"] for row in rows),
            "stop_reason": stop_reason,
            "elapsed_ms": (perf_counter() - started) * 1000,
            "rows": rows,
        }
        atomic_write_json(output / "summary.json", summary)

    persist()
    limit = Decimal(str(max_cost_usd))
    reserved = Decimal(str(settings.max_cost_usd))
    for row in rows:
        folder = output / row["directory"]
        total = sum((Decimal(str(item["known_cost_usd"])) for item in rows), start=Decimal(0))
        if stop_reason is None and total + reserved > limit:
            stop_reason = "budget"
        if stop_reason:
            row["status"] = f"not_run_{stop_reason}"
        else:
            row.update(status="running", attempted=True)
            persist()
            result = None
            before = perf_counter()
            phase = "input"
            item = items[row["index"]]
            try:
                row["input_manifest_sha256"] = hashlib.sha256(
                    item.manifest.read_bytes()
                ).hexdigest()
                case_id, documents = load_inputs(item.manifest)
                row["case_id"] = case_id
                phase = "preparation"
                row["preparation_started"] = True
                result = prepare_case(
                    folder,
                    case_id=case_id,
                    documents=documents,
                    settings=settings,
                    model_factory=lambda log: create_model_client(
                        model_config, run_log=log, env_path=env_path
                    ),
                    reservation=reservation,
                )
                row["extraction_ms"] = (perf_counter() - before) * 1000
                row["error_type"] = result.error_type
                phase = "evaluation"
                _account_cost(row, folder, result)
                _evaluate_result(row, item, result)
                row["status"] = "pipeline_failed" if result.error_type else "measured"
            except (KeyboardInterrupt, SystemExit) as exc:
                row.update(status="cancelled", error_type=type(exc).__name__, error_phase=phase)
                stop_reason = "cancelled"
            except Exception as exc:
                row.update(
                    status=f"{phase}_failed", error_type=type(exc).__name__, error_phase=phase
                )
            finally:
                row["elapsed_ms"] = (perf_counter() - before) * 1000
                _account_cost(row, folder, result)
            if row["unknown_cost"]:
                stop_reason = stop_reason or "unknown_cost"
            elif Decimal(str(row["known_cost_usd"])) > reserved or (
                total + Decimal(str(row["known_cost_usd"])) > limit
            ):
                stop_reason = stop_reason or "cost_overrun"
            if row["status"] != "measured" or row["unknown_cost"] or stop_reason == "cost_overrun":
                # Evaluation may have populated metrics before an interruption.
                # Keep observed fields, but never credit a cancelled decision.
                row["correction_evaluable"] = False
                row["correction_decision_matches"] = None
                row["extraction_success"] = False
                row["validation_success"] = False
                row["expected_outcome_success"] = False
                if row["status"] == "measured":
                    row["status"] = "cost_failed"
        atomic_write_json(folder / "evaluation.json", row)
        persist()
        print(
            json.dumps(
                {
                    key: row.get(key)
                    for key in (
                        "index",
                        "repeat",
                        "scenario",
                        "status",
                        "ready",
                        "matched_fields",
                        "detected_anomalies",
                        "expected_anomalies",
                        "known_cost_usd",
                        "error_type",
                    )
                }
            ),
            flush=True,
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/pipeline.toml"))
    parser.add_argument("--model-config", type=Path, default=Path("config/models.lite.toml"))
    parser.add_argument("--env", type=Path)
    parser.add_argument(
        "--repeats", type=int, default=3, help="Repeats per case; use 1 for smoke runs"
    )
    parser.add_argument("--max-cost-usd", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_cost_usd) or not 0 < args.max_cost_usd <= 10:
        parser.error("Campaign budget must be between zero and ten dollars")
    if not 1 <= args.repeats <= 100:
        parser.error("Repeats must be an integer between one and 100")
    summary = run_campaign(
        evaluation_manifest=args.evaluation_manifest,
        output=args.output,
        config=args.config,
        model_config=args.model_config,
        env_path=args.env,
        repeats=args.repeats,
        max_cost_usd=args.max_cost_usd,
    )
    if summary["stop_reason"] == "cancelled":
        return 130
    return (
        0
        if all(row["status"] == "measured" for row in summary["rows"])
        and not summary["unknown_cost"]
        and summary["stop_reason"] is None
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
