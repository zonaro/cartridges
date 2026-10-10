"""Pure argv construction for the optional GPU Screen Recorder backend.

The recorder is a thin controller around the external ``gpu-screen-recorder``
process (GPL-3.0-only). Nothing here imports GTK: settings are plain data and
``build_argv`` is deterministic, so the command line can be unit-tested without
a display, a running application or the binary installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from shutil import which
from typing import Callable


RECORDER_BINARY = "gpu-screen-recorder"

# Values understood by gpu-screen-recorder for each option. Kept here so the
# preferences UI and the tests share one source of truth. ``quality`` and
# ``bitrate_mode`` are not validated because gpu-screen-recorder also accepts a
# numeric bitrate (kbps) for ``-q``.
VIDEO_SOURCES = ("portal", "screen", "focused", "region")
CONTAINERS = ("mp4", "mkv", "flv", "webm")
CODECS = ("auto", "h264", "hevc", "av1")
QUALITIES = ("medium", "high", "very_high", "ultra")
BITRATE_MODES = ("auto", "qp", "vbr", "cbr")
AUDIO_CODECS = ("opus", "aac", "flac")
REPLAY_STORAGE = ("ram", "disk")


@dataclass(frozen=True)
class RecorderSettings:
    """Persistable recording policy; mirrors the future Preferences page."""

    video_source: str = "portal"
    container: str = "mp4"
    codec: str = "auto"
    quality: str = "very_high"
    bitrate_mode: str = "auto"
    fps: int = 60
    cursor: bool = True
    scale: str = ""
    audio_sources: tuple[str, ...] = ("default_output",)
    audio_codec: str = "opus"
    audio_bitrate: int = 128
    replay_seconds: int = 0
    replay_storage: str = "ram"
    output_dir: str = "~/Videos/Jolven"
    stream_url: str = ""


def find_backend(
    find_program: Callable[[str], str | None] = which,
) -> str | None:
    """Return the resolved path of the recorder binary, or ``None``."""
    return find_program(RECORDER_BINARY)


def build_argv(
    settings: RecorderSettings,
    *,
    program: str = RECORDER_BINARY,
    output: str | None = None,
) -> list[str]:
    """Return the ``gpu-screen-recorder`` argument vector for ``settings``.

    ``output`` overrides the ``-o`` target; otherwise the stream URL, then the
    output directory, is used. Replay mode expects ``-o`` to point at an
    existing directory, and a regular recording expects a file path; the
    service resolves the concrete target before calling this.
    """
    argv = [
        program,
        "-w", settings.video_source,
        "-c", settings.container,
        "-k", settings.codec,
        "-q", settings.quality,
        "-bm", settings.bitrate_mode,
        "-f", str(settings.fps),
        "-cursor", "yes" if settings.cursor else "no",
    ]
    if settings.scale:
        argv += ["-s", settings.scale]
    for source in settings.audio_sources:
        argv += ["-a", source]
    argv += ["-ac", settings.audio_codec, "-ab", str(settings.audio_bitrate)]
    if settings.replay_seconds > 0:
        argv += ["-r", str(settings.replay_seconds)]
        argv += ["-replay-storage", settings.replay_storage]
    argv += ["-o", output or settings.stream_url or settings.output_dir]
    return argv
