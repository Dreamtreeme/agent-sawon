"""A learned method reaches the existing visual executor with new business inputs."""

import hashlib
import json
from copy import deepcopy
from functools import cache
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sawon_interfaces import ActionResult
from test_brain import ScriptedModel
from test_methods import completion_response, completion_scene, learned
from test_visual_scene import wire

import brain.graph.screen_decision as screen_decision
from brain.cache.methods import (
    MethodAction,
    MethodDocument,
    MethodLibrary,
    MethodTarget,
    compile_method_candidate,
    method_completion_from_match,
    method_from_match,
)
from brain.graph.engine import Brain
from brain.graph.memory_retrieval import open_memory_library
from brain.graph.screen_decision import MethodBinding, ScreenMatch
from brain.graph.screen_mapping import ScreenMapping
from brain.settings import BrainSettings
from brain.task_instruction import (
    ObservedStepResult,
    TaskInputRecord,
    TaskInstruction,
    TaughtProcedureStep,
)
from brain.visual.action_rebind import perceptual_hash
from brain.visual.markers import Marker, build_markers, ground_meanings
from brain.visual.scene import ControlMeaning, bind_scene
from pipeline.schemas import OcrBlock
from sawon_runtime.storage import RunLog, atomic_write_json


@cache
def synthetic_screen_phash():
    # Synthetic white capture: floating-point DCT rounding differs across platforms.
    # These tests exercise memory routing, not a platform-specific numeric hash.
    capture = BytesIO()
    Image.new("RGB", (400, 200), "white").save(capture, format="PNG")
    capture.seek(0)
    return perceptual_hash(capture)


def test_compiled_only_library_does_not_replay_raw_model_trace(tmp_path):
    cold = learned()
    warm = cold.model_copy(update={"id": "compiled", "artifact_kind": "warm_compiled"})
    source = tmp_path / "source.json"
    atomic_write_json(source, MethodDocument(methods=[cold, warm]))

    library = open_memory_library(source, tmp_path / "case", enabled=True, compiled_only=True)

    assert [method.id for method in library.methods()] == ["compiled"]


@pytest.mark.parametrize("similar", [True, False], ids=["memory", "scanning"])
def test_synthetic_similarity_routes_before_ocr_and_shared_context(tmp_path, similar, monkeypatch):
    # Synthetic capture/OCR/model responses; actual scanner, memory and context functions.
    brain, model, _ = setup(tmp_path, changed_label=True)
    if not similar:
        library = brain.memory_retrieval.library
        method = library.methods()[0]
        groups = [
            group.model_copy(update={"screen_phash": group.screen_phash ^ ((1 << 64) - 1)})
            for group in method.action_groups
        ]
        atomic_write_json(
            library.path,
            MethodDocument(methods=[method.model_copy(update={"action_groups": groups})]),
        )
    order = []
    scanner = brain.screen_scanner
    recognize, read, detect = (
        scanner.retrieve_screen_memory,
        scanner.ocr.read,
        scanner.detector.read,
    )

    def recognition(screen):
        order.append("similarity")
        assert "ocr" not in screen
        state = brain.memory_retrieval.state
        pending, used, verified = state.pending, set(state.used), dict(brain.verified)
        result = recognize(screen)
        assert state.pending is pending and state.used == used and brain.verified == verified
        return result

    def ocr(page):
        order.append("current_values")
        return read(page)

    def detector(page, **kwargs):
        order.append("scanning")
        return detect(page, **kwargs)

    original_context = screen_decision.recall_context
    original_pattern = screen_decision.match_screen_pattern
    original_generate = model._generate

    def context(request, *, projected_screen=None):
        order.append("context_recall")
        assert request.screen["observation_route"] == (
            "memory_retrieval" if similar else "scanning"
        )
        before = deepcopy(request)
        restored = original_context(request, projected_screen=projected_screen)
        assert request == before  # Context Recall reads state; it does not choose or execute.
        assert restored["notebook"]["remaining_inputs"] == ["customer", "reference"]
        if similar:
            assert {f["source"] for f in request.screen["memory_fields"]} == {
                "customer",
                "reference",
            }
            assert request.screen["retrieved_procedure"][0]["steps"][0]["source"] == "customer"
        return restored

    def pattern(*args, **kwargs):
        order.append("pattern_matching")
        return original_pattern(*args, **kwargs)

    def generate(*args, **kwargs):
        order.append("decision")
        return original_generate(*args, **kwargs)

    scanner.retrieve_screen_memory, scanner.ocr.read = recognition, ocr
    scanner.detector.read = detector
    monkeypatch.setattr(screen_decision, "recall_context", context)
    monkeypatch.setattr(screen_decision, "match_screen_pattern", pattern)
    monkeypatch.setattr(model, "_generate", generate)
    brain._observe()

    assert order == [
        "similarity",
        "current_values",
        *([] if similar else ["scanning"]),
        "pattern_matching",
        "context_recall",  # This fixture needs model recovery after pattern selection.
        "decision",
    ]
    assert '"remembered_fields"' not in model.prompts[0]  # Unscoped geometry is not a node.
    assert brain._select_node() == "review"
    assert [a.decision.action.source for a in brain.action_plan.actions] == [
        "customer",
        "reference",
    ]
    events = brain.log.read_all()
    recognition_event = next(e for e in events if e.payload.get("check") == "screen_similarity")
    assert recognition_event.payload["matched"] is similar
    assert any(e.payload.get("check") == "screen_pattern_selection" for e in events)


@pytest.mark.parametrize("identical", [False, True], ids=["library", "identical-catalog"])
def test_synthetic_provider_refinement_reenters_scanning_with_same_capture(
    tmp_path, monkeypatch, identical
):
    # Synthetic image/OCR/provider replies; real provider parsing, decision and scanner functions.
    brain, model, relay = setup(tmp_path, changed_label=True)
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    next_plan = next(model.decisions)
    if identical:
        brain.method_library.enabled = False
        model.decisions = iter([next_plan])
        brain._observe()
        model.prompts.clear()
        assert brain.screen_scanner._catalog.memory is None
    model.decisions = iter(
        [
            {
                "fields": [],
                "steps": [],
                "needs_refinement": True,
                "purpose": "Synthetic: a required visible control is missing from memory.",
            },
            next_plan,
        ]
    )
    calls = []

    def count(obj, name, label):
        original = getattr(obj, name)

        def invoke(*args, **kwargs):
            calls.append(label)
            return original(*args, **kwargs)

        monkeypatch.setattr(obj, name, invoke)

    scanner = brain.screen_scanner
    count(relay, "screenshot", "capture")
    count(scanner.ocr, "read", "ocr")
    count(scanner.detector, "read", "detector")
    count(model, "_generate", "model")
    assert ScreenMatch.model_json_schema()["properties"]["needs_refinement"]["type"] == "boolean"

    brain._observe()

    assert calls == ["capture", "ocr", "model", "detector", "model"]
    # Similar run-local geometry no longer injects fields from an unselected node.
    assert '"remembered_fields"' not in model.prompts[0]
    assert '"remembered_fields"' not in model.prompts[1]
    assert brain._select_node() == "review"
    assert [a.decision.action.source for a in brain.action_plan.actions] == [
        "customer",
        "reference",
    ]
    events = brain.log.read_all()
    assert sum(e.payload.get("check") == "screen_memory_fallback" for e in events) == 1
    assert not any(e.payload.get("check") == "missing_match_actions_completed" for e in events)
    assert relay.actions == []


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
        "screen_phash": synthetic_screen_phash(),
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


def setup(
    tmp_path,
    *,
    changed_label=False,
    fail_second=False,
    promotion_path=None,
):
    task = TaskInstruction(
        name="Business entry",
        objective="Enter supplied business data",
        completion_conditions=["Saved"],
    )
    task_path = tmp_path / "task.json"
    atomic_write_json(task_path, task)
    _, _, source_screen, source_response = catalog(shift=20)
    method = method_from_match(
        source_screen,
        source_response.model_dump(mode="json"),
        method_id="business-entry",
        purpose=source_response.purpose,
        task_objective=task.objective,
        task_contract_sha256=hashlib.sha256(task_path.read_bytes()).hexdigest(),
    )
    assert method is not None
    source = MethodLibrary(tmp_path / "source-methods.json", enabled=True)
    method = method.model_copy(update={"artifact_kind": "warm_compiled"})
    source.upsert(method)
    blocks, boxes, current_screen, current_response = catalog(
        "Account" if changed_label else "Customer", shift=20
    )
    current_response = current_response.model_copy(update={"method_id": method.id})
    names = [block.text for block in blocks]
    model_response = {
        "screen_kind": "editor",
        "procedure_step": "task-action",
        "method_id": method.id,
        "purpose": "Enter customer and reference",
        "fields": {
            name: {"source": source, "role": "edit", "bbox": list(box.bbox), "value": ""}
            for name, box, source in zip(names, boxes, ("customer", "reference"), strict=True)
        },
        "steps": [
            {
                "target": name,
                "action": "fill",
                "intent": "fill",
                "point": [box.bbox[0] + 25, box.bbox[1] + 10],
            }
            for name, box in zip(names, boxes, strict=True)
        ],
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
    model = ScriptedModel(log, [model_response, model_response])
    brain = Brain(
        record=record,
        goal=task.objective,
        relay=relay,
        model=model,
        settings=BrainSettings(
            task_path=task_path,
            screen_matching=True,
            visual_screen_fill=True,
            visual_model_led_semantics=True,
            visual_model_only_review=True,
            methods={
                "enabled": True,
                "compiled_only": True,
                "path": source.path,
                "promotion_path": promotion_path,
            },
        ),
        case_dir=tmp_path / "case",
        run_log=log,
        visual_ocr=Ocr(),
    )
    brain.screen_scanner.detector = Detector()
    assert (
        brain.memory_retrieval.match_pattern(current_screen) is None
    )  # A matching saved detail is not a goal.
    if not changed_label:
        # A preceding navigation decision selects the next purpose; the worker then
        # binds that purpose on the newly observed form without another model call.
        brain.memory_retrieval.remember(
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


def test_learned_method_is_scoped_to_exact_user_education_file(tmp_path):
    brain, _, _ = setup(tmp_path)

    assert brain.memory_retrieval.prompt_context()["methods"]
    brain.task_instruction_sha256 = "0" * 64

    assert brain.memory_retrieval.prompt_context()["methods"] == []
    assert brain.memory_retrieval.context()["methods"] == []


def test_synthetic_phash_memory_jitter_reaches_notebook_without_duplicate_controls(tmp_path):
    # Synthetic reproduction of 078: independent memory/evidence boxes differ by one pixel.
    brain, model, relay = setup(tmp_path)
    library = brain.memory_retrieval.library
    method = library.methods()[0]
    group = method.action_groups[0]
    shifted = [
        target.model_copy(
            update={
                "bbox": (target.bbox[0], target.bbox[1] - 1, target.bbox[2], target.bbox[3] + 1)
            }
        )
        for target in group.targets
    ]
    library.upsert(
        method.model_copy(
            update={
                "action_groups": [
                    group.model_copy(
                        update={"observed_fields": shifted, "screen_phash": group.screen_phash ^ 1}
                    )
                ]
            }
        )
    )
    blocks = brain.screen_scanner.ocr.read(None)
    for index, (source, target) in enumerate(
        zip(("customer", "reference"), group.targets, strict=True)
    ):
        x, y, _, _ = target.bbox
        blocks.append(
            OcrBlock(
                id=180 + index,
                text=brain.values[source],
                bbox=(x + 5, y + 5, 130, 15),
                confidence=1,
            )
        )

    reply = next(model.decisions)
    for field in reply["fields"].values():
        field["value"] = brain.values[field["source"]]
    reply.update(operation="stop", steps=[])
    model.decisions = iter([reply])
    brain._observe()

    assert set(brain.verified) == {"customer", "reference"}
    inputs = [element for element in brain.observation.elements if element.role == "edit"]
    assert len(inputs) == 2
    assert all(element.enabled and element.value for element in inputs)
    assert len(model.prompts) == 1 and relay.actions == []
    recognition = next(
        e.payload for e in brain.log.read_all() if e.payload.get("check") == "screen_similarity"
    )
    assert recognition["matched"] and recognition["distance"] == 1


def test_synthetic_geometry_evidence_alone_does_not_update_model_owned_notebook(tmp_path):
    # Synthetic current OCR, using the real binder, scene projection and memo update.
    brain, model, relay = setup(tmp_path)
    blocks, _, screen, plan = catalog(shift=20)
    blocks.extend(
        [
            OcrBlock(id=180, text=brain.values["customer"], bbox=(105, 45, 130, 15), confidence=1),
            OcrBlock(
                id=190,
                text=brain.values["reference"],
                bbox=(105, 115, 100, 15),
                confidence=1,
            ),
        ]
    )
    screen["ocr"] = [block.model_dump(mode="json") for block in blocks]
    response = ScreenMatch(
        screen_kind="editor",
        procedure_step="task-action",
        controls=plan.controls,
        evidence=[dict(field=step.source, target=step.target) for step in plan.steps],
        steps=[],
    )
    meanings = brain._groundable_interpretation(response, screen)
    markers = [
        Marker(
            id=item["id"],
            source=item["source"],
            bbox=item["bbox"],
            confidence=1,
            text_ids=item.get("text_ids", []),
        )
        for item in screen["markers"]
    ]
    brain.observation = bind_scene(wire(), blocks, ground_meanings(meanings, markers, blocks))
    brain._remember_model_task_evidence(response)

    assert brain.verified == {}
    assert brain.screen_nodes == {}
    assert not brain.save_issued
    assert model.prompts == [] and relay.actions == []


@pytest.mark.parametrize("observe_again", [False, True])
def test_synthetic_missing_layout_uses_model_coordinates_at_executor(tmp_path, observe_again):
    brain, model, relay = setup(tmp_path)
    brain._observe()
    first_id = brain.observation.obs_id
    assert any(event.payload.get("check") == "screen_mapping" for event in brain.log.read_all())
    if observe_again:
        brain._observe()
        assert brain.observation.obs_id != first_id
    assert brain._select_node() == "review"
    assert brain.action_plan is not None
    assert len(brain.action_plan.actions) == 2
    assert brain.action_plan.actions[0].decision.subgoal == "Enter customer and reference"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert brain.pending is not None
    assert relay.entered == ["New customer B", "ORDER-B"]
    assert [list(action.root.target.image_point) for action in relay.actions[:2]] == [
        [125, 50],
        [125, 120],
    ]
    assert len(model.prompts) == 1 + int(observe_again)
    assert any(
        event.payload.get("check") == "memory_retrieval_exception"
        and event.payload.get("reason") == "missing_screen_memory"
        for event in brain.log.read_all()
    )
    assert sum(event.kind == "model_call" for event in brain.log.read_all()) == len(model.prompts)
    reuse = next(
        event
        for event in brain.log.read_all()
        if event.kind == "cache_hit" and event.payload.get("check") == "method_bound"
    )
    assert reuse.payload["task_contract_sha256"] == brain.task_instruction_sha256
    assert reuse.payload["artifact_kind"] == "warm_compiled"
    assert reuse.payload["revision"] == 1
    assert reuse.payload["source_transitions"] == []
    education = next(
        event
        for event in brain.log.read_all()
        if event.payload.get("check") == "user_education_loaded"
    )
    assert education.payload["name"] == brain.task_instruction.name
    assert len(education.payload["sha256"]) == 64
    assert education.payload["procedure_step_ids"] == [
        step.id for step in brain.task_instruction.procedure
    ]
    assert brain.memory_retrieval.state.history[-1]["origin"] == "adaptation"
    assert brain.memory_retrieval.state.history[-1]["status"] == "delivered"
    assert brain.memory_retrieval.state.used
    # A rejected new source mapping must not reactivate the previous valid one.
    _, _, current, response = catalog(shift=20)
    previous_bindings = deepcopy(brain.memory_retrieval.state.bindings)
    brain.memory_retrieval.remember(
        current,
        response.model_copy(
            update={
                "method_id": "business-entry",
                "method_bindings": [MethodBinding(slot="customer", source="unknown_input")],
            }
        ),
    )
    assert brain.memory_retrieval.state.active_method_id == "business-entry"
    assert brain.memory_retrieval.state.pending is None
    assert brain.memory_retrieval.state.bindings == previous_bindings
    assert "business-entry" in brain.memory_retrieval.state.blocked_method_ids
    assert brain.memory_retrieval.match_pattern(current) is None
    brain.memory_retrieval.remember(
        current, response.model_copy(update={"method_id": "business-entry"})
    )
    assert brain.memory_retrieval.state.pending is not None
    assert "business-entry" not in brain.memory_retrieval.state.blocked_method_ids


def test_synthetic_saved_review_requires_current_model_values_after_structure_retrieval(tmp_path):
    # Synthetic successful source and fake desktop; real compile/bind/graph/memo/execution paths.
    brain, model, relay = setup(tmp_path)
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    brain.task_instruction = brain.task_instruction.model_copy(
        update={
            "procedure": [
                TaughtProcedureStep(
                    id="open-detail",
                    instruction="Open the saved record",
                    completion="Detail opened",
                ),
                TaughtProcedureStep(
                    id="verify-detail",
                    instruction="Review the saved values across tabs",
                    completion="All saved values match",
                ),
            ]
        }
    )
    atomic_write_json(brain.settings.task_path, brain.task_instruction)
    brain.task_instruction_sha256 = hashlib.sha256(
        brain.settings.task_path.read_bytes()
    ).hexdigest()
    runtime = brain.memory_retrieval
    original = runtime.library.methods()[0]
    source_screen = {"screen_phash": synthetic_screen_phash(), "image_size": [400, 200]}
    source_values = {**brain.values, "customer": "Previous customer A"}
    mapping = ScreenMapping(
        screen_kind="dialog",
        screen_path=["Saved detail"],
        taught_step_id="verify-detail",
        observed_step_results=[
            {
                "step_id": "open-detail",
                "observed_result": "Saved detail for Previous customer A opened",
                "completed": True,
            }
        ],
        fields={
            "Customer": {
                "role": "text",
                "bbox": [100, 40, 150, 30],
                "source": "customer",
                "value": source_values["customer"],
            },
            "More": {"role": "tabitem", "bbox": [300, 140, 80, 30]},
        },
        steps=[{"target": "More", "action": "click", "intent": "select_tab", "point": [340, 155]}],
    )
    method = method_from_match(
        source_screen,
        mapping.local_payload(source_screen, source_values),
        method_id=original.id,
        purpose="Review saved values",
        slots=original.slots,
        task_objective=brain.task_instruction.objective,
        task_contract_sha256=brain.task_instruction_sha256,
        values=source_values,
    )
    assert method is not None
    method = compile_method_candidate(method, source_values).model_copy(
        update={"artifact_kind": "warm_compiled"}
    )
    # Synthetic final observation accepted from cumulative saved-detail evidence,
    # just like 135: no redundant completion_evidence annotation was emitted.
    final_screen = {**source_screen, "ocr": []}
    final_mapping = ScreenMapping(
        screen_kind="dialog",
        screen_path=["Saved detail"],
        operation="complete",
        taught_step_id="verify-detail",
        observed_step_results=[
            ObservedStepResult(
                step_id="verify-detail", observed_result="Both saved fields match", completed=True
            )
        ],
        fields={
            "Reference": {
                "role": "text",
                "bbox": [100, 110, 150, 30],
                "source": "reference",
                "value": source_values["reference"],
            }
        },
    )
    method = method_completion_from_match(
        final_screen,
        final_mapping.local_payload(final_screen, source_values),
        method,
        source_values,
        verified_fields=source_values,
    )
    assert method is not None and method.completion is not None
    assert all(not item.condition_indexes for item in method.completion.targets)
    atomic_write_json(runtime.library.path, MethodDocument(methods=[method]))
    brain.save_issued = brain.saved_detail_transition_pending = True
    brain.saved_detail_transition_step = brain.active_taught_step = "open-detail"

    # Fake current replies use this run's data, never the successful source's values.
    current_mapping = mapping.model_dump(exclude_unset=True)
    current_mapping["fields"]["Customer"]["value"] = brain.values["customer"]
    current_mapping["observed_step_results"][0]["observed_result"] = "Current detail opened"
    model.decisions = iter([current_mapping, final_mapping.model_dump(exclude_unset=True)])

    brain._observe()

    assert brain.observation.screen_kind == "dialog"
    assert brain.model_saved_detail_review and not brain.saved_detail_transition_pending
    assert brain.post_save_verified == {"customer"}
    remembered = brain.screen_nodes['["Saved detail"]']["fields"]["customer"]["last_observation"]
    assert remembered["value"] == brain.values["customer"] == "New customer B"
    assert remembered["scope"] == "saved_detail"
    assert brain.completed_taught_steps == {"open-detail"}
    assert brain.active_taught_step == "verify-detail"
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert [action.root.target.image_point for action in relay.actions] == [(340, 155)]
    assert len(model.prompts) == 1
    assert brain._verify_node() == "review"
    assert brain.visual_proposal.operation == "complete"
    assert brain.visual_proposal.completion_evidence == []
    assert brain.post_save_verified == {"customer", "reference"}
    assert brain.completed_taught_steps == {"open-detail", "verify-detail"}
    assert brain._select_node() == "finish"
    assert brain.result.status == "success" and len(model.prompts) == 2
    events = brain.log.read_all()
    assert any(event.payload.get("check") == "screen_similarity" for event in events)
    assert any(event.payload.get("reason") == "current_values_not_observed" for event in events)


@pytest.mark.parametrize("corrupt_ocr", [False, True], ids=["readable-ocr", "corrupted-ocr"])
def test_synthetic_retrieval_inputs_new_values_then_model_confirms_not_ocr(tmp_path, corrupt_ocr):
    # Synthetic OCR, capture and provider reply; real observation/decision/executor/memory code.
    brain, model, relay = setup(tmp_path)
    runtime = brain.memory_retrieval
    method = runtime.library.methods()[0]
    first = method.action_groups[0].model_copy(
        update={
            "screen_path": ["Entry"],
            "trace_index": 0,
        }
    )
    button = MethodTarget(name="Next", role="button", bbox=(300, 140, 80, 30), point=(340, 155))
    second = first.model_copy(
        update={
            "id": "next",
            "trace_index": 1,
            "predecessor_group_ids": [first.id],
            "targets": [button],
            "observed_fields": first.targets,
            "field_sources": {action.target: action.slot for action in first.actions},
            "actions": [
                MethodAction(target="Next", action="click", intent="navigate", point=button.point)
            ],
        }
    )
    # Final proof is learned from a synthetic successful source, not asserted as current success.
    final_screen = {"screen_phash": first.screen_phash, "image_size": [400, 200]}
    final_mapping = ScreenMapping(
        screen_kind="editor",
        screen_path=["Entry"],
        operation="complete",
        fields={
            target.name: dict(
                role=target.role,
                bbox=list(target.bbox),
                source=action.slot,
                value=brain.values[action.slot],
            )
            for target, action in zip(first.targets, first.actions, strict=True)
        },
        completion_evidence=[dict(condition_index=0, target=first.targets[0].name)],
    )
    method = method.model_copy(update={"action_groups": [first, second]})
    method = method_completion_from_match(
        final_screen, final_mapping.local_payload(final_screen), method, brain.values
    )
    assert method.completion is not None
    atomic_write_json(runtime.library.path, MethodDocument(methods=[method]))
    current_review = final_mapping.model_dump(exclude_unset=True)
    current_review.update(
        operation="act",
        completion_evidence=[],
        steps=[{"target": "Next", "action": "click", "intent": "navigate", "point": [340, 155]}],
    )
    current_review["fields"]["Next"] = dict(role="button", bbox=[300, 140, 80, 30])
    model.decisions = iter([current_review, final_mapping.model_dump(exclude_unset=True)])
    labels = brain.screen_scanner.ocr.read(None)

    def read_current_values(page):
        if not relay.entered:
            return labels
        return [
            *labels,
            *[
                OcrBlock(
                    id=200 + i,
                    text="wrong" if corrupt_ocr and i == 0 else brain.values[action.slot],
                    bbox=(target.bbox[0] + 2, target.bbox[1] + 2, 140, 20),
                    confidence=1,
                )
                for i, (target, action) in enumerate(zip(first.targets, first.actions, strict=True))
            ],
        ]

    brain.screen_scanner.ocr.read = read_current_values
    brain._observe()
    assert brain.current_screen_path == ["Entry"] and not brain.verified
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert relay.entered == ["New customer B", "ORDER-B"]
    assert [list(action.root.target.image_point) for action in relay.actions] == [
        list(action.point) for action in first.actions
    ]
    assert model.prompts == []
    assert runtime.state.history[-1]["origin"] == "reuse"
    # A prior viewport's model fact survives when absent from the current frame.
    hidden = {
        "label": "Earlier field",
        "role": "text",
        "last_observation": {
            "value": "Already checked",
            "scope": "entry",
            "observation_number": 0,
            "observation_id": "earlier",
        },
    }
    brain.screen_nodes['["Entry"]']["fields"]["offscreen"] = hidden
    assert brain._verify_node() == "review"
    assert brain.screen_nodes['["Entry"]']["fields"]["offscreen"] == hidden
    exceptions = [
        e.payload
        for e in brain.log.read_all()
        if e.payload.get("check") == "memory_retrieval_exception"
    ]
    assert len(model.prompts) == 1
    assert any(item["reason"] == "current_values_not_observed" for item in exceptions)
    assert set(brain.verified) == {"customer", "reference"}
    assert (
        brain.screen_nodes['["Entry"]']["fields"]["reference"]["last_observation"]["value"]
        == "ORDER-B"
    )
    # Synthetic model-approved memory survives the corrupted reread through real graph nodes.
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert len(relay.actions) == 3 and relay.actions[-1].root.type == "click"
    assert brain._verify_node() == "review"
    assert brain.visual_proposal.operation == "complete"
    assert brain.visual_proposal.mapping.fields[first.targets[0].name].value == "New customer B"
    assert len(model.prompts) == 2
    final_plan = next(
        e
        for e in reversed(brain.log.read_all())
        if e.payload.get("check") == "screen_plan_proposed"
    )
    assert final_plan.payload["mapping"]["screen_path"] == ["Entry"]
    assert final_plan.payload["mapping"]["completion_evidence"][0]["condition_index"] == 0
    assert brain._select_node() == "review"
    assert brain._review_node() == "finish"
    assert brain.result.status == "success" and len(model.prompts) == 2


def test_synthetic_reentered_node_does_not_reobserve_old_memo_or_skip_current_input(tmp_path):
    # Synthetic 138 reentry: old model observation is newer than the old input delivery.
    brain, model, relay = setup(tmp_path)
    runtime = brain.memory_retrieval
    method = runtime.library.methods()[0]
    group = method.action_groups[0]
    group = group.model_copy(update={"screen_path": ["Entry"]})
    runtime.library.upsert(method.model_copy(update={"action_groups": [group]}))
    brain.screen_nodes['["Entry"]'] = {
        "path": ["Entry"],
        "fields": {
            "customer": {
                "last_observation": {
                    "value": brain.values["customer"],
                    "scope": "entry",
                    "observation_number": 9,
                    "observation_id": "previous-visit",
                },
                "label": "Customer",
                "role": "edit",
                "delivery": {"observation_number": 8, "scope": "entry", "value": "Old input"},
            },
        },
        "controls": {},
    }
    _, _, current, _ = catalog(shift=20)
    current["ocr"] = []
    response = runtime.match_pattern(current)
    assert response is not None and response.mapping is not None
    assert {f.source for f in response.mapping.fields.values()} == {"customer", "reference"}
    assert all("value" not in f.model_fields_set for f in response.mapping.fields.values())
    assert all("value" not in f for f in current["memory_fields"])
    assert response.evidence == [] and response.observed_step_results == []
    assert model.prompts == [] and relay.actions == []
    previous = deepcopy(brain.screen_nodes['["Entry"]']["fields"]["customer"]["last_observation"])
    # Even matching OCR cannot fill in the missing model reading during geometry binding.
    blocks = brain.screen_scanner.ocr.read(None)
    blocks.append(
        OcrBlock(id=180, text=brain.values["customer"], bbox=(105, 45, 130, 15), confidence=1)
    )
    brain._observe()
    # Exercise the scanner's current OCR binding too: a text id is not a model value.
    scanner = brain.screen_scanner
    reading = next(b.id for b in scanner._catalog.blocks if b.text == brain.values["customer"])
    mapping = brain.visual_proposal.mapping
    meanings = brain.visual_proposal.interpretation()
    meanings = meanings.model_copy(
        update={
            "controls": [
                control.model_copy(
                    update={
                        "text_ids": [reading]
                        if mapping.fields[control.visual_description].source == "customer"
                        else []
                    }
                )
                for control in meanings.controls
            ]
        }
    )
    brain.observation = scanner.bind_mapping(brain.observation.wire, meanings, mapping.fields)
    assert all("value" not in field for field in scanner._catalog.selected_fields.values())
    assert brain.screen_nodes['["Entry"]']["fields"]["customer"]["last_observation"] == previous
    assert not brain.verified
    assert all(
        element.value is None for element in brain.observation.elements if element.role == "edit"
    )
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert relay.entered == ["New customer B", "ORDER-B"]
    assert model.prompts == []


@pytest.mark.parametrize("fail_second", [False, True])
def test_model_adaptation_is_retained_only_after_whole_batch_delivery(tmp_path, fail_second):
    brain, model, relay = setup(tmp_path, changed_label=True, fail_second=fail_second)
    brain._observe()
    assert len(model.prompts) == 1
    assert len(brain.memory_retrieval.library.methods()[0].action_groups) == 1
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    outcome = brain._act_node()
    groups = brain.memory_retrieval.library.methods()[0].action_groups
    if fail_second:
        assert outcome in {"observation", "finish"}
        assert brain.pending is None
        assert len(groups) == 1
        assert brain.memory_retrieval.state.history[-1]["status"] == "interrupted"
        assert relay.entered == ["New customer B"]
    else:
        assert outcome == "observation"
        assert brain.pending is not None
        # A delivered local variant remains quarantined until the whole
        # business task succeeds.
        assert len(groups) == 1
        assert brain.memory_retrieval.state.history[-1]["origin"] == "adaptation"
        assert brain.memory_retrieval.finish(success=True) == 1
        groups = brain.memory_retrieval.library.methods()[0].action_groups
        assert len(groups) == 1
        candidate = MethodDocument.model_validate_json(
            (brain.case_dir / "method-candidate.json").read_bytes()
        )
        assert candidate.methods[0].action_groups == groups
        current_screen = brain.screen_scanner.planning_context()[0].screen
        bound = brain.memory_retrieval.library.bind_with_context(
            "business-entry", {}, current_screen, brain.values
        )
        assert bound is not None
        assert bound.group_id == groups[-1].id


@pytest.mark.parametrize("business_success", [False, True])
def test_final_model_proof_is_promoted_only_after_business_success(tmp_path, business_success):
    brain, _, _ = setup(tmp_path)
    final_screen = completion_scene()
    brain.memory_retrieval.remember(final_screen, ScreenMatch.model_validate(completion_response()))
    assert brain.memory_retrieval.library.methods()[0].completion is None

    promoted = brain.memory_retrieval.finish(success=business_success)
    assert promoted == int(business_success)
    method = brain.memory_retrieval.library.methods()[0]
    if not business_success:
        assert method.completion is None
        return

    assert method.completion is not None
    brain.memory_retrieval.state.used.update(
        (method.id, group.id) for group in method.action_groups
    )
    response = brain.memory_retrieval.match_pattern(
        completion_scene(customer="New customer B", reference="ORDER-B", ids=100)
    )
    assert response is not None
    assert response.operation == "complete"
    assert response.steps == []
    assert response.evidence == []  # A completion candidate is not current-frame proof.
    assert brain.memory_retrieval.state.history == []
    events = brain.log.read_all()
    assert any(
        event.kind == "cache_hit" and event.payload.get("check") == "method_completion_bound"
        for event in events
    )


@pytest.mark.parametrize("business_success", [False, True])
def test_successful_candidate_installs_versioned_live_library(tmp_path, business_success):
    from brain.graph.memory_retrieval import open_memory_library

    live = tmp_path / "live" / "methods.json"
    brain, _, _ = setup(
        tmp_path,
        changed_label=True,
        promotion_path=live,
    )
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert brain.pending is not None
    assert brain.memory_retrieval.finish(success=business_success) == int(business_success)
    if not business_success:
        assert not live.exists()
        return

    installed = MethodDocument.model_validate_json(live.read_bytes()).methods[0]
    assert installed.revision == 2
    assert len(installed.action_groups) == 1
    assert MethodDocument.model_validate_json(
        (brain.case_dir / "method-candidate.json").read_bytes()
    ).methods == [installed]
    learning_result = json.loads(
        (brain.case_dir / "method-learning-result.json").read_text(encoding="utf-8")
    )
    assert learning_result["task_contract_sha256"] == brain.task_instruction_sha256
    assert learning_result["methods"] == [
        {
            "id": installed.id,
            "task_contract_sha256": installed.task_contract_sha256,
            "artifact_kind": installed.artifact_kind,
            "revision": installed.revision,
            "source_transitions": installed.source_transitions,
        }
    ]

    # The next task prefers the successful live revision over the immutable seed.
    library = open_memory_library(
        brain.settings.methods.path,
        tmp_path / "next-case",
        enabled=True,
        promotion_path=live,
    )
    assert library.methods() == [installed]


def test_login_structure_replays_credentials_by_source_without_storing_values(tmp_path):
    brain, model, _ = setup(tmp_path)
    business_group = brain.memory_retrieval.library.methods()[0].action_groups[0]
    brain.credentials = {
        "credential.username": "private-user-47",
        "credential.password": "private-password-83",
    }
    blocks = [
        OcrBlock(id=10, text="Account", bbox=(10, 25, 70, 15), confidence=1),
        OcrBlock(id=11, text="Password", bbox=(10, 75, 70, 15), confidence=1),
        OcrBlock(id=12, text="Sign in", bbox=(120, 127, 60, 15), confidence=1),
    ]
    boxes = [
        Marker(id=0, bbox=(100, 20, 150, 30), source="omniparser", confidence=1),
        Marker(id=1, bbox=(100, 70, 150, 30), source="omniparser", confidence=1),
        Marker(id=2, bbox=(100, 120, 100, 30), source="omniparser", confidence=1),
    ]
    markers = build_markers(blocks, boxes, 400, 200)
    controls_by_box = {tuple(box.bbox): box for box in markers if box.source == "omniparser"}
    username = controls_by_box[(100, 20, 150, 30)]
    password = controls_by_box[(100, 70, 150, 30)]
    submit = controls_by_box[(100, 120, 100, 30)]
    screen = {
        "image_size": [400, 200],
        "screen_phash": synthetic_screen_phash(),
        "ocr": [block.model_dump(mode="json") for block in blocks],
        "markers": [marker.model_dump(mode="json") for marker in markers],
    }
    learned = ScreenMatch(
        screen_kind="login",
        procedure_step="task-action",
        taught_step_id="login",
        purpose="Authenticate private-user-47",
        expected_result="private-password-83 is accepted and the business menu is visible.",
        controls=[
            ControlMeaning(id=username.id, role="edit", label_ids=[10], confidence=1),
            ControlMeaning(id=password.id, role="edit", label_ids=[11], confidence=1),
            ControlMeaning(id=submit.id, role="button", text_ids=[12], confidence=1),
        ],
        steps=[
            {
                "target": username.id,
                "action": "fill",
                "source": "credential.username",
                "intent": "credential",
            },
            {
                "target": password.id,
                "action": "fill",
                "source": "credential.password",
                "intent": "credential",
            },
            {
                "target": submit.id,
                "action": "click",
                "intent": "login",
                "point": [150, 135],
            },
        ],
    )
    brain.observation = wire()
    brain.task_instruction = TaskInstruction.model_validate(
        {
            **brain.task_instruction.model_dump(),
            "procedure": [
                {
                    "id": "login",
                    "instruction": "Authenticate to the business application.",
                    "completion": "The business menu is visible.",
                }
            ],
        }
    )
    brain.memory_retrieval.remember(screen, learned)
    assert brain.memory_retrieval.state.pending is not None
    brain.memory_retrieval.delivered(complete=True)
    assert brain.memory_retrieval.finish(success=True) == 1

    candidate_text = (brain.case_dir / "method-candidate.json").read_text(encoding="utf-8")
    assert "private-user-47" not in candidate_text
    assert "private-password-83" not in candidate_text
    assert "credential.username" in candidate_text
    assert "credential.password" in candidate_text

    # The compiler supplies login before business entry; execution retains that route.
    installed_methods = brain.memory_retrieval.library.methods()
    installed = next(
        method
        for method in installed_methods
        if any(
            action.slot == "credential.username"
            for group in method.action_groups
            for action in group.actions
        )
    )
    login_group = next(
        group
        for group in installed.action_groups
        if any(action.slot == "credential.username" for action in group.actions)
    ).model_copy(update={"taught_step_id": "login", "trace_index": 0, "predecessor_group_ids": []})
    business_group = business_group.model_copy(
        update={"trace_index": 1, "predecessor_group_ids": [login_group.id]}
    )
    atomic_write_json(
        brain.memory_retrieval.library.path,
        MethodDocument(
            methods=[installed.model_copy(update={"action_groups": [login_group, business_group]})]
        ),
    )
    brain.memory_retrieval.state.used.clear()
    replay = brain.memory_retrieval.match_pattern(screen)
    assert replay is not None
    assert brain.memory_retrieval.state.pending["group_id"] == login_group.id
    assert [step.source for step in replay.steps] == [
        "credential.username",
        "credential.password",
        None,
    ]
    assert model.prompts == []


# Older compiled artifacts without trace indexes retain their declared route order.
def test_compiled_method_without_trace_indexes_does_not_skip_to_a_matching_later_group(tmp_path):
    brain, model, _ = setup(tmp_path)
    method = brain.memory_retrieval.library.methods()[0]
    first = method.action_groups[0].model_copy(
        update={"id": "customer-screen", "taught_step_id": "customer"}
    )
    _, _, account_screen, account_response = catalog("Account", shift=20)
    account_screen["screen_phash"] ^= (1 << 64) - 1
    account_method = method_from_match(
        account_screen,
        account_response.model_dump(mode="json"),
        method_id=method.id,
        purpose=method.purpose,
        task_objective=method.task_objective,
        task_contract_sha256=method.task_contract_sha256,
    )
    assert account_method is not None
    second = account_method.action_groups[0].model_copy(
        update={"id": "account-screen", "taught_step_id": "account"}
    )
    atomic_write_json(
        brain.memory_retrieval.library.path,
        MethodDocument(methods=[method.model_copy(update={"action_groups": [first, second]})]),
    )
    brain.task_instruction = TaskInstruction.model_validate(
        {
            **brain.task_instruction.model_dump(),
            "procedure": [
                {"id": "customer", "instruction": "Use customer form", "completion": "Done"},
                {"id": "account", "instruction": "Use account form", "completion": "Done"},
            ],
        }
    )
    brain.memory_retrieval.state.pending = None
    brain.memory_retrieval.state.used.clear()

    assert brain.memory_retrieval.match_pattern(deepcopy(account_screen)) is None
    assert not brain.memory_retrieval.state.used
    replay = brain.memory_retrieval.match_pattern(catalog(shift=20)[2])
    assert replay is not None and brain.memory_retrieval.state.pending["group_id"] == first.id
    brain.memory_retrieval.delivered(complete=True)
    brain.memory_retrieval.verify_delivery(success=True)
    replay = brain.memory_retrieval.match_pattern(account_screen)
    assert replay is not None
    assert brain.memory_retrieval.state.pending["group_id"] == second.id
    brain.memory_retrieval.delivered(complete=True)
    brain.memory_retrieval.verify_delivery(success=True)
    assert (method.id, first.id) in brain.memory_retrieval.state.used
    assert (method.id, second.id) in brain.memory_retrieval.state.used
    assert model.prompts == []


def test_unproductive_method_edge_is_removed_before_success_promotion(tmp_path):
    brain, _, _ = setup(tmp_path, changed_label=True)
    method = brain.memory_retrieval.library.methods()[0]
    _, _, screen, response = catalog("Account", shift=20)
    control = response.controls[0].model_copy(update={"role": "button"})
    response = response.model_copy(
        update={
            "method_id": method.id,
            "controls": [control],
            "steps": [
                response.steps[0].model_copy(
                    update={
                        "target": control.id,
                        "action": "click",
                        "source": None,
                        "intent": "navigate",
                        "point": [175, 55],
                    }
                )
            ],
        }
    )

    brain.memory_retrieval.remember(screen, response)
    assert brain.memory_retrieval.state.pending is not None
    group_id = brain.memory_retrieval.state.pending["group_id"]
    brain.memory_retrieval.delivered(complete=True)
    assert method.id in brain.memory_retrieval.state.staged
    assert (method.id, group_id) in brain.memory_retrieval.state.used

    brain.memory_retrieval.verify_delivery(success=False)

    assert method.id not in brain.memory_retrieval.state.staged
    assert (method.id, group_id) not in brain.memory_retrieval.state.used
    assert brain.memory_retrieval.state.history[-1]["status"] == "unproductive"
    assert brain.memory_retrieval.finish(success=True) == 0


def test_delivered_fill_with_incomplete_reread_is_promoted_only_on_task_success(tmp_path):
    brain, _, _ = setup(tmp_path, changed_label=True)
    method = brain.memory_retrieval.library.methods()[0]
    _, _, screen, response = catalog("Account", shift=20)
    response = response.model_copy(update={"method_id": method.id})

    brain.memory_retrieval.remember(screen, response)
    group_id = brain.memory_retrieval.state.pending["group_id"]
    brain.memory_retrieval.delivered(complete=True)
    brain.memory_retrieval.verify_delivery(success=False)

    assert brain.memory_retrieval.state.history[-1]["status"] == "provisional"
    assert (method.id, group_id) in brain.memory_retrieval.state.used
    assert brain.memory_retrieval.finish(success=True) == 1
    promoted = brain.memory_retrieval.library.methods()[0]
    assert [group.id for group in promoted.action_groups] == [group_id]


def test_unproductive_reused_edge_is_consumed_for_the_current_run(tmp_path):
    brain, _, _ = setup(tmp_path)
    method = brain.memory_retrieval.library.methods()[0]
    group = method.action_groups[0]
    action = group.actions[0].model_copy(
        update={
            "action": "click",
            "intent": "navigate",
            "slot": None,
        }
    )
    group = group.model_copy(update={"actions": [action]})
    method = method.model_copy(update={"action_groups": [group]})
    brain.memory_retrieval.state.pending = {
        "method": method,
        "group_id": group.id,
        "origin": "reuse",
        "pruned_group_ids": [],
    }

    brain.memory_retrieval.delivered(complete=True)
    brain.memory_retrieval.verify_delivery(success=False)

    assert (method.id, group.id) in brain.memory_retrieval.state.used
    assert brain.memory_retrieval.state.history[-1]["status"] == "unproductive"


def test_predispatch_rejection_blocks_that_method_without_blame_on_previous_delivery(tmp_path):
    brain, _, _ = setup(tmp_path)
    method = brain.memory_retrieval.library.methods()[0]
    group = method.action_groups[0]
    previous = {
        "method": method,
        "group_id": "previous-group",
        "origin": "reuse",
        "pruned_group_ids": [],
        "history_index": 0,
    }
    brain.memory_retrieval.state.unverified = previous
    brain.memory_retrieval.state.pending = {
        "method": method,
        "group_id": group.id,
        "origin": "reuse",
        "pruned_group_ids": [],
    }

    rejected = brain.memory_retrieval.reject_pending(reason="input_label_not_near_control")

    assert rejected is not None
    assert rejected["group_id"] == group.id
    assert rejected["status"] == "rejected_before_delivery"
    assert brain.memory_retrieval.state.unverified is previous
    assert method.id in brain.memory_retrieval.state.blocked_method_ids
    assert brain.memory_retrieval.match_pattern(catalog()[2]) is None


def test_successful_fallback_keeps_untried_followup_in_the_same_taught_step(tmp_path):
    brain, _, _ = setup(tmp_path, changed_label=True)
    installed = brain.memory_retrieval.library.methods()[0]
    failed_action = (
        installed.action_groups[0]
        .actions[0]
        .model_copy(
            update={
                "action": "click",
                "intent": "navigate",
                "slot": None,
            }
        )
    )
    old_group = installed.action_groups[0].model_copy(
        update={
            "id": "failed-branch",
            "taught_step_id": "details",
            "actions": [failed_action],
        }
    )
    followup_group = installed.action_groups[0].model_copy(
        update={
            "id": "followup-action",
            "taught_step_id": "details",
            "predecessor_group_ids": [old_group.id],
        }
    )
    atomic_write_json(
        brain.memory_retrieval.library.path,
        MethodDocument(
            methods=[installed.model_copy(update={"action_groups": [old_group, followup_group]})]
        ),
    )
    brain.memory_retrieval.state.pending = {
        "method": installed.model_copy(update={"action_groups": [old_group, followup_group]}),
        "group_id": old_group.id,
        "origin": "reuse",
        "pruned_group_ids": [],
    }
    brain.memory_retrieval.delivered(complete=True)
    brain.memory_retrieval.verify_delivery(success=False)

    _, _, screen, response = catalog("Account", shift=20)
    response = response.model_copy(update={"method_id": installed.id, "taught_step_id": "details"})

    brain.memory_retrieval.remember(screen, response)
    assert brain.memory_retrieval.state.pending is not None
    new_group_id = brain.memory_retrieval.state.pending["group_id"]
    brain.memory_retrieval.delivered(complete=True)
    brain.memory_retrieval.verify_delivery(success=True)

    assert (installed.id, old_group.id) in brain.memory_retrieval.state.used
    assert (installed.id, new_group_id) in brain.memory_retrieval.state.used
    assert (installed.id, followup_group.id) not in brain.memory_retrieval.state.used


def test_method_history_does_not_own_education_completion(tmp_path):
    brain, _, _ = setup(tmp_path)
    method = brain.memory_retrieval.library.methods()[0]
    first = method.action_groups[0].model_copy(
        update={"id": "group-first", "taught_step_id": "step-1"}
    )
    second = method.action_groups[0].model_copy(
        update={"id": "group-second", "taught_step_id": "step-2"}
    )
    third = method.action_groups[0].model_copy(
        update={"id": "group-third", "taught_step_id": "step-3"}
    )
    atomic_write_json(
        brain.memory_retrieval.library.path,
        MethodDocument(
            methods=[method.model_copy(update={"action_groups": [first, second, third]})]
        ),
    )
    brain.task_instruction = TaskInstruction.model_validate(
        {
            "name": "Three steps",
            "objective": brain.task_instruction.objective,
            "completion_conditions": ["Done"],
            "procedure": [
                {"id": f"step-{index}", "instruction": f"Do {index}", "completion": "Done"}
                for index in range(1, 5)
            ],
        }
    )
    brain.memory_retrieval.state.used.add((method.id, first.id))
    brain.memory_retrieval.state.history.extend(
        [
            {
                "taught_step_id": "step-1",
                "purpose": "first",
                "status": "delivered",
            },
            {
                "taught_step_id": "step-2",
                "purpose": "retry",
                "status": "interrupted",
            },
            {
                "taught_step_id": "step-2",
                "purpose": "second",
                "status": "delivered",
            },
        ]
    )

    procedure = brain._taught_procedure_progress()
    memory = brain.memory_retrieval.prompt_context()

    assert [step["id"] for step in procedure["steps"]] == ["step-1", "step-2"]
    assert procedure["completed_step_ids"] == []
    assert [group["id"] for group in memory["methods"][0]["groups"]] == [
        "group-second",
        "group-third",
    ]
    assert len(memory["recent_executions"]) == 2


# Cleanup audit C7: retain predecessor order and exclusion of consumed groups.
def test_generic_success_trace_becomes_model_free_execution_graph(tmp_path):
    brain, model, _ = setup(tmp_path)
    atomic_write_json(brain.memory_retrieval.library.path, MethodDocument())
    _, _, screen, response = catalog(shift=20)
    first = response.model_copy(
        update={
            "purpose": "Enter the visible customer",
            "controls": [response.controls[0]],
            "steps": [response.steps[0]],
        }
    )
    second = response.model_copy(
        update={
            "purpose": "Enter the visible reference",
            "controls": [response.controls[1]],
            "steps": [response.steps[1]],
        }
    )
    brain.observation = wire()
    for item in (first, second):
        brain.memory_retrieval.remember(screen, item)
        assert brain.memory_retrieval.state.pending is not None
        brain.memory_retrieval.delivered(complete=True)

    assert len(brain.memory_retrieval.state.staged) == 1
    assert brain.memory_retrieval.finish(success=True) == 1
    method = brain.memory_retrieval.library.methods()[0]
    assert [group.trace_index for group in method.action_groups] == [0, 1]
    assert method.action_groups[0].predecessor_group_ids == []
    assert method.action_groups[1].predecessor_group_ids == [method.action_groups[0].id]

    brain.memory_retrieval.state.used.clear()
    first_replay = brain.memory_retrieval.match_pattern(screen)
    assert first_replay is not None
    assert first_replay.purpose == "Enter the visible customer"
    brain.memory_retrieval.delivered(complete=True)
    second_replay = brain.memory_retrieval.match_pattern(screen)
    assert second_replay is not None
    assert second_replay.purpose == "Enter the visible reference"
    assert model.prompts == []


def test_next_observation_error_does_not_discard_delivered_method(tmp_path):
    brain, _, _ = setup(tmp_path)
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    brain._act_node()
    brain.memory_retrieval.verify_delivery(success=None)
    assert brain.memory_retrieval.state.history[-1]["status"] == "provisional"
    event = next(
        e for e in reversed(brain.log.read_all()) if e.payload.get("check") == "method_effect"
    )
    assert event.payload["success"] is None
    assert event.payload["reason"] == "observation_unavailable"


@pytest.mark.parametrize("verified", [False, True], ids=["no-effect", "observed-effect"])
# Cleanup audit C7: retain conditional predecessor handling before effect commit.
def test_bound_edge_replaced_without_dispatch_unblocks_only_after_effect(tmp_path, verified):
    brain, _, _ = setup(tmp_path)
    runtime = brain.memory_retrieval
    method = runtime.library.methods()[0]
    first = method.action_groups[0].model_copy(update={"trace_index": 0})
    next_hash = first.screen_phash ^ 1023
    followup = first.model_copy(
        update={
            "id": "next-edge",
            "screen_phash": next_hash,
            "predecessor_group_ids": [first.id],
            "trace_index": 1,
        }
    )
    runtime.library.upsert(method.model_copy(update={"action_groups": [first, followup]}))
    _, _, screen, _ = catalog(shift=20)
    bound = runtime.match_pattern(screen)
    assert bound is not None
    original = runtime.state.pending
    old_edge = (original["method"].id, original["group_id"])
    assert old_edge not in runtime.state.used
    fallback = bound.model_copy(
        update={
            "method_id": None,
            "purpose": "Model reviewed current values",
            "steps": [
                s.model_copy(update={"point": [s.point[0] + 4, s.point[1]]}) for s in bound.steps
            ],
        }
    )
    # Synthetic model projection reallocates every transient target, as a real fallback does.
    replacements = {old: old + 100 for old in [step.target for step in fallback.steps]}
    screen["selected_fields"] = {
        str(replacements[int(ref)]): value
        for ref, value in screen.get("selected_fields", {}).items()
        if int(ref) in replacements
    }
    fallback = fallback.model_copy(
        update={
            "steps": [
                step.model_copy(update={"target": replacements[step.target]})
                for step in fallback.steps
            ]
        }
    )
    # Synthetic 140-shaped recovery: scrolling changes the full image, but the
    # model chooses the same controls. Attribution must not require image similarity.
    screen["screen_phash"] = first.screen_phash ^ ((1 << 14) - 1)
    runtime.remember(screen, fallback)
    assert runtime.state.pending["replaced_edge"] == old_edge
    runtime.delivered(complete=True)
    _, _, next_screen, _ = catalog(shift=20)
    next_screen["screen_phash"] = next_hash
    assert runtime.match_pattern(next_screen) is not None  # Conditional plan before effect commit.
    assert runtime.state.pending["group_id"] == "next-edge"
    assert old_edge not in runtime.state.used
    runtime.verify_delivery(success=verified)
    assert old_edge not in runtime.state.used
    assert (runtime.state.history[-1].get("replaced_edge") == old_edge) is verified
    _, _, next_screen, _ = catalog(shift=20)
    next_screen["screen_phash"] = next_hash
    assert (runtime.match_pattern(next_screen) is not None) is verified


@pytest.mark.parametrize("mismatch", ["control", "viewport"])
def test_same_actions_do_not_replace_another_control_or_coordinate_space(tmp_path, mismatch):
    brain, _, _ = setup(tmp_path)
    runtime = brain.memory_retrieval
    _, _, screen, _ = catalog(shift=20)
    bound = runtime.match_pattern(screen)
    # Same inputs cannot justify either another control or a different coordinate space.
    fallback = bound.model_copy(
        update={
            "method_id": None,
            "steps": [
                step.model_copy(
                    update={
                        "point": [
                            step.point[0],
                            step.point[1] + (70 if mismatch == "control" else 0),
                        ]
                    }
                )
                for step in bound.steps
            ],
        }
    )
    if mismatch == "viewport":
        screen["image_size"] = [800, 400]
    runtime.remember(screen, fallback)
    assert runtime.state.pending.get("replaced_edge") is None
    assert not runtime.state.used


def synthetic_ordered_route(tmp_path):
    # Synthetic 136-shaped input/save images; fake capture/model, real memory binding.
    brain, model, _ = setup(tmp_path)
    brain.task_instruction = brain.task_instruction.model_copy(
        update={
            "procedure": [
                TaughtProcedureStep(id="save-record", instruction="Save", completion="Saved"),
                TaughtProcedureStep(
                    id="enter-record-fields", instruction="Fill", completion="All fields entered"
                ),
            ]
        }
    )
    runtime = brain.memory_retrieval
    method = runtime.library.methods()[0]
    first = method.action_groups[0].model_copy(
        update={"id": "input", "trace_index": 0, "taught_step_id": "enter-record-fields"}
    )
    button = MethodTarget(name="Save", role="button", bbox=(300, 140, 80, 30), point=(340, 155))
    second = first.model_copy(
        update={
            "id": "save",
            "trace_index": 1,
            "taught_step_id": "save-record",
            "predecessor_group_ids": [first.id],
            "screen_phash": first.screen_phash ^ 63,
            "targets": [button],
            "actions": [
                MethodAction(target="Save", action="click", intent="save", point=button.point)
            ],
            "observed_step_results": [
                ObservedStepResult(
                    step_id="enter-record-fields", completed=True, observed_result="Fields filled"
                )
            ],
        }
    )
    # Both file order and education order differ from the model's compiled trace order.
    method = method.model_copy(update={"action_groups": [second, first]})
    final_screen = {
        "image_size": [400, 200],
        "screen_phash": first.screen_phash ^ (1023 << 10),
        "ocr": [],
    }
    final = ScreenMapping(
        screen_kind="editor",
        screen_path=["Saved detail"],
        operation="complete",
        fields={
            target.name: dict(
                role="text",
                bbox=list(target.bbox),
                source=action.slot,
                value=brain.values[action.slot],
            )
            for target, action in zip(first.targets, first.actions, strict=True)
        },
        completion_evidence=[dict(condition_index=0, target=first.targets[0].name)],
    )
    method = method_completion_from_match(
        final_screen, final.local_payload(final_screen), method, brain.values
    )
    assert method is not None and method.completion is not None
    atomic_write_json(runtime.library.path, MethodDocument(methods=[method]))
    _, _, current, _ = catalog(shift=20)
    return brain, model, runtime, method, first, second, current, final_screen


def test_synthetic_compiled_route_owns_input_save_and_completion_without_candidate_competition(
    tmp_path,
):
    brain, model, runtime, method, first, second, current, final_screen = synthetic_ordered_route(
        tmp_path
    )
    assert not brain.completed_taught_steps
    response = runtime.match_pattern(deepcopy(current))
    assert response is not None, "historical saved-state claims must not compete with next fill"
    assert runtime.state.pending["group_id"] == first.id
    assert [(step.action, step.source) for step in response.steps] == [
        ("fill", "customer"),
        ("fill", "reference"),
    ]
    runtime.delivered(complete=True)
    runtime.verify_delivery(success=True)

    # A closer entry frame from another route cannot replace the already selected route.
    runtime.library.upsert(
        method.model_copy(
            update={
                "id": "another-business-route",
                "action_groups": [first.model_copy(update={"id": "other-input"})],
                "completion": None,
            }
        )
    )
    # A broad education outcome is not permission to skip the compiled save action.
    brain.completed_taught_steps.update({"enter-record-fields", "save-record"})
    runtime = brain.memory_retrieval
    memory = runtime.retrieve_screen_memory(deepcopy(current))
    assert memory is not None
    assert memory["groups"] == [{"method_id": method.id, "group_id": second.id}]
    assert memory["procedure"][0]["steps"][0]["action"] == "click"
    assert memory["distance"] == 6
    response = runtime.match_pattern(deepcopy(current))
    assert response is not None and runtime.state.pending["group_id"] == second.id
    assert [(step.action, step.point) for step in response.steps] == [("click", [340, 155])]
    runtime.delivered(complete=True)
    runtime.verify_delivery(success=True)

    response = runtime.match_pattern(final_screen)
    assert response is not None and response.operation == "complete"
    assert runtime.state.used == {(method.id, first.id), (method.id, second.id)}
    assert model.prompts == []


def test_synthetic_next_route_mismatch_does_not_jump_to_a_later_similar_save(tmp_path):
    _, _, runtime, _, _, second, current, _ = synthetic_ordered_route(tmp_path)
    current["screen_phash"] = second.screen_phash ^ (63 << 6)
    assert runtime.retrieve_screen_memory(deepcopy(current)) is None
    assert runtime.match_pattern(current) is None
    assert not runtime.state.used and runtime.state.pending is None


def test_synthetic_rejected_plan_reconnects_after_model_delivers_the_same_next_actions(tmp_path):
    _, _, runtime, method, first, second, current, _ = synthetic_ordered_route(tmp_path)
    bound = runtime.match_pattern(deepcopy(current))
    assert bound is not None
    runtime.reject_pending(reason="synthetic_preparation_rejection")
    assert runtime.state.pending is None and method.id in runtime.state.blocked_method_ids
    # Synthetic fresh model response: same concrete actions, no claimed educational completion.
    recovery = bound.model_copy(update={"method_id": None, "observed_step_results": []})
    runtime.remember(current, recovery)
    assert runtime.state.pending["replaced_edge"] == (method.id, first.id)
    replacement = runtime.state.pending["group_id"]
    runtime.delivered(complete=True)
    runtime.verify_delivery(success=True)

    assert runtime.state.used == {(method.id, replacement)}
    assert (method.id, first.id) not in runtime.state.used
    assert runtime.state.history[-1]["replaced_edge"] == (method.id, first.id)
    response = runtime.match_pattern(deepcopy(current))
    assert response is not None and runtime.state.pending["group_id"] == second.id


def test_synthetic_cached_outcomes_cannot_resume_a_route_or_clear_its_rejection(tmp_path):
    _, _, runtime, method, first, _, current, _ = synthetic_ordered_route(tmp_path)
    cached = ScreenMatch(
        screen_kind="editor",
        procedure_step="task-action",
        method_id=method.id,
        controls=[],
        observed_step_results=[
            ObservedStepResult(
                step_id="enter-record-fields", completed=True, observed_result="Past input complete"
            )
        ],
        steps=[],
    )
    runtime.remember(current, cached, allow_recovery=False)
    assert runtime.match_pattern(deepcopy(current)) is not None
    assert runtime.state.pending["group_id"] == first.id
    assert not runtime.state.used
    runtime.reject_pending(reason="synthetic_preparation_rejection")
    runtime.remember(current, cached, allow_recovery=False)
    assert method.id in runtime.state.blocked_method_ids
    assert runtime.match_pattern(deepcopy(current)) is None
    assert not any(item.get("resumed_group_ids") for item in runtime.state.history)


def synthetic_recovery_graph(tmp_path, *, outcome="open-detail", ambiguous=False):
    # Synthetic 135-shaped trace: one original click, a multi-action recovery, then detail.
    brain, model, _ = setup(tmp_path)
    brain.task_instruction = brain.task_instruction.model_copy(
        update={
            "procedure": [
                TaughtProcedureStep(id="open-detail", instruction="Open detail", completion="Open"),
                TaughtProcedureStep(
                    id="review-detail", instruction="Review detail", completion="Done"
                ),
            ]
        }
    )
    runtime = brain.memory_retrieval
    method = runtime.library.methods()[0]
    initial = method.action_groups[0]
    query = initial.model_copy(
        update={
            "id": "original-query",
            "taught_step_id": "open-detail",
            "trace_index": 0,
            "actions": [
                initial.actions[0].model_copy(
                    update={"action": "double_click", "intent": "navigate", "slot": None}
                )
            ],
        }
    )
    detail = initial.model_copy(
        update={
            "id": "detail-followup",
            "taught_step_id": "review-detail",
            "trace_index": 1,
            "predecessor_group_ids": [query.id],
            "screen_phash": initial.screen_phash ^ 1023,
            "observed_step_results": (
                [ObservedStepResult(step_id=outcome, completed=True, observed_result="Detail open")]
                if outcome
                else []
            ),
        }
    )
    groups = [query, detail]
    if ambiguous:
        groups.append(detail.model_copy(update={"id": "another-detail-action", "trace_index": 2}))
    atomic_write_json(
        runtime.library.path,
        MethodDocument(methods=[method.model_copy(update={"action_groups": groups})]),
    )
    _, _, current, recovery = catalog(shift=20)
    current["screen_phash"] = detail.screen_phash
    return brain, model, runtime, method, query, detail, current, recovery


def test_synthetic_current_model_observation_resumes_recovery_without_fake_delivery(tmp_path):
    brain, model, runtime, method, query, detail, current, recovery = synthetic_recovery_graph(
        tmp_path, ambiguous=True
    )
    _, _, recovery_screen, _ = catalog(shift=20)
    # No original pending plan: the query frame failed similarity in the real 135 path.
    assert runtime.state.pending is None
    assert len(recovery.steps) != len(query.actions)
    runtime.remember(recovery_screen, recovery.model_copy(update={"method_id": method.id}))
    recovery_id = runtime.state.pending["group_id"]
    runtime.delivered(complete=True)
    runtime.verify_delivery(success=True)
    assert brain.completed_taught_steps == set()

    assert runtime.match_pattern(deepcopy(current)) is None, "stored outcomes are not current proof"
    runtime.remember(
        current,
        ScreenMatch(
            screen_kind="editor",
            procedure_step="task-action",
            method_id=method.id,
            controls=[],
            observed_step_results=[
                ObservedStepResult(
                    step_id="open-detail", completed=True, observed_result="Current detail is open"
                )
            ],
            steps=[],
        ),
    )
    response = runtime.match_pattern(current)

    assert response is not None
    assert runtime.state.pending["group_id"] == detail.id
    assert response.observed_step_results == []
    assert runtime.state.used == {(method.id, recovery_id)}
    assert (method.id, query.id) not in runtime.state.used
    assert model.prompts == []


def test_synthetic_stored_and_global_outcomes_cannot_replace_a_current_recovery_response(tmp_path):
    brain, _, _, method, _, _, current, _ = synthetic_recovery_graph(tmp_path)
    brain.completed_taught_steps.add("open-detail")
    runtime = brain.memory_retrieval
    for label, observations in (
        ("no current model response", None),
        ("response without an outcome", []),
        (
            "response about an unknown education step",
            [ObservedStepResult(step_id="foreign-step", completed=True, observed_result="Done")],
        ),
    ):
        if observations is not None:
            runtime.remember(
                current,
                ScreenMatch(
                    screen_kind="editor",
                    procedure_step="task-action",
                    method_id=method.id,
                    controls=[],
                    observed_step_results=observations,
                    steps=[],
                ),
            )
        assert runtime.match_pattern(deepcopy(current)) is None, label
        assert runtime.state.used == set(), label


def test_synthetic_completion_uses_current_recovery_outcomes_without_inventing_group_deliveries(
    tmp_path,
):
    brain, _, runtime, _, query, detail, _, _ = synthetic_recovery_graph(tmp_path, outcome=None)
    method = runtime.library.methods()[0]
    final_screen = {
        "image_size": [400, 200],
        "screen_phash": detail.screen_phash ^ (1023 << 10),
        "ocr": [],
    }
    final = ScreenMapping(
        screen_kind="editor",
        screen_path=["Final detail"],
        operation="complete",
        observed_step_results=[
            ObservedStepResult(step_id="review-detail", completed=True, observed_result="All match")
        ],
        fields={
            target.name: dict(
                role="text",
                bbox=list(target.bbox),
                source=action.slot,
                value=brain.values[action.slot],
            )
            for target, action in zip(detail.targets, detail.actions, strict=True)
        },
        completion_evidence=[dict(condition_index=0, target=detail.targets[0].name)],
    )
    method = method_completion_from_match(
        final_screen, final.local_payload(final_screen), method, brain.values
    )
    assert method is not None and method.completion is not None
    atomic_write_json(runtime.library.path, MethodDocument(methods=[method]))
    # Matching the compiled final frame cannot itself complete unexecuted steps.
    assert runtime.match_pattern(dict(final_screen)) is None
    runtime.remember(
        final_screen,
        ScreenMatch(
            screen_kind="editor",
            procedure_step="task-action",
            method_id=method.id,
            controls=[],
            observed_step_results=[
                ObservedStepResult(step_id=step, completed=True, observed_result="Current proof")
                for step in ("open-detail", "review-detail")
            ],
            steps=[],
        ),
    )

    response = runtime.match_pattern(dict(final_screen))

    assert response is not None and response.operation == "complete"
    assert runtime.state.used.isdisjoint({(method.id, query.id), (method.id, detail.id)})
