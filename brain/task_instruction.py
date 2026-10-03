"""User-authored task intent and input data without application routes or coordinates."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaughtProcedureStep(BaseModel):
    """One user-taught program step, independent of transient coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    instruction: str = Field(min_length=1, max_length=500)
    completion: str = Field(min_length=1, max_length=500)


class TaskInstruction(BaseModel):
    """Describe desired work and an optional user-taught program procedure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "0.1"
    name: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=1000)
    requirements: list[str] = Field(default_factory=list, max_length=20)
    completion_conditions: list[str] = Field(min_length=1, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    procedure: list[TaughtProcedureStep] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def unique_nonempty_items(self):
        for attribute in ("requirements", "completion_conditions", "constraints"):
            items = getattr(self, attribute)
            normalized = [item.strip() for item in items]
            if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
                raise ValueError("task_instruction_items_must_be_unique_and_nonempty")
        step_ids = [step.id for step in self.procedure]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("task_instruction_procedure_step_ids_must_be_unique")
        return self


class TaskInputRecord(BaseModel):
    """Arbitrary business input keys supplied by the user or an upstream extractor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "0.1"
    case_id: str = Field(min_length=1, max_length=200)
    inputs: dict[
        str,
        str,
    ] = Field(min_length=1, max_length=200)
    origin: Literal["manual"] = "manual"

    @model_validator(mode="after")
    def safe_input_keys(self):
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
        for key in self.inputs:
            if (
                not key
                or len(key) > 120
                or key.startswith("credential.")
                or any(character not in allowed for character in key)
            ):
                raise ValueError("task_input_key_invalid")
        return self
