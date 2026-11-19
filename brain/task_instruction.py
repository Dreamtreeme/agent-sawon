"""User-authored task intent and input data without application routes or coordinates."""

from collections import Counter
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

COLLECTED_INPUT_PREFIX = "collected_result."


def _validate_input_keys(keys: Iterable[str]) -> None:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    for key in keys:
        if (
            not key
            or len(key) > 120
            or key.startswith("credential.")
            or any(character not in allowed for character in key)
        ):
            raise ValueError("task_input_key_invalid")


class ObservedStepResult(BaseModel):
    """Taught outcome from this run's accumulated evidence and current observation."""

    model_config = ConfigDict(extra="forbid")
    step_id: str
    observed_result: str
    completed: bool


class TaughtProcedureAction(BaseModel):
    """A user's semantic UI instruction without transient geometry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verb: Literal["activate", "fill", "navigate", "verify"]
    target: str = Field(min_length=1, max_length=200)
    input_key: str | None = Field(default=None, min_length=1, max_length=120)
    surface: Literal["direct_input", "table_cell", "any"] | None = None

    @model_validator(mode="after")
    def input_binding_matches_verb(self):
        if self.verb == "fill" and self.input_key is None:
            raise ValueError("taught_fill_requires_input_key")
        if self.input_key is not None:
            allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
            if any(character not in allowed for character in self.input_key):
                raise ValueError("taught_action_input_key_invalid")
        return self


class TaughtProcedureStep(BaseModel):
    """One user-taught program step, independent of transient coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    instruction: str = Field(min_length=1, max_length=500)
    completion: str = Field(min_length=1, max_length=500)
    actions: list[TaughtProcedureAction] = Field(default_factory=list, max_length=30)


class TaskInputFormat(BaseModel):
    """Numeric destination format for one exact execution input key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["number", "integer"]
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    count_unit_from: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_count_unit(self):
        if self.count_unit_from is not None:
            _validate_input_keys([self.count_unit_from])
            if self.type != "integer" or self.unit is not None:
                raise ValueError("count_unit_from requires an integer without a mass unit")
        return self


class TaskInstruction(BaseModel):
    """Describe desired work and an optional user-taught program procedure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "0.1"
    name: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=1000)
    identity_input_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    requirements: list[str] = Field(default_factory=list, max_length=20)
    completion_conditions: list[str] = Field(min_length=1, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    procedure: list[TaughtProcedureStep] = Field(default_factory=list, max_length=40)
    result_keys: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="Business fact keys to read from screens and export; not supplied input values",
    )
    input_formats: dict[str, TaskInputFormat] = Field(default_factory=dict)

    def input_targets(self) -> dict[str, list[str]]:
        """Taught screen labels for each write source and the record's identity."""
        targets: dict[str, list[str]] = {}
        for step in self.procedure:
            for action in step.actions:
                key = action.input_key
                if action.verb == "fill" and key:
                    names = targets.setdefault(key, [])
                    if action.target not in names:
                        names.append(action.target)
        if self.identity_input_key:
            targets.setdefault(self.identity_input_key, [])
        return targets

    @model_validator(mode="after")
    def unique_nonempty_items(self):
        for attribute in ("requirements", "completion_conditions", "constraints", "result_keys"):
            items = getattr(self, attribute)
            normalized = [item.strip() for item in items]
            if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
                raise ValueError("task_instruction_items_must_be_unique_and_nonempty")
        _validate_input_keys(self.result_keys)
        _validate_input_keys(self.input_formats)
        step_ids = [step.id for step in self.procedure]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("task_instruction_procedure_step_ids_must_be_unique")
        return self


class ReceiptFactSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fact_key: str
    receipt_id: int = Field(gt=0)
    source: str


class ReceiptUnitReference(ReceiptFactSource):
    plan_id: int = Field(gt=0)
    value: str = Field(min_length=1)


class ReceiptInputSource(ReceiptFactSource):
    memo_label: str | None = None
    unit_reference: ReceiptUnitReference | None = None


class PlanInputProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan_id: int = Field(gt=0)
    work_id: str
    inputs: dict[str, ReceiptInputSource]


class TaskInputRecord(BaseModel):
    """Arbitrary business input keys supplied by the user or an upstream extractor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "0.1"
    case_id: str = Field(min_length=1, max_length=200)
    inputs: dict[
        str,
        str,
    ] = Field(max_length=200)
    origin: Literal["manual", "inbox_plan"] = "manual"
    provenance: PlanInputProvenance | None = None

    def memo_labels(self, task: TaskInstruction) -> dict[str, str]:
        """Human item names for the exact inputs and receipts selected by the central plan."""
        if self.origin != "inbox_plan" or self.provenance is None:
            return {}
        targets = task.input_targets()
        labels = {}
        for key in sorted(self.inputs):
            names = targets.get(key, [])
            fallback = "업무 식별 정보" if key == task.identity_input_key else "업무 항목"
            labels[key] = (self.provenance.inputs[key].memo_label or "").strip() or (
                names[0] if names else fallback
            )
        counts = Counter(labels.values())
        for key, names in targets.items():
            reading = key.startswith(COLLECTED_INPUT_PREFIX) and (
                key.removeprefix(COLLECTED_INPUT_PREFIX) in task.result_keys
            )
            if key not in labels and names and (key.startswith("credential.") or reading):
                labels[key] = names[0]
        unique = {}
        used = set()
        for key, name in labels.items():
            if key in self.inputs and counts[name] > 1 and targets.get(key):
                name = f"{name} · {targets[key][0]}"
            candidate = name
            ordinal = 1
            while candidate in used:
                ordinal += 1
                candidate = f"{name} ({ordinal})"
            unique[key] = candidate
            used.add(candidate)
        return unique

    @model_serializer(mode="wrap")
    def serialize(self, serialize_fields):
        fields = serialize_fields(self)
        if self.provenance is None:
            fields.pop("provenance", None)
        return fields

    @model_validator(mode="after")
    def safe_input_keys(self):
        _validate_input_keys(self.inputs)
        if self.origin == "inbox_plan" and (
            self.provenance is None or self.provenance.inputs.keys() != self.inputs.keys()
        ):
            raise ValueError("Inbox plan inputs require a receipt source for every field")
        return self
