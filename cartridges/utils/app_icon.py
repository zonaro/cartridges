# app_icon.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Escolha do ícone do aplicativo.

O Jolven tem um ícone oficial (``jolven-dpad``) e uma alternativa
(``jolven-portal``). A seleção vive no GSettings (chave ``app-icon``)
e é aplicada como uma sobreposição de ícone no diretório do usuário:
o arquivo do tema do sistema nunca é tocado, então a escolha sobrevive
a atualizações e é revertida apenas apagando a sobreposição. Como o
cache de ícones do desktop pode demorar a notar a troca, a interface
avisa que pode ser preciso entrar de novo.
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

from gi.repository import Gio

from cartridges import shared

DEFAULT = "jolven-dpad"
CHOICES = ("jolven-dpad", "jolven-portal")

# Ícones antigos (antes da padronização em jolven-dpad/jolven-portal)
# mapeados para o equivalente atual durante a migração.
LEGACY_ICONS = {
    "j-na-tela": "jolven-dpad",
    "j-dpad": "jolven-dpad",
    "portal": "jolven-portal",
}

_RESOURCE_FILES = {
    "jolven-portal": "alternatives/jolven-portal.svg",
}


def user_icon_path(base: Optional[Path] = None) -> Path:
    """Caminho da sobreposição de ícone no diretório do usuário."""
    root = base if base is not None else Path.home() / ".local" / "share" / "icons"
    return root / "hicolor" / "scalable" / "apps" / f"{shared.APP_ID}.svg"


def _marker_path(target: Path) -> Path:
    """Prova de que a sobreposição foi criada pelo Jolven (e não é o
    ícone instalado pelo pacote, que nunca pode ser apagado daqui)."""
    return target.with_suffix(".svg.jolven-override")


def _is_managed(target: Path) -> bool:
    return _marker_path(target).is_file()


def _refresh_cache(icon_file: Path) -> None:
    try:
        subprocess.run(
            ["gtk-update-icon-cache", "-f", "-t", str(icon_file.parent.parent.parent)],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        logging.debug("Não foi possível atualizar o cache de ícones: %s", error)


def apply(choice: str, base: Optional[Path] = None) -> str:
    """Aplica ``choice`` e devolve a escolha efetiva.

    ``jolven-dpad`` remove a sobreposição (volta o ícone do sistema);
    qualquer valor fora de ``CHOICES`` é ignorado sem efeitos.
    """
    if choice not in CHOICES:
        logging.warning("Ícone desconhecido: %s", choice)
        return current(base)
    target = user_icon_path(base)
    if choice == DEFAULT:
        if _is_managed(target):
            try:
                target.unlink(missing_ok=True)
                _marker_path(target).unlink(missing_ok=True)
            except OSError as error:
                logging.warning("Não foi possível remover o ícone: %s", error)
            else:
                _refresh_cache(target)
        return DEFAULT
    try:
        data = Gio.resources_lookup_data(
            f"{shared.PREFIX}/{_RESOURCE_FILES[choice]}", 0
        ).get_data()
    except Exception as error:  # pylint: disable=broad-exception-caught
        logging.warning("Ícone %s indisponível nos recursos: %s", choice, error)
        return current(base)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(data))
        _marker_path(target).write_text(choice, encoding="utf-8")
    except OSError as error:
        logging.warning("Não foi possível instalar o ícone: %s", error)
        return current(base)
    _refresh_cache(target)
    return choice


def current(base: Optional[Path] = None) -> str:
    """Escolha efetiva: a salva, ou o padrão quando nada foi sobreposto."""
    saved = ""
    try:
        saved = shared.schema.get_string("app-icon")
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    if saved in LEGACY_ICONS:
        saved = LEGACY_ICONS[saved]
    if saved in CHOICES and (saved == DEFAULT or user_icon_path(base).is_file()):
        return saved
    return DEFAULT


def ensure() -> None:
    """Remove sobreposição órfã quando a escolha salva é o padrão.

    Só remove arquivos com marcador do Jolven: o ícone instalado pelo
    pacote no mesmo caminho nunca é tocado. Valores antigos de
    ``app-icon`` são migrados para os nomes atuais.
    """
    try:
        saved = shared.schema.get_string("app-icon")
    except Exception:  # pylint: disable=broad-exception-caught
        return
    if saved in LEGACY_ICONS:
        migrated = LEGACY_ICONS[saved]
        try:
            shared.schema.set_string("app-icon", migrated)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        apply(migrated)
        return
    if saved != DEFAULT:
        return
    target = user_icon_path()
    if not _is_managed(target):
        return
    try:
        target.unlink(missing_ok=True)
        _marker_path(target).unlink(missing_ok=True)
    except OSError as error:
        logging.debug("Sobreposição de ícone não removida: %s", error)
