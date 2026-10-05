# launcher.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Launcher ou jogo? Entradas como Steam, Heroic ou TwinTail chegam via
Flatpak/Desktop e não são jogos: viram ícones pequenos fixos no topo em
vez de cards na grade.

Só display + flag: `is_launcher` persiste no json do jogo e o usuário pode
marcar/desmarcar qualquer item no menu do card. A detecção automática só
vale para fontes que trazem launchers (flatpak/desktop). Quem o usuário
desmarcar entra em `launcher.json` e nunca mais é remarcado sozinho — nem
na carga do disco, que usa a mesma regra da importação.
"""

import json
import logging

from cartridges import shared

_APLICATIVOS = {
    "steam": (
        "com.valvesoftware.Steam",
        "steam",
        "steam-source-symbolic",
    ),
    "heroic": (
        "com.heroicgameslauncher.hgl",
        "heroic",
        "heroic-source-symbolic",
    ),
    "lutris": ("net.lutris.Lutris", "lutris", "lutris-source-symbolic"),
    "bottles": (
        "com.usebottles.bottles",
        "bottles",
        "bottles-source-symbolic",
    ),
    "twintail": (
        "twintaillauncher",
        "app.twintaillauncher.ttl",
        "twintail-source-symbolic",
    ),
    "itch": ("io.itch.itch", "itch", "itch-source-symbolic"),
    "jolven": (
        "io.github.zonaro.Jolven",
        "io.github.zonaro.Jolven.Devel",
        "jolven",
        "page.redclaw.Cartridges",
        "page.redclaw.Cartridges.Devel",
        "cartridges",
    ),
    "parsec": ("com.parsecgaming.parsec", "parsec"),
    "greenlight": ("io.github.unknownskl.greenlight", "greenlight"),
    "moonlight": ("com.moonlight_stream.Moonlight", "moonlight"),
    "sunshine": ("dev.lizardbyte.app.Sunshine", "sunshine"),
    "retroarch": (
        "org.libretro.RetroArch",
        "retroarch",
        "retroarch-source-symbolic",
    ),
    "retrodeck": ("net.retrodeck.retrodeck", "retrodeck"),
    "emudeck": ("emudeck",),
}

_ALIASES = {
    "heroicgameslauncher": "heroic",
    "heroic games launcher": "heroic",
    "twintaillauncher": "twintail",
    "twintail launcher": "twintail",
    "cartridges": "jolven",
}

_CONHECIDOS = frozenset(
    icon_name.casefold()
    for icon_names in _APLICATIVOS.values()
    for icon_name in icon_names
    if not icon_name.endswith("-symbolic")
) | frozenset(_ALIASES)

_NOMES = frozenset(
    {
        "steam",
        "heroic",
        "heroic games launcher",
        "lutris",
        "bottles",
        "twintaillauncher",
        "twintail launcher",
        "itch",
        "cartridges",
        "parsec",
        "greenlight",
        "moonlight",
        "sunshine",
        "retroarch",
        "retrodeck",
        "emudeck",
    }
)


def detectar(game) -> bool:
    if game.base_source not in ("flatpak", "desktop"):
        return False
    resto = game.game_id[len(game.base_source) + 1 :].casefold()
    if resto in _CONHECIDOS:
        return True
    return (game.name or "").casefold().strip() in _NOMES


def _tipo(game) -> str | None:
    identificador = game.game_id[len(game.base_source) + 1 :].casefold()
    nome = (game.name or "").casefold().strip()
    for tipo, icon_names in _APLICATIVOS.items():
        if identificador in {name.casefold() for name in icon_names}:
            return tipo
    return _ALIASES.get(identificador) or _ALIASES.get(nome) or (
        nome if nome in _APLICATIVOS else None
    )


def nomes_de_icone(game) -> tuple[str, ...]:
    """Ícones do aplicativo em ordem de fidelidade, antes do fallback da UI."""
    identificador = game.game_id[len(game.base_source) + 1 :]
    tipo = _tipo(game)
    nomes = (identificador, *(_APLICATIVOS.get(tipo, ())))
    # Um .desktop e o ID Flatpak frequentemente são o melhor nome de ícone.
    # Remover duplicados mantém a prioridade sem consultas repetidas ao tema.
    return tuple(dict.fromkeys(nome for nome in nomes if nome))


def nomes_de_icone_da_fonte(source_id: str) -> tuple[str, ...]:
    """Ícones para uma plataforma/fonte exibida na navegação lateral."""
    tipo = source_id.split("_")[0]
    if tipo == "desktop":
        return ("desktop-source-symbolic", "user-desktop-symbolic")
    return (f"{tipo}-source-symbolic", *(_APLICATIVOS.get(tipo, ())))


# Tipos que nunca viram ícone no topo. O próprio Jolven chega via
# Flatpak/Desktop como qualquer launcher, mas abrir o app dentro dele
# mesmo não faz sentido: detectado como launcher ele já some da grade,
# e fora do listar() some do topo também.
_OCULTOS = frozenset({"jolven", "cartridges"})


def listar() -> list:
    grupos: dict[str, list] = {}
    for game in shared.store:
        if not (
            game.is_launcher
            and not game.removed
            and not game.hidden
            and not game.blacklisted
        ):
            continue
        tipo = _tipo(game)
        if tipo in _OCULTOS:
            continue
        grupos.setdefault(tipo or game.game_id, []).append(game)
    primarios = (
        max(
            membros,
            key=lambda game: (
                game.last_played or 0,
                game.added or 0,
                game.game_id,
            ),
        )
        for membros in grupos.values()
    )
    return sorted(primarios, key=lambda game: (game.name or "").casefold())


_ARQUIVO = "launcher.json"

_prefs = None


def _carregar() -> dict:
    global _prefs
    if _prefs is None:
        try:
            dados = json.loads(
                (shared.app_dir / _ARQUIVO).read_text(encoding="utf-8")
            )
            _prefs = {"desmarcados": list(dados.get("desmarcados", []))}
        except (OSError, ValueError, AttributeError):
            _prefs = {"desmarcados": []}
    return _prefs


def _salvar() -> None:
    try:
        shared.app_dir.mkdir(parents=True, exist_ok=True)
        (shared.app_dir / _ARQUIVO).write_text(
            json.dumps(_carregar(), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as error:
        logging.debug("Não foi possível salvar launcher.json: %s", error)


def foi_desmarcado(game_id: str) -> bool:
    return game_id in _carregar()["desmarcados"]


def registrar(game, como_launcher: bool) -> None:
    prefs = _carregar()
    if como_launcher:
        if game.game_id in prefs["desmarcados"]:
            prefs["desmarcados"].remove(game.game_id)
    elif game.game_id not in prefs["desmarcados"]:
        prefs["desmarcados"].append(game.game_id)
    _salvar()


def marcar_se_detectado(game) -> bool:
    if detectar(game) and not foi_desmarcado(game.game_id):
        game.is_launcher = True
        return True
    return False
