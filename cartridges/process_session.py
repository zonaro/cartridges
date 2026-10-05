# process_session.py
#
# Copyright 2024 redclaw
#
# Linux port of the fork's automatic playtime tracker. The polling core
# (_poll/_accumulate/flush/stop) is unchanged; what changed is recognition
# and UI integration:
#
# * No Microsoft Store / Game Pass path: there are no AUMIDs or package
#   families on Linux, so games are recognised by executable name and by
#   install folder only.
# * No session blocker window, no manual SessionWindow fallback and no
#   session_toast yet — those UI pieces live in later commits. When the
#   process is never seen the session ends silently (logged); when it is
#   seen and ends, time is recorded and a toast is queued.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Track a game's playtime automatically by watching its process.

Once the game is launched we wait for its process to appear, count the time
it stays running, and end the session automatically when it exits. Elapsed
time is flushed into ``game.playtime`` every poll so a crash only loses the
last poll's worth of time.

A game is recognised by its executable name (configured per game, or derived
from the launch command) or by the install folder read out of the launch
command, whichever answers first.
"""

import logging
import os
import signal
from time import monotonic, sleep
from typing import Optional

from gi.repository import Adw, GLib

from cartridges import shared
from cartridges.game import Game
from cartridges.utils import session_log
from cartridges.utils.format_playtime import format_playtime
from cartridges.utils.process_monitor import (
    exe_name_from_command,
    flatpak_id_from_command,
    flatpak_app_pids,
    install_dir_from_command,
    is_flatpak_app_running,
    is_process_group_running,
    is_process_running,
    is_process_running_under,
    is_steam_app_running,
    steam_appid_from_command,
    steam_app_pids,
    process_group_pids,
)


class ProcessSession:
    """Automatic, process-watching play session for a single game."""

    # Only one session is tracked at a time. Keeping the reference here also
    # stops the instance being garbage-collected while its timer is running.
    active: "Optional[ProcessSession]" = None

    # Seconds between process-list checks. Kept short so the grace period
    # below (which the user configures) is what really governs how long we
    # wait.
    POLL_INTERVAL = 2
    # How long to wait for the process to appear before giving up. Launchers
    # and shader compilation can delay the real exe.
    STARTUP_GRACE = 300
    # How often to write accumulated time to disk (crash/power-loss safety).
    PERSIST_INTERVAL = 60
    # A gap between two polls longer than this is not play: the monotonic
    # clock keeps counting while the PC sleeps, and a game left paused
    # overnight would otherwise be credited the whole night on the first
    # poll after waking. Such a gap is credited as a single poll interval.
    MAX_GAP = POLL_INTERVAL + 30

    def __init__(self, game: Game, launcher_pid: int = 0) -> None:
        self.game = game
        self.launcher_pid = launcher_pid
        # A user-configured process name wins when present (getattr: the
        # details-dialog fields land in a later commit); otherwise fall back
        # to the executable named by the launch command itself.
        configured = (getattr(game, "process_executable", "") or "").strip()
        if getattr(game, "track_process", False) and configured:
            self.exe_name = configured
        else:
            self.exe_name = exe_name_from_command(game.executable)
        # Install folder taken from the launch command, empty when there is
        # no executable path in it to be sure of. Watched *in addition to*
        # `exe_name` rather than instead of it: the folder catches the game
        # handing off to a differently named executable, and the name catches
        # a game whose command points into a subfolder.
        self.install_dir = install_dir_from_command(game.executable)
        self.steam_appid = str(
            getattr(game, "steam_appid", "")
            or steam_appid_from_command(game.executable)
        )
        self.flatpak_id = flatpak_id_from_command(game.executable)
        # Seconds to keep waiting after the process disappears before ending
        # the session, so a game that restarts itself isn't cut off.
        self.grace = max(0, shared.schema.get_int("process-tracking-grace"))
        self.session_seconds = 0  # this session's running total, for the toast
        self.started = False  # has the process been seen at least once?
        self.counting = False  # are we currently accruing time?
        # All three are monotonic readings, never wall-clock: they measure
        # intervals (see `_accumulate`), so a clock change must not move them.
        self.last_tick: Optional[float] = None  # when we last accrued time
        self.last_persist = 0.0  # last time we saved to disk
        self.waited = 0  # seconds spent waiting for the process to appear
        self.missing_since: Optional[float] = None  # when the process vanished
        self.poll_id = 0

    @property
    def elapsed(self) -> int:
        """Seconds counted so far, including the stretch not yet banked."""
        if self.counting and self.last_tick is not None:
            return self.session_seconds + int(monotonic() - self.last_tick)
        return self.session_seconds

    def _is_running(self) -> bool:
        """Is the game running right now?"""
        if self.launcher_pid and is_process_group_running(self.launcher_pid):
            return True
        if self.steam_appid and is_steam_app_running(self.steam_appid):
            return True
        if self.flatpak_id and is_flatpak_app_running(self.flatpak_id):
            return True
        if self.exe_name and is_process_running(self.exe_name):
            return True
        return bool(self.install_dir) and is_process_running_under(self.install_dir)

    def _attributed_pids(self) -> set[int]:
        """Processes that can be safely attributed to this launch."""
        pids = process_group_pids(self.launcher_pid)
        pids.extend(steam_app_pids(self.steam_appid))
        pids.extend(flatpak_app_pids(self.flatpak_id))
        return set(pids)

    def terminate_game(self, force: bool = False) -> None:
        """Signal only processes that can be safely attributed to this game."""
        sig = signal.SIGKILL if force else signal.SIGTERM
        pids = process_group_pids(self.launcher_pid)
        if pids:
            try:
                os.killpg(self.launcher_pid, sig)
                logging.info(
                    "%s game process group %s",
                    "Killed" if force else "Terminated",
                    self.launcher_pid,
                )
            except (OSError, ProcessLookupError) as error:
                logging.warning("Could not signal game process group: %s", error)
        attributed = set(steam_app_pids(self.steam_appid))
        attributed.update(flatpak_app_pids(self.flatpak_id))
        attributed.difference_update(pids)
        for pid in attributed:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                continue
            except OSError as error:
                logging.warning("Could not signal game PID %s: %s", pid, error)
        if attributed:
            logging.info("Signalled %s attributed game processes", len(attributed))
        elif not pids:
            logging.warning(
                "No safely attributable process to terminate for %s", self.game.name
            )

    def shutdown(self, timeout: float = 2.0) -> None:
        """End a dedicated session without leaving its game processes behind."""
        self.terminate_game()
        deadline = monotonic() + max(0.0, timeout)
        while self._attributed_pids() and monotonic() < deadline:
            sleep(0.05)
        if self._attributed_pids():
            logging.warning("Game did not exit after SIGTERM; forcing shutdown")
            self.terminate_game(force=True)
        if self.poll_id:
            GLib.source_remove(self.poll_id)
            self.poll_id = 0
        if self.started:
            self._accumulate()
            session_log.record(self.game.game_id, self.session_seconds)
            self.game.save()
        if ProcessSession.active is self:
            ProcessSession.active = None

    def start(self) -> None:
        """Begin watching for the game's process."""
        ProcessSession.active = self
        self.last_persist = monotonic()
        self.poll_id = GLib.timeout_add_seconds(self.POLL_INTERVAL, self._poll)

    def _poll(self) -> bool:
        running = self._is_running()

        if not self.started:
            if running:
                # Process appeared: start the clock
                self.started = True
                self.counting = True
                self.last_tick = monotonic()
                self.missing_since = None
                logging.debug(
                    "%s seen running after %ss; counting playtime",
                    self.game.name,
                    self.waited,
                )
                if shared.win is not None:
                    shared.win.show_session_blocker(self.game)
            else:
                self.waited += self.POLL_INTERVAL
                if self.waited >= self.STARTUP_GRACE:
                    # Returning SOURCE_REMOVE already destroys this source,
                    # so zero the id first to keep stop() from removing it
                    # again.
                    self.poll_id = 0
                    logging.info(
                        "%s never appeared; ending session without a record",
                        self.exe_name or self.install_dir or self.game.name,
                    )
                    self.stop(record=False)
                    return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        if running:
            self.missing_since = None
            if not self.counting:
                # Resuming after a brief disappearance (e.g. the game
                # relaunched itself): start a fresh stretch so the downtime
                # isn't counted.
                self.counting = True
                self.last_tick = monotonic()
            self._accumulate()
            self._maybe_persist()
        else:
            if self.counting:
                # Just vanished: bank the time up to now, then pause counting
                # so the grace wait below is never counted as playtime.
                self._accumulate()
                self.counting = False
            if self.missing_since is None:
                self.missing_since = monotonic()
            if monotonic() - self.missing_since >= self.grace:
                # See the startup-grace path: avoid a double source removal.
                self.poll_id = 0
                self.stop(record=True)
                return GLib.SOURCE_REMOVE

        return GLib.SOURCE_CONTINUE

    def _accumulate(self) -> None:
        """Add the time since the last tick to the running totals.

        Measured on the monotonic clock, not the wall clock: `time()` can
        jump (NTP, manual changes, sleep/wake drift) and a forward jump would
        be credited as playtime the game never had.
        """
        now = monotonic()
        if self.counting and self.last_tick is not None:
            elapsed = int(now - self.last_tick)
            if elapsed > self.MAX_GAP:
                logging.info(
                    "%ss gap while tracking %s (sleep?); counting %ss",
                    elapsed,
                    self.game.name,
                    self.POLL_INTERVAL,
                )
                self.last_tick = now - self.POLL_INTERVAL
                elapsed = self.POLL_INTERVAL
            if elapsed > 0:
                self.game.playtime += elapsed
                self.session_seconds += elapsed
                # Carry the sub-second remainder, or truncating every
                # two-second poll would quietly lose time on long sessions.
                now = self.last_tick + elapsed
        self.last_tick = now

    def _maybe_persist(self) -> None:
        """Save accumulated time at most once per PERSIST_INTERVAL."""
        now = monotonic()
        if now - self.last_persist >= self.PERSIST_INTERVAL:
            self.game.save()
            self.last_persist = now

    def flush(self) -> None:
        """Persist any accumulated time without touching the UI.

        Used on application shutdown, when widgets may already be gone but
        the session's last stretch of playtime still has to reach the disk.
        """
        if self.started:
            self._accumulate()
            self.game.save()

    def stop(self, record: bool = True) -> None:
        """End the session, recording the elapsed time by default."""
        if self.poll_id:
            GLib.source_remove(self.poll_id)
            self.poll_id = 0

        if ProcessSession.active is self:
            ProcessSession.active = None

        if shared.win is not None:
            shared.win.hide_session_blocker()
            if shared.runtime.is_game_mode:
                # Present is the compositor-supported focus request. It also
                # covers games which hand off through Steam/Lutris and whose
                # original launcher PID has long since exited.
                shared.win.present()

        if record and self.started:
            # Capture the final running stretch (a no-op if already paused).
            self._accumulate()
            # Recorded after the accumulate and before the save, so the
            # history line and the game's total hold the same seconds.
            session_log.record(self.game.game_id, self.session_seconds)
            self.game.save()
            self.game.update()
            toast = Adw.Toast.new(
                # The variables are the game's title and the session length.
                _("{} played for {}").format(
                    self.game.name, format_playtime(self.session_seconds)
                )
            )
            toast.set_use_markup(False)
            shared.win.toast_queue.add(toast)
        elif record:
            logging.info(
                "Session for %s ended before %s was ever seen; nothing recorded",
                self.game.name,
                self.exe_name or self.install_dir or "the game",
            )

    def close(self, *_args: object) -> None:
        """Alias so callers can treat this like a manual session window."""
        self.stop(record=True)
