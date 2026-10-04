"""Centralised runtime-mode detection for desktop and console sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from os import environ as process_environment
from typing import Mapping, Sequence


class RuntimeMode(Enum):
    DESKTOP = "desktop"
    GAME_SESSION = "game-session"
    NESTED_GAME_MODE = "nested-game-mode"


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class RuntimeContext:
    mode: RuntimeMode = RuntimeMode.DESKTOP

    @property
    def is_game_mode(self) -> bool:
        return self.mode is not RuntimeMode.DESKTOP

    @property
    def is_session(self) -> bool:
        return self.mode is RuntimeMode.GAME_SESSION

    @property
    def is_nested(self) -> bool:
        return self.mode is RuntimeMode.NESTED_GAME_MODE

    @classmethod
    def detect(
        cls,
        argv: Sequence[str],
        environment: Mapping[str, str] | None = None,
    ) -> "RuntimeContext":
        env = process_environment if environment is None else environment
        args = set(argv[1:])
        requested = bool({"--game-mode", "--session"} & args) or (
            env.get("CARTRIDGES_GAME_SESSION", "").casefold() in _TRUE_VALUES
        )
        if not requested:
            return cls()
        nested = bool({"--windowed", "--nested"} & args) or (
            env.get("CARTRIDGES_GAME_MODE_NESTED", "").casefold() in _TRUE_VALUES
        )
        return cls(
            RuntimeMode.NESTED_GAME_MODE if nested else RuntimeMode.GAME_SESSION
        )

