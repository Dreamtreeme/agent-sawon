"""Business gates consume visual semantics and emit only capture-bound pixels."""

import hashlib
import io
import json
from datetime import UTC, datetime
from itertools import repeat
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw
from sawon_interfaces import (
    Action,
    ActionResult,
    CaptureGeometry,
    ImageTarget,
    Observation,
    Screenshot,
    WindowInfo,
)
from test_brain import FakeRelay, ScriptedModel, success_steps
from test_brain import record as record

from brain.graph.engine import Brain
from brain.graph.matching import RULES
from brain.observe.values import values_for
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings
from brain.visual.markers import Marker
from brain.visual.scene import SceneObservation
from pipeline.schemas import OcrBlock


class VisualFixture(FakeRelay):
    def observe(self):
        original = super().observe()
        # Fixture geometry is unique for each target, independent of labels.
        self.items = [
            item.model_copy(update={"bbox": (int(item.ref[1:]) * 10, 10, 8, 20)})
            for item in original.elements
        ]
        data = original.model_dump()
        data.update(
            observation_mode="visual_only",
            elements=[],
            screenshot=Screenshot(
                path="screenshots/fixture.png",
                width=1000,
                height=100,
                geometry=CaptureGeometry(
                    capture_id=uuid4(),
                    captured_at=datetime.now(UTC),
                    origin=(-100, 0),
                    physical_size=(1000, 100),
                    dpi=144,
                ),
            ),
        )
        self.current = Observation.model_validate(data)
        return self.current

    def act(self, action):
        assert isinstance(action.root.target, ImageTarget)
        assert action.root.obs_id == self.current.obs_id
        assert action.root.target.capture_id == self.current.screenshot.geometry.capture_id
        x, y = action.root.target.image_point
        targets = [
            item
            for item in self.items
            if item.bbox[0] <= x < item.bbox[0] + item.bbox[2]
            and item.bbox[1] <= y < item.bbox[1] + item.bbox[3]
        ]
        assert len(targets) == 1
        data = action.model_dump(mode="json")
        data["target"] = {"ref": targets[0].ref}
        result = super().act(Action.model_validate(data))
        self.actions[-1] = action
        return result


class SyntheticPerception:
    """Perception output fixture. No desktop/UIA is accessed in this test."""

    def __init__(self, relay):
        self.relay = relay

    def observe(self, wire):
        return SceneObservation(wire=wire, elements=self.relay.items)


@pytest.mark.parametrize(
    "error", ["screen_changed", "unsupported_key", "PRIVATE unexpected server text"]
)
def test_relay_diagnostic_records_only_known_codes(tmp_path, record, error):
    class RejectedRelay(VisualFixture):
        def act(self, action):
            return ActionResult(
                action_id=action.root.action_id, ok=False, error=error, duration_ms=0
            )

    relay = RejectedRelay(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="visual-test")
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=ScriptedModel(log, success_steps(record)),
        settings=BrainSettings(perception_mode="visual_only"),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    brain.visual_perception = SyntheticPerception(relay)
    assert brain.run().status != "success"
    events = [e for e in log.read_all() if e.kind == "act" and e.payload.get("stage") == "returned"]
    assert events and events[0].payload["error_code"] == (
        error if error in {"screen_changed", "unsupported_key"} else None
    )
    assert "PRIVATE" not in (tmp_path / "run_log.jsonl").read_text()


class MatchingDesktop:
    """Painted replay desktop: only delivered inputs can reach persisted rows.

    OCR/detector doubles replay annotations for the actual captured frame. Model
    replies below consume the public prompt, never this desktop's hidden state.
    """

    def __init__(self, fields, *, failure, caret_blink):
        self.fields = fields
        self.failure, self.caret_blink = failure, caret_blink
        self.screen = "login"
        self.inputs = dict.fromkeys(fields, "")
        self.credentials = {}
        self.query = ""
        self.saved_snapshot = {}
        self.actions = []
        self.searches = []
        self.captures = {}
        self.capture_count = 0
        self.result_visible = False
        self.save_confirmed = False

    def health(self):
        return {"ok": True}

    def observe(self):
        controls = []

        def add(key, role, y, *, value="", x=240, width=600, height=32):
            controls.append(dict(key=key, role=role, bbox=(x, y, width, height), value=value))

        if self.screen == "login":
            add("credential.username", "edit", 50, value=self.credentials.get("username", ""))
            add(
                "credential.password",
                "edit",
                100,
                value="***" if self.credentials.get("password") else "",
            )
            add("login", "button", 150)
        elif self.screen == "menu":
            add("open-query", "button", 50)
        elif self.screen == "query":
            add("query", "edit", 50, value=self.query)
            add("search", "button", 100)
            add("new", "button", 150)
            add("results", "table", 200, width=4500, height=650)
            if self.result_visible and self.saved_snapshot:
                add("saved-row", "row", 250, x=260, width=4400, height=80)
                for i, field in enumerate(self.fields):
                    add(
                        field,
                        "cell",
                        280,
                        x=280 + i * 400,
                        width=360,
                        value=self.saved_snapshot[field],
                    )
        elif self.screen == "editor":
            for i, field in enumerate(self.fields):
                add(field, "edit", 50 + i * 50, value=self.inputs[field])
            add("save", "button", 650)
        else:
            add("confirm-save", "button", 100)

        width = 4800 if self.screen == "query" else 1000
        pixels = Image.new("RGB", (width, 900), "white")
        draw = ImageDraw.Draw(pixels)
        blocks = []

        def text(value, bbox):
            blocks.append(OcrBlock(id=len(blocks), text=value, bbox=bbox, confidence=1))
            draw.text((bbox[0], bbox[1]), value, fill="black")

        text("Screen " + self.screen, (5, 5, 180, 18))
        for c in controls:
            x, y, w, h = c["bbox"]
            # Explicit synthetic captions make scripted semantics independent of
            # marker numbering and of the expected record's literal values.
            label_box = (
                (x, y - 24, 220, 18) if c["role"] == "cell" else (max(5, x - 230), y + 6, 220, 18)
            )
            text("K:" + c["role"] + ":" + c["key"], label_box)
            draw.rectangle((x, y, x + w - 1, y + h - 1), outline="gray")
            if c["value"]:
                text(c["value"], (x + 8, y + 7, min(w - 16, 300), 16))
        base = io.BytesIO()
        pixels.save(base, format="PNG")
        content_hash = hashlib.sha1(base.getvalue()).hexdigest()
        if self.caret_blink:
            pixels.putpixel((999, 899), (self.capture_count % 2, 0, 0))
        raw = io.BytesIO()
        pixels.save(raw, format="PNG")
        self.png = raw.getvalue()
        digest = hashlib.sha256(self.png).hexdigest()
        self.captures[digest] = (
            blocks,
            [
                Marker(id=i, bbox=c["bbox"], source="omniparser", confidence=1)
                for i, c in enumerate(controls)
            ],
            len(self.actions),
        )
        self.capture_count += 1
        self.controls = controls
        window = WindowInfo(handle=1, title="Synthetic desktop", process="fixture.exe")
        self.current = Observation(
            obs_id=uuid4(),
            ts=datetime.now(UTC),
            active_window=window,
            windows=[window],
            elements=[],
            observation_mode="visual_only",
            screen_hash=hashlib.sha1(self.png).hexdigest(),
            screenshot=Screenshot(
                path="screenshots/replay.png",
                width=width,
                height=900,
                content_hash=content_hash,
                geometry=CaptureGeometry(
                    capture_id=uuid4(),
                    captured_at=datetime.now(UTC),
                    origin=(-100, 0),
                    physical_size=(width, 900),
                    dpi=144,
                ),
            ),
        )
        return self.current

    def screenshot(self, observed, path):
        assert observed.obs_id == self.current.obs_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.png)
        return path

    def act(self, action):
        item = action.root
        assert item.obs_id == self.current.obs_id
        assert isinstance(item.target, ImageTarget)
        assert item.target.capture_id == self.current.screenshot.geometry.capture_id
        x, y = item.target.image_point
        targets = [
            c
            for c in self.controls
            if c["role"] not in {"table", "row"}
            and c["bbox"][0] <= x < c["bbox"][0] + c["bbox"][2]
            and c["bbox"][1] <= y < c["bbox"][1] + c["bbox"][3]
        ]
        assert len(targets) == 1
        key = targets[0]["key"]
        self.actions.append((self.screen, key, action))
        if item.type == "set_text":
            if key.startswith("credential."):
                self.credentials[key.split(".")[1]] = item.text
            elif key == "query":
                self.query, self.result_visible = item.text, False
            else:
                assert self.screen == "editor"
                self.inputs[key] = item.text
        elif item.type == "key":
            assert item.keys == "tab" and self.screen == "editor"
        elif item.type == "click":
            if key == "login":
                assert self.credentials == {"username": "audit-user", "password": "audit-secret"}
                self.screen = "menu"
            elif key == "open-query":
                self.screen = "query"
            elif key == "search":
                self.searches.append(self.query)
                self.result_visible = True
            elif key == "new":
                assert self.searches and not self.saved_snapshot
                self.screen = "editor"
            elif key == "save":
                self.screen = "dialog"
            elif key == "confirm-save":
                if self.failure != "no_save":
                    self.saved_snapshot = self.inputs.copy()
                    if self.failure == "saved_value":
                        self.saved_snapshot["description"] = "STORAGE MISMATCH"
                # A save acknowledgment is deliberately identical on success
                # and no-op save. Only the new query can distinguish them.
                self.screen, self.query = "query", ""
                self.save_confirmed, self.result_visible = True, False
            else:
                pytest.fail(f"Unexpected synthetic command: {key}")
        else:
            pytest.fail(f"Unexpected action type: {item.type}")
        return ActionResult(action_id=item.action_id, ok=True, post_obs_id=uuid4(), duration_ms=0)


class FrameReplay:
    def __init__(self, desktop, component):
        self.desktop, self.component = desktop, component
        self.action_counts = []

    def read(self, page):
        digest = hashlib.sha256(page.image_path.read_bytes()).hexdigest()
        blocks, boxes, count = self.desktop.captures[digest]
        self.action_counts.append(count)
        return blocks if self.component == "ocr" else boxes


def matched_frame(prompt):
    """Scripted semantic/decision response using only the real provider request."""
    assert prompt.startswith(RULES)
    state, _ = json.JSONDecoder().raw_decode(prompt[len(RULES) :])
    screen = state["screen"]
    blocks = screen["ocr"]
    labels = [b for b in blocks if b["text"].startswith("K:")]
    candidates = [m for m in screen["markers"] if m["source"] == "omniparser"]
    controls, ids = [], {}
    for label in labels:
        _, role, key = label["text"].split(":", 2)
        matches = [
            m
            for m in candidates
            if (
                m["bbox"][1] == label["bbox"][1] + 24 and m["bbox"][0] == label["bbox"][0]
                if role == "cell"
                else m["bbox"][1] + 6 == label["bbox"][1]
                and m["bbox"][0] >= label["bbox"][0] + label["bbox"][2]
            )
        ]
        assert len(matches) == 1
        m = matches[0]
        ids[key] = m["id"]
        texts = [
            i
            for i in m["contained_ocr_ids"]
            if not next(b for b in blocks if b["id"] == i)["text"].startswith("K:")
        ]
        controls.append(
            dict(
                id=m["id"],
                role=role,
                label_ids=[label["id"]],
                text_ids=texts if role in {"edit", "cell"} else [],
                confidence=1,
            )
        )
    for c in controls:
        if c["role"] == "table":
            c["table_state"] = "rows" if "saved-row" in ids else "empty"
        if c["role"] == "row":
            c["parent_id"] = ids["results"]
        if c["role"] == "cell":
            c["parent_id"] = ids["saved-row"]
    kind = next(
        b["text"].removeprefix("Screen ") for b in blocks if b["text"].startswith("Screen ")
    )
    notebook = state["notebook"]
    phase, checkpoint = notebook["phase"], state["execution"]["checkpoint"]
    response = dict(screen_kind=kind, controls=controls, operation="act", steps=[])

    def click(key, intent):
        return dict(target=ids[key], action="click", intent=intent)

    def fill(key, source, intent):
        return dict(target=ids[key], action="fill", source=source, intent=intent)

    if kind == "login":
        step = "login"
        response["steps"] = [fill(k, k, "credential") for k in notebook["available_credentials"]]
        response["steps"].append(click("login", "login"))
    elif kind == "menu":
        step, response["steps"] = "open-duplicate-query", [click("open-query", "navigate")]
    elif phase == "save_result":
        step = "inspect-save"
        confirmation = kind == "dialog"
        response["save_result"] = "confirmation_required" if confirmation else "ready_for_query"
        response["message_targets"] = [ids["confirm-save" if confirmation else "query"]]
        response["steps"] = [
            click("confirm-save", "confirm_save")
            if confirmation
            else fill("query", "hbl_no", "query_input")
        ]
    elif kind == "editor":
        filled = all(
            any(
                c["id"] == ids[f]
                and c["text_ids"]
                and next(b["text"] for b in blocks if b["id"] == c["text_ids"][0]) == value
                for c in controls
            )
            for f, value in notebook["record"].items()
        )
        step = "save-entry" if filled else "fill-entry"
        response["steps"] = (
            [click("save", "save")]
            if filled
            else [
                fill(f, f, "fill")
                for f in notebook["record"]
                if f not in notebook["verified_fields"]
            ]
        )
    elif phase == "enter":
        step, response["steps"] = "open-entry", [click("new", "navigate")]
    elif checkpoint == "query_input":
        step = "enter-duplicate-key" if phase == "duplicate_query" else "enter-final-key"
        response["steps"] = [fill("query", "hbl_no", "query_input")]
    elif checkpoint == "search":
        step = "search-duplicate" if phase == "duplicate_query" else "search-final"
        response["steps"] = [click("search", "search")]
    else:
        final = phase == "final_query"
        step = "verify-final" if final else "check-duplicate"
        response.update(operation="complete" if final else "duplicate_clear", grid=ids["results"])
        if final:
            response["row"] = ids.get("saved-row")
            response["evidence"] = [
                dict(field=f, target=ids[f]) for f in notebook["record"] if f in ids
            ]
    response["procedure_step"] = step
    return response


@pytest.mark.parametrize(
    "failure,caret_blink", [(None, False), (None, True), ("saved_value", False), ("no_save", False)]
)
def test_screen_match_workflow_uses_actual_saved_snapshot(tmp_path, record, failure, caret_blink):
    relay = MatchingDesktop(list(values_for(record)), failure=failure, caret_blink=caret_blink)
    ocr, detector = FrameReplay(relay, "ocr"), FrameReplay(relay, "detector")
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="visual-test")
    model = ScriptedModel(log, repeat(matched_frame))
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=model,
        username="audit-user",
        password="audit-secret",
        settings=BrainSettings(
            perception_mode="visual_only",
            screen_matching=True,
            visual_screen_fill=True,
            max_steps=40,
            procedure_path=Path("examples/shipment-procedure.json"),
        ),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=ocr,
    )
    brain.visual_perception.detector = detector
    result = brain.run()
    assert relay.inputs == values_for(record), result
    assert relay.save_confirmed and relay.searches == [record.fields.hbl_no.value] * 2, result
    assert relay.saved_snapshot is not relay.inputs
    assert "audit-secret" not in "".join(model.prompts)
    assert "audit-secret" not in (tmp_path / "run_log.jsonl").read_text()
    events = log.read_all()
    reviews = [e.payload for e in events if e.payload.get("check") == "current_evidence_review"]
    commands = [key for _, key, action in relay.actions if action.root.type == "click"]
    assert commands == ["login", "open-query", "search", "new", "save", "confirm-save", "search"]
    if failure:
        assert result.status == "stopped", result
        assert relay.saved_snapshot != relay.inputs
        if failure == "saved_value":
            assert result.reason == "saved_field_conflict:description"
            assert reviews[-1]["reason"] == result.reason
            assert reviews[-1]["missing_fields"] == ["description"]
        else:
            assert result.reason == "repeated_subgoal_failure:final_query:decision"
            assert reviews[-1]["reason"] == "saved_record_evidence_missing"
            assert reviews[-1]["matching_rows"] == 0
            assert set(reviews[-1]["missing_fields"]) == set(brain.values)
            assert not brain.saved_evidence.query_row_seen
    else:
        assert result.status == "success", result
        assert result.reason == "saved_and_verified_awaiting_approval"
        assert relay.saved_snapshot == relay.inputs
        assert brain.saved_evidence.query_row_seen
        assert set(brain.saved_evidence.fields) == set(brain.values)
        assert (tmp_path / "saved-evidence.json").exists()
    # Real perception and measured ScreenMatch calls run at boundaries only.
    assert ocr.action_counts == detector.action_counts
    assert not any(0 < count < 3 for count in ocr.action_counts)
    assert not any(7 < count < 19 for count in ocr.action_counts)
    calls = [e for e in events if e.kind == "model_call"]
    assert calls and len(calls) == len(model.prompts)
    assert all(e.payload["status"] == "success" and e.cost.usd == 0 for e in calls)


@pytest.mark.parametrize("after_blur", ["correct", "wrong_value", "still_focused", "disabled"])
def test_caret_recheck_uses_fresh_evidence_without_cleaning_values(tmp_path, record, after_blur):
    class CaretRelay(VisualFixture):
        focused_ref = None
        blurred = False

        def act(self, action):
            result = super().act(action)
            if action.root.type in {"set_text", "type_text"} and self.mode == "editor":
                self.focused_ref = "e20"
            elif action.root.type == "key":
                assert action.root.keys == "tab"
                self.blurred = True
                if after_blur != "still_focused":
                    self.focused_ref = None
            return result

    class CaretPerception(SyntheticPerception):
        def observe(self, wire):
            scene = super().observe(wire)
            elements = [
                item.model_copy(
                    update={
                        "focused": self.relay.focused_ref == item.ref,
                        "value": (item.value or "") + "|",
                    }
                )
                if item.ref == "e20"
                and item.value
                and (not self.relay.blurred or after_blur != "correct")
                else item
                for item in scene.elements
            ]
            return scene.model_copy(update={"elements": elements})

    relay = CaretRelay(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="visual-test")
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=ScriptedModel(log, success_steps(record)),
        settings=BrainSettings(
            perception_mode="visual_only", visual_input_blur_recheck=after_blur != "disabled"
        ),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    brain.visual_perception = CaretPerception(relay)
    result = brain.run()
    keys = [action for action in relay.actions if action.root.type == "key"]
    assert len(keys) == (0 if after_blur == "disabled" else 1)
    if after_blur == "correct":
        assert result.status == "success", result
        assert relay.saved
        assert len(relay.actions) == 18
    else:
        assert result.status != "success"
        assert not relay.saved
