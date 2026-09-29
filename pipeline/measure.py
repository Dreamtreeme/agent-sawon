"""Explicit local development document campaign; truth is read only after extraction."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from brain.runtime.storage import atomic_write_json
from models.factory import create_model_client

from .evaluate import evaluate
from .extract import PipelineCallBudget
from .runner import load_inputs, prepare_case
from .schemas import ExtractionBundle, PipelineSettings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/pipeline.toml"))
    parser.add_argument("--model-config", type=Path, default=Path("config/models.lite.toml"))
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--max-cost-usd", type=float, default=1.0)
    args = parser.parse_args()
    if not 0 < args.max_cost_usd <= 10:
        parser.error("Campaign budget must be between zero and ten dollars")
    items = json.loads(args.evaluation_manifest.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=False)
    settings = PipelineSettings.load(args.config)
    reservation = PipelineCallBudget.from_config(args.model_config)
    rows, total, unknown = [], 0.0, False
    started = perf_counter()
    for index, item in enumerate(items):
        if unknown or total + settings.max_cost_usd > args.max_cost_usd:
            row = {"index": index, "scenario": item["scenario"], "status": "not_run_budget"}
        else:
            case_id, documents = load_inputs(Path(item["manifest"]))
            folder = args.output / f"case-{index:03d}"
            before = perf_counter()
            result = prepare_case(
                folder,
                case_id=case_id,
                documents=documents,
                settings=settings,
                model_factory=lambda log: create_model_client(
                    args.model_config, run_log=log, env_path=args.env
                ),
                reservation=reservation,
            )
            extraction_ms = (perf_counter() - before) * 1000
            # Evaluation is deliberately outside the extraction/model boundary.
            truth = json.loads(Path(item["truth"]).read_text(encoding="utf-8"))
            extracted = ExtractionBundle.model_validate_json(
                (folder / "extracted.json").read_bytes()
            )
            metrics = evaluate(result.record, result.checks, truth, extracted=extracted)
            row = {
                "index": index,
                "scenario": item["scenario"],
                "status": "measured",
                "extraction_ms": extraction_ms,
                "known_cost_usd": result.known_cost_usd,
                "unknown_cost": result.unknown_cost,
                "error_type": result.error_type,
                **metrics,
            }
            atomic_write_json(folder / "evaluation.json", row)
            total += result.known_cost_usd
            unknown |= result.unknown_cost
        rows.append(row)
        atomic_write_json(
            args.output / "summary.json",
            {
                "schema_version": "0.1",
                "planned_cases": len(items),
                "finished_cases": len(rows),
                "known_cost_usd": total,
                "unknown_cost": unknown,
                "elapsed_ms": (perf_counter() - started) * 1000,
                "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
                "model_config_sha256": hashlib.sha256(args.model_config.read_bytes()).hexdigest(),
                "rows": rows,
            },
        )
        print(
            json.dumps(
                {
                    key: row.get(key)
                    for key in (
                        "index",
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
    return 0 if all(row["status"] == "measured" for row in rows) and not unknown else 1


if __name__ == "__main__":
    raise SystemExit(main())
