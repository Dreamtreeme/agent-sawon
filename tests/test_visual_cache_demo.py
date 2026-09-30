"""UIA evidence cannot seed visual cache hits or demonstrations."""

import pytest
from test_semantic_cache import fill, observation, seed
from test_semantic_cache import record as record
from test_visual_scene import wire

from brain.cache import SemanticCache
from brain.demo_learning import convert_demonstration
from brain.visual.record import record_scenes
from brain.visual.scene import SceneObservation


def visual(source):
    raw = wire().model_copy(
        update={
            "obs_id": source.obs_id,
            "ts": source.ts,
            "active_window": source.active_window,
            "windows": source.windows,
        }
    )
    return SceneObservation(
        wire=raw,
        elements=[item.model_copy(update={"automation_id": None}) for item in source.elements],
    )


def test_uia_cache_is_ineligible_until_visual_success_promotes_separate_entry(tmp_path, record):
    cache = SemanticCache(tmp_path / "cache.json")
    uia = seed(cache, record)
    current = visual(observation())
    assert cache.start_run().lookup(current, phase="enter", allowed_sources=["hbl_no"]) is None
    session = cache.start_run()
    after = visual(observation(record.fields.hbl_no.value, seconds=1))
    assert session.stage_validated(current, after, fill(), record)
    assert session.finish(success=True) == 1
    assert len(cache.entries()) == 2
    hit = cache.start_run().lookup(
        visual(observation(shift=40)), phase="enter", allowed_sources=["hbl_no"]
    )
    assert hit is not None and hit.decision.action.ref == "e41"
    assert hit.entry_id != uia.entry_id


def test_visual_demonstration_keeps_source_and_mixed_sequence_is_rejected(record):
    sources = [
        observation(),
        observation(record.fields.hbl_no.value, seconds=1),
        observation(record.fields.hbl_no.value, seconds=2),
    ]
    result = convert_demonstration([visual(item) for item in sources], record)
    assert len(result.entries) == 1
    assert result.entries[0].evidence_source == "ocr_visual"
    with pytest.raises(ValueError, match="Mixed"):
        convert_demonstration([sources[0], visual(sources[1]), visual(sources[2])], record)


def test_visual_recorder_requires_read_only_relay_and_never_sends_actions(tmp_path):
    current = wire()

    class Source:
        read_only = False

        def health(self):
            return {"observation_mode": "visual_only", "observation_only": self.read_only}

        def observe(self):
            return current

    class Perception:
        def observe(self, source):
            assert source == current
            return SceneObservation(wire=source, elements=[])

    source = Source()
    output = tmp_path / "recording.jsonl"
    with pytest.raises(ValueError, match="observation-only"):
        record_scenes(source, Perception(), output, samples=1)
    assert not output.exists()
    source.read_only = True
    assert record_scenes(source, Perception(), output, samples=1) == 1
    with pytest.raises(FileExistsError):
        record_scenes(source, Perception(), output, samples=1)
