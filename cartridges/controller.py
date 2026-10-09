"""Controller semantics independent from a particular gamepad brand."""

from enum import Enum


class ControllerAction(Enum):
    CONFIRM = "confirm"
    BACK = "back"
    GAME_MENU = "game-menu"
    SEARCH = "search"
    MAIN_MENU = "main-menu"
    GUIDE = "guide"
    HOME = "home"


class ControllerLayout(Enum):
    XBOX = "xbox"
    PLAYSTATION = "playstation"
    GENERIC = "generic"


# Some controllers expose their central system button as Linux ``BTN_MODE``;
# others expose it as ``KEY_HOMEPAGE``. Keep both codes explicit because the
# session overlay preference lets the user choose either event or both.
BUTTON_MODE = 316
BUTTON_HOME = 172


# Linux input button roles used by libmanette/SDL mappings. These describe
# physical roles, not Xbox-specific labels.
_BUTTON_ACTIONS = {
    304: ControllerAction.BACK,
    305: ControllerAction.CONFIRM,
    307: ControllerAction.SEARCH,  # Y / north face button on Xbox pads
    308: ControllerAction.GAME_MENU,  # X / west face button on Xbox pads
    315: ControllerAction.MAIN_MENU,  # start/menu
    BUTTON_MODE: ControllerAction.GUIDE,
    BUTTON_HOME: ControllerAction.HOME,
}


# Names follow the Xbox layout shown by the UI.  Unknown codes are still
# exposed by the tester so unusual controllers can be diagnosed.
_BUTTON_NAMES = {
    304: "B",
    305: "A",
    307: "Y",
    308: "X",
    310: "LB",
    311: "RB",
    314: "View",
    315: "Menu",
    BUTTON_MODE: "Mode",
    BUTTON_HOME: "Home",
    317: "L3",
    318: "R3",
}

_TRIGGER_BUTTONS = {
    312: "LT",
    313: "RT",
}


# Labels for the face and system buttons in the PlayStation layout, using the
# same Linux input codes as ``_BUTTON_NAMES``. Triggers appear here too because
# a DualShock/DualSense exposes them as L2/R2.
_PLAYSTATION_BUTTON_NAMES = {
    304: "Circle",
    305: "Cross",
    307: "Triangle",
    308: "Square",
    310: "L1",
    311: "R1",
    312: "L2",
    313: "R2",
    314: "Share",
    315: "Options",
    316: "PS",
    BUTTON_HOME: "Home",
    317: "L3",
    318: "R3",
}

_XBOX_KEYWORDS = ("xbox", "x-box", "microsoft")
_PLAYSTATION_KEYWORDS = (
    "sony",
    "playstation",
    "ps3",
    "ps4",
    "ps5",
    "dualshock",
    "dualsense",
)


def detect_controller_layout(name: str | None) -> ControllerLayout:
    """Guess the physical button layout from a controller's reported name.

    Detection is intentionally conservative and case-insensitive: anything
    that does not clearly identify an Xbox or PlayStation family device falls
    back to the generic layout.
    """
    normalized = (name or "").lower()
    if any(keyword in normalized for keyword in _XBOX_KEYWORDS):
        return ControllerLayout.XBOX
    if any(keyword in normalized for keyword in _PLAYSTATION_KEYWORDS):
        return ControllerLayout.PLAYSTATION
    return ControllerLayout.GENERIC


def labels_for_layout(layout: ControllerLayout) -> dict[int, str]:
    """Return the display labels keyed by Linux input button code."""
    if layout is ControllerLayout.PLAYSTATION:
        return dict(_PLAYSTATION_BUTTON_NAMES)
    return dict(_BUTTON_NAMES)


def action_for_button(button: int) -> ControllerAction | None:
    return _BUTTON_ACTIONS.get(button)


def name_for_button(button: int) -> str:
    return _BUTTON_NAMES.get(button, f"Button {button}")


def trigger_for_button(button: int) -> str | None:
    """Return a trigger name for digital full-press events."""
    return _TRIGGER_BUTTONS.get(button)


def opens_game_overlay(button: int, preference: str) -> bool:
    """Whether *button* matches the configured game-overlay system button."""
    if preference == "both":
        return button in (BUTTON_MODE, BUTTON_HOME)
    if preference == "mode":
        return button == BUTTON_MODE
    if preference == "home":
        return button == BUTTON_HOME
    return False


# libmanette hat encoding used by ``gamepad_hat_axis``: the event carries a
# (hat, axis, value) triple and ``axis + value`` identifies the direction.
# 15 = left, 16 = up, 17 = right, 18 = down.
_HAT_DIRECTIONS = {
    15: "left",
    17: "right",
    16: "up",
    18: "down",
}

#: Stick deflection needed to count as one discrete D-pad step.
STICK_THRESHOLD = 0.55


def hat_direction(hat: int, value: int) -> str | None:
    """Return a direction name for a hat-axis event, if it is a D-pad step."""
    if value == 0:
        return None
    return _HAT_DIRECTIONS.get(hat + value)


def stick_direction(value: float, threshold: float = STICK_THRESHOLD) -> int:
    """Quantize a stick axis value into -1, 0 or +1."""
    if value < -threshold:
        return -1
    if value > threshold:
        return 1
    return 0
