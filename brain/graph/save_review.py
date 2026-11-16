"""Review saves for the fixed ShipmentRecord workflow without user task instructions."""

from typing import Literal

from pydantic import Field, model_validator
from sawon_interfaces import Observation

from .execution import GuardStop
from .schemas import DecisionResponse, FieldName, StrictModel

SAVE_RESULT_ACTIONS = {
    "rejected": {"navigate", "cancel", "wait"},
    "confirmation_required": {"confirm_save"},
    "ready_for_query": {"navigate", "select_tab", "query_input", "wait"},
    "unknown": set(),
}


SAVE_REVIEW_GUIDANCE = """Save result response:
result: rejected, confirmation_required, ready_for_query, or unknown.
evidence_refs: observed message/control references.
repair_fields: record fields identified in a save error.
next_decision: the next workflow decision.
"""


class SaveReviewResponse(StrictModel):
    result: Literal["rejected", "confirmation_required", "ready_for_query", "unknown"]
    evidence_refs: list[str] = Field(max_length=50)
    repair_fields: list[FieldName] = Field(max_length=100)
    next_decision: DecisionResponse

    @model_validator(mode="after")
    def consistent(self):
        if self.result != "unknown" and not self.evidence_refs:
            raise ValueError("save result needs current evidence")
        if self.result != "rejected" and self.repair_fields:
            raise ValueError("only rejection can invalidate fields")
        if len(set(self.repair_fields)) != len(self.repair_fields):
            raise ValueError("duplicate repair field")
        decision = self.next_decision.to_decision()
        if decision.operation == "stop":
            return self
        if decision.operation != "act" or decision.intent not in SAVE_RESULT_ACTIONS[self.result]:
            raise ValueError("next decision conflicts with save result")
        return self


def validate_save_review(review: SaveReviewResponse, observation: Observation) -> None:
    by_ref = {e.ref: e for e in observation.elements}
    if any(ref not in by_ref for ref in review.evidence_refs):
        raise GuardStop("save_result_evidence_not_current")
    if review.evidence_refs and not any(
        (by_ref[ref].name or by_ref[ref].value or by_ref[ref].label_hint)
        for ref in review.evidence_refs
    ):
        raise GuardStop("save_result_evidence_has_no_content")
