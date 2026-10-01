"""Read-only recordings require fresh, focused, stable and unique field proof."""

import json

import pytest
from test_semantic_cache import observation
from test_semantic_cache import record as record

from brain.cache import SemanticCache
from brain.demo_learning import convert_demonstration, install_demonstration, read_recording
from brain.demo_learning.convert import main


def observations(record, **kwargs):
    return [
        observation(seconds=0, **kwargs),
        observation(record.fields.hbl_no.value, shift=10, seconds=1, **kwargs),
        observation(record.fields.hbl_no.value, shift=20, seconds=2, **kwargs),
    ]


def test_converts_to_private_proposals_without_action_replay(tmp_path, record, monkeypatch):
    # Conversion's proposal-only cache path is relative to the working directory.
    monkeypatch.chdir(tmp_path)
    result = convert_demonstration(observations(record), record)
    assert result.status == "proposed"
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.field == "hbl_no"
    assert entry.origin == "demonstration"
    assert entry.action_template == "set_text"
    text = result.model_dump_json()
    for forbidden in [
        record.fields.hbl_no.value,
        "bbox",
        "ref",
        "automation_id",
        "handle",
        "unstable-identifier",
    ]:
        assert forbidden not in text
    assert not list(tmp_path.iterdir())


def test_install_is_explicit_and_resolves_current_refs(tmp_path, record):
    cache = SemanticCache(tmp_path / "cache.json")
    convert_demonstration(observations(record), record)
    assert not cache.path.exists()
    result = install_demonstration(observations(record), record, cache)
    assert len(result.entries) == 1
    hit = cache.start_run().lookup(
        observation(shift=100), phase="enter", allowed_sources=["hbl_no"]
    )
    assert hit is not None and hit.decision.action.ref == "e101"


@pytest.mark.parametrize(
    "kind",
    [
        "unfocused",
        "unstable",
        "ambiguous_source",
        "duplicate",
        "protected",
        "redacted",
        "last_change_unconfirmed",
        "window_changed",
    ],
)
def test_uncertain_demonstrations_are_not_accepted(record, kind):
    items = observations(record)
    if kind == "unfocused":
        items = observations(record, focused=False)
    elif kind == "unstable":
        items[2] = observation("different", seconds=2)
    elif kind == "ambiguous_source":
        record = record.model_copy(deep=True)
        record.fields.mbl_no.value = record.fields.hbl_no.value
    elif kind == "duplicate":
        items = observations(record, duplicate=True)
    elif kind == "protected":
        items = observations(record, label="Password")
    elif kind == "redacted":
        items = observations(record, label="[REDACTED]")
    elif kind == "last_change_unconfirmed":
        items = items[:2]
    elif kind == "window_changed":
        items[2] = observation(record.fields.hbl_no.value, seconds=2, window="Other")
    assert convert_demonstration(items, record).entries == ()


@pytest.mark.parametrize("kind", ["same_id", "same_time", "old_time"])
def test_stale_recordings_are_rejected(record, kind):
    items = observations(record)
    if kind == "same_id":
        items[1] = items[1].model_copy(update={"obs_id": items[0].obs_id})
    elif kind == "same_time":
        items[1] = items[1].model_copy(update={"ts": items[0].ts})
    elif kind == "old_time":
        items.reverse()
    with pytest.raises(ValueError):
        convert_demonstration(items, record)


def test_recorder_jsonl_roundtrip_and_truncation(tmp_path, record):
    path = tmp_path / "observations.jsonl"
    original = observations(record)
    text = "".join(item.model_dump_json() + "\n" for item in original)
    path.write_text(text, encoding="utf-8")
    assert read_recording(path) == original
    path.write_text(text.rstrip(), encoding="utf-8")
    with pytest.raises(ValueError, match="Incomplete"):
        read_recording(path)
    assert path.read_text(encoding="utf-8") == text.rstrip()


def test_cli_outputs_proposals_without_silent_install(tmp_path, record, capsys):
    source = tmp_path / "observations.jsonl"
    source.write_text(
        "".join(item.model_dump_json() + "\n" for item in observations(record)), encoding="utf-8"
    )
    record_path = tmp_path / "record.json"
    record_path.write_text(record.model_dump_json(), encoding="utf-8")
    output = tmp_path / "proposals.json"
    arguments = [
        "--observations",
        str(source),
        "--record",
        str(record_path),
        "--output",
        str(output),
    ]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["installed"] is False
    cache_path = tmp_path / "cache.json"
    assert not cache_path.exists()
    assert main([*arguments, "--install-cache", str(cache_path)]) == 0
    assert json.loads(capsys.readouterr().out)["installed"] is True
    assert len(SemanticCache(cache_path).entries()) == 1
