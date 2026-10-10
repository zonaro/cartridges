"""Native controller for the optional GPU Screen Recorder backend."""

from .backend import (
    RECORDER_BINARY,
    RecorderSettings,
    build_argv,
    find_backend,
)
from .service import RecorderService

__all__ = [
    "RECORDER_BINARY",
    "RecorderService",
    "RecorderSettings",
    "build_argv",
    "find_backend",
]
