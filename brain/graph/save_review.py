"""Ground the next step after a save attempt without claiming persistence."""

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


SAVE_REVIEW_GUIDANCE = """
SAVE RESULT REVIEW: the last command was delivered, but saving is NOT proven.
Read CURRENT visible messages and controls, not the previous action's intention.
Return result=rejected for a validation/error message, confirmation_required for
a save confirmation awaiting action, ready_for_query when the current screen
permits verification of the attempted save without a visible error/confirmation,
or unknown if you cannot tell. Cite CURRENT evidence_refs supporting your result.
A generic OK button alone does not prove successful saving.
For rejection, repair_fields names ONLY flat ShipmentRecord fields explicitly
implicated by the error. An optional/detail-row error is not a record-field error:
use repair_fields=[] and stop instead of inventing detail values or repeating save.
Choose next_decision in the SAME response, avoiding another planning call:
- rejected: dismiss the error by meaning (navigate/cancel/wait), or stop. The runtime
  returns to entry and un-verifies only repair_fields; field entry is replanned later.
- confirmation_required: confirm_save on a current confirmation control, or stop.
- ready_for_query: dismiss a completed-save notice using navigate, navigate toward the query,
  enter its identifier, or stop. confirm_save is only for a pending save confirmation,
  not acknowledgement of a message reporting that saving already happened.
The pending-state policy is superseded ONLY after runtime validation of this review.
Do not claim completion, refill data, submit search before query input is verified,
or infer persistence from a delivered click. Fresh final query proof is still required.
"""


class SaveReviewResponse(StrictModel):
    result: Literal["rejected", "confirmation_required", "ready_for_query", "unknown"]
    evidence_refs: list[str] = Field(max_length=12)
    repair_fields: list[FieldName] = Field(max_length=11)
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
