"""Model outputs express intent and grounded evidence, never direct wire actions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FieldName = Literal[
    "hbl_no",
    "mbl_no",
    "shipper",
    "consignee",
    "description",
    "packages.count",
    "packages.unit",
    "gross_weight_kg",
    "container_no",
    "pol",
    "pod",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Plan(StrictModel):
    subgoals: list[str] = Field(min_length=3, max_length=12)


class Evidence(StrictModel):
    field: FieldName
    ref: str


class Choice(StrictModel):
    type: Literal[
        "click",
        "double_click",
        "set_text",
        "type_text",
        "key",
        "select_option",
        "focus_window",
        "wait",
    ]
    ref: str | None = None
    source: (
        Literal[
            "hbl_no",
            "mbl_no",
            "shipper",
            "consignee",
            "description",
            "packages.count",
            "packages.unit",
            "gross_weight_kg",
            "container_no",
            "pol",
            "pod",
            "credential.username",
            "credential.password",
        ]
        | None
    ) = None
    keys: str | None = None
    option: str | None = None
    handle: int | None = None
    duration_ms: int = Field(default=250, ge=0, le=3000)


class Decision(StrictModel):
    screen_kind: Literal["login", "menu", "query", "editor", "partner", "dialog", "unknown"]
    operation: Literal["act", "duplicate_clear", "duplicate_found", "complete", "stop"]
    intent: Literal[
        "credential",
        "login",
        "navigate",
        "query_input",
        "search",
        "fill",
        "save",
        "confirm_save",
        "select_tab",
        "select_option",
        "cancel",
        "wait",
        "verify",
        "stop",
    ]
    subgoal: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1000)
    action: Choice | None = None
    grid_ref: str | None = None
    row_ref: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
