"""Offline recorded graph runner. External I/O is fake; graph/scanner/state code is real.

Private recordings stay outside Git. Prefer raw relay observations when recorded.
Legacy renewal envelopes are synthetic; missing failed observations stop replay.
No SDK or desktop is opened.
"""

from __future__ import annotations

import argparse
import difflib
import json
import shutil
import sys
from collections import deque
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from sawon_interfaces import ActionBatchResult, ActionResult, Observation, ShipmentRecord

from brain.graph.engine import Brain
from brain.settings import BrainSettings
from brain.visual.markers import Marker
from models.base import _CALL_CONTEXT, MeasuredModelClient, ProviderReply, TokenUsage
from sawon_runtime.storage import RunLog
from sawon_runtime.visual import OcrBlock


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class ReplayMismatch(AssertionError):
    pass


class RecordedInputs:
    def __init__(self, source: Path, output: Path):
        self.source, self.output = source, output
        self.events = [
            json.loads(line)
            for line in (source / "run_log.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.observations = self._observations()
        self.requests = deque(
            e
            for e in self.events
            if e["kind"] == "act" and e["payload"].get("stage") == "requested"
        )
        self.results = {
            e["payload"]["action_id"]: e["payload"]
            for e in self.events
            if e["kind"] == "act" and e["payload"].get("stage") == "returned"
        }
        self.batches = deque(
            e
            for e in self.events
            if e["kind"] == "act" and e["payload"].get("stage") == "batch_returned"
        )
        self.models = deque(
            e
            for e in self.events
            if e["kind"] == "model_call" and e["payload"].get("provider") != "local"
        )
        self.scenes = {
            read_json(p)["wire"]["obs_id"]: read_json(p)
            for p in (source / "observations").glob("*.json")
            if "wire" in read_json(p)
        }
        self.action_by_observation = {
            e["payload"]["action"]["obs_id"]: e["payload"]["action"] for e in self.requests
        }
        self.images = {}
        self.errors = []
        self.renewals = 0
        self.model_count = 0
        self.action_count = 0

    def _observations(self):
        raw_path = self.source / "relay-observations.jsonl"
        if raw_path.is_file():
            self.observation_source = "raw_relay"
            return deque(
                {"seq": number, "payload": {"raw_wire": json.loads(line)}}
                for number, line in enumerate(raw_path.read_text(encoding="utf-8").splitlines(), 1)
            )
        self.observation_source = "legacy_events"
        observed = {
            e["payload"].get("obs_id")
            for e in self.events
            if e["kind"] == "observe" and "snapshot" in e["payload"]
        }
        # Perception can fail before an observe snapshot is emitted. Keep its slot
        # rather than consuming the next successful observation at the wrong time.
        return deque(
            e
            for e in self.events
            if e["kind"] == "observe"
            or (
                e["payload"].get("check") == "screen_similarity"
                and e["payload"].get("obs_id") not in observed
            )
        )

    def require(self, condition, message):
        if not condition:
            self.errors.append(message)
            raise ReplayMismatch(message)

    def pop(self, queue, label):
        if self.errors:
            raise ReplayMismatch(self.errors[0])
        self.require(bool(queue), f"unexpected {label}: recording exhausted")
        return queue.popleft()


class RecordedRelay:
    def __init__(self, tape):
        self.tape = tape

    def health(self):
        return {"ok": True}

    def observe(self):
        t = self.tape
        e = t.pop(t.observations, "observe")
        p = e["payload"]
        if "raw_wire" in p:
            wire = Observation.model_validate(p["raw_wire"])
            t.images[str(wire.obs_id)] = t.source / "relay-captures" / f"{wire.obs_id}.png"
            return wire
        t.require(
            p.get("check") != "screen_similarity",
            f"seq {e['seq']}: missing raw observation for {p.get('obs_id')} "
            "(perception has no final snapshot; later observations were not substituted)",
        )
        if "snapshot" in p:
            snapshot = t.source / p["snapshot"]
            t.require(snapshot.is_file(), f"seq {e['seq']}: missing snapshot {p['snapshot']}")
            raw = read_json(snapshot)
            wire = Observation.model_validate(raw.get("wire", raw))
            t.images[str(wire.obs_id)] = t.source / "visual" / str(wire.obs_id) / "capture.png"
            return wire
        # Synthetic renewal envelope: IDs/hash are recorded, geometry is inherited
        # only when the recorded renewal says the window origin was unchanged.
        t.require(not p.get("origin_changed"), f"seq {e['seq']}: renewal geometry unavailable")
        scene = t.scenes[p["perceived_obs_id"]]
        raw = json.loads(json.dumps(scene["wire"]))
        raw["obs_id"] = p["action_obs_id"]
        raw["screen_hash"] = p["screen_hash"]
        action = t.action_by_observation[p["action_obs_id"]]
        raw["screenshot"]["geometry"]["capture_id"] = action["target"]["capture_id"]
        if not p["same_content"]:
            raw["screenshot"]["content_hash"] = None
        image = t.source / "action-renewal" / p["action_obs_id"] / "capture.png"
        if p["same_content"]:
            image = t.source / "visual" / p["perceived_obs_id"] / "capture.png"
        t.require(image.is_file(), f"seq {e['seq']}: missing renewal capture")
        t.images[p["action_obs_id"]] = image
        t.renewals += 1
        return Observation.model_validate(raw)

    def screenshot(self, observed, path):
        source = self.tape.images[str(observed.obs_id)]
        self.tape.require(source.is_file(), f"missing capture for {observed.obs_id}")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
        return path

    def act(self, action):
        t = self.tape
        e = t.pop(t.requests, "action")
        expected = dict(e["payload"]["action"])
        actual = action.model_dump(mode="json")
        expected.pop("action_id")
        actual.pop("action_id")
        if expected.get("text") == "[REDACTED]":
            t.require(
                actual.get("text") in {"replay-user", "replay-password"},
                "unexpected credential source",
            )
            actual["text"] = "[REDACTED]"
        t.require(
            actual == expected, f"action differs at recorded seq {e['seq']}: {actual.get('type')}"
        )
        result = t.results[e["payload"]["action"]["action_id"]]
        t.action_count += 1
        return ActionResult(
            action_id=action.root.action_id,
            ok=result["ok"],
            error=result["error_code"],
            post_obs_id=result["post_obs_id"],
            duration_ms=0.0,
        )

    def act_batch(self, batch):
        t = self.tape
        p = t.pop(t.batches, "batch")["payload"]
        t.require(p["requested"] == len(batch.actions), "batch size differs")
        results = [self.act(action) for action in batch.actions[: p["returned"]]]
        return ActionBatchResult(
            batch_id=batch.batch_id,
            ok=p["ok"],
            error=p["error_code"],
            results=results,
            post_obs_id=p["post_obs_id"],
            duration_ms=0.0,
        )


class RecordedOcr:
    def __init__(self, tape):
        self.tape = tape

    def read(self, page):
        source = self.tape.source / "visual" / page.image_path.parent.name / "ocr.json"
        self.tape.require(source.is_file(), f"missing OCR for {page.image_path.parent.name}")
        raw = read_json(source)
        return [OcrBlock.model_validate(item) for item in raw["blocks"]]


class RecordedDetector:
    def __init__(self, tape):
        self.tape = tape

    def read(self, page, *, ocr_boxes=()):
        source = self.tape.source / "visual" / page.image_path.parent.name / "local-detections.json"
        self.tape.require(
            source.is_file(), f"missing detector output for {page.image_path.parent.name}"
        )
        raw = read_json(source)
        return [Marker.model_validate(item) for item in raw["boxes"]]


class RecordedModel(MeasuredModelClient):
    def __init__(self, tape, log):
        super().__init__(provider="replay", model="recorded", run_log=log)
        self.tape = tape

    def _generate(self, prompt, schema, *, images):
        t = self.tape
        event = t.pop(t.models, "model")
        p = event["payload"]
        folder = t.source / p["request_artifact"]
        expected = (folder / "prompt.txt").read_text(encoding="utf-8")
        t.require(
            schema.__name__ == p["response_schema"], f"model schema differs at seq {event['seq']}"
        )
        t.require(
            (_CALL_CONTEXT.get() or {}).get("purpose") == p["context"].get("purpose"),
            f"model purpose differs at seq {event['seq']}",
        )
        if prompt != expected:
            (t.output / f"prompt-diff-{event['seq']}.txt").write_text(
                "".join(difflib.unified_diff(expected.splitlines(True), prompt.splitlines(True))),
                encoding="utf-8",
            )
        t.require(prompt == expected, f"model prompt differs at seq {event['seq']}")
        t.require(
            schema.model_json_schema() == read_json(folder / "schema.json"),
            f"model schema JSON differs at seq {event['seq']}",
        )
        names = read_json(folder / "metadata.json")["images"]
        t.require(
            [sha256(path.read_bytes()).hexdigest() for path in images or []]
            == [sha256((folder / name).read_bytes()).hexdigest() for name in names],
            f"model image differs at seq {event['seq']}",
        )
        t.model_count += 1
        return ProviderReply(
            text=(folder / "response.txt").read_text(encoding="utf-8"),
            usage=TokenUsage(input_tokens=0, response_tokens=0),
        )

    def _cost(self, usage):
        return Decimal(0)


def replay(source, config, output, goal):
    output.mkdir(parents=True, exist_ok=False)
    tape = RecordedInputs(source, output)
    log = RunLog(output, case_id=read_json(source / "record.json")["case_id"], agent_id="brain")
    settings = BrainSettings.load(config)
    expected_education = next(
        e["payload"]["sha256"]
        for e in tape.events
        if e["payload"].get("check") == "user_education_loaded"
    )
    tape.require(
        sha256(settings.task_path.read_bytes()).hexdigest() == expected_education,
        "education file changed",
    )
    settings = settings.model_copy(
        update={
            "task_path": settings.task_path,
            "visual_match_cache": settings.visual_match_cache.model_copy(
                update={"path": output / "cache.json"}
            ),
            "methods": settings.methods.model_copy(
                update={"path": output / "methods.json", "promotion_path": output / "methods.json"}
            ),
        }
    )
    brain = Brain(
        record=ShipmentRecord.model_validate_json((source / "record.json").read_bytes()),
        goal=goal,
        relay=RecordedRelay(tape),
        model=RecordedModel(tape, log),
        settings=settings,
        case_dir=output,
        run_log=log,
        username="replay-user",
        password="replay-password",
        visual_ocr=RecordedOcr(tape),
    )
    brain.screen_scanner.detector = RecordedDetector(tape)
    nodes = []

    def trace_node(name, function):
        def wrapped(state):
            before = {
                "phase": brain.phase,
                "verified": sorted(brain.verified),
                "post_save_verified": sorted(brain.post_save_verified),
            }
            result_state = function(state)
            nodes.append(
                {
                    "node": name,
                    "before": before,
                    "route": result_state["lifecycle"]["route"],
                    "phase": brain.phase,
                    "verified": sorted(brain.verified),
                    "post_save_verified": sorted(brain.post_save_verified),
                    "save_issued": brain.save_issued,
                    "pending": brain.pending is not None,
                }
            )
            return result_state

        return wrapped

    for name in ["observation_node", "decision_node", "review_node", "execution_node"]:
        setattr(brain, name, trace_node(name, getattr(brain, name)))
    with (
        patch(
            "socket.socket.connect",
            side_effect=AssertionError("network forbidden in recorded replay"),
        ),
        patch(
            "subprocess.Popen", side_effect=AssertionError("process forbidden in recorded replay")
        ),
    ):
        result = brain.run()
    (output / "node-trace.json").write_text(
        json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        "status": result.status,
        "reason": result.reason,
        "errors": tape.errors,
        "models": tape.model_count,
        "actions": tape.action_count,
        "remaining": {
            "models": len(tape.models),
            "actions": len(tape.requests),
            "observations": len(tape.observations),
            "batches": len(tape.batches),
        },
        "reconstructed_renewals": tape.renewals,
        "observation_source": tape.observation_source,
        "verified": sorted(brain.verified),
        "post_save_verified": sorted(brain.post_save_verified),
    }
    expected_result = read_json(source / "result.json")
    summary["matches_recorded_result"] = (
        result.status == expected_result["status"] and result.reason == expected_result["reason"]
    )
    summary["complete_replay"] = (
        not tape.errors
        and not any(summary["remaining"].values())
        and summary["matches_recorded_result"]
    )
    summary["node_count"] = len(nodes)
    (output / "replay-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--goal", required=True)
    args = parser.parse_args()
    summary = replay(args.source.resolve(), args.config.resolve(), args.output.resolve(), args.goal)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if summary["complete_replay"] else 1)
