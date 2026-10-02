# details_dialog.py
#
# Copyright 2022-2024 kramo
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

# pyright: reportAssignmentType=none

import shlex
from pathlib import Path
from sys import platform
from time import time
from typing import Any, Optional

from gi.repository import Adw, Gio, GLib, Gtk
from PIL import Image, UnidentifiedImageError

from cartridges import shared
from cartridges.errors.friendly_error import FriendlyError
from cartridges.game import Game, STATUS_LABELS
from cartridges.game_cover import GameCover
from cartridges.store.managers.cover_manager import CoverManager
from cartridges.store.managers.sgdb_manager import SgdbManager
from cartridges.utils.create_dialog import create_dialog
from cartridges.utils.save_cover import convert_cover, save_cover
from cartridges.utils.steam import format_release_date


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/details-dialog.ui")
class DetailsDialog(Adw.Dialog):
    __gtype_name__ = "DetailsDialog"

    cover_overlay: Gtk.Overlay = Gtk.Template.Child()
    cover: Gtk.Picture = Gtk.Template.Child()
    cover_button_edit: Gtk.Button = Gtk.Template.Child()
    cover_button_delete_revealer: Gtk.Revealer = Gtk.Template.Child()
    cover_button_delete: Gtk.Button = Gtk.Template.Child()
    spinner: Adw.Spinner = Gtk.Template.Child()

    name: Adw.EntryRow = Gtk.Template.Child()
    developer: Adw.EntryRow = Gtk.Template.Child()
    publisher: Adw.EntryRow = Gtk.Template.Child()
    release_date: Adw.EntryRow = Gtk.Template.Child()
    genre: Adw.EntryRow = Gtk.Template.Child()
    controller_support: Adw.ComboRow = Gtk.Template.Child()
    status: Adw.ComboRow = Gtk.Template.Child()
    rating_row: Adw.ActionRow = Gtk.Template.Child()
    rating_box: Gtk.Box = Gtk.Template.Child()
    executable: Adw.EntryRow = Gtk.Template.Child()
    track_updates_switch: Adw.SwitchRow = Gtk.Template.Child()
    logo_row: Adw.ActionRow = Gtk.Template.Child()
    logo_button_reset: Gtk.Button = Gtk.Template.Child()
    logo_button_browse: Gtk.Button = Gtk.Template.Child()
    logo_button_file: Gtk.Button = Gtk.Template.Child()
    wallpaper_row: Adw.ActionRow = Gtk.Template.Child()
    wallpaper_button_reset: Gtk.Button = Gtk.Template.Child()
    wallpaper_button_browse: Gtk.Button = Gtk.Template.Child()
    wallpaper_button_file: Gtk.Button = Gtk.Template.Child()
    fita_row: Adw.ActionRow = Gtk.Template.Child()
    fita_button_reset: Gtk.Button = Gtk.Template.Child()
    fita_cor_menu: Gtk.MenuButton = Gtk.Template.Child()
    fita_amostra: Gtk.DrawingArea = Gtk.Template.Child()
    fita_color_button: Gtk.ColorChooserWidget = Gtk.Template.Child()
    fita_brilho_row: Adw.SpinRow = Gtk.Template.Child()

    exec_info_label: Gtk.Label = Gtk.Template.Child()
    exec_info_popover: Gtk.Popover = Gtk.Template.Child()
    file_chooser_button: Gtk.Button = Gtk.Template.Child()

    apply_button: Gtk.Button = Gtk.Template.Child()

    cover_changed: bool = False

    is_open: bool = False

    _rating: int = 0

    def __init__(self, game: Optional[Game] = None, **kwargs: Any):
        super().__init__(**kwargs)

        # Make it so only one dialog can be open at a time
        self.__class__.is_open = True
        self.tmp_cover_path = None
        self.connect("closed", self.on_closed)

        self.game: Optional[Game] = game
        self.game_cover: GameCover = GameCover({self.cover})

        self.status.set_model(
            Gtk.StringList.new([_("Sem status"), *STATUS_LABELS.values()])
        )

        self.rating_buttons: list[Gtk.Button] = []
        for value in range(1, 6):
            button = Gtk.Button(
                valign=Gtk.Align.CENTER,
                tooltip_text=(
                    f"{value} estrela" if value == 1 else f"{value} estrelas"
                ),
            )
            button.add_css_class("flat")
            button.connect("clicked", self.on_star_clicked, value)
            self.rating_box.append(button)
            self.rating_buttons.append(button)

        if self.game:
            self.set_title(_("Game Details"))
            self.name.set_text(self.game.name)
            if self.game.developer:
                self.developer.set_text(self.game.developer)
            if self.game.publisher:
                self.publisher.set_text(self.game.publisher)
            if self.game.release_date:
                self.release_date.set_text(self.game.release_date)
            if self.game.genre:
                self.genre.set_text(self.game.genre)
            self.set_controller_support(self.game.controller_support)
            self.set_status(self.game.status)
            self._rating = self.game.stars
            self.executable.set_text(self.game.executable)
            self.track_updates_switch.set_active(self.game.track_updates)
            self.update_rating_stars()
            self.apply_button.set_label(_("Apply"))

            self.game_cover.new_cover(self.game.get_cover_path())
            if self.game_cover.get_texture():
                self.cover_button_delete_revealer.set_reveal_child(True)
        else:
            self.set_title(_("Add New Game"))
            self.apply_button.set_label(_("Add"))

        image_filter = Gtk.FileFilter(name=_("Images"))

        # .palm and .pdf are write-only
        for extension in set(Image.registered_extensions()) - {".palm", ".pdf"}:
            image_filter.add_suffix(extension[1:])

        image_filter.add_suffix("svg")  # Gdk.Texture supports .svg but PIL doesn't

        image_filters = Gio.ListStore.new(Gtk.FileFilter)
        image_filters.append(image_filter)

        exec_filter = Gtk.FileFilter(name=_("Executables"))
        exec_filter.add_mime_type("application/x-executable")

        exec_filters = Gio.ListStore.new(Gtk.FileFilter)
        exec_filters.append(exec_filter)

        self.image_file_dialog = Gtk.FileDialog()
        self.image_file_dialog.set_filters(image_filters)
        self.image_file_dialog.set_default_filter(image_filter)

        self.exec_file_dialog = Gtk.FileDialog()
        self.exec_file_dialog.set_filters(exec_filters)
        self.exec_file_dialog.set_default_filter(exec_filter)

        # Translate this string as you would translate "file"
        file_name = _("file.txt")
        # As in software
        exe_name = _("program")

        if platform == "win32":
            exe_name += ".exe"
            # Translate this string as you would translate "path to {}"
            exe_path = _("C:\\path\\to\\{}").format(exe_name)
            # Translate this string as you would translate "path to {}"
            file_path = _("C:\\path\\to\\{}").format(file_name)
            command = "start"
        else:
            # Translate this string as you would translate "path to {}"
            exe_path = _("/path/to/{}").format(exe_name)
            # Translate this string as you would translate "path to {}"
            file_path = _("/path/to/{}").format(file_name)
            command = "open" if platform == "darwin" else "xdg-open"

        # pylint: disable=line-too-long
        exec_info_text = _(
            'To launch the executable "{}", use the command:\n\n<tt>"{}"</tt>\n\nTo open the file "{}" with the default application, use:\n\n<tt>{} "{}"</tt>\n\nIf the path contains spaces, make sure to wrap it in double quotes!'
        ).format(exe_name, exe_path, file_name, command, file_path)

        self.exec_info_label.set_label(exec_info_text)

        self.exec_info_popover.update_property(
            (Gtk.AccessibleProperty.LABEL,),
            (
                exec_info_text.replace("<tt>", "").replace("</tt>", ""),
            ),  # Remove formatting, else the screen reader reads it
        )

        def set_exec_info_a11y_label(*_args: Any) -> None:
            self.set_focus(self.exec_info_popover)

        self.exec_info_popover.connect("show", set_exec_info_a11y_label)

        self.cover_button_delete.connect("clicked", self.delete_pixbuf)
        self.cover_button_edit.connect("clicked", self.choose_cover)
        self.file_chooser_button.connect("clicked", self.choose_executable)
        self.apply_button.connect("clicked", self.apply_preferences)

        self.name.connect("entry-activated", self.focus_executable)
        self.developer.connect("entry-activated", self.focus_executable)
        self.executable.connect("entry-activated", self.apply_preferences)

        self.set_focus(self.name)

    def delete_pixbuf(self, *_args: Any) -> None:
        if self.tmp_cover_path:
            self.tmp_cover_path.unlink(missing_ok=True)

        self.game_cover.new_cover()

        self.cover_button_delete_revealer.set_reveal_child(False)
        self.cover_changed = True

    def on_closed(self, *args):
        if self.tmp_cover_path:
            self.tmp_cover_path.unlink(missing_ok=True)

        self.set_is_open(False)

    def apply_preferences(self, *_args: Any) -> None:
        final_name = self.name.get_text()
        final_developer = self.developer.get_text()
        final_publisher = self.publisher.get_text()
        typed_release_date = self.release_date.get_text().strip()
        final_release_date = format_release_date(typed_release_date)
        final_genre = self.genre.get_text().strip()
        final_controller_support = self.get_controller_support()
        final_status = self.get_status()
        final_executable = self.executable.get_text()

        if not self.game:
            if final_name == "":
                create_dialog(
                    self, _("Couldn't Add Game"), _("Game title cannot be empty.")
                )
                return

            if final_executable == "":
                create_dialog(
                    self, _("Couldn't Add Game"), _("Executable cannot be empty.")
                )
                return

            # Increment the number after the game id (eg. imported_1, imported_2)
            source_id = "imported"
            numbers = [0]
            game_id: str
            for game_id in shared.store.source_games.get(source_id, set()):
                prefix = "imported_"
                if not game_id.startswith(prefix):
                    continue
                numbers.append(int(game_id.replace(prefix, "", 1)))

            game_number = max(numbers) + 1

            self.game = Game(
                {
                    "game_id": f"imported_{game_number}",
                    "hidden": False,
                    "source": source_id,
                    "added": int(time()),
                }
            )

            if shared.win.sidebar.get_selected_row().get_child() not in (
                shared.win.all_games_row_box,
                shared.win.added_row_box,
            ):
                shared.win.sidebar.select_row(shared.win.added_row_box.get_parent())

        else:
            if final_name == "":
                create_dialog(
                    self,
                    _("Couldn't Apply Preferences"),
                    _("Game title cannot be empty."),
                )
                return

            if final_executable == "":
                create_dialog(
                    self,
                    _("Couldn't Apply Preferences"),
                    _("Executable cannot be empty."),
                )
                return

        if typed_release_date and not final_release_date:
            create_dialog(
                self,
                _("Couldn't Apply Preferences"),
                _("Release date not recognized."),
            )
            return

        self.game.name = final_name
        self.game.developer = final_developer or None
        self.game.publisher = final_publisher or None
        self.game.release_date = final_release_date
        self.game.genre = final_genre or None
        self.game.controller_support = final_controller_support
        self.game.definir_status(final_status)
        self.game.rating = self._rating
        self.game.executable = final_executable

        if self.game.game_id in shared.win.game_covers.keys():
            shared.win.game_covers[self.game.game_id].animation = None

        shared.win.game_covers[self.game.game_id] = self.game_cover

        if self.cover_changed:
            save_cover(
                self.game.game_id,
                self.game_cover.path,
                self.game_cover.pixbuf,
            )

        track_updates = self.track_updates_switch.get_active()
        just_enabled = track_updates and not self.game.track_updates
        if not track_updates:
            self.game.update_available_ts = 0
            self.game.update_url = ""
        self.game.track_updates = track_updates

        shared.store.add_game(self.game, {}, run_pipeline=False)
        self.game.save()
        self.game.update()

        if just_enabled:
            app = shared.win.get_application()
            checker = getattr(app, "updates_checker", None)
            if checker is not None:
                checker.check_async()

        # TODO: this is fucked up (less than before)
        # Get a cover from SGDB if none is present
        if not self.game_cover.get_texture():
            self.game.set_loading(1)
            sgdb_manager = shared.store.managers[SgdbManager]
            sgdb_manager.reset_cancellable()
            sgdb_manager.process_game(self.game, {}, self.update_cover_callback)

        self.game_cover.pictures.remove(self.cover)

        self.close()
        shared.win.show_details_page(self.game)

    def update_cover_callback(self, manager: SgdbManager) -> None:
        # Set the game as not loading
        self.game.set_loading(-1)
        self.game.update()

        # Handle errors that occured
        for error in manager.collect_errors():
            # On auth error, inform the user
            if isinstance(error, FriendlyError):
                create_dialog(
                    shared.win,
                    error.title,
                    error.subtitle,
                    "open_preferences",
                    _("Preferences"),
                ).connect("response", self.update_cover_error_response)

    def update_cover_error_response(self, _widget: Any, response: str) -> None:
        if response == "open_preferences":
            shared.win.get_application().on_preferences_action(page_name="sgdb")

    def focus_executable(self, *_args: Any) -> None:
        self.set_focus(self.executable)

    CONTROLLER_POSITIONS = {None: 0, "full": 1, "partial": 2}
    CONTROLLER_VALUES = {0: None, 1: "full", 2: "partial"}

    def set_controller_support(self, value: Optional[str]) -> None:
        self.controller_support.set_selected(self.CONTROLLER_POSITIONS.get(value, 0))

    def get_controller_support(self) -> Optional[str]:
        return self.CONTROLLER_VALUES.get(self.controller_support.get_selected())

    STATUS_VALUES = ("", *STATUS_LABELS)

    def set_status(self, value: str) -> None:
        try:
            self.status.set_selected(self.STATUS_VALUES.index(value or ""))
        except ValueError:
            self.status.set_selected(0)

    def get_status(self) -> str:
        selected = self.status.get_selected()
        if selected >= len(self.STATUS_VALUES):
            return ""
        return self.STATUS_VALUES[selected]

    def on_star_clicked(self, _widget: Any, value: int) -> None:
        self._rating = 0 if self._rating == value else value
        self.update_rating_stars()

    def update_rating_stars(self) -> None:
        for index, button in enumerate(self.rating_buttons, start=1):
            button.set_icon_name(
                "starred-symbolic" if index <= self._rating else "non-starred-symbolic"
            )

    def toggle_loading(self) -> None:
        self.apply_button.set_sensitive(not self.apply_button.get_sensitive())
        self.spinner.set_visible(not self.spinner.get_visible())
        self.cover_overlay.set_opacity(not self.cover_overlay.get_opacity())

    def set_cover(self, _source: Any, result: Gio.Task, *_args: Any) -> None:
        try:
            path = self.image_file_dialog.open_finish(result).get_path()
        except GLib.Error:
            return

        def thread_func() -> None:
            is_animated = False

            try:
                with Image.open(path) as image:
                    if getattr(image, "is_animated", False):
                        is_animated = True
            except (UnidentifiedImageError, OSError, ValueError):
                pass

            if is_animated:
                if self.tmp_cover_path:
                    self.tmp_cover_path.unlink(missing_ok=True)
                self.tmp_cover_path = convert_cover(path)
                self.game_cover.new_cover(self.tmp_cover_path)
            else:
                self.game_cover.new_cover(
                    pixbuf=shared.store.managers[CoverManager].composite_cover(
                        Path(path)
                    )
                )

            self.cover_button_delete_revealer.set_reveal_child(True)
            self.cover_changed = True

            self.toggle_loading()

        self.toggle_loading()
        GLib.Thread.new(None, thread_func)

    def set_executable(self, _source: Any, result: Gio.Task, *_args: Any) -> None:
        try:
            path = self.exec_file_dialog.open_finish(result).get_path()
        except GLib.Error:
            return

        self.executable.set_text(shlex.quote(path))

    def choose_executable(self, *_args: Any) -> None:
        self.exec_file_dialog.open(self.get_root(), None, self.set_executable)

    def choose_cover(self, *_args: Any) -> None:
        self.image_file_dialog.open(self.get_root(), None, self.set_cover)

    def set_is_open(self, is_open: bool) -> None:
        self.__class__.is_open = is_open
