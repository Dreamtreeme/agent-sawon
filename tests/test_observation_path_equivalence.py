"""Stage 1 regression baseline; no live model, desktop or production patches.

Synthetic reductions of reports education-context-audit-066 and
observed-facts-unification-20261017 (068--071). The private 071 call 10 response
was inspected: visible values + assignments coexist with a next-tab action.
Names, values and geometry below use the existing public test fixtures, NOT a
verbatim pilot response. Explicit evidence is omitted to expose duplicate-proof
requirements. Warm paths cover both promoted screen cache and actual compilation.
Physical dispatch remains covered by test_memory_retrieval.
"""

import json
from copy import deepcopy

import pytest
from test_brain import record as record
from test_screen_decision import named_response, setup_brain

from brain.cache import ScreenPatternCache
from brain.cache.methods import MethodLibrary
from brain.demo_learning.memory_compilation import MemoryCandidate, compile_memory
from brain.graph.execution import ActionPlan, PlanExecution
from brain.graph.schemas import Choice, Decision
from brain.graph.screen_memory import pending_screen_inputs
from brain.graph.transitions import ObservedEffect
from brain.observe.summary import describe
from pipeline.schemas import OcrBlock


def observed_inputs(brain, path=()):
    return {
        source: item["last_observation"]
        for source, item in brain.screen_nodes.get(json.dumps(list(path)), {})
        .get("fields", {})
        .items()
        if "last_observation" in item
    }


class MissingRetrievedEvidence(AssertionError):
    """Only measured warm memo loss is expected, not setup or other failures."""


class ObservationBlockedByPlan(AssertionError):
    """Synthetic 106 replay reached the specific observation/plan coupling defect."""


@pytest.mark.xfail(
    strict=True, raises=ObservationBlockedByPlan, reason="106 undeclared click target"
)
def test_synthetic_106_click_declaration_must_not_discard_observation(tmp_path, record):
    # Synthetic reduction of seq261/540, using public fixtures, not private ERP data.
    declared = observed_response(record)
    control, _ = prepare_worker(tmp_path / "declared", record, [declared])
    control._observe()
    assert observed_inputs(control)["hbl_no"]["value"] == record.fields.hbl_no.value
    assert control.visual_proposal.steps[0].point == [280, 75]
    missing = deepcopy(declared)
    del missing["fields"]["Continue"]
    brain, model = prepare_worker(tmp_path / "missing", record, [missing])
    try:
        brain._observe()
    except KeyError as exc:
        assert exc.args == ("Continue",)
        assert len(model.prompts) == 1
        assert "hbl_no" not in observed_inputs(brain)
        raise ObservationBlockedByPlan("Valid observed value lost on undeclared click") from exc
    assert observed_inputs(brain)["hbl_no"]["value"] == record.fields.hbl_no.value
    assert brain.visual_proposal.steps[0].point == [280, 75]


@pytest.mark.xfail(
    strict=True, raises=ObservationBlockedByPlan, reason="106 unused zero-size field"
)
def test_synthetic_106_unused_zero_area_must_not_discard_observation(tmp_path, record):
    # Synthetic seq469: the added text field has no action, evidence or parent references.
    valid = observed_response(record)
    control, _ = prepare_worker(tmp_path / "valid", record, [valid])
    control._observe()
    expected = observed_inputs(control)["hbl_no"]["value"]
    invalid = deepcopy(valid)
    invalid["fields"]["Unused title"] = {"role": "text", "bbox": [10, 8, 0, 0]}
    brain, model = prepare_worker(tmp_path / "invalid", record, [invalid])
    try:
        brain._observe()
    except ValueError as exc:
        assert str(exc) == "visual_bbox_outside_capture"
        assert len(model.prompts) == 1
        assert "hbl_no" not in observed_inputs(brain)
        raise ObservationBlockedByPlan("Unreferenced zero-area text discarded valid field") from exc
    assert observed_inputs(brain)["hbl_no"]["value"] == expected


@pytest.mark.xfail(
    strict=True, raises=ObservationBlockedByPlan, reason="106 recovery uses old memo"
)
def test_synthetic_106_recovery_must_receive_current_observed_memory(tmp_path, record):
    # Synthetic seq537/540: fake recovery does not prove how a real model would choose.
    from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES

    first = observed_response(record)
    first.update(steps=[], screen_path=["Registration", "Basic"])
    final = deepcopy(first)
    final["operation"] = "stop"
    brain, model = prepare_worker(tmp_path / "recovery", record, [first, final])
    brain._observe()
    assert len(model.prompts) == 2
    recovered = json.loads(model.prompts[1][len(CONTEXT_MAPPING_RULES) :])
    assert "hbl_no" in recovered["recovery"]["matched_inputs"]
    assert (
        observed_inputs(brain, first["screen_path"])["hbl_no"]["value"]
        == record.fields.hbl_no.value
    )
    assert brain.current_screen_path == first["screen_path"]
    notebook = recovered["notebook"]
    if not notebook["screen_tree"]["children"]:
        assert notebook["screen_tree"]["fields"] == {}
        raise ObservationBlockedByPlan("Recovery sees matching evidence but pre-observation memo")
    assert (
        notebook["screen_tree"]["children"]["Registration"]["children"]["Basic"]["fields"][
            "hbl_no"
        ]["last_observation"]["value"]
        == record.fields.hbl_no.value
    )


@pytest.mark.parametrize("compiled_hint", [False, True], ids=["run-local", "unscoped-compiled"])
def test_synthetic_screen_nodes_isolate_tabs_and_refresh_current_fields(
    tmp_path, record, compiled_hint
):
    # Synthetic 105 regression: identical screenshot/pHash, different active tabs.
    main = observed_response(record)
    main["screen_path"] = ["Registration", "Basic"]
    main["fields"]["Reference"]["source"] = "hbl_no"
    extra = deepcopy(main)
    extra["screen_path"] = ["Registration", "Additional"]
    extra["fields"] = {
        "MBL": {**main["fields"]["Reference"], "value": record.fields.mbl_no.value},
        "Continue": main["fields"]["Continue"],
    }
    extra["fields"]["MBL"]["source"] = "mbl_no"
    scrolled = deepcopy(main)
    scrolled["fields"]["Reference"]["bbox"] = [100, 65, 150, 30]
    hidden = deepcopy(main)
    hidden["fields"] = {"Continue": main["fields"]["Continue"]}
    changed = deepcopy(scrolled)
    changed["fields"]["Reference"]["value"] = "CHANGED-REFERENCE"
    unidentified = deepcopy(hidden)
    unidentified["screen_path"] = []
    brain, model = prepare_worker(
        tmp_path / "nodes", record, [main, extra, scrolled, changed, hidden, unidentified]
    )
    brain.screen_scanner.reuse_similar = True
    brain._observe()
    basic_key = json.dumps(main["screen_path"])
    basic_before = deepcopy(brain.screen_nodes[basic_key])
    if compiled_hint:
        # Synthetic legacy artifact without node identity; never current-view evidence.
        brain.screen_scanner.retrieve_screen_memory = lambda screen: {
            "fields": [{"name": "Reference", "source": "hbl_no", **main["fields"]["Reference"]}],
            "distance": 0,
            "groups": [],
        }
    # Put another tab's value exactly where the previous Reference was located.
    blocks = brain.screen_scanner.ocr.read(None)
    current = [
        b.model_copy(update={"text": record.fields.mbl_no.value}) if b.id == 9 else b
        for b in blocks
    ]
    brain.screen_scanner.ocr.read = lambda page: current
    brain._observe()
    assert brain.current_screen_path == extra["screen_path"]
    assert brain.screen_nodes[basic_key] == basic_before
    assert set(observed_inputs(brain, extra["screen_path"])) == {"mbl_no"}
    assert set(
        field.source for field in brain.visual_proposal.mapping.fields.values() if field.source
    ) == {"mbl_no"}
    assert (
        observed_inputs(brain, main["screen_path"])["hbl_no"]["value"] == record.fields.hbl_no.value
    )
    assert "hbl_no" in brain.verified
    # Returning to Basic and scrolling must replace the old field position, not revive it.
    current = [b.model_copy(update={"text": "OTHER FIELD"}) if b.id == 9 else b for b in blocks]
    current.append(
        OcrBlock(id=109, text=record.fields.hbl_no.value, bbox=(110, 70, 90, 15), confidence=1)
    )
    brain._observe()
    assert brain.current_screen_path == main["screen_path"]
    node = brain.screen_nodes[basic_key]
    assert node["fields"]["hbl_no"]["bbox"] == [100, 65, 150, 30]
    assert node["fields"]["hbl_no"]["last_observation"]["value"] == record.fields.hbl_no.value
    assert "hbl_no" in brain.verified
    inputs = [e for e in brain.observation.elements if e.role == "edit"]
    assert len(inputs) == 1 and inputs[0].bbox[1] == 65
    current[-1] = current[-1].model_copy(update={"text": "CHANGED-REFERENCE"})
    brain._observe()
    assert node["fields"]["hbl_no"]["last_observation"]["value"] == "CHANGED-REFERENCE"
    assert "hbl_no" not in brain.verified
    last_seen = deepcopy(node)
    brain._observe()
    assert brain.screen_nodes[basic_key]["fields"] == last_seen["fields"]
    assert all(field.source is None for field in brain.visual_proposal.mapping.fields.values())
    brain._observe()
    assert brain.current_screen_path == []  # Never infer the previous node when unidentified.
    assert len(brain.screen_nodes) == 4  # Root, parent and two children; no scroll node.
    memo = json.loads((brain.case_dir / "task-notebook.json").read_text(encoding="utf-8"))
    assert memo["screen_nodes"] == brain.screen_nodes
    assert len(model.prompts) == 6  # No extra model call for choosing a node.
    assert '"screen_tree"' in model.prompts[2]
    assert record.fields.mbl_no.value in model.prompts[2]
    from brain.graph.screen_memory import recall_screen_tree

    before_projection = deepcopy(brain.screen_nodes)
    tree = recall_screen_tree(brain.screen_nodes)
    tabs = tree["children"]["Registration"]["children"]
    assert set(tabs) == {"Basic", "Additional"}
    assert set(tabs["Basic"]["fields"]) == {"hbl_no"}
    assert set(tabs["Additional"]["fields"]) == {"mbl_no"}
    assert "Continue" in tabs["Basic"]["controls"]
    assert "seen_in_last_view" not in tabs["Basic"]["fields"]["hbl_no"]
    assert "bbox" not in json.dumps(tree)
    tree["children"]["Registration"]["children"]["Basic"]["fields"]["hbl_no"]["label"] = "changed"
    assert brain.screen_nodes == before_projection


def observed_response(record):
    return named_response(
        screen_kind="dialog",  # 070: an intermediate dialog must not erase pending review.
        fields={
            "Reference": {
                "source": "hbl_no",
                "role": "edit",
                "bbox": [100, 15, 150, 30],
                "value": record.fields.hbl_no.value,
            },
            "Continue": {"role": "button", "bbox": [250, 60, 80, 30]},
        },
        evidence=[],
        steps=[{"target": "Continue", "action": "click", "intent": "navigate", "point": [280, 75]}],
    )


def test_synthetic_model_memory_delta_is_not_reinterpreted_from_ocr(tmp_path, record):
    # Synthetic fake model response, real observe/memory/recall functions; no ERP/API.
    from brain.graph.context_recall import recall_context

    response = observed_response(record)
    response["screen_path"] = ["Registration", "Basic"]
    brain, model = prepare_worker(tmp_path / "model-memory", record, [response])
    brain._observe()
    plan_before = brain.visual_proposal.model_dump()
    # A subsequent executor projection disagrees with the model's saved reading.
    # It must not overwrite the model-owned memory through the evidence loop.
    brain.observation = brain.observation.model_copy(
        update={
            "elements": [
                item.model_copy(update={"value": "OCR-PROJECTION-DIFFERS"})
                if item.role == "edit"
                else item
                for item in brain.observation.elements
            ]
        }
    )
    brain._remember_model_task_evidence(brain.visual_proposal)
    recalled = recall_context(brain._screen_decision_request({"ocr": [], "markers": []}, None))
    memory = recalled["notebook"]["screen_tree"]["children"]["Registration"]["children"]["Basic"][
        "fields"
    ]["hbl_no"]
    assert memory["last_observation"]["value"] == record.fields.hbl_no.value
    assert "hbl_no" in brain.verified
    assert brain.evidence_ledger.owner(brain.verified["hbl_no"]) == "hbl_no"
    assert brain.visual_proposal.model_dump() == plan_before
    assert len(model.prompts) == 1
    assert not any(event.kind == "act" for event in brain.log.read_all())


def test_synthetic_memory_uses_explicit_model_bindings_and_preserves_unseen_values():
    from brain.graph.screen_mapping import ScreenMapping
    from brain.graph.screen_memory import remember_screen

    nodes = {}
    values = {"reference": "EXPECTED", "other": "OTHER"}
    mapping = ScreenMapping(
        screen_path=["Editor"],
        fields={
            "Ref": {
                "source": "reference",
                "role": "text",
                "bbox": [10, 10, 40, 20],
                "value": "READ",
            }
        },
    )
    node = remember_screen(nodes, mapping, values, "frame-1", observation_number=1, scope="entry")
    assert node["fields"]["reference"]["last_observation"]["value"] == "READ"
    # Hidden/omitted readings do not clear a fact; an explicit blank does.
    for label, fields, expected in [
        ("offscreen", {}, "READ"),
        (
            "unread",
            {"Ref": {"source": "reference", "role": "text", "bbox": [10, 10, 40, 20]}},
            "READ",
        ),
        (
            "empty",
            {"Ref": {"source": "reference", "role": "text", "bbox": [10, 10, 40, 20], "value": ""}},
            "",
        ),
    ]:
        update = mapping.model_copy(update={"fields": ScreenMapping(fields=fields).fields})
        remember_screen(nodes, update, values, label, observation_number=2, scope="entry")
        assert node["fields"]["reference"]["last_observation"]["value"] == expected, label
    # Neither a fill plan nor an equal literal value creates an observed binding.
    unmapped = ScreenMapping(
        screen_path=["Other"],
        fields={
            "Ref": {
                "source": "EXPECTED",
                "role": "edit",
                "bbox": [10, 10, 40, 20],
                "value": "EXPECTED",
            }
        },
        steps=[
            {
                "target": "Ref",
                "action": "fill",
                "intent": "fill",
                "source": "reference",
                "point": [20, 20],
            }
        ],
    )
    other = remember_screen(
        nodes, unmapped, values, "frame-3", observation_number=3, scope="saved_detail"
    )
    assert other["fields"] == {}
    assert "Ref" in other["controls"]
    assert node["fields"]["reference"]["last_observation"]["scope"] == "entry"


def test_synthetic_116_screen_tree_keeps_delivered_inputs_across_scrolls(tmp_path, record):
    # Synthetic reduction of 116: fake model/relay, real observe/plan/dispatch/memory functions.
    from sawon_interfaces import ActionResult

    from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES

    def view(name, source, value, *, steps, bbox=(100, 15, 150, 30)):
        return named_response(
            screen_kind="editor",
            screen_path=["Registration", "Details"],
            fields={name: {"source": source, "role": "edit", "bbox": list(bbox), "value": value}},
            steps=steps,
            operation="act" if steps else "stop",
        )

    upper = view(
        "Reference",
        "hbl_no",
        "",
        steps=[
            {"target": "Reference", "action": "fill", "intent": "fill", "point": [175, 30]},
            {
                "target": "Reference",
                "action": "scroll",
                "intent": "navigate",
                "direction": "down",
                "point": [300, 130],
            },
        ],
    )
    lower = view(
        "Master",
        "mbl_no",
        "",
        steps=[{"target": "Master", "action": "fill", "intent": "fill", "point": [175, 30]}],
    )
    lower_done = view("Master", "mbl_no", record.fields.mbl_no.value, steps=[])
    upper_done = view(
        "Reference", "hbl_no", record.fields.hbl_no.value, steps=[], bbox=(100, 65, 150, 30)
    )
    brain, model, relay, *_ = setup_brain(tmp_path, record, [upper, lower, lower_done, upper_done])
    brain.settings = brain.settings.model_copy(
        update={"guard_mode": "observe_only", "visual_model_only_review": True}
    )
    sent = []

    def fake_act(action):
        sent.append(action.root)
        if action.root.type == "scroll":
            relay.color = "black"
        return ActionResult(
            action_id=action.root.action_id,
            ok=True,
            duration_ms=0,
            post_obs_id=relay.current.obs_id,
        )

    relay.act = fake_act
    brain._observe()
    for label in ["upper then scroll", "lower input"]:
        assert brain._select_node() == "review", label
        assert brain._review_node() == "execution", label
        assert brain._act_node() == "observation", label
        persisted = json.loads((tmp_path / "task-notebook.json").read_text(encoding="utf-8"))
        entries = persisted["screen_nodes"][json.dumps(["Registration", "Details"])]["fields"]
        assert entries["hbl_no"]["delivery"]["value"] == record.fields.hbl_no.value, label
        assert "hbl_no" in pending_screen_inputs(brain.screen_nodes, "entry"), label
        assert brain._verify_node() == "review", label

    assert [a.type for a in sent] == ["set_text", "scroll", "set_text"]
    request = json.loads(model.prompts[2][len(CONTEXT_MAPPING_RULES) :])
    notebook = request["notebook"]
    node = notebook["screen_tree"]["children"]["Registration"]["children"]["Details"]
    assert set(node["fields"]) == {"hbl_no", "mbl_no"}
    upper_memory = node["fields"]["hbl_no"]
    assert upper_memory["delivery"]["value"] == record.fields.hbl_no.value
    assert "status" not in upper_memory["delivery"]
    assert "seen_in_last_view" not in upper_memory
    assert node["fields"]["mbl_no"]["delivery"]["value"] == record.fields.mbl_no.value
    assert set(notebook["pending_verification_inputs"]) == {"hbl_no", "mbl_no"}
    assert "bbox" not in json.dumps(notebook["screen_tree"])
    assert "screen_nodes" not in notebook  # Do not also expose the old flat coordinate memory.
    assert "hbl_no" not in brain.verified  # Delivery is not visual confirmation.
    assert "mbl_no" in brain.verified
    # The response has now been applied: the old delivered batch cannot keep MBL pending.
    from brain.graph.context_recall import recall_context

    current = recall_context(brain._screen_decision_request({"ocr": [], "markers": []}, None))[
        "notebook"
    ]
    assert current["pending_verification_inputs"] == ["hbl_no"]

    brain.pending = None
    brain._observe()
    node = brain.screen_nodes[json.dumps(["Registration", "Details"])]
    assert "hbl_no" not in pending_screen_inputs(brain.screen_nodes, "entry")
    assert node["fields"]["hbl_no"]["last_observation"]["value"] == record.fields.hbl_no.value
    assert node["fields"]["hbl_no"]["bbox"] == [100, 65, 150, 30]
    assert node["fields"]["mbl_no"]["last_observation"]["value"] == record.fields.mbl_no.value
    assert {"hbl_no", "mbl_no"} <= brain.verified.keys()
    assert len(model.prompts) == 4  # Tree maintenance introduces no extra model calls.


def test_synthetic_screen_tree_partial_delivery_and_saved_review_scope(tmp_path, record):
    # Fake relay rejects the second input. Only acknowledged delivery may enter the tree.
    from sawon_interfaces import ActionResult

    from brain.graph.context_recall import recall_context

    data = named_response(
        screen_kind="editor",
        screen_path=["Registration"],
        fields={
            "Reference": {"source": "hbl_no", "role": "edit", "bbox": [100, 15, 150, 30]},
            "Master": {"source": "mbl_no", "role": "edit", "bbox": [100, 65, 150, 30]},
        },
        steps=[
            {"target": "Reference", "action": "fill", "intent": "fill", "point": [175, 30]},
            {"target": "Master", "action": "fill", "intent": "fill", "point": [175, 80]},
        ],
    )
    mismatch = deepcopy(data)
    mismatch.update(steps=[], operation="stop")
    mismatch["fields"]["Reference"]["value"] = "WRONG-REFERENCE"
    brain, _, relay, *_ = setup_brain(tmp_path, record, [data, mismatch])
    brain.settings = brain.settings.model_copy(
        update={"guard_mode": "observe_only", "visual_model_only_review": True}
    )

    def fake_act(action):
        ok = action.root.text == brain.values["hbl_no"]
        return ActionResult(
            action_id=action.root.action_id,
            ok=ok,
            duration_ms=0,
            post_obs_id=relay.current.obs_id,
            error=None if ok else "action_failed",
        )

    relay.act = fake_act
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    brain._act_node()
    node = brain.screen_nodes[json.dumps(["Registration"])]
    assert "hbl_no" in pending_screen_inputs(brain.screen_nodes, "entry")
    assert "delivery" not in node["fields"]["mbl_no"]
    assert not brain.verified
    assert "observed_inputs" not in json.loads(
        (tmp_path / "task-notebook.json").read_text(encoding="utf-8")
    )
    assert brain.pending is None

    def recalled():
        return recall_context(brain._screen_decision_request({"ocr": [], "markers": []}, None))[
            "notebook"
        ]

    assert recalled()["pending_verification_inputs"] == ["hbl_no"]
    brain.save_issued = True
    brain.saved_detail_transition_pending = True
    assert recalled()["evidence_scope"] == "saved_detail_candidate"
    assert recalled()["pending_verification_inputs"] == []
    brain.model_saved_detail_review = True
    assert recalled()["evidence_scope"] == "saved_detail"
    assert recalled()["pending_verification_inputs"] == []
    brain.model_saved_detail_review = brain.saved_detail_transition_pending = False
    brain._observe()
    assert "hbl_no" not in pending_screen_inputs(brain.screen_nodes, "entry")
    assert node["fields"]["hbl_no"]["last_observation"]["value"] == "WRONG-REFERENCE"
    assert "hbl_no" not in brain.verified


def test_synthetic_input_receipt_order_and_screen_scope_replace_status_flags():
    # Synthetic memory facts. Identical capture IDs/values cannot prove a later reread.
    from brain.graph.screen_memory import recall_screen_tree, remember_input_delivery

    path = ["Registration", "Basic"]
    key = json.dumps(path)
    entry = {
        "label": "Reference",
        "role": "edit",
        "last_observation": {
            "value": "SAME-VALUE",
            "scope": "entry",
            "observation_id": "same-capture",
            "observation_number": 1,
        },
    }
    nodes = {key: {"path": path, "fields": {"reference": entry}, "controls": {}}}
    for read_number, scope, pending in [
        (1, "entry", True),
        (2, "entry", False),
        (2, "saved_detail", True),
    ]:
        remember_input_delivery(
            nodes,
            path,
            "reference",
            "SAME-VALUE",
            observation_id="same-capture",
            observation_number=1,
            scope="entry",
        )
        entry["last_observation"].update(observation_number=read_number, scope=scope)
        assert pending_screen_inputs(nodes, "entry") == ({"reference"} if pending else set()), (
            read_number,
            scope,
        )
    # Another node may use the same source without replacing this node's pending delivery.
    other_path = ["Registration", "Additional"]
    other = deepcopy(nodes[key])
    other["path"] = other_path
    other["fields"]["reference"]["last_observation"].update(scope="entry")
    nodes[json.dumps(other_path)] = other
    assert pending_screen_inputs(nodes, "entry") == {"reference"}
    tree = recall_screen_tree(nodes)
    tabs = tree["children"]["Registration"]["children"]
    assert tabs["Basic"]["fields"]["reference"]["last_observation"]["scope"] == "saved_detail"
    assert tabs["Additional"]["fields"]["reference"]["last_observation"]["scope"] == "entry"
    assert "status" not in json.dumps(tree)
    assert "observation_number" not in json.dumps(tree)


def prepare_worker(path, record, responses):
    path.mkdir(parents=True)
    brain, model, *_ = setup_brain(path, record, responses)
    read = brain.screen_scanner.ocr.read

    def current_ocr(page):
        # Synthetic CURRENT observation, not proof copied from cold history.
        return [
            *read(page),
            OcrBlock(id=9, text=record.fields.hbl_no.value, bbox=(110, 20, 90, 15), confidence=1),
        ]

    brain.screen_scanner.ocr.read = current_ocr
    brain.settings = brain.settings.model_copy(
        update={"guard_mode": "observe_only", "visual_model_only_review": True}
    )
    # Identical run-local input state; never copied from a previous cold run.
    brain.save_issued = True
    brain.saved_detail_transition_pending = True
    brain.saved_detail_transition_step = "requery-saved-record"
    brain.rolling_context.record_turn(
        turn_index=0,
        intent="search",
        target_summary="key:enter",
        action_type="key",
        observed_effect="changed",
        success=True,
        checkpoint="task",
    )
    return brain, model


def semantic_memory(brain):
    # Frame IDs and evidence receipt revisions differ legitimately on reread.
    # Do not discard values, bindings,
    # action history or the lifetime of pending detail review from the comparison.
    return {
        "verified": sorted(brain.verified),
        "evidence": [
            {"field": claim.field}
            for claim in sorted(brain.evidence_ledger.history, key=lambda item: item.field)
            if claim.status == "active"
        ],
        # Compiled targets use the input key as a display name. Compare the
        # actual source -> value AND geometry binding, not alias spelling.
        "visible_values": {
            source: [
                (element.value, element.bbox)
                for element in brain.observation.elements
                if describe(element, brain.observation.elements) == descriptor
            ]
            for source, descriptor in brain.verified.items()
        },
        "recent_actions": [turn.model_dump() for turn in brain.rolling_context.recent_turns],
        "milestones": brain.rolling_context.completed_milestones,
        "pending_detail": (
            brain.saved_detail_transition_pending,
            brain.saved_detail_transition_step,
            brain.model_saved_detail_review,
            sorted(brain.post_save_verified),
        ),
    }


def run_route(path, record, route):
    raw = observed_response(record)
    # Identical pixels reuse detection, while current context still passes through decision.
    responses = [raw, deepcopy(raw)] if route == "identical-capture" else [raw]
    if route == "empty-action-recovery":
        initial = {**deepcopy(raw), "steps": []}
        next_action_only = {
            **deepcopy(raw),
            "fields": deepcopy(raw["fields"]),
        }
        responses = [initial, next_action_only]
    cache_path = path / "cache.json"
    compiled = None
    if route in {"model-to-pattern-cache", "model-to-compiled-memory"}:
        if route == "model-to-compiled-memory":
            training = deepcopy(raw)
            training["steps"] = [
                {
                    "target": "Reference",
                    "action": "fill",
                    "intent": "fill",
                    "source": "hbl_no",
                    "point": [175, 30],
                }
            ]
            responses = [training]
        cold, _ = prepare_worker(path / "cold", record, responses)
        cold.visual_match_cache = ScreenPatternCache(cache_path, enabled=True)
        cold._observe()
        assert cold.visual_match_candidate is not None
        cold.visual_match_cache.stage(*cold.visual_match_candidate)
        assert cold.visual_match_cache.finish(success=True) == 1
        if route == "model-to-compiled-memory":
            screen = cold.screen_scanner.planning_context()[0].screen
            candidate = MemoryCandidate(
                "c000",
                cold.visual_match_candidate[0],
                str(cold.observation.obs_id),
                0,
                screen,
                cold.visual_proposal.model_dump(mode="json"),
            )
            compiled = compile_memory(
                {
                    "source_case": "synthetic-cold",
                    "task": cold.task_instruction.model_dump(mode="json"),
                    "task_sha256": cold.task_instruction_sha256,
                    "values": cold.values,
                },
                [candidate],
                {
                    "purpose": "Fill reference",
                    "groups": [
                        {
                            "candidate_id": "c000",
                            "purpose": "Fill reference",
                            "context_anchors": [],
                            "expected_result": "Reference matches",
                        }
                    ],
                },
                method_id="synthetic-warm",
            )
        responses = [raw] if compiled is not None else []
    brain, model = prepare_worker(path / "current", record, responses)
    if route == "model-to-pattern-cache":
        brain.visual_match_cache = ScreenPatternCache(cache_path, enabled=True)
    elif compiled is not None:
        brain.method_library = MethodLibrary(path / "compiled.json", enabled=True)
        brain.method_library.upsert(compiled)
    brain._observe()
    if route == "identical-capture":
        first_id = brain.observation.obs_id
        first_hash = brain.observation.capture_phash
        brain._observe()
        assert brain.observation.obs_id != first_id
        assert brain.observation.capture_phash == first_hash
        assert first_hash is not None

    # Assert value receipt BEFORE verification, so later helpers cannot conceal loss.
    observed = semantic_memory(brain)
    assert any(
        e.value == record.fields.hbl_no.value and e.role == "edit"
        for e in brain.observation.elements
    ), f"{route}: current value was not received"
    assert observed_inputs(brain).get("hbl_no", {}).get("value") == record.fields.hbl_no.value, (
        route
    )
    if route == "model-to-compiled-memory":
        assert len(model.prompts) == 1
        assert sum(e.payload.get("check") == "method_bound" for e in brain.log.read_all()) == 1
        if "hbl_no" not in brain.verified:
            raise MissingRetrievedEvidence(
                f"Current value received; action targets="
                f"{[step.target for step in brain.visual_proposal.steps]}, "
                f"evidence targets={[item.target for item in brain.visual_proposal.evidence]}; "
                f"verified={brain.verified}"
            )
    assert observed["visible_values"] == {
        "hbl_no": [(record.fields.hbl_no.value, (100, 15, 150, 30))]
    }, route
    assert set(observed["verified"]) == {"hbl_no"}, route
    assert observed["pending_detail"] == (True, "requery-saved-record", False, []), route
    assert len(observed["recent_actions"]) == 1, route
    target = next(
        e
        for e in brain.observation.elements
        if describe(e, brain.observation.elements) == brain.verified["hbl_no"]
    )
    decision = Decision(
        screen_kind="dialog",
        operation="act",
        intent="fill",
        subgoal="fill",
        reason="Synthetic acknowledged input, awaiting observation",
        action=Choice(type="set_text", ref=target.ref, source="hbl_no"),
    )
    choice = decision.action
    if route == "field-batch":
        execution = PlanExecution(
            ActionPlan.single(decision),
            brain.observation,
            delivered=[{"choice": choice, "decision": decision}],
        )
        assert brain._verify_screen_batch(execution, brain.observation) == "review"
        assert execution.matched_refs == {target.ref}
    else:
        facts = []
        outcome, _, effect = brain._verify_text_input(
            {
                "descriptor": describe(target, brain.observation.elements),
                "before_observation": brain.observation,
            },
            brain.observation,
            choice,
            decision,
            facts,
            ObservedEffect.NO_CHANGE,
        )
        assert outcome is None, route
        brain._record_action_verification(decision, choice, None, None, effect, facts)
    expected_calls = (
        0
        if route == "model-to-pattern-cache"
        else 2
        if route in {"empty-action-recovery", "identical-capture"}
        else 1
    )
    assert len(model.prompts) == expected_calls, route
    events = brain.log.read_all()
    checks = [event.payload.get("check") for event in events]
    required_check = {
        "single-action": "input_value_matches",
        "field-batch": "screen_fill_values",
        "empty-action-recovery": "missing_match_actions_completed",
        "identical-capture": "similar_form_reused",
        "model-to-pattern-cache": "visual_match_cache",
        "model-to-compiled-memory": "method_bound",
    }[route]
    assert checks.count(required_check) == 1, (route, checks)
    return semantic_memory(brain)


@pytest.mark.parametrize(
    "route",
    [
        "single-action",
        "field-batch",
        "empty-action-recovery",
        "identical-capture",
        "model-to-pattern-cache",
        "model-to-compiled-memory",
    ],
)
def test_synthetic_same_facts_reach_same_memory(tmp_path, record, route):
    reference = run_route(tmp_path / "reference", record, "single-action")
    actual = run_route(tmp_path / route, record, route)
    assert actual == reference, f"route={route}: semantic memory differs"
    assert actual["recent_actions"][-1]["target_summary"] == "set_text:hbl_no"


@pytest.mark.parametrize("variant", ["description", "long-description", "field-order"])
def test_synthetic_semantic_response_variants_preserve_executable_action(tmp_path, record, variant):
    original = observed_response(record)
    changed = deepcopy(original)
    if variant == "field-order":
        changed["fields"] = dict(reversed(list(changed["fields"].items())))
        changed = dict(reversed(list(changed.items())))
    else:
        changed["purpose"] = "Same observed input; continue. " * (
            20 if variant == "long-description" else 1
        )
    results = []
    for label, response in (("original", original), (variant, changed)):
        brain, model = prepare_worker(tmp_path / label, record, [response])
        brain._observe()
        if "purpose" in response:
            assert brain.visual_proposal.purpose == response["purpose"][:160], label
        assert brain._select_node() == "review", label
        decision = brain.decision or brain.action_plan.actions[0].decision
        wire = brain._wire_action(decision.action, decision).root
        results.append((wire.type, wire.target.image_point, semantic_memory(brain)))
        assert len(model.prompts) == 1, label
        assert brain.visual_plan_error is None, label
        if variant != "field-order" and label == variant:
            # Long prose may be summarized internally, never truncate business values.
            artifacts = list(brain.case_dir.glob("matching/*semantic-mapping.json"))
            assert any(
                json.loads(p.read_text(encoding="utf-8"))["purpose"] == changed["purpose"]
                for p in artifacts
            )
    assert results[0] == results[1], variant


def test_synthetic_118_recovery_replaces_mistaken_header_observation(tmp_path, record):
    # Synthetic reduction of 118: fake responses, actual observation/recovery/memory pipeline.
    first = observed_response(record)
    first["steps"] = []
    first["fields"]["Mistaken header"] = {"role": "edit", "bbox": [10, 120, 90, 20], "value": ""}
    first["fields"]["Mistaken header"]["source"] = "description"
    final = observed_response(record)
    brain, model = prepare_worker(tmp_path / "header", record, [first, final])
    brain._observe()
    assert len(model.prompts) == 2
    assert "Mistaken header" not in brain.visual_proposal.mapping.fields
    assert "description" not in observed_inputs(brain)
    assert observed_inputs(brain)["hbl_no"]["value"] == record.fields.hbl_no.value
