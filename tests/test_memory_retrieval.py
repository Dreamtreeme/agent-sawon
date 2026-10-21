"""A learned method reaches the existing visual executor with new business inputs."""

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from PIL import Image
from sawon_interfaces import ActionResult
from test_brain import ScriptedModel
from test_methods import completion_response, completion_scene, learned
from test_visual_scene import wire

import brain.graph.screen_decision as screen_decision
from brain.cache.methods import MethodDocument, MethodLibrary, method_from_match
from brain.graph.engine import Brain
from brain.graph.memory_retrieval import open_memory_library
from brain.graph.screen_decision import MethodBinding, ScreenMatch
from brain.settings import BrainSettings
from brain.task_instruction import TaskInputRecord, TaskInstruction
from brain.visual.markers import Marker, build_markers, ground_meanings
from brain.visual.scene import ControlMeaning, bind_scene
from pipeline.schemas import OcrBlock
from sawon_runtime.storage import RunLog, atomic_write_json

TEST_SCREEN_PHASH = 10945075410289642384  # 400x200 white fixture capture.


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

    def context(request):
        order.append("context_recall")
        assert request.screen["observation_route"] == (
            "memory_retrieval" if similar else "scanning"
        )
        before = deepcopy(request)
        restored = original_context(request)
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
        "context_recall",
        "pattern_matching",
        "decision",
    ]
    assert ('"remembered_fields"' in model.prompts[0]) is similar
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
                "assignments": [],
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
    assert ('"remembered_fields"' in model.prompts[0]) is not identical
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
        "screen_phash": TEST_SCREEN_PHASH,
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
            name: {"role": "edit", "bbox": list(box.bbox), "value": ""}
            for name, box in zip(names, boxes, strict=True)
        },
        "assignments": dict(zip(names, ("customer", "reference"), strict=True)),
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
        def read(self, page, *, ocr_boxes=()):
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

    brain._observe()

    assert set(brain.verified) == {"customer", "reference"}
    inputs = [element for element in brain.observation.elements if element.role == "edit"]
    assert len(inputs) == 2
    assert all(element.enabled and element.value for element in inputs)
    assert model.prompts == [] and relay.actions == []
    recognition = next(
        e.payload for e in brain.log.read_all() if e.payload.get("check") == "screen_similarity"
    )
    assert recognition["matched"] and recognition["distance"] == 1


def test_synthetic_memory_evidence_survives_scene_grounding_and_updates_notebook(tmp_path):
    # Synthetic current OCR, using the real binder, scene projection and memo update.
    brain, model, relay = setup(tmp_path)
    original = brain.memory_retrieval.library.methods()[0]
    group = original.action_groups[0]
    library = MethodLibrary(tmp_path / "evidence-methods.json", enabled=True)
    library.upsert(
        original.model_copy(
            update={
                "action_groups": [
                    group.model_copy(
                        update={
                            "id": f"input-{index}",
                            "targets": [next(t for t in group.targets if t.name == action.target)],
                            "actions": [action],
                        }
                    )
                    for index, action in enumerate(group.actions)
                ]
            }
        )
    )
    blocks, _, screen, _ = catalog(shift=20)
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
    controls, evidence = library.bind_visible_value_evidence(original.id, {}, screen, brain.values)
    assert {item["field"] for item in evidence} == {"customer", "reference"}
    assert len({item["target"] for item in evidence}) == 2
    response = ScreenMatch(
        screen_kind="editor",
        procedure_step="task-action",
        controls=controls,
        evidence=evidence,
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

    assert set(brain.verified) == {"customer", "reference"}
    assert not brain.save_issued
    assert model.prompts == [] and relay.actions == []


@pytest.mark.parametrize("observe_again", [False, True])
def test_compiled_coordinates_reach_executor_without_model(tmp_path, observe_again):
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
        [175, 55],
        [175, 125],
    ]
    assert model.prompts == []
    assert not any(event.kind == "model_call" for event in brain.log.read_all())
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
    assert brain.memory_retrieval.state.history[-1]["origin"] == "reuse"
    assert brain.memory_retrieval.state.history[-1]["status"] == "delivered"
    assert brain.memory_retrieval.state.used
    # A rejected new source mapping must not reactivate the previous valid one.
    _, _, current, response = catalog(shift=20)
    brain.memory_retrieval.remember(
        current,
        response.model_copy(
            update={
                "method_id": "business-entry",
                "method_bindings": [MethodBinding(slot="customer", source="unknown_input")],
            }
        ),
    )
    assert brain.memory_retrieval.state.active_method_id is None
    assert brain.memory_retrieval.match_pattern(current) is None


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
    assert {item.field for item in response.evidence} == {"customer", "reference"}
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
        "screen_phash": TEST_SCREEN_PHASH,
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

    # Older live libraries appended newly learned login screens after the
    # business groups.  Runtime order follows the taught procedure, so those
    # libraries self-heal without another model call.
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
    ).model_copy(update={"taught_step_id": "login"})
    old_business_group = next(
        group
        for method in installed_methods
        if method.id != installed.id
        for group in method.action_groups
    )
    atomic_write_json(
        brain.memory_retrieval.library.path,
        MethodDocument(
            methods=[
                installed.model_copy(update={"action_groups": [old_business_group, login_group]})
            ]
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


def test_taught_method_selects_the_only_branch_that_binds_current_screen(tmp_path):
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

    replay = brain.memory_retrieval.match_pattern(account_screen)

    assert replay is not None
    assert brain.memory_retrieval.state.pending["group_id"] == second.id
    assert brain.memory_retrieval.state.pending["pruned_group_ids"] == [first.id]
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
    runtime.remember(screen, fallback)
    assert runtime.state.pending["replaced_edge"] == old_edge
    runtime.delivered(complete=True)
    _, _, next_screen, _ = catalog(shift=20)
    next_screen["screen_phash"] = next_hash
    assert runtime.match_pattern(next_screen) is not None  # Conditional plan before effect commit.
    assert runtime.state.pending["group_id"] == "next-edge"
    assert old_edge not in runtime.state.used
    runtime.verify_delivery(success=verified)
    assert (old_edge in runtime.state.used) is verified
    _, _, next_screen, _ = catalog(shift=20)
    next_screen["screen_phash"] = next_hash
    assert (runtime.match_pattern(next_screen) is not None) is verified


def test_same_frame_and_sources_do_not_make_another_control_the_same_edge(tmp_path):
    brain, _, _ = setup(tmp_path)
    runtime = brain.memory_retrieval
    _, _, screen, _ = catalog(shift=20)
    bound = runtime.match_pattern(screen)
    # Move Customer into Reference's region on the identical screenshot.
    fallback = bound.model_copy(
        update={
            "method_id": None,
            "steps": [
                step.model_copy(update={"point": [step.point[0], step.point[1] + 70]})
                for step in bound.steps
            ],
        }
    )
    runtime.remember(screen, fallback)
    assert runtime.state.pending.get("replaced_edge") is None
    assert not runtime.state.used
