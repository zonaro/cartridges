"""Build game commands without coupling launch policy to GTK widgets."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from shutil import which
from shlex import join as shell_join
from shlex import split as shell_split
from typing import Callable, Mapping


@dataclass(frozen=True)
class GameProfile:
    """Per-game launch policy; fields are intentionally persistence-friendly."""

    executable: str
    arguments: tuple[str, ...] = ()
    working_directory: str | None = None
    use_gamemode: bool = True
    use_mangohud: bool = False
    gamescope_options: tuple[str, ...] = ()
    fps_limit: int | None = None
    resolution: tuple[int, int] | None = None
    scaling_mode: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)


def build_game_command(
    executable: str,
    *,
    use_gamemode: bool = False,
    use_mangohud: bool = False,
    gamescope_options: str = "",
    fps_limit: int = 0,
    resolution: str = "",
    scaling_mode: str = "",
    allow_gamescope: bool = True,
    find_program: Callable[[str], str | None] = which,
) -> tuple[str, tuple[str, ...]]:
    """Return the shell command and optional integrations actually enabled."""
    command = executable.strip()
    integrations: list[str] = []
    # MangoHud must wrap the game inside gamemoderun. The dedicated session
    # can instead use Gamescope's mangoapp option in the future.
    if use_mangohud and find_program("mangohud"):
        command = f"mangohud {command}"
        integrations.append("mangohud")
    if use_gamemode and find_program("gamemoderun"):
        command = f"gamemoderun {command}"
        integrations.append("gamemode")
    scope_args: list[str] = []
    if gamescope_options:
        try:
            scope_args.extend(shell_split(gamescope_options))
        except ValueError:
            pass
    if fps_limit > 0:
        scope_args.extend(("--framerate-limit", str(fps_limit)))
    if resolution:
        match = re.fullmatch(r"(\d+)x(\d+)", resolution.strip())
        if match:
            scope_args.extend(("-W", match.group(1), "-H", match.group(2)))
    if scaling_mode in {"auto", "integer", "fit", "fill", "stretch"}:
        scope_args.extend(("-S", scaling_mode))
    if allow_gamescope and scope_args and find_program("gamescope"):
        command = f"gamescope {shell_join(scope_args)} -- {command}"
        integrations.append("gamescope")
    return command, tuple(integrations)
