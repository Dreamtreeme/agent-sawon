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
