"""Document-to-ERP orchestration never uses the harness's expected field values."""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sawon_interfaces import EventCost, ShipmentRecord
from test_brain import record as record

from brain.graph import Result
from brain.runtime.storage import CaseStatus, RunLog, StateStore, atomic_write_json
from pipeline import workflow


def arguments(tmp_path):
    expected = tmp_path / "expected.json"
    expected.write_text(
        json.dumps(
            {
                "case_id": "workflow-synthetic",
                "fields": {
                    "hbl_no": {"value": "ORACLE-VALUE-MUST-NOT-ENTER"},
                    "invalid_expected_field": object.__name__,
                },
                "truth": "FORBIDDEN-TRUTH-LOCATION",
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "inputs/manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps({"schema_version": "1.0", "case_id": "input-case-id", "documents": []}),
        encoding="utf-8",
    )
    brain_config = tmp_path / "brain.toml"
    brain_config.write_text("max_cost_usd = 0.4\n", encoding="utf-8")
    pipeline_config = tmp_path / "pipeline.toml"
    pipeline_config.write_text("max_cost_usd = 0.5\n", encoding="utf-8")
    return [
        "--record",
        str(expected),
        "--manifest",
        str(manifest),
        "--case-dir",
        str(tmp_path / "case"),
        "--relay-url",
        "http://fake-relay",
        "--goal",
        "Synthetic shipment registration",
        "--config",
        str(brain_config),
        "--model-config",
        str(tmp_path / "model.toml"),
        "--pipeline-config",
        str(pipeline_config),
    ]


def fake_preparation(record, *, ready=True, crash=False):
    def prepare(case_dir, **kwargs):
        assert set(kwargs) == {"case_id", "documents", "settings", "model_factory", "reservation"}
        assert kwargs["case_id"] == "workflow-synthetic"
        assert kwargs["settings"].max_cost_usd == 0.4
        assert kwargs["documents"] == []
        log = RunLog(case_dir, case_id=kwargs["case_id"], agent_id="pipeline")
        state = StateStore(case_dir, case_id=kwargs["case_id"])
        state.initialize()
        state.transition(CaseStatus.EXTRACTING, expected=CaseStatus.RECEIVED)
        log.append("model_call", payload={"stage": "extraction"}, cost=EventCost(usd=0.01))
        if crash:
            raise RuntimeError("SECRET-PROVIDER-BODY-NOT-FOR-OUTPUT")
        extracted = record.model_copy(
            deep=True, update={"case_id": kwargs["case_id"], "origin": "extracted"}
        )
        extracted.fields.hbl_no.value = "EXTRACTED-DOCUMENT-HBL"
        atomic_write_json(Path(case_dir) / "record.json", extracted)
        state.transition(
            CaseStatus.READY if ready else CaseStatus.FAILED, expected=CaseStatus.EXTRACTING
        )
        return SimpleNamespace(
            ready=ready,
            record=extracted if ready else None,
            known_cost_usd=0.01,
            unknown_cost=False,
            error_type=None,
            checks=SimpleNamespace(checks=[]),
        )

    return prepare


@pytest.fixture
def no_provider(monkeypatch):
    monkeypatch.setattr(
        workflow.PipelineCallBudget, "from_config", lambda path: lambda *a: Decimal(0)
    )


def test_only_extracted_record_is_given_to_brain_and_log_costs_continue(
    tmp_path, record, monkeypatch, no_provider
):
    argv = arguments(tmp_path)
    monkeypatch.setattr(workflow, "prepare_case", fake_preparation(record))
    received = []

    def brain(args):
        received.extend(args)
        record_path = Path(args[args.index("--record") + 1])
        assert record_path == tmp_path / "case/record.json"
        extracted = ShipmentRecord.model_validate_json(record_path.read_bytes())
        assert extracted.fields.hbl_no.value == "EXTRACTED-DOCUMENT-HBL"
        assert extracted.origin == "extracted"
        state = StateStore(tmp_path / "case", case_id=extracted.case_id)
        assert state.read().status == CaseStatus.READY
        state.transition(CaseStatus.ENTERING, expected=CaseStatus.READY)
        log = RunLog(tmp_path / "case", case_id=extracted.case_id, agent_id="brain")
        assert log.read_all()[0].cost.usd == 0.01
        log.append("model_call", payload={"stage": "execution"}, cost=EventCost(usd=0.02))
        state.transition(CaseStatus.AWAITING_APPROVAL, expected=CaseStatus.ENTERING)
        atomic_write_json(
            tmp_path / "case/result.json",
            Result(status="success", reason="verified", steps=1, elapsed_ms=1),
        )
        return 0

    monkeypatch.setattr(workflow, "run_brain", brain)
    assert (
        workflow.main(
            argv
            + [
                "--env-path",
                str(tmp_path / ".env"),
                "--recovery-model-config",
                str(tmp_path / "recovery.toml"),
                "--summary-strategy",
                "flat_fields",
            ]
        )
        == 0
    )
    assert "--prepared-case" in received and "--recovery-model-config" in received
    assert str(tmp_path / "expected.json") not in received
    assert not any("ORACLE" in arg or "TRUTH" in arg for arg in received)
    log = RunLog(tmp_path / "case", case_id="workflow-synthetic", agent_id="test")
    assert [event.seq for event in log.read_all()] == [0, 1]
    assert sum(event.cost.usd for event in log.read_all()) == pytest.approx(0.03)
    assert (
        StateStore(tmp_path / "case", case_id="workflow-synthetic").read().status
        == CaseStatus.AWAITING_APPROVAL
    )
    for path in (tmp_path / "case").glob("*.json*"):
        assert "ORACLE-VALUE" not in path.read_text(encoding="utf-8")
        assert "FORBIDDEN-TRUTH" not in path.read_text(encoding="utf-8")


def test_rejected_extraction_never_invokes_brain_and_writes_harness_terminal_result(
    tmp_path, record, monkeypatch, no_provider
):
    argv = arguments(tmp_path)
    monkeypatch.setattr(workflow, "prepare_case", fake_preparation(record, ready=False))
    monkeypatch.setattr(workflow, "run_brain", lambda args: pytest.fail("ERP must not run"))
    assert workflow.main(argv) == 1
    result = Result.model_validate_json((tmp_path / "case/result.json").read_bytes())
    assert result.status == "failed" and result.reason == "document_review_required"
    assert result.steps == 0
    assert (
        StateStore(tmp_path / "case", case_id="workflow-synthetic").read().status
        == CaseStatus.FAILED
    )


def test_preparation_exception_keeps_charged_evidence_and_retry_preserves_entire_case(
    tmp_path, record, monkeypatch, no_provider, capsys
):
    argv = arguments(tmp_path)
    monkeypatch.setattr(workflow, "prepare_case", fake_preparation(record, crash=True))
    monkeypatch.setattr(workflow, "run_brain", lambda args: pytest.fail("ERP must not run"))
    assert workflow.main(argv) == 2
    case = tmp_path / "case"
    result = Result.model_validate_json((case / "result.json").read_bytes())
    assert result.reason == "workflow_setup_error:RuntimeError"
    events = RunLog(case, case_id="workflow-synthetic", agent_id="test").read_all()
    assert events[0].kind == "model_call" and events[0].cost.usd == 0.01
    assert len(events) == 2 and events[1].kind == "stop"
    before = {path.name: path.read_bytes() for path in case.iterdir() if path.is_file()}
    assert workflow.main(argv) == 2
    assert {path.name: path.read_bytes() for path in case.iterdir() if path.is_file()} == before
    assert "SECRET-PROVIDER-BODY" not in capsys.readouterr().out


def test_invalid_manifest_fails_before_extraction_or_erp(tmp_path, monkeypatch, no_provider):
    argv = arguments(tmp_path)
    (tmp_path / "inputs/manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow, "prepare_case", lambda *a, **kw: pytest.fail("No extraction"))
    monkeypatch.setattr(workflow, "run_brain", lambda args: pytest.fail("No ERP"))
    assert workflow.main(argv) == 2
    assert (
        Result.model_validate_json((tmp_path / "case/result.json").read_bytes()).reason
        == "workflow_setup_error:ValueError"
    )
