# main.py
#
# Copyright 2022-2024 redclaw
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
import os
import shlex
import subprocess
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
from cartridges.controller import (
    ControllerAction,
    action_for_button,
    hat_direction,
    stick_direction,
    trigger_for_button,
)
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
from cartridges.importer.twintail_source import TwintailSource
from cartridges.importer.waydroid_source import WaydroidSource
from cartridges.importer.yuzu_source import YuzuSource
from cartridges.logging.setup import log_system_info, setup_logging
from cartridges.power import PowerManager
from cartridges.preferences import CartridgesPreferences
from cartridges.runtime import RuntimeContext
from cartridges.user_profile import current_user_profile
from cartridges.store.managers.cover_manager import CoverManager
from cartridges.store.managers.display_manager import DisplayManager
from cartridges.store.managers.file_manager import FileManager
from cartridges.store.managers.sgdb_manager import SgdbManager
from cartridges.store.managers.steam_api_manager import SteamAPIManager
from cartridges.store.managers.sunshine_manager import SunshineManager
from cartridges.store.managers.thegamesdb_manager import TheGamesDBManager
from cartridges.store.store import Store
from cartridges.utils import backup, session_fita, session_wallpaper
from cartridges.utils.install_size import InstallSizeSweep
from cartridges.utils.run_executable import run_executable
from cartridges.utils.single_instance import (
    acquire as acquire_single_instance,
    present_running_instance,
    release as release_single_instance,
)
from cartridges.utils.news_checker import NewsChecker
from cartridges.utils.updates_checker import UpdatesChecker
from cartridges.window import CartridgesWindow


def _exit_failed_game_session(
    exception_type: type[BaseException],
    exception: BaseException,
    traceback: Any,
) -> None:
    """Return an unusable dedicated session to the display manager.

    PyGObject reports exceptions raised by signal callbacks through
    ``sys.excepthook`` and then keeps the GTK main loop alive.  In a Gamescope
    login session that leaves only the compositor's grey background on screen.
    Desktop mode keeps Python's normal behaviour, but a dedicated session must
    fail closed so Gamescope and GDM can tear it down.
    """
    sys.__excepthook__(exception_type, exception, traceback)
    os._exit(1)  # pylint: disable=protected-access


class CartridgesApplication(Adw.Application):
    state = shared.AppState.DEFAULT
    win: CartridgesWindow
    init_search_term: Optional[str] = None
    keyboard_emulator = None
    updates_checker: Optional[UpdatesChecker] = None
    news_checker: Optional[NewsChecker] = None
    install_size_sweep: Optional[InstallSizeSweep] = None
    restauracao_falhou = False
    reiniciar = False
    _gamepad_devices: dict[int, str]

    @GObject.Signal(name="emulate-key", arg_types=[int])
    def emulate_key(self, keyval) -> None:
        """Signal emitted when the app wants to emulate a keypress"""

    def gamepad_listen(self, monitor_or_device, device=None, *_args) -> None:
        device = device or monitor_or_device
        name = device.get_name() or _("Controle desconhecido")
        logging.debug("Gamepad connected: %s", name)
        self._gamepad_devices[id(device)] = name
        device.connect("button-press-event", self.gamepad_button_pressed)
        device.connect("button-release-event", self.gamepad_button_released)
        device.connect("hat-axis-event", self.gamepad_hat_axis)
        device.connect("absolute-axis-event", self.gamepad_absolute_axis)
        self.update_gamepad_devices()

    def gamepad_disconnected(self, _monitor, device) -> None:
        logging.debug("Gamepad disconnected: %s", device.get_name())
        self._gamepad_devices.pop(id(device), None)
        self.update_gamepad_devices()

    def update_gamepad_devices(self) -> None:
        if getattr(shared, "win", None) is not None:
            shared.win.set_gamepad_devices(list(self._gamepad_devices.values()))

    def gamepad_button_pressed(self, _device, event) -> None:
        logging.debug("Gamepad: %s pressed", (button := event.get_button()[1]))

        if trigger := trigger_for_button(button):
            shared.win.update_gamepad_trigger(trigger, True)
            return
        shared.win.update_gamepad_button(button, True)
        if shared.win.gamepad_test_active:
            if action_for_button(button) == ControllerAction.BACK:
                shared.win.gamepad_back()
            return

        match action_for_button(button):
            case ControllerAction.CONFIRM:
                shared.win.gamepad_confirm()
            case ControllerAction.BACK:
                shared.win.gamepad_back()
            case ControllerAction.GAME_MENU:
                shared.win.gamepad_game_menu()
            case ControllerAction.SEARCH:
                shared.win.gamepad_search()
            case ControllerAction.MAIN_MENU:
                shared.win.gamepad_main_menu()
            case ControllerAction.GUIDE:
                shared.win.gamepad_toggle_sidebar()

    def gamepad_button_released(self, _device, event) -> None:
        button = event.get_button()[1]
        logging.debug("Gamepad: %s released", button)
        if trigger := trigger_for_button(button):
            shared.win.update_gamepad_trigger(trigger, False)
            return
        shared.win.update_gamepad_button(button, False)

    def gamepad_hat_axis(self, _device, event) -> None:
        hat = event.get_hat()
        logging.debug("Gamepad: hat axis: %s, value: %s", hat[1:3])

        shared.win.update_gamepad_hat(hat[1], hat[2])

        if hat_direction(hat[1], hat[2]) is not None:
            self.navigate(hat[1] + hat[2])

    def gamepad_absolute_axis(self, _device, event) -> None:
        """Turn the left stick into one keypress per threshold crossing."""
        absolute = event.get_absolute()
        if not absolute[0]:
            return
        axis, value = absolute[1], absolute[2]
        shared.win.update_gamepad_axis(axis, value)
        if axis not in (0, 1):
            return
        previous = getattr(self, "_gamepad_axis", {}).get(axis, 0)
        direction = stick_direction(value)
        if not hasattr(self, "_gamepad_axis"):
            self._gamepad_axis = {}
        self._gamepad_axis[axis] = direction
        if direction == 0 or direction == previous:
            return
        if axis == 0:
            key = Gdk.KEY_Left if direction < 0 else Gdk.KEY_Right
        else:
            key = Gdk.KEY_Up if direction < 0 else Gdk.KEY_Down
        shared.win.gamepad_navigate(
            Gtk.DirectionType.LEFT
            if key == Gdk.KEY_Left
            else Gtk.DirectionType.RIGHT
            if key == Gdk.KEY_Right
            else Gtk.DirectionType.UP
            if key == Gdk.KEY_Up
            else Gtk.DirectionType.DOWN
        )

    def navigate(self, direction: int) -> None:
        match direction:
            case 16:
                shared.win.gamepad_navigate(Gtk.DirectionType.UP)
            case 18:
                shared.win.gamepad_navigate(Gtk.DirectionType.DOWN)
            case 15:
                shared.win.gamepad_navigate(Gtk.DirectionType.LEFT)
            case 17:
                shared.win.gamepad_navigate(Gtk.DirectionType.RIGHT)
            case _:
                logging.debug(
                    "Gamepad: unhandled navigation direction: %s", direction
                )

    def __init__(self) -> None:
        shared.store = Store()
        shared.runtime = RuntimeContext.detect(sys.argv, os.environ)
        self._gamepad_devices = {}
        flags = (
            Gio.ApplicationFlags.NON_UNIQUE
            if shared.runtime.is_nested
            else Gio.ApplicationFlags(0)
        )
        super().__init__(application_id=shared.APP_ID, flags=flags)

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

        mode_options = []
        for name, description in (
            ("game-mode", "Run with the console-oriented interface"),
            ("jolven-session", "Alias for --game-mode"),
            ("session", "Run as the frontend of a dedicated graphical session"),
            ("windowed", "Keep console mode windowed for development"),
            ("nested", "Alias for --windowed"),
        ):
            option = GLib.OptionEntry()
            option.long_name = name
            option.flags = int(GLib.OptionFlags.NONE)
            option.arg = int(GLib.OptionArg.NONE)
            option.arg_data = None
            option.description = description
            mode_options.append(option)

        self.add_main_option_entries((search, launch, *mode_options))

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
        from cartridges.utils import app_icon

        app_icon.ensure()

        # Setup gamepads
        if Manette is not None:
            manette_monitor = Manette.Monitor.new()
            self.manette_monitor = manette_monitor
            manette_monitor.connect("device-connected", self.gamepad_listen)
            manette_monitor.connect("device-disconnected", self.gamepad_disconnected)
            manette_iter = manette_monitor.iterate()
            gamepad_count = 0
            while (device := manette_iter.next())[0]:
                self.gamepad_listen(device[1])
                gamepad_count += 1
            if gamepad_count == 0:
                logging.info("No gamepad connected; waiting for hotplug")
        else:
            logging.debug("Manette not available, gamepad support disabled")

        # Create the main window
        win = self.props.active_window  # pylint: disable=no-member
        if not win:
            shared.win = win = CartridgesWindow(application=self)
        self.update_gamepad_devices()

        if shared.runtime.is_game_mode:
            win.add_css_class("game-mode")
            profile = current_user_profile()
            win.sidebar_navigation_page.set_title(
                _("Jolven — {}").format(profile.display_name)
            )
            win.configure_user_profile(profile)

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
        shared.store.add_manager(TheGamesDBManager())
        shared.store.add_manager(SgdbManager())
        shared.store.add_manager(SunshineManager())
        shared.store.toggle_manager_in_pipelines(FileManager, True)

        if shared.schema.get_boolean("xbox-cloud-gaming"):
            from cartridges.xcloud_catalog import sync_async

            sync_async()

        # Create actions
        self.create_actions(
            {
                ("quit", ("<primary>q",)),
                ("about",),
                ("preferences", ("<primary>comma",)),
                ("sunshine",),
                ("launch_game",),
                ("hide_game",),
                ("edit_game",),
                ("add_to_sunshine",),
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
                ("show_zerados", (), shared.win),
                ("show_news", (), shared.win),
                ("open_xcloud", (), shared.win),
                ("close_xcloud", (), shared.win),
                ("reload_xcloud", (), shared.win),
                ("toggle_xcloud_fullscreen", ("F11",), shared.win),
                ("xcloud_escape", (), shared.win),
                ("go_to_parent", ("<alt>Up",), shared.win),
                ("go_home", ("<alt>Home",), shared.win),
                ("toggle_search", ("<primary>f",), shared.win),
                ("undo", ("<primary>z",), shared.win),
                ("open_menu", ("F10",), shared.win),
                ("close", ("<primary>w",), shared.win),
                ("open_library", (), shared.win),
                ("open_gamepad_test", (), shared.win),
                ("power_menu",),
                ("continue_game",),
                ("install_game_mode",),
                ("uninstall_game_mode",),
            }
        )
        power_action = self.lookup_action("power_menu")
        if power_action is not None:
            power_action.set_enabled(shared.runtime.is_game_mode)
        self._game_mode_setup_process = None
        self.refresh_game_mode_setup_actions()

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
        shared.win.update_game_mode_home()

        shared.win.present()
        if shared.runtime.is_session:
            shared.win.fullscreen()
        if shared.runtime.is_game_mode:
            shared.win.focus_game_mode_home()

        if self.restauracao_falhou:
            shared.win.toast_queue.add(
                Adw.Toast.new(_("Não foi possível restaurar o backup"))
            )

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
        elif source_id.split("_")[0] == "xcloud":
            name = _("Xbox Cloud Gaming")
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
        # A versão exibida é sempre a da compilação (YY.DDD.HHMM, Fixes #6),
        # não o release mais recente do metainfo.xml.
        about.set_version(shared.VERSION)
        about.set_developers(
            (
                "Jolven contributors https://github.com/zonaro/jolven",
                "redclaw https://redclaw.page",
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
        about.set_designers(("redclaw https://redclaw.page",))
        about.set_copyright("© 2022-2024 redclaw, © 2026 Jolven contributors")
        # Translators: Replace this with Your Name, Your Name <your.email@example.com>, or Your Name https://your-site.com for it to show up in the About dialog.
        about.set_translator_credits(_("translator-credits"))
        about.set_debug_info(debug_str)
        about.set_debug_info_filename("jolven.log")
        about.set_website("https://github.com/zonaro/jolven")
        about.set_issue_url("https://github.com/zonaro/jolven/issues")
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

    def on_sunshine_action(self, *_args: Any) -> None:
        self.on_preferences_action(page_name="sunshine")

    def on_launch_game_action(self, *_args: Any) -> None:
        shared.win.launch_library_game(shared.win.active_game)

    def on_continue_game_action(self, *_args: Any) -> None:
        games = [
            game
            for game in shared.store
            if not game.removed and game.executable and game.last_played > 0
        ]
        if recent := max(games, key=lambda game: game.last_played, default=None):
            recent.launch()

    def on_hide_game_action(self, *_args: Any) -> None:
        shared.win.active_game.toggle_hidden()

    def on_edit_game_action(self, *_args: Any) -> None:
        DetailsDialog(shared.win.active_game).present(shared.win)

    def on_add_to_sunshine_action(self, *_args: Any) -> None:
        from cartridges.utils import sunshine

        game = shared.win.active_game
        if game is None:
            return
        if not sunshine.is_eligible(game):
            game.create_toast(_("This game cannot be added to Sunshine"))
            return
        try:
            sunshine.add_game(
                game, shared.schema.get_string("sunshine-apps-path")
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logging.exception("Could not export %s to Sunshine", game.game_id)
            game.create_toast(_("Could not add to Sunshine"))
            return
        game.create_toast(_("{} added to Sunshine").format(game.name))

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

        if shared.schema.get_boolean("twintail"):
            shared.importer.add_source(TwintailSource())

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
        self.quit()

    def do_shutdown(self) -> None:  # pylint: disable=arguments-differ
        if shared.runtime.is_session:
            from cartridges.process_session import ProcessSession

            if ProcessSession.active is not None:
                ProcessSession.active.shutdown()
        if self.news_checker is not None:
            self.news_checker.stop()
        if self.updates_checker is not None:
            self.updates_checker.stop()
        session_wallpaper.restaurar()
        session_fita.fechar()
        Gio.Application.do_shutdown(self)

    def on_power_menu_action(self, *_args: Any) -> None:
        if not shared.runtime.is_game_mode:
            return
        dialog = Adw.AlertDialog.new(
            _("Jolven Session"),
            _("Choose a session or power action."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("logout", _("Exit Jolven Session"))
        dialog.add_response("library", _("Back to Library"))
        from cartridges.process_session import ProcessSession

        if ProcessSession.active is not None:
            dialog.add_response("stop_game", _("Close Current Game"))
        try:
            power = PowerManager()
        except GLib.Error:
            power = None
        if power is not None and power.available("suspend"):
            dialog.add_response("suspend", _("Suspend"))
        if power is not None and power.available("reboot"):
            dialog.add_response("reboot", _("Restart"))
        if power is not None and power.available("poweroff"):
            dialog.add_response("poweroff", _("Shut Down"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        if power is not None and power.available("poweroff"):
            dialog.set_response_appearance(
                "poweroff", Adw.ResponseAppearance.DESTRUCTIVE
            )
        dialog.connect("response", self._on_power_response)
        dialog.present(shared.win)

    def _on_power_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response == "logout":
            self.on_quit_action()
        elif response == "library":
            shared.win.on_open_library_action()
        elif response == "stop_game":
            from cartridges.process_session import ProcessSession

            if ProcessSession.active is not None:
                ProcessSession.active.terminate_game()
        elif response in {"suspend", "reboot", "poweroff"}:
            try:
                PowerManager().invoke(response)
            except GLib.Error as error:
                logging.error("Could not contact systemd-logind: %s", error.message)
                shared.win.toast_queue.add(
                    Adw.Toast.new(_("The power action could not be completed"))
                )

    def refresh_game_mode_setup_actions(self) -> None:
        """Show only the install or uninstall action that currently applies."""
        available = (
            not os.getenv("FLATPAK_ID")
            and shared.GAME_MODE_HELPER.is_file()
            and os.access(shared.GAME_MODE_HELPER, os.X_OK)
            and self._game_mode_setup_process is None
        )
        installed = shared.GAME_SESSION_FILE.is_file()
        install = self.lookup_action("install_game_mode")
        uninstall = self.lookup_action("uninstall_game_mode")
        if install is not None:
            install.set_enabled(available and not installed)
        if uninstall is not None:
            uninstall.set_enabled(available and installed)

    def on_install_game_mode_action(self, *_args: Any) -> None:
        self._run_game_mode_setup("install")

    def on_uninstall_game_mode_action(self, *_args: Any) -> None:
        self._run_game_mode_setup("uninstall")

    def _run_game_mode_setup(self, operation: str) -> None:
        if self._game_mode_setup_process is not None:
            return
        try:
            process = Gio.Subprocess.new(
                ["pkexec", str(shared.GAME_MODE_HELPER), operation],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
            )
        except GLib.Error as error:
            logging.error("Could not start Jolven Session setup: %s", error.message)
            shared.win.toast_queue.add(
                Adw.Toast.new(_("Could not open the system authorization dialog"))
            )
            return

        self._game_mode_setup_process = process
        self.refresh_game_mode_setup_actions()
        shared.win.toast_queue.add(
            Adw.Toast.new(
                _("Installing Jolven Session…")
                if operation == "install"
                else _("Uninstalling Jolven Session…")
            )
        )
        process.communicate_utf8_async(
            None, None, self._on_game_mode_setup_finished, operation
        )

    def _on_game_mode_setup_finished(
        self, process: Gio.Subprocess, result: Gio.AsyncResult, operation: str
    ) -> None:
        try:
            _ok, stdout, stderr = process.communicate_utf8_finish(result)
            successful = process.get_successful()
        except GLib.Error as error:
            stdout, stderr, successful = "", error.message, False
        self._game_mode_setup_process = None
        self.refresh_game_mode_setup_actions()
        if successful:
            message = (
                _("Jolven Session installed. It will appear at the next login.")
                if operation == "install"
                else _("Jolven Session uninstalled")
            )
            logging.info("Jolven Session setup completed: %s", (stdout or "").strip())
        else:
            message = _("Jolven Session setup was not completed")
            logging.error("Jolven Session setup failed: %s", (stderr or "").strip())
        toast = Adw.Toast.new(message)
        toast.set_priority(Adw.ToastPriority.HIGH)
        shared.win.toast_queue.add(toast)

    def create_actions(self, actions: set) -> None:
        for action in actions:
            simple_action = Gio.SimpleAction.new(action[0], None)

            scope = action[2] if action[2:3] else self
            simple_action.connect("activate", getattr(scope, f"on_{action[0]}_action"))

            if action[1:2]:
                self.set_accels_for_action(
                    f"app.{action[0]}" if scope == self else f"win.{action[0]}",
                    action[1],
                )

            scope.add_action(simple_action)


def relancar() -> None:
    subprocess.Popen(  # pylint: disable=consider-using-with
        [sys.executable, *sys.argv],
        start_new_session=True,
        close_fds=True,
    )


def main(_version: int) -> Any:
    """App entry point"""
    if RuntimeContext.detect(sys.argv, os.environ).is_session:
        sys.excepthook = _exit_failed_game_session

    # Before anything is loaded: a second copy must not read the game files
    # at all, let alone save over them. GApplication's own uniqueness check
    # runs over the D-Bus session bus, which may not exist on all platforms.
    if not acquire_single_instance():
        logging.warning("Jolven is already running")
        present_running_instance()
        return 0

    app = CartridgesApplication()
    status = app.run(sys.argv)
    if app.reiniciar:
        release_single_instance()
        relancar()
    return status
