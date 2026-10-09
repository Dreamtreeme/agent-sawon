"""Shared worker execution context, independent of provider and perception adapter.

Pending outcomes describe a conditional proposal, never mutate verified runtime facts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .execution import PendingAction
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

FINAL_QUERY_INPUT_GUIDANCE = (
    "CURRENT STEP: Fresh query input is required. Saving ended record entry. "
    "Do not fill record fields or save again. If a save confirmation is visible, "
    "handle it by meaning; otherwise use CURRENT observed navigation to reach a query "
    "screen. Choose navigation by each control's observed role and meaning. Window-"
    "management menus are not evidence of a business search route; consider observed "
    "back/close controls when leaving a completed document window. "
    "Set its observed query input from hbl_no with operation=act, "
    "intent=query_input and action.type=set_text only when execution_policy reports "
    "that the CURRENT observed value is not already verified. Do not submit final "
    "evidence at this step."
)
FINAL_QUERY_SEARCH_GUIDANCE = (
    "CURRENT STEP ONLY: The query value has already been entered and verified, "
    "but the search has NOT been executed. Execute that search now using a CURRENT "
    "observed enabled search control identified by its meaning. Return operation=act, "
    "intent=search and one click or Enter action with its current ref. Do not submit "
    "verification evidence yet. Existing visible rows do not fulfill the explicit "
    "search step. If no appropriate control can be identified, stop with intent=stop "
    "and action=null. Do not fill or save any record field."
)
FINAL_QUERY_EVIDENCE_GUIDANCE = (
    "CURRENT STEP: Fresh query input and search are verified. Submit final proof with "
    "operation=complete, intent=verify, action=null, screen_kind=query, CURRENT grid_ref, "
    "row_ref and evidence for every final_required_field from the SAME visible query "
    "row. If columns are hidden, first use operation=act, intent=navigate and a CURRENT "
    "observed maximize/navigation control, then reobserve. Never substitute earlier "
    "editor values or remembered refs for current query cells. Do not fill or save."
)
DUPLICATE_EVIDENCE_GUIDANCE = (
    "CURRENT STEP: Stay on the query screen. Return duplicate_clear for an empty "
    "visible result grid or duplicate_found for a matching record, intent=verify, "
    "action=null and the CURRENT result grid_ref. grid_ref is the nonempty string ref "
    "of the observed result table control, EVEN WHEN it contains zero rows. An empty "
    "table still has a control ref: do not return null or a row ref for grid_ref. "
    "If that table cannot be identified in this observation, return stop/stop instead "
    "of claiming duplicate clearance. Do not navigate or enter data until "
    "the runtime accepts this evidence. Never infer a grid ref from a prior observation."
)


def phase_instruction(policy: DecisionPolicy) -> str:
    step = policy.current_step
    if step == "task":
        return (
            "Use the user task description as the workflow. Choose only grounded CURRENT-screen "
            "actions and supplied input keys. No application route is predefined. Verify visible "
            "input values after entry, observe each screen boundary, and return completion only "
            "when current evidence supports an applicable user completion condition."
        )
    if step == "save_result":
        return SAVE_REVIEW_GUIDANCE
    if step == "duplicate_result_evidence":
        return DUPLICATE_EVIDENCE_GUIDANCE
    if policy.facts.phase == "final_query":
        if step == "final_row_evidence" and not policy.facts.final_evidence_ready:
            return (
                "CURRENT STEP: Current query evidence is incomplete. Completion is not "
                "an allowed response. evidence_review identifies the missing current "
                "fields or row identifier. Choose an observed window expansion or other "
                "navigation control to reveal the required cells together, then reobserve. "
                "Do not replace row cells with query inputs or reuse earlier refs. "
                "Do not enter or save the record again. Stop if no grounded route exists."
            )
        return {
            "query_input": FINAL_QUERY_INPUT_GUIDANCE,
            "search": FINAL_QUERY_SEARCH_GUIDANCE,
            "final_row_evidence": FINAL_QUERY_EVIDENCE_GUIDANCE,
        }[step]
    if policy.facts.phase == "enter":
        if step == "save":
            return (
                "CURRENT STEP: Every record field has verified editor evidence. "
                "Activate the CURRENT observed save command once. Do not re-enter "
                "verified fields. Delivery will be followed by fresh query verification."
            )
        return (
            "CURRENT PHASE: Duplicate clearance is accepted. Enter and verify ALL "
            "record fields, including both packages components. Use observed tabs "
            "and partner dialogs to reach missing fields. remaining_fields lists "
            "unverified fields; do not repeat verified ones or save before this "
            "list is empty. evidence may map CURRENT editor controls to fields. "
            "To submit existing field evidence without another edit, use an allowed "
            "wait action with that evidence; save becomes available on the next decision."
        )
    submitted = policy.facts.submitted_credentials
    if submitted:
        remaining = [source for source in policy.facts.remaining_credentials]
        if remaining:
            return (
                "CURRENT STEP: Input has already been submitted for "
                + ", ".join(submitted)
                + ". Do not type those credentials again. Submit only the remaining "
                "available credential source(s): " + ", ".join(remaining) + ". "
                "Protected password values remain unreadable after input."
            )
        return (
            "CURRENT STEP: Available credential inputs have already been submitted "
            "to the current login controls. Do not type them again; a password field "
            "remains unreadable even after successful input delivery. Activate the "
            "CURRENT observed login command with operation=act, intent=login and a "
            "click or Enter action. Authentication is not yet verified; observe the "
            "actual result. If the screen shows an authentication error, stop."
        )
    if step == "search":
        return (
            "CURRENT STEP: The duplicate query value is verified. Activate its "
            "CURRENT observed search control with operation=act, intent=search. "
            "Do not navigate to entry before the runtime accepts duplicate evidence."
        )
    return (
        "CURRENT STEP: Check for an existing record before entry. Log in if needed "
        "using opaque credential sources, and reach a query screen by observed "
        "navigation. Set its query input from hbl_no with operation=act, "
        "intent=query_input, action.type=set_text only when the CURRENT observed value "
        "is not already verified. The runtime must observe the matching value before search."
    )


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
            anticipated_fields = set(facts.verified_field_names) | pending_fields
            if anticipated_fields != set(facts.verified_field_names):
                facts = replace(
                    facts,
                    verified_field_names=tuple(sorted(anticipated_fields)),
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
        "pending_actions": [
            {"intent": d.intent, "source": d.action.source if d.action else None}
            for d in decisions
            if d
        ],
        "conditional": proposal is not actual,
        "final_required_fields": (
            sorted(request.values) if request.phase == "final_query" else []
        ),
    }
    if actual.facts.remaining_credentials:
        contract["allowed_action_batches"] = [
            {
                "kind": "login",
                "credential_sources": list(actual.facts.remaining_credentials),
                "final_intent": "login",
                "atomic_screen_boundary": True,
            }
        ]
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
        contract["guidance"] = (
            "Verify the saved record using CURRENT query-row and linked detail-screen values. "
            "Identify the matching query row, then the same record in its detail form. "
            "Map visible input values to field evidence, including "
            f"{request.saved_identity_field} "
            "when visible. "
            "Use observed navigation/tabs to expose missing values. Do not fill, save, transmit "
            "or approve. Code joins validated observations and determines completion. "
            "Truncated values and the original record are not proof of persisted values."
        )
    if proposal is not actual:
        contract["condition"] = (
            "ONLY after code verifies the pending action from the CURRENT screen. "
            "A failed verification discards this proposal. These are not completed facts."
        )
    if request.phase == "save_result":
        contract["save_review"] = {
            "allowed_intents": {k: sorted(v) for k, v in SAVE_RESULT_ACTIONS.items()},
            "instructions": SAVE_REVIEW_GUIDANCE,
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
        "instruction": "On a query screen, write its identifier only when required=true. "
        "If pending=true, retain CURRENT input/value evidence and propose search only if "
        "that value matches. Code verifies it before the next action; repair a mismatch. "
        "Already verified inputs must not be filled again.",
    }
