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
