# game_logo.py
#
# Copyright 2026 joaomgabaldi
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

"""The official horizontal logo shown as the details page header.

The logo is branding, not artwork: it replaces the game's title where the
title used to be, so everything here is built around staying inside the space
a title occupied. Nothing is ever invented locally — a game either has a real
logo on SteamGridDB or it keeps its text title.

Every lookup is remembered on disk, hits and misses alike. A hit is kept for
good: a logo on screen stays on screen, and only renaming the game (or picking
another one by hand) replaces it. A miss is retried after a week, because art
added to SteamGridDB later should still reach the game, while asking on every
visit would be a request per page view for an answer that rarely changes.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import uuid4

import requests
from gi.repository import Gdk, GdkPixbuf, GLib

from cartridges import shared
from cartridges.game import Game
from cartridges.game_cover import texture_from_pixbuf
from cartridges.utils.download import download_bytes
from cartridges.utils.ler_json import ler_json
from cartridges.utils.steamgriddb import SgdbAuthError, SgdbError, SgdbHelper

# On-screen bounds for the header. The height is the primary control (a logo
# should read as a heading, and heights vary far less than widths across
# games); the width is the safety net that keeps a very wide, short wordmark
# from stretching across the whole column. 360px sits just past the
# HowLongToBeat card below it, so the block reads as the widest element of the
# stack without dominating it.
LOGO_MAX_HEIGHT = 72
LOGO_MAX_WIDTH = 360
# Small assets are scaled up only far enough to stay legible, never to fill the
# box: a blown-up 200px logo looks worse than a modest, crisp one.
LOGO_MIN_HEIGHT = 44

# A logo has to actually be horizontal to work as a header. Square-ish assets
# (icons, badges) blow the reserved height budget for the width they buy, and
# they are what the "logo" category on SteamGridDB fills up with for games
# that have no real wordmark.
MIN_ASPECT_RATIO = 1.3

# Teto de DIMENSÃO, além do teto de bytes do download: o loader de PNG
# decodifica a imagem inteira antes de escalar, na main thread, a cada visita
# à página de detalhes — um upload comunitário de 15000x8000 px cabe em 25 MiB
# e custaria centenas de MB de pico e um engasgo visível, repetidos. E o
# ranking desempata PARA o maior, então sem isto o candidato desmedido ganha.
# 4096 é folga larga para um wordmark (os do SGDB raramente passam de 1280).
MAX_SOURCE_DIMENSION = 4096

# The page is drawn on a dark, blurred cover. A black logo would be invisible
# on it, so it is ranked below everything else and only used when it is all
# there is; official art and white wordmarks both sit well on dark.
STYLE_RANK = {"official": 3, "white": 2, "custom": 1, "black": -2}

# Formats that can carry transparency. A JPEG logo comes with a baked-in
# rectangle of background, which is exactly the "artificial frame" the header
# must not have, so it loses to any alternative. Sem leitor WebP no runtime
# (o GdkPixbuf do MSYS2 não tem o loader), `.webp` não entra aqui — `_usable`
# já descarta esses candidatos antes de chegar a este critério.
TRANSPARENT_SUFFIXES = (".png",)

IMAGE_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")

# How long a "this game has no logo" answer is trusted. Long enough that
# browsing the library is not a stream of requests, short enough that art
# added to SteamGridDB shows up without the user clearing anything.
MISS_TTL_SECONDS = 7 * 24 * 60 * 60


def logo_display_size(
    width: int,
    height: int,
    max_width: int = LOGO_MAX_WIDTH,
    max_height: int = LOGO_MAX_HEIGHT,
) -> tuple[int, int]:
    """Clamp an intrinsic logo size to the header box, keeping its ratio.

    Returned as an exact pair rather than as a max-height alone: the picture is
    given this size verbatim, so the aspect ratio is preserved by construction
    and the widget can never be handed a box of a different shape to letterbox
    the logo into. The box defaults to the details page's header.
    """
    if width <= 0 or height <= 0:
        return (0, 0)

    scale = min(max_height / height, max_width / width)
    if scale > 1:
        # Upscaling is a last resort for tiny assets, and it stops as soon as
        # the logo is tall enough to read as a heading.
        scale = min(scale, max(1.0, LOGO_MIN_HEIGHT / height))

    return (max(1, round(width * scale)), max(1, round(height * scale)))


def _intrinsic_size(path: Path) -> Optional[tuple[int, int]]:
    """The stored logo's real pixel size, read without decoding it if possible."""
    try:
        image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(str(path))
    except (GLib.Error, TypeError, ValueError):
        image_format = None
        width = height = 0

    if image_format is not None and width and height:
        return (width, height)

    # A format GdkPixbuf has no loader for (a .webp on some installs) can still
    # go through the texture loader, which reports the same dimensions.
    try:
        texture = Gdk.Texture.new_from_filename(str(path))
    except GLib.Error:
        return None
    return (texture.get_intrinsic_width(), texture.get_intrinsic_height())


def load_logo(
    path: Path, max_width: int = LOGO_MAX_WIDTH, max_height: int = LOGO_MAX_HEIGHT
) -> Optional[tuple[Gdk.Texture, int]]:
    """Load a cached logo ready to be drawn as the header.

    Returns the texture and the width, in logical pixels, it should be given —
    its height is deliberately not returned, because the widget derives that
    from the width and the image's own proportions, which is what guarantees
    the logo is never stretched.

    The texture is decoded at the display's pixel density rather than at the
    source resolution: a logo is a wordmark whose fine strokes have to stay
    crisp, but a 3000px asset scaled down at draw time costs megabytes for
    a header that is 72 pixels tall.
    """
    size = _intrinsic_size(path)
    if not size:
        return None
    # O mesmo teto do fetch, para o arquivo local escolhido à mão: as
    # dimensões vêm do get_file_info, sem decodificar — recusar aqui custa
    # nada e evita a decodificação gigante na main thread. O cabeçalho volta
    # ao título em texto.
    if max(size) > MAX_SOURCE_DIMENSION:
        logging.debug("Logo %s excede %d px, ignorado", path, MAX_SOURCE_DIMENSION)
        return None

    width, height = logo_display_size(*size, max_width, max_height)
    if not (width and height):
        return None

    scale = max(1, int(shared.scale_factor))
    try:
        texture = texture_from_pixbuf(
            GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(path), width * scale, height * scale, True
            )
        )
    except GLib.Error:
        try:
            texture = Gdk.Texture.new_from_filename(str(path))
        except GLib.Error as error:
            logging.debug("Could not load logo %s: %s", path, error)
            return None

    return (texture, width)


def _sidecar_path(game_id: str) -> Path:
    return shared.logos_dir / f"{game_id}.json"


def _read_sidecar(game_id: str) -> Optional[dict[str, Any]]:
    try:
        data = ler_json(_sidecar_path(game_id))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        logging.warning("Unreadable logo sidecar %s: %s", _sidecar_path(game_id).name, error)
        return None
    return data if isinstance(data, dict) else None


def _write_sidecar(
    game_id: str, name: str, filename: Optional[str], locked: bool = False
) -> None:
    data = {
        "name": name,
        "file": filename,
        "timestamp": int(time.time()),
        # A locked entry was decided by the user, not by the ranking. It is
        # never re-fetched and never re-matched against the title, so renaming
        # a game (or a better-scoring upload appearing) cannot undo the choice.
        "locked": locked,
    }
    # Temporário + replace: um sidecar truncado no meio da escrita lê como
    # "nunca buscado", e a busca automática seguinte apagava o logo que o
    # usuário tinha escolhido. Nome único porque buscas de jogos diferentes
    # (e a do seletor) gravam em paralelo. A pasta entra no `try` porque falha
    # pelos mesmos motivos da escrita.
    path = _sidecar_path(game_id)
    tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        shared.logos_dir.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(data), encoding="utf-8")
        tmp_path.replace(path)
    except OSError as error:
        tmp_path.unlink(missing_ok=True)
        logging.debug("Could not record logo lookup for %s: %s", game_id, error)


def _cached_file(sidecar: Optional[dict[str, Any]]) -> Optional[Path]:
    """The image a recorded lookup points at, if it is still on disk."""
    if not sidecar:
        return None

    filename = sidecar.get("file")
    if not filename:
        return None

    path = shared.logos_dir / str(filename)
    return path if path.is_file() else None


def cached_logo_path(game: Game) -> Optional[Path]:
    """The logo on disk for ``game``, if one applies to it right now.

    An automatically fetched logo cached under a different name does not
    count. Titles get corrected by hand all the time in this app — that is
    half of what the edit dialog is for — and a header still showing the
    previous game's branding is a worse outcome than the plain title, so the
    stale file is ignored (and replaced by the lookup that follows).

    A logo the user picked is exempt from that: they matched it to this game
    themselves, which is worth more than any title comparison.

    A idade nunca desqualifica um logo em disco: o logo que está na tela
    fica, e só renomear o jogo (ou escolher outro à mão) o troca. Só o "sem
    logo" envelhece — ver `_miss_expired`.
    """
    sidecar = _read_sidecar(game.game_id)
    if not sidecar:
        return None
    # Uma tumba nunca busca de novo (`logo_lookup_needed`), então nem o nome
    # nem a validade podem descartar o logo de um zerado: sem outro para pôr
    # no lugar, descartar seria só apagar. E um zerado já passou pela
    # biblioteca com o título acertado.
    if sidecar.get("locked") or getattr(game, "removed", False):
        return _cached_file(sidecar)
    if sidecar.get("name") != game.name:
        return None
    return _cached_file(sidecar)


def _miss_expired(sidecar: dict[str, Any]) -> bool:
    """Whether an automatic "no logo" answer is old enough to ask again.

    Um logo em disco nunca vence. Um acerto cujo arquivo sumiu conta como
    "sem logo" — o usuário esvaziou o cache, e esperar semanas para notar
    seria a resposta errada.
    """
    if _cached_file(sidecar):
        return False
    try:
        age = time.time() - float(sidecar.get("timestamp", 0))
    except (TypeError, ValueError):
        return True
    return age > MISS_TTL_SECONDS


def logo_choice(game: Game) -> str:
    """How ``game``'s header is currently decided: automatic, manual or title.

    Used to tell the user what state they are in before they change it.
    """
    sidecar = _read_sidecar(game.game_id)
    if not sidecar or not sidecar.get("locked"):
        return "auto"
    return "manual" if _cached_file(sidecar) else "title"


def save_manual_logo(game_id: str, name: str, source: Path) -> Optional[Path]:
    """Adopt ``source`` as ``game_id``'s logo, chosen by hand. Locks it."""
    suffix = source.suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        suffix = ".png"

    filename = f"{game_id}{suffix}"
    destination = shared.logos_dir / filename
    try:
        shared.logos_dir.mkdir(parents=True, exist_ok=True)
        remove_logo(game_id, keep=filename)
        destination.write_bytes(source.read_bytes())
    except OSError as error:
        logging.warning("Could not save the chosen logo for %s: %s", game_id, error)
        return None

    _write_sidecar(game_id, name, filename, locked=True)
    return destination


def use_title_instead(game_id: str, name: str) -> None:
    """Record that ``game_id`` should show its title, not a logo.

    Stored as a locked lookup with no image rather than as a deletion, so the
    automatic fetch does not simply put a logo back the next time the page is
    opened. This is the escape hatch for games whose only available artwork is
    unreadable.
    """
    remove_logo(game_id)
    _write_sidecar(game_id, name, None, locked=True)


def reset_logo(game_id: str) -> None:
    """Forget every decision about ``game_id``'s logo, manual ones included.

    The next time the page is opened the ordinary lookup runs again, exactly
    as it would for a game seen for the first time.
    """
    remove_logo(game_id)


def logo_lookup_needed(game: Game) -> bool:
    """Whether ``game`` is worth asking SteamGridDB about right now.

    Answers no for a game we already have a logo for, for one whose miss is
    still fresh, and for a library with no API key configured — in that last
    case there is nothing to ask with, and the text title is the whole feature.

    A renamed game always counts as needing a new lookup: the cached asset was
    matched against the old title, and a wrong logo is worse than none.
    """
    if not shared.schema.get_string("sgdb-key"):
        return False

    if game.blacklisted or game.removed:
        return False

    sidecar = _read_sidecar(game.game_id)
    if not sidecar:
        return True

    # The user has already answered this question for this game.
    if sidecar.get("locked"):
        return False

    if sidecar.get("name") != game.name:
        return True

    # Só o "sem logo" é perguntado de novo; um logo em disco fica para sempre.
    return _miss_expired(sidecar)


def _candidate_key(logo: dict[str, Any]) -> tuple:
    """Rank one candidate; larger sorts better.

    Style first (it decides whether the logo is even visible on this page),
    then transparency, then SteamGridDB's own community score, and only then
    size — resolution is a tie-breaker, never a reason to pick a logo that
    reads worse.
    """
    style = str(logo.get("style", "")).lower()
    suffix = Path(urlparse(str(logo.get("url", ""))).path).suffix.lower()

    try:
        score = float(logo.get("score", 0) or 0)
    except (TypeError, ValueError):
        score = 0.0

    return (
        STYLE_RANK.get(style, 0),
        suffix in TRANSPARENT_SUFFIXES,
        score,
        int(logo.get("width", 0) or 0),
    )


def _usable(logo: dict[str, Any]) -> bool:
    """Whether a candidate is a horizontal image we could actually draw."""
    url = str(logo.get("url", "") or "")
    if not url:
        return False

    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        return False

    # Sem leitor WebP no runtime (o GdkPixbuf do MSYS2 não tem o loader): um
    # candidato .webp seria baixado, descartado e registrado como "sem logo",
    # e o jogo nunca ganharia logo.
    if suffix == ".webp":
        return False

    try:
        width = int(logo.get("width", 0) or 0)
        height = int(logo.get("height", 0) or 0)
    except (TypeError, ValueError):
        return False

    # A missing size is not a reason to drop a candidate — the API omits it for
    # some entries — but a known square one is.
    if width and height and width / height < MIN_ASPECT_RATIO:
        return False

    # Um candidato desmedido é pulado aqui para que o pick escolha o PRÓXIMO,
    # em vez de baixar algo que a validação descartaria depois.
    if width > MAX_SOURCE_DIMENSION or height > MAX_SOURCE_DIMENSION:
        return False

    return True


def pick_logo(logos: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The best logo for a dark, minimal header, or None if none fit."""
    usable = [logo for logo in logos if _usable(logo)]
    if not usable:
        return None
    return max(usable, key=_candidate_key)


def _keep_existing_logo(game: Game) -> Optional[Path]:
    """Renew the logo ``game`` already has, for a lookup that found nothing.

    A lookup that could not be completed says nothing about whether the logo
    already on disk is right. Without this, an unreachable SteamGridDB (or one
    that has stopped listing the game) would replace a perfectly good logo
    with a miss, and the details page would fall back to the text title for a
    game whose artwork is sitting on disk.
    """
    sidecar = _read_sidecar(game.game_id)
    # A renamed game is the one case where the old logo is not worth keeping:
    # it was matched against the previous title.
    if not sidecar or sidecar.get("name") != game.name:
        return None

    path = _cached_file(sidecar)
    if path is None:
        return None

    _write_sidecar(
        game.game_id, game.name, path.name, locked=bool(sidecar.get("locked"))
    )
    return path


def fetch_logo(game: Game) -> Optional[Path]:
    """Look ``game``'s logo up on SteamGridDB and cache it. Blocking.

    Returns the path to the cached image, or None when the game has no usable
    logo — which is an ordinary outcome, not a failure: the caller simply keeps
    the text title. Errors are logged and swallowed for the same reason; a
    decorative asset is never worth interrupting the page for.

    Meant to be called off the main thread.
    """
    sgdb = SgdbHelper()

    try:
        sgdb_id = sgdb.get_game_id(game)
        logos = sgdb.get_logos(sgdb_id)
    except SgdbAuthError:
        # No miss is recorded: the key is wrong, not the game. Doing so would
        # hide every logo in the library for a week after the user fixes their
        # key. A logo already fetched is kept, for the same reason.
        logging.info("Could not authenticate to SteamGridDB while fetching a logo")
        return _keep_existing_logo(game)
    except (SgdbError, requests.RequestException) as error:
        logging.debug("No logo for %s: %s", game.name, error)
        if kept := _keep_existing_logo(game):
            return kept
        # A game SteamGridDB does not know is a real answer, worth caching;
        # a network hiccup is not, and is retried on the next visit.
        if isinstance(error, SgdbError):
            _write_sidecar(game.game_id, game.name, None)
        return None

    logo = pick_logo(logos)
    if not logo:
        if kept := _keep_existing_logo(game):
            return kept
        _write_sidecar(game.game_id, game.name, None)
        return None

    url = str(logo["url"])
    try:
        content = download_bytes(url, timeout=15)
    except requests.RequestException as error:
        logging.debug("Could not download logo for %s: %s", game.name, error)
        return _keep_existing_logo(game)

    # The user may have answered this question by hand while the request was
    # out — the lookup starts when the details page opens, and picking a logo
    # in the edit dialog takes less time than the round trip. Their choice is
    # locked, and writing over it here would delete the file they picked
    # (`remove_logo` below) and downgrade the sidecar back to unlocked, so the
    # next visit would quietly re-fetch and replace it again.
    if (existing := _read_sidecar(game.game_id)) and existing.get("locked"):
        logging.debug("Keeping the chosen logo for %s", game.name)
        return _cached_file(existing)

    suffix = Path(urlparse(url).path).suffix.lower() or ".png"
    filename = f"{game.game_id}{suffix}"
    path = shared.logos_dir / filename
    try:
        shared.logos_dir.mkdir(parents=True, exist_ok=True)
        # Drop any logo kept in another format, so a game switching from .webp
        # to .png does not leave the old file behind forever.
        remove_logo(game.game_id, keep=filename)
        path.write_bytes(content)
    except OSError as error:
        logging.debug("Could not save logo for %s: %s", game.name, error)
        return None

    # Nothing checked that the bytes were an image. A server returning an error
    # page (or a truncated download) was recorded as a *hit*, and a hit is
    # trusted for good — `logo_lookup_needed` sees a file on disk and does
    # not ask again — so one bad response cost that game its header for good.
    fetched_size = _intrinsic_size(path)
    # Desmedido conta como inutilizável: o metadado da API pode mentir sobre as
    # dimensões, e um arquivo em cache é confiado para sempre — o load recusaria
    # a cada visita, mas descartar aqui registra "sem logo" no sidecar e não
    # deixa o arquivo gigante parado no disco.
    if fetched_size is None or max(fetched_size) > MAX_SOURCE_DIMENSION:
        logging.debug("Discarding an unusable logo for %s", game.name)
        path.unlink(missing_ok=True)
        _write_sidecar(game.game_id, game.name, None)
        return None

    _write_sidecar(game.game_id, game.name, filename)
    return path


def remove_logo(game_id: str, keep: Optional[str] = None) -> None:
    """Delete ``game_id``'s cached logo files (except ``keep``, if given)."""
    for suffix in IMAGE_SUFFIXES:
        path = shared.logos_dir / f"{game_id}{suffix}"
        if path.name != keep:
            path.unlink(missing_ok=True)

    if keep is None:
        _sidecar_path(game_id).unlink(missing_ok=True)
