"""Prepared isolation preserves extraction evidence and its share of the budget."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sawon_interfaces import EventCost, ShipmentRecord
from test_brain import FakeRelay, ScriptedModel, success_steps
from test_cache_experiment import provenance, snapshot_document
from test_pipeline_evaluate import record as extracted_record
from test_pipeline_evaluate import report as checked_report
from test_pipeline_workflow import arguments
from test_prepared_brain import execute, prepare

import brain.run as runner
from brain.cache.experiment import CacheExperiment, CacheProvenance
from brain.cache.semantic import CacheDocument
from brain.graph import Result
from brain.runtime.storage import CaseStatus, RunLog, StateStore, atomic_write_json
from brain.settings import BrainSettings, CacheSettings
from pipeline import workflow
from pipeline.checks import record_fingerprint


def cache_options(tmp_path: Path, mode: str) -> list[str]:
    options = ["--cache-isolation", mode]
    if mode == "snapshot":
        snapshot = tmp_path / "frozen.json"
        atomic_write_json(snapshot, snapshot_document())
        options += ["--cache-snapshot", str(snapshot)]
    return options


def enable_cache(monkeypatch, tmp_path):
    settings = BrainSettings(cache=CacheSettings(enabled=True, path=tmp_path / "global.json"))
    monkeypatch.setattr(runner.BrainSettings, "load", lambda _path: settings)
    return settings


@pytest.mark.parametrize("mode", ["cold", "snapshot"])
def test_prepared_isolation_finishes_real_brain_and_keeps_ocr_log(tmp_path, monkeypatch, mode):
    case, record, state, log = prepare(tmp_path)
    log.append("model_call", payload={"stage": "extraction"}, cost=EventCost(usd=0.01))
    original = log.path.read_bytes()
    options = cache_options(tmp_path, mode)
    settings = enable_cache(monkeypatch, tmp_path)
    call, relay, models = execute("cli", case, record, log, monkeypatch, options=options)
    assert call() == 0
    evidence = provenance(case)
    assert evidence.prepared_case and evidence.complete and evidence.outcome == "success"
    assert evidence.schema_version == "0.2" and evidence.preparation_log_preserved
    assert evidence.preparation_log_sha256 == hashlib.sha256(original).hexdigest()
    assert evidence.preparation_event_count == 2
    assert log.path.read_bytes().startswith(original)
    events = log.read_all()
    assert [event.seq for event in events] == list(range(len(events)))
    assert sum(event.cost.usd for event in events if event.kind == "model_call") == 0.01
    assert state.read().status == CaseStatus.AWAITING_APPROVAL
    assert relay.actions and models[0].prompts
    assert not settings.cache.path.exists()
    assert (
        len(CacheDocument.model_validate_json((case / evidence.working_path).read_bytes()).entries)
        >= 11
    )
    if mode == "snapshot":
        assert (tmp_path / "frozen.json").read_bytes() == (
            case / evidence.initial_path
        ).read_bytes()
        assert not (tmp_path / "frozen.json.lock").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "value",
        "source",
        "missing_hash",
        "blocking",
        "not_ready",
        "state_identity",
        "log_identity",
        "missing_log",
        "empty_log",
        "existing_result",
        "manual",
    ],
)
def test_invalid_prepared_isolation_does_not_read_snapshot_or_change_evidence(
    tmp_path, monkeypatch, mutation
):
    case, record, state, log = prepare(tmp_path, mutation=mutation)
    if mutation == "state_identity":
        atomic_write_json(state.path, state.read().model_copy(update={"case_id": "another-case"}))
    elif mutation == "log_identity":
        event = log.read_all()[0].model_copy(update={"case_id": "another-case"})
        log.path.write_text(event.model_dump_json() + "\n", encoding="utf-8")
    elif mutation == "missing_log":
        log.path.unlink()
    elif mutation == "empty_log":
        log.path.write_bytes(b"")
    elif mutation == "existing_result":
        (case / "result.json").write_bytes(b"prior result must be preserved")
    elif mutation == "manual":
        record.origin = "manual"
        atomic_write_json(case / "record.json", record)
    before = {path.name: path.read_bytes() for path in case.iterdir() if path.is_file()}
    enable_cache(monkeypatch, tmp_path)
    call, relay, models = execute(
        "cli",
        case,
        record,
        log,
        monkeypatch,
        options=[
            "--cache-isolation",
            "snapshot",
            "--cache-snapshot",
            str(tmp_path / "MISSING.json"),
        ],
    )
    assert call() == 2
    assert models == [] and relay.health_calls == 0 and not relay.actions
    assert not (case / "cache-experiment").exists()
    assert {path.name: path.read_bytes() for path in case.iterdir() if path.is_file()} == before


def test_prepared_isolation_never_reuses_interrupted_artifacts(tmp_path, monkeypatch):
    case, record, _, log = prepare(tmp_path)
    settings = enable_cache(monkeypatch, tmp_path)
    CacheExperiment(settings, case_dir=case, mode="cold").start(prepared_record=record)
    before = {
        str(path.relative_to(case)): path.read_bytes() for path in case.rglob("*") if path.is_file()
    }
    call, relay, models = execute(
        "cli", case, record, log, monkeypatch, options=["--cache-isolation", "cold"]
    )
    assert call() == 2
    assert models == [] and not relay.actions
    assert {
        str(path.relative_to(case)): path.read_bytes() for path in case.rglob("*") if path.is_file()
    } == before


def test_prepared_missing_snapshot_finalizes_failure_without_losing_extraction(
    tmp_path, monkeypatch
):
    case, record, state, log = prepare(tmp_path)
    original = log.path.read_bytes()
    enable_cache(monkeypatch, tmp_path)
    call, relay, models = execute(
        "cli",
        case,
        record,
        log,
        monkeypatch,
        options=[
            "--cache-isolation",
            "snapshot",
            "--cache-snapshot",
            str(tmp_path / "missing.json"),
        ],
    )
    assert call() == 2
    evidence = provenance(case)
    assert evidence.outcome == "failed" and not evidence.complete
    assert evidence.error_type == "FileNotFoundError" and evidence.preparation_log_preserved
    assert state.read().status == CaseStatus.FAILED and (case / "result.json").is_file()
    assert log.path.read_bytes().startswith(original)
    assert models == [] and not relay.actions


def test_prepared_provenance_cannot_claim_complete_after_ocr_prefix_changes(tmp_path, monkeypatch):
    case, record, _, log = prepare(tmp_path)
    settings = enable_cache(monkeypatch, tmp_path)
    experiment = CacheExperiment(settings, case_dir=case, mode="cold")
    experiment.start(prepared_record=record)
    event = log.read_all()[0].model_copy(update={"payload": {"tampered": True}})
    log.path.write_text(event.model_dump_json() + "\n", encoding="utf-8")
    assert not experiment.finish(outcome="success")
    evidence = provenance(case)
    assert not evidence.preparation_log_preserved and not evidence.complete
    assert evidence.initial_cache_valid and evidence.final_cache_valid
    assert evidence.finalization_error_type == "ValueError"


def test_provenance_reader_keeps_historical_v01_compatible(tmp_path, monkeypatch):
    case, record, _, _ = prepare(tmp_path)
    experiment = CacheExperiment(enable_cache(monkeypatch, tmp_path), case_dir=case, mode="cold")
    experiment.start(prepared_record=record)
    legacy = experiment.provenance.model_dump(
        mode="json",
        exclude={
            "prepared_case",
            "preparation_log_sha256",
            "preparation_event_count",
            "preparation_log_preserved",
        },
    )
    legacy["schema_version"] = "0.1"
    restored = CacheProvenance.model_validate(legacy)
    assert restored.schema_version == "0.1" and not restored.prepared_case
    assert restored.preparation_log_sha256 is None


@pytest.mark.parametrize("usd", [0.05, None])
def test_prepared_costs_stop_before_any_new_provider_call(tmp_path, monkeypatch, usd):
    case, record, _, log = prepare(tmp_path)
    log.append("model_call", payload={"stage": "extraction"}, cost=EventCost(usd=usd))
    original = log.path.read_bytes()
    enable_cache(monkeypatch, tmp_path)
    call, relay, models = execute(
        "cli",
        case,
        record,
        log,
        monkeypatch,
        options=["--cache-isolation", "cold", "--max-cost-usd", "0.05"],
    )
    assert call() == 1
    result = Result.model_validate_json((case / "result.json").read_bytes())
    assert result.reason == ("case_cost_cap" if usd is not None else "unknown_model_call_cost")
    assert models[0].prompts == [] and not relay.actions
    assert log.path.read_bytes().startswith(original)
    assert provenance(case).complete and provenance(case).outcome == "stopped"


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_prepared_setup_failure_or_interrupt_finalizes_owned_isolation(
    tmp_path, monkeypatch, error
):
    case, record, state, log = prepare(tmp_path)
    original = log.path.read_bytes()
    enable_cache(monkeypatch, tmp_path)
    call, _, _ = execute(
        "cli", case, record, log, monkeypatch, options=["--cache-isolation", "cold"]
    )

    def fail(*args, **kwargs):
        raise error("private failure")

    monkeypatch.setattr(runner, "create_model_client", fail)
    if error is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            call()
        assert state.read().status == CaseStatus.READY
    else:
        assert call() == 2
        assert state.read().status == CaseStatus.FAILED
    evidence = provenance(case)
    assert evidence.complete and evidence.error_type == error.__name__
    assert evidence.outcome == ("interrupted" if error is KeyboardInterrupt else "failed")
    assert log.path.read_bytes().startswith(original)


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "0.6"])
def test_invalid_budget_override_leaves_preparation_untouched(tmp_path, monkeypatch, value):
    case, record, _, log = prepare(tmp_path)
    before = {path.name: path.read_bytes() for path in case.iterdir() if path.is_file()}
    enable_cache(monkeypatch, tmp_path)
    call, relay, models = execute(
        "cli",
        case,
        record,
        log,
        monkeypatch,
        options=["--cache-isolation", "cold", "--max-cost-usd", value],
    )
    if value == "0.6":
        assert call() == 2
    else:
        with pytest.raises(SystemExit):
            call()
    assert models == [] and not relay.actions
    assert {path.name: path.read_bytes() for path in case.iterdir() if path.is_file()} == before
    assert not (case / "cache-experiment").exists()


@pytest.mark.parametrize("mode", ["cold", "snapshot"])
@pytest.mark.parametrize("extraction_cost", [0.01, 0.04])
def test_workflow_uses_real_prepared_brain_with_isolation_and_combined_cap(
    tmp_path, monkeypatch, mode, extraction_cost
):
    argv = arguments(tmp_path) + cache_options(tmp_path, mode)
    (tmp_path / "brain.toml").write_text(
        "max_cost_usd = 0.4\n[cache]\nenabled = true\n", encoding="utf-8"
    )
    (tmp_path / "pipeline.toml").write_text("max_cost_usd = 0.05\n", encoding="utf-8")
    captured = {}

    def preparation(case_dir, *, case_id, **kwargs):
        assert kwargs["settings"].max_cost_usd == 0.05
        record = extracted_record().model_copy(update={"case_id": case_id})
        report = checked_report().model_copy(
            update={"case_id": case_id, "record_sha256": record_fingerprint(record)}
        )
        state = StateStore(case_dir, case_id=case_id)
        state.initialize()
        state.transition(CaseStatus.EXTRACTING, expected=CaseStatus.RECEIVED)
        atomic_write_json(case_dir / "record.json", record)
        atomic_write_json(case_dir / "checks.json", report)
        log = RunLog(case_dir, case_id=case_id, agent_id="pipeline")
        log.append(
            "model_call", payload={"stage": "extraction"}, cost=EventCost(usd=extraction_cost)
        )
        state.transition(CaseStatus.READY, expected=CaseStatus.EXTRACTING)
        captured.update(record=record, original=log.path.read_bytes())
        return SimpleNamespace(
            ready=True,
            record=record,
            checks=report,
            known_cost_usd=extraction_cost,
            unknown_cost=False,
            error_type=None,
        )

    def model_factory(_config, *, run_log, env_path):
        model = ScriptedModel(run_log, success_steps(captured["record"]))
        captured["model"] = model
        return model

    def relay_factory(*args, **kwargs):
        relay = FakeRelay(captured["record"])
        captured["relay"] = relay
        return relay

    monkeypatch.setattr(workflow, "prepare_case", preparation)
    monkeypatch.setattr(workflow.PipelineCallBudget, "from_config", lambda path: None)
    monkeypatch.setattr(runner, "create_model_client", model_factory)
    monkeypatch.setattr(runner, "HttpRelay", relay_factory)
    monkeypatch.setattr(runner.CallBudget, "from_config", lambda path: None)
    expected_success = extraction_cost == 0.01
    assert workflow.main(argv) == (0 if expected_success else 1)
    case = tmp_path / "case"
    result = Result.model_validate_json((case / "result.json").read_bytes())
    if expected_success:
        assert result.status == "success" and captured["relay"].actions
        assert captured["model"].prompts
    else:
        assert result.reason == "case_cost_cap"
        assert captured["model"].prompts == [] and not captured["relay"].actions
    assert (case / "run_log.jsonl").read_bytes().startswith(captured["original"])
    evidence = provenance(case)
    assert evidence.prepared_case and evidence.complete
    assert evidence.outcome == ("success" if expected_success else "stopped")
    actual = ShipmentRecord.model_validate_json((case / "record.json").read_bytes())
    assert (
        actual.origin == "extracted" and actual.fields.hbl_no.value != "ORACLE-VALUE-MUST-NOT-ENTER"
    )
    assert (
        json.loads((case / "pipeline_result.json").read_text())["known_cost_usd"] == extraction_cost
    )
    calls = [
        event
        for event in RunLog(case, case_id=actual.case_id, agent_id="test").read_all()
        if event.kind == "model_call"
    ]
    assert sum(event.cost.usd for event in calls) == extraction_cost
    assert all("ORACLE-VALUE-MUST-NOT-ENTER" not in prompt for prompt in captured["model"].prompts)


@pytest.mark.parametrize(
    "options",
    [
        ["--cache-isolation", "snapshot"],
        ["--cache-snapshot", "missing.json"],
        ["--cache-isolation", "cold", "--cache-snapshot", "missing.json"],
    ],
)
def test_workflow_invalid_cache_flags_stop_before_case_creation(tmp_path, monkeypatch, options):
    argv = arguments(tmp_path) + options
    monkeypatch.setattr(workflow, "prepare_case", lambda *a, **kw: pytest.fail("No extraction"))
    with pytest.raises(SystemExit):
        workflow.main(argv)
    assert not (tmp_path / "case").exists()


def test_workflow_disabled_cache_rejects_isolation_before_paid_preparation(tmp_path, monkeypatch):
    argv = arguments(tmp_path) + ["--cache-isolation", "cold"]
    monkeypatch.setattr(workflow, "prepare_case", lambda *a, **kw: pytest.fail("No extraction"))
    monkeypatch.setattr(workflow, "run_brain", lambda *a, **kw: pytest.fail("No ERP"))
    assert workflow.main(argv) == 2
    assert not (tmp_path / "case/cache-experiment").exists()
