"""Lifecycle control for the optional ``gpu-screen-recorder`` process.

The service only starts, pauses, saves and stops the external recorder; it
never captures frames itself. It is GTK-free so it can run from a worker, and
every operation degrades to a ``False`` no-op when the binary is missing,
keeping the recorder strictly optional and non-fatal.
"""

from __future__ import annotations

import logging
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from shutil import which
from typing import Callable

from .backend import RecorderSettings, build_argv, find_backend

log = logging.getLogger(__name__)

# After SIGINT the recorder flushes the file, which can take a while for a long
# recording. Wait for it before considering the process stuck.
STOP_TIMEOUT = 10.0
KILL_TIMEOUT = 5.0


class RecorderService:
    """Own a single ``gpu-screen-recorder`` child and signal it."""

    def __init__(
        self,
        settings: RecorderSettings | None = None,
        *,
        find_program: Callable[[str], str | None] = which,
    ) -> None:
        self._settings = settings or RecorderSettings()
        self._find_program = find_program
        self._process: subprocess.Popen[bytes] | None = None
        self.paused = False

    @property
    def settings(self) -> RecorderSettings:
        return self._settings

    def backend_path(self) -> str | None:
        """Resolved path of the recorder binary, or ``None`` when absent."""
        return find_backend(self._find_program)

    def is_available(self) -> bool:
        return self.backend_path() is not None

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(
        self,
        settings: RecorderSettings | None = None,
        *,
        output: str | None = None,
    ) -> bool:
        """Spawn the recorder, returning whether the process was started."""
        if self.is_running():
            return False
        backend = self.backend_path()
        if backend is None:
            log.info("gpu-screen-recorder is not installed; recording is disabled")
            return False
        if settings is not None:
            self._settings = settings
        argv = build_argv(
            self._settings, program=backend, output=output or self._resolve_output()
        )
        try:
            self._process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            log.warning("Could not start gpu-screen-recorder: %s", error)
            self._process = None
            return False
        self.paused = False
        return True

    def stop(self, timeout: float = STOP_TIMEOUT) -> bool:
        """Stop the recorder with SIGINT, which saves a regular recording."""
        process = self._process
        if process is None or process.poll() is not None:
            self._process = None
            return False
        if not self._signal(signal.SIGINT):
            return False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("gpu-screen-recorder ignored SIGINT; killing it")
            process.kill()
            try:
                process.wait(timeout=KILL_TIMEOUT)
            except subprocess.TimeoutExpired:
                log.warning("gpu-screen-recorder could not be reaped")
        self._process = None
        self.paused = False
        return True

    def save_replay(self) -> bool:
        """Ask a replay buffer to flush the last N seconds to disk."""
        return self._signal(signal.SIGUSR1)

    def toggle_pause(self) -> bool:
        """Pause or resume a regular recording, tracking the new state."""
        if not self._signal(signal.SIGUSR2):
            return False
        self.paused = not self.paused
        return True

    def _signal(self, signum: int) -> bool:
        process = self._process
        if process is None or process.poll() is not None:
            return False
        try:
            process.send_signal(signum)
        except (ProcessLookupError, OSError) as error:
            log.warning("Could not signal gpu-screen-recorder: %s", error)
            return False
        return True

    def _resolve_output(self) -> str:
        """Pick the ``-o`` target: stream URL, replay folder or a clip file."""
        settings = self._settings
        if settings.stream_url:
            return settings.stream_url
        directory = Path(settings.output_dir).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            log.warning("Could not create recording folder %s: %s", directory, error)
        if settings.replay_seconds > 0:
            return str(directory)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return str(directory / f"Jolven-{stamp}.{settings.container}")
