"""Controller semantics independent from a particular gamepad brand."""

from enum import Enum


class ControllerAction(Enum):
    CONFIRM = "confirm"
    BACK = "back"
    GAME_MENU = "game-menu"
    SEARCH = "search"
    MAIN_MENU = "main-menu"
    GUIDE = "guide"


# Linux input button roles used by libmanette/SDL mappings. These describe
# physical roles, not Xbox-specific labels.
_BUTTON_ACTIONS = {
    304: ControllerAction.BACK,
    305: ControllerAction.CONFIRM,
    307: ControllerAction.SEARCH,  # Y / north face button on Xbox pads
    308: ControllerAction.GAME_MENU,  # X / west face button on Xbox pads
    315: ControllerAction.MAIN_MENU,  # start/menu
    316: ControllerAction.GUIDE,  # home/guide
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
    316: "Guide",
    317: "L3",
    318: "R3",
}

_TRIGGER_BUTTONS = {
    312: "LT",
    313: "RT",
}


def action_for_button(button: int) -> ControllerAction | None:
    return _BUTTON_ACTIONS.get(button)


def name_for_button(button: int) -> str:
    return _BUTTON_NAMES.get(button, f"Button {button}")


def trigger_for_button(button: int) -> str | None:
    """Return a trigger name for digital full-press events."""
    return _TRIGGER_BUTTONS.get(button)


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
