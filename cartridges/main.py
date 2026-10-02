# main.py
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

import json
import logging
import lzma
import shlex
import sys
from time import time
from typing import Any, Optional
from urllib.parse import quote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

try:
    gi.require_version("Manette", "0.2")
    from gi.repository import Manette
except (ValueError, ImportError):
    Manette = None

# pylint: disable=wrong-import-position
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from cartridges import shared
from cartridges.details_dialog import DetailsDialog
from cartridges.game import Game
from cartridges.importer.bottles_source import BottlesSource
from cartridges.importer.desktop_source import DesktopSource
from cartridges.importer.dolphin_source import DolphinSource
from cartridges.importer.flatpak_source import FlatpakSource
from cartridges.importer.heroic_source import HeroicSource
from cartridges.importer.importer import Importer  # yo dawg
from cartridges.importer.itch_source import ItchSource
from cartridges.importer.legendary_source import LegendarySource
from cartridges.importer.lutris_source import LutrisSource
from cartridges.importer.retroarch_source import RetroarchSource
from cartridges.importer.steam_source import SteamSource
from cartridges.importer.yuzu_source import YuzuSource
from cartridges.logging.setup import log_system_info, setup_logging
from cartridges.preferences import CartridgesPreferences
from cartridges.store.managers.cover_manager import CoverManager
from cartridges.store.managers.display_manager import DisplayManager
from cartridges.store.managers.file_manager import FileManager
from cartridges.store.managers.sgdb_manager import SgdbManager
from cartridges.store.managers.steam_api_manager import SteamAPIManager
from cartridges.store.store import Store
from cartridges.utils import backup, session_fita, session_wallpaper
from cartridges.utils.install_size import InstallSizeSweep
from cartridges.utils.run_executable import run_executable
from cartridges.utils.single_instance import (
    acquire as acquire_single_instance,
    present_running_instance,
)
from cartridges.utils.news_checker import NewsChecker
from cartridges.utils.updates_checker import UpdatesChecker
from cartridges.window import CartridgesWindow

try:
    from cartridges.keyboard_emulator import KeyboardEmulator
except ImportError:
    KeyboardEmulator = None


class CartridgesApplication(Adw.Application):
    state = shared.AppState.DEFAULT
    win: CartridgesWindow
    init_search_term: Optional[str] = None
    keyboard_emulator = None
    updates_checker: Optional[UpdatesChecker] = None
    news_checker: Optional[NewsChecker] = None
    install_size_sweep: Optional[InstallSizeSweep] = None
    restauracao_falhou = False

    @GObject.Signal(name="emulate-key", arg_types=[int])
    def emulate_key(self, keyval) -> None:
        """Signal emitted when the app wants to emulate a keypress"""

    def gamepad_listen(self, device, *_args) -> None:
        logging.debug("Gamepad connected: %s", device.get_name())
        device.connect("button-press-event", self.gamepad_button_pressed)
        device.connect("hat-axis-event", self.gamepad_hat_axis)

    def gamepad_button_pressed(self, _device, event) -> None:
        logging.debug("Gamepad: %s pressed", (button := event.get_button()[1]))

        match button:
            case 304:  # A, confirm
                self.emit("emulate-key", Gdk.KEY_Return)
            case 305:  # B, back
                self.emit("emulate-key", Gdk.KEY_Escape)
            case 307:  # X
                logging.debug("Gamepad: X button pressed")
            case 308:  # Y
                logging.debug("Gamepad: Y button pressed")

    def gamepad_hat_axis(self, _device, event) -> None:
        hat = event.get_hat()
        logging.debug("Gamepad: hat axis: %s, value: %s", hat[1:3])

        if hat[2] != 0:
            self.navigate(hat[1] + hat[2])

    def navigate(self, direction: int) -> None:
        match direction:
            case 16:
                self.emit("emulate-key", Gdk.KEY_Up)
            case 18:
                self.emit("emulate-key", Gdk.KEY_Down)
            case 15:
                self.emit("emulate-key", Gdk.KEY_Left)
            case 17:
                self.emit("emulate-key", Gdk.KEY_Right)
            case _:
                logging.debug(
                    "Gamepad: unhandled navigation direction: %s", direction
                )

    def __init__(self) -> None:
        shared.store = Store()
        super().__init__(application_id=shared.APP_ID)

        search = GLib.OptionEntry()
        search.long_name = "search"
        search.short_name = ord("s")
        search.flags = 0
        search.arg = int(GLib.OptionArg.STRING)
        search.arg_data = None
        search.description = "Open the app with this term in the search entry"
        search.arg_description = "TERM"

        launch = GLib.OptionEntry()
        launch.long_name = "launch"
        launch.short_name = ord("l")
        launch.flags = int(GLib.OptionFlags.NONE)
        launch.arg = int(GLib.OptionArg.STRING)
        launch.arg_data = None
        launch.description = "Run a game with the given game_id"
        launch.arg_description = "GAME_ID"

        self.add_main_option_entries((search, launch))

        if sys.platform.startswith("darwin"):
            if settings := Gtk.Settings.get_default():
                settings.props.gtk_decoration_layout = "close,minimize,maximize:"

    def do_activate(self) -> None:  # pylint: disable=arguments-differ
        """Called on app creation"""

        try:
            setup_logging()
        except ValueError:
            pass

        log_system_info()

        self.restauracao_falhou = backup.aplicar_pendente() is False
        session_wallpaper.restaurar_orfaos()
        session_fita.abrir()

        # Setup gamepads
        if Manette is not None:
            manette_monitor = Manette.Monitor.new()
            manette_iter = manette_monitor.iterate()
            while (device := manette_iter.next())[0]:
                self.gamepad_listen(device[1])
        else:
            logging.debug("Manette not available, gamepad support disabled")

        if KeyboardEmulator is not None:
            self.keyboard_emulator = KeyboardEmulator(self)
        else:
            logging.debug("snegg not available, key emulation disabled")

        # Create the main window
        win = self.props.active_window  # pylint: disable=no-member
        if not win:
            shared.win = win = CartridgesWindow(application=self)

        # Save window geometry
        shared.state_schema.bind(
            "width", shared.win, "default-width", Gio.SettingsBindFlags.DEFAULT
        )
        shared.state_schema.bind(
            "height", shared.win, "default-height", Gio.SettingsBindFlags.DEFAULT
        )
        shared.state_schema.bind(
            "is-maximized", shared.win, "maximized", Gio.SettingsBindFlags.DEFAULT
        )

        # Load games from disk
        shared.store.add_manager(FileManager(), False)
        shared.store.add_manager(DisplayManager())
        self.state = shared.AppState.LOAD_FROM_DISK
        self.load_games_from_disk()
        self.state = shared.AppState.DEFAULT
        shared.win.create_source_rows()

        # Add rest of the managers for game imports
        shared.store.add_manager(CoverManager())
        shared.store.add_manager(SteamAPIManager())
        shared.store.add_manager(SgdbManager())
        shared.store.toggle_manager_in_pipelines(FileManager, True)

        # Create actions
        self.create_actions(
            {
                ("quit", ("<primary>q",)),
                ("about",),
                ("preferences", ("<primary>comma",)),
                ("launch_game",),
                ("hide_game",),
                ("edit_game",),
                ("add_game", ("<primary>n",)),
                ("import", ("<primary>i",)),
                ("remove_game_details_view", ("Delete",)),
                ("remove_game",),
                ("igdb_search",),
                ("sgdb_search",),
                ("protondb_search",),
                ("pcgw_search",),
                ("lutris_search",),
                ("hltb_search",),
                ("show_sidebar", ("F9",), shared.win),
                ("show_hidden", ("<primary>h",), shared.win),
                ("show_zerados", shared.win),
                ("show_news", shared.win),
                ("go_to_parent", ("<alt>Up",), shared.win),
                ("go_home", ("<alt>Home",), shared.win),
                ("toggle_search", ("<primary>f",), shared.win),
                ("undo", ("<primary>z",), shared.win),
                ("open_menu", ("F10",), shared.win),
                ("close", ("<primary>w",), shared.win),
            }
        )

        sort_action = Gio.SimpleAction.new_stateful(
            "sort_by",
            GLib.VariantType.new("s"),
            sort_mode := GLib.Variant("s", shared.state_schema.get_string("sort-mode")),
        )
        sort_action.connect("activate", shared.win.on_sort_action)
        shared.win.add_action(sort_action)
        shared.win.on_sort_action(sort_action, sort_mode)

        if self.init_search_term:  # For command line activation
            shared.win.search_bar.set_search_mode(True)
            shared.win.search_entry.set_text(self.init_search_term)
            shared.win.search_entry.set_position(-1)

        self.updates_checker = UpdatesChecker()
        self.updates_checker.start()
        self.news_checker = NewsChecker()
        shared.win.attach_news_checker(self.news_checker)
        shared.schema.connect("changed::show-news-button", self.on_show_news_changed)
        self.on_show_news_changed()
        self.install_size_sweep = InstallSizeSweep()
        self.install_size_sweep.start()

        shared.win.present()

        if shared.schema.get_boolean("auto-import"):
            self.on_import_action()

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if search := options.lookup_value("search"):
            self.init_search_term = search.get_string()
        elif game_id := options.lookup_value("launch"):
            try:
                data = json.load(
                    (path := shared.games_dir / (game_id.get_string() + ".json")).open(
                        "r", encoding="utf-8"
                    )
                )
                executable = (
                    shlex.join(data["executable"])
                    if isinstance(data["executable"], list)
                    else data["executable"]
                )
                name = data["name"]

                run_executable(executable)

                data["last_played"] = int(time())
                json.dump(data, path.open("w", encoding="utf-8"))

            except (IndexError, KeyError, OSError, json.decoder.JSONDecodeError):
                return 1

            self.register()
            self.send_notification(
                "launch", Gio.Notification.new(_("{} launched").format(name))
            )

            # Sleep for 6 seconds before withdrawing the notification
            # The amount a notification stays up is ~5, so leave an extra second for the animation
            GLib.usleep(6000000)
            self.withdraw_notification("launch")

            return 0
        return -1

    def load_games_from_disk(self) -> None:
        if shared.games_dir.is_dir():
            for game_file in shared.games_dir.iterdir():
                try:
                    data = json.load(game_file.open())
                except (OSError, json.decoder.JSONDecodeError):
                    continue
                game = Game(data)
                shared.store.add_game(game, {"skip_save": True})

    def get_source_name(self, source_id: str) -> Any:
        if source_id == "all":
            name = _("All Games")
        elif source_id == "imported":
            name = _("Added")
        else:
            try:
                name = globals()[f"{source_id.split('_')[0].title()}Source"].name
            except KeyError:
                return source_id
        return name

    def on_about_action(self, *_args: Any) -> None:
        # Get the debug info from the log files
        debug_str = ""
        for index, path in enumerate(shared.log_files):
            # Add a horizontal line between runs
            if index > 0:
                debug_str += "─" * 37 + "\n"
            # Add the run's logs
            log_file = (
                lzma.open(path, "rt", encoding="utf-8")
                if path.name.endswith(".xz")
                else open(path, "r", encoding="utf-8")
            )
            debug_str += log_file.read()
            log_file.close()

        about = Adw.AboutDialog.new_from_appdata(
            shared.PREFIX + "/" + shared.APP_ID + ".metainfo.xml", shared.VERSION
        )
        about.set_developers(
            (
                "kramo https://kramo.page",
                "Geoffrey Coulaud https://geoffrey-coulaud.fr",
                "Rilic https://rilic.red",
                "Arcitec https://github.com/Arcitec",
                "Paweł Lidwin https://github.com/imLinguin",
                "Domenico https://github.com/Domefemia",
                "Rafael Mardojai CM https://mardojai.com",
                "Clara Hobbs https://github.com/Ratfink",
                "Sabri Ünal https://github.com/sabriunal",
            )
        )
        about.set_designers(("kramo https://kramo.page",))
        about.set_copyright("© 2022-2024 kramo")
        # Translators: Replace this with Your Name, Your Name <your.email@example.com>, or Your Name https://your-site.com for it to show up in the About dialog.
        about.set_translator_credits(_("translator-credits"))
        about.set_debug_info(debug_str)
        about.set_debug_info_filename("cartridges.log")
        about.add_legal_section(
            "Steam Branding",
            "© 2023 Valve Corporation",
            Gtk.License.CUSTOM,
            "Steam and the Steam logo are trademarks and/or registered trademarks of Valve Corporation in the U.S. and/or other countries.",  # pylint: disable=line-too-long
        )
        about.present(shared.win)

    def on_preferences_action(
        self,
        _action: Any = None,
        _parameter: Any = None,
        page_name: Optional[str] = None,
        expander_row: Optional[str] = None,
    ) -> Optional[CartridgesPreferences]:
        if CartridgesPreferences.is_open:
            return

        win = CartridgesPreferences()
        if page_name:
            win.set_visible_page_name(page_name)
        if expander_row:
            getattr(win, expander_row).set_expanded(True)
        win.present(shared.win)

        return win

    def on_launch_game_action(self, *_args: Any) -> None:
        shared.win.active_game.launch()

    def on_hide_game_action(self, *_args: Any) -> None:
        shared.win.active_game.toggle_hidden()

    def on_edit_game_action(self, *_args: Any) -> None:
        DetailsDialog(shared.win.active_game).present(shared.win)

    def on_add_game_action(self, *_args: Any) -> None:
        if DetailsDialog.is_open:
            return

        DetailsDialog().present(shared.win)

    def on_import_action(self, *_args: Any) -> None:
        shared.importer = Importer()

        if shared.schema.get_boolean("lutris"):
            shared.importer.add_source(LutrisSource())

        if shared.schema.get_boolean("steam"):
            shared.importer.add_source(SteamSource())

        if shared.schema.get_boolean("heroic"):
            shared.importer.add_source(HeroicSource())

        if shared.schema.get_boolean("bottles"):
            shared.importer.add_source(BottlesSource())

        if shared.schema.get_boolean("dolphin"):
            shared.importer.add_source(DolphinSource())

        if shared.schema.get_boolean("flatpak"):
            shared.importer.add_source(FlatpakSource())

        if shared.schema.get_boolean("desktop"):
            shared.importer.add_source(DesktopSource())

        if shared.schema.get_boolean("itch"):
            shared.importer.add_source(ItchSource())

        if shared.schema.get_boolean("legendary"):
            shared.importer.add_source(LegendarySource())

        if shared.schema.get_boolean("retroarch"):
            shared.importer.add_source(RetroarchSource())

        if shared.schema.get_boolean("yuzu"):
            shared.importer.add_source(YuzuSource())

        shared.importer.run()

    def on_remove_game_action(self, *_args: Any) -> None:
        shared.win.active_game.remove_game()

    def on_remove_game_details_view_action(self, *_args: Any) -> None:
        if shared.win.navigation_view.get_visible_page() == shared.win.details_page:
            self.on_remove_game_action()

    def search(self, uri: str) -> None:
        Gio.AppInfo.launch_default_for_uri(f"{uri}{quote(shared.win.active_game.name)}")

    def on_igdb_search_action(self, *_args: Any) -> None:
        self.search("https://www.igdb.com/search?type=1&q=")

    def on_sgdb_search_action(self, *_args: Any) -> None:
        self.search("https://www.steamgriddb.com/search/grids?term=")

    def on_protondb_search_action(self, *_args: Any) -> None:
        self.search("https://www.protondb.com/search?q=")

    def on_pcgw_search_action(self, *_args: Any) -> None:
        self.search("https://www.pcgamingwiki.com/w/index.php?search=")

    def on_lutris_search_action(self, *_args: Any) -> None:
        self.search("https://lutris.net/games?q=")

    def on_hltb_search_action(self, *_args: Any) -> None:
        self.search("https://howlongtobeat.com/?q=")

    def on_show_news_changed(self, *_args: Any) -> None:
        if self.news_checker is None:
            return
        if shared.schema.get_boolean("show-news-button"):
            self.news_checker.start()
        else:
            self.news_checker.stop()

    def on_quit_action(self, *_args: Any) -> None:
        if self.news_checker is not None:
            self.news_checker.stop()
        if self.updates_checker is not None:
            self.updates_checker.stop()
        session_wallpaper.restaurar()
        session_fita.fechar()
        self.quit()

    def create_actions(self, actions: set) -> None:
        for action in actions:
            simple_action = Gio.SimpleAction.new(action[0], None)

            scope = action[2] if action[2:3] else self
            simple_action.connect("activate", getattr(scope, f"on_{action[0]}_action"))

            if action[1:2]:
                self.set_accels_for_action(
                    f"app.{action[0]}" if scope == self else f"win.{action[0]}",
                    (
                        tuple(s.replace("<primary>", "<meta>") for s in action[1])
                        if sys.platform.startswith("darwin")
                        else action[1]
                    ),
                )

            scope.add_action(simple_action)


def main(_version: int) -> Any:
    """App entry point"""
    # Before anything is loaded: a second copy must not read the game files
    # at all, let alone save over them. GApplication's own uniqueness check
    # runs over the D-Bus session bus, which may not exist on all platforms.
    if not acquire_single_instance():
        logging.warning("Cartridges is already running")
        present_running_instance()
        return 0

    app = CartridgesApplication()
    return app.run(sys.argv)
