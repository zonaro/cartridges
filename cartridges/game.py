# game.py
#
# Copyright 2022-2023 kramo
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

import logging
import shlex
from pathlib import Path
from time import time
from typing import Any, Optional

from gi.repository import Adw, GLib, GObject, Gtk

from cartridges import shared
from cartridges.game_cover import GameCover
from cartridges.utils.run_executable import run_executable


STATUS_LABELS = {
    "backlog": _("Quero jogar"),
    "playing": _("Jogando"),
    "beaten": _("Zerado"),
    "dropped": _("Abandonado"),
}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status or "", "")


# pylint: disable=too-many-instance-attributes
@Gtk.Template(resource_path=shared.PREFIX + "/gtk/game.ui")
class Game(Gtk.Box):
    __gtype_name__ = "Game"

    title = Gtk.Template.Child()
    play_button = Gtk.Template.Child()
    cover = Gtk.Template.Child()
    source_badge = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    cover_button = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()
    play_revealer = Gtk.Template.Child()
    menu_revealer = Gtk.Template.Child()
    game_options = Gtk.Template.Child()
    hidden_game_options = Gtk.Template.Child()

    loading: int = 0
    filtered: bool = False

    added: int
    executable: str
    game_id: str
    source: str
    hidden: bool = False
    is_launcher: bool = False
    last_played: int = 0
    playtime: int = 0
    name: str
    developer: Optional[str] = None
    publisher: Optional[str] = None
    release_date: Optional[str] = None
    metacritic: Optional[int] = None
    steam_review: Optional[str] = None
    # Already display-ready: picked from Steam's user tags, which have no
    # stable meaning to re-derive later, so the chosen name is what is kept.
    genre: Optional[str] = None
    # "full" or "partial". None means Steam says neither, which is not the
    # same as "no gamepad support".
    controller_support: Optional[str] = None
    # Steam's "Gamepad Recommended": not "works with a gamepad" but "meant
    # to be played with one".
    gamepad_recommended: bool = False
    # Steam's short_description pitch.
    description: Optional[str] = None
    # TheGamesDB augments non-Steam games and supplies artwork fallbacks.
    # URLs are persisted so the details view never needs another API request.
    tgdb_id: Optional[int] = None
    tgdb_checked: int = 0
    tgdb_platform: Optional[str] = None
    tgdb_players: Optional[int] = None
    tgdb_age_rating: Optional[str] = None
    tgdb_coop: Optional[str] = None
    tgdb_screenshots: list[str] = []
    tgdb_fanart: list[str] = []
    # STEAM_METADATA_VERSION at the last successful Steam lookup, or 0 for a
    # game that predates the marker.
    steam_checked: int = 0
    # Steam appid this game's metadata comes from. Once known it is reused
    # for every later refresh: resolving by name again could land on a
    # sequel or re-release, silently breaking a title corrected by hand.
    steam_appid: Optional[str] = None
    # HowLongToBeat completion estimates, in seconds (same unit as
    # `playtime`). None means "not looked up yet or nobody has submitted
    # that time", which is why they are nullable rather than defaulting
    # to 0 — a real 0 h game does not exist.
    hltb_id: Optional[int] = None
    hltb_main: Optional[int] = None
    hltb_main_extra: Optional[int] = None
    hltb_completionist: Optional[int] = None
    # Games catalogued on HowLongToBeat only chapter by chapter carry one
    # entry per chapter ({"number", "name", "hltb_id", "hltb_main", …}).
    hltb_chapters: Optional[list[dict]] = None
    # Opt-in per game: watch the repack feed for a newer patch of this
    # title. Off by default — most games never need it.
    track_updates: bool = False
    # pubDate (epoch seconds) of the newest update-digest post this game was
    # matched in and is currently being advertised for. 0 means "no notice".
    update_available_ts: int = 0
    # pubDate of the most recent patch the user dismissed for this game. A
    # future digest only re-raises the notice when it is newer than this.
    update_dismissed_ts: int = 0
    # Repack page of the currently advertised patch. Opened when the user
    # acts on the notice; "" when there is no notice or the digest carried
    # no link.
    update_url: str = ""
    install_size: int = 0
    install_size_ts: int = 0
    status: str = ""
    rating: int = 0
    notes: str = ""
    removed: bool = False
    blacklisted: bool = False
    game_cover: GameCover = None
    version: int = 0
    game_mode_use_gamemode: Optional[bool] = None
    game_mode_use_mangohud: Optional[bool] = None
    launch_working_directory: str = ""
    launch_environment: dict[str, str] = {}
    gamescope_options: str = ""
    fps_limit: int = 0
    game_resolution: str = ""
    scaling_mode: str = ""
    track_process: bool = False
    process_executable: str = ""

    @property
    def has_update(self) -> bool:
        return self.track_updates and self.update_available_ts > 0

    @property
    def zerado(self) -> bool:
        return self.removed and not self.blacklisted and self.status == "beaten"

    @property
    def stars(self) -> int:
        try:
            rating = int(self.rating or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, min(5, rating))

    def definir_status(self, status: str) -> None:
        era_zerado = self.zerado
        self.status = status
        # Jogos Zerados is an archive of beaten games.  Merely storing the
        # status used to leave an installed game in the regular library,
        # although the UI had just told the user it was "Zerado".  Move it to
        # the archive as part of the same state transition; changing the
        # status again restores games that still have an executable below.
        if status == "beaten" and not self.blacklisted:
            self.removed = True
        if era_zerado and not self.zerado and self.executable:
            self.removed = False

    def dismiss_update(self) -> None:
        self.update_dismissed_ts = max(
            self.update_dismissed_ts, self.update_available_ts
        )
        self.update_available_ts = 0
        self.update_url = ""
        self.save()
        self.update()

    def __init__(self, data: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.app = shared.win.get_application()
        self.version = shared.SPEC_VERSION

        self.update_values(data)
        # Class attributes provide backwards-compatible defaults for games
        # created before launch profiles existed.  Keep the mutable value
        # private to each instance so editing one game cannot affect another.
        self.launch_environment = dict(self.launch_environment or {})
        self.tgdb_screenshots = list(self.tgdb_screenshots or [])
        self.tgdb_fanart = list(self.tgdb_fanart or [])
        self.base_source = self.source.split("_")[0]

        self.set_play_icon()

        self.event_contoller_motion = Gtk.EventControllerMotion.new()
        self.add_controller(self.event_contoller_motion)
        self.event_contoller_motion.connect("enter", self.toggle_play, False)
        self.event_contoller_motion.connect("leave", self.toggle_play, None, None)
        self.cover_button.connect("clicked", self.main_button_clicked, False)
        self.play_button.connect("clicked", self.main_button_clicked, True)

        shared.schema.connect("changed", self.schema_changed)

    def update_values(self, data: dict[str, Any]) -> None:
        for key, value in data.items():
            # Convert executables to strings
            if key == "executable" and isinstance(value, list):
                value = shlex.join(value)
            setattr(self, key, value)

    def update(self) -> None:
        self.emit("update-ready", {})

    def save(self) -> None:
        self.emit("save-ready", {})

    def create_toast(self, title: str, action: Optional[str] = None) -> None:
        toast = Adw.Toast.new(title.format(self.name))
        toast.set_priority(Adw.ToastPriority.HIGH)
        toast.set_use_markup(False)

        if action:
            toast.set_button_label(_("Undo"))
            toast.connect("button-clicked", shared.win.on_undo_action, self, action)

            if (self, action) in shared.win.toasts.keys():
                # Dismiss the toast if there already is one
                shared.win.toast_queue.dismiss(shared.win.toasts[(self, action)])

            shared.win.toasts[(self, action)] = toast

        shared.win.toast_queue.add(toast)

    def launch(self) -> None:
        try:
            launcher = run_executable(
                self.executable,
                use_gamemode=self.game_mode_use_gamemode,
                use_mangohud=self.game_mode_use_mangohud,
                working_directory=self.launch_working_directory or None,
                environment=self.launch_environment,
                gamescope_options=self.gamescope_options,
                fps_limit=self.fps_limit,
                resolution=self.game_resolution,
                scaling_mode=self.scaling_mode,
            )
        except OSError as error:
            logging.error("Could not launch %s: %s", self.name, error)
            self.create_toast(_("{} could not be launched"))
            return

        self.last_played = int(time())
        self.save()
        self.update()

        def check_launcher() -> bool:
            status = launcher.poll()
            if status is not None and status != 0:
                logging.error("Launcher for %s exited with status %s", self.name, status)
                from cartridges.process_session import ProcessSession

                if (
                    ProcessSession.active is not None
                    and ProcessSession.active.game is self
                    and not ProcessSession.active.started
                ):
                    ProcessSession.active.stop(record=False)
                self.create_toast(_("{} could not be launched"))
            return GLib.SOURCE_REMOVE

        GLib.timeout_add_seconds(2, check_launcher)

        # Automatic playtime tracking. Skipped when disabled, when another
        # session is already running it is ended first, and when the game
        # is not followable (launcher URI with no path) the launch
        # behaves exactly as before.
        if shared.schema.get_boolean("playtime-tracking"):
            from cartridges.process_session import ProcessSession

            if ProcessSession.active is not None:
                ProcessSession.active.stop(record=True)
            session = ProcessSession(self, launcher.pid)
            if (
                session.launcher_pid
                or session.exe_name
                or session.install_dir
                or session.steam_appid
                or session.flatpak_id
            ):
                session.start()
            else:
                logging.debug(
                    "%s is not followable, skipping playtime tracking", self.name
                )

        if (
            shared.schema.get_boolean("exit-after-launch")
            and not shared.runtime.is_game_mode
        ):
            self.app.quit()

        # The variable is the title of the game
        self.create_toast(_("{} launched"))

    def toggle_hidden(self, toast: bool = True) -> None:
        self.hidden = not self.hidden
        self.save()

        if shared.win.navigation_view.get_visible_page() == shared.win.details_page:
            shared.win.navigation_view.pop()

        self.update()

        if toast:
            self.create_toast(
                # The variable is the title of the game
                (_("{} hidden") if self.hidden else _("{} unhidden")).format(self.name),
                "hide",
            )

    def remove_game(self) -> None:
        # Add "removed=True" to the game properties so it can be deleted on next init
        self.removed = True
        self.save()
        self.update()

        if shared.win.navigation_view.get_visible_page() == shared.win.details_page:
            shared.win.navigation_view.pop()

        # The variable is the title of the game
        self.create_toast(_("{} removed").format(self.name), "remove")

    def set_loading(self, state: int) -> None:
        self.loading += state
        loading = self.loading > 0

        self.cover.set_opacity(int(not loading))
        self.spinner.set_visible(loading)

    def get_cover_path(self) -> Optional[Path]:
        cover_path = shared.covers_dir / f"{self.game_id}.gif"
        if cover_path.is_file():
            return cover_path  # type: ignore

        cover_path = shared.covers_dir / f"{self.game_id}.tiff"
        if cover_path.is_file():
            return cover_path  # type: ignore

        return None

    def toggle_play(
        self, _widget: Any, _prop1: Any, _prop2: Any, state: bool = True
    ) -> None:
        if not self.menu_button.get_active():
            self.play_revealer.set_reveal_child(not state)
            self.menu_revealer.set_reveal_child(not state)

    def main_button_clicked(self, _widget: Any, button: bool) -> None:
        if shared.schema.get_boolean("cover-launches-game") ^ button:
            self.launch()
        else:
            shared.win.show_details_page(self)

    def set_play_icon(self) -> None:
        self.play_button.set_icon_name(
            "help-about-symbolic"
            if shared.schema.get_boolean("cover-launches-game")
            else "media-playback-start-symbolic"
        )

    def schema_changed(self, _settings: Any, key: str) -> None:
        if key == "cover-launches-game":
            self.set_play_icon()

    @GObject.Signal(name="update-ready", arg_types=[object])
    def update_ready(self, _additional_data):  # type: ignore
        """Signal emitted when the game needs updating"""

    @GObject.Signal(name="save-ready", arg_types=[object])
    def save_ready(self, _additional_data):  # type: ignore
        """Signal emitted when the game needs saving"""
