# agrupamento.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Um jogo, vários launchers: agrupa num card só as cópias do mesmo jogo
vindas de fontes diferentes (Steam, Heroic, Flatpak, TwinTail...).

Só display, sem migração: os jogos continuam separados na store (cada um
com seu executável e seu arquivo); o agrupamento é calculado pelo nome e
só decide qual card aparece na grade. A chave usa `title_match` (portão
numérico incluso): "The Outer Worlds" nunca cai no grupo de
"The Outer Worlds 2", e "Jogo: Definitive Edition" cai no do "Jogo".

Desfeitas vão para `agrupamento.json` na pasta do app: a chave desagrupada
nunca mais junta (nem para cópias futuras com o mesmo nome), e o preferido
vira o padrão do botão jogar.
"""

import json
import logging
from typing import Optional

from cartridges import shared
from cartridges.utils.title_match import (
    _EDITION_WORDS,
    core_words,
    numeric_signature,
    tokenize,
)

_ARQUIVO = "agrupamento.json"

_prefs: Optional[dict] = None
_curando = False
_indice: Optional[dict[str, list]] = None
_indice_chave: Optional[tuple] = None


def _carregar() -> dict:
    global _prefs
    if _prefs is None:
        try:
            dados = json.loads(
                (shared.app_dir / _ARQUIVO).read_text(encoding="utf-8")
            )
            _prefs = {
                "preferidos": dict(dados.get("preferidos", {})),
                "desagrupados": list(dados.get("desagrupados", [])),
            }
        except (OSError, ValueError, AttributeError):
            _prefs = {"preferidos": {}, "desagrupados": []}
    return _prefs


def _salvar() -> None:
    try:
        shared.app_dir.mkdir(parents=True, exist_ok=True)
        (shared.app_dir / _ARQUIVO).write_text(
            json.dumps(_carregar(), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as error:
        logging.debug("Não foi possível salvar o agrupamento: %s", error)


def ativo() -> bool:
    try:
        return shared.schema.get_boolean("agrupar-duplicados")
    except Exception:  # pylint: disable=broad-exception-caught
        return True


def chave_para(nome: str) -> str:
    tokens = tokenize(nome or "")
    numeros = numeric_signature(tokens)
    nucleo = sorted({w for w in core_words(tokens) if w not in _EDITION_WORDS})
    chave = "|".join(numeros) + "||" + "+".join(nucleo)
    if not nucleo:
        chave += "||" + (nome or "").casefold().strip()
    return chave


def elegivel(game) -> bool:
    return bool(
        ativo()
        and not game.removed
        and not game.blacklisted
        and not game.hidden
        and not game.is_launcher
    )


def atualizar(game) -> str:
    nome = game.name or ""
    if getattr(game, "_grupo_nome", None) != nome:
        game._grupo_chave = chave_para(nome)
        game._grupo_nome = nome
        limpar_indice()
    return game._grupo_chave


def limpar_indice() -> None:
    global _indice, _indice_chave
    _indice = None
    _indice_chave = None


def construir_indice(jogos, *, agrupar: bool = True) -> dict[str, list]:
    grupos: dict[str, list] = {}
    for outro in jogos:
        if not agrupar or not elegivel(outro):
            continue
        chave = atualizar(outro)
        if desagrupada(chave):
            continue
        grupos.setdefault(chave, []).append(outro)
    return grupos


def indice() -> dict[str, list]:
    global _indice, _indice_chave
    agrupar = ativo()
    try:
        revisao = shared.store.revision
    except AttributeError:
        revisao = 0
    chave = (revisao, agrupar)
    if _indice is None or _indice_chave != chave:
        try:
            jogos = list(shared.store)
        except AttributeError:
            return {}
        _indice = construir_indice(jogos, agrupar=agrupar)
        _indice_chave = chave
    return _indice


def desagrupada(chave: str) -> bool:
    return chave in _carregar()["desagrupados"]


def membros(game) -> list:
    chave = atualizar(game)
    if desagrupada(chave) or not elegivel(game):
        return [game]
    return list(indice().get(chave) or [game])


def primario(members: list):
    if len(members) == 1:
        return members[0]
    preferido = _carregar()["preferidos"].get(atualizar(members[0]))
    for member in members:
        if member.game_id == preferido:
            return member
    return max(
        members,
        key=lambda g: (g.last_played or 0, g.added or 0, g.name or ""),
    )


def definir_preferido(game) -> None:
    prefs = _carregar()
    prefs["preferidos"][atualizar(game)] = game.game_id
    _salvar()
    limpar_indice()


def desagrupar_chave(chave: str) -> None:
    prefs = _carregar()
    if chave not in prefs["desagrupados"]:
        prefs["desagrupados"].append(chave)
    prefs["preferidos"].pop(chave, None)
    _salvar()
    limpar_indice()


def garantir(game) -> None:
    """Garante card para o primário do grupo (cura após remoção)."""
    global _curando
    if _curando:
        return
    try:
        prim = primario(membros(game))
        if prim.get_parent() is None:
            _curando = True
            try:
                prim.update()
            finally:
                _curando = False
    except Exception as error:  # pylint: disable=broad-exception-caught
        logging.debug("Não foi possível curar o grupo: %s", error)


def reconciliar() -> None:
    """Garante card para o primário de todo grupo (cura após exclusão)."""
    global _curando
    if _curando or not ativo():
        return
    try:
        grupos = indice()
        _curando = True
        try:
            for members in grupos.values():
                if len(members) < 2:
                    continue
                prim = primario(members)
                if prim.get_parent() is None:
                    prim.update()
        finally:
            _curando = False
    except Exception as error:  # pylint: disable=broad-exception-caught
        logging.debug("Não foi possível reconciliar os grupos: %s", error)
