"""Shared worker execution context, independent of provider and perception adapter.

Pending outcomes describe a conditional proposal, never mutate verified runtime facts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .execution import PendingAction, describe_action
from .policy import DecisionPolicy
from .save_review import SAVE_RESULT_ACTIONS, SAVE_REVIEW_GUIDANCE
from .schemas import Decision


@dataclass(frozen=True)
class ExecutionContextRequest:
    """Runtime facts needed to describe the next execution boundary.

    The context compiler is shared by direct and visual matching paths.  It must
    not reach back into the mutable worker object, because doing so made prompt
    construction depend on undocumented Brain attributes.
    """

    policy: DecisionPolicy
    pending: PendingAction | None
    current_decision: Decision | None
    values: dict[str, str]
    phase: str
    saved_record_review: dict[str, Any] | None = None
    saved_identity_field: str | None = None


def phase_instruction(policy: DecisionPolicy) -> str:
    if policy.current_step == "task":
        return "Use the user task description as the workflow."
    return f"Current phase: {policy.facts.phase}; current step: {policy.current_step}."


def _delivered_actions(pending: PendingAction | None) -> list[dict]:
    """Project actual deliveries without turning a plan's purpose into history."""
    if pending is None:
        return []
    execution = pending.get("execution")
    deliveries = execution.delivered if execution is not None else [pending]
    result = []
    for delivered in deliveries:
        decision = delivered.get("decision")
        if decision is None:
            continue
        result.append(describe_action(decision, delivered.get("before_observation")))
    return result


def execution_context(request: ExecutionContextRequest) -> dict:
    """Compile model-facing execution context from an explicit immutable request."""

    actual = request.policy
    proposal = actual
    pending = request.pending
    decisions = []
    if pending:
        execution = pending.get("execution")
        decisions = (
            [a.decision for a in execution.plan.actions]
            if execution
            else [pending.get("decision", request.current_decision)]
        )
        last = decisions[-1]
        facts = actual.facts
        if facts.phase in {"task", "enter"}:
            pending_fields = {
                decision.action.source
                for decision in decisions
                if decision is not None
                and decision.intent == "fill"
                and decision.action is not None
                and decision.action.source in facts.field_names
            }
            effect_source = pending.get("effect_source")
            if (
                last is not None
                and last.intent == "select_option"
                and effect_source in facts.field_names
            ):
                pending_fields.add(effect_source)
            if facts.phase == "task":
                facts = replace(
                    facts,
                    delivered_field_names=tuple(
                        sorted(set(facts.delivered_field_names) | pending_fields)
                    ),
                )
            else:
                facts = replace(
                    facts,
                    verified_field_names=tuple(
                        sorted(set(facts.verified_field_names) | pending_fields)
                    ),
                )
        if last is not None and last.intent == "login":
            # A pending login batch is evaluated against the freshly observed
            # post-action screen.  Its conditional next-step proposal must not
            # ask the matcher to reconcile or resubmit the credentials that
            # were already part of that batch.  The verifier still owns the
            # actual authentication decision and discards this proposal when
            # the login screen remains visible.
            facts = replace(
                facts,
                available_credential_sources=(),
                submitted_credentials=(),
            )
        if last is not None and facts.phase in {"duplicate_query", "final_query"}:
            if last.intent == "query_input":
                facts = replace(facts, query_value_verified=True)
            elif last.intent == "search" and last.screen_kind == "query":
                facts = replace(facts, query_submitted=True)
        if facts != actual.facts:
            proposal = DecisionPolicy(facts)
    query_pending = any(d and d.intent == "query_input" for d in decisions)
    proposal_prompt = proposal.as_prompt()
    contract = {
        "checkpoint": proposal.current_step,
        "allowed_intents": sorted({c["intent"] for c in proposal_prompt["allowed_next_choices"]}),
        "guidance": phase_instruction(proposal),
        "last_delivered_actions": _delivered_actions(pending),
        "conditional": proposal is not actual,
        "final_required_fields": (sorted(request.values) if request.phase == "final_query" else []),
    }
    contract["remaining_inputs" if actual.facts.phase == "task" else "remaining_fields"] = (
        proposal_prompt["remaining_fields"]
    )
    if actual.facts.phase != "task":
        # This is a contract for the legacy duplicate/final-query workflow.
        # A generic task decides whether a visible input is a query, editor,
        # filter, or something else from the user instruction and screen.
        contract["query_input"] = query_input_requirement(
            actual.facts.phase, actual.current_step, query_pending
        )
    proof = request.saved_record_review
    if proof is not None:
        # The model needs review progress, not run-local provenance.  UUIDs,
        # observation/capture IDs, paths and refs made an otherwise identical
        # verified screen produce a different persistent visual-cache key.
        contract["saved_record_review"] = {
            key: proof[key]
            for key in (
                "identity_field",
                "record_id",
                "query_row_seen",
                "confirmed_fields",
                "missing_fields",
                "conflict",
                "detail_identity_observed",
            )
        }
        contract["guidance"] = "Saved-record review context."
    if proposal is not actual:
        contract["condition"] = "Pending action outcome."
    if request.phase == "save_result":
        contract["guidance"] = SAVE_REVIEW_GUIDANCE
        contract["save_review"] = {
            "allowed_intents": {k: sorted(v) for k, v in SAVE_RESULT_ACTIONS.items()},
            "schema_mapping": {
                "result": "save_result",
                "evidence_refs": "message_targets",
                "next_decision": "steps",
            },
        }
    return contract


def query_input_requirement(phase: str, checkpoint: str, pending: bool) -> dict:
    return {
        "pending": pending,
        "required": phase in {"duplicate_query", "final_query"}
        and checkpoint == "query_input"
        and not pending,
    }
