# window.py
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

import hashlib
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from cartridges import gpu_screen_recorder, shared
from cartridges.botao_tarefas import BotaoTarefas
from cartridges.controller import detect_controller_layout, name_for_button
from cartridges.gamepad_visual import GamepadVisual
from cartridges.game import Game, STATUS_LABELS, status_label
from cartridges.game_cover import GameCover
from cartridges.onscreen_keyboard import OnscreenKeyboardDialog
from cartridges.session_history import SessionHistoryDialog
from cartridges.utils import agrupamento, launcher, restauracao, session_fita, session_log, session_wallpaper, tarefas
from cartridges.utils.animated_flow_box import AnimatedFlowBox
from cartridges.utils.format_playtime import format_playtime, format_stopwatch
from cartridges.utils.install_size import format_size
from cartridges.utils.download import download_bytes
from cartridges.utils.library_background import resolver_fundo
from cartridges.utils.news_feed import NewsPost
from cartridges.utils.open_uri import open_uri
from cartridges.utils.relative_date import relative_date
from cartridges.utils.spring_scroll import attach as attach_spring_scroll
from cartridges.utils.toast_queue import ToastQueue
from cartridges.zerados_picker import ZeradosPicker
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango


_NEWS_THUMB_SIZE = 64
_NEWS_THUMB_BYTES = 2 * 1024 * 1024
_NEWS_THUMB_CACHE_MAX = 200


def can_edit_notes(game: Game) -> bool:
    if game.zerado:
        return False
    return game.status == "playing" or bool((game.notes or "").strip())


_XCLOUD_VKEY = {
    Gdk.KEY_Return: 0x0D,
    Gdk.KEY_KP_Enter: 0x0D,
    Gdk.KEY_Tab: 0x09,
    Gdk.KEY_ISO_Left_Tab: 0x09,
    Gdk.KEY_Escape: 0x1B,
    Gdk.KEY_space: 0x20,
    Gdk.KEY_BackSpace: 0x08,
    Gdk.KEY_Left: 0x25,
    Gdk.KEY_Up: 0x26,
    Gdk.KEY_Right: 0x27,
    Gdk.KEY_Down: 0x28,
    Gdk.KEY_Home: 0x24,
    Gdk.KEY_End: 0x23,
    Gdk.KEY_Page_Up: 0x21,
    Gdk.KEY_Page_Down: 0x22,
    Gdk.KEY_Delete: 0x2E,
    Gdk.KEY_Insert: 0x2D,
    Gdk.KEY_Shift_L: 0x10,
    Gdk.KEY_Shift_R: 0x10,
    Gdk.KEY_Control_L: 0x11,
    Gdk.KEY_Control_R: 0x11,
    Gdk.KEY_Alt_L: 0x12,
    Gdk.KEY_Alt_R: 0x12,
    Gdk.KEY_Super_L: 0x5B,
    Gdk.KEY_Super_R: 0x5B,
    Gdk.KEY_semicolon: 0xBA,
    Gdk.KEY_equal: 0xBB,
    Gdk.KEY_comma: 0xBC,
    Gdk.KEY_minus: 0xBD,
    Gdk.KEY_period: 0xBE,
    Gdk.KEY_slash: 0xBF,
    Gdk.KEY_grave: 0xC0,
    Gdk.KEY_bracketleft: 0xDB,
    Gdk.KEY_backslash: 0xDC,
    Gdk.KEY_bracketright: 0xDD,
    Gdk.KEY_apostrophe: 0xDE,
}

for _i in range(12):
    _XCLOUD_VKEY[getattr(Gdk, f"KEY_F{_i + 1}")] = 0x70 + _i


def _xcloud_keyval_to_vkey(keyval: int) -> Optional[int]:
    if 0x30 <= keyval <= 0x39:
        return keyval
    if 0x41 <= keyval <= 0x5A:
        return keyval
    if 0x61 <= keyval <= 0x7A:
        return keyval - 0x20
    return _XCLOUD_VKEY.get(keyval)


_XCLOUD_MOUSE_BUTTONS = {
    1: 0x01,
    3: 0x02,
    2: 0x04,
    8: 0x08,
    9: 0x10,
}


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/window.ui")
class CartridgesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "CartridgesWindow"

    overlay_split_view: Adw.OverlaySplitView = Gtk.Template.Child()
    navigation_view: Adw.NavigationView = Gtk.Template.Child()
    game_mode_home_page: Adw.NavigationPage = Gtk.Template.Child()
    game_mode_avatar: Adw.Avatar = Gtk.Template.Child()
    game_mode_welcome_label: Gtk.Label = Gtk.Template.Child()
    game_mode_continue_button: Gtk.Button = Gtk.Template.Child()
    game_mode_library_button: Gtk.Button = Gtk.Template.Child()
    sidebar_navigation_page: Adw.NavigationPage = Gtk.Template.Child()
    sidebar: Gtk.ListBox = Gtk.Template.Child()
    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    session_overlay: Gtk.Overlay = Gtk.Template.Child()
    session_blocker: Gtk.Box = Gtk.Template.Child()
    session_blocker_label: Gtk.Label = Gtk.Template.Child()
    session_blocker_button: Gtk.Button = Gtk.Template.Child()
    session_return_button: Gtk.Button = Gtk.Template.Child()
    gpu_screen_recorder_button: Gtk.Button = Gtk.Template.Child()
    session_blocker_notes_button: Gtk.MenuButton = Gtk.Template.Child()
    session_blocker_notes_popover: Gtk.Popover = Gtk.Template.Child()
    session_blocker_notes_view: Gtk.TextView = Gtk.Template.Child()
    session_blocker_timer: Gtk.Label = Gtk.Template.Child()
    primary_menu_button: Gtk.MenuButton = Gtk.Template.Child()
    show_sidebar_button: Gtk.Button = Gtk.Template.Child()
    details_view: Gtk.Overlay = Gtk.Template.Child()
    library_page: Adw.NavigationPage = Gtk.Template.Child()
    library_view: Adw.ToolbarView = Gtk.Template.Child()
    library: AnimatedFlowBox = Gtk.Template.Child()
    scrolledwindow: Gtk.ScrolledWindow = Gtk.Template.Child()
    library_overlay: Gtk.Overlay = Gtk.Template.Child()
    library_background_stack: Gtk.Stack = Gtk.Template.Child()
    library_background_a: Gtk.Picture = Gtk.Template.Child()
    library_background_b: Gtk.Picture = Gtk.Template.Child()
    notice_empty: Adw.StatusPage = Gtk.Template.Child()
    notice_no_results: Adw.StatusPage = Gtk.Template.Child()
    search_bar: Gtk.SearchBar = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    search_button: Gtk.ToggleButton = Gtk.Template.Child()
    game_mode_keyboard_button: Gtk.Button = Gtk.Template.Child()
    launchers_bar: Gtk.Box = Gtk.Template.Child()
    launchers_box: Gtk.Box = Gtk.Template.Child()

    gamepad_test_page: Adw.NavigationPage = Gtk.Template.Child()
    gamepad_test_device_label: Gtk.Label = Gtk.Template.Child()
    gamepad_test_layout_label: Gtk.Label = Gtk.Template.Child()
    gamepad_visual_slot: Gtk.Box = Gtk.Template.Child()
    gamepad_test_event_label: Gtk.Label = Gtk.Template.Child()
    gamepad_test_buttons: Gtk.FlowBox = Gtk.Template.Child()
    gamepad_test_axis_0: Gtk.Label = Gtk.Template.Child()
    gamepad_test_axis_1: Gtk.Label = Gtk.Template.Child()
    gamepad_test_axis_2: Gtk.Label = Gtk.Template.Child()
    gamepad_test_axis_3: Gtk.Label = Gtk.Template.Child()
    gamepad_test_axis_4: Gtk.Label = Gtk.Template.Child()
    gamepad_test_axis_5: Gtk.Label = Gtk.Template.Child()
    gamepad_test_hat_label: Gtk.Label = Gtk.Template.Child()

    details_page: Adw.NavigationPage = Gtk.Template.Child()
    details_view_toolbar_view: Adw.ToolbarView = Gtk.Template.Child()
    details_view_cover: Gtk.Picture = Gtk.Template.Child()
    details_view_spinner: Adw.Spinner = Gtk.Template.Child()
    details_view_title: Gtk.Label = Gtk.Template.Child()
    details_view_blurred_cover: Gtk.Picture = Gtk.Template.Child()
    details_view_play_button: Gtk.Button = Gtk.Template.Child()
    details_view_developer: Gtk.Label = Gtk.Template.Child()
    details_view_metadata: Gtk.Label = Gtk.Template.Child()
    details_view_description: Gtk.Label = Gtk.Template.Child()
    details_view_screenshots_group: Gtk.Box = Gtk.Template.Child()
    details_view_screenshots: Gtk.Box = Gtk.Template.Child()
    details_view_added: Gtk.ShortcutLabel = Gtk.Template.Child()
    details_view_last_played: Gtk.Label = Gtk.Template.Child()
    details_view_size: Gtk.Label = Gtk.Template.Child()
    details_view_update_notice: Gtk.Button = Gtk.Template.Child()
    details_view_playtime: Gtk.Label = Gtk.Template.Child()
    details_view_status_button: Gtk.MenuButton = Gtk.Template.Child()
    details_view_notes_box: Gtk.Box = Gtk.Template.Child()
    details_view_notes: Gtk.Label = Gtk.Template.Child()
    details_view_notes_button: Gtk.MenuButton = Gtk.Template.Child()
    details_view_notes_popover: Gtk.Popover = Gtk.Template.Child()
    details_view_notes_view: Gtk.TextView = Gtk.Template.Child()
    details_view_delete_button: Gtk.Button = Gtk.Template.Child()
    details_view_hide_button: Gtk.Button = Gtk.Template.Child()

    hidden_library_page: Adw.NavigationPage = Gtk.Template.Child()
    hidden_primary_menu_button: Gtk.MenuButton = Gtk.Template.Child()
    hidden_library: Gtk.FlowBox = Gtk.Template.Child()
    hidden_library_view: Adw.ToolbarView = Gtk.Template.Child()
    hidden_scrolledwindow: Gtk.ScrolledWindow = Gtk.Template.Child()
    hidden_library_overlay: Gtk.Overlay = Gtk.Template.Child()
    hidden_notice_empty: Adw.StatusPage = Gtk.Template.Child()
    hidden_notice_no_results: Adw.StatusPage = Gtk.Template.Child()
    hidden_search_bar: Gtk.SearchBar = Gtk.Template.Child()
    hidden_search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    hidden_search_button: Gtk.ToggleButton = Gtk.Template.Child()
    hidden_game_mode_keyboard_button: Gtk.Button = Gtk.Template.Child()

    zerados_library_page: Adw.NavigationPage = Gtk.Template.Child()
    zerados_primary_menu_button: Gtk.MenuButton = Gtk.Template.Child()
    zerados_library: AnimatedFlowBox = Gtk.Template.Child()
    zerados_scrolledwindow: Gtk.ScrolledWindow = Gtk.Template.Child()
    zerados_library_overlay: Gtk.Overlay = Gtk.Template.Child()
    zerados_notice_empty: Adw.StatusPage = Gtk.Template.Child()
    zerados_notice_no_results: Adw.StatusPage = Gtk.Template.Child()

    news_button: Gtk.Button = Gtk.Template.Child()
    news_badge: Gtk.Box = Gtk.Template.Child()
    news_page: Adw.NavigationPage = Gtk.Template.Child()
    news_stack: Gtk.Stack = Gtk.Template.Child()
    news_list: Gtk.ListBox = Gtk.Template.Child()
    news_scrolledwindow: Gtk.ScrolledWindow = Gtk.Template.Child()
    news_refresh_button: Gtk.Button = Gtk.Template.Child()
    news_retry_button: Gtk.Button = Gtk.Template.Child()
    xcloud_refresh_button: Gtk.Button = Gtk.Template.Child()
    xcloud_refresh_spinner: Adw.Spinner = Gtk.Template.Child()

    xcloud_player_page: Adw.NavigationPage = Gtk.Template.Child()
    xcloud_header_bar: Adw.HeaderBar = Gtk.Template.Child()
    xcloud_close_button: Gtk.Button = Gtk.Template.Child()
    xcloud_menu_button: Gtk.Button = Gtk.Template.Child()
    xcloud_fullscreen_button: Gtk.Button = Gtk.Template.Child()
    xcloud_box: Gtk.Overlay = Gtk.Template.Child()
    xcloud_spinner: Adw.Spinner = Gtk.Template.Child()
    xcloud_container: Gtk.Box = Gtk.Template.Child()
    xcloud_error: Adw.StatusPage = Gtk.Template.Child()
    xcloud_retry_button: Gtk.Button = Gtk.Template.Child()

    game_covers: dict = {}
    toasts: dict = {}
    toast_queue: ToastQueue
    active_game: Game
    news_checker: Optional[Any] = None
    _news_handler_ids: list = []
    _news_refresh_toast: Optional[Adw.Toast] = None
    _news_refresh_requested = False
    _xcloud_refreshing = False
    _xcloud_refresh_toast: Optional[Adw.Toast] = None
    session_game: Optional[Game] = None
    session_timer_id: int = 0
    _game_overlay_open = False
    botao_tarefas: BotaoTarefas
    _xcloud_controller: Optional[Any] = None
    _xcloud_retry_game: Optional[Any] = None
    _xcloud_pending_game: Optional[Any] = None
    _xcloud_deps_pending_game: Optional[Any] = None
    _xcloud_deps_waiting: bool = False
    _xcloud_deps_dialog: Optional[Any] = None
    _xcloud_deps_status: Optional[Any] = None
    _xcloud_was_fullscreen: bool = False
    _xcloud_axis_state: dict[int, float] = {}
    _xcloud_trigger_state: dict[str, float] = {}
    _xcloud_widget: Optional[Any] = None
    _xcloud_mouse_buttons: int = 0
    _playtime_clickable = False
    details_view_game_cover: Optional[GameCover] = None
    _details_images_generation: int = 0
    _fundo_biblioteca_geracao: int = 0
    _fundo_biblioteca_jogo: Optional[str] = None
    _fundo_biblioteca_lado: bool = False
    sort_state: str = "last_played"
    active_source_filters: set[str]
    known_source_filters: set[str]
    source_rows: dict

    def add_toast(self, toast: Adw.Toast) -> None:
        self.toast_queue.add(toast)

    def _get_disabled_source_filters(self) -> set[str]:
        try:
            return set(shared.schema.get_strv("disabled-source-filters"))
        except Exception:  # pylint: disable=broad-exception-caught
            return set()

    def _save_disabled_source_filters(self) -> None:
        try:
            previous = self._get_disabled_source_filters()
            disabled = (previous | (self.known_source_filters - self.active_source_filters)) - self.active_source_filters
            shared.schema.set_strv("disabled-source-filters", sorted(disabled))
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def create_source_rows(self) -> None:
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())

        counts: dict[str, int] = {}
        for game in shared.store:
            if game.removed or game.hidden or game.blacklisted:
                continue
            if game.base_source == "xcloud" and not shared.schema.get_boolean(
                "xbox-cloud-gaming"
            ):
                continue
            filter_id = self._source_filter_id(game)
            counts[filter_id] = counts.get(filter_id, 0) + 1

        self.known_source_filters.update(counts)
        disabled = self._get_disabled_source_filters()
        self.active_source_filters = set(counts) - disabled

        while row := self.sidebar.get_row_at_index(1):
            self.sidebar.remove(row)
        self.source_rows.clear()

        def sort_key(item: tuple[str, int]) -> tuple:
            source_id, games_no = item
            priority = {
                "imported": 0,
                "xcloud_gamepass": 1,
                "xcloud_free": 2,
                "xcloud_owned": 3,
            }.get(source_id, 4)
            return (priority, -games_no, self._source_filter_name(source_id).casefold())

        for source_id, games_no in sorted(counts.items(), key=sort_key):
            content = Gtk.Box(
                margin_top=12,
                margin_bottom=12,
                margin_start=6,
                margin_end=6,
                spacing=12,
            )
            content.append(self._source_filter_image(source_id, theme))
            content.append(
                Gtk.Label(
                    label=self._source_filter_name(source_id),
                    halign=Gtk.Align.START,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.CHAR,
                )
            )
            content.append(
                count_label := Gtk.Label(
                    label=str(games_no),
                    hexpand=True,
                    halign=Gtk.Align.END,
                )
            )
            count_label.add_css_class("dim-label")

            button = Gtk.ToggleButton(
                active=source_id in self.active_source_filters,
                child=content,
                hexpand=True,
                css_classes=["flat", "source-filter-toggle"],
                tooltip_text=_("Left click toggles • Right click shows only this"),
            )
            button.connect("toggled", self._source_filter_toggled, source_id)
            right = Gtk.GestureClick.new()
            right.set_button(3)
            right.connect("released", self._on_source_filter_right_click, source_id)
            button.add_controller(right)
            self.sidebar.append(button)
            self.source_rows[button] = (source_id, games_no)

        header = self.sidebar.get_row_at_index(0)
        if header is not None:
            header.set_visible(bool(counts))
        self._update_source_filter_title()
        self.library.invalidate_filter()
        self.set_library_child()
        self.atualizar_launchers()

    @staticmethod
    def _source_filter_id(game: Game) -> str:
        if game.base_source != "xcloud":
            return game.base_source
        if game.source in ("xcloud_free", "xcloud_gamepass", "xcloud_owned"):
            return game.source
        return "xcloud_gamepass"

    def _source_filter_name(self, source_id: str) -> str:
        names = {
            "xcloud_free": _("Xbox Cloud Gaming — Grátis"),
            "xcloud_gamepass": _("Xbox Cloud Gaming — Game Pass"),
            "xcloud_owned": _("Xbox Cloud Gaming — Comprados"),
        }
        return names.get(source_id, self.get_application().get_source_name(source_id))

    def _source_filter_image(
        self, source_id: str, theme: Gtk.IconTheme
    ) -> Gtk.Image:
        if source_id.startswith("xcloud_"):
            image = Gtk.Image.new_from_resource(shared.PREFIX + "/xbox-cloud.png")
            image.set_pixel_size(18)
            return image
        if source_id == "imported":
            return Gtk.Image.new_from_icon_name("list-add-symbolic")
        return Gtk.Image.new_from_icon_name(
            self._icone_disponivel(
                theme,
                launcher.nomes_de_icone_da_fonte(source_id),
            )
        )

    def _source_filter_toggled(
        self, button: Gtk.ToggleButton, source_id: str
    ) -> None:
        if button.get_active():
            self.active_source_filters.add(source_id)
        else:
            self.active_source_filters.discard(source_id)
        self._save_disabled_source_filters()
        self._update_source_filter_title()
        self.library.invalidate_filter()
        self.set_library_child()

    def _on_source_filter_right_click(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
        source_id: str,
    ) -> None:
        """Botão direito isola a plataforma: só ela fica marcada."""
        self._isolate_source_filter(source_id)

    def _isolate_source_filter(self, source_id: str) -> None:
        available = {sid for sid, _count in self.source_rows.values()}
        if source_id not in available:
            return
        if self.active_source_filters == {source_id}:
            return
        self.active_source_filters = {source_id}
        for button, (current_id, _count) in self.source_rows.items():
            is_target = current_id == source_id
            if button.get_active() != is_target:
                button.set_active(is_target)
        self._save_disabled_source_filters()
        self._update_source_filter_title()
        self.library.invalidate_filter()
        self.set_library_child()

    def _update_source_filter_title(self) -> None:
        available = {source_id for source_id, _count in self.source_rows.values()}
        selected = self.active_source_filters & available
        if not available:
            title = _("Biblioteca")
        elif len(selected) == 1:
            title = self._source_filter_name(next(iter(selected)))
        elif selected == available and available:
            title = _("Biblioteca")
        else:
            title = _("{} plataformas").format(len(selected))
        self.library_page.set_title(title)

    def enable_source_filter(self, source_id: str) -> None:
        """Garante que uma plataforma fique visível sem apagar as demais."""
        self.active_source_filters.add(source_id)
        self._save_disabled_source_filters()
        for button, (current_id, _count) in self.source_rows.items():
            if current_id == source_id:
                button.set_active(True)
                break
        self._update_source_filter_title()
        self.library.invalidate_filter()
        self.set_library_child()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.active_source_filters = set()
        self.known_source_filters = set()
        self.source_rows = {}

        self._news_thumb_cache: dict[str, Gdk.Texture] = {}
        self._news_thumb_pending: dict[str, list[Gtk.Picture]] = {}

        self.game_mode_home_page.set_visible(shared.runtime.is_game_mode)
        if not shared.runtime.is_game_mode:
            # The landing page is the template's initial navigation root.
            # Hiding it is not enough: leaving it below the library makes
            # HeaderBar expose a Back button that pops to an invisible page.
            self.navigation_view.replace([self.library_page])
        elif shared.schema.get_boolean("game-mode-start-library"):
            self.navigation_view.push(self.library_page)

        self.details_view.set_measure_overlay(self.details_view_toolbar_view, True)
        self.details_view.set_clip_overlay(self.details_view_toolbar_view, False)

        self.library.set_filter_func(self.filter_func)
        self.hidden_library.set_filter_func(self.filter_func)
        self.zerados_library.set_filter_func(self.filter_func)

        self.library.set_sort_func(self.sort_func)
        self.hidden_library.set_sort_func(self.sort_func)
        self.zerados_library.set_sort_func(self.sort_func)
        self.zerados_library.set_max_children_per_line(999)

        attach_spring_scroll(
            self.scrolledwindow, self.hidden_scrolledwindow, self.zerados_scrolledwindow
        )

        self.toast_queue = ToastQueue(self.toast_overlay)

        self.botao_tarefas = BotaoTarefas(self)
        self.session_overlay.add_overlay(self.botao_tarefas)
        tarefas.lista.connect("items-changed", self.botao_tarefas.ao_mudar_lista)
        self.navigation_view.connect("notify::visible-page", self.botao_tarefas.reavaliar)
        self.connect("notify::visible-dialog", self.botao_tarefas.reavaliar)
        self.session_blocker_button.connect("clicked", self.on_session_blocker_clicked)
        self.session_return_button.connect("clicked", self.return_to_game)
        self.gpu_screen_recorder_button.connect(
            "clicked", self.open_gpu_screen_recorder
        )
        self.session_blocker_notes_popover.connect(
            "notify::visible", self.on_session_notes_popover_toggled
        )

        add_zerado = Gio.SimpleAction.new("add_zerado", None)
        add_zerado.connect("activate", lambda *_: ZeradosPicker().present(self))
        self.add_action(add_zerado)

        launch_via = Gio.SimpleAction.new("launch_via", GLib.VariantType.new("s"))
        launch_via.connect("activate", self.on_launch_via_action)
        self.add_action(launch_via)

        desagrupar = Gio.SimpleAction.new("desagrupar", GLib.VariantType.new("s"))
        desagrupar.connect("activate", self.on_desagrupar_action)
        self.add_action(desagrupar)

        toggle_launcher = Gio.SimpleAction.new(
            "toggle_launcher", GLib.VariantType.new("s")
        )
        toggle_launcher.connect("activate", self.on_toggle_launcher_action)
        self.add_action(toggle_launcher)

        set_status = Gio.SimpleAction.new("set_status", GLib.VariantType.new("s"))
        set_status.connect("activate", self.on_set_status_action)
        self.add_action(set_status)

        delete_game = Gio.SimpleAction.new("delete_game", None)
        delete_game.connect("activate", self.on_delete_game_action)
        self.add_action(delete_game)

        shared.schema.bind(
            "show-news-button",
            self.news_button,
            "visible",
            Gio.SettingsBindFlags.GET,
        )
        shared.schema.connect(
            "changed::xbox-cloud-gaming", self._xcloud_enabled_changed
        )
        self.connect("notify::fullscreened", self._update_xcloud_fullscreen_button)
        self.news_refresh_button.connect("clicked", self.on_news_refresh_clicked)
        self.news_retry_button.connect("clicked", self.on_news_refresh_clicked)
        self.xcloud_retry_button.connect("clicked", self.on_xcloud_retry_action)
        self.xcloud_refresh_button.connect(
            "clicked", self._on_xcloud_refresh_clicked
        )
        self._update_xcloud_refresh_visible()

        self.set_library_child()

        self.notice_empty.set_icon_name(shared.APP_ID + "-symbolic")

        self.overlay_split_view.set_show_sidebar(
            shared.state_schema.get_boolean("show-sidebar")
        )

        if shared.PROFILE == "development":
            self.add_css_class("devel")

        # Connect search entries
        self.search_bar.connect_entry(self.search_entry)
        self.hidden_search_bar.connect_entry(self.hidden_search_entry)

        # Connect signals
        self.search_entry.connect("search-changed", self.search_changed, False)
        self.hidden_search_entry.connect("search-changed", self.search_changed, True)

        for button, entry in (
            (self.game_mode_keyboard_button, self.search_entry),
            (self.hidden_game_mode_keyboard_button, self.hidden_search_entry),
        ):
            # Controller input is supported in both the desktop and Gaming
            # Mode interfaces, so controller-only text entry must be too.
            button.set_visible(True)
            button.connect("clicked", self.show_gamepad_keyboard, entry)

        self.search_entry.connect("activate", self.show_details_page_search)
        self.hidden_search_entry.connect("activate", self.show_details_page_search)
        self.details_view_update_notice.connect(
            "clicked", self.on_update_notice_clicked
        )
        self.details_view_notes_popover.connect(
            "notify::visible", self.on_notes_popover_toggled
        )
        click = Gtk.GestureClick.new()
        click.connect("released", self.on_playtime_activated)
        self.details_view_playtime.add_controller(click)

        self.navigation_view.connect("popped", self.set_show_hidden)
        self.navigation_view.connect("pushed", self.set_show_hidden)
        self.navigation_view.connect("popped", self._schedule_library_refocus)
        self.navigation_view.connect("pushed", self._schedule_library_refocus)
        self.navigation_view.connect(
            "notify::visible-page", self._schedule_library_refocus
        )

        for grade in (self.library, self.hidden_library, self.zerados_library):
            grade.connect("child-activated", self.on_library_child_activated)

        self._gamepad_button_widgets = {}
        for code in (304, 305, 307, 308, 310, 311, 314, 315, 316, 317, 318):
            label = Gtk.Label(
                label=name_for_button(code),
                width_request=92,
                height_request=44,
                css_classes=["gamepad-input"],
            )
            self._gamepad_button_widgets[code] = label
            self.gamepad_test_buttons.append(label)

        self._gamepad_visual = None
        try:
            self._gamepad_visual = GamepadVisual()
            self.gamepad_visual_slot.append(self._gamepad_visual)
        except Exception:  # A falha do visual não pode quebrar a janela.
            logging.exception("Could not build the gamepad visual")
            self._gamepad_visual = None

        style_manager = Adw.StyleManager.get_default()
        style_manager.connect("notify::dark", self.set_details_view_opacity)
        style_manager.connect("notify::high-contrast", self.set_details_view_opacity)

        # Allow for a custom number of rows for the library
        if shared.schema.get_uint("library-rows"):
            shared.schema.bind(
                "library-rows",
                self.library,
                "max-children-per-line",
                Gio.SettingsBindFlags.DEFAULT,
            )
            shared.schema.bind(
                "library-rows",
                self.hidden_library,
                "max-children-per-line",
                Gio.SettingsBindFlags.DEFAULT,
            )
        else:
            self.library.set_max_children_per_line(10)
            self.hidden_library.set_max_children_per_line(10)

    def configure_user_profile(self, profile: Any) -> None:
        self.game_mode_welcome_label.set_label(
            _("Welcome, {}").format(profile.display_name)
        )
        self.game_mode_avatar.set_text(profile.display_name)
        if profile.avatar is not None:
            try:
                self.game_mode_avatar.set_custom_image(
                    Gdk.Texture.new_from_filename(str(profile.avatar))
                )
            except GLib.Error:
                pass

    def update_game_mode_home(self) -> None:
        games = [
            game
            for game in shared.store
            if not game.removed and game.executable and game.last_played > 0
        ]
        recent = max(games, key=lambda game: game.last_played, default=None)
        self.game_mode_continue_button.set_sensitive(recent is not None)
        self.game_mode_continue_button.set_tooltip_text(
            recent.name if recent is not None else _("No recently played game")
        )

    def focus_game_mode_home(self) -> None:
        if self.navigation_view.get_visible_page() == self.game_mode_home_page:
            (
                self.game_mode_continue_button
                if self.game_mode_continue_button.get_sensitive()
                else self.game_mode_library_button
            ).grab_focus()

    def on_open_library_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() != self.library_page:
            self.navigation_view.push(self.library_page)
        GLib.idle_add(self.focus_first_library_tile)

    def gamepad_back(self) -> None:
        dialog = self.get_visible_dialog()
        if dialog is not None:
            dialog.close()
        elif self._game_overlay_open and self.session_game is not None:
            self.return_to_game()
        elif self.navigation_view.get_visible_page() not in (
            self.game_mode_home_page,
            self.library_page,
        ):
            self.navigation_view.pop()
        elif (
            shared.runtime.is_game_mode
            and self.navigation_view.get_visible_page() == self.library_page
        ):
            self.navigation_view.pop()

    @property
    def gamepad_test_active(self) -> bool:
        return self.navigation_view.get_visible_page() == self.gamepad_test_page

    def on_open_gamepad_test_action(self, *_args: Any) -> None:
        if not self.gamepad_test_active:
            self.navigation_view.push(self.gamepad_test_page)

    def set_gamepad_devices(self, names: list[str]) -> None:
        self.gamepad_test_device_label.set_label(
            _("Conectado: {}").format(", ".join(names))
            if names
            else _("Nenhum gamepad conectado")
        )
        layout = (
            detect_controller_layout(names[0]).value if names else "generic"
        )
        badge = {
            "xbox": _("Controle Xbox"),
            "playstation": _("Controle PlayStation"),
            "generic": _("Controle genérico"),
        }.get(layout, _("Controle genérico"))
        if not names:
            badge = _("Layout não identificado")
        try:
            self.gamepad_test_layout_label.set_label(badge)
        except AttributeError:
            pass
        if self._gamepad_visual is not None:
            try:
                self._gamepad_visual.set_layout(layout)
            except Exception:
                logging.exception("Could not apply the gamepad layout")

    def update_gamepad_button(self, button: int, pressed: bool) -> None:
        if self.xcloud_input_active:
            self._xcloud_feed_button(button, pressed)
        name = name_for_button(button)
        self.gamepad_test_event_label.set_label(
            _("{} pressionado").format(name)
            if pressed
            else _("{} solto").format(name)
        )
        label = self._gamepad_button_widgets.get(button)
        if label is None:
            label = Gtk.Label(
                label=name,
                width_request=92,
                height_request=44,
                css_classes=["gamepad-input"],
            )
            self._gamepad_button_widgets[button] = label
            self.gamepad_test_buttons.append(label)
        if pressed:
            label.add_css_class("active")
        else:
            label.remove_css_class("active")
        if self._gamepad_visual is not None:
            try:
                self._gamepad_visual.set_button_pressed(button, pressed)
            except Exception:
                logging.exception("Could not update the gamepad visual")

    def update_gamepad_axis(self, axis: int, value: float) -> None:
        if self.xcloud_input_active:
            self._xcloud_feed_axis(axis, value)
        self.gamepad_test_event_label.set_label(
            _("Eixo {}: {:.2f}").format(axis, value)
        )
        label = getattr(self, f"gamepad_test_axis_{axis}", None)
        if label is not None:
            label.set_label(f"{value:+.2f}")
        if self._gamepad_visual is not None:
            try:
                self._gamepad_visual.set_axis(axis, value)
            except Exception:
                logging.exception("Could not update the gamepad visual")

    def update_gamepad_trigger(self, trigger: str, pressed: bool) -> None:
        """Reflect digital trigger events in the trigger axis rows."""
        if self.xcloud_input_active:
            self._xcloud_feed_trigger(trigger, pressed)
        value = 1.0 if pressed else 0.0
        axis = 2 if trigger == "LT" else 5
        getattr(self, f"gamepad_test_axis_{axis}").set_label(f"{value:.2f}")
        self.gamepad_test_event_label.set_label(
            _("{}: {:.2f}").format(trigger, value)
        )
        if self._gamepad_visual is not None:
            try:
                self._gamepad_visual.set_trigger(trigger, pressed)
            except Exception:
                logging.exception("Could not update the gamepad visual")

    def update_gamepad_hat(self, axis: int, value: int) -> None:
        if self.xcloud_input_active:
            self._xcloud_feed_hat(axis, value)
        directions = {
            15: _("esquerda"),
            16: _("cima"),
            17: _("direita"),
            18: _("baixo"),
        }
        direction = (
            _("centralizado")
            if value == 0
            else directions.get(axis + value, str(value))
        )
        self.gamepad_test_hat_label.set_label(
            _("Direcional {}: {}").format(axis, direction)
        )
        self.gamepad_test_event_label.set_label(
            _("Direcional: {}").format(direction)
        )
        if self._gamepad_visual is not None:
            try:
                self._gamepad_visual.set_hat(axis, value)
            except Exception:
                logging.exception("Could not update the gamepad visual")

    def gamepad_search(self) -> None:
        page = self.navigation_view.get_visible_page()
        if page not in (self.library_page, self.hidden_library_page):
            if page == self.game_mode_home_page:
                self.navigation_view.push(self.library_page)
            else:
                self.navigation_view.pop_to_page(self.library_page)
        self.on_toggle_search_action()

    @staticmethod
    def _game_from_widget(widget: Gtk.Widget | None) -> Game | None:
        while widget is not None:
            if isinstance(widget, Game):
                return widget
            if isinstance(widget, Gtk.FlowBoxChild) and isinstance(
                widget.get_child(), Game
            ):
                return widget.get_child()
            widget = widget.get_parent()
        return None

    def _game_from_focus(self) -> Game | None:
        return self._game_from_widget(self.get_focus())

    def _focus_in_library_chrome(self) -> bool:
        widget = self.get_focus()
        while widget is not None:
            if widget in (
                self.search_entry,
                self.hidden_search_entry,
                self.launchers_bar,
                self.sidebar,
            ):
                return True
            widget = widget.get_parent()
        return False

    def get_visible_library(self) -> Gtk.FlowBox | None:
        page = self.navigation_view.get_visible_page()
        if page == self.library_page:
            return self.library
        if page == self.hidden_library_page:
            return self.hidden_library
        if page == self.zerados_library_page:
            return self.zerados_library
        return None

    def focus_first_library_tile(self) -> bool:
        grade = self.get_visible_library()
        if grade is None:
            return GLib.SOURCE_REMOVE
        index = 0
        while child := grade.get_child_at_index(index):
            index += 1
            if not child.get_visible():
                continue
            child.grab_focus()
            if isinstance(child.get_child(), Game):
                self.atualizar_fundo_biblioteca(child.get_child())
            return GLib.SOURCE_REMOVE
        if first_launcher := self.launchers_box.get_first_child():
            first_launcher.grab_focus()
        return GLib.SOURCE_REMOVE

    def gamepad_navigate(self, direction: Gtk.DirectionType) -> None:
        moved = self.child_focus(direction)
        if (game := self._game_from_focus()) is not None:
            self.atualizar_fundo_biblioteca(game)
            return
        if not moved and self.get_visible_library() is not None:
            self.focus_first_library_tile()

    def gamepad_confirm(self) -> None:
        if (game := self._game_from_focus()) is not None:
            game.main_button_clicked(None, False)
            return
        if (focus := self.get_focus()) is not None:
            focus.activate()

    def on_library_child_activated(
        self, _flowbox: Gtk.FlowBox, child: Gtk.FlowBoxChild
    ) -> None:
        if isinstance(child.get_child(), Game):
            self.show_details_page(child.get_child())

    def _schedule_library_refocus(self, *_args: Any) -> None:
        GLib.idle_add(self._refocus_library_if_needed)

    def _refocus_library_if_needed(self) -> bool:
        page = self.navigation_view.get_visible_page()
        if page == self.game_mode_home_page:
            self.focus_game_mode_home()
        elif self.get_visible_library() is not None:
            if (
                self._game_from_focus() is None
                and not self._focus_in_library_chrome()
            ):
                self.focus_first_library_tile()
        return GLib.SOURCE_REMOVE

    def gamepad_game_menu(self) -> None:
        if (game := self._game_from_focus()) is not None:
            game.menu_revealer.set_reveal_child(True)
            game.menu_button.popup()

    def gamepad_main_menu(self) -> None:
        if self.navigation_view.get_visible_page() not in (
            self.library_page,
            self.hidden_library_page,
            self.zerados_library_page,
        ):
            if self.navigation_view.get_visible_page() == self.game_mode_home_page:
                self.navigation_view.push(self.library_page)
            else:
                self.navigation_view.pop_to_page(self.library_page)
        GLib.idle_add(self.on_open_menu_action)

    def gamepad_toggle_sidebar(self) -> None:
        self.on_show_sidebar_action()

    def show_gamepad_keyboard(
        self, _button: Gtk.Button, entry: Gtk.SearchEntry
    ) -> None:
        """Show the full on-screen keyboard for controller-only entry."""
        dialog = OnscreenKeyboardDialog(entry)
        dialog.present(self)
        GLib.idle_add(dialog.focus_first_key)

    def search_changed(self, _widget: Any, hidden: bool) -> None:
        # Refresh search filter on keystroke in search box
        (self.hidden_library if hidden else self.library).invalidate_filter()
        self.set_library_child()

    def _refresh_library_filters(self) -> None:
        self.library.invalidate_filter()
        self.hidden_library.invalidate_filter()
        self.zerados_library.invalidate_filter()
        self.set_library_child()

    def set_library_child(self) -> None:
        child, hidden_child, zerados_child = (
            self.notice_empty,
            self.hidden_notice_empty,
            self.zerados_notice_empty,
        )

        for game in shared.store:
            if game.blacklisted or restauracao.e_pendente(game.game_id):
                continue
            if game.base_source == "xcloud" and not shared.schema.get_boolean(
                "xbox-cloud-gaming"
            ):
                continue
            if game.is_launcher and not game.hidden:
                continue
            if game.removed:
                if not game.zerado:
                    continue
                if game.filtered and zerados_child:
                    zerados_child = self.zerados_notice_no_results
                    continue
                zerados_child = None
            elif game.hidden:
                if game.filtered and hidden_child:
                    hidden_child = self.hidden_notice_no_results
                    continue
                hidden_child = None
            else:
                if game.filtered and child:
                    child = self.notice_no_results
                    continue
                child = None

        def remove_from_overlay(widget: Gtk.Widget) -> None:
            if isinstance(widget.get_parent(), Gtk.Overlay):
                widget.get_parent().remove_overlay(widget)

        if child:
            self.library_overlay.add_overlay(child)
        else:
            remove_from_overlay(self.notice_empty)
            remove_from_overlay(self.notice_no_results)

        if hidden_child:
            self.hidden_library_overlay.add_overlay(hidden_child)
        else:
            remove_from_overlay(self.hidden_notice_empty)
            remove_from_overlay(self.hidden_notice_no_results)

        for notice in (self.zerados_notice_empty, self.zerados_notice_no_results):
            if notice is not zerados_child:
                remove_from_overlay(notice)
        if zerados_child:
            self.zerados_library_overlay.add_overlay(zerados_child)

    def filter_func(self, child: Gtk.Widget) -> bool:
        game = child.get_child()
        if restauracao.e_pendente(game.game_id):
            return False
        parent = child.get_parent()
        em_zerados = parent is self.zerados_library if parent else game.zerado
        text = (
            ""
            if em_zerados
            else (
                (
                    self.hidden_search_entry
                    if self.navigation_view.get_visible_page()
                    == self.hidden_library_page
                    else self.search_entry
                )
                .get_text()
                .lower()
            )
        )

        members = [game] if em_zerados else agrupamento.membros(game)

        def casa(member: Game) -> bool:
            return (
                text in member.name.lower()
                or (text in member.developer.lower() if member.developer else False)
                or (text in member.publisher.lower() if member.publisher else False)
                or (text in member.notes.lower() if member.notes else False)
            )

        filtered = text != "" and not any(casa(member) for member in members)

        if not filtered and not em_zerados:
            if not any(
                self._source_filter_id(member) in self.active_source_filters
                for member in members
            ):
                filtered = True

        game.filtered = filtered

        return not filtered

    def set_active_game(self, _widget: Any, _pspec: Any, game: Game) -> None:
        self.active_game = game

    def show_details_page(self, game: Game) -> None:
        self.active_game = game

        self.details_view_cover.set_opacity(int(not game.loading))
        self.details_view_spinner.set_visible(game.loading)

        self.details_view_developer.set_label(game.developer or "")
        self.details_view_developer.set_visible(bool(game.developer))

        metadata = []
        if game.tgdb_platform:
            metadata.append(_("Plataforma: {}").format(game.tgdb_platform))
        if game.release_date:
            metadata.append(_("Lançamento: {}").format(game.release_date))
        if game.publisher:
            metadata.append(_("Publicadora: {}").format(game.publisher))
        if game.genre:
            metadata.append(_("Gênero: {}").format(game.genre))
        if game.tgdb_players:
            metadata.append(_("Jogadores: {}").format(game.tgdb_players))
        if game.tgdb_age_rating:
            metadata.append(_("Classificação: {}").format(game.tgdb_age_rating))
        if game.tgdb_coop:
            metadata.append(_("Cooperativo: {}").format(game.tgdb_coop))
        self.details_view_metadata.set_label("  •  ".join(metadata))
        self.details_view_metadata.set_visible(bool(metadata))

        description = (game.description or "").strip()
        self.details_view_description.set_label(description)
        self.details_view_description.set_visible(bool(description))
        self._load_details_screenshots(game)

        icon, text = "view-conceal-symbolic", _("Hide")
        if game.hidden:
            icon, text = "view-reveal-symbolic", _("Unhide")

        self.details_view_hide_button.set_icon_name(icon)
        self.details_view_hide_button.set_tooltip_text(text)

        if self.details_view_game_cover:
            self.details_view_game_cover.pictures.remove(self.details_view_cover)

        self.details_view_game_cover = game.game_cover
        self.details_view_game_cover.add_picture(self.details_view_cover)

        self.details_view_blurred_cover.set_paintable(
            self.details_view_game_cover.get_blurred()
        )

        self.details_view_title.set_label(game.name)
        self.details_page.set_title(game.name)

        date = relative_date(game.added)
        self.details_view_added.set_label(
            # The variable is the date when the game was added
            _("Added: {}").format(date)
        )
        last_played_date = (
            relative_date(game.last_played) if game.last_played else _("Never")
        )
        self.details_view_last_played.set_label(
            # The variable is the date when the game was last played
            _("Last played: {}").format(last_played_date)
        )
        self.update_details_notice(game)
        self.update_install_size_label(game)
        self.update_status_button(game)
        self.update_notes_block(game)
        self.update_playtime_label(game)

        if self.navigation_view.get_visible_page() != self.details_page:
            self.navigation_view.push(self.details_page)
            self.set_focus(self.details_view_play_button)

        self.set_details_view_opacity()

    def _load_details_screenshots(self, game: Game) -> None:
        """Populate cached TheGamesDB screenshots without blocking GTK."""
        self._details_images_generation += 1
        generation = self._details_images_generation
        while child := self.details_view_screenshots.get_first_child():
            self.details_view_screenshots.remove(child)

        urls = list(game.tgdb_screenshots or [])[:6]
        self.details_view_screenshots_group.set_visible(bool(urls))
        if not urls:
            return

        cache_dir = shared.cache_dir / "jolven" / "thegamesdb" / game.game_id

        def worker() -> None:
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return
            for url in urls:
                suffix = Path(urlparse(url).path).suffix.lower()
                if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
                    suffix = ".jpg"
                filename = hashlib.sha256(url.encode("utf-8")).hexdigest() + suffix
                path = cache_dir / filename
                try:
                    if not path.is_file():
                        path.write_bytes(download_bytes(url, timeout=20))
                except Exception as error:  # pylint: disable=broad-except
                    logging.info("Could not load TheGamesDB screenshot: %s", error)
                    continue
                GLib.idle_add(self._add_details_screenshot, path, game, generation)

        threading.Thread(target=worker, daemon=True).start()

    def _add_details_screenshot(
        self, path: Path, game: Game, generation: int
    ) -> bool:
        if generation != self._details_images_generation or self.active_game is not game:
            return GLib.SOURCE_REMOVE
        try:
            texture = Gdk.Texture.new_from_filename(str(path))
        except GLib.Error as error:
            logging.info("Invalid TheGamesDB screenshot: %s", error)
            path.unlink(missing_ok=True)
            return GLib.SOURCE_REMOVE
        picture = Gtk.Picture(paintable=texture)
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(240, 135)
        picture.set_overflow(Gtk.Overflow.HIDDEN)
        picture.add_css_class("card")
        self.details_view_screenshots.append(picture)
        return GLib.SOURCE_REMOVE

    def atualizar_fundo_biblioteca(self, game: Game) -> None:
        """Troca o fundo da grade para a arte do jogo em foco, sem bloquear.

        Resolve fora da UI (rede: IGDB → TheGamesDB → wallhaven) e aplica na
        volta com guarda de geração: se o foco já mudou, o resultado vencido
        é descartado. Em caso de falha, não faz nada — o fundo fica como está.
        """
        self._fundo_biblioteca_geracao += 1
        geracao = self._fundo_biblioteca_geracao
        self._fundo_biblioteca_jogo = game.game_id

        def worker() -> None:
            try:
                caminho = resolver_fundo(game)
            except Exception as erro:  # pylint: disable=broad-except
                logging.info("Não foi possível resolver o fundo: %s", erro)
                return
            if caminho is None:
                return
            GLib.idle_add(
                self._aplicar_fundo_biblioteca, str(caminho), game.game_id, geracao
            )

        threading.Thread(target=worker, daemon=True).start()

    def _aplicar_fundo_biblioteca(
        self, caminho: str, game_id: str, geracao: int
    ) -> bool:
        if (
            geracao != self._fundo_biblioteca_geracao
            or self._fundo_biblioteca_jogo != game_id
        ):
            return GLib.SOURCE_REMOVE
        try:
            textura = Gdk.Texture.new_from_filename(caminho)
        except GLib.Error as erro:
            logging.info("Fundo da biblioteca inválido: %s", erro)
            Path(caminho).unlink(missing_ok=True)
            return GLib.SOURCE_REMOVE
        self._mostrar_fundo_biblioteca(textura)
        return GLib.SOURCE_REMOVE

    def _mostrar_fundo_biblioteca(self, textura: Optional[Gdk.Texture]) -> None:
        """Aplica ``textura`` com fade, alternando as duas faces do Stack.

        A face escondida recebe a imagem nova e vira a visível: o
        ``transition-type: crossfade`` do Stack dissolve uma na outra. Com
        ``None``, dissolve para o vazio.
        """
        self._fundo_biblioteca_lado = not self._fundo_biblioteca_lado
        escondida = (
            self.library_background_b
            if self._fundo_biblioteca_lado
            else self.library_background_a
        )
        escondida.set_paintable(textura)
        self.library_background_stack.set_visible_child(escondida)

    def limpar_fundo_biblioteca(self, game: Optional[Game] = None) -> None:
        """Volta o fundo da grade ao vazio quando o foco sai do tile."""
        if game is not None and game.game_id != self._fundo_biblioteca_jogo:
            return
        self._fundo_biblioteca_geracao += 1
        self._fundo_biblioteca_jogo = None
        self._mostrar_fundo_biblioteca(None)

    def set_details_view_opacity(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() != self.details_page:
            return

        if (
            style_manager := Adw.StyleManager.get_default()
        ).get_high_contrast() or not style_manager.get_system_supports_color_schemes():
            self.details_view_blurred_cover.set_opacity(0.3)
            return

        self.details_view_blurred_cover.set_opacity(
            1 - self.details_view_game_cover.luminance[0]  # type: ignore
            if style_manager.get_dark()
            else self.details_view_game_cover.luminance[1]  # type: ignore
        )

    def sort_func(self, child1: Gtk.Widget, child2: Gtk.Widget) -> int:
        var, order = "name", True

        if self.sort_state in ("newest", "oldest"):
            var, order = "added", self.sort_state == "newest"
        elif self.sort_state == "last_played":
            var = "last_played"
        elif self.sort_state == "install_size":
            val1 = child1.get_child().install_size or 0
            val2 = child2.get_child().install_size or 0
            if val1 != val2:
                return -1 if val1 > val2 else 1
            var, order = "name", False
        elif self.sort_state == "a-z":
            order = False

        def get_value(index: int) -> str:
            return (
                str(getattr((child1.get_child(), child2.get_child())[index], var))
                .lower()
                .removeprefix("the ")
            )

        if var != "name" and get_value(0) == get_value(1):
            var, order = "name", False

        return ((get_value(0) > get_value(1)) ^ order) * 2 - 1

    def set_show_hidden(self, navigation_view: Adw.NavigationView, *_args: Any) -> None:
        self.lookup_action("show_hidden").set_enabled(
            navigation_view.get_visible_page() == self.library_page
        )

    def on_show_sidebar_action(self, *_args: Any) -> None:
        shared.state_schema.set_boolean(
            "show-sidebar", (value := not self.overlay_split_view.get_show_sidebar())
        )
        self.overlay_split_view.set_show_sidebar(value)

    def on_go_to_parent_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() == self.details_page:
            self.navigation_view.pop()

    def on_go_home_action(self, *_args: Any) -> None:
        self.navigation_view.pop_to_page(self.library_page)

    def on_show_hidden_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() == self.hidden_library_page:
            return

        self.navigation_view.push(self.hidden_library_page)

    def on_show_zerados_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() == self.zerados_library_page:
            return

        self.navigation_view.push(self.zerados_library_page)

    def attach_news_checker(self, checker: Any) -> None:
        self.news_checker = checker
        self._news_handler_ids = [
            checker.connect("posts-changed", self.on_news_posts_changed),
            checker.connect("poll-finished", self.on_news_poll_finished),
            checker.connect("unseen-changed", self.on_news_unseen_changed),
        ]
        self.news_badge.set_visible(checker.has_unseen)
        self.rebuild_news_list()
        self.update_news_view()

    def detach_news_checker(self) -> None:
        if (checker := self.news_checker) is not None:
            for handler_id in self._news_handler_ids:
                if checker.handler_is_connected(handler_id):
                    checker.disconnect(handler_id)
        self._news_handler_ids = []
        self.news_checker = None

    def on_news_unseen_changed(self, _checker: Any, unseen: bool) -> None:
        self.news_badge.set_visible(unseen)

    def on_news_posts_changed(self, *_args: Any) -> None:
        self.rebuild_news_list()
        self.update_news_view()
        if (
            self.navigation_view.get_visible_page() == self.news_page
            and self.news_checker is not None
        ):
            self.news_checker.mark_seen()

    def on_news_poll_finished(self, _checker: Any, success: bool) -> None:
        self.update_news_view()
        if not self._news_refresh_requested:
            return
        self._news_refresh_requested = False
        self.dismiss_news_refresh_toast()
        self.toast_queue.add(
            Adw.Toast.new(
                _("Novidades atualizadas")
                if success
                else _("Não foi possível atualizar as novidades")
            )
        )

    def on_news_refresh_clicked(self, *_args: Any) -> None:
        if (checker := self.news_checker) is None:
            return
        self._news_refresh_requested = True
        self.dismiss_news_refresh_toast()
        toast = Adw.Toast.new(_("Atualizando novidades…"))
        toast.set_timeout(0)
        self._news_refresh_toast = toast
        self.toast_queue.add(toast)
        checker.check_async()
        self.update_news_view()

    def dismiss_news_refresh_toast(self) -> None:
        if self._news_refresh_toast is not None:
            self.toast_queue.dismiss(self._news_refresh_toast)
            self._news_refresh_toast = None

    def on_show_news_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() == self.news_page:
            return
        if (checker := self.news_checker) is not None:
            checker.mark_seen()
            if not checker.posts:
                checker.check_async()
        self.update_news_view()
        self.navigation_view.push(self.news_page)

    def update_news_view(self) -> None:
        checker = self.news_checker
        if checker is not None and checker.posts:
            name = "list"
        elif checker is not None and checker.loading:
            name = "loading"
        else:
            name = "empty"
        self.news_stack.set_visible_child_name(name)

    def rebuild_news_list(self) -> None:
        while child := self.news_list.get_first_child():
            self.news_list.remove(child)
        if self.news_checker is None:
            return
        for post in self.news_checker.posts:
            self.news_list.append(self.build_news_row(post))
        self.news_scrolledwindow.get_vadjustment().set_value(0)

    def build_news_row(self, post: NewsPost) -> Gtk.ListBoxRow:
        children: list[Gtk.Widget] = []
        if post.summary:
            children.append(self.build_news_summary_row(post.summary))
        if post.url:
            open_row = Adw.ActionRow(activatable=True)
            open_row.set_use_markup(False)
            open_row.set_title(_("Abrir no navegador"))
            open_row.add_suffix(
                Gtk.Image(
                    icon_name="adw-external-link-symbolic",
                    valign=Gtk.Align.CENTER,
                    css_classes=["dim-label"],
                )
            )
            open_row.connect("activated", self.on_news_row_activated, post.url)
            children.append(open_row)
        row = Adw.ExpanderRow() if children else Adw.ActionRow()
        row.set_use_markup(False)
        row.set_title(post.title)
        row.set_title_lines(0)
        if subtitle := " · ".join(
            part
            for part in (
                post.source,
                relative_date(post.timestamp) if post.timestamp else "",
                post.category,
            )
            if part
        ):
            row.set_subtitle(subtitle)
            row.set_subtitle_lines(0)
        if post.image:
            thumbnail = Gtk.Picture(
                width_request=_NEWS_THUMB_SIZE,
                height_request=_NEWS_THUMB_SIZE,
                content_fit=Gtk.ContentFit.COVER,
                valign=Gtk.Align.CENTER,
            )
            thumbnail.add_css_class("card")
            row.add_prefix(thumbnail)
            self._queue_news_thumbnail(thumbnail, post.image)
        for child in children:
            row.add_row(child)
        return row

    def _queue_news_thumbnail(self, picture: Gtk.Picture, url: str) -> None:
        """Paint `picture` with the cached thumbnail, or download it off-thread."""
        if cached := self._news_thumb_cache.get(url):
            picture.set_paintable(cached)
            return
        pending = self._news_thumb_pending.setdefault(url, [])
        pending.append(picture)
        if len(pending) > 1:
            return
        threading.Thread(
            target=self._fetch_news_thumbnail, args=(url,), daemon=True
        ).start()

    def _fetch_news_thumbnail(self, url: str) -> None:
        data: Optional[bytes] = None
        try:
            data = download_bytes(url, timeout=10, max_bytes=_NEWS_THUMB_BYTES)
        except Exception:  # pylint: disable=broad-exception-caught
            logging.debug("News thumbnail failed to download: %s", url)
        GLib.idle_add(self._apply_news_thumbnail, url, data)

    def _apply_news_thumbnail(self, url: str, data: Optional[bytes]) -> bool:
        """Decode downloaded bytes and paint every row still waiting for `url`."""
        pictures = self._news_thumb_pending.pop(url, [])
        if data:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except GLib.Error:
                texture = None
            if texture is not None:
                if len(self._news_thumb_cache) >= _NEWS_THUMB_CACHE_MAX:
                    self._news_thumb_cache.clear()
                self._news_thumb_cache[url] = texture
                for picture in pictures:
                    if picture.get_parent() is not None:
                        picture.set_paintable(texture)
        return False

    @staticmethod
    def build_news_summary_row(text: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(activatable=False, selectable=False)
        row.set_child(
            Gtk.Label(
                label=text,
                wrap=True,
                xalign=0,
                margin_top=12,
                margin_bottom=12,
                margin_start=12,
                margin_end=12,
                css_classes=["body"],
            )
        )
        return row

    def on_news_row_activated(self, _row: Adw.ActionRow, url: str) -> None:
        open_uri(url, self)

    def on_sort_action(self, action: Gio.SimpleAction, state: GLib.Variant) -> None:
        action.set_state(state)
        self.sort_state = str(state).strip("'")
        self.library.invalidate_sort()

        shared.state_schema.set_string("sort-mode", self.sort_state)

    def on_toggle_search_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() == self.library_page:
            search_bar = self.search_bar
            search_entry = self.search_entry
        elif self.navigation_view.get_visible_page() == self.hidden_library_page:
            search_bar = self.hidden_search_bar
            search_entry = self.hidden_search_entry
        else:
            return

        search_bar.set_search_mode(not (search_mode := search_bar.get_search_mode()))

        if not search_mode:
            self.set_focus(search_entry)

        search_entry.set_text("")

    def show_details_page_search(self, widget: Gtk.Widget) -> None:
        library = (
            self.hidden_library if widget == self.hidden_search_entry else self.library
        )
        index = 0

        while True:
            if not (child := library.get_child_at_index(index)):
                break

            if self.filter_func(child):
                self.show_details_page(child.get_child())
                break

            index += 1

    def on_undo_action(
        self, _widget: Any, game: Optional[Game] = None, undo: Optional[str] = None
    ) -> None:
        if not game:  # If the action was activated via Ctrl + Z
            if shared.importer and (
                shared.importer.imported_game_ids or shared.importer.removed_game_ids
            ):
                shared.importer.undo_import()
                return

            try:
                game = tuple(self.toasts.keys())[-1][0]
                undo = tuple(self.toasts.keys())[-1][1]
            except IndexError:
                return

        if game:
            if undo == "hide":
                game.toggle_hidden(False)

            elif undo == "remove":
                game.removed = False
                game.save()
                game.update()

            self.toasts[(game, undo)].dismiss()
            self.toasts.pop((game, undo))

    def on_open_menu_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() == self.library_page:
            self.primary_menu_button.popup()
        elif self.navigation_view.get_visible_page() == self.hidden_library_page:
            self.hidden_primary_menu_button.popup()
        elif self.navigation_view.get_visible_page() == self.zerados_library_page:
            self.zerados_primary_menu_button.popup()

    def on_close_action(self, *_args: Any) -> None:
        self.close()

    def update_details_notice(self, game: Game) -> None:
        self.details_view_update_notice.set_visible(game.has_update and not game.zerado)

    def on_update_notice_clicked(self, *_args: Any) -> None:
        game = self.active_game
        if game is None or not game.has_update:
            return

        open_uri(getattr(game, "update_url", ""), self)

        dialog = Adw.AlertDialog.new(
            _("Você instalou a atualização?"),
            _(
                "A página da atualização foi aberta no navegador. Confirme após "
                "instalá-la para ocultar o aviso."
            ),
        )
        dialog.add_response("cancel", _("Ainda não"))
        dialog.add_response("confirm", _("Já instalei"))
        dialog.set_response_appearance(
            "confirm", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.set_default_response("confirm")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_update_notice_response, game)
        dialog.present(self)

    def on_update_notice_response(
        self, _dialog: Adw.AlertDialog, response: str, game: Game
    ) -> None:
        if response != "confirm":
            return
        game.dismiss_update()
        if self.active_game is game:
            self.update_details_notice(game)

    def update_install_size_label(self, game: Game) -> None:
        text = "" if game.zerado else format_size(game.install_size)
        self.details_view_size.set_visible(bool(text))
        if text:
            self.details_view_size.set_label(_("Tamanho: {}").format(text))

    def update_status_button(self, game: Game) -> None:
        self.details_view_status_button.set_label(
            status_label(game.status) or _("Definir status")
        )
        self.details_view_status_button.set_menu_model(self.build_status_menu())
        self.details_view_delete_button.set_visible(game.zerado)

    @staticmethod
    def build_status_menu() -> Gio.Menu:
        menu = Gio.Menu()
        section = Gio.Menu()
        for value, label in STATUS_LABELS.items():
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.set_status", GLib.Variant("s", value))
            section.append_item(item)
        menu.append_section(None, section)

        clear = Gio.Menu()
        item = Gio.MenuItem.new(_("Sem status"), None)
        item.set_action_and_target_value("win.set_status", GLib.Variant("s", ""))
        clear.append_item(item)
        menu.append_section(None, clear)
        return menu

    def on_set_status_action(self, _action: Any, target: GLib.Variant) -> None:
        game = getattr(self, "active_game", None)
        if game is None:
            return

        era_zerado = game.zerado
        game.definir_status(target.get_string())
        game.save()
        game.update()

        if era_zerado and game.removed and not game.zerado:
            self.navigation_view.pop()
            return
        if era_zerado and not game.zerado:
            self.show_details_page(game)

        self.update_status_button(game)
        self.update_notes_block(game)
        if getattr(self, "filter_status_state", ""):
            self.library.invalidate_filter()
            self.zerados_library.invalidate_filter()
            self.set_library_child()

    def build_card_menu(
        self, game: Game, members: Optional[list] = None
    ) -> Gio.Menu:
        grouped = bool(members and len(members) > 1)
        menu = Gio.Menu()
        if grouped:
            vias = Gio.Menu()
            for member in sorted(members, key=lambda m: m.name.casefold()):
                item = Gio.MenuItem.new(
                    self.get_application().get_source_name(member.source), None
                )
                item.set_action_and_target_value(
                    "win.launch_via", GLib.Variant("s", member.game_id)
                )
                vias.append_item(item)
            # The variable is the section title listing the launchers
            menu.append_section(_("Jogar via"), vias)

        base = Gio.Menu()
        for label, action in (
            (_("Edit"), "app.edit_game"),
            (_("Unhide") if game.hidden else _("Hide"), "app.hide_game"),
            (_("Remove"), "app.remove_game"),
            (_("Add to Sunshine"), "app.add_to_sunshine"),
        ):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(action, None)
            base.append_item(item)
        menu.append_section(None, base)

        alternar = Gio.Menu()
        item = Gio.MenuItem.new(
            _("Marcar como jogo")
            if game.is_launcher
            else _("Marcar como launcher"),
            None,
        )
        item.set_action_and_target_value(
            "win.toggle_launcher", GLib.Variant("s", game.game_id)
        )
        alternar.append_item(item)
        menu.append_section(None, alternar)

        if grouped:
            grupo = Gio.Menu()
            item = Gio.MenuItem.new(_("Desagrupar"), None)
            item.set_action_and_target_value(
                "win.desagrupar", GLib.Variant("s", agrupamento.atualizar(game))
            )
            grupo.append_item(item)
            menu.append_section(None, grupo)
        return menu

    def on_launch_via_action(self, _action: Any, target: GLib.Variant) -> None:
        game = shared.store.get(target.get_string())
        if game is None:
            return
        agrupamento.definir_preferido(game)
        for member in agrupamento.membros(game):
            member.update()
        game.launch()

    def on_desagrupar_action(self, _action: Any, target: GLib.Variant) -> None:
        chave = target.get_string()
        agrupamento.desagrupar_chave(chave)
        for game in shared.store:
            if agrupamento.atualizar(game) == chave:
                game.update()

    def on_toggle_launcher_action(
        self, _action: Any, target: GLib.Variant
    ) -> None:
        game = shared.store.get(target.get_string())
        if game is None:
            return
        chave = agrupamento.atualizar(game)
        game.is_launcher = not game.is_launcher
        launcher.registrar(game, game.is_launcher)
        game.save()
        for outro in shared.store:
            if agrupamento.atualizar(outro) == chave:
                outro.update()
        agrupamento.reconciliar()

    def atualizar_launchers(self) -> None:
        while (child := self.launchers_box.get_first_child()) is not None:
            self.launchers_box.remove(child)
        items = launcher.listar()
        # The gamepad tester is always present in this bar, even when there are
        # no external launchers configured.
        self.launchers_bar.set_visible(True)
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        for game in items:
            icon_name = self._icone_disponivel(
                theme,
                launcher.nomes_de_icone(game),
            )
            button = Gtk.Button(
                tooltip_text=game.name,
                css_classes=["flat", "circular"],
                width_request=40,
                height_request=40,
                valign=Gtk.Align.CENTER,
            )
            button.set_child(Gtk.Image.new_from_icon_name(icon_name))
            button.connect("clicked", self.on_launcher_clicked, game)
            right = Gtk.GestureClick.new()
            right.set_button(3)
            right.connect("released", self.on_launcher_menu, game, button)
            button.add_controller(right)
            self.launchers_box.append(button)

    def _xcloud_enabled_changed(self, *_args: Any) -> None:
        self.atualizar_launchers()
        enabled = shared.schema.get_boolean("xbox-cloud-gaming")
        self._update_xcloud_refresh_visible()
        app = self.get_application()
        games = tuple(shared.store.source_games.get("xcloud", {}).values())
        iterator = iter(games)
        previous_state = app.state
        app.state = shared.AppState.LOAD_FROM_DISK

        if not games:
            self._xcloud_enabled_finished(previous_state, enabled)
            return
        from gi.repository import GLib

        try:
            for _index in range(24):
                try:
                    next(iterator).update()
                except StopIteration:
                    self._xcloud_enabled_finished(previous_state, enabled)
                    return
        except StopIteration:
            pass
        GLib.idle_add(self._xcloud_enabled_step, iterator, previous_state, enabled)

    def _xcloud_enabled_step(self, iterator: Any, previous_state: Any, enabled: bool) -> bool:
        from gi.repository import GLib

        try:
            for _index in range(24):
                try:
                    next(iterator).update()
                except StopIteration:
                    return self._xcloud_enabled_finished(previous_state, enabled)
        except StopIteration:
            return self._xcloud_enabled_finished(previous_state, enabled)
        return GLib.SOURCE_CONTINUE

    def _xcloud_enabled_finished(self, previous_state: Any, enabled: bool) -> bool:
        from gi.repository import GLib

        app = self.get_application()
        try:
            app.state = previous_state
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        self.set_library_child()
        self.library.invalidate_filter()
        self.set_library_child()
        self.create_source_rows()
        if enabled:
            from cartridges.xcloud_catalog import sync_async

            sync_async(
                on_complete=self._xcloud_refresh_done,
                on_error=self._xcloud_refresh_failed,
            )
        return GLib.SOURCE_REMOVE

    def _update_xcloud_refresh_visible(self) -> None:
        try:
            enabled = shared.schema.get_boolean("xbox-cloud-gaming")
        except Exception:  # pylint: disable=broad-exception-caught
            enabled = False
        self.xcloud_refresh_button.set_visible(enabled)
        if not enabled:
            self._set_xcloud_refreshing(False)

    def _set_xcloud_refreshing(self, busy: bool) -> None:
        self._xcloud_refreshing = busy
        self.xcloud_refresh_button.set_sensitive(not busy)
        self.xcloud_refresh_spinner.set_visible(busy)

    def _on_xcloud_refresh_clicked(self, *_args: Any) -> None:
        from cartridges.utils.na_tela import entregar_na_tela

        if self._xcloud_refreshing:
            self.toast_queue.add(
                Adw.Toast.new(_("Sincronização do Xbox Cloud em andamento"))
            )
            return
        from cartridges.xcloud_catalog import sync_async

        started = sync_async(
            on_complete=lambda total: entregar_na_tela(
                self._xcloud_refresh_done, total
            ),
            on_error=lambda error: entregar_na_tela(
                self._xcloud_refresh_failed, error
            ),
            force=True,
        )
        if not started:
            self.toast_queue.add(
                Adw.Toast.new(_("Sincronização do Xbox Cloud em andamento"))
            )
            return
        self._set_xcloud_refreshing(True)
        toast = Adw.Toast.new(_("Atualizando catálogo Xbox Cloud…"))
        toast.set_timeout(0)
        toast.set_use_markup(False)
        self.toast_queue.add(toast)
        self._xcloud_refresh_toast = toast

    def _xcloud_refresh_done(self, total: int) -> None:
        self._set_xcloud_refreshing(False)
        if getattr(self, "_xcloud_refresh_toast", None) is not None:
            self.toast_queue.dismiss(self._xcloud_refresh_toast)
            self._xcloud_refresh_toast = None
        toast = Adw.Toast.new(
            _("Catálogo Xbox Cloud atualizado: {} jogos").format(total)
        )
        toast.set_use_markup(False)
        self.toast_queue.add(toast)
        self.create_source_rows()

    def _xcloud_refresh_failed(self, error: Any) -> None:
        self._set_xcloud_refreshing(False)
        if getattr(self, "_xcloud_refresh_toast", None) is not None:
            self.toast_queue.dismiss(self._xcloud_refresh_toast)
            self._xcloud_refresh_toast = None
        logging.warning("Refresh manual do xCloud falhou: %s", error)
        toast = Adw.Toast.new(
            _("Não foi possível atualizar o Xbox Cloud (offline?)")
        )
        toast.set_use_markup(False)
        self.toast_queue.add(toast)

    def on_launcher_clicked(self, _button: Gtk.Button, game: Game) -> None:
        game.launch()

    def launch_library_game(self, game: Game) -> None:
        """Joga pela plataforma que tornou o card agrupado visível.

        Um card pode representar, por exemplo, uma instalação local e a
        edição do xCloud. Quando apenas o filtro do xCloud está ativo, lançar
        o primário local faria o botão parecer inerte (ou abriria a cópia
        errada). Preserve o primário quando ele ainda pertence aos filtros
        visíveis; caso contrário, use o membro visível preferido do grupo.
        """
        members = agrupamento.membros(game)
        visible_members = [
            member
            for member in members
            if self._source_filter_id(member) in self.active_source_filters
        ]
        target = game
        if visible_members and game not in visible_members:
            target = agrupamento.primario(visible_members)
        logging.info(
            "Lançando %s via %s (%s)",
            target.name,
            target.source,
            target.executable,
        )
        target.launch()

    def on_launcher_menu(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
        game: Game,
        button: Gtk.Button,
    ) -> None:
        menu = Gio.Menu()
        item = Gio.MenuItem.new(_("Marcar como jogo"), None)
        item.set_action_and_target_value(
            "win.toggle_launcher", GLib.Variant("s", game.game_id)
        )
        menu.append_item(item)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(button)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.popup()

    @staticmethod
    def _icone_disponivel(
        theme: Gtk.IconTheme,
        candidates: tuple[str, ...],
    ) -> str:
        return next(
            (name for name in candidates if theme.has_icon(name)),
            "application-x-executable-symbolic",
        )

    def _is_fullscreened(self) -> bool:
        try:
            return bool(self.is_fullscreen())
        except Exception:
            try:
                return bool(self.get_property("fullscreened"))
            except Exception:
                return False

    def launch_xcloud_game(self, game: Game) -> bool:
        from cartridges import xcloud_login
        from cartridges.xcloud_player import XCloudPlayerController, webrtc_available

        if not shared.schema.get_boolean("xbox-cloud-gaming"):
            logging.warning("Jogo xCloud ignorado porque a integração está desativada")
            self.toast_queue.add(
                Adw.Toast.new(_("Ative o Xbox Cloud Gaming nas preferências"))
            )
            return False

        available, reason = webrtc_available()
        if not available:
            if self._xcloud_offer_deps_install(game):
                return False
            self.toast_queue.add(Adw.Toast.new(reason))
            return False

        if not xcloud_login.has_saved_login():
            self._xcloud_pending_game = game
            xcloud_login.show_login_dialog(
                parent=self, on_finished=self._xcloud_login_finished
            )
            return False

        product_id = self._xcloud_product_id(game)
        if not product_id:
            logging.warning("Jogo xCloud sem product_id: %r", game.executable)
            self.toast_queue.add(
                Adw.Toast.new(_("Não foi possível abrir o jogo no Xbox Cloud Gaming"))
            )
            return False

        self._xcloud_stop_player()
        self._xcloud_retry_game = game
        controller = XCloudPlayerController(
            product_id,
            on_ready=self._xcloud_on_ready,
            on_error=self._xcloud_on_error,
        )
        self._xcloud_controller = controller
        controller.start()

        if self.navigation_view.get_visible_page() != self.xcloud_player_page:
            self._xcloud_was_fullscreen = self._is_fullscreened()
            self.navigation_view.push(self.xcloud_player_page)
        self.xcloud_spinner.set_visible(True)
        self.xcloud_error.set_visible(False)
        self._update_xcloud_fullscreen_button()
        self.xcloud_close_button.grab_focus()
        return True

    def _xcloud_offer_deps_install(self, game: Game) -> bool:
        """Diálogo de auto-instalação quando faltam plugins GStreamer do sistema.

        Retorna True quando assumiu o fluxo (o chamador não deve seguir).
        """
        from cartridges import xcloud_deps  # noqa: PLC0415

        missing = xcloud_deps.missing_elements()
        if not missing:
            return False
        if self.get_visible_dialog() is not None:
            return True
        self._xcloud_deps_pending_game = game
        dialog = Adw.AlertDialog.new(
            _("Dependências do Xbox Cloud Gaming"),
            _(
                "Faltam plugins de streaming do sistema (%s). "
                "O Jolven pode instalar sozinho — o sistema vai pedir sua senha."
            )
            % ", ".join(missing),
        )
        dialog.add_response("later", _("Agora não"))
        dialog.add_response("install", _("Instalar dependências"))
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("later")
        dialog.connect("response", self._on_xcloud_deps_response)
        dialog.present(self)
        return True

    def _on_xcloud_deps_response(
        self, _dialog: Adw.AlertDialog, response: str
    ) -> None:
        if response != "install":
            self._xcloud_deps_pending_game = None
            return
        from cartridges import xcloud_deps  # noqa: PLC0415
        from cartridges.utils.na_tela import entregar_na_tela  # noqa: PLC0415

        spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(32, 32)
        self._xcloud_deps_status = Gtk.Label(label=_("Preparando instalação…"))
        self._xcloud_deps_status.set_wrap(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(spinner)
        box.append(self._xcloud_deps_status)
        progress = Adw.AlertDialog.new(
            _("Instalando dependências"),
            _("Aguarde — o Jolven vai reiniciar sozinho ao concluir."),
        )
        progress.set_extra_child(box)
        self._xcloud_deps_waiting = True
        self._xcloud_deps_dialog = progress
        progress.connect("closed", self._on_xcloud_deps_closed)
        progress.present(self)

        def _line(line: str) -> None:
            entregar_na_tela(self._xcloud_deps_progress, self, line)

        def _done(ok: bool, message: str) -> None:
            entregar_na_tela(self._xcloud_deps_finished, self, ok, message)

        started = xcloud_deps.install_async(_done, on_output=_line)
        if not started:
            self._xcloud_deps_waiting = False
            self._xcloud_deps_dialog = None
            try:
                progress.close()
            except Exception:
                pass
            self._xcloud_deps_pending_game = None
            self.toast_queue.add(
                Adw.Toast.new(
                    _("Instalador indisponível. Rode scripts/install-xcloud-deps.sh com sudo.")
                )
            )

    def _on_xcloud_deps_closed(self, _dialog: Adw.AlertDialog) -> None:
        self._xcloud_deps_waiting = False
        self._xcloud_deps_dialog = None

    def _xcloud_deps_progress(self, line: str) -> None:
        status = getattr(self, "_xcloud_deps_status", None)
        if status is not None:
            status.set_label(line[-160:])

    def _xcloud_deps_finished(self, ok: bool, message: str) -> None:
        if not self._xcloud_deps_waiting:
            self.toast_queue.add(Adw.Toast.new(message))
            return
        self._xcloud_deps_waiting = False
        dialog = self._xcloud_deps_dialog
        self._xcloud_deps_dialog = None
        if ok:
            status = getattr(self, "_xcloud_deps_status", None)
            if status is not None:
                status.set_label(_("Concluído! Reiniciando o Jolven…"))
            GLib.timeout_add_seconds(2, self._xcloud_deps_restart)
        else:
            try:
                if dialog is not None:
                    dialog.close()
            except Exception:
                pass
            self._xcloud_deps_pending_game = None
            self.toast_queue.add(Adw.Toast.new(message))

    def _xcloud_deps_restart(self) -> bool:
        from cartridges import xcloud_deps  # noqa: PLC0415

        dialog = self._xcloud_deps_dialog
        self._xcloud_deps_dialog = None
        try:
            if dialog is not None:
                dialog.close()
        except Exception:
            pass
        try:
            subprocess.Popen(
                xcloud_deps.build_restart_command(sys.argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as error:
            logging.warning("Não foi possível reiniciar o Jolven: %s", error)
            self.toast_queue.add(
                Adw.Toast.new(_("Reinicie o Jolven para concluir."))
            )
            return False
        app = self.get_application()
        if app is not None:
            app.quit()
        return False

    def _xcloud_login_finished(self, resultado: Any) -> None:
        game = self._xcloud_pending_game
        self._xcloud_pending_game = None
        if resultado is not None and game is not None:
            game.launch()

    @staticmethod
    def _xcloud_product_id(game: Game) -> Optional[str]:
        executable = game.executable or ""
        if executable:
            try:
                path = urlparse(executable).path.rstrip("/")
                if path:
                    return path.rsplit("/", 1)[-1]
            except ValueError:
                pass
        game_id = game.game_id or ""
        if game_id.startswith("xcloud_"):
            return game_id.removeprefix("xcloud_")
        return None

    @property
    def xcloud_input_active(self) -> bool:
        controller = self._xcloud_controller
        return (
            controller is not None
            and controller.streaming
            and self.navigation_view.get_visible_page() == self.xcloud_player_page
        )

    def _xcloud_stop_player(self) -> None:
        controller = self._xcloud_controller
        self._xcloud_controller = None
        self._xcloud_widget = None
        self._xcloud_mouse_buttons = 0
        if controller is not None:
            controller.stop()
        while (child := self.xcloud_container.get_first_child()) is not None:
            self.xcloud_container.remove(child)
        self.xcloud_container.set_visible(False)
        self.xcloud_spinner.set_visible(False)
        self.xcloud_error.set_visible(False)

    def _xcloud_on_ready(self, controller: Any) -> None:
        if controller is not self._xcloud_controller:
            return
        widget = controller.build_widget()
        if widget is None:
            self._xcloud_on_error(controller, _("Vídeo indisponível"))
            return
        widget.set_hexpand(True)
        widget.set_vexpand(True)
        widget.set_halign(Gtk.Align.FILL)
        widget.set_valign(Gtk.Align.FILL)
        self.xcloud_container.append(widget)
        self._xcloud_widget = widget
        widget.set_focusable(True)
        self._xcloud_wire_input(widget)
        self.xcloud_container.set_visible(True)
        self.xcloud_error.set_visible(False)
        self.xcloud_spinner.set_visible(False)
        self._update_xcloud_fullscreen_button()
        GLib.idle_add(widget.grab_focus)
        if (
            shared.schema.get_boolean("xcloud-fullscreen-on-start")
            and not self._is_fullscreened()
        ):
            self.fullscreen()

    def _xcloud_on_error(self, controller: Any, message: str) -> None:
        if controller is not self._xcloud_controller:
            return
        self._xcloud_controller = None
        self.xcloud_spinner.set_visible(False)
        self.xcloud_container.set_visible(False)
        self.xcloud_error.set_visible(True)
        if message:
            self.toast_queue.add(Adw.Toast.new(message))
        self.xcloud_retry_button.grab_focus()

    def on_xcloud_retry_action(self, *_args: Any) -> None:
        game = self._xcloud_retry_game
        if game is not None:
            self.launch_xcloud_game(game)

    def show_xcloud_player_menu(self) -> None:
        if self.navigation_view.get_visible_page() != self.xcloud_player_page:
            return
        if self.get_visible_dialog() is not None:
            return
        fullscreened = self._is_fullscreened()
        dialog = Adw.AlertDialog.new(
            _("Xbox Cloud Gaming"),
            _("Opções da transmissão."),
        )
        dialog.add_response(
            "fullscreen",
            _("Sair da tela cheia") if fullscreened else _("Tela cheia"),
        )
        dialog.add_response("close", _("Encerrar transmissão"))
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("fullscreen")
        dialog.set_close_response("fullscreen")
        dialog.connect("response", self._on_xcloud_menu_response)
        dialog.present(self)

    def _on_xcloud_menu_response(
        self, dialog: Adw.AlertDialog, response: str
    ) -> None:
        if response == "fullscreen":
            self.on_toggle_xcloud_fullscreen_action()
        elif response == "close":
            self.on_close_xcloud_action()

    def on_xcloud_player_menu_action(self, *_args: Any) -> None:
        self.show_xcloud_player_menu()

    def _xcloud_feed_button(self, button: int, pressed: bool) -> None:
        from cartridges.xcloud_native import input as xinput

        mapping = {
            304: xinput.BTN_B,
            305: xinput.BTN_A,
            307: xinput.BTN_Y,
            308: xinput.BTN_X,
            310: xinput.BTN_LEFT_SHOULDER,
            311: xinput.BTN_RIGHT_SHOULDER,
            314: xinput.BTN_VIEW,
            315: xinput.BTN_MENU,
            316: xinput.BTN_NEXUS,
            172: xinput.BTN_NEXUS,
            317: xinput.BTN_LEFT_THUMB,
            318: xinput.BTN_RIGHT_THUMB,
        }
        bit = mapping.get(button)
        controller = self._xcloud_controller
        if bit is not None and controller is not None:
            controller.feed_button(bit, pressed)

    def _xcloud_feed_axis(self, axis: int, value: float) -> None:
        if axis not in (0, 1, 2, 3):
            return
        controller = self._xcloud_controller
        if controller is None:
            return
        self._xcloud_axis_state[axis] = value
        state = self._xcloud_axis_state
        controller.feed_axis(
            state.get(0, 0.0),
            state.get(1, 0.0),
            state.get(2, 0.0),
            state.get(3, 0.0),
        )

    def _xcloud_feed_trigger(self, trigger: str, pressed: bool) -> None:
        controller = self._xcloud_controller
        if controller is None:
            return
        self._xcloud_trigger_state[trigger] = 1.0 if pressed else 0.0
        controller.feed_trigger(
            self._xcloud_trigger_state.get("LT", 0.0),
            self._xcloud_trigger_state.get("RT", 0.0),
        )

    def _xcloud_feed_hat(self, axis: int, value: int) -> None:
        controller = self._xcloud_controller
        if controller is None:
            return
        if value == 0:
            controller.feed_hat(0, 0)
            return
        code = axis + value
        dx = dy = 0
        if code == 15:
            dx = -1
        elif code == 16:
            dy = 1
        elif code == 17:
            dx = 1
        elif code == 18:
            dy = -1
        controller.feed_hat(dx, dy)

    def _xcloud_wire_input(self, widget: Any) -> None:
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._xcloud_key_pressed)
        key_controller.connect("key-released", self._xcloud_key_released)
        widget.add_controller(key_controller)

        motion_controller = Gtk.EventControllerMotion()
        motion_controller.connect("motion", self._xcloud_mouse_motion)
        widget.add_controller(motion_controller)

        click_gesture = Gtk.GestureClick()
        click_gesture.connect("pressed", self._xcloud_mouse_pressed)
        click_gesture.connect("released", self._xcloud_mouse_released)
        widget.add_controller(click_gesture)

        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.HORIZONTAL | Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_controller.connect("scroll", self._xcloud_mouse_scroll)
        widget.add_controller(scroll_controller)

    def _xcloud_key_pressed(
        self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, _state: Gdk.ModifierType
    ) -> bool:
        if not self.xcloud_input_active:
            return False
        vkey = _xcloud_keyval_to_vkey(keyval)
        if vkey is None:
            return False
        self._xcloud_controller.feed_keyboard(vkey, True)
        return keyval not in (Gdk.KEY_Escape, Gdk.KEY_F11)

    def _xcloud_key_released(
        self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, _state: Gdk.ModifierType
    ) -> bool:
        if not self.xcloud_input_active:
            return False
        vkey = _xcloud_keyval_to_vkey(keyval)
        if vkey is None:
            return False
        self._xcloud_controller.feed_keyboard(vkey, False)
        return keyval not in (Gdk.KEY_Escape, Gdk.KEY_F11)

    def _xcloud_mouse_motion(
        self, _controller: Gtk.EventControllerMotion, x: float, y: float
    ) -> None:
        if not self.xcloud_input_active:
            return
        widget = self._xcloud_widget
        if widget is None:
            return
        width = max(widget.get_width(), 1)
        height = max(widget.get_height(), 1)
        self._xcloud_controller.feed_mouse(
            x_fraction=min(max(x / width, 0.0), 1.0),
            y_fraction=min(max(y / height, 0.0), 1.0),
            buttons=self._xcloud_mouse_buttons,
        )

    def _xcloud_mouse_pressed(
        self, gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
    ) -> None:
        if not self.xcloud_input_active:
            return
        bit = _XCLOUD_MOUSE_BUTTONS.get(gesture.get_current_button(), 0)
        self._xcloud_mouse_buttons |= bit
        self._xcloud_mouse_motion(None, x, y)

    def _xcloud_mouse_released(
        self, gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
    ) -> None:
        if not self.xcloud_input_active:
            return
        bit = _XCLOUD_MOUSE_BUTTONS.get(gesture.get_current_button(), 0)
        self._xcloud_mouse_buttons &= ~bit
        self._xcloud_mouse_motion(None, x, y)

    def _xcloud_mouse_scroll(
        self, _controller: Gtk.EventControllerScroll, dx: float, dy: float
    ) -> None:
        if not self.xcloud_input_active:
            return
        self._xcloud_controller.feed_mouse(
            buttons=self._xcloud_mouse_buttons,
            wheel_x=int(dx) & 0xFFFFFFFF,
            wheel_y=int(dy) & 0xFFFFFFFF,
        )

    def _update_xcloud_fullscreen_button(self, *_args: Any) -> None:
        fullscreened = self._is_fullscreened()
        self.xcloud_header_bar.set_visible(not fullscreened)
        self.xcloud_fullscreen_button.set_icon_name(
            "view-restore-symbolic" if fullscreened else "view-fullscreen-symbolic"
        )
        self.xcloud_fullscreen_button.set_tooltip_text(
            _("Sair da tela cheia") if fullscreened else _("Tela cheia")
        )

    def on_toggle_xcloud_fullscreen_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() != self.xcloud_player_page:
            return
        if self._is_fullscreened():
            self.unfullscreen()
        else:
            self.fullscreen()

    def on_xcloud_escape_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() != self.xcloud_player_page:
            return
        if self._is_fullscreened():
            self.unfullscreen()
            self.xcloud_fullscreen_button.grab_focus()
        else:
            self.show_xcloud_player_menu()

    def on_close_xcloud_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() != self.xcloud_player_page:
            return
        self._xcloud_stop_player()
        self.navigation_view.pop()
        if not self._xcloud_was_fullscreen and self._is_fullscreened():
            self.unfullscreen()
        if shared.runtime.is_game_mode and (
            self.navigation_view.get_visible_page() == self.game_mode_home_page
        ):
            self.focus_game_mode_home()
        else:
            GLib.idle_add(self.focus_first_library_tile)

    def on_delete_game_action(self, *_args: Any) -> None:
        game = getattr(self, "active_game", None)
        if game is None or not game.zerado:
            return
        dialog = Adw.AlertDialog.new(
            _("Excluir {}?").format(game.name),
            _(
                "Tem certeza que deseja excluir este jogo? "
                "Esta ação é irreversível."
            ),
        )
        dialog.add_response("cancel", _("Cancelar"))
        dialog.add_response("delete", _("Excluir"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_delete_game_response, game)
        dialog.present(self)

    def on_delete_game_response(
        self, _dialog: Adw.AlertDialog, response: str, game: Game
    ) -> None:
        if response != "delete" or not game.zerado:
            return

        self.retirar_da_grade(game)
        shared.store.excluir(game)
        agrupamento.reconciliar()

        if self.navigation_view.get_visible_page() == self.details_page:
            self.navigation_view.pop()
        self.set_library_child()

        toast = Adw.Toast.new(_("{} excluído").format(game.name))
        toast.set_use_markup(False)
        self.toast_queue.add(toast)

    def update_notes_block(self, game: Game) -> None:
        notes = (game.notes or "").strip()
        self.details_view_notes.set_label(notes)
        self.details_view_notes_box.set_visible(bool(notes))
        self.details_view_notes_button.set_visible(can_edit_notes(game))

    def on_notes_popover_toggled(
        self, popover: Gtk.Popover, _pspec: Any
    ) -> None:
        self.sync_notes_editor(
            popover, self.details_view_notes_view, getattr(self, "active_game", None)
        )

    def update_playtime_label(self, game: Game) -> None:
        self.details_view_playtime.set_visible(bool(game.playtime))
        if not game.playtime:
            return

        self.details_view_playtime.set_text(
            _("Tempo de jogo: {}").format(format_playtime(game.playtime))
        )

        self._playtime_clickable = bool(session_log.load(game.game_id))
        self.details_view_playtime.set_tooltip_text(
            _("Ver o histórico de sessões") if self._playtime_clickable else None
        )
        self.details_view_playtime.set_cursor(
            Gdk.Cursor.new_from_name("pointer", None)
            if self._playtime_clickable
            else None
        )
        if self._playtime_clickable:
            self.details_view_playtime.add_css_class("playtime-clickable")
        else:
            self.details_view_playtime.remove_css_class("playtime-clickable")

    def on_playtime_activated(self, *_args: Any) -> None:
        if not self._playtime_clickable:
            return
        game = getattr(self, "active_game", None)
        if game is not None:
            SessionHistoryDialog(game).present(self)

    def retirar_da_grade(self, game: Game) -> None:
        if (parent := game.get_parent()) is not None:
            grade = parent.get_parent()
            if grade is not None:
                grade.remove(game)
            if game.get_parent():
                game.get_parent().set_child()
        self.game_covers.pop(game.game_id, None)

    def session_elapsed(self) -> int:
        from cartridges.process_session import ProcessSession

        if ProcessSession.active is not None:
            return ProcessSession.active.elapsed
        return 0

    def session_tick(self, *_args: Any) -> bool:
        self.session_blocker_timer.set_label(format_stopwatch(self.session_elapsed()))
        return GLib.SOURCE_CONTINUE

    def show_session_blocker(self, game: Game) -> None:
        if self.session_game is game and self.session_blocker.get_visible():
            return

        self.session_game = game
        self._game_overlay_open = False
        self.botao_tarefas.reavaliar()
        self.session_blocker_label.set_label(_("{} em execução").format(game.name))
        self.session_blocker.set_visible(True)
        self.navigation_view.set_sensitive(False)
        self.session_return_button.grab_focus()
        self.session_tick()
        self.session_blocker_timer.set_visible(True)
        self.session_timer_id = GLib.timeout_add_seconds(1, self.session_tick)

        if shared.schema.get_boolean("session-wallpaper"):
            session_wallpaper.comecar(game)
        session_fita.comecar(game)

    def hide_session_blocker(self) -> None:
        self.session_blocker.set_visible(False)
        self.session_game = None
        self._game_overlay_open = False
        self.botao_tarefas.reavaliar()
        self.navigation_view.set_sensitive(True)

        if self.session_timer_id:
            GLib.source_remove(self.session_timer_id)
            self.session_timer_id = 0
        self.session_blocker_timer.set_visible(False)
        session_wallpaper.restaurar()
        session_fita.voltar()

    def on_session_blocker_clicked(self, *_args: Any) -> None:
        from cartridges.process_session import ProcessSession

        if ProcessSession.active is not None:
            ProcessSession.active.stop(record=True)

    def open_game_overlay(self) -> None:
        """Bring the in-game controls to the front of the Gamescope session."""
        if self.session_game is None:
            return
        self._game_overlay_open = True
        self.present()
        GLib.idle_add(self._focus_session_return_button)

    def _focus_session_return_button(self) -> bool:
        self.session_return_button.grab_focus()
        return GLib.SOURCE_REMOVE

    def return_to_game(self, *_args: Any) -> None:
        """Hide Jolven so Gamescope can focus the running game again."""
        if self.session_game is None:
            return
        self._game_overlay_open = False
        self.minimize()

    def open_gpu_screen_recorder(self, *_args: Any) -> None:
        try:
            launched = gpu_screen_recorder.launch()
        except GLib.Error as error:
            logging.warning("Could not launch GPU Screen Recorder: %s", error.message)
            launched = False

        if not launched:
            self.toast_queue.add(
                Adw.Toast.new(
                    _(
                        "GPU Screen Recorder não foi encontrado. Instale o aplicativo e tente novamente."
                    )
                )
            )
            return

        self._game_overlay_open = False
        self.minimize()

    def on_session_notes_popover_toggled(
        self, popover: Gtk.Popover, _pspec: Any
    ) -> None:
        self.sync_notes_editor(
            popover, self.session_blocker_notes_view, self.session_game
        )

    def sync_notes_editor(
        self, popover: Gtk.Popover, view: Gtk.TextView, game: Optional[Game]
    ) -> None:
        if game is None:
            return

        buffer = view.get_buffer()
        if popover.get_visible():
            buffer.set_text(game.notes or "")
            return

        notes = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False
        ).strip()
        if notes == (game.notes or ""):
            return

        game.notes = notes
        game.save()
        game.update()
        refresh = getattr(self, "update_notes_block", None)
        if game is getattr(self, "active_game", None) and refresh is not None:
            refresh(game)
