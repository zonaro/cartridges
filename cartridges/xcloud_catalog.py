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
from pathlib import Path
from time import time
from typing import Any, Callable, Iterable, Optional

from PIL import Image, UnidentifiedImageError


SOURCE_ID = "xcloud"
CATALOG_LIST_ID = "29a81209-df6f-41fd-a528-2ae6b91f719c"
# Fortnite is served by Microsoft's separate free-to-play offering and is not
# consistently present in the public ``allCloud`` SIGL, although its Store
# page explicitly lists Xbox Cloud Gaming support.
FREE_CLOUD_PRODUCT_IDS = ("BT5P2X999VH2",)
SIGL_URL = "https://catalog.gamepass.com/sigls/v2"
PRODUCTS_URL = "https://displaycatalog.mp.microsoft.com/v7.0/products"
PLAY_URL = "https://www.xbox.com/play/launch/{product_id}"
BATCH_SIZE = 50
CATALOG_CACHE_FILE = "xcloud_catalog.json"
CATALOG_TTL = 86400
CACHE_VERSION = 1
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
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
    is_free: bool = False

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
                is_free=_is_free_product(product),
            )
        )
    return games


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_free_product(product: dict[str, Any]) -> bool:
    """True quando há uma oferta pública comprável por preço zero.

    Ofertas de assinatura também chegam com preço zero, mas exigem
    ``RemediationRequired``. Exigi-la como falsa evita classificar todo o Game
    Pass como grátis.
    """
    for sku in product.get("DisplaySkuAvailabilities") or ():
        if not isinstance(sku, dict):
            continue
        if (sku.get("Sku") or {}).get("SkuType") == "trial":
            continue
        for availability in sku.get("Availabilities") or ():
            if not isinstance(availability, dict):
                continue
            actions = availability.get("Actions") or ()
            price = (availability.get("OrderManagementData") or {}).get("Price") or {}
            if (
                "Purchase" in actions
                and not availability.get("RemediationRequired")
                and price.get("ListPrice") == 0
            ):
                return True
    return False


def _batches(values: list[str], size: int = BATCH_SIZE) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def fetch_catalog() -> list[CloudGame]:
    market, language = _locale()
    sigl_query = urllib.parse.urlencode(
        {"id": CATALOG_LIST_ID, "market": market, "language": language}
    )
    product_ids = parse_product_ids(_request_json(f"{SIGL_URL}?{sigl_query}"))
    product_ids = list(dict.fromkeys((*product_ids, *FREE_CLOUD_PRODUCT_IDS)))
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


def catalog_cache_path(base: Optional[Path] = None) -> Path:
    """Caminho do JSON de cache; ``base`` isola o cache nos testes."""
    if base is None:
        from cartridges import shared

        base = shared.cache_dir / "jolven"
    return Path(base) / CATALOG_CACHE_FILE


def _resolve_cache_path(path: Optional[Path]) -> Path:
    return Path(path) if path is not None else catalog_cache_path()


def _game_to_dict(game: CloudGame) -> dict[str, Any]:
    return {
        "product_id": game.product_id,
        "name": game.name,
        "developer": game.developer,
        "publisher": game.publisher,
        "description": game.description,
        "cover_url": game.cover_url,
        "is_free": game.is_free,
    }


def _game_from_dict(data: Any) -> Optional[CloudGame]:
    if not isinstance(data, dict):
        return None
    product_id = data.get("product_id")
    name = data.get("name")
    if not isinstance(product_id, str) or not product_id:
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    return CloudGame(
        product_id=product_id.upper(),
        name=name.strip(),
        developer=_text(data.get("developer")),
        publisher=_text(data.get("publisher")),
        description=_text(data.get("description")),
        cover_url=_text(data.get("cover_url")),
        is_free=bool(data.get("is_free", False)),
    )


def _read_cache(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    try:
        raw = json.loads(
            _resolve_cache_path(path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("version") != CACHE_VERSION:
        return None
    if not isinstance(raw.get("games"), list):
        return None
    return raw


def load_cached_catalog(path: Optional[Path] = None) -> list[CloudGame]:
    """Devolve o catálogo persistido, ignorando cache ausente ou incompatível."""
    raw = _read_cache(path)
    if raw is None:
        return []
    games = [
        game
        for entry in raw["games"]
        if (game := _game_from_dict(entry)) is not None
    ]
    return sorted(games, key=lambda game: game.name.casefold())


def save_cached_catalog(
    games: list[CloudGame],
    path: Optional[Path] = None,
    *,
    now: Optional[float] = None,
) -> bool:
    """Grava o catálogo de forma atômica. Ignora lista vazia para não apagar cache."""
    if not games:
        return False
    cache_path = _resolve_cache_path(path)
    payload = {
        "version": CACHE_VERSION,
        "fetched_at": time() if now is None else now,
        "games": [_game_to_dict(game) for game in games],
    }
    temporary = cache_path.with_name(cache_path.name + ".tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(cache_path)
    except OSError as error:
        logging.warning(
            "Não foi possível gravar o cache do catálogo xCloud: %s", error
        )
        temporary.unlink(missing_ok=True)
        return False
    return True


def cache_is_fresh(
    path: Optional[Path] = None,
    *,
    ttl: float = CATALOG_TTL,
    now: Optional[float] = None,
) -> bool:
    """True quando o cache existe, tem versão suportada e está dentro do TTL."""
    raw = _read_cache(path)
    if raw is None:
        return False
    fetched_at = raw.get("fetched_at")
    if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
        return False
    return ((time() if now is None else now) - fetched_at) < ttl


def get_catalog(
    path: Optional[Path] = None,
    *,
    force: bool = False,
    fetcher: Callable[[], list[CloudGame]] = fetch_catalog,
    now: Optional[float] = None,
) -> list[CloudGame]:
    """Cache-first: usa cache fresco, senão busca e recai no cache ao falhar."""
    if not force and cache_is_fresh(path, now=now):
        cached = load_cached_catalog(path)
        if cached:
            logging.info("Catálogo xCloud do cache local (%d jogos)", len(cached))
            return cached
    try:
        games = fetcher()
    except Exception as error:  # integração opcional: degrada para o cache
        cached = load_cached_catalog(path)
        if cached:
            logging.warning(
                "Catálogo xCloud indisponível, usando cache local: %s", error
            )
            return cached
        raise
    if games:
        save_cached_catalog(games, path, now=now)
    return games


def sync_async(
    on_complete: Optional[Callable[[int], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
    force: bool = False,
) -> bool:
    """Atualiza a fonte em segundo plano. Retorna False se já houver sync.

    ``force=True`` ignora o TTL para o refresh manual; sem cache e sem rede,
    ``on_error`` é chamado na thread principal.
    """
    global _sync_running
    if _sync_running:
        return False
    _sync_running = True

    def work() -> None:
        try:
            games = get_catalog(force=force)
        except Exception as error:  # sem cache e sem rede: nada a atualizar
            logging.warning("Não foi possível atualizar o catálogo xCloud: %s", error)
            if on_error is not None:
                from cartridges.utils.na_tela import entregar_na_tela

                entregar_na_tela(_notify_error, on_error, error)
            _finish_sync()
            return

        from gi.repository import GLib

        GLib.idle_add(_install_catalog, games, on_complete)

    threading.Thread(target=work, daemon=True, name="xcloud-catalog").start()
    return True


def _notify_error(
    on_error: Callable[[Exception], None], error: Exception
) -> bool:
    try:
        on_error(error)
    except Exception:  # pylint: disable=broad-exception-caught
        logging.debug("Callback de erro do catálogo xCloud falhou", exc_info=True)
    return False


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
        existing = shared.store.source_games.get(SOURCE_ID, {}).get(item.game_id)
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
                ("source", f"{SOURCE_ID}_{'free' if item.is_free else 'gamepass'}"),
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

        # Novos registros também precisam nascer na subcategoria correta.
        expected_source = f"{SOURCE_ID}_{'free' if item.is_free else 'gamepass'}"
        if existing.source != expected_source:
            existing.source = expected_source
            changed = True

        if changed:
            # Escrita JSON fora da thread GTK (FileManager é AsyncManager):
            # o json.dump roda no worker do Gio.Task, sem congelar a grade.
            # Relates to #13
            shared.store.managers[FileManager].process_game(
                existing, {}, lambda *_: None
            )
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


def _cover_fit(image: Image.Image, width: int = 200, height: int = 300) -> Image.Image:
    """Redimensiona com ajuste cover: preenche e recorta o centro.

    O ``resize`` direto estica qualquer aspecto diferente de 2:3 (caso das
    BoxArt quadradas do catálogo Microsoft). Aqui a imagem é escalada para
    cobrir o alvo preservando proporção e o excedente é cortado no centro,
    espelhando o ``content-fit: cover`` do tile.
    """
    if image.width <= 0 or image.height <= 0:
        return image.resize((width, height), Image.Resampling.LANCZOS)
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _download_cover(url: str) -> Optional[bytes]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
        with Image.open(BytesIO(raw)) as image:
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            image = _cover_fit(image)
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
    # O pool já é worker: persiste aqui e só encosta no GTK para trocar a capa.
    path = _persist_cover(game_id, contents)
    if path is None:
        return
    from cartridges.utils.na_tela import entregar_na_tela

    entregar_na_tela(_apply_cover, game_id, path)


def _persist_cover(game_id: str, contents: bytes):
    """Grava os bytes da capa de forma atômica; roda fora da thread GTK."""
    from cartridges import shared

    shared.covers_dir.mkdir(parents=True, exist_ok=True)
    path = shared.covers_dir / f"{game_id}.tiff"
    temporary = path.with_suffix(".tiff.tmp")
    try:
        temporary.write_bytes(contents)
        temporary.replace(path)
    except OSError as error:
        logging.debug("Não foi possível salvar capa do xCloud %s: %s", game_id, error)
        temporary.unlink(missing_ok=True)
        return None
    return path


def _apply_cover(game_id: str, path) -> bool:
    """Troca a capa do tile; roda na thread principal via entregar_na_tela."""
    from cartridges import shared

    game = shared.store.source_games.get(SOURCE_ID, {}).get(game_id)
    if game is not None and game.game_cover is not None:
        game.game_cover.new_cover(path)
    return False


def _save_cover(game_id: str, contents: bytes) -> bool:
    path = _persist_cover(game_id, contents)
    if path is None:
        return False
    return _apply_cover(game_id, path)
