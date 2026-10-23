"""Capture-only HTTP observations use the client default contract."""

from test_visual_scene import wire

from brain.relay import HttpRelay


def test_capture_client_default_round_trip(monkeypatch):
    relay = HttpRelay("http://127.0.0.1:8765", "synthetic")
    observation = wire()
    monkeypatch.setattr(
        relay,
        "_request",
        lambda path: (
            {"observation_mode": "visual_only"}
            if path == "/health"
            else observation.model_dump(mode="json")
        ),
    )
    assert relay.health()["observation_mode"] == "visual_only"
    assert relay.observe() == observation
