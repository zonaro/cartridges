"""Small Waydroid helpers that do not depend on GTK."""

import shlex
from pathlib import Path
from typing import Optional


def package_id_from_exec(executable: str) -> Optional[str]:
    """Return the package passed to ``waydroid app launch``."""

    try:
        arguments = shlex.split(executable)
    except ValueError:
        return None

    for index, argument in enumerate(arguments):
        if Path(argument).name != "waydroid":
            continue
        if arguments[index + 1 : index + 3] != ["app", "launch"]:
            continue
        try:
            package_id = arguments[index + 3]
        except IndexError:
            return None
        return package_id if package_id and not package_id.startswith("%") else None

    return None
