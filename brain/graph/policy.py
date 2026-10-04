"""Derive provider choices and runtime membership from the same verified facts.

This policy has no environment access. A screen kind is a model claim, not an
observation; the engine still grounds targets and validates observed effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache, reduce
from operator import or_
from typing import Any, Literal, get_args

from pydantic import ValidationError, create_model

from brain.graph.schemas import (
    ClickChoice,
    Decision,
    DecisionResponse,
    FieldName,
    FocusChoice,
    KeyChoice,
    NamedOptionChoice,
    SourceOptionChoice,
    TextChoice,
    WaitChoice,
    WireActDecision,
    WireCompleteDecision,
    WireDuplicateDecision,
    WireModel,
    WireStopDecision,
    WireTaskCompleteDecision,
)

Phase = Literal["task", "duplicate_query", "enter", "save_result", "final_query"]
CredentialSource = Literal["credential.username", "credential.password"]
CurrentStep = Literal[
    "task",
    "save_result",
    "query_input",
    "search",
    "duplicate_result_evidence",
    "fill_fields",
    "save",
    "final_row_evidence",
]

_SCREENS = ("login", "menu", "query", "editor", "partner", "dialog")
_NAVIGATION_KEYS = (
    "tab",
    "shift+tab",
    "esc",
    "down",
    "up",
    "space",
    "pagedown",
    "pageup",
    "home",
    "end",
)


@dataclass(frozen=True)
class ExecutionFacts:
    """Immutable, canonical names and booleans, never credentials or field values."""

    phase: Phase
    field_names: tuple[FieldName, ...] = ()
    verified_field_names: tuple[FieldName, ...] = ()
    available_credential_sources: tuple[CredentialSource, ...] = ()
    submitted_credentials: tuple[CredentialSource, ...] = ()
    query_value_verified: bool = False
    query_submitted: bool = False
    duplicate_clear: bool = False
    save_issued: bool = False
    final_evidence_ready: bool = False

    def __post_init__(self) -> None:
        if self.phase not in get_args(Phase):
            raise ValueError("Unknown execution phase")
        for attribute in ("field_names", "verified_field_names"):
            value = getattr(self, attribute)
            if (
                not isinstance(value, tuple)
                or any(not isinstance(item, str) or not item.strip() for item in value)
                or any(item.startswith("credential.") for item in value)
            ):
                raise ValueError("Execution facts contain unsupported names")
            object.__setattr__(self, attribute, tuple(sorted(set(value))))
        allowed_credentials = get_args(CredentialSource)
        for attribute in ("available_credential_sources", "submitted_credentials"):
            value = getattr(self, attribute)
            if not isinstance(value, tuple) or any(
                item not in allowed_credentials for item in value
            ):
                raise ValueError("Execution facts contain unsupported names")
            object.__setattr__(self, attribute, tuple(sorted(set(value))))
        if not set(self.verified_field_names).issubset(self.field_names):
            raise ValueError("Verified fields must belong to this task")
        if not set(self.submitted_credentials).issubset(self.available_credential_sources):
            raise ValueError("Submitted credential sources must be available")

    @property
    def remaining_fields(self) -> tuple[FieldName, ...]:
        return tuple(name for name in self.field_names if name not in self.verified_field_names)

    @property
    def final_query_workflow_ready(self) -> bool:
        """Observed input plus a fresh search permit result inspection."""
        return (
            self.phase == "final_query"
            and self.duplicate_clear
            and self.save_issued
            and self.query_submitted
            and self.query_value_verified
        )

    @property
    def remaining_credentials(self) -> tuple[CredentialSource, ...]:
        return tuple(
            name
            for name in self.available_credential_sources
            if name not in self.submitted_credentials
        )


@dataclass(frozen=True)
class _ActionRule:
    intent: str
    screens: tuple[str, ...]
    click_types: tuple[str, ...] = ()
    text_sources: tuple[str, ...] = ()
    keys: tuple[str, ...] = ()
    option_sources: tuple[str, ...] = ()
    named_option: bool = False
    focus: bool = False
    wait: bool = False

    def as_prompt(self) -> dict[str, Any]:
        actions: list[dict[str, Any]] = []
        if self.click_types:
            actions.append({"type": list(self.click_types), "requires": ["current_enabled_ref"]})
        if self.text_sources:
            actions.append({"type": ["set_text", "type_text"], "source": list(self.text_sources)})
        if self.keys:
            actions.append({"type": ["key"], "keys": list(self.keys)})
        if self.option_sources:
            actions.append({"type": ["select_option"], "source": list(self.option_sources)})
        if self.named_option:
            actions.append({"type": ["select_option"], "requires": ["observed_option_name"]})
        if self.focus:
            actions.append({"type": ["focus_window"], "requires": ["observed_window_handle"]})
        if self.wait:
            actions.append({"type": ["wait"], "duration_ms": {"minimum": 0, "maximum": 3000}})
        return {
            "operation": "act",
            "intent": self.intent,
            "screen_kind": list(self.screens),
            "action": actions,
        }


def _rules(facts: ExecutionFacts) -> tuple[_ActionRule, ...]:
    if facts.phase == "task":
        screens = _SCREENS
        work_screens = tuple(screen for screen in screens if screen != "login")
        rules = [
            _ActionRule(
                "navigate",
                screens,
                click_types=("click", "double_click"),
                keys=_NAVIGATION_KEYS,
                focus=True,
            ),
            _ActionRule("cancel", screens, click_types=("click",), keys=("esc",)),
            _ActionRule("wait", screens, wait=True),
            _ActionRule("select_tab", work_screens, click_types=("click",), keys=_NAVIGATION_KEYS),
        ]
        if facts.remaining_credentials:
            rules.append(
                _ActionRule("credential", ("login",), text_sources=facts.remaining_credentials)
            )
        elif facts.available_credential_sources:
            rules.append(_ActionRule("login", ("login",), click_types=("click",), keys=("enter",)))
        if facts.remaining_fields:
            rules.extend(
                [
                    _ActionRule("fill", work_screens, text_sources=facts.remaining_fields),
                    _ActionRule("search", work_screens, click_types=("click",), keys=("enter",)),
                    _ActionRule(
                        "select_option",
                        work_screens,
                        click_types=("click", "double_click"),
                        keys=("enter",),
                        option_sources=facts.remaining_fields,
                        named_option=True,
                    ),
                ]
            )
        if not facts.remaining_fields and not facts.save_issued:
            rules.append(_ActionRule("save", work_screens, click_types=("click",), keys=("enter",)))
        if facts.save_issued:
            rules.append(
                _ActionRule("confirm_save", ("dialog",), click_types=("click",), keys=("enter",))
            )
        return tuple(rules)
    if facts.phase == "duplicate_query" and facts.query_submitted:
        return ()  # The result must be examined before another action changes it.
    screens = tuple(
        name for name in _SCREENS if not (facts.phase == "duplicate_query" and name == "editor")
    )
    rules = [
        _ActionRule(
            "navigate",
            screens,
            click_types=("click", "double_click"),
            keys=_NAVIGATION_KEYS,
            focus=True,
        ),
        _ActionRule("cancel", screens, click_types=("click",), keys=("esc",)),
        _ActionRule("wait", screens, wait=True),
    ]
    if facts.remaining_credentials:
        rules.append(
            _ActionRule("credential", ("login",), text_sources=facts.remaining_credentials)
        )
    elif facts.available_credential_sources:
        rules.append(_ActionRule("login", ("login",), click_types=("click",), keys=("enter",)))
    if facts.phase in {"duplicate_query", "final_query"}:
        if not facts.query_value_verified and not facts.query_submitted:
            rules.append(_ActionRule("query_input", ("query",), text_sources=("hbl_no",)))
        elif facts.query_value_verified and not facts.query_submitted:
            rules.append(_ActionRule("search", ("query",), click_types=("click",), keys=("enter",)))
    if facts.phase in {"enter", "final_query"}:
        rules.append(
            _ActionRule(
                "select_tab",
                ("menu", "query", "editor", "partner", "dialog"),
                click_types=("click",),
                keys=_NAVIGATION_KEYS,
            )
        )
    if facts.phase == "enter" and facts.duplicate_clear and not facts.save_issued:
        if facts.remaining_fields:
            rules.extend(
                [
                    _ActionRule("fill", ("editor", "partner"), text_sources=facts.remaining_fields),
                    _ActionRule(
                        "select_option",
                        ("editor", "partner"),
                        click_types=("click", "double_click"),
                        keys=("enter",),
                        option_sources=facts.remaining_fields,
                        named_option=True,
                    ),
                    _ActionRule("search", ("partner",), click_types=("click",), keys=("enter",)),
                ]
            )
        elif facts.field_names:
            rules.append(_ActionRule("save", ("editor",), click_types=("click",), keys=("enter",)))
    if facts.save_issued:
        rules.append(
            _ActionRule("confirm_save", ("dialog",), click_types=("click",), keys=("enter",))
        )
    return tuple(rules)


def _literal(values: tuple[str, ...]) -> Any:
    return Literal[values]


def _action_model(rule: _ActionRule) -> Any:
    name = "Policy" + "".join(part.title() for part in rule.intent.split("_"))
    alternatives: list[type[WireModel]] = []
    if rule.click_types:
        alternatives.append(
            create_model(
                name + "Click", __base__=ClickChoice, type=(_literal(rule.click_types), ...)
            )
        )
    if rule.text_sources:
        alternatives.append(
            create_model(
                name + "Text", __base__=TextChoice, source=(_literal(rule.text_sources), ...)
            )
        )
    if rule.keys:
        alternatives.append(
            create_model(name + "Key", __base__=KeyChoice, keys=(_literal(rule.keys), ...))
        )
    if rule.option_sources:
        alternatives.append(
            create_model(
                name + "SourceOption",
                __base__=SourceOptionChoice,
                source=(_literal(rule.option_sources), ...),
            )
        )
    if rule.named_option:
        alternatives.append(NamedOptionChoice)
    if rule.focus:
        alternatives.append(FocusChoice)
    if rule.wait:
        alternatives.append(WaitChoice)
    return reduce(or_, alternatives)


@lru_cache(maxsize=128)
def _response_schema(
    rules: tuple[_ActionRule, ...],
    proof: Literal["duplicate", "complete", "task_complete"] | None,
) -> type[DecisionResponse]:
    alternatives: list[type[WireModel]] = [WireStopDecision]
    for rule in rules:
        alternatives.append(
            create_model(
                "Policy" + "".join(part.title() for part in rule.intent.split("_")) + "Decision",
                __base__=WireActDecision,
                screen_kind=(_literal(rule.screens), ...),
                intent=(_literal((rule.intent,)), ...),
                action=(_action_model(rule), ...),
            )
        )
    if proof == "duplicate":
        alternatives.append(WireDuplicateDecision)
    elif proof == "complete":
        alternatives.append(WireCompleteDecision)
    elif proof == "task_complete":
        alternatives.append(WireTaskCompleteDecision)
    return create_model(
        "PolicyDecisionResponse",
        __base__=DecisionResponse,
        decision=(reduce(or_, alternatives), ...),
    )


@dataclass(frozen=True)
class DecisionPolicy:
    facts: ExecutionFacts

    @property
    def current_step(self) -> CurrentStep:
        if self.facts.phase == "task":
            return "task"
        if self.facts.phase == "save_result":
            return "save_result"
        if self.facts.phase == "enter":
            return "fill_fields" if self.facts.remaining_fields else "save"
        if self.facts.phase == "duplicate_query" and self.facts.query_submitted:
            return "duplicate_result_evidence"
        if not self.facts.query_value_verified:
            return "query_input"
        return "final_row_evidence" if self.facts.query_submitted else "search"

    @property
    def _proof(self) -> Literal["duplicate", "complete", "task_complete"] | None:
        if self.facts.phase == "task":
            return "task_complete"
        if (
            self.facts.phase == "duplicate_query"
            and self.facts.query_submitted
            and self.facts.query_value_verified
        ):
            return "duplicate"
        if self.facts.final_query_workflow_ready and self.facts.final_evidence_ready:
            return "complete"
        return None

    def response_schema(self) -> type[DecisionResponse]:
        return _response_schema(_rules(self.facts), self._proof)

    def allows(self, decision: Decision) -> bool:
        """Validate the same provider-visible combinations, without adding new rules."""
        payload = decision.model_dump(exclude_none=True, exclude_defaults=True)
        if decision.operation == "complete":
            # Evidence is required by the wire branch, even when an empty list
            # will later fail the engine's environment-dependent proof guard.
            payload["evidence"] = [item.model_dump() for item in decision.evidence]
        try:
            self.response_schema().model_validate({"decision": payload})
        except ValidationError:
            return False
        return True

    def as_prompt(self) -> dict[str, Any]:
        choices = [{"operation": "stop", "intent": "stop", "action": None}]
        choices.extend(rule.as_prompt() for rule in _rules(self.facts))
        if self._proof == "duplicate":
            choices.append(
                {
                    "operation": ["duplicate_clear", "duplicate_found"],
                    "intent": "verify",
                    "screen_kind": ["query"],
                    "action": None,
                    "requires": ["current_grid_ref"],
                }
            )
        elif self._proof == "complete":
            choices.append(
                {
                    "operation": "complete",
                    "intent": "verify",
                    "screen_kind": ["query"],
                    "action": None,
                    "requires": ["current_grid_ref", "current_row_ref", "all_field_evidence"],
                }
            )
        elif self._proof == "task_complete":
            choices.append(
                {
                    "operation": "complete",
                    "intent": "verify",
                    "screen_kind": list(_SCREENS),
                    "action": None,
                    "requires": ["current_evidence_for_applicable_user_completion_condition"],
                }
            )
        return {
            "current_step": self.current_step,
            "allowed_next_choices": choices,
            "remaining_fields": list(self.facts.remaining_fields),
            "remaining_credential_sources": list(self.facts.remaining_credentials),
            "credential_action_if_login": (
                "credential_input"
                if self.facts.remaining_credentials
                else "login"
                if self.facts.available_credential_sources
                else None
            ),
            "verified_facts": {
                "phase": self.facts.phase,
                "verified_fields": list(self.facts.verified_field_names),
                "credential_inputs_submitted": list(self.facts.submitted_credentials),
                "credential_submission_is_authentication": False,
                "query_value_verified": self.facts.query_value_verified,
                "query_submitted": self.facts.query_submitted,
                "duplicate_clear": self.facts.duplicate_clear,
                "save_issued": self.facts.save_issued,
                "final_evidence_ready": self.facts.final_evidence_ready,
            },
        }
