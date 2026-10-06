# SPDX-License-Identifier: GPL-3.0-or-later
"""Catálogo público do Xbox Cloud Gaming para a biblioteca do Jolven.

O Greenlight separa a descoberta em duas etapas: obtém os product IDs de uma
lista SIGL do Game Pass e hidrata esses IDs no catálogo da Microsoft. Aqui
usamos o mesmo desenho, deixando autenticação e streaming com o webview já
existente. A assinatura é validada pelo próprio xbox.com ao abrir o jogo.
"""

from __future__ import annotations

import json
import locale
import logging
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from time import time
from typing import Any, Callable, Iterable, Optional

from PIL import Image, UnidentifiedImageError


SOURCE_ID = "xcloud"
CATALOG_LIST_ID = "29a81209-df6f-41fd-a528-2ae6b91f719c"
SIGL_URL = "https://catalog.gamepass.com/sigls/v2"
PRODUCTS_URL = "https://displaycatalog.mp.microsoft.com/v7.0/products"
PLAY_URL = "https://www.xbox.com/play/launch/{product_id}"
BATCH_SIZE = 50
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_cover_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="xcloud-cover")
_sync_running = False


@dataclass(frozen=True)
class CloudGame:
    product_id: str
    name: str
    developer: Optional[str] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = None

    @property
    def game_id(self) -> str:
        return f"{SOURCE_ID}_{self.product_id.lower()}"

    @property
    def play_url(self) -> str:
        return PLAY_URL.format(product_id=self.product_id)


def _locale() -> tuple[str, str]:
    """Retorna ``(market, language)`` aceitos pelo catálogo."""
    current = locale.getlocale()[0] or "en_US"
    language = current.replace("_", "-")
    parts = current.split("_", 1)
    market = parts[1].upper() if len(parts) == 2 else "US"
    return market, language


def _request_json(url: str, *, timeout: int = 30) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def parse_product_ids(data: Any) -> list[str]:
    """Extrai IDs preservando ordem; o primeiro item do SIGL é um cabeçalho."""
    if not isinstance(data, list):
        return []
    return list(
        dict.fromkeys(
            row["id"].upper()
            for row in data
            if isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and row["id"]
        )
    )


def _image_url(localized: dict[str, Any]) -> Optional[str]:
    images = localized.get("Images")
    if not isinstance(images, list):
        return None
    for purpose in ("BoxArt", "Poster"):
        for image in images:
            if not isinstance(image, dict) or image.get("ImagePurpose") != purpose:
                continue
            uri = image.get("Uri")
            if isinstance(uri, str) and uri:
                url = "https:" + uri if uri.startswith("//") else uri
                # O CDN da Microsoft redimensiona com ``w``. Isso reduz uma
                # sincronização inicial de centenas de MB para poucas dezenas.
                return f"{url}{'&' if '?' in url else '?'}w=400"
    return None


def parse_products(data: Any) -> list[CloudGame]:
    if not isinstance(data, dict) or not isinstance(data.get("Products"), list):
        return []
    games: list[CloudGame] = []
    for product in data["Products"]:
        if not isinstance(product, dict):
            continue
        product_id = product.get("ProductId")
        localized_items = product.get("LocalizedProperties")
        if (
            not isinstance(product_id, str)
            or not product_id
            or not isinstance(localized_items, list)
            or not localized_items
            or not isinstance(localized_items[0], dict)
        ):
            continue
        localized = localized_items[0]
        name = localized.get("ProductTitle")
        if not isinstance(name, str) or not name.strip():
            continue
        games.append(
            CloudGame(
                product_id=product_id.upper(),
                name=name.strip(),
                developer=_text(localized.get("DeveloperName")),
                publisher=_text(localized.get("PublisherName")),
                description=_text(localized.get("ShortDescription")),
                cover_url=_image_url(localized),
            )
        )
    return games


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _batches(values: list[str], size: int = BATCH_SIZE) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def fetch_catalog() -> list[CloudGame]:
    market, language = _locale()
    sigl_query = urllib.parse.urlencode(
        {"id": CATALOG_LIST_ID, "market": market, "language": language}
    )
    product_ids = parse_product_ids(_request_json(f"{SIGL_URL}?{sigl_query}"))
    if not product_ids:
        raise ValueError("o catálogo público do xCloud retornou vazio")

    def fetch_batch(batch: list[str]) -> list[CloudGame]:
        query = urllib.parse.urlencode(
            {"bigIds": ",".join(batch), "market": market, "languages": language}
        )
        return parse_products(_request_json(f"{PRODUCTS_URL}?{query}"))

    games: list[CloudGame] = []
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="xcloud-catalog") as pool:
        for result in pool.map(fetch_batch, _batches(product_ids)):
            games.extend(result)
    unique = {game.product_id: game for game in games}
    return sorted(unique.values(), key=lambda game: game.name.casefold())


def sync_async(on_complete: Optional[Callable[[int], None]] = None) -> bool:
    """Atualiza a fonte em segundo plano. Retorna False se já houver sync."""
    global _sync_running
    if _sync_running:
        return False
    _sync_running = True

    def work() -> None:
        try:
            games = fetch_catalog()
        except Exception as error:  # rede/catálogo indisponível: mantém o cache
            logging.warning("Não foi possível atualizar o catálogo xCloud: %s", error)
            _finish_sync()
            return

        from gi.repository import GLib

        GLib.idle_add(_install_catalog, games, on_complete)

    threading.Thread(target=work, daemon=True, name="xcloud-catalog").start()
    return True


def _finish_sync() -> bool:
    global _sync_running
    _sync_running = False
    return False


def _install_catalog(
    games: list[CloudGame], on_complete: Optional[Callable[[int], None]]
) -> bool:
    """Instala modelos GTK em lotes pequenos e agenda capas ausentes."""
    from cartridges import shared
    from cartridges.game import Game
    from cartridges.store.managers.display_manager import DisplayManager
    from cartridges.store.managers.file_manager import FileManager
    from gi.repository import GLib

    iterator = iter(games)
    added = 0

    def install_one(item: CloudGame) -> None:
        nonlocal added
        existing = shared.store.get(item.game_id)
        changed = False
        if existing is None:
            existing = Game(
                {
                    "added": int(time()),
                    "name": item.name,
                    "source": SOURCE_ID,
                    "game_id": item.game_id,
                    "executable": item.play_url,
                    "developer": item.developer,
                    "publisher": item.publisher,
                    "description": item.description,
                    "tgdb_platform": "Xbox Cloud Gaming",
                }
            )
            shared.store.add_game(existing, {}, run_pipeline=False)
            added += 1
            changed = True
        else:
            for attr, value in (
                ("name", item.name),
                ("executable", item.play_url),
                ("developer", item.developer),
                ("publisher", item.publisher),
                ("description", item.description),
                ("tgdb_platform", "Xbox Cloud Gaming"),
            ):
                if value is not None and getattr(existing, attr, None) != value:
                    setattr(existing, attr, value)
                    changed = True

        if changed:
            shared.store.managers[FileManager].main(existing, {})
        # Mesmo quando nada mudou, um item carregado com a integração
        # desativada ainda precisa entrar na grade depois que ela é ligada.
        if changed or existing.get_parent() is None:
            shared.store.managers[DisplayManager].main(existing, {})

        if item.cover_url and existing.get_cover_path() is None:
            future = _cover_pool.submit(_download_cover, item.cover_url)
            future.add_done_callback(
                lambda result, game_id=item.game_id: _cover_downloaded(game_id, result)
            )

    def step() -> bool:
        previous_state = shared.win.get_application().state
        shared.win.get_application().state = shared.AppState.LOAD_FROM_DISK
        exhausted = False
        try:
            for _index in range(12):
                try:
                    install_one(next(iterator))
                except StopIteration:
                    exhausted = True
                    break
        finally:
            shared.win.get_application().state = previous_state

        if not exhausted:
            return GLib.SOURCE_CONTINUE

        logging.info(
            "Catálogo xCloud atualizado: %d jogos (%d novos)", len(games), added
        )
        shared.win.create_source_rows()
        if on_complete is not None:
            on_complete(len(games))
        _finish_sync()
        return GLib.SOURCE_REMOVE

    GLib.idle_add(step)
    return GLib.SOURCE_REMOVE


def _download_cover(url: str) -> Optional[bytes]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
        with Image.open(BytesIO(raw)) as image:
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            image = image.resize((200, 300), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="TIFF", compression="tiff_adobe_deflate")
            return output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError) as error:
        logging.debug("Falha ao baixar capa do xCloud (%s): %s", url, error)
        return None


def _cover_downloaded(game_id: str, future) -> None:
    try:
        contents = future.result()
    except Exception as error:  # pylint: disable=broad-exception-caught
        logging.debug("Falha ao preparar capa %s: %s", game_id, error)
        return
    if not contents:
        return
    from gi.repository import GLib

    GLib.idle_add(_save_cover, game_id, contents)


def _save_cover(game_id: str, contents: bytes) -> bool:
    from cartridges import shared

    shared.covers_dir.mkdir(parents=True, exist_ok=True)
    path = shared.covers_dir / f"{game_id}.tiff"
    temporary = path.with_suffix(".tiff.tmp")
    try:
        temporary.write_bytes(contents)
        temporary.replace(path)
        game = shared.store.get(game_id)
        if game is not None and game.game_cover is not None:
            game.game_cover.new_cover(path)
    except OSError as error:
        logging.debug("Não foi possível salvar capa do xCloud %s: %s", game_id, error)
        temporary.unlink(missing_ok=True)
    return False
