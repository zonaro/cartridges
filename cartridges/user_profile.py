"""The Linux account selected by the display manager."""

from __future__ import annotations

import configparser
import os
import pwd
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UserProfile:
    username: str
    display_name: str
    avatar: Path | None = None


def current_user_profile(
    *,
    username: str | None = None,
    home: Path | None = None,
    accounts_root: Path = Path("/var/lib/AccountsService"),
) -> UserProfile:
    """Read the current Linux profile with AccountsService-compatible fallbacks."""
    username = username or pwd.getpwuid(os.getuid()).pw_name
    entry = pwd.getpwnam(username)
    home = home or Path(entry.pw_dir)
    display_name = (entry.pw_gecos.split(",", 1)[0] or username).strip()

    account_file = accounts_root / "users" / username
    candidates: list[Path] = []
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(account_file, encoding="utf-8")
        if icon := parser.get("User", "Icon", fallback="").strip():
            candidates.append(Path(icon))
        if real_name := parser.get("User", "RealName", fallback="").strip():
            display_name = real_name
    except (OSError, configparser.Error):
        pass

    candidates.extend(
        (
            accounts_root / "icons" / username,
            home / ".face",
            home / ".face.icon",
        )
    )
    avatar = next((path for path in candidates if path.is_file()), None)
    return UserProfile(username, display_name, avatar)
