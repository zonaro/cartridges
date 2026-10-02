# window.py
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

# pyright: reportAssignmentType=none

from sys import platform
from typing import Any, Optional

from cartridges import shared
from cartridges.botao_tarefas import BotaoTarefas
from cartridges.game import Game, STATUS_LABELS, status_label
from cartridges.game_cover import GameCover
from cartridges.session_history import SessionHistoryDialog
from cartridges.utils import restauracao, session_fita, session_log, session_wallpaper, tarefas
from cartridges.utils.animated_flow_box import AnimatedFlowBox
from cartridges.utils.format_playtime import format_playtime, format_stopwatch
from cartridges.utils.install_size import format_size
from cartridges.utils.news_feed import NewsPost
from cartridges.utils.open_uri import open_uri
from cartridges.utils.relative_date import relative_date
from cartridges.utils.spring_scroll import attach as attach_spring_scroll
from cartridges.utils.toast_queue import ToastQueue
from cartridges.zerados_picker import ZeradosPicker
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango


def can_edit_notes(game: Game) -> bool:
    if game.zerado:
        return False
    return game.status == "playing" or bool((game.notes or "").strip())


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/window.ui")
class CartridgesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "CartridgesWindow"

    overlay_split_view: Adw.OverlaySplitView = Gtk.Template.Child()
    navigation_view: Adw.NavigationView = Gtk.Template.Child()
    sidebar_navigation_page: Adw.NavigationPage = Gtk.Template.Child()
    sidebar: Gtk.ListBox = Gtk.Template.Child()
    all_games_row_box: Gtk.Box = Gtk.Template.Child()
    all_games_no_label: Gtk.Label = Gtk.Template.Child()
    added_row_box: Gtk.Box = Gtk.Template.Child()
    added_games_no_label: Gtk.Label = Gtk.Template.Child()
    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    session_overlay: Gtk.Overlay = Gtk.Template.Child()
    session_blocker: Gtk.Box = Gtk.Template.Child()
    session_blocker_label: Gtk.Label = Gtk.Template.Child()
    session_blocker_button: Gtk.Button = Gtk.Template.Child()
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
    notice_empty: Adw.StatusPage = Gtk.Template.Child()
    notice_no_results: Adw.StatusPage = Gtk.Template.Child()
    search_bar: Gtk.SearchBar = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    search_button: Gtk.ToggleButton = Gtk.Template.Child()

    details_page: Adw.NavigationPage = Gtk.Template.Child()
    details_view_toolbar_view: Adw.ToolbarView = Gtk.Template.Child()
    details_view_cover: Gtk.Picture = Gtk.Template.Child()
    details_view_spinner: Adw.Spinner = Gtk.Template.Child()
    details_view_title: Gtk.Label = Gtk.Template.Child()
    details_view_blurred_cover: Gtk.Picture = Gtk.Template.Child()
    details_view_play_button: Gtk.Button = Gtk.Template.Child()
    details_view_developer: Gtk.Label = Gtk.Template.Child()
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

    game_covers: dict = {}
    toasts: dict = {}
    toast_queue: ToastQueue
    active_game: Game
    news_checker: Optional[Any] = None
    _news_handler_ids: list = []
    _news_refresh_toast: Optional[Adw.Toast] = None
    _news_refresh_requested = False
    session_game: Optional[Game] = None
    session_timer_id: int = 0
    botao_tarefas: BotaoTarefas
    _playtime_clickable = False
    details_view_game_cover: Optional[GameCover] = None
    sort_state: str = "last_played"
    filter_state: str = "all"
    source_rows: dict = {}

    def add_toast(self, toast: Adw.Toast) -> None:
        self.toast_queue.add(toast)

    def create_source_rows(self) -> None:
        def get_removed(source_id: str) -> Any:
            removed = tuple(
                game.removed or game.hidden or game.blacklisted
                for game in shared.store.source_games[source_id].values()
            )
            return (
                (count,) if (count := sum(removed)) != len(removed) else False
            )  # Return a tuple because 0 == False and 1 == True

        total_games_no = 0
        restored = False

        selected_id = (
            self.source_rows[selected_row][0]
            if (selected_row := self.sidebar.get_selected_row()) in self.source_rows
            else None
        )

        if selected_row == self.added_row_box.get_parent():
            self.sidebar.select_row(self.added_row_box.get_parent())
            restored = True

        if added_missing := (
            not shared.store.source_games.get("imported")
            or not (removed := get_removed("imported"))
        ):
            self.sidebar.select_row(self.all_games_row_box.get_parent())
        else:
            games_no = len(shared.store.source_games["imported"]) - removed[0]
            self.added_games_no_label.set_label(str(games_no))
            total_games_no += games_no
        self.added_row_box.get_parent().set_visible(not added_missing)

        self.sidebar.get_row_at_index(2).set_visible(False)

        while row := self.sidebar.get_row_at_index(3):
            self.sidebar.remove(row)

        for source_id in shared.store.source_games:
            if source_id == "imported":
                continue
            if not (removed := get_removed(source_id)):
                continue

            row = Gtk.Box(
                margin_top=12,
                margin_bottom=12,
                margin_start=6,
                margin_end=6,
                spacing=12,
            )
            games_no = len(shared.store.source_games[source_id]) - removed[0]
            total_games_no += games_no

            row.append(
                Gtk.Image.new_from_icon_name(
                    "user-desktop-symbolic"
                    if (split_id := source_id.split("_")[0]) == "desktop"
                    else f"{split_id}-source-symbolic"
                )
            )

            row.append(
                Gtk.Label(
                    label=self.get_application().get_source_name(source_id),
                    halign=Gtk.Align.START,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.CHAR,
                )
            )

            row.append(
                games_no_label := Gtk.Label(
                    label=str(games_no),
                    hexpand=True,
                    halign=Gtk.Align.END,
                )
            )

            games_no_label.add_css_class("dim-label")

            # Order rows based on the number of games in them
            index = 3
            while source_row := self.sidebar.get_row_at_index(index):
                if self.source_rows[source_row][1] < games_no:
                    self.sidebar.insert(row, index)
                    break
                index += 1
            if not row.get_parent():
                self.sidebar.append(row)

            self.source_rows[row.get_parent()] = (
                source_id,
                games_no,
            )

            if source_id == selected_id:
                self.sidebar.select_row(row.get_parent())
                restored = True

            self.sidebar.get_row_at_index(2).set_visible(True)

        self.all_games_no_label.set_label(str(total_games_no))

        if not restored:
            self.sidebar.select_row(self.all_games_row_box.get_parent())

    def row_selected(self, _widget: Any, row: Gtk.ListBoxRow | None) -> None:
        if not row:
            return
        match row.get_child():
            case self.all_games_row_box:
                value = "all"
            case self.added_row_box:
                value = "imported"
            case _:
                value = self.source_rows[row][0]

        self.library_page.set_title(self.get_application().get_source_name(value))

        self.filter_state = value
        self.library.invalidate_filter()

        if self.overlay_split_view.get_collapsed():
            self.overlay_split_view.set_show_sidebar(False)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        if platform == "darwin":
            self.sidebar_navigation_page.set_title("")

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
        self.session_blocker_notes_popover.connect(
            "notify::visible", self.on_session_notes_popover_toggled
        )

        add_zerado = Gio.SimpleAction.new("add_zerado", None)
        add_zerado.connect("activate", lambda *_: ZeradosPicker().present(self))
        self.add_action(add_zerado)

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
        self.news_refresh_button.connect("clicked", self.on_news_refresh_clicked)
        self.news_retry_button.connect("clicked", self.on_news_refresh_clicked)

        self.set_library_child()

        self.notice_empty.set_icon_name(shared.APP_ID + "-symbolic")

        self.overlay_split_view.set_show_sidebar(
            shared.state_schema.get_boolean("show-sidebar")
        )

        self.sidebar.select_row(self.all_games_row_box.get_parent())

        if shared.PROFILE == "development":
            self.add_css_class("devel")

        # Connect search entries
        self.search_bar.connect_entry(self.search_entry)
        self.hidden_search_bar.connect_entry(self.hidden_search_entry)

        # Connect signals
        self.search_entry.connect("search-changed", self.search_changed, False)
        self.hidden_search_entry.connect("search-changed", self.search_changed, True)

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

        self.sidebar.connect("row-selected", self.row_selected)

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

    def search_changed(self, _widget: Any, hidden: bool) -> None:
        # Refresh search filter on keystroke in search box
        (self.hidden_library if hidden else self.library).invalidate_filter()

    def set_library_child(self) -> None:
        child, hidden_child, zerados_child = (
            self.notice_empty,
            self.hidden_notice_empty,
            self.zerados_notice_empty,
        )

        for game in shared.store:
            if game.blacklisted or restauracao.e_pendente(game.game_id):
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
            self.set_library_child()
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

        filtered = text != "" and not (
            text in game.name.lower()
            or (text in game.developer.lower() if game.developer else False)
            or (text in game.publisher.lower() if game.publisher else False)
            or (text in game.notes.lower() if game.notes else False)
        )

        if not filtered:
            if self.filter_state == "all":
                pass
            elif game.base_source != self.filter_state:
                filtered = True

        game.filtered = filtered
        self.set_library_child()

        return not filtered

    def set_active_game(self, _widget: Any, _pspec: Any, game: Game) -> None:
        self.active_game = game

    def show_details_page(self, game: Game) -> None:
        self.active_game = game

        self.details_view_cover.set_opacity(int(not game.loading))
        self.details_view_spinner.set_visible(game.loading)

        self.details_view_developer.set_label(game.developer or "")
        self.details_view_developer.set_visible(bool(game.developer))

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
                relative_date(post.timestamp) if post.timestamp else "",
                post.category,
            )
            if part
        ):
            row.set_subtitle(subtitle)
            row.set_subtitle_lines(0)
        for child in children:
            row.add_row(child)
        return row

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
        self.botao_tarefas.reavaliar()
        self.session_blocker_label.set_label(_("{} em execução").format(game.name))
        self.session_blocker.set_visible(True)
        self.navigation_view.set_sensitive(False)
        self.session_blocker_button.grab_focus()
        self.session_tick()
        self.session_blocker_timer.set_visible(True)
        self.session_timer_id = GLib.timeout_add_seconds(1, self.session_tick)

        if shared.schema.get_boolean("session-wallpaper"):
            session_wallpaper.comecar(game)
        session_fita.comecar(game)

    def hide_session_blocker(self) -> None:
        self.session_blocker.set_visible(False)
        self.session_game = None
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
