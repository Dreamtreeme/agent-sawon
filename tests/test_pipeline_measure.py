"""Campaign accounting uses fake measured clients, never OCR or live models."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sawon_interfaces import ShipmentRecord

from models.base import MeasuredModelClient, ProviderReply, TokenUsage
from pipeline import measure
from pipeline.runner import PipelineResult
from pipeline.schemas import Check, CheckReport, ExtractionBundle, PageExtraction
from sawon_runtime.storage import RunLog, atomic_write_json


class FakeMeasuredClient(MeasuredModelClient):
    def __init__(self, log: RunLog, outcome: str, cost: float):
        super().__init__(provider="fake", model="offline-test", run_log=log)
        self.outcome, self.cost = outcome, cost

    def _generate(self, prompt, schema, *, images=None):
        if self.outcome == "cancel":
            raise KeyboardInterrupt
        if self.outcome == "unknown":
            raise RuntimeError("A private error must not be copied into campaign artifacts")
        return ProviderReply(
            text='{"fields": []}', usage=TokenUsage(input_tokens=1, response_tokens=1)
        )

    def _cost(self, usage):
        return Decimal(str(self.cost))


def _record() -> ShipmentRecord:
    values = {
        "hbl_no": "SYNTHETIC1",
        "mbl_no": "MASTER1",
        "shipper": "Synthetic shipper",
        "consignee": "Synthetic consignee",
        "description": "Machine parts",
        "packages": {"count": 12, "unit": "CT"},
        "gross_weight_kg": 1000,
        "container_no": "CSQU3054383",
        "pol": "CNSHA",
        "pod": "KRPUS",
    }
    return ShipmentRecord(
        case_id="same-id-across-scenarios",
        origin="extracted",
        fields={name: {"value": value} for name, value in values.items()},
    )


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    config = tmp_path / "pipeline.toml"
    config.write_text("max_cost_usd = 0.1\n", encoding="utf-8")
    model_config = tmp_path / "models.toml"
    model_config.write_text('provider = "replay"\n', encoding="utf-8")
    state: dict[str, Any] = {"calls": [], "outcomes": [], "cost": 0.01, "in_prepare": False}
    items = []
    for index in range(2):
        manifest = tmp_path / f"input-{index}.json"
        manifest.write_text(
            json.dumps({"schema_version": "1.0", "case_id": _record().case_id, "documents": []}),
            encoding="utf-8",
        )
        truth = tmp_path / f"truth-{index}.json"
        truth.write_text(
            json.dumps(
                {
                    "canonical_record": _record().model_dump(mode="json"),
                    "injections": [],
                    "expected_needs_correction": False,
                }
            ),
            encoding="utf-8",
        )
        items.append(
            {"scenario": f"scenario-{index}", "manifest": manifest.name, "truth": truth.name}
        )
    manifest_path = tmp_path / "evaluation.json"
    manifest_path.write_text(json.dumps(items), encoding="utf-8")
    arguments = {
        "evaluation_manifest": manifest_path,
        "output": tmp_path / "out",
        "config": config,
        "model_config": model_config,
        "env_path": None,
    }
    original_read = Path.read_bytes

    def read_bytes(path):
        if path.name.startswith("truth-"):
            assert not state["in_prepare"], "Oracle read before extraction ended"
            state.setdefault("truth_reads", []).append(path)
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    def factory(config_path, *, run_log, env_path):
        assert config_path == model_config and env_path is None
        return FakeMeasuredClient(run_log, state["current"], state["cost"])

    def prepare(folder, *, case_id, documents, model_factory, settings, reservation):
        state["in_prepare"] = True
        try:
            saved = json.loads((arguments["output"] / "summary.json").read_text())
            assert len(saved["rows"]) == saved["planned_runs"]
            call_index = len(state["calls"])
            outcome = state["outcomes"][call_index] if call_index < len(state["outcomes"]) else "ok"
            state["current"] = outcome
            state["calls"].append(folder)
            log = RunLog(folder, case_id=case_id, agent_id="pipeline")
            log.append("plan")
            if outcome == "raise":
                model_factory(log).complete_json("fake document input", PageExtraction)
                raise ValueError("private failure content")
            error = None
            try:
                model_factory(log).complete_json("fake document input", PageExtraction)
            except RuntimeError as exc:
                error = type(exc).__name__
            if outcome == "pipeline_error":
                error = "PipelineDependencyError"
            checks = CheckReport(
                case_id=case_id,
                field_confidences={},
                checks=[Check(code="pipeline_error", severity="blocking", message="failed")]
                if error
                else [],
            )
            atomic_write_json(
                folder / "extracted.json", ExtractionBundle(case_id=case_id, pages=[])
            )
            return PipelineResult(
                case_id=case_id,
                case_dir=folder,
                ready=error is None,
                record=_record() if error is None else None,
                checks=checks,
                known_cost_usd=0.0 if outcome == "unknown" else state["cost"],
                unknown_cost=outcome == "unknown",
                error_type=error,
            )
        finally:
            state["in_prepare"] = False

    monkeypatch.setattr(measure, "prepare_case", prepare)
    monkeypatch.setattr(measure, "create_model_client", factory)
    return arguments, state


def test_default_three_repeats_have_fresh_stable_paths_and_separate_scenarios(campaign):
    args, state = campaign
    summary = measure.run_campaign(**args)
    assert summary["schema_version"] == "0.2"
    assert summary["planned_cases"] == 2
    assert summary["planned_runs"] == summary["evaluated_runs"] == 6
    assert [path.relative_to(args["output"]).as_posix() for path in state["calls"]] == [
        f"case-{index:03d}/repeat-{repeat:03d}" for index in range(2) for repeat in range(1, 4)
    ]
    assert len(state["truth_reads"]) == 6
    assert summary["known_cost_usd"] == 0.06
    assert summary["field_count"] == 60 and summary["field_accuracy"] == 1
    assert summary["correct_correction_decisions"] == 6
    assert summary["repeat_success"]["expected_outcome_success"]["case_count"] == 2
    assert (
        summary["repeat_success"]["expected_outcome_success"]["three_consecutive_success_rate"] == 1
    )
    assert summary["documents_to_erp_success"] is None
    for row in summary["rows"]:
        saved = json.loads((args["output"] / row["directory"] / "evaluation.json").read_text())
        assert saved == row
    with pytest.raises(FileExistsError):
        measure.run_campaign(**args)


def test_single_repeat_smoke_does_not_claim_three_consecutive_success(campaign):
    args, _ = campaign
    summary = measure.run_campaign(**args, repeats=1)
    success = summary["repeat_success"]["expected_outcome_success"]
    assert success["all_repeats_success_rate"] == 1
    assert success["three_consecutive_success_rate"] is None


def test_failed_pipeline_cannot_get_credit_from_generic_blocking_check(campaign):
    args, state = campaign
    state["outcomes"] = ["pipeline_error", "ok"]
    truth = args["evaluation_manifest"].parent / "truth-0.json"
    data = json.loads(truth.read_text())
    data["expected_needs_correction"] = True
    truth.write_text(json.dumps(data), encoding="utf-8")
    summary = measure.run_campaign(**args, repeats=1)
    failed = summary["rows"][0]
    assert failed["status"] == "pipeline_failed"
    assert failed["raw_correction_decision_matches"] is True
    assert failed["correction_decision_matches"] is None
    assert summary["correction_decision_count"] == 2
    assert summary["correction_evaluable_runs"] == 1
    assert summary["correction_decision_accuracy"] == 0.5
    assert summary["correction_decision_accuracy_evaluable"] == 1
    assert summary["field_count"] == 20 and summary["field_accuracy"] == 0.5


def test_known_cost_budget_preserves_all_unstarted_items_and_denominators(campaign):
    args, state = campaign
    state["cost"] = 0.1
    summary = measure.run_campaign(**args, max_cost_usd=0.2)
    assert summary["known_cost_usd"] == 0.2
    assert summary["stop_reason"] == "budget"
    assert summary["planned_runs"] == summary["terminal_runs"] == 6
    assert summary["attempted_runs"] == 2
    assert summary["status_counts"] == {"measured": 2, "not_run_budget": 4}
    assert summary["field_count"] == 60
    assert summary["matched_fields"] == 20
    assert summary["field_accuracy"] == pytest.approx(1 / 3)
    assert summary["correction_decision_accuracy"] == pytest.approx(1 / 3)
    assert summary["evaluation_coverage"] == pytest.approx(1 / 3)
    assert summary["document_field_accuracy"] is None
    assert summary["repeat_success"]["expected_outcome_success"]["all_repeats_success_rate"] == 0
    assert len(state["truth_reads"]) == 2


def test_unknown_measured_cost_stops_before_next_attempt(campaign):
    args, state = campaign
    state["outcomes"] = ["unknown"]
    summary = measure.run_campaign(**args)
    assert len(state["calls"]) == 1
    assert summary["unknown_cost"] and summary["known_cost_usd"] == 0
    assert summary["stop_reason"] == "unknown_cost"
    assert summary["rows"][0]["model_calls"] == 1
    assert summary["status_counts"] == {"pipeline_failed": 1, "not_run_unknown_cost": 5}
    assert summary["correct_correction_decisions"] == 0
    assert "private error" not in json.dumps(summary)


def test_cancellation_preserves_ledger_and_marks_every_remaining_run(campaign):
    args, state = campaign
    state["outcomes"] = ["ok", "cancel"]
    summary = measure.run_campaign(**args)
    assert summary["known_cost_usd"] == 0.01
    assert summary["unknown_cost"]
    assert summary["stop_reason"] == "cancelled"
    assert summary["status_counts"] == {"measured": 1, "cancelled": 1, "not_run_cancelled": 4}
    assert summary["terminal_runs"] == 6
    assert len(state["truth_reads"]) == 1
    assert (
        summary["repeat_success"]["expected_outcome_success"]["three_consecutive_success_rate"] == 0
    )


def test_cancellation_after_evaluation_never_retains_decision_credit(campaign, monkeypatch):
    args, state = campaign
    original_evaluate = measure._evaluate_result

    def interrupted_evaluate(row, item, result):
        original_evaluate(row, item, result)
        assert row["correction_decision_matches"] and row["expected_outcome_success"]
        raise KeyboardInterrupt

    monkeypatch.setattr(measure, "_evaluate_result", interrupted_evaluate)
    summary = measure.run_campaign(**args, repeats=1)
    first = summary["rows"][0]
    assert first["status"] == "cancelled" and first["evaluated"]
    assert first["matched_fields"] == 10
    assert not first["correction_evaluable"]
    assert first["correction_decision_matches"] is None
    assert not first["extraction_success"]
    assert not first["validation_success"]
    assert not first["expected_outcome_success"]
    assert summary["correct_correction_decisions"] == 0
    assert summary["repeat_success"]["expected_outcome_success"]["all_repeats_success_rate"] == 0
    assert summary["known_cost_usd"] == 0.01 and not summary["unknown_cost"]
    assert len(state["calls"]) == 1


def test_unhandled_preparation_failure_keeps_cost_and_continues_known_budget(campaign):
    args, state = campaign
    state["outcomes"] = ["raise", "ok"]
    summary = measure.run_campaign(**args, repeats=1)
    assert summary["rows"][0]["status"] == "preparation_failed"
    assert summary["rows"][0]["known_cost_usd"] == 0.01
    assert summary["known_cost_usd"] == 0.02
    assert not summary["unknown_cost"]
    assert summary["field_accuracy"] == 0.5
    assert summary["field_accuracy_evaluated"] == 1
    assert "private failure" not in json.dumps(summary)


@pytest.mark.parametrize("phase", ["input", "evaluation"])
def test_bad_input_or_oracle_is_retained_as_failure(campaign, phase):
    args, state = campaign
    path = args["evaluation_manifest"].parent / (
        "input-0.json" if phase == "input" else "truth-0.json"
    )
    path.write_text("invalid json", encoding="utf-8")
    summary = measure.run_campaign(**args, repeats=1)
    assert summary["rows"][0]["status"] == f"{phase}_failed"
    assert summary["terminal_runs"] == 2
    assert summary["field_accuracy"] == 0.5
    assert summary["known_cost_usd"] == (0.01 if phase == "input" else 0.02)
    assert len(state["calls"]) == (1 if phase == "input" else 2)


def test_actual_reservation_overrun_stops_the_campaign(campaign):
    args, state = campaign
    state["cost"] = 0.11
    summary = measure.run_campaign(**args)
    assert len(state["calls"]) == 1
    assert summary["known_cost_usd"] == 0.11
    assert summary["stop_reason"] == "cost_overrun"
    assert not summary["rows"][0]["expected_outcome_success"]
    assert summary["rows"][0]["status"] == "cost_failed"
    assert summary["correct_correction_decisions"] == 0


def test_document_field_accuracy_is_micro_averaged_with_explicit_denominator(campaign):
    args, _ = campaign
    summary = measure.run_campaign(**args, repeats=1)
    first, second = summary["rows"]
    first["document_field_matches"] = {"hbl.hbl_no": True}
    second["document_field_matches"] = {"hbl.hbl_no": True, "hbl.mbl_no": False, "hbl.pol": False}
    metrics = measure.summarize(summary["rows"], repeats=1)
    assert metrics["evaluated_document_field_count"] == 4
    assert metrics["matched_document_fields"] == 2
    assert metrics["document_field_accuracy"] == 0.5
    second["evaluated"] = False
    metrics = measure.summarize(summary["rows"], repeats=1)
    assert metrics["document_field_accuracy"] is None
    assert metrics["document_field_accuracy_evaluated"] == 1
    assert metrics["evaluation_coverage"] == 0.5


def test_missing_document_halt_can_succeed_validation_without_claiming_extraction(
    campaign, monkeypatch
):
    args, _ = campaign
    original_prepare = measure.prepare_case

    def missing_prepare(folder, **kwargs):
        result = original_prepare(folder, **kwargs)
        checks = result.checks.model_copy(
            update={
                "checks": [Check(code="missing_document", severity="blocking", message="missing")]
            }
        )
        return result.model_copy(update={"record": None, "checks": checks, "ready": False})

    monkeypatch.setattr(measure, "prepare_case", missing_prepare)
    for index in range(2):
        path = args["evaluation_manifest"].parent / f"truth-{index}.json"
        truth = json.loads(path.read_text())
        truth["expected_needs_correction"] = True
        truth["injections"] = [{"kind": "missing_document", "expected_severity": "blocking"}]
        path.write_text(json.dumps(truth), encoding="utf-8")
    summary = measure.run_campaign(**args)
    assert summary["field_accuracy"] == 0
    assert summary["correction_decision_accuracy"] == 1
    assert summary["repeat_success"]["extraction_success"]["all_repeats_success_rate"] == 0
    assert summary["repeat_success"]["validation_success"]["all_repeats_success_rate"] == 1
    assert summary["repeat_success"]["expected_outcome_success"]["all_repeats_success_rate"] == 1


def test_missing_ledger_stops_with_unknown_cost(campaign, monkeypatch):
    args, state = campaign

    def no_log(folder, **kwargs):
        state["calls"].append(folder)
        raise RuntimeError("failed before ledger was readable")

    monkeypatch.setattr(measure, "prepare_case", no_log)
    summary = measure.run_campaign(**args)
    assert summary["stop_reason"] == "unknown_cost"
    assert summary["unknown_cost"] and len(state["calls"]) == 1
    assert summary["rows"][0]["cost_error_type"] == "ValueError"


@pytest.mark.parametrize(("reported_cost", "unknown_cost"), [(0.02, False), (0.01, True)])
def test_result_disagreeing_with_ledger_stops_before_next_attempt(
    campaign, monkeypatch, reported_cost, unknown_cost
):
    args, state = campaign
    original_prepare = measure.prepare_case

    def inconsistent_prepare(folder, **kwargs):
        result = original_prepare(folder, **kwargs)
        return result.model_copy(
            update={"known_cost_usd": reported_cost, "unknown_cost": unknown_cost}
        )

    monkeypatch.setattr(measure, "prepare_case", inconsistent_prepare)
    summary = measure.run_campaign(**args)
    assert summary["unknown_cost"] and summary["stop_reason"] == "unknown_cost"
    assert summary["known_cost_usd"] == max(state["cost"], reported_cost)
    assert len(state["calls"]) == 1
    assert summary["rows"][0]["model_calls"] == 1
    assert summary["rows"][0]["cost_error_type"] == "ModelAccountingMismatch"
    assert summary["status_counts"] == {"cost_failed": 1, "not_run_unknown_cost": 5}
    assert summary["correct_correction_decisions"] == 0


def test_first_three_and_all_repeats_cannot_pick_later_successful_retries(campaign):
    args, state = campaign
    state["outcomes"] = ["ok", "ok", "ok", "pipeline_error", "pipeline_error", "ok", "ok", "ok"]
    summary = measure.run_campaign(**args, repeats=4)
    success = summary["repeat_success"]["expected_outcome_success"]
    assert success["three_consecutive_success_rate"] == 0.5
    assert success["all_repeats_success_rate"] == 0


@pytest.mark.parametrize("repeats", [0, -1, True, 1.5, 101])
def test_invalid_repeat_count_rejected_before_output_creation(campaign, repeats):
    args, _ = campaign
    with pytest.raises(ValueError, match="repeats_must"):
        measure.run_campaign(**args, repeats=repeats)
    assert not args["output"].exists()


@pytest.mark.parametrize("budget", [0, -1, 11, float("inf"), float("nan")])
def test_invalid_budget_rejected_before_output_creation(campaign, budget):
    args, _ = campaign
    with pytest.raises(ValueError, match="campaign_budget"):
        measure.run_campaign(**args, max_cost_usd=budget)
    assert not args["output"].exists()


def test_cli_repeat_option_and_cancellation_exit_code(campaign):
    args, state = campaign
    state["outcomes"] = ["cancel"]
    code = measure.main(
        [
            "--evaluation-manifest",
            str(args["evaluation_manifest"]),
            "--output",
            str(args["output"]),
            "--config",
            str(args["config"]),
            "--model-config",
            str(args["model_config"]),
            "--repeats",
            "1",
        ]
    )
    assert code == 130
    summary = json.loads((args["output"] / "summary.json").read_text())
    assert summary["planned_runs"] == 2


def test_cli_rejects_more_than_100_repeats_before_materializing_campaign(campaign):
    args, state = campaign
    with pytest.raises(SystemExit) as error:
        measure.main(
            [
                "--evaluation-manifest",
                str(args["evaluation_manifest"]),
                "--output",
                str(args["output"]),
                "--repeats",
                "101",
            ]
        )
    assert error.value.code == 2
    assert not args["output"].exists() and not state["calls"]
