"""Model outputs express intent and grounded evidence, never direct wire actions."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    ref: str | None = Field(
        default=None,
        description=(
            "Current enabled target ref; required for every action except wait/focus_window."
        ),
    )
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

    @model_validator(mode="after")
    def require_action_target(self) -> "Choice":
        if self.type not in {"wait", "focus_window"} and (not self.ref or not self.ref.strip()):
            raise ValueError("A targeted action requires a current element ref")
        if self.type in {"set_text", "type_text"} and self.source is None:
            raise ValueError("A text action requires a record or credential source")
        return self


class Decision(StrictModel):
    screen_kind: Literal["login", "menu", "query", "editor", "partner", "dialog", "unknown"]
    operation: Literal["act", "duplicate_clear", "duplicate_found", "complete", "stop"] = Field(
        description=(
            "act executes one UI action with an executable intent; complete submits final "
            "query grid/row/field proof with intent=verify and action=null. Duplicate "
            "evidence operations also use intent=verify and action=null. stop ends work."
        )
    )
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
    ] = Field(
        description=(
            "Intent of this one action, not the overall subgoal: credential for typing "
            "username/password; login for activating login; query_input for typing the "
            "query; search for activating search; fill for record text; navigate for "
            "screen movement; select_tab for tabs; save/confirm_save for activation; "
            "wait for waiting. verify/stop are only for non-action operations."
        )
    )
    subgoal: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1000)
    action: Choice | None = None
    grid_ref: str | None = None
    row_ref: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_consistent_operation(self) -> "Decision":
        if self.screen_kind == "unknown":
            return self  # The runtime always stops on an unknown window.
        if self.operation == "act":
            if self.action is None or self.intent in {"verify", "stop"}:
                raise ValueError("An action requires an executable intent and one action")
        elif self.action is not None:
            raise ValueError("Evidence and stop decisions cannot include UI actions")
        elif self.intent != ("stop" if self.operation == "stop" else "verify"):
            raise ValueError("Non-action decision intent must match its operation")
        return self


class FinalQueryPendingDecision(Decision):
    """Fresh query input and search must occur before final proof is eligible."""

    operation: Literal["act", "stop"] = Field(
        description=(
            "Final query prerequisites are pending. Perform the next required UI step "
            "with act, or stop. Completion evidence cannot be submitted yet."
        )
    )


class DuplicateDecision(Decision):
    """Evidence-only checkpoint; a search cannot be followed by a UI action."""

    screen_kind: Literal["query", "unknown"]
    operation: Literal["duplicate_clear", "duplicate_found", "stop"]
    intent: Literal["verify", "stop"]
    action: None = None
    grid_ref: str | None = Field(
        default=None,
        description="Current result grid ref; required for either duplicate decision.",
    )
    row_ref: None = None
    evidence: list[Evidence] = Field(default_factory=list, max_length=0)

    @model_validator(mode="after")
    def require_duplicate_evidence(self) -> "DuplicateDecision":
        if self.operation != "stop" and self.screen_kind != "unknown":
            if self.intent != "verify" or not self.grid_ref or not self.grid_ref.strip():
                raise ValueError("Duplicate verification requires a current result grid ref")
        return self


# Provider-facing contracts use explicit alternatives. Python model validators
# cannot encode cross-field requirements in the JSON schema sent to the model.
# The internal Decision/Choice API and environment-dependent guards stay intact.
NonemptyRef = Annotated[str, Field(min_length=1, pattern=r"\S")]
TextSource = FieldName | Literal["credential.username", "credential.password"]
ScreenKind = Literal["login", "menu", "query", "editor", "partner", "dialog", "unknown"]
ExecutableIntent = Literal[
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
]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Use singleton enums for constants at the measured provider boundary.

        Gemini documents enum support, while const is not in its documented
        subset. Keep the exported/fingerprinted schema equivalent to local
        Literal validation without relying on the provider enforcing const.
        """
        schema = super().model_json_schema(*args, **kwargs)

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                if "const" in node:
                    node["enum"] = [node.pop("const")]
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        visit(schema)
        return schema


class ClickChoice(WireModel):
    type: Literal["click", "double_click"]
    ref: NonemptyRef


class TextChoice(WireModel):
    type: Literal["set_text", "type_text"]
    ref: NonemptyRef
    source: TextSource


class KeyChoice(WireModel):
    type: Literal["key"]
    ref: NonemptyRef
    keys: Literal["{ENTER}", "{TAB}", "+{TAB}", "{ESC}", "{DOWN}", "{UP}", "{SPACE}"]


class SourceOptionChoice(WireModel):
    type: Literal["select_option"]
    ref: NonemptyRef
    source: FieldName


class NamedOptionChoice(WireModel):
    type: Literal["select_option"]
    ref: NonemptyRef
    option: Annotated[str, Field(min_length=1, pattern=r"\S")]


class FocusChoice(WireModel):
    type: Literal["focus_window"]
    handle: int


class WaitChoice(WireModel):
    type: Literal["wait"]
    duration_ms: int = Field(default=250, ge=0, le=3000)


WireChoice = (
    ClickChoice
    | TextChoice
    | KeyChoice
    | SourceOptionChoice
    | NamedOptionChoice
    | FocusChoice
    | WaitChoice
)


class WireEvidence(WireModel):
    field: FieldName
    ref: NonemptyRef


class WireDecisionBase(WireModel):
    screen_kind: ScreenKind
    subgoal: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1000)


class WireActDecision(WireDecisionBase):
    operation: Literal["act"]
    intent: ExecutableIntent
    action: WireChoice
    evidence: list[WireEvidence] = Field(default_factory=list)


class WireStopDecision(WireDecisionBase):
    operation: Literal["stop"]
    intent: Literal["stop"]
    action: None = None
    evidence: list[WireEvidence] = Field(default_factory=list, max_length=0)


class WireDuplicateDecision(WireDecisionBase):
    screen_kind: Literal["query"]
    operation: Literal["duplicate_clear", "duplicate_found"]
    intent: Literal["verify"]
    action: None = None
    grid_ref: NonemptyRef = Field(
        description=(
            "String ref of the CURRENT observed result table control, required even "
            "when it contains zero rows. Empty results do not mean a missing grid."
        )
    )
    evidence: list[WireEvidence] = Field(default_factory=list, max_length=0)


class WireCompleteDecision(WireDecisionBase):
    screen_kind: Literal["query"]
    operation: Literal["complete"]
    intent: Literal["verify"]
    action: None = None
    grid_ref: NonemptyRef = Field(description="String ref of the CURRENT observed result table.")
    row_ref: NonemptyRef = Field(description="String ref of the matching row within that table.")
    evidence: list[WireEvidence]


class DecisionResponse(WireModel):
    """Top-level object with provider-visible operation/intent/action alternatives."""

    decision: WireActDecision | WireStopDecision | WireDuplicateDecision | WireCompleteDecision

    def to_decision(self) -> Decision:
        return Decision.model_validate(self.decision.model_dump(mode="python"))


class DuplicateResponse(DecisionResponse):
    decision: WireDuplicateDecision | WireStopDecision


class FinalQueryPendingResponse(DecisionResponse):
    decision: WireActDecision | WireStopDecision


class FinalQueryResponse(DecisionResponse):
    decision: WireActDecision | WireCompleteDecision | WireStopDecision
