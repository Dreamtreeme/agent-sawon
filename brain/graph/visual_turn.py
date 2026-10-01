"""Bind screen-match marker references to the existing execution contracts."""

from pydantic import BaseModel

from .screen_batch import LoginScreenPlan, ScreenFillPlan


def bind_proposal(proposal: BaseModel, prefix: str, allowed_refs: set[str]) -> dict:
    """Translate marker references only; record sources and other text stay literal."""

    def ref(value):
        if value is None:
            return None
        if not isinstance(value, str) or not value.startswith("m") or not value[1:].isdigit():
            raise ValueError("visual_proposal_invalid_marker")
        result = prefix + str(int(value[1:]))
        if result not in allowed_refs:
            raise ValueError("visual_proposal_unknown_marker")
        return result

    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, dict):
            return {
                key: ref(item)
                if key in {"ref", "grid_ref", "row_ref", "login_ref"}
                else [ref(entry) for entry in item]
                if key == "evidence_refs"
                else visit(item)
                for key, item in value.items()
            }
        return value

    return visit(proposal.model_dump(mode="json"))


def adapt_proposal(proposal: dict, schema: type[BaseModel]) -> BaseModel:
    """Use the same post-verification schema as ordinary model selection."""
    if "fields" in proposal and not proposal["fields"] and proposal.get("next_decision"):
        proposal = proposal["next_decision"]
    if schema in {LoginScreenPlan, ScreenFillPlan} and "decision" in proposal:
        proposal = {"fields": [], "next_decision": proposal}
        if schema is LoginScreenPlan:
            proposal["login_ref"] = None
    elif "decision" in schema.model_fields and "next_decision" in proposal:
        if proposal.get("fields"):
            raise ValueError("visual_proposal_fields_still_pending")
        proposal = proposal["next_decision"]
    return schema.model_validate(proposal)
