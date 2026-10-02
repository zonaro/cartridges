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

import math
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
from cartridges.logo_picker import LogoPicker
from cartridges.sgdb_picker import SgdbPicker
from cartridges.store.managers.cover_manager import CoverManager
from cartridges.store.managers.sgdb_manager import SgdbManager
from cartridges.utils import session_fita
from cartridges.utils.create_dialog import create_dialog
from cartridges.utils.game_logo import (
    IMAGE_SUFFIXES,
    logo_choice,
    reset_logo,
    save_manual_logo,
    use_title_instead,
)
from cartridges.utils.save_cover import convert_cover, save_cover
from cartridges.utils.session_wallpaper import (
    Posicoes,
    escolha as wallpaper_choice,
    nao_trocar,
    redefinir as reset_wallpaper,
    salvar_escolha,
)
from cartridges.utils.steam import format_release_date
from cartridges.wallpaper_picker import WallpaperPicker


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
    executable_group: Adw.PreferencesGroup = Gtk.Template.Child()
    updates_group: Adw.PreferencesGroup = Gtk.Template.Child()

    exec_info_label: Gtk.Label = Gtk.Template.Child()
    exec_info_popover: Gtk.Popover = Gtk.Template.Child()
    file_chooser_button: Gtk.Button = Gtk.Template.Child()

    apply_button: Gtk.Button = Gtk.Template.Child()

    cover_changed: bool = False

    is_open: bool = False

    _rating: int = 0
    _logo_choice: Optional[tuple[str, Optional[Path]]] = None
    _wallpaper_choice: Optional[tuple[str, Optional[Path], Posicoes]] = None
    _wallpaper_tmp: Optional[Path] = None
    _logo_tmp: Optional[Path] = None
    _fita_mostrada: Optional[session_fita.Cor] = None
    _fita_redefinir: bool = False
    _fita_previa_usada: bool = False

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
            self.update_logo_row()
            self.update_wallpaper_row()
            self.atualizar_fita()
            if self.game.zerado:
                for widget in (
                    self.wallpaper_row,
                    self.fita_row,
                    self.fita_brilho_row,
                    self.executable_group,
                    self.updates_group,
                ):
                    widget.set_visible(False)
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

        logo_filter = Gtk.FileFilter(name=_("Imagens de logo"))
        for suffix in IMAGE_SUFFIXES:
            logo_filter.add_suffix(suffix[1:])
        logo_filters = Gio.ListStore.new(Gtk.FileFilter)
        logo_filters.append(logo_filter)

        self.logo_file_dialog = Gtk.FileDialog()
        self.logo_file_dialog.set_filters(logo_filters)
        self.logo_file_dialog.set_default_filter(logo_filter)

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
        self.cover_button_browse.connect("clicked", self.browse_covers)
        self.logo_button_browse.connect("clicked", self.browse_logos)
        self.logo_button_file.connect("clicked", self.choose_logo_file)
        self.logo_button_reset.connect("clicked", self.reset_logo_choice)
        self.wallpaper_button_browse.connect("clicked", self.browse_wallpapers)
        self.wallpaper_button_file.connect("clicked", self.choose_wallpaper_file)
        self.wallpaper_button_reset.connect("clicked", self.reset_wallpaper_choice)
        self.fita_button_reset.connect("clicked", self.redefinir_fita)
        self.fita_color_button.connect("notify::rgba", self.previa_da_fita)
        self.fita_color_button.connect(
            "notify::rgba", lambda *_: self.fita_amostra.queue_draw()
        )
        self.fita_amostra.set_draw_func(self.desenhar_amostra)
        self.fita_brilho_row.connect("notify::value", self.previa_da_fita)
        self.connect("closed", lambda *_: self.encerrar_previa())
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
        self._discard_tmp("_logo_tmp")
        self.discard_wallpaper_tmp()
        self.encerrar_previa()

        self.set_is_open(False)

    def _discard_tmp(self, attribute: str) -> None:
        path = getattr(self, attribute, None)
        if path is not None:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            setattr(self, attribute, None)

    def browse_covers(self, *_args: Any) -> None:
        SgdbPicker(self.name.get_text(), self.set_cover_from_path).present(self)

    def set_cover_from_path(self, new_path: Path) -> None:
        if self.tmp_cover_path:
            self.tmp_cover_path.unlink(missing_ok=True)
        self.tmp_cover_path = new_path
        self.game_cover.new_cover(new_path)
        self.cover_button_delete_revealer.set_reveal_child(True)
        self.cover_changed = True

    def browse_logos(self, *_args: Any) -> None:
        LogoPicker(
            self.name.get_text(), self.set_logo_from_picker, self.set_logo_to_title
        ).present(self)

    def choose_logo_file(self, *_args: Any) -> None:
        self.logo_file_dialog.open(self.get_root(), None, self.set_logo_file)

    def set_logo_file(self, _source: Any, result: Gio.Task, *_args: Any) -> None:
        try:
            path = Path(self.logo_file_dialog.open_finish(result).get_path())
        except GLib.Error:
            return
        self.set_logo_from_path(path)

    def set_logo_from_path(self, path: Path) -> None:
        self._discard_tmp("_logo_tmp")
        self._logo_choice = ("manual", path)
        self.update_logo_row()

    def set_logo_from_picker(self, path: Path) -> None:
        self.set_logo_from_path(path)
        self._logo_tmp = path

    def set_logo_to_title(self) -> None:
        self._discard_tmp("_logo_tmp")
        self._logo_choice = ("title", None)
        self.update_logo_row()

    def reset_logo_choice(self, *_args: Any) -> None:
        self._discard_tmp("_logo_tmp")
        self._logo_choice = ("auto", None)
        self.update_logo_row()

    def update_logo_row(self) -> None:
        if self._logo_choice:
            choice = self._logo_choice[0]
        elif self.game:
            choice = logo_choice(self.game)
        else:
            choice = "auto"

        self.logo_row.set_subtitle(
            {
                "manual": _("Escolhido manualmente"),
                "title": _("Sem logo; o título é exibido"),
            }.get(choice, _("Automático (SteamGridDB)"))
        )
        self.logo_button_reset.set_visible(choice != "auto")

    def apply_logo_choice(self, game: Game) -> bool:
        if not self._logo_choice:
            return False

        choice, path = self._logo_choice
        if choice == "manual" and path:
            save_manual_logo(game.game_id, game.name, path)
        elif choice == "title":
            use_title_instead(game.game_id, game.name)
        else:
            reset_logo(game.game_id)

        self._discard_tmp("_logo_tmp")
        self._logo_choice = None
        return True

    def browse_wallpapers(self, *_args: Any) -> None:
        WallpaperPicker(
            self.name.get_text(),
            self.set_wallpaper_from_picker,
            self.set_wallpaper_none,
        ).present(self)

    def choose_wallpaper_file(self, *_args: Any) -> None:
        self.image_file_dialog.open(self.get_root(), None, self.set_wallpaper_file)

    def set_wallpaper_file(self, _source: Any, result: Gio.Task, *_args: Any) -> None:
        try:
            chosen = self.image_file_dialog.open_finish(result).get_path()
        except GLib.Error:
            return
        if chosen:
            self.open_wallpaper_file(Path(chosen))

    def open_wallpaper_file(self, path: Path) -> None:
        WallpaperPicker(
            self.name.get_text(),
            self.set_wallpaper_from_picker,
            self.set_wallpaper_none,
            arquivo=path,
        ).present(self)

    def set_wallpaper_from_picker(self, path: Path, posicoes: Posicoes) -> None:
        self.discard_wallpaper_tmp()
        self._wallpaper_choice = ("manual", path, posicoes)
        self._wallpaper_tmp = path
        self.update_wallpaper_row()

    def discard_wallpaper_tmp(self) -> None:
        self._discard_tmp("_wallpaper_tmp")

    def set_wallpaper_none(self) -> None:
        self.discard_wallpaper_tmp()
        self._wallpaper_choice = ("none", None, Posicoes())
        self.update_wallpaper_row()

    def reset_wallpaper_choice(self, *_args: Any) -> None:
        self.discard_wallpaper_tmp()
        self._wallpaper_choice = ("auto", None, Posicoes())
        self.update_wallpaper_row()

    def update_wallpaper_row(self) -> None:
        if self._wallpaper_choice:
            choice = self._wallpaper_choice[0]
        elif self.game:
            choice = wallpaper_choice(self.game)
        else:
            choice = "auto"

        self.wallpaper_row.set_subtitle(
            {
                "manual": _("Escolhido manualmente"),
                "none": _("Não trocar o papel de parede"),
            }.get(choice, _("Automático (wallhaven)"))
        )
        self.wallpaper_button_reset.set_visible(choice != "auto")

    def apply_wallpaper_choice(self, game: Game) -> bool:
        if not self._wallpaper_choice:
            return False

        choice, path, posicoes = self._wallpaper_choice
        if choice == "manual" and path:
            salvar_escolha(game.game_id, game.name, path, posicoes)
        elif choice == "none":
            nao_trocar(game.game_id, game.name)
        else:
            reset_wallpaper(game.game_id)

        self.discard_wallpaper_tmp()
        self._wallpaper_choice = None
        return True

    def cor_automatica(self) -> session_fita.Cor:
        if self.game is None:
            return session_fita.cor_do_app()
        return session_fita.cor_do_jogo(self.game, self._fita_redefinir)

    def atualizar_fita(self) -> None:
        if self.game is None or not session_fita.fitas():
            self._fita_mostrada = None
            self.fita_row.set_sensitive(False)
            self.fita_row.set_subtitle(_("Nenhum dispositivo configurado"))
            self.fita_brilho_row.set_sensitive(False)
            self.fita_brilho_row.set_subtitle(_("Nenhum dispositivo configurado"))
            self.fita_button_reset.set_visible(False)
            return

        cor = self.cor_automatica()
        self._fita_mostrada = cor
        self.fita_color_button.set_property("rgba", session_fita.cor_para_rgba(cor))
        self.fita_brilho_row.set_value(session_fita.por_cento(cor.brilho))

        manual = not self._fita_redefinir and session_fita.escolhida(
            self.game.game_id
        )
        self.fita_button_reset.set_visible(manual)
        self.fita_row.set_subtitle(
            _("Escolhida manualmente") if manual else _("Extraída da capa")
        )

    def desenhar_amostra(self, _area: Any, contexto: Any, largura: int, altura: int) -> None:
        cor = self.fita_color_button.props.rgba
        contexto.set_source_rgb(cor.red, cor.green, cor.blue)
        raio = min(largura, altura) / 2
        contexto.arc(largura / 2, altura / 2, raio, 0, 2 * math.pi)
        contexto.fill()

    def previa_da_fita(self, *_args: Any) -> None:
        if self._fita_mostrada is None:
            return
        self._fita_previa_usada = True
        session_fita.previa(
            session_fita.rgba_para_cor(
                self.fita_color_button.props.rgba,
                session_fita.de_por_cento(self.fita_brilho_row.get_value()),
            )
        )

    def encerrar_previa(self) -> None:
        if self._fita_previa_usada:
            self._fita_previa_usada = False
            session_fita.previa(session_fita.cor_do_app())

    def redefinir_fita(self, *_args: Any) -> None:
        if not self.game:
            return
        self._fita_redefinir = True
        self.atualizar_fita()

    def aplicar_fita(self, game: Game) -> None:
        if self._fita_mostrada is None and not self._fita_redefinir:
            return

        na_tela = session_fita.rgba_para_cor(
            self.fita_color_button.props.rgba,
            session_fita.de_por_cento(self.fita_brilho_row.get_value()),
        )
        mostrada = self._fita_mostrada
        mesma = (
            mostrada is not None
            and (na_tela.matiz, na_tela.saturacao, na_tela.brilho)
            == (mostrada.matiz, mostrada.saturacao, mostrada.brilho)
        )
        escolha_nova = mostrada is not None and not mesma

        if escolha_nova:
            session_fita.salvar_cor(game.game_id, game.name, na_tela)
        elif self._fita_redefinir:
            session_fita.redefinir(game.game_id)
        elif session_fita.escolhida(game.game_id):
            session_fita.salvar_cor(game.game_id, game.name, na_tela)

        self._fita_redefinir = False

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

        self.apply_logo_choice(self.game)
        self.apply_wallpaper_choice(self.game)
        self.aplicar_fita(self.game)

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
