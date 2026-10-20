"""Stage 1 regression baseline; no live model, desktop or production patches.

Synthetic reductions of reports education-context-audit-066 and
observed-facts-unification-20261017 (068--071). The private 071 call 10 response
was inspected: visible values + assignments coexist with a next-tab action.
Names, values and geometry below use the existing public test fixtures, NOT a
verbatim pilot response. Explicit evidence is omitted to expose duplicate-proof
requirements. Warm paths cover both promoted screen cache and actual compilation.
Physical dispatch remains covered by test_method_runtime, including its known xfail.
"""

import json
from copy import deepcopy

import pytest
from test_brain import record as record
from test_screen_matching import named_response, setup_brain

from brain.cache import VisualMatchCache
from brain.cache.methods import MethodLibrary
from brain.demo_learning.warm_methods import WarmCandidate, compile_warm_method
from brain.graph.execution import ActionPlan, PlanExecution
from brain.graph.schemas import Choice, Decision
from brain.graph.transitions import ObservedEffect
from brain.observe.summary import describe
from pipeline.schemas import OcrBlock


class MissingWarmEvidence(AssertionError):
    """Only measured warm memo loss is expected, not setup or other failures."""


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
    read = brain.visual_perception.ocr.read

    def current_ocr(page):
        # Synthetic CURRENT observation, not proof copied from cold history.
        return [
            *read(page),
            OcrBlock(id=9, text=record.fields.hbl_no.value, bbox=(110, 20, 90, 15), confidence=1),
        ]

    brain.visual_perception.ocr.read = current_ocr
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


def run_route(path, record, route):
    raw = observed_response(record)
    responses = [raw]
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
    if route in {"cold-to-warm-cache", "cold-to-warm-compiled"}:
        if route == "cold-to-warm-compiled":
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
        cold.visual_match_cache = VisualMatchCache(cache_path, enabled=True)
        cold._observe()
        assert cold.visual_match_candidate is not None
        cold.visual_match_cache.stage(*cold.visual_match_candidate)
        assert cold.visual_match_cache.finish(success=True) == 1
        if route == "cold-to-warm-compiled":
            screen = cold.visual_perception.planning_context()[0].screen
            candidate = WarmCandidate(
                "c000",
                cold.visual_match_candidate[0],
                str(cold.observation.obs_id),
                0,
                screen,
                cold.visual_proposal.model_dump(mode="json"),
            )
            compiled = compile_warm_method(
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
    if route == "cold-to-warm-cache":
        brain.visual_match_cache = VisualMatchCache(cache_path, enabled=True)
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
    if route == "cold-to-warm-compiled":
        assert not model.prompts
        assert sum(e.payload.get("check") == "method_bound" for e in brain.log.read_all()) == 1
        if "hbl_no" not in brain.verified:
            raise MissingWarmEvidence(
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
        0 if route.startswith("cold-to-warm") else 2 if route == "empty-action-recovery" else 1
    )
    assert len(model.prompts) == expected_calls, route
    events = brain.log.read_all()
    checks = [event.payload.get("check") for event in events]
    required_check = {
        "single-action": "input_value_matches",
        "field-batch": "screen_fill_values",
        "empty-action-recovery": "missing_match_actions_completed",
        "identical-capture": "identical_form_reused",
        "cold-to-warm-cache": "visual_match_cache",
        "cold-to-warm-compiled": "method_bound",
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
        "cold-to-warm-cache",
        pytest.param(
            "cold-to-warm-compiled",
            marks=pytest.mark.xfail(
                strict=True,
                raises=MissingWarmEvidence,
                reason=(
                    "Stage 3: compiled action target 3 / evidence target 4; "
                    "current value received but memo empty"
                ),
            ),
        ),
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
