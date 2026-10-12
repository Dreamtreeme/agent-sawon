"""Shared GUI vocabulary used by model contracts, policy, and perception."""

from typing import Literal

KEY_INPUT_VALUES = (
    "enter",
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
KeyInput = Literal[*KEY_INPUT_VALUES]

# Enter submits or confirms; it is not a navigation or tab-selection key.
NAVIGATION_KEY_INPUTS = tuple(key for key in KEY_INPUT_VALUES if key != "enter")

CONTROL_ROLE_VALUES = (
    "edit",
    "combobox",
    "button",
    "checkbox",
    "tabitem",
    "menuitem",
    "table",
    "row",
    "cell",
    "header",
    "group",
    "window",
    "text",
    "icon",
)
ControlRole = Literal[*CONTROL_ROLE_VALUES]

ACTION_CONTROL_ROLES: dict[str, set[ControlRole]] = {
    "fill": {"edit", "combobox", "cell"},
    "click": {
        "edit",
        "combobox",
        "button",
        "checkbox",
        "tabitem",
        "menuitem",
        "row",
        "cell",
        "icon",
    },
    "double_click": {"button", "menuitem", "row", "cell", "icon"},
    "key": {
        "edit",
        "combobox",
        "button",
        "checkbox",
        "tabitem",
        "menuitem",
        "table",
        "row",
        "cell",
        "group",
        "window",
    },
}

if unknown_roles := set().union(*ACTION_CONTROL_ROLES.values()) - set(CONTROL_ROLE_VALUES):
    raise ValueError(f"action_control_roles_not_in_vocabulary:{sorted(unknown_roles)}")

ACTION_INTENT_VALUES = (
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
    "complete",
    "inspect",
)
ActionIntent = Literal[*ACTION_INTENT_VALUES]

EXECUTABLE_INTENT_VALUES = tuple(
    intent
    for intent in ACTION_INTENT_VALUES
    if intent not in {"verify", "stop", "complete", "inspect"}
)
ExecutableIntent = Literal[*EXECUTABLE_INTENT_VALUES]

MATCH_ACTION_INTENT_VALUES = tuple(
    intent for intent in EXECUTABLE_INTENT_VALUES if intent != "wait"
)
MatchActionIntent = Literal[*MATCH_ACTION_INTENT_VALUES]

DECISION_INTENT_VALUES = (*EXECUTABLE_INTENT_VALUES, "verify", "stop")
DecisionIntent = Literal[*DECISION_INTENT_VALUES]

# This contract predates the common ordering and intentionally places fill before search.
ACTION_CAPABILITY_INTENT_VALUES = (
    "login",
    "navigate",
    "query_input",
    "fill",
    "search",
    "save",
    "confirm_save",
    "select_tab",
    "select_option",
    "cancel",
    "wait",
    "complete",
    "inspect",
)
ActionCapabilityIntent = Literal[*ACTION_CAPABILITY_INTENT_VALUES]

for intent_contract, intent_values in {
    "action_capability": ACTION_CAPABILITY_INTENT_VALUES,
    "match_action": MATCH_ACTION_INTENT_VALUES,
    "decision": DECISION_INTENT_VALUES,
    "executable": EXECUTABLE_INTENT_VALUES,
}.items():
    if unknown_intents := set(intent_values) - set(ACTION_INTENT_VALUES):
        raise ValueError(f"{intent_contract}_intents_not_in_vocabulary:{sorted(unknown_intents)}")
