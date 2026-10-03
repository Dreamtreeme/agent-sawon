"""A learned method reaches the existing visual executor with new business inputs."""

import hashlib
from uuid import uuid4

import pytest
from PIL import Image
from sawon_interfaces import ActionResult
from test_brain import ScriptedModel
from test_visual_scene import wire

from brain.cache.methods import MethodLibrary, method_from_match
from brain.graph.engine import Brain
from brain.graph.matching import MethodBinding, ScreenMatch
from brain.runtime.storage import RunLog, atomic_write_json
from brain.settings import BrainSettings
from brain.task_instruction import TaskInputRecord, TaskInstruction
from brain.visual.marker_contract import model_controls
from brain.visual.markers import Marker, build_markers
from brain.visual.scene import ControlMeaning
from pipeline.schemas import OcrBlock


def catalog(label="Customer", shift=0):
    blocks = [
        OcrBlock(id=80, text=label, bbox=(10, 25 + shift, 65, 15), confidence=0.99),
        OcrBlock(id=90, text="Reference", bbox=(10, 95 + shift, 65, 15), confidence=0.99),
    ]
    boxes = [
        Marker(id=0, bbox=(100, 20 + shift, 150, 30), source="omniparser", confidence=0.99),
        Marker(id=1, bbox=(100, 90 + shift, 150, 30), source="omniparser", confidence=0.99),
    ]
    markers = build_markers(blocks, boxes, 400, 200)
    inputs = [marker for marker in markers if marker.source == "omniparser"]
    screen = {
        "image_size": [400, 200],
        "ocr": [block.model_dump(mode="json") for block in blocks],
        "markers": [marker.model_dump(mode="json") for marker in markers],
    }
    response = ScreenMatch(
        screen_kind="editor",
        procedure_step="task-action",
        purpose="Enter customer and reference",
        controls=[
            ControlMeaning(id=marker.id, role="edit", label_ids=[block.id], confidence=0.99)
            for marker, block in zip(inputs, blocks, strict=True)
        ],
        steps=[
            {"target": marker.id, "action": "fill", "source": source, "intent": "fill"}
            for marker, source in zip(inputs, ("customer", "reference"), strict=True)
        ],
        expected_result="The current customer and reference are entered.",
    )
    return blocks, boxes, screen, response


def setup(tmp_path, *, changed_label=False, fail_second=False):
    task = TaskInstruction(
        name="Business entry",
        objective="Enter supplied business data",
        completion_conditions=["Saved"],
    )
    task_path = tmp_path / "task.json"
    atomic_write_json(task_path, task)
    _, _, source_screen, source_response = catalog()
    method = method_from_match(
        source_screen,
        source_response.model_dump(mode="json"),
        method_id="business-entry",
        purpose=source_response.purpose,
        task_objective=task.objective,
    )
    assert method is not None
    source = MethodLibrary(tmp_path / "source-methods.json", enabled=True)
    source.upsert(method)
    blocks, boxes, current_screen, current_response = catalog(
        "Account" if changed_label else "Customer", shift=20
    )
    current_response = current_response.model_copy(update={"method_id": method.id})
    model_response = {
        **current_response.model_dump(mode="json"),
        "controls": model_controls(current_response.controls, current_screen),
    }

    class Relay:
        def __init__(self):
            self.actions = []
            self.entered = []

        def observe(self):
            observed = wire()
            return observed.model_copy(
                update={"screen_hash": hashlib.sha1(str(self.entered).encode()).hexdigest()}
            )

        def screenshot(self, observed, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (400, 200), "white").save(path)
            return path

        def act(self, action):
            self.actions.append(action)
            ok = not (fail_second and len(self.actions) == 2)
            if ok and action.root.type == "set_text":
                self.entered.append(action.root.text)
            return ActionResult(
                action_id=action.root.action_id,
                ok=ok,
                duration_ms=1,
                post_obs_id=uuid4() if ok else None,
                error=None if ok else "action_failed",
            )

    class Ocr:
        def read(self, page):
            return blocks

    class Detector:
        def read(self, page):
            return boxes

    relay = Relay()
    record = TaskInputRecord(
        case_id="method-runtime", inputs={"customer": "New customer B", "reference": "ORDER-B"}
    )
    log = RunLog(tmp_path / "case", case_id=record.case_id, agent_id="test")
    model = ScriptedModel(log, [model_response] if changed_label else [])
    brain = Brain(
        record=record,
        goal=task.objective,
        relay=relay,
        model=model,
        settings=BrainSettings(
            task_path=task_path,
            perception_mode="visual_only",
            screen_matching=True,
            visual_screen_fill=True,
            visual_model_led_semantics=True,
            visual_model_only_review=True,
            methods={"enabled": True, "path": source.path},
        ),
        case_dir=tmp_path / "case",
        run_log=log,
        visual_ocr=Ocr(),
    )
    brain.visual_perception.detector = Detector()
    assert brain.methods.apply(current_screen) is None  # A matching saved detail is not a goal.
    if not changed_label:
        # A preceding navigation decision selects the next purpose; the worker then
        # binds that purpose on the newly observed form without another model call.
        brain.methods.remember(
            {"ocr": [], "markers": []},
            ScreenMatch(
                screen_kind="menu",
                procedure_step="task-action",
                purpose=method.purpose,
                method_id=method.id,
                method_bindings=[
                    {"slot": "customer", "source": "customer"},
                    {"slot": "reference", "source": "reference"},
                ],
                controls=[ControlMeaning(id=7, role="button", confidence=1)],
                steps=[{"target": 7, "action": "click", "intent": "navigate", "point": [20, 20]}],
            ),
        )
    return brain, model, relay


@pytest.mark.parametrize("observe_again", [False, True])
def test_learned_method_uses_current_scene_and_executor_without_model(tmp_path, observe_again):
    brain, model, relay = setup(tmp_path)
    brain._observe()
    first_id = brain.observation.obs_id
    if observe_again:
        brain._observe()
        assert brain.observation.obs_id != first_id
    assert brain._select_node({}) == {"next": "review"}
    assert brain.action_plan is not None
    assert len(brain.action_plan.actions) == 3  # Two entries and the common final blur.
    assert brain.action_plan.actions[0].decision.subgoal == "Enter customer and reference"
    assert brain._review_node({}) == {"next": "act"}
    assert brain._act_node({}) == {"next": "verify"}
    assert relay.entered == ["New customer B", "ORDER-B"]
    assert [list(action.root.target.image_point) for action in relay.actions[:2]] == [
        [175, 55],
        [175, 125],
    ]
    assert model.prompts == []
    assert not any(event.kind == "model_call" for event in brain.log.read_all())
    assert brain.methods.history[-1]["origin"] == "reuse"
    assert brain.methods.history[-1]["status"] == "delivered"
    assert brain.methods.used
    # A rejected new source mapping must not reactivate the previous valid one.
    _, _, current, response = catalog(shift=20)
    brain.methods.remember(
        current,
        response.model_copy(
            update={
                "method_id": "business-entry",
                "method_bindings": [MethodBinding(slot="customer", source="unknown_input")],
            }
        ),
    )
    assert brain.methods.active_method_id is None
    assert brain.methods.apply(current) is None


@pytest.mark.parametrize("fail_second", [False, True])
def test_model_adaptation_is_retained_only_after_whole_batch_delivery(tmp_path, fail_second):
    brain, model, relay = setup(tmp_path, changed_label=True, fail_second=fail_second)
    brain._observe()
    assert len(model.prompts) == 1
    assert len(brain.methods.library.methods()[0].action_groups) == 1
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    outcome = brain._act_node({})
    groups = brain.methods.library.methods()[0].action_groups
    if fail_second:
        assert outcome["next"] != "verify"
        assert len(groups) == 1
        assert brain.methods.history[-1]["status"] == "interrupted"
        assert relay.entered == ["New customer B"]
    else:
        assert outcome == {"next": "verify"}
        assert len(groups) == 2
        assert brain.methods.history[-1]["origin"] == "adaptation"
        current_screen = brain.visual_perception.planning_context()[0].screen
        bound = brain.methods.library.bind_with_context(
            "business-entry", {}, current_screen, brain.values
        )
        assert bound is not None
        assert bound.group_id == groups[-1].id
