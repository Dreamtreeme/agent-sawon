"""Context Recall reads this run's progress; it never selects or executes an action."""

from __future__ import annotations

from typing import TYPE_CHECKING

from brain.input_binding import MODEL_INPUTS_NAME
from brain.visual.marker_contract import model_screen

if TYPE_CHECKING:
    from .screen_decision import ScreenDecisionRequest


def recall_context(request: ScreenDecisionRequest) -> dict:
    """Restore current history, evidence and unfinished work without changing state."""
    contract = request.execution_contract
    required = request.required_verification
    pending_verification_fields = sorted(
        {
            item["source"]
            for item in contract["last_delivered_actions"]
            if item["intent"] == "fill" and item["source"] in request.values
        }
    )
    # Generic input keys are reusable across screens. Keep action eligibility
    # broad while exposing a separate, model-owned coverage ledger. Calling
    # every source "remaining" forever made matching values look unfinished
    # and obscured the few fields that had never been observed.
    available_inputs = sorted(request.values)
    remaining_inputs = sorted(set(request.values) - request.verified_fields)
    contract = {
        **contract,
        "available_inputs": available_inputs,
    }
    record = dict(request.values)
    context_layers = request.context_layers

    notebook = {
        "phase": request.phase,
        "checkpoint": request.policy.current_step,
        "awaiting_visual_result_review": request.pending_verification,
        "last_delivered_actions": contract["last_delivered_actions"],
        # 누적 raw feedback 대신 정제된 스크래치패드와 최근 N턴 버퍼를 주입한다.
        "scratchpad": context_layers["scratchpad"],
        "recent_turns": context_layers["recent_turns_buffer"],
        "recent_feedback": list(request.recent_feedback),
        "required_verification": required,
        "available_credentials": list(request.available_credentials),
        "observed_inputs": dict(request.observed_inputs),
    }
    if request.phase == "task":
        notebook.update(
            {
                "verified_inputs": sorted(request.verified_fields),
                "evidence_scope": (
                    "saved_detail"
                    if request.workflow_flags.get("post_save_detail_review")
                    else "saved_detail_candidate"
                    if request.workflow_flags.get("saved_result_open_pending")
                    else "entry"
                ),
                "evidence_claims": list(request.evidence_claims),
                "failed_transitions": list(request.failed_transitions),
                "anticipated_verified_inputs": (
                    sorted(set(request.values) - set(contract["remaining_inputs"]))
                    if contract["conditional"]
                    else []
                ),
                "remaining_inputs": remaining_inputs,
                "available_inputs": available_inputs,
                "pending_verification_inputs": pending_verification_fields,
                MODEL_INPUTS_NAME: record,
                "workflow_state": {
                    key: value
                    for key, value in request.workflow_flags.items()
                    if key not in {"duplicate_clear", "query_value_verified", "query_submitted"}
                },
            }
        )
        if request.saved_record_review is not None:
            # This is workflow state, not a second value judge.  It tells the
            # planner whether a post-save query has actually produced a result;
            # otherwise an editor left open after Save can be mistaken for the
            # required re-queried record.
            notebook["saved_record_review"] = request.saved_record_review
    else:
        notebook.update(
            {
                "verified_fields": sorted(request.verified_fields),
                "anticipated_verified_fields": (
                    sorted(set(request.values) - set(contract["remaining_fields"]))
                    if contract["conditional"]
                    else []
                ),
                "remaining_fields": remaining_inputs,
                "pending_verification_fields": pending_verification_fields,
                **request.workflow_flags,
                "record": record,
            }
        )
    state = {
        "notebook": notebook,
        "screen": model_screen(request.screen),
        "execution": contract,
    }
    if request.post_action_review:
        state["post_action_review"] = {
            "mode": "field_evidence_and_next_action",
            "delivered_sources": pending_verification_fields,
        }
    # User education is the semantic source of truth for field-to-input bindings.
    # Omitting it left the model with input keys and pixels but no company-specific
    # explanation of what each visible field means.
    # The focused procedure window below already carries the applicable user
    # teaching. Repeating every distant step here wastes context and lets stale
    # routes compete with the current screen.
    state["task"] = {
        **request.task_instruction.model_dump(mode="json", exclude={"procedure"}),
        "procedure": [],
    }
    # The digest is the user-education contract boundary.  Two companies can
    # show identical pixels, and one company's memo can later be revised
    # without changing its display name.  Neither case may reuse a decision
    # compiled under another education file.
    state["task_contract_sha256"] = request.task_instruction_sha256
    state["taught_procedure_progress"] = request.taught_procedure_progress
    return state
