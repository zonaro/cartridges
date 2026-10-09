# preferences.py
#
# Copyright 2022-2023 redclaw
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

import logging
import math
import re
import threading
from shutil import which
from datetime import date
from pathlib import Path
from shutil import rmtree
from typing import Any, Callable, Optional

from gi.repository import Adw, Gio, GLib, Gtk

from cartridges import shared
from cartridges.errors.friendly_error import FriendlyError
from cartridges.game import Game
from cartridges.hardware import drm_connectors, pipewire_nodes, vrr_supported
from cartridges.importer.bottles_source import BottlesSource
from cartridges.importer.desktop_source import DesktopSource
from cartridges.importer.dolphin_source import DolphinSource
from cartridges.importer.flatpak_source import FlatpakSource
from cartridges.importer.heroic_source import HeroicSource
from cartridges.importer.itch_source import ItchSource
from cartridges.importer.legendary_source import LegendarySource
from cartridges.importer.location import UnresolvableLocationError
from cartridges.importer.lutris_source import LutrisSource
from cartridges.importer.retroarch_source import RetroarchSource
from cartridges.importer.source import Source
from cartridges.importer.steam_source import SteamSource
from cartridges.importer.twintail_source import TwintailSource
from cartridges.importer.waydroid_source import WaydroidSource
from cartridges.importer.yuzu_source import YuzuSource
from cartridges.store.managers.sgdb_manager import SgdbManager
from cartridges.store.managers.thegamesdb_manager import TheGamesDBManager
from cartridges.utils import backup
from cartridges.utils import global_shortcut
from cartridges.utils import session_fita
from cartridges.utils.create_dialog import create_dialog
from cartridges.utils.na_tela import entregar_na_tela


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/preferences.ui")
class CartridgesPreferences(Adw.PreferencesDialog):
    __gtype_name__ = "CartridgesPreferences"

    general_page: Adw.PreferencesPage = Gtk.Template.Child()
    import_page: Adw.PreferencesPage = Gtk.Template.Child()
    sgdb_page: Adw.PreferencesPage = Gtk.Template.Child()

    sources_group: Adw.PreferencesGroup = Gtk.Template.Child()

    exit_after_launch_switch: Adw.SwitchRow = Gtk.Template.Child()
    cover_launches_game_switch: Adw.SwitchRow = Gtk.Template.Child()
    agrupar_duplicados_switch: Adw.SwitchRow = Gtk.Template.Child()
    language_row: Adw.ComboRow = Gtk.Template.Child()
    high_quality_images_switch: Adw.SwitchRow = Gtk.Template.Child()
    app_icon_row: Adw.ComboRow = Gtk.Template.Child()
    xbox_cloud_gaming_switch: Adw.SwitchRow = Gtk.Template.Child()
    xcloud_account_row: Adw.ActionRow = Gtk.Template.Child()
    xcloud_login_button: Gtk.Button = Gtk.Template.Child()
    xcloud_fullscreen_on_start_switch: Adw.SwitchRow = Gtk.Template.Child()

    sunshine_auto_sync_switch: Adw.SwitchRow = Gtk.Template.Child()
    sunshine_apps_path_row: Adw.EntryRow = Gtk.Template.Child()
    sunshine_sync_all_row: Adw.ActionRow = Gtk.Template.Child()
    sunshine_sync_all_button: Gtk.Button = Gtk.Template.Child()
    sunshine_host_row: Adw.EntryRow = Gtk.Template.Child()
    sunshine_port_row: Adw.EntryRow = Gtk.Template.Child()
    sunshine_username_row: Adw.EntryRow = Gtk.Template.Child()
    sunshine_password_row: Adw.EntryRow = Gtk.Template.Child()
    sunshine_status_row: Adw.ActionRow = Gtk.Template.Child()
    sunshine_test_button: Gtk.Button = Gtk.Template.Child()
    sunshine_web_button: Gtk.Button = Gtk.Template.Child()
    sunshine_start_button: Gtk.Button = Gtk.Template.Child()
    sunshine_apps_group: Adw.PreferencesGroup = Gtk.Template.Child()
    sunshine_api_sync_button: Gtk.Button = Gtk.Template.Child()
    sunshine_max_bitrate_row: Adw.EntryRow = Gtk.Template.Child()
    sunshine_min_fps_row: Adw.EntryRow = Gtk.Template.Child()
    sunshine_upnp_switch: Adw.SwitchRow = Gtk.Template.Child()
    sunshine_origin_row: Adw.EntryRow = Gtk.Template.Child()
    sunshine_encoder_row: Adw.EntryRow = Gtk.Template.Child()
    sunshine_apply_button: Gtk.Button = Gtk.Template.Child()
    sunshine_pairings_group: Adw.PreferencesGroup = Gtk.Template.Child()
    sunshine_unpair_all_button: Gtk.Button = Gtk.Template.Child()

    auto_import_switch: Adw.SwitchRow = Gtk.Template.Child()
    remove_missing_switch: Adw.SwitchRow = Gtk.Template.Child()

    atalho_global_row: Adw.ActionRow = Gtk.Template.Child()
    atalho_global_button: Gtk.Button = Gtk.Template.Child()

    steam_expander_row: Adw.ExpanderRow = Gtk.Template.Child()
    steam_data_action_row: Adw.ActionRow = Gtk.Template.Child()
    steam_data_file_chooser_button: Gtk.Button = Gtk.Template.Child()

    lutris_expander_row: Adw.ExpanderRowClass = Gtk.Template.Child()
    lutris_data_action_row: Adw.ActionRow = Gtk.Template.Child()
    lutris_data_file_chooser_button: Gtk.Button = Gtk.Template.Child()
    lutris_import_steam_switch: Adw.SwitchRow = Gtk.Template.Child()
    lutris_import_flatpak_switch: Adw.SwitchRow = Gtk.Template.Child()

    heroic_expander_row: Adw.ExpanderRow = Gtk.Template.Child()
    heroic_config_action_row: Adw.ActionRow = Gtk.Template.Child()
    heroic_config_file_chooser_button: Gtk.Button = Gtk.Template.Child()
    heroic_import_epic_switch: Adw.SwitchRow = Gtk.Template.Child()
    heroic_import_gog_switch: Adw.SwitchRow = Gtk.Template.Child()
    heroic_import_amazon_switch: Adw.SwitchRow = Gtk.Template.Child()
    heroic_import_sideload_switch: Adw.SwitchRow = Gtk.Template.Child()

    bottles_expander_row: Adw.ExpanderRow = Gtk.Template.Child()
    bottles_data_action_row: Adw.ActionRow = Gtk.Template.Child()
    bottles_data_file_chooser_button: Gtk.Button = Gtk.Template.Child()

    dolphin_expander_row: Adw.ExpanderRow = Gtk.Template.Child()
    dolphin_cache_action_row: Adw.ActionRow = Gtk.Template.Child()
    dolphin_cache_file_chooser_button: Gtk.Button = Gtk.Template.Child()

    itch_expander_row: Adw.ExpanderRow = Gtk.Template.Child()
    itch_config_action_row: Adw.ActionRow = Gtk.Template.Child()
    itch_config_file_chooser_button: Gtk.Button = Gtk.Template.Child()

    legendary_expander_row: Adw.ExpanderRow = Gtk.Template.Child()
    legendary_config_action_row: Adw.ActionRow = Gtk.Template.Child()
    legendary_config_file_chooser_button: Gtk.Button = Gtk.Template.Child()

    retroarch_expander_row: Adw.ExpanderRow = Gtk.Template.Child()
    retroarch_config_action_row: Adw.ActionRow = Gtk.Template.Child()
    retroarch_config_file_chooser_button: Gtk.Button = Gtk.Template.Child()

    waydroid_expander_row: Adw.ExpanderRow = Gtk.Template.Child()

    yuzu_expander_row: Adw.ExpanderRow = Gtk.Template.Child()
    yuzu_data_action_row: Adw.ActionRow = Gtk.Template.Child()
    yuzu_data_file_chooser_button: Gtk.Button = Gtk.Template.Child()

    twintail_expander_row: Adw.ExpanderRow = Gtk.Template.Child()
    twintail_data_action_row: Adw.ActionRow = Gtk.Template.Child()
    twintail_data_file_chooser_button: Gtk.Button = Gtk.Template.Child()

    flatpak_expander_row: Adw.ExpanderRow = Gtk.Template.Child()
    flatpak_system_data_action_row: Adw.ActionRow = Gtk.Template.Child()
    flatpak_system_data_file_chooser_button: Gtk.Button = Gtk.Template.Child()
    flatpak_user_data_action_row: Adw.ActionRow = Gtk.Template.Child()
    flatpak_user_data_file_chooser_button: Gtk.Button = Gtk.Template.Child()
    flatpak_import_launchers_switch: Adw.SwitchRow = Gtk.Template.Child()

    desktop_switch: Adw.SwitchRow = Gtk.Template.Child()

    sgdb_key_group: Adw.PreferencesGroup = Gtk.Template.Child()
    sgdb_key_entry_row: Adw.EntryRow = Gtk.Template.Child()
    sgdb_switch: Adw.SwitchRow = Gtk.Template.Child()
    sgdb_prefer_switch: Adw.SwitchRow = Gtk.Template.Child()
    sgdb_animated_switch: Adw.SwitchRow = Gtk.Template.Child()
    sgdb_fetch_button: Gtk.Button = Gtk.Template.Child()
    sgdb_stack: Gtk.Stack = Gtk.Template.Child()
    sgdb_spinner: Adw.Spinner = Gtk.Template.Child()
    thegamesdb_key_group: Adw.PreferencesGroup = Gtk.Template.Child()
    thegamesdb_key_entry_row: Adw.EntryRow = Gtk.Template.Child()
    thegamesdb_switch: Adw.SwitchRow = Gtk.Template.Child()
    thegamesdb_fetch_button: Gtk.Button = Gtk.Template.Child()
    thegamesdb_stack: Gtk.Stack = Gtk.Template.Child()
    thegamesdb_spinner: Adw.Spinner = Gtk.Template.Child()
    igdb_key_group: Adw.PreferencesGroup = Gtk.Template.Child()
    igdb_client_id_entry_row: Adw.EntryRow = Gtk.Template.Child()
    igdb_client_secret_entry_row: Adw.EntryRow = Gtk.Template.Child()

    danger_zone_group = Gtk.Template.Child()
    remove_all_games_button_row = Gtk.Template.Child()
    reset_button_row = Gtk.Template.Child()
    export_backup_button_row = Gtk.Template.Child()
    import_backup_button_row = Gtk.Template.Child()

    playtime_tracking_switch: Adw.SwitchRow = Gtk.Template.Child()
    show_news_button_switch: Adw.SwitchRow = Gtk.Template.Child()
    session_wallpaper_switch: Adw.SwitchRow = Gtk.Template.Child()
    wallhaven_key_entry_row: Adw.EntryRow = Gtk.Template.Child()
    wallpaper_restore_row: Adw.ActionRow = Gtk.Template.Child()
    wallpaper_restore_button: Gtk.Button = Gtk.Template.Child()
    session_fita_switch: Adw.SwitchRow = Gtk.Template.Child()
    game_mode_group: Adw.PreferencesGroup = Gtk.Template.Child()
    game_mode_use_gamemode_switch: Adw.SwitchRow = Gtk.Template.Child()
    game_mode_use_mangohud_switch: Adw.SwitchRow = Gtk.Template.Child()
    game_mode_enable_fps_limiter_switch: Adw.SwitchRow = Gtk.Template.Child()
    game_mode_enable_vrr_switch: Adw.SwitchRow = Gtk.Template.Child()
    game_mode_start_library_switch: Adw.SwitchRow = Gtk.Template.Child()
    game_mode_hide_mouse_cursor_switch: Adw.SwitchRow = Gtk.Template.Child()
    game_overlay_button_row: Adw.ComboRow = Gtk.Template.Child()
    game_mode_monitor_entry_row: Adw.ComboRow = Gtk.Template.Child()
    game_mode_audio_output_entry_row: Adw.ComboRow = Gtk.Template.Child()
    game_mode_audio_input_entry_row: Adw.ComboRow = Gtk.Template.Child()
    fita_brilho_row: Adw.SpinRow = Gtk.Template.Child()
    fita_brilho_individual_row: Adw.ActionRow = Gtk.Template.Child()
    fita_brilho_individual_button: Gtk.Button = Gtk.Template.Child()
    fita_cor_app_row: Adw.ActionRow = Gtk.Template.Child()
    fita_cor_app_reset: Gtk.Button = Gtk.Template.Child()
    fita_cor_app_menu: Gtk.MenuButton = Gtk.Template.Child()
    fita_cor_app_amostra: Gtk.DrawingArea = Gtk.Template.Child()
    fita_cor_app_seletor: Gtk.ColorChooserWidget = Gtk.Template.Child()
    fita_configurar_row: Adw.ActionRow = Gtk.Template.Child()
    fita_configurar_button: Gtk.Button = Gtk.Template.Child()
    fita_testar_row: Adw.ActionRow = Gtk.Template.Child()
    fita_testar_button: Gtk.Button = Gtk.Template.Child()

    removed_games: set[Game] = set()
    warning_menu_buttons: dict = {}

    is_open = False
    _teste_em_curso: Optional[threading.Event] = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # Make it so only one dialog can be open at a time
        self.__class__.is_open = True
        self.connect("closed", lambda *_: self.set_is_open(False))

        self.file_chooser = Gtk.FileDialog()

        self.toast = Adw.Toast.new(_("All games removed"))
        self.toast.set_button_label(_("Undo"))
        self.toast.connect("button-clicked", self.undo_remove_all, None)
        self.toast.set_priority(Adw.ToastPriority.HIGH)

        (shortcut_controller := Gtk.ShortcutController()).add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("<primary>z"),
                Gtk.CallbackAction.new(self.undo_remove_all),
            )
        )
        self.add_controller(shortcut_controller)

        # General
        self.remove_all_games_button_row.connect("activated", self.remove_all_games)
        self.export_backup_button_row.connect("activated", self.export_backup)
        self.import_backup_button_row.connect("activated", self.import_backup)
        self.setup_app_icon_row()
        self.setup_language_row()

        # "Exit After Launching Games" makes no sense in the exclusive
        # Jolven Session: closing Jolven ends Gamescope and returns to the
        # login screen, so the option is completely ignored while in the
        # session (see Game.launch) and must stay hidden there.
        if shared.runtime.is_session:
            self.exit_after_launch_switch.set_visible(False)

        # Debug
        if shared.PROFILE == "development":
            self.reset_button_row.set_visible(True)
            self.reset_button_row.connect("activated", self.reset_app)

        # Sources settings
        for source_class in (
            BottlesSource,
            DolphinSource,
            FlatpakSource,
            HeroicSource,
            ItchSource,
            LegendarySource,
            LutrisSource,
            RetroarchSource,
            SteamSource,
            TwintailSource,
            WaydroidSource,
            YuzuSource,
        ):
            source = source_class()
            if not source.is_available:
                expander_row = getattr(self, f"{source.source_id}_expander_row")
                expander_row.set_visible(False)
            else:
                self.init_source_row(source)

        # Special case for the desktop source
        if not DesktopSource().is_available:
            self.desktop_switch.set_visible(False)

        # SteamGridDB
        def sgdb_key_changed(*_args: Any) -> None:
            shared.schema.set_string("sgdb-key", self.sgdb_key_entry_row.get_text())

        self.sgdb_key_entry_row.set_text(shared.schema.get_string("sgdb-key"))
        self.sgdb_key_entry_row.connect("changed", sgdb_key_changed)

        # Sunshine
        def sunshine_apps_path_changed(*_args: Any) -> None:
            shared.schema.set_string(
                "sunshine-apps-path",
                self.sunshine_apps_path_row.get_text().strip(),
            )

        self.sunshine_apps_path_row.set_text(
            shared.schema.get_string("sunshine-apps-path")
        )
        self.sunshine_apps_path_row.connect("changed", sunshine_apps_path_changed)
        self.sunshine_sync_all_button.connect("clicked", self.sincronizar_sunshine)

        # Sunshine remoto (a senha vive só nesta sessão: nunca é salva)
        def sunshine_host_changed(*_args: Any) -> None:
            shared.schema.set_string(
                "sunshine-host", self.sunshine_host_row.get_text().strip()
            )

        def sunshine_port_changed(*_args: Any) -> None:
            try:
                porta = int(self.sunshine_port_row.get_text().strip())
            except ValueError:
                return
            if 1 <= porta <= 65535:
                shared.schema.set_int("sunshine-port", porta)

        def sunshine_username_changed(*_args: Any) -> None:
            shared.schema.set_string(
                "sunshine-username", self.sunshine_username_row.get_text().strip()
            )

        self.sunshine_host_row.set_text(shared.schema.get_string("sunshine-host"))
        self.sunshine_host_row.connect("changed", sunshine_host_changed)
        self.sunshine_port_row.set_text(str(shared.schema.get_int("sunshine-port")))
        self.sunshine_port_row.connect("changed", sunshine_port_changed)
        self.sunshine_username_row.set_text(
            shared.schema.get_string("sunshine-username")
        )
        self.sunshine_username_row.connect("changed", sunshine_username_changed)
        self.sunshine_test_button.connect("clicked", self.testar_sunshine)
        self.sunshine_web_button.connect("clicked", self.abrir_sunshine_web)
        self.sunshine_start_button.connect("clicked", self.iniciar_sunshine)
        self.sunshine_apply_button.connect("clicked", self.aplicar_sunshine_host)
        self.sunshine_api_sync_button.connect("clicked", self.exportar_sunshine_api)
        self.sunshine_unpair_all_button.connect("clicked", self.desemparelhar_todos)
        self._linhas_sunshine: list = []

        self.sgdb_key_group.set_description(
            _(
                "An API key is required to use SteamGridDB. You can generate one {}here{}."
            ).format(
                '<a href="https://www.steamgriddb.com/profile/preferences/api">', "</a>"
            )
        )

        def update_sgdb(*_args: Any) -> None:
            counter = 0
            games_len = len(shared.store)
            sgdb_manager = shared.store.managers[SgdbManager]
            sgdb_manager.reset_cancellable()

            self.sgdb_spinner.set_visible(True)
            self.sgdb_stack.set_visible_child(self.sgdb_spinner)

            self.add_toast(download_toast := Adw.Toast.new(_("Downloading covers…")))

            def update_cover_callback(manager: SgdbManager) -> None:
                nonlocal counter
                nonlocal games_len
                nonlocal download_toast

                counter += 1
                if counter != games_len:
                    return

                for error in manager.collect_errors():
                    if isinstance(error, FriendlyError):
                        create_dialog(self, error.title, error.subtitle)
                        break

                for game in shared.store:
                    game.update()

                toast = Adw.Toast.new(_("Covers updated"))
                toast.set_priority(Adw.ToastPriority.HIGH)
                download_toast.dismiss()
                self.add_toast(toast)

                self.sgdb_spinner.set_visible(False)
                self.sgdb_stack.set_visible_child(self.sgdb_fetch_button)

            for game in shared.store:
                sgdb_manager.process_game(game, {}, update_cover_callback)

        self.sgdb_fetch_button.connect("clicked", update_sgdb)

        # TheGamesDB
        def thegamesdb_key_changed(*_args: Any) -> None:
            key = self.thegamesdb_key_entry_row.get_text().strip()
            shared.schema.set_string("thegamesdb-key", key)
            self.thegamesdb_switch.set_sensitive(bool(key))
            if not key:
                shared.schema.set_boolean("thegamesdb", False)

        self.thegamesdb_key_entry_row.set_text(
            shared.schema.get_string("thegamesdb-key")
        )
        self.thegamesdb_key_entry_row.connect("changed", thegamesdb_key_changed)
        self.thegamesdb_switch.set_sensitive(
            bool(self.thegamesdb_key_entry_row.get_text().strip())
        )
        self.thegamesdb_key_group.set_description(
            _(
                "A chave permite buscar detalhes, capturas de tela e imagens. "
                "Consulte a {}página da API do TheGamesDB{}."
            ).format('<a href="https://api.thegamesdb.net/">', "</a>")
        )

        # IGDB (Client ID + Secret do Twitch; sem eles a fonte fica indisponível)
        def igdb_key_changed(*_args: Any) -> None:
            shared.schema.set_string(
                "igdb-client-id", self.igdb_client_id_entry_row.get_text().strip()
            )
            shared.schema.set_string(
                "igdb-client-secret",
                self.igdb_client_secret_entry_row.get_text().strip(),
            )

        self.igdb_client_id_entry_row.set_text(
            shared.schema.get_string("igdb-client-id")
        )
        self.igdb_client_secret_entry_row.set_text(
            shared.schema.get_string("igdb-client-secret")
        )
        self.igdb_client_id_entry_row.connect("changed", igdb_key_changed)
        self.igdb_client_secret_entry_row.connect("changed", igdb_key_changed)
        self.igdb_key_group.set_description(
            _(
                "As credenciais permitem buscar capas e capturas de tela. "
                "Consulte a {}página da API do IGDB{}."
            ).format('<a href="https://api.igdb.com/">', "</a>")
        )

        def update_thegamesdb(*_args: Any) -> None:
            games = list(shared.store)
            if not games:
                return
            manager = shared.store.managers[TheGamesDBManager]
            manager.reset_cancellable()
            remaining = len(games)
            self.thegamesdb_spinner.set_visible(True)
            self.thegamesdb_stack.set_visible_child(self.thegamesdb_spinner)
            self.add_toast(progress_toast := Adw.Toast.new(_("Buscando metadados…")))

            def updated(_manager: TheGamesDBManager, game: Game) -> None:
                nonlocal remaining
                game.save()
                game.update()
                remaining -= 1
                if remaining:
                    return
                progress_toast.dismiss()
                errors = manager.collect_errors()
                friendly = next(
                    (error for error in errors if isinstance(error, FriendlyError)),
                    None,
                )
                if friendly:
                    create_dialog(self, friendly.title, friendly.subtitle)
                elif errors:
                    create_dialog(
                        self,
                        _("Não foi possível concluir a atualização"),
                        _("Verifique a conexão e tente novamente."),
                    )
                else:
                    self.add_toast(Adw.Toast.new(_("Metadados atualizados")))
                self.thegamesdb_spinner.set_visible(False)
                self.thegamesdb_stack.set_visible_child(
                    self.thegamesdb_fetch_button
                )

            for game in games:
                manager.process_game(
                    game, {}, lambda current, item=game: updated(current, item)
                )

        self.thegamesdb_fetch_button.connect("clicked", update_thegamesdb)

        # Switches
        self.bind_switches(
            {
                "exit-after-launch",
                "cover-launches-game",
                "agrupar-duplicados",
                "high-quality-images",
                "xbox-cloud-gaming",
                "xcloud-fullscreen-on-start",
                "sunshine-auto-sync",
                "auto-import",
                "remove-missing",
                "lutris-import-steam",
                "lutris-import-flatpak",
                "heroic-import-epic",
                "heroic-import-gog",
                "heroic-import-amazon",
                "heroic-import-sideload",
                "flatpak-import-launchers",
                "sgdb",
                "sgdb-prefer",
                "sgdb-animated",
                "thegamesdb",
                "desktop",
                "playtime-tracking",
                "show-news-button",
                "session-wallpaper",
                "session-fita",
                "game-mode-use-gamemode",
                "game-mode-use-mangohud",
                "game-mode-enable-fps-limiter",
                "game-mode-enable-vrr",
                "game-mode-start-library",
                "game-mode-hide-mouse-cursor",
            }
        )
        self.agrupar_duplicados_switch.connect(
            "notify::active", lambda *_: self._reagrupar()
        )
        self.xcloud_login_button.connect("clicked", self.alternar_xcloud_login)
        self.reler_xcloud_login()
        self.atalho_global_button.connect("clicked", self.alternar_atalho_global)
        self._atalho_ativo = False
        self.atualizar_atalho_global()

        sinks, sources = pipewire_nodes()
        self.setup_device_row(
            self.game_mode_monitor_entry_row,
            "game-mode-monitor",
            drm_connectors(),
        )
        self.setup_device_row(
            self.game_mode_audio_output_entry_row,
            "game-mode-audio-output",
            sinks,
        )
        self.setup_device_row(
            self.game_mode_audio_input_entry_row,
            "game-mode-audio-input",
            sources,
        )
        has_vrr = vrr_supported()
        self.game_mode_enable_vrr_switch.set_visible(has_vrr)
        if not has_vrr:
            shared.schema.set_boolean("game-mode-enable-vrr", False)
        self.game_mode_use_gamemode_switch.set_sensitive(which("gamemoderun") is not None)
        self.game_mode_use_mangohud_switch.set_sensitive(which("mangohud") is not None)
        gamescope_available = which("gamescope") is not None
        self.game_mode_enable_fps_limiter_switch.set_sensitive(gamescope_available)
        self.setup_game_overlay_button_row()

        def set_sgdb_sensitive(widget: Adw.EntryRow) -> None:
            if not widget.get_text():
                shared.schema.set_boolean("sgdb", False)

            self.sgdb_switch.set_sensitive(widget.get_text())

        self.sgdb_key_entry_row.connect("changed", set_sgdb_sensitive)
        set_sgdb_sensitive(self.sgdb_key_entry_row)

        def wallhaven_key_changed(*_args: Any) -> None:
            shared.schema.set_string(
                "wallhaven-key", self.wallhaven_key_entry_row.get_text().strip()
            )

        self._wallhaven_key_changed_id = self.wallhaven_key_entry_row.connect(
            "changed", wallhaven_key_changed
        )
        self.reler_wallhaven()
        self.wallpaper_restore_button.connect("clicked", self.restaurar_parede)
        self.atualizar_parede()
        shared.schema.connect(
            "changed::session-wallpaper-saved", lambda *_: self.atualizar_parede()
        )
        self.setup_fita_rows()
        self.reler_fitas()

    def alternar_xcloud_login(self, *_args: Any) -> None:
        from cartridges import xcloud_login

        if xcloud_login.has_saved_login():
            xcloud_login.clear_saved_login()
            self.reler_xcloud_login()
            return

        def finished(resultado: Any) -> None:
            self.reler_xcloud_login()

        xcloud_login.show_login_dialog(parent=self, on_finished=finished)

    def reler_xcloud_login(self) -> None:
        from cartridges import xcloud_login

        conectado = xcloud_login.has_saved_login()
        self.xcloud_account_row.set_subtitle(
            _("Conectado") if conectado else _("Não conectado")
        )
        self.xcloud_login_button.set_label(_("Sair") if conectado else _("Entrar"))

    def sincronizar_sunshine(self, *_args: Any) -> None:
        import threading

        from cartridges.utils import sunshine

        self.sunshine_sync_all_button.set_sensitive(False)
        custom = shared.schema.get_string("sunshine-apps-path")
        jogos = [game for game in shared.store if sunshine.is_eligible(game)]

        def concluir(resultado: dict) -> bool:
            total = resultado.get("added", 0) + resultado.get("updated", 0)
            self.sunshine_sync_all_button.set_sensitive(True)
            self.add_toast(
                Adw.Toast.new(
                    _("{} jogo(s) sincronizado(s) com o Sunshine").format(total)
                )
            )
            return GLib.SOURCE_REMOVE

        def falhar(mensagem: str) -> bool:
            self.sunshine_sync_all_button.set_sensitive(True)
            self.add_toast(Adw.Toast.new(_("Não foi possível sincronizar")))
            logging.error("Sunshine sync-all failed: %s", mensagem)
            return GLib.SOURCE_REMOVE

        def executar() -> None:
            try:
                resultado = sunshine.sync_library(jogos, custom)
            except Exception as error:  # pylint: disable=broad-exception-caught
                entregar_na_tela(falhar, str(error))
            else:
                entregar_na_tela(concluir, resultado)

        threading.Thread(target=executar, daemon=True).start()

    def _conexao_sunshine(self) -> Any:
        from cartridges.utils import sunshine_api

        try:
            porta = int(self.sunshine_port_row.get_text().strip())
        except ValueError:
            porta = 0
        return sunshine_api.SunshineConnection(
            host=self.sunshine_host_row.get_text().strip() or "localhost",
            port=porta,
            username=self.sunshine_username_row.get_text().strip(),
            password=self.sunshine_password_row.get_text(),
        )

    def _limpar_linhas_sunshine(self) -> None:
        for linha in self._linhas_sunshine:
            parent = linha.get_parent()
            if parent is not None:
                parent.remove(linha)
        self._linhas_sunshine = []

    def _confirmar(
        self, titulo: str, corpo: str, rotulo: str, ao_confirmar: Any
    ) -> None:
        dialog = Adw.AlertDialog.new(titulo, corpo)
        dialog.add_response("cancel", _("Cancelar"))
        dialog.add_response("ok", rotulo)
        dialog.set_default_response("ok")

        def resposta(_dialog: Any, codigo: str) -> None:
            if codigo == "ok":
                ao_confirmar()

        dialog.connect("response", resposta)
        dialog.present(self)

    def testar_sunshine(self, *_args: Any) -> None:
        import threading

        from cartridges.utils import sunshine_api

        conexao = self._conexao_sunshine()
        self.sunshine_test_button.set_sensitive(False)
        self.sunshine_status_row.set_subtitle(_("Verificando…"))

        def concluir(resultado: dict) -> bool:
            self.sunshine_test_button.set_sensitive(True)
            erro = resultado.get("error")
            if erro is not None:
                self.sunshine_status_row.set_subtitle(_("Não foi possível conectar"))
                self.add_toast(Adw.Toast.new(str(erro)))
                return GLib.SOURCE_REMOVE
            status = resultado["status"]
            versao = status.version or _("desconhecida")
            total = len(status.app_names)
            self.sunshine_status_row.set_subtitle(
                _("Sunshine {} · {} app(s)").format(versao, total)
            )
            self._limpar_linhas_sunshine()
            self._preencher_config_sunshine(resultado["config"])
            self._listar_apps_sunshine(conexao, status.app_names)
            self._listar_pareamentos(conexao, resultado["pairings"])
            self._listar_clientes(conexao, resultado["clients"])
            return GLib.SOURCE_REMOVE

        def executar() -> None:
            try:
                status = sunshine_api.probe(conexao)
                config = sunshine_api.fetch_config(conexao)
            except Exception as error:  # pylint: disable=broad-exception-caught
                entregar_na_tela(concluir, {"error": error, "status": None})
                return
            try:
                pareamentos = sunshine_api.get_pending_pairings(conexao)
            except Exception:  # pylint: disable=broad-exception-caught
                pareamentos = []
            try:
                clientes = sunshine_api.get_clients(conexao)
            except Exception:  # pylint: disable=broad-exception-caught
                clientes = []
            entregar_na_tela(
                concluir,
                {
                    "error": None,
                    "status": status,
                    "config": config,
                    "pairings": pareamentos,
                    "clients": clientes,
                },
            )

        threading.Thread(target=executar, daemon=True).start()

    def _preencher_config_sunshine(self, config: dict) -> None:
        self.sunshine_max_bitrate_row.set_text(str(config.get("max_bitrate", "")))
        self.sunshine_min_fps_row.set_text(str(config.get("minimum_fps_target", "")))
        self.sunshine_upnp_switch.set_active(config.get("upnp") == "enabled")
        self.sunshine_origin_row.set_text(str(config.get("origin_web_ui_allowed", "")))
        self.sunshine_encoder_row.set_text(str(config.get("encoder", "")))

    def _listar_apps_sunshine(self, conexao: Any, nomes: list) -> None:
        for nome in sorted(nomes, key=str.casefold):
            linha = Adw.ActionRow(title=nome)
            botao = Gtk.Button(label=_("Remover"), valign=Gtk.Align.CENTER)
            botao.add_css_class("destructive-action")
            botao.connect("clicked", self._remover_app_sunshine, conexao, nome)
            linha.add_suffix(botao)
            linha.set_activatable_widget(botao)
            self.sunshine_apps_group.add(linha)
            self._linhas_sunshine.append(linha)
        if not nomes:
            linha = Adw.ActionRow(title=_("Nenhum app cadastrado"))
            self.sunshine_apps_group.add(linha)
            self._linhas_sunshine.append(linha)

    def _remover_app_sunshine(
        self, _botao: Any, conexao: Any, nome: str
    ) -> None:
        import threading

        from cartridges.utils import sunshine_api

        def executar() -> None:
            try:
                sunshine_api.delete_app(conexao, nome)
            except Exception as error:  # pylint: disable=broad-exception-caught
                entregar_na_tela(
                    lambda: self.add_toast(Adw.Toast.new(str(error)))
                    or GLib.SOURCE_REMOVE
                )
            else:
                entregar_na_tela(self.testar_sunshine)

        self._confirmar(
            _("Remover do Sunshine?"),
            _("{} deixará de aparecer no Moonlight.").format(nome),
            _("Remover"),
            lambda: threading.Thread(target=executar, daemon=True).start(),
        )

    def exportar_sunshine_api(self, *_args: Any) -> None:
        import threading

        from cartridges.utils import sunshine, sunshine_api

        conexao = self._conexao_sunshine()
        jogos = [game for game in shared.store if sunshine.is_eligible(game)]
        self.sunshine_api_sync_button.set_sensitive(False)

        def concluir(resultado: dict) -> bool:
            self.sunshine_api_sync_button.set_sensitive(True)
            if (erro := resultado.get("error")) is not None:
                self.add_toast(Adw.Toast.new(str(erro)))
            else:
                self.add_toast(
                    Adw.Toast.new(
                        _("{} jogo(s) exportado(s) via API").format(
                            resultado.get("added", 0)
                        )
                    )
                )
                self.testar_sunshine()
            return GLib.SOURCE_REMOVE

        def executar() -> None:
            adicionados = 0
            try:
                existentes = {
                    app.get("name")
                    for app in sunshine_api.fetch_apps(conexao)
                    if isinstance(app, dict)
                }
            except Exception as error:  # pylint: disable=broad-exception-caught
                entregar_na_tela(concluir, {"error": error})
                return
            for jogo in jogos:
                try:
                    capa = self._capa_sunshine(conexao, jogo)
                    entrada = sunshine.build_app_entry(jogo, capa)
                    if entrada["name"] in existentes:
                        continue
                    sunshine_api.create_app(conexao, entrada)
                    adicionados += 1
                except Exception as error:  # pylint: disable=broad-exception-caught
                    logging.warning("API export skipped %s: %s", jogo.game_id, error)
            entregar_na_tela(concluir, {"added": adicionados})

        threading.Thread(target=executar, daemon=True).start()

    @staticmethod
    def _capa_sunshine(conexao: Any, jogo: Any) -> Any:
        from pathlib import Path

        from cartridges.utils import sunshine_api

        obter = getattr(jogo, "get_cover_path", None)
        if not callable(obter):
            return None
        try:
            capa = obter()
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        if capa is None:
            return None
        try:
            dados = Path(str(capa)).read_bytes()
        except OSError:
            return None
        if not dados or len(dados) > 5 * 1024 * 1024:
            return None
        try:
            return sunshine_api.upload_cover(
                conexao, f"jolven-{jogo.game_id}", dados
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logging.debug("Cover upload skipped for %s", jogo.game_id)
            return None

    def aplicar_sunshine_host(self, *_args: Any) -> None:
        import threading

        from cartridges.utils import sunshine_api

        conexao = self._conexao_sunshine()
        try:
            bitrate = int(self.sunshine_max_bitrate_row.get_text().strip() or "0")
            fps = int(self.sunshine_min_fps_row.get_text().strip() or "0")
        except ValueError:
            self.add_toast(Adw.Toast.new(_("Bitrate e FPS precisam ser números")))
            return
        if bitrate < 0 or fps < 0:
            self.add_toast(Adw.Toast.new(_("Bitrate e FPS precisam ser números")))
            return
        origem = self.sunshine_origin_row.get_text().strip()
        if origem and origem not in ("pc", "lan", "wan"):
            self.add_toast(Adw.Toast.new(_("Origem válida: pc, lan ou wan")))
            return
        mudancas = {
            "max_bitrate": str(bitrate),
            "minimum_fps_target": str(fps),
            "upnp": "enabled" if self.sunshine_upnp_switch.get_active() else "disabled",
        }
        if origem:
            mudancas["origin_web_ui_allowed"] = origem
        codificador = self.sunshine_encoder_row.get_text().strip()
        if codificador:
            mudancas["encoder"] = codificador

        def executar() -> None:
            try:
                sunshine_api.update_config(conexao, mudancas)
            except Exception as error:  # pylint: disable=broad-exception-caught
                entregar_na_tela(
                    lambda: self.add_toast(Adw.Toast.new(str(error)))
                    or GLib.SOURCE_REMOVE
                )
            else:
                entregar_na_tela(self._oferecer_reinicio)

        def aplicar() -> None:
            threading.Thread(target=executar, daemon=True).start()

        self._confirmar(
            _("Aplicar ajustes do host?"),
            _("A config inteira do Sunshine será regravada."),
            _("Aplicar"),
            aplicar,
        )

    def _oferecer_reinicio(self) -> bool:
        import threading

        from cartridges.utils import sunshine_api

        conexao = self._conexao_sunshine()

        def executar() -> None:
            try:
                sunshine_api.restart(conexao)
            except Exception as error:  # pylint: disable=broad-exception-caught
                entregar_na_tela(
                    lambda: self.add_toast(Adw.Toast.new(str(error)))
                    or GLib.SOURCE_REMOVE
                )
            else:
                entregar_na_tela(
                    lambda: self.add_toast(
                        Adw.Toast.new(_("Ajustes aplicados. Reiniciando…"))
                    )
                    or GLib.SOURCE_REMOVE
                )

        self._confirmar(
            _("Reiniciar o Sunshine?"),
            _("Os ajustes do host só valem após reiniciar."),
            _("Reiniciar"),
            lambda: threading.Thread(target=executar, daemon=True).start(),
        )
        return GLib.SOURCE_REMOVE

    def _listar_pareamentos(self, conexao: Any, pareamentos: list) -> None:
        for item in sorted(
            pareamentos, key=lambda p: str(p.get("name", "")).casefold()
        ):
            nome = str(item.get("name") or item.get("address") or _("Sem nome"))
            linha = Adw.ActionRow(
                title=nome, subtitle=str(item.get("address", ""))
            )
            aprovar = Gtk.Button(label=_("Aprovar"), valign=Gtk.Align.CENTER)
            aprovar.add_css_class("suggested-action")
            aprovar.connect(
                "clicked", self._aprovar_pareamento, conexao, item
            )
            recusar = Gtk.Button(label=_("Recusar"), valign=Gtk.Align.CENTER)
            recusar.connect(
                "clicked", self._recusar_pareamento, conexao, item
            )
            linha.add_suffix(aprovar)
            linha.add_suffix(recusar)
            self.sunshine_pairings_group.add(linha)
            self._linhas_sunshine.append(linha)
        if not pareamentos:
            linha = Adw.ActionRow(title=_("Nenhum pedido pendente"))
            self.sunshine_pairings_group.add(linha)
            self._linhas_sunshine.append(linha)

    def _aprovar_pareamento(
        self, _botao: Any, conexao: Any, item: dict
    ) -> None:
        import threading

        from cartridges.utils import sunshine_api

        entrada = Gtk.Entry(
            placeholder_text="PIN", max_length=32,
            input_purpose=Gtk.InputPurpose.PIN,
        )
        dialog = Adw.AlertDialog.new(
            _("Aprovar pareamento?"),
            _("Digite o PIN mostrado no Moonlight."),
        )
        dialog.add_response("cancel", _("Cancelar"))
        dialog.add_response("ok", _("Aprovar"))
        dialog.set_default_response("ok")
        dialog.set_extra_child(entrada)

        def resposta(_dialog: Any, codigo: str) -> None:
            if codigo != "ok":
                return

            def executar() -> None:
                try:
                    sunshine_api.approve_pairing(
                        conexao,
                        str(item.get("id", "")),
                        entrada.get_text(),
                        str(item.get("name", "")),
                    )
                except Exception as error:  # pylint: disable=broad-exception-caught
                    entregar_na_tela(
                        lambda: self.add_toast(Adw.Toast.new(str(error)))
                        or GLib.SOURCE_REMOVE
                    )
                else:
                    entregar_na_tela(self.testar_sunshine)

            threading.Thread(target=executar, daemon=True).start()

        dialog.connect("response", resposta)
        dialog.present(self)

    def _recusar_pareamento(
        self, _botao: Any, conexao: Any, item: dict
    ) -> None:
        import threading

        from cartridges.utils import sunshine_api

        def executar() -> None:
            try:
                sunshine_api.cancel_pairing(conexao, str(item.get("id", "")))
            except Exception as error:  # pylint: disable=broad-exception-caught
                entregar_na_tela(
                    lambda: self.add_toast(Adw.Toast.new(str(error)))
                    or GLib.SOURCE_REMOVE
                )
            else:
                entregar_na_tela(self.testar_sunshine)

        threading.Thread(target=executar, daemon=True).start()

    def _listar_clientes(self, conexao: Any, clientes: list) -> None:
        for item in clientes:
            nome = str(
                item.get("name") or item.get("uuid") or _("Sem nome")
            )
            linha = Adw.ActionRow(title=nome)
            botao = Gtk.Button(label=_("Remover"), valign=Gtk.Align.CENTER)
            botao.add_css_class("destructive-action")
            botao.connect(
                "clicked", self._remover_cliente, conexao, item
            )
            linha.add_suffix(botao)
            linha.set_activatable_widget(botao)
            self.sunshine_clients_group.add(linha)
            self._linhas_sunshine.append(linha)
        if not clientes:
            linha = Adw.ActionRow(title=_("Nenhum cliente pareado"))
            self.sunshine_clients_group.add(linha)
            self._linhas_sunshine.append(linha)

    def _remover_cliente(self, _botao: Any, conexao: Any, item: dict) -> None:
        import threading

        from cartridges.utils import sunshine_api

        def executar() -> None:
            try:
                sunshine_api.unpair_client(conexao, str(item.get("uuid", "")))
            except Exception as error:  # pylint: disable=broad-exception-caught
                entregar_na_tela(
                    lambda: self.add_toast(Adw.Toast.new(str(error)))
                    or GLib.SOURCE_REMOVE
                )
            else:
                entregar_na_tela(self.testar_sunshine)

        self._confirmar(
            _("Desemparelhar cliente?"),
            str(item.get("name") or item.get("uuid") or ""),
            _("Remover"),
            lambda: threading.Thread(target=executar, daemon=True).start(),
        )

    def desemparelhar_todos(self, *_args: Any) -> None:
        import threading

        from cartridges.utils import sunshine_api

        conexao = self._conexao_sunshine()

        def executar() -> None:
            try:
                sunshine_api.unpair_all_clients(conexao)
            except Exception as error:  # pylint: disable=broad-exception-caught
                entregar_na_tela(
                    lambda: self.add_toast(Adw.Toast.new(str(error)))
                    or GLib.SOURCE_REMOVE
                )
            else:
                entregar_na_tela(self.testar_sunshine)

        self._confirmar(
            _("Desemparelhar todos?"),
            _("Todos os Moonlights precisarão parear de novo."),
            _("Remover todos"),
            lambda: threading.Thread(target=executar, daemon=True).start(),
        )

    def abrir_sunshine_web(self, *_args: Any) -> None:
        from cartridges.utils.open_uri import open_uri

        try:
            porta = int(self.sunshine_port_row.get_text().strip())
        except ValueError:
            porta = 0
        if not 1 <= porta <= 65535:
            self.add_toast(Adw.Toast.new(_("Porta inválida")))
            return
        host = self.sunshine_host_row.get_text().strip() or "localhost"
        open_uri(f"https://{host}:{porta}/", parent=self)

    def iniciar_sunshine(self, *_args: Any) -> None:
        from cartridges.utils import sunshine

        try:
            launched = sunshine.start_server()
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.add_toast(Adw.Toast.new(str(error)))
            return
        if not launched:
            self.add_toast(Adw.Toast.new(_("O Sunshine já está rodando")))
            return
        self.add_toast(Adw.Toast.new(_("Iniciando o Sunshine…")))
        GLib.timeout_add_seconds(4, self._retestar_sunshine)

    def _retestar_sunshine(self) -> bool:
        self.testar_sunshine()
        return GLib.SOURCE_REMOVE

    def atualizar_atalho_global(self) -> None:
        try:
            estado = global_shortcut.get_status()
        except Exception:  # pylint: disable=broad-exception-caught
            logging.exception("Não foi possível ler o atalho global")
            estado = global_shortcut.ShortcutStatus(backend=None, registered=False)
        self._atalho_ativo = estado.registered
        botao = self.atalho_global_button
        if estado.backend is None:
            self.atalho_global_row.set_subtitle(
                _("Sem suporte neste ambiente. No GNOME: Configurações > "
                  "Teclado > Atalhos personalizados.")
            )
            botao.set_label(_("Ativar"))
            botao.set_sensitive(False)
            return
        ambiente = "GNOME" if estado.backend == "gnome" else "KDE"
        botao.set_sensitive(True)
        if estado.registered:
            self.atalho_global_row.set_subtitle(
                _("Ativo no {} — Super+G abre o Jolven de qualquer lugar").format(
                    ambiente
                )
            )
            botao.set_label(_("Remover"))
        elif estado.conflict:
            self.atalho_global_row.set_subtitle(
                _("Super+G está com {} — ativar move para o Jolven ({})").format(
                    estado.conflict, ambiente
                )
            )
            botao.set_label(_("Ativar"))
        else:
            self.atalho_global_row.set_subtitle(
                _("Abre o Jolven de qualquer lugar ({})").format(ambiente)
            )
            botao.set_label(_("Ativar"))

    def alternar_atalho_global(self, *_args: Any) -> None:
        alvo = not self._atalho_ativo
        self.atalho_global_button.set_sensitive(False)
        self.atalho_global_row.set_subtitle(_("Aplicando…"))

        def trabalho() -> None:
            try:
                global_shortcut.set_enabled(alvo)
            except global_shortcut.ShortcutError as erro:
                entregar_na_tela(self._atalho_global_pronto, False, str(erro))
            except Exception as erro:  # pylint: disable=broad-exception-caught
                logging.exception("Não foi possível alternar o atalho global")
                entregar_na_tela(self._atalho_global_pronto, False, str(erro))
            else:
                entregar_na_tela(self._atalho_global_pronto, True, "")

        threading.Thread(target=trabalho, daemon=True).start()

    def _atalho_global_pronto(self, ok: bool, mensagem: str) -> bool:
        if ok:
            self.add_toast(
                Adw.Toast.new(
                    _("Atalho Super+G ativo")
                    if self._atalho_ativo is False
                    else _("Atalho Super+G removido")
                )
            )
        else:
            create_dialog(
                self,
                _("Não foi possível configurar o atalho"),
                mensagem
                or _("Verifique as permissões do ambiente e tente novamente."),
            )
        self.atualizar_atalho_global()
        return False

    def reler_wallhaven(self) -> None:
        self.wallhaven_key_entry_row.handler_block(self._wallhaven_key_changed_id)
        try:
            self.wallhaven_key_entry_row.set_text(
                shared.schema.get_string("wallhaven-key")
            )
        finally:
            self.wallhaven_key_entry_row.handler_unblock(
                self._wallhaven_key_changed_id
            )

    def atualizar_parede(self) -> None:
        tem_salvo = bool(shared.schema.get_string("session-wallpaper-saved"))
        self.wallpaper_restore_row.set_subtitle(
            _("Há um papel de parede de sessão a devolver")
            if tem_salvo
            else _("Nenhum papel de parede de sessão a devolver")
        )
        self.wallpaper_restore_row.set_sensitive(tem_salvo)

    def restaurar_parede(self, *_args: Any) -> None:
        from cartridges.utils import session_wallpaper

        session_wallpaper.restaurar()
        self.atualizar_parede()
        if shared.schema.get_string("session-wallpaper-saved"):
            self.add_toast(
                Adw.Toast.new(_("Não foi possível devolver o papel de parede"))
            )
        else:
            self.add_toast(Adw.Toast.new(_("Papel de parede restaurado")))

    def setup_fita_rows(self) -> None:
        self._fita_brilho_changed_id = self.fita_brilho_row.connect(
            "notify::value", self.mudar_brilho_padrao
        )
        self.fita_brilho_individual_button.connect(
            "clicked", self.brilho_por_dispositivo
        )
        self.fita_configurar_button.connect("clicked", self.configurar_fitas)
        self.fita_testar_button.connect("clicked", self.testar_fitas)

        self.fita_cor_app_amostra.set_draw_func(self.desenhar_cor_app)
        self._fita_cor_app_changed_id = self.fita_cor_app_seletor.connect(
            "notify::rgba", self.mudar_cor_app
        )
        self.fita_cor_app_reset.connect("clicked", self.voltar_ao_roxo)
        self.session_fita_switch.connect("notify::active", self.arrancar_fitas)

    def reler_fitas(self) -> None:
        self.fita_brilho_row.handler_block(self._fita_brilho_changed_id)
        try:
            self.fita_brilho_row.set_value(
                session_fita.por_cento(shared.schema.get_int("fita-brilho-padrao"))
            )
        finally:
            self.fita_brilho_row.handler_unblock(self._fita_brilho_changed_id)
        self.atualizar_fitas()

        self.fita_cor_app_seletor.handler_block(self._fita_cor_app_changed_id)
        try:
            self.fita_cor_app_seletor.set_property(
                "rgba", session_fita.cor_para_rgba(session_fita.cor_do_app())
            )
        finally:
            self.fita_cor_app_seletor.handler_unblock(self._fita_cor_app_changed_id)
        self.fita_cor_app_amostra.queue_draw()
        self.fita_cor_app_reset.set_visible(
            session_fita.tom_do_app() != session_fita.ROXO_DO_APP
        )

    def atualizar_fitas(self) -> None:
        configuradas = session_fita.fitas()
        self.session_fita_switch.set_sensitive(bool(configuradas))
        if configuradas:
            n = len(configuradas)
            self.session_fita_switch.set_subtitle(
                f"{n} dispositivo configurado"
                if n == 1
                else f"{n} dispositivos configurados"
            )
            return

        shared.schema.set_boolean("session-fita", False)
        self.session_fita_switch.set_subtitle(_("Nenhum dispositivo configurado"))

    def arrancar_fitas(self, row: Adw.SwitchRow, *_args: Any) -> None:
        if row.get_active():
            shared.schema.set_boolean("session-fita", True)
            session_fita.reacender()

    def mudar_brilho_padrao(self, row: Adw.SpinRow, *_args: Any) -> None:
        brilho = session_fita.de_por_cento(row.get_value())
        shared.schema.set_int("fita-brilho-padrao", brilho)
        session_fita.previa(session_fita.Cor(*session_fita.tom_do_app(), brilho))

    def brilho_por_dispositivo(self, *_args: Any) -> None:
        configuradas = session_fita.fitas()
        dialogo = Adw.Dialog(
            title=_("Brilho máximo por dispositivo"), content_width=420
        )
        cabecalho = Adw.HeaderBar(
            show_start_title_buttons=False, show_end_title_buttons=False
        )
        cancelar = Gtk.Button(label=_("Cancelar"))
        salvar = Gtk.Button(label=_("Salvar"), css_classes=["suggested-action"])
        cabecalho.pack_start(cancelar)
        cabecalho.pack_end(salvar)

        grupo = Adw.PreferencesGroup(
            description=_(
                "Brilho máximo de cada dispositivo, em porcentagem. O brilho "
                "definido nas Preferências e em cada jogo é aplicado "
                "proporcionalmente sobre estes valores."
            )
        )
        linhas: dict[str, Adw.SpinRow] = {}

        def mostrar(*_args: Any) -> None:
            session_fita.previa(
                session_fita.Cor(*session_fita.tom_do_app(), session_fita.BRILHO_CHEIO),
                {id_: round(linha.get_value()) for id_, linha in linhas.items()},
            )

        for fita in configuradas:
            linha = Adw.SpinRow.new_with_range(1, 100, 1)
            linha.set_title(fita.nome)
            linha.set_value(fita.brilho)
            linha.connect("notify::value", mostrar)
            grupo.add(linha)
            linhas[fita.id] = linha

        pagina = Adw.PreferencesPage()
        pagina.add(grupo)
        vista = Adw.ToolbarView(content=pagina)
        vista.add_top_bar(cabecalho)
        dialogo.set_child(vista)

        def gravar(*_args: Any) -> None:
            session_fita.gravar_fitas(
                [
                    fita._replace(brilho=round(linhas[fita.id].get_value()))
                    if fita.id in linhas
                    else fita
                    for fita in session_fita.fitas()
                ]
            )
            dialogo.close()

        cancelar.connect("clicked", lambda *_: dialogo.close())
        salvar.connect("clicked", gravar)
        dialogo.connect("closed", lambda *_: session_fita.previa(session_fita.cor_do_app()))
        dialogo.present(self)
        mostrar()

    def desenhar_cor_app(
        self, _area: Any, contexto: Any, largura: int, altura: int
    ) -> None:
        cor = self.fita_cor_app_seletor.props.rgba
        contexto.set_source_rgb(cor.red, cor.green, cor.blue)
        raio = min(largura, altura) / 2
        contexto.arc(largura / 2, altura / 2, raio, 0, 2 * math.pi)
        contexto.fill()

    def mudar_cor_app(self, seletor: Gtk.ColorChooserWidget, *_args: Any) -> None:
        cor = session_fita.rgba_para_cor(
            seletor.props.rgba, session_fita.brilho_padrao()
        )
        session_fita.salvar_tom_do_app(cor.matiz, cor.saturacao)
        self.fita_cor_app_amostra.queue_draw()
        self.fita_cor_app_reset.set_visible(
            session_fita.tom_do_app() != session_fita.ROXO_DO_APP
        )
        session_fita.previa(session_fita.cor_do_app())

    def voltar_ao_roxo(self, *_args: Any) -> None:
        self.fita_cor_app_seletor.set_property(
            "rgba",
            session_fita.cor_para_rgba(
                session_fita.Cor(*session_fita.ROXO_DO_APP, session_fita.brilho_padrao())
            ),
        )
        session_fita.redefinir_tom_do_app()
        self.fita_cor_app_reset.set_visible(False)

    def configurar_fitas(self, *_args: Any) -> None:
        from cartridges.fita_wizard import FitaWizard  # noqa: PLC0415

        assistente = FitaWizard()
        assistente.connect("closed", lambda *_: self.fitas_configuradas())
        assistente.present(self)

    def fitas_configuradas(self) -> None:
        self.atualizar_fitas()
        if session_fita.ligada():
            session_fita.reacender()

    def testar_fitas(self, *_args: Any) -> None:
        if self._teste_em_curso is not None:
            self._teste_em_curso.set()
            return

        parar = threading.Event()
        self._teste_em_curso = parar
        session_fita.retomar()

        def tarefa() -> None:
            cor = session_fita.cor_do_app()
            mudas = []
            for fita in session_fita.fitas():
                if parar.is_set():
                    break
                if not session_fita.aplicar(
                    fita, True, session_fita.hsv_hex(session_fita.na_fita(cor, fita))
                ):
                    mudas.append(fita.nome)
            entregar_na_tela(pronto, mudas, parar.is_set())

        def pronto(mudas: list[str], cancelado: bool) -> bool:
            self._teste_em_curso = None
            if not self.__class__.is_open:
                return False
            self.fita_testar_button.set_icon_name("media-playback-start-symbolic")
            if cancelado:
                self.fita_testar_row.set_subtitle(_("Teste interrompido"))
            else:
                self.fita_testar_row.set_subtitle(
                    _(
                        "Sem resposta: {}. Se o dispositivo estiver ligado, o "
                        "endereço dele na rede pode ter mudado; configure a "
                        "iluminação inteligente novamente."
                    ).format(", ".join(mudas))
                    if mudas
                    else _("Teste concluído")
                )
            return False

        threading.Thread(target=tarefa, daemon=True).start()

    def set_is_open(self, is_open: bool) -> None:
        self.__class__.is_open = is_open

    @staticmethod
    def _reagrupar(*_args: Any) -> None:
        for game in shared.store:
            game.update()

    def get_switch(self, setting: str) -> Any:
        return getattr(self, f'{setting.replace("-", "_")}_switch')

    def bind_switches(self, settings: set[str]) -> None:
        for setting in settings:
            shared.schema.bind(
                setting,
                self.get_switch(setting),
                "active",
                Gio.SettingsBindFlags.DEFAULT,
            )

    @staticmethod
    def setup_device_row(row: Adw.ComboRow, setting: str, values: list[str]) -> None:
        choices = [_('System default'), *values]
        row.set_model(Gtk.StringList.new(choices))
        saved = shared.schema.get_string(setting)
        if saved and saved not in values:
            shared.schema.set_string(setting, "")
            saved = ""
        row.set_selected(choices.index(saved) if saved in choices else 0)
        row.set_visible(len(values) > 1)

        def selected_changed(widget: Adw.ComboRow, *_args: Any) -> None:
            selected = widget.get_selected()
            shared.schema.set_string(
                setting, values[selected - 1] if 0 < selected <= len(values) else ""
            )

        row.connect("notify::selected", selected_changed)

    def setup_app_icon_row(self) -> None:
        """Seletor do ícone do aplicativo (oficial + alternativas)."""
        from cartridges.utils import app_icon  # noqa: PLC0415

        labels = {
            "jolven-dpad": _("Jolven D-pad (padrão)"),
            "jolven-portal": _("Jolven Portal"),
        }
        self.app_icon_row.set_model(Gtk.StringList.new(list(labels.values())))
        saved = shared.schema.get_string("app-icon")
        if saved not in app_icon.CHOICES:
            shared.schema.set_string("app-icon", app_icon.DEFAULT)
            saved = app_icon.DEFAULT
        self.app_icon_row.set_selected(app_icon.CHOICES.index(saved))

        def selected_changed(widget: Adw.ComboRow, *_args: Any) -> None:
            choice = app_icon.CHOICES[widget.get_selected()]
            shared.schema.set_string("app-icon", choice)
            app_icon.apply(choice)
            self.add_toast(
                Adw.Toast.new(
                    _("Ícone atualizado. Saia e entre de novo se o menu não mudar.")
                )
            )

        self.app_icon_row.connect("notify::selected", selected_changed)

    # (código gettext, nome no próprio idioma). Os nomes não passam por
    # gettext de propósito: o autônimo é igual em todo locale.
    LANGUAGES = [
        ("ar", "العربية"),
        ("be", "Беларуская"),
        ("ca", "Català"),
        ("cs", "Čeština"),
        ("de", "Deutsch"),
        ("el", "Ελληνικά"),
        ("en_GB", "English (UK)"),
        ("es", "Español"),
        ("eu", "Euskara"),
        ("fa", "فارسی"),
        ("fi", "Suomi"),
        ("fr", "Français"),
        ("hi", "हिन्दी"),
        ("hr", "Hrvatski"),
        ("hu", "Magyar"),
        ("ia", "Interlingua"),
        ("ie", "Interlingue"),
        ("it", "Italiano"),
        ("ja", "日本語"),
        ("ka", "ქართული"),
        ("kk", "Қазақша"),
        ("ko", "한국어"),
        ("kw", "Kernewek"),
        ("nb_NO", "Norsk bokmål"),
        ("nl", "Nederlands"),
        ("nn", "Norsk nynorsk"),
        ("pl", "Polski"),
        ("pt", "Português"),
        ("pt_BR", "Português (Brasil)"),
        ("ro", "Română"),
        ("ru", "Русский"),
        ("sv", "Svenska"),
        ("ta", "தமிழ்"),
        ("te", "తెలుగు"),
        ("tr", "Türkçe"),
        ("uk", "Українська"),
        ("vi", "Tiếng Việt"),
        ("zh_Hans", "简体中文"),
    ]

    def setup_language_row(self) -> None:
        """Seletor de idioma: detecção automática ou catálogo fixo.

        O valor é lido pelo launcher (jolven.in) antes do gettext.install,
        por isso a troca só vale após reiniciar o aplicativo.
        """
        codes = [code for code, _name in self.LANGUAGES]
        self.language_row.set_model(
            Gtk.StringList.new(
                [_("Automatic detection"), *[name for _code, name in self.LANGUAGES]]
            )
        )
        saved = shared.schema.get_string("language")
        if saved != "auto" and saved not in codes:
            shared.schema.set_string("language", "auto")
            saved = "auto"
        self.language_row.set_selected(0 if saved == "auto" else codes.index(saved) + 1)

        def selected_changed(widget: Adw.ComboRow, *_args: Any) -> None:
            selected = widget.get_selected()
            shared.schema.set_string(
                "language", "auto" if selected == 0 else codes[selected - 1]
            )
            self.add_toast(
                Adw.Toast.new(_("Restart Jolven to apply the new language"))
            )

        self.language_row.connect("notify::selected", selected_changed)

    def setup_game_overlay_button_row(self) -> None:
        choices = ("mode", "home", "both")
        self.game_overlay_button_row.set_model(
            Gtk.StringList.new(
                [
                    _("Button Mode"),
                    _("Button Home"),
                    _("Both buttons"),
                ]
            )
        )
        saved = shared.schema.get_string("game-overlay-button")
        self.game_overlay_button_row.set_selected(
            choices.index(saved) if saved in choices else choices.index("both")
        )

        def selected_changed(widget: Adw.ComboRow, *_args: Any) -> None:
            selected = widget.get_selected()
            if selected < len(choices):
                shared.schema.set_string("game-overlay-button", choices[selected])

        self.game_overlay_button_row.connect("notify::selected", selected_changed)

    def choose_folder(
        self, _widget: Any, callback: Callable, callback_data: Optional[str] = None
    ) -> None:
        self.file_chooser.select_folder(shared.win, None, callback, callback_data)

    def undo_remove_all(self, *_args: Any) -> bool:
        shared.win.get_application().state = shared.AppState.UNDO_REMOVE_ALL_GAMES
        for game in self.removed_games:
            game.removed = False
            game.save()
            game.update()

        self.removed_games = set()
        self.toast.dismiss()
        shared.win.get_application().state = shared.AppState.DEFAULT
        shared.win.create_source_rows()

        return True

    def remove_all_games(self, *_args: Any) -> None:
        shared.win.get_application().state = shared.AppState.REMOVE_ALL_GAMES
        for game in shared.store:
            if not game.removed:
                self.removed_games.add(game)
                game.removed = True
                game.save()
                game.update()

        if shared.win.navigation_view.get_visible_page() == shared.win.details_page:
            shared.win.navigation_view.pop()

        self.add_toast(self.toast)
        shared.win.get_application().state = shared.AppState.DEFAULT
        shared.win.create_source_rows()

    def _backup_filters(self) -> Gio.ListStore:
        backup_filter = Gtk.FileFilter(name=_("Backup do Jolven"))
        backup_filter.add_suffix("zip")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(backup_filter)
        return filters

    def export_backup(self, *_args: Any) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(f"jolven-backup-{date.today().isoformat()}.zip")
        dialog.set_filters(self._backup_filters())

        def finish(file_dialog: Gtk.FileDialog, result: Gio.Task) -> None:
            try:
                path = Path(file_dialog.save_finish(result).get_path())
            except GLib.Error:
                return
            settings = backup.ler_configuracoes()
            progress = Adw.Toast(title=_("Exportando backup…"), timeout=0)
            self.add_toast(progress)

            def work() -> None:
                try:
                    backup.exportar(path, settings)
                except Exception as error:  # pylint: disable=broad-exception-caught
                    logging.exception("Não foi possível exportar o backup")
                    entregar_na_tela(self._export_done, progress, str(error))
                else:
                    entregar_na_tela(self._export_done, progress, None)

            threading.Thread(target=work, daemon=True).start()

        dialog.save(shared.win, None, finish)

    def _export_done(self, progress: Adw.Toast, error: Optional[str]) -> bool:
        progress.dismiss()
        if error:
            create_dialog(
                self,
                _("Não foi possível exportar"),
                _(
                    "Não foi possível gravar o arquivo. Verifique se a pasta "
                    "de destino está acessível e se há espaço livre em disco."
                ),
            )
        else:
            self.add_toast(Adw.Toast.new(_("Backup exportado")))
        return False

    def import_backup(self, *_args: Any) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_filters(self._backup_filters())

        def finish(file_dialog: Gtk.FileDialog, result: Gio.Task) -> None:
            try:
                path = Path(file_dialog.open_finish(result).get_path())
            except GLib.Error:
                return
            self._restore_backup(path)

        dialog.open(shared.win, None, finish)

    def _restore_backup(self, path: Path) -> None:
        def on_response(_dialog: Any, response: str) -> None:
            if response == "restore":
                self._schedule_restore(path)

        dialog = create_dialog(
            self,
            _("Restaurar este backup?"),
            _("Tem certeza que deseja restaurar este backup? Esta ação é irreversível."),
            "restore",
            _("Restaurar"),
        )
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", on_response)

    def _schedule_restore(self, path: Path) -> None:
        progress = Adw.Toast(title=_("Preparando a restauração…"), timeout=0)
        self.add_toast(progress)

        def work() -> None:
            try:
                backup.validar(path)
                backup.agendar(path)
            except backup.BackupInvalido:
                entregar_na_tela(self._restore_invalid, progress)
            except Exception:  # pylint: disable=broad-exception-caught
                logging.exception("Não foi possível agendar a restauração do backup")
                entregar_na_tela(self._restore_failed, progress)
            else:
                entregar_na_tela(self._restart_to_restore)

        threading.Thread(target=work, daemon=True).start()

    def _restore_invalid(self, progress: Adw.Toast) -> bool:
        progress.dismiss()
        create_dialog(
            self,
            _("Backup inválido"),
            _("O arquivo não é um backup válido do Jolven."),
        )
        return False

    def _restore_failed(self, progress: Adw.Toast) -> bool:
        progress.dismiss()
        create_dialog(
            self,
            _("Não foi possível restaurar"),
            _("Não foi possível restaurar o backup. Tente novamente."),
        )
        return False

    def _restart_to_restore(self) -> bool:
        app = shared.win.get_application()
        app.reiniciar = True
        app.quit()
        return False

    def reset_app(self, *_args: Any) -> None:
        for dirname in ("jolven", "cartridges"):
            rmtree(shared.data_dir / dirname, True)
            rmtree(shared.config_dir / dirname, True)
            rmtree(shared.cache_dir / dirname, True)

        for key in (
            (settings_schema_source := Gio.SettingsSchemaSource.get_default())
            .lookup(shared.APP_ID, True)
            .list_keys()
        ):
            shared.schema.reset(key)
        for key in settings_schema_source.lookup(
            shared.APP_ID + ".State", True
        ).list_keys():
            shared.state_schema.reset(key)

        shared.win.get_application().quit()

    def update_source_action_row_paths(self, source: Source) -> None:
        """Set the dir subtitle for a source's action rows"""
        for location_name, location in source.locations._asdict().items():
            # Get the action row to subtitle
            action_row = getattr(
                self, f"{source.source_id}_{location_name}_action_row", None
            )
            if not action_row:
                continue

            subtitle = str(Path(shared.schema.get_string(location.schema_key)))

            # Remove the path prefix if picked via Flatpak portal
            subtitle = re.sub("/run/user/\\d*/doc/.*/", "", subtitle)

            # Replace the home directory with "~"
            subtitle = re.sub(f"^{str(shared.home)}", "~", subtitle)

            action_row.set_subtitle(subtitle)

    def resolve_locations(self, source: Source) -> None:
        """Resolve locations and add a warning if location cannot be found"""

        for location_name, location in source.locations._asdict().items():
            action_row = getattr(
                self, f"{source.source_id}_{location_name}_action_row", None
            )
            if not action_row:
                continue

            try:
                location.resolve()

            except UnresolvableLocationError:
                title = _("Installation Not Found")
                description = _("Select a valid directory")
                format_start = '<span rise="12pt"><b><big>'
                format_end = "</big></b></span>\n"

                popover = Gtk.Popover(
                    focusable=True,
                    child=(
                        Gtk.Label(
                            label=format_start + title + format_end + description,
                            use_markup=True,
                            wrap=True,
                            max_width_chars=50,
                            halign=Gtk.Align.CENTER,
                            valign=Gtk.Align.CENTER,
                            justify=Gtk.Justification.CENTER,
                            margin_top=9,
                            margin_bottom=9,
                            margin_start=12,
                            margin_end=12,
                        )
                    ),
                )

                popover.update_property(
                    (Gtk.AccessibleProperty.LABEL,), (title + description,)
                )

                def set_a11y_label(widget: Gtk.Popover) -> None:
                    self.set_focus(widget)

                popover.connect("show", set_a11y_label)

                menu_button = Gtk.MenuButton(
                    icon_name="dialog-warning-symbolic",
                    valign=Gtk.Align.CENTER,
                    popover=popover,
                    tooltip_text=_("Warning"),
                )
                menu_button.add_css_class("warning")

                action_row.add_prefix(menu_button)
                self.warning_menu_buttons[source.source_id] = menu_button

    def init_source_row(self, source: Source) -> None:
        """Initialize a preference row for a source class"""

        def set_dir(_widget: Any, result: Gio.Task, location_name: str) -> None:
            """Callback called when a dir picker button is clicked"""
            try:
                path = Path(self.file_chooser.select_folder_finish(result).get_path())
            except GLib.Error:
                return

            # Good picked location
            location = source.locations._asdict()[location_name]
            if location.check_candidate(path):
                shared.schema.set_string(location.schema_key, str(path))
                self.update_source_action_row_paths(source)
                if self.warning_menu_buttons.get(source.source_id):
                    action_row = getattr(
                        self, f"{source.source_id}_{location_name}_action_row", None
                    )
                    action_row.remove(  # type: ignore
                        self.warning_menu_buttons[source.source_id]
                    )
                    self.warning_menu_buttons.pop(source.source_id)
                logging.debug("User-set value for %s is %s", location.schema_key, path)

            # Bad picked location, inform user
            else:
                title = _("Invalid Directory")
                dialog = create_dialog(
                    self,
                    title,
                    location.invalid_subtitle.format(source.name),
                    "choose_folder",
                    _("Set Location"),
                )

                def on_response(widget: Any, response: str) -> None:
                    if response == "choose_folder":
                        self.choose_folder(widget, set_dir, location_name)

                dialog.connect("response", on_response)

        # Bind expander row activation to source being enabled
        expander_row = getattr(self, f"{source.source_id}_expander_row")
        shared.schema.bind(
            source.source_id,
            expander_row,
            "enable-expansion",
            Gio.SettingsBindFlags.DEFAULT,
        )

        # Connect dir picker buttons
        for location_name in source.locations._asdict():
            button = getattr(
                self, f"{source.source_id}_{location_name}_file_chooser_button", None
            )
            if button is not None:
                button.connect("clicked", self.choose_folder, set_dir, location_name)

        # Set the source row subtitles
        self.resolve_locations(source)
        self.update_source_action_row_paths(source)
