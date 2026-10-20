"""Synthetic faults test diagnostic non-interference, not new acceptance rules."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from brain.demo_learning.warm_methods import WarmMethodAssembly
from models.base import MeasuredModelClient, ProviderReply, TokenUsage
from sawon_runtime import storage


class LocalReply(MeasuredModelClient):
    def __init__(self, log, text):
        super().__init__(provider="local", model="synthetic", run_log=log)
        self.text = text
        self.calls = 0

    def _generate(self, prompt, schema, *, images):
        self.calls += 1
        return ProviderReply(self.text, TokenUsage(input_tokens=0, response_tokens=0))

    def _cost(self, usage):
        return Decimal(0)


@pytest.mark.parametrize("failure", [None, "artifact", "event"])
def test_recording_preserves_exception_identity_and_caller_recovery(tmp_path, monkeypatch, failure):
    log = storage.RunLog(tmp_path, case_id="diagnostic", agent_id="test")
    original = ValueError("synthetic_rejection")
    continued = []

    def fail(*args, **kwargs):
        raise OSError("synthetic disk failure")

    if failure == "artifact":
        monkeypatch.setattr(storage, "atomic_write_json", fail)
    elif failure == "event":
        monkeypatch.setattr(log, "append", fail)
    try:
        with storage.rejection_evidence(log, "test.synthetic", response={"step": "unchanged"}):
            raise original
    except ValueError as caught:
        assert caught is original
        continued.append("same recovery")
    assert continued == ["same recovery"]
    with storage.rejection_evidence(log, "test.accepted"):
        result = 7
    assert result == 7
    if failure is None:
        (event,) = log.read_all()
        evidence = json.loads(Path(event.payload["artifact"]).read_text(encoding="utf-8"))
        assert evidence["reason"] == str(original)
        assert evidence["response"] == {"step": "unchanged"}
        assert evidence["trace"][-1]["function"] == (
            "test_recording_preserves_exception_identity_and_caller_recovery"
        )


@pytest.mark.parametrize("broken_storage", [False, True])
def test_ninth_compiler_anchor_preserves_raw_response_and_same_validation_error(
    tmp_path, monkeypatch, broken_storage
):
    log = storage.RunLog(tmp_path, case_id="anchors", agent_id="test")
    raw = json.dumps(
        {
            "purpose": "synthetic compilation",
            "groups": [
                {
                    "candidate_id": "c000",
                    "purpose": "fill",
                    "expected_result": "filled",
                    "context_anchors": [f"anchor {index}" for index in range(9)],
                }
            ],
        },
        indent=3,
    )
    client = LocalReply(log, raw)
    if broken_storage:

        def fail(*args, **kwargs):
            raise OSError("synthetic diagnostic write failure")

        monkeypatch.setattr(storage, "atomic_write_json", fail)
    with pytest.raises(ValidationError) as caught:
        client.complete_json("synthetic input", WarmMethodAssembly)
    assert [(item["loc"], item["type"]) for item in caught.value.errors()] == [
        (("groups", 0, "context_anchors"), "too_long")
    ]
    assert client.calls == 1
    (event,) = log.read_all()
    assert event.kind == "model_call" and event.cost.usd == 0
    assert event.payload["validator"].endswith("WarmMethodAssembly.model_validate_json")
    if broken_storage:
        assert event.payload["response_artifact"] is None
    else:
        preserved = json.loads(Path(event.payload["response_artifact"]).read_text(encoding="utf-8"))
        assert preserved["text"].encode() == raw.encode()


def test_accepted_model_response_is_unchanged_when_diagnostic_storage_fails(tmp_path, monkeypatch):
    log = storage.RunLog(tmp_path, case_id="accepted", agent_id="test")
    raw = (
        '{"purpose":"fill","groups":[{"candidate_id":"c0",'
        '"purpose":"fill","expected_result":"filled"}]}'
    )
    expected = WarmMethodAssembly.model_validate_json(raw)

    def fail(*args, **kwargs):
        raise OSError("synthetic diagnostic write failure")

    monkeypatch.setattr(storage, "atomic_write_json", fail)
    client = LocalReply(log, raw)
    assert client.complete_json("synthetic input", WarmMethodAssembly).response == expected
    assert client.calls == 1
    assert log.read_all()[0].payload["status"] == "success"
