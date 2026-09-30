"""The existing UIA brain must not silently run against the new visual-only relay."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sawon_interfaces import CaptureGeometry, Observation, Screenshot, WindowInfo

from brain.relay import HttpRelay, RelayError


def test_legacy_brain_rejects_visual_mode_before_planning(monkeypatch):
    relay = HttpRelay("http://127.0.0.1:8765", "synthetic")
    monkeypatch.setattr(relay, "_request", lambda _: {"observation_mode": "visual_only"})
    with pytest.raises(RelayError, match="visual_agent_not_implemented"):
        relay.health()


def test_visual_wire_is_supported_but_not_sent_to_legacy_reasoning(monkeypatch):
    window = WindowInfo(handle=1, title="Synthetic", process="Synthetic.exe")
    observation = Observation(
        obs_id=uuid4(),
        ts=datetime.now(UTC),
        observation_mode="visual_only",
        active_window=window,
        windows=[window],
        elements=[],
        screen_hash="a" * 40,
        screenshot=Screenshot(
            path="screenshots/synthetic.png",
            width=100,
            height=100,
            geometry=CaptureGeometry(
                capture_id=uuid4(),
                captured_at=datetime.now(UTC),
                origin=(-100, 0),
                physical_size=(100, 100),
                dpi=144,
            ),
        ),
    )
    payload = observation.model_dump(mode="json")
    assert Observation.model_validate(payload) == observation
    relay = HttpRelay("http://127.0.0.1:8765", "synthetic")
    monkeypatch.setattr(relay, "_request", lambda _: payload)
    with pytest.raises(RelayError, match="visual_agent_not_implemented"):
        relay.observe()
