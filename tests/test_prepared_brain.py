"""Prepared extraction handoff validates its report before any environment access."""

import pytest
from case_labels import labeled_case
from test_brain import FakeRelay, ScriptedModel
from test_pipeline_evaluate import record as extracted_record
from test_pipeline_evaluate import report as checked_report

import brain.run as runner
from brain.graph import Brain
from brain.settings import BrainSettings
from pipeline.handoff import validate_prepared_case
from pipeline.schemas import Check
from sawon_runtime.storage import CaseStatus, RunLog, StateStore, atomic_write_json


class TrackedRelay(FakeRelay):
    def __init__(self, record):
        super().__init__(record)
        self.health_calls = 0

    def health(self):
        self.health_calls += 1
        return super().health()


def prepare(tmp_path, *, mutation=None):
    case_dir = tmp_path / "prepared"
    record, report = extracted_record(), checked_report()
    state = StateStore(case_dir, case_id=record.case_id)
    state.initialize()
    state.transition(CaseStatus.EXTRACTING, expected=CaseStatus.RECEIVED)
    if mutation != "not_ready":
        state.transition(CaseStatus.READY, expected=CaseStatus.EXTRACTING)
    if mutation == "value":
        record.fields.hbl_no.value = "UNVERIFIED-MUTATION"
    elif mutation == "source":
        record.fields.hbl_no.source.page = 9
    elif mutation == "missing_hash":
        report = report.model_copy(update={"record_sha256": None})
    elif mutation == "blocking":
        check = Check(code="field_disagreement", severity="blocking", message="Needs correction")
        report = report.model_copy(update={"checks": [check]})
        record.checks = [check.model_dump(mode="json")]
    atomic_write_json(case_dir / "record.json", record)
    atomic_write_json(case_dir / "checks.json", report)
    log = RunLog(case_dir, case_id=record.case_id, agent_id="extraction-fixture")
    log.append("verify", payload={"check": "extraction_ready", "ready": mutation is None})
    return case_dir, record, state, log


def execute(mode, case_dir, record, log, monkeypatch, *, options=()):
    relay = TrackedRelay(record)
    models = []

    def model_factory(_config, *, run_log, env_path):
        model = ScriptedModel(run_log, [])
        models.append(model)
        return model

    if mode == "engine":
        model = model_factory(None, run_log=log, env_path=None)
        brain = Brain(
            record=record,
            goal="Enter checked extraction",
            relay=relay,
            model=model,
            settings=BrainSettings(),
            case_dir=case_dir,
            run_log=log,
            prepared_case=True,
        )
        return brain.run, relay, models
    settings = case_dir.parent / "brain.toml"
    settings.write_text("", encoding="utf-8")
    monkeypatch.setattr(runner, "HttpRelay", lambda *args, **kwargs: relay)
    monkeypatch.setattr(runner, "create_model_client", model_factory)
    monkeypatch.setattr(runner.CallBudget, "from_config", lambda _path: None)
    arguments = [
        "--prepared-case",
        "--record",
        str(case_dir / "record.json"),
        "--case-dir",
        str(case_dir),
        "--relay-url",
        "http://127.0.0.1:1",
        "--goal",
        "Enter checked extraction",
        "--config",
        str(settings),
        "--model-config",
        str(case_dir.parent / "fake-model.toml"),
    ]
    return lambda: runner.main(arguments + list(options)), relay, models


def test_prepared_validator_rejects_each_invalid_handoff_without_mutation(tmp_path):
    for mutation in ("value", "source", "missing_hash", "blocking", "not_ready"):
        with labeled_case(mutation):
            case_dir, record, state, log = prepare(tmp_path / mutation, mutation=mutation)
            before_state, before_log = state.read(), log.path.read_bytes()
            with pytest.raises(ValueError) as raised:
                validate_prepared_case(case_dir, record)
            assert str(raised.value)
            assert state.read() == before_state
            assert log.path.read_bytes() == before_log
            assert not (case_dir / "result.json").exists()


def test_prepared_cli_calls_validator_before_environment_or_model_access(tmp_path, monkeypatch):
    case_dir, record, state, log = prepare(tmp_path)
    before_state, before_log = state.read(), log.path.read_bytes()
    validated = []

    def reject_prepared(folder, supplied):
        validated.append((folder, supplied))
        raise ValueError("synthetic handoff rejection")

    monkeypatch.setattr(runner, "validate_prepared_case", reject_prepared)
    call, relay, models = execute("cli", case_dir, record, log, monkeypatch)
    assert call() == 2
    assert validated == [(case_dir, record)]
    assert state.read() == before_state
    assert relay.health_calls == relay.calls == 0 and relay.actions == []
    assert models == []
    assert log.path.read_bytes() == before_log
    assert not (case_dir / "result.json").exists()


def test_brain_rechecks_each_independent_prepared_handoff_gate(tmp_path, monkeypatch):
    # Direct Brain users bypass the CLI validator. Cover its three own branches:
    # state readiness, saved-record equality and can_enter(report).
    for mutation in ("not_ready", "value", "blocking"):
        with labeled_case(mutation):
            case_dir, record, state, log = prepare(tmp_path / mutation, mutation=mutation)
            before_state, before_log = state.read(), log.path.read_bytes()
            call, relay, models = execute("engine", case_dir, record, log, monkeypatch)
            with pytest.raises(ValueError) as raised:
                call()
            assert str(raised.value)
            assert state.read() == before_state
            assert relay.health_calls == relay.calls == 0 and relay.actions == []
            assert all(model.prompts == [] for model in models)
            assert log.path.read_bytes() == before_log
            assert not (case_dir / "result.json").exists()
