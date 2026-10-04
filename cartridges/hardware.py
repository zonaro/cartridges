"""Discover session devices without changing system configuration."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def drm_connectors(root: Path = Path("/sys/class/drm")) -> list[str]:
    connectors = []
    for status in root.glob("card*-*/status"):
        try:
            if status.read_text(encoding="utf-8").strip() == "connected":
                connectors.append(status.parent.name.split("-", 1)[1])
        except (OSError, IndexError):
            continue
    return sorted(set(connectors))


def vrr_supported(root: Path = Path("/sys/class/drm")) -> bool:
    for capability in root.glob("card*-*/vrr_capable"):
        try:
            if capability.read_text(encoding="utf-8").strip() == "1":
                return True
        except OSError:
            continue
    return False


_NODE_LINE = re.compile(r"(?:\*\s+)?\d+\.\s+(\S+)")


def parse_wpctl_nodes(output: str) -> tuple[list[str], list[str]]:
    section = ""
    sinks: list[str] = []
    sources: list[str] = []
    for line in output.splitlines():
        stripped = line.strip(" │├└─")
        if stripped == "Sinks:":
            section = "sinks"
            continue
        if stripped == "Sources:":
            section = "sources"
            continue
        if stripped.endswith(":"):
            section = ""
            continue
        match = _NODE_LINE.search(stripped)
        if not match:
            continue
        if section == "sinks":
            sinks.append(match.group(1))
        elif section == "sources":
            sources.append(match.group(1))
    return sinks, sources


def pipewire_nodes() -> tuple[list[str], list[str]]:
    try:
        result = subprocess.run(
            ("wpctl", "status", "-n"),
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], []
    return parse_wpctl_nodes(result.stdout)
