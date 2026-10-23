"""Synthetic recordings test the replay oracle, not ERP success."""

import json
from uuid import uuid4

import pytest
from pydantic import BaseModel
from recorded_graph_replay import RecordedInputs, RecordedModel, RecordedRelay, ReplayMismatch
from test_visual_scene import wire

from sawon_runtime.storage import RunLog


class SyntheticReply(BaseModel):
    answer: str


@pytest.mark.parametrize("changed", [None, "prompt", "schema", "image", "purpose"])
def test_synthetic_model_recording_rejects_changed_inputs(tmp_path, changed):
    source = tmp_path / "source"
    request = source / "request"
    request.mkdir(parents=True)
    event = {
        "seq": 1,
        "kind": "model_call",
        "payload": {
            "provider": "fake",
            "request_artifact": "request",
            "response_schema": "SyntheticReply",
            "context": {"purpose": "unexpected" if changed == "purpose" else None},
        },
    }
    (source / "run_log.jsonl").write_text(json.dumps(event), encoding="utf-8")
    (request / "prompt.txt").write_text("synthetic prompt", encoding="utf-8")
    schema = SyntheticReply.model_json_schema()
    if changed == "schema":
        schema["properties"]["answer"]["description"] = "changed contract"
    (request / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
    (request / "metadata.json").write_text('{"images": ["image.png"]}', encoding="utf-8")
    (request / "image.png").write_bytes(b"synthetic image bytes")
    (request / "response.txt").write_text('{"answer": "ok"}', encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    image = tmp_path / "input.png"
    image.write_bytes(b"different" if changed == "image" else b"synthetic image bytes")
    tape = RecordedInputs(source, output)
    model = RecordedModel(tape, RunLog(output, case_id="synthetic", agent_id="test"))
    prompt = "different" if changed == "prompt" else "synthetic prompt"
    if changed is None:
        assert model._generate(prompt, SyntheticReply, images=[image]).text == '{"answer": "ok"}'
        assert tape.model_count == 1
        assert not tape.models
    else:
        with pytest.raises(ReplayMismatch, match=f"model {changed}.*differs"):
            model._generate(prompt, SyntheticReply, images=[image])
        assert tape.model_count == 0
        assert tape.errors
        if changed == "prompt":
            assert "-synthetic prompt" in (output / "prompt-diff-1.txt").read_text()


def test_synthetic_missing_observation_stops_instead_of_inventing_state(tmp_path):
    event = {"seq": 9, "kind": "observe", "payload": {"snapshot": "missing.json"}}
    (tmp_path / "run_log.jsonl").write_text(json.dumps(event), encoding="utf-8")
    tape = RecordedInputs(tmp_path, tmp_path)
    relay = RecordedRelay(tape)
    with pytest.raises(ReplayMismatch, match="seq 9: missing snapshot"):
        relay.observe()
    with pytest.raises(ReplayMismatch, match="seq 9: missing snapshot"):
        relay.observe()
    assert len(tape.errors) == 1


def test_synthetic_failed_perception_is_not_replaced_by_later_observation(tmp_path):
    """Synthetic legacy log: failed perception has no final observe event or raw wire."""
    failed, successful = str(uuid4()), str(uuid4())
    events = [
        {"seq": 10, "kind": "verify", "payload": {"check": "screen_similarity", "obs_id": failed}},
        {
            "seq": 11,
            "kind": "verify",
            "payload": {"stage": "visual_grounding_error", "obs_id": failed},
        },
        {
            "seq": 12,
            "kind": "verify",
            "payload": {"check": "screen_similarity", "obs_id": successful},
        },
        {"seq": 13, "kind": "observe", "payload": {"obs_id": successful, "snapshot": "later.json"}},
    ]
    (tmp_path / "run_log.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
    tape = RecordedInputs(tmp_path, tmp_path)
    assert [e["seq"] for e in tape.observations] == [10, 13]
    relay = RecordedRelay(tape)
    with pytest.raises(ReplayMismatch, match=f"missing raw observation for {failed}"):
        relay.observe()
    assert tape.observations[0]["seq"] == 13


def test_synthetic_raw_relay_keeps_failed_successful_and_renewal_observations(tmp_path):
    """Synthetic raw wire sequence includes the failed perception and pre-action renewal."""
    observations = [wire().model_copy(update={"obs_id": uuid4()}) for _ in range(3)]
    events = [
        {
            "seq": 1,
            "kind": "verify",
            "payload": {"check": "screen_similarity", "obs_id": str(observations[0].obs_id)},
        },
        {"seq": 2, "kind": "observe", "payload": {"snapshot": "not-needed.json"}},
    ]
    (tmp_path / "run_log.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
    (tmp_path / "relay-observations.jsonl").write_text(
        "\n".join(o.model_dump_json() for o in observations), encoding="utf-8"
    )
    captures = tmp_path / "relay-captures"
    captures.mkdir()
    for index, observed in enumerate(observations):
        (captures / f"{observed.obs_id}.png").write_bytes(f"synthetic capture {index}".encode())
    tape = RecordedInputs(tmp_path, tmp_path)
    relay = RecordedRelay(tape)
    for index, expected in enumerate(observations):
        observed = relay.observe()
        assert observed == expected, f"synthetic observation {index}"
        saved = relay.screenshot(observed, tmp_path / f"copied-{index}.png")
        assert saved.read_bytes() == f"synthetic capture {index}".encode()
    assert tape.observation_source == "raw_relay"
    assert tape.renewals == 0
    assert not tape.observations
    with pytest.raises(ReplayMismatch, match="unexpected observe: recording exhausted"):
        relay.observe()
