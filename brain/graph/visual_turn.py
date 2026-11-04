"""Bind screen-match marker references to the existing execution contracts."""

from pydantic import BaseModel

from .screen_batch import ScreenFillPlan


def adapt_proposal(proposal: dict, schema: type[BaseModel]) -> BaseModel:
    """Use the same post-verification schema as ordinary model selection."""
    if "fields" in proposal and not proposal["fields"] and proposal.get("next_decision"):
        proposal = proposal["next_decision"]
    if schema is ScreenFillPlan and "decision" in proposal:
        proposal = {"fields": [], "next_decision": proposal}
    elif "decision" in schema.model_fields and "next_decision" in proposal:
        if proposal.get("fields"):
            raise ValueError("visual_proposal_fields_still_pending")
        proposal = proposal["next_decision"]
    return schema.model_validate(proposal)
