"""Stage 1 regression baseline; no live model, desktop or production patches.

Synthetic reductions of reports education-context-audit-066 and
observed-facts-unification-20261017 (068--071). The private 071 call 10 response
was inspected: visible values + assignments coexist with a next-tab action.
Names, values and geometry below use the existing public test fixtures, NOT a
verbatim pilot response. Explicit evidence is omitted to expose duplicate-proof
requirements. Warm paths cover both promoted screen cache and actual compilation.
Physical dispatch remains covered by test_memory_retrieval, including its known xfail.
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
from brain.graph.transitions import ObservedEffect
from brain.observe.summary import describe
from pipeline.schemas import OcrBlock


class MissingRetrievedEvidence(AssertionError):
    """Only measured warm memo loss is expected, not setup or other failures."""


@pytest.mark.parametrize("compiled_hint", [False, True], ids=["run-local", "unscoped-compiled"])
def test_synthetic_screen_nodes_isolate_tabs_and_refresh_current_fields(
    tmp_path, record, compiled_hint
):
    # Synthetic 105 regression: identical screenshot/pHash, different active tabs.
    main = observed_response(record)
    main["screen_path"] = ["Registration", "Basic"]
    main["assignments"] = {"Reference": "hbl_no"}
    extra = deepcopy(main)
    extra["screen_path"] = ["Registration", "Additional"]
    extra["fields"] = {
        "MBL": {**main["fields"]["Reference"], "value": record.fields.mbl_no.value},
        "Continue": main["fields"]["Continue"],
    }
    extra["assignments"] = {"MBL": "mbl_no"}
    scrolled = deepcopy(main)
    scrolled["fields"]["Reference"]["bbox"] = [100, 65, 150, 30]
    hidden = deepcopy(main)
    hidden["fields"] = {"Continue": main["fields"]["Continue"]}
    hidden["assignments"] = {}
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
    additional_key = json.dumps(extra["screen_path"])
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
    assert set(brain.screen_nodes[additional_key]["observed_inputs"]) == {"mbl_no"}
    assert set(
        brain.screen_scanner.observed_input_bindings(str(brain.observation.obs_id)).values()
    ) == {"mbl_no"}
    assert brain.observed_inputs["hbl_no"]["value"] == record.fields.hbl_no.value
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
    assert node["observed_inputs"]["hbl_no"]["value"] == record.fields.hbl_no.value
    assert "hbl_no" in brain.verified
    inputs = [e for e in brain.observation.elements if e.role == "edit"]
    assert len(inputs) == 1 and inputs[0].bbox[1] == 65
    current[-1] = current[-1].model_copy(update={"text": "CHANGED-REFERENCE"})
    brain._observe()
    assert node["observed_inputs"]["hbl_no"]["value"] == "CHANGED-REFERENCE"
    assert "hbl_no" not in brain.verified
    last_seen = deepcopy(node)
    brain._observe()
    assert brain.screen_nodes[basic_key]["observed_inputs"] == last_seen["observed_inputs"]
    assert brain.screen_nodes[basic_key]["fields"] == last_seen["fields"]
    assert brain.screen_scanner.observed_input_bindings(str(brain.observation.obs_id)) == {}
    brain._observe()
    assert brain.current_screen_path == []  # Never infer the previous node when unidentified.
    assert len(brain.screen_nodes) == 3  # Parent plus two children, no scroll node.
    memo = json.loads((brain.case_dir / "task-notebook.json").read_text(encoding="utf-8"))
    assert memo["screen_nodes"] == brain.screen_nodes
    assert len(model.prompts) == 6  # No extra model call for choosing a node.
    assert '"screen_nodes"' in model.prompts[2]
    assert record.fields.mbl_no.value in model.prompts[2]


def observed_response(record):
    return named_response(
        screen_kind="dialog",  # 070: an intermediate dialog must not erase pending review.
        fields={
            "Reference": {
                "role": "edit",
                "bbox": [100, 15, 150, 30],
                "value": record.fields.hbl_no.value,
            },
            "Continue": {"role": "button", "bbox": [250, 60, 80, 30]},
        },
        evidence=[],
        steps=[{"target": "Continue", "action": "click", "intent": "navigate", "point": [280, 75]}],
    )


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
            {k: v for k, v in item.items() if k not in {"observation_id", "revision"}}
            for item in brain.evidence_ledger.prompt_context()
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


def run_route(path, record, route, *, mutate=None):
    raw = observed_response(record)
    # Identical pixels reuse detection, while current context still passes through decision.
    responses = [raw, deepcopy(raw)] if route == "identical-capture" else [raw]
    if route == "empty-action-recovery":
        initial = {**deepcopy(raw), "steps": []}
        next_action_only = {
            **deepcopy(raw),
            "fields": {"Continue": raw["fields"]["Continue"]},
            "assignments": {},
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
        responses = []
    brain, model = prepare_worker(path / "current", record, responses)
    if route == "model-to-pattern-cache":
        brain.visual_match_cache = ScreenPatternCache(cache_path, enabled=True)
    elif compiled is not None:
        brain.method_library = MethodLibrary(path / "compiled.json", enabled=True)
        brain.method_library.upsert(compiled)
    if mutate is not None:
        mutate("before-observe", brain)
    brain._observe()
    if route == "identical-capture":
        first_id = brain.observation.obs_id
        first_hash = brain.observation.capture_phash
        if mutate is not None:
            mutate("before-reobserve", brain)
        brain._observe()
        assert brain.observation.obs_id != first_id
        assert brain.observation.capture_phash == first_hash
        assert first_hash is not None

    # Assert value receipt BEFORE verification, so later helpers cannot conceal loss.
    observed = semantic_memory(brain)
    if mutate is not None and route in {
        "empty-action-recovery",
        "identical-capture",
        "model-to-pattern-cache",
    }:
        # Compare memory itself, before prerequisite assertions or target lookup
        # can mask the missing update with an unrelated exception.
        return observed
    assert any(
        e.value == record.fields.hbl_no.value and e.role == "edit"
        for e in brain.observation.elements
    ), f"{route}: current value was not received"
    if route == "model-to-compiled-memory":
        assert not model.prompts
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
    if mutate is not None:
        return semantic_memory(brain)
    expected_calls = (
        0
        if route.startswith("model-to-")
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


@pytest.mark.parametrize(
    "route",
    [
        "single-action",
        "field-batch",
        "empty-action-recovery",
        "identical-capture",
        "model-to-pattern-cache",
    ],
)
def test_baseline_detects_disabled_memory_update(tmp_path, record, monkeypatch, route):
    reference = run_route(tmp_path / "reference", record, "single-action")
    invoked = []

    def disable(stage, brain):
        if route == "identical-capture":
            if stage == "before-reobserve":

                def omit_values(wire, scene, selected):
                    invoked.append("_apply_field_values")
                    return scene

                monkeypatch.setattr(brain.screen_scanner, "_apply_field_values", omit_values)
        elif stage == "before-observe":
            name = {
                "single-action": "_record_action_verification",
                "field-batch": "_remember_action_effect",
                "empty-action-recovery": "_remember_model_task_evidence",
                "model-to-pattern-cache": "_remember_model_task_evidence",
            }[route]

            def omit_memory(*args, **kwargs):
                invoked.append(name)

            monkeypatch.setattr(brain, name, omit_memory)

    with pytest.raises(AssertionError, match="semantic memory differs"):
        actual = run_route(tmp_path / route, record, route, mutate=disable)
        assert actual == reference, f"route={route}: semantic memory differs"
        assert actual["recent_actions"][-1]["target_summary"] == "set_text:hbl_no"
    assert invoked, f"route={route}: mutation was never exercised"


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
