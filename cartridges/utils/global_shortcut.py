# global_shortcut.py
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

"""Registra o atalho global Super+G que abre o Cartridges de qualquer lugar.

Wayland não deixa um app GTK capturar teclas globais sozinho, então o
registro é delegado ao compositor de cada ambiente:

- GNOME (e derivados com o mesmo esquema): entrada personalizada em
  ``org.gnome.settings-daemon.plugins.media-keys`` via ``Gio.Settings``.
- KDE Plasma 5/6: arquivo ``cartridges-global.desktop`` com
  ``X-KDE-Shortcuts`` + entrada ``[services]`` no ``kglobalshortcutsrc``,
  ativado na sessão atual via D-Bus (``org.kde.KGlobalAccel``). Nunca se
  reinicia o daemon no Plasma 6 — ele roda dentro do KWin.
- Qualquer outro ambiente: sem backend; a interface mostra o comando
  manual em vez do botão.

Nada aqui importa ``gi`` no topo para continuar importável nos testes.
"""

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

BINDING_GNOME = "<Super>g"
BINDING_KDE = "Meta+G"

GNOME_MEDIA_KEYS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
GNOME_CUSTOM_SCHEMA = GNOME_MEDIA_KEYS_SCHEMA + ".custom-keybinding"
GNOME_SHORTCUT_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/cartridges/"
)

KDE_DESKTOP_FILE = "cartridges-global.desktop"
KDE_SERVICE = "org.kde.kglobalaccel"
KDE_OBJECT = "/kglobalaccel"
KDE_IFACE = "org.kde.KGlobalAccel"

# Qt.KeyboardModifier.META (tecla Super/Windows); vale no Qt5 e no Qt6.
QT_META_MODIFIER = 0x10000000
KDE_KEYCODE_G = QT_META_MODIFIER | ord("G")


class ShortcutError(Exception):
    """Falha ao consultar, registrar ou remover o atalho global."""


@dataclass
class ShortcutStatus:
    backend: Optional[str]  # "gnome", "kde" ou None (sem suporte)
    registered: bool
    conflict: Optional[str] = None  # quem já segura a tecla, se descoberto


def resolve_command() -> str:
    """Comando que abre o Cartridges instalado, na melhor forma encontrada."""
    override = os.environ.get("CARTRIDGES_COMMAND", "").strip()
    if override:
        return override
    local = Path.home() / ".local" / "bin" / "cartridges"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    found = shutil.which("cartridges")
    if found:
        return found
    if shutil.which("flatpak"):
        try:
            subprocess.run(
                ["flatpak", "info", "page.kramo.Cartridges"],
                capture_output=True,
                check=True,
                timeout=15,
            )
        except (subprocess.SubprocessError, OSError):
            pass
        else:
            return "flatpak run page.kramo.Cartridges"
    return "cartridges"


def desktop_family_from_string(desktop: str) -> Optional[str]:
    """Mapeia XDG_CURRENT_DESKTOP/DESKTOP_SESSION para "gnome", "kde" ou None."""
    tokens = {
        token.strip().lower()
        for token in desktop.replace(":", ";").replace(",", ";").split(";")
        if token.strip()
    }
    if tokens & {"gnome", "unity", "budgie", "pantheon", "cinnamon"}:
        return "gnome"
    if tokens & {"kde", "plasma", "lxqt"}:
        return "kde"
    return None


def gnome_available() -> bool:
    """Diz se o esquema de atalhos do GNOME existe nesta máquina."""
    try:
        from gi.repository import Gio  # noqa: PLC0415
    except (ImportError, ValueError):
        return False
    try:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None:
            return False
        return source.lookup(GNOME_MEDIA_KEYS_SCHEMA, True) is not None and (
            source.lookup(GNOME_CUSTOM_SCHEMA, True) is not None
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logging.debug("Esquema de atalhos do GNOME indisponível", exc_info=True)
        return False


def kde_available() -> bool:
    """Diz se as ferramentas de atalho do KDE existem nesta máquina."""
    return (
        shutil.which("kwriteconfig6") is not None
        or shutil.which("kwriteconfig5") is not None
    )


def detect_backend() -> Optional[str]:
    """Backend utilizável aqui: "gnome", "kde" ou None."""
    desktop = ";".join(
        filter(
            None,
            (os.environ.get("XDG_CURRENT_DESKTOP", ""), os.environ.get("DESKTOP_SESSION", "")),
        )
    )
    family = desktop_family_from_string(desktop)
    if family == "gnome" and gnome_available():
        return "gnome"
    if family == "kde" and kde_available():
        return "kde"
    # Sem pista do ambiente, usa o primeiro backend funcional.
    if gnome_available():
        return "gnome"
    if kde_available():
        return "kde"
    return None


def get_status() -> ShortcutStatus:
    """Estado atual do Super+G para a interface de configurações."""
    backend = detect_backend()
    if backend == "gnome":
        return ShortcutStatus(
            backend="gnome",
            registered=_gnome_is_registered(),
            conflict=_gnome_conflict(),
        )
    if backend == "kde":
        return ShortcutStatus(
            backend="kde",
            registered=_kde_is_registered(),
            conflict=_kde_conflict(),
        )
    return ShortcutStatus(backend=None, registered=False)


def set_enabled(enabled: bool) -> ShortcutStatus:
    """Registra ou remove o Super+G; devolve o estado resultante."""
    backend = detect_backend()
    if backend == "gnome":
        if enabled:
            _gnome_register()
        else:
            _gnome_unregister()
    elif backend == "kde":
        if enabled:
            _kde_register()
        else:
            _kde_unregister()
    else:
        raise ShortcutError(
            "Ambiente sem suporte para atalho global. "
            "No GNOME, crie em Configurações > Teclado > Atalhos personalizados "
            f"com o comando: {resolve_command()}"
        )
    return get_status()


def with_path(entries: list[str], path: str) -> list[str]:
    """Devolve a lista de atalhos garantindo ``path`` sem duplicar."""
    return entries if path in entries else [*entries, path]


def without_path(entries: list[str], path: str) -> list[str]:
    """Devolve a lista de atalhos sem ``path``."""
    return [entry for entry in entries if entry != path]


def _gnome_settings():
    from gi.repository import Gio  # noqa: PLC0415

    try:
        media = Gio.Settings.new(GNOME_MEDIA_KEYS_SCHEMA)
    except Exception as error:  # pylint: disable=broad-exception-caught
        raise ShortcutError(f"Não foi possível abrir os atalhos do GNOME: {error}")
    return media


def _gnome_custom(path: str):
    from gi.repository import Gio  # noqa: PLC0415

    return Gio.Settings.new_with_path(GNOME_CUSTOM_SCHEMA, path)


def _gnome_entries() -> list[str]:
    return list(_gnome_settings().get_strv("custom-keybindings"))


def _gnome_is_registered() -> bool:
    try:
        entries = _gnome_entries()
    except ShortcutError:
        return False
    if GNOME_SHORTCUT_PATH not in entries:
        return False
    try:
        binding = _gnome_custom(GNOME_SHORTCUT_PATH).get_string("binding")
    except Exception:  # pylint: disable=broad-exception-caught
        return False
    return binding == BINDING_GNOME


def _gnome_conflict() -> Optional[str]:
    try:
        entries = _gnome_entries()
    except ShortcutError:
        return None
    for path in entries:
        if path == GNOME_SHORTCUT_PATH:
            continue
        try:
            custom = _gnome_custom(path)
            if custom.get_string("binding") != BINDING_GNOME:
                continue
            name = custom.get_string("name") or path
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        return str(name)
    return None


def _gnome_register() -> None:
    media = _gnome_settings()
    entries = with_path(list(media.get_strv("custom-keybindings")), GNOME_SHORTCUT_PATH)
    media.set_strv("custom-keybindings", entries)
    for path in entries:
        if path == GNOME_SHORTCUT_PATH:
            continue
        try:
            custom = _gnome_custom(path)
            if custom.get_string("binding") == BINDING_GNOME:
                custom.set_string("binding", "")
        except Exception:  # pylint: disable=broad-exception-caught
            logging.debug("Ignorando entrada de atalho %s", path, exc_info=True)
    custom = _gnome_custom(GNOME_SHORTCUT_PATH)
    custom.set_string("name", "Cartridges")
    custom.set_string("command", resolve_command())
    custom.set_string("binding", BINDING_GNOME)
    try:
        from gi.repository import Gio  # noqa: PLC0415

        Gio.Settings.sync()
    except Exception as error:  # pylint: disable=broad-exception-caught
        raise ShortcutError(f"Não foi possível gravar o atalho: {error}")


def _gnome_unregister() -> None:
    media = _gnome_settings()
    entries = without_path(list(media.get_strv("custom-keybindings")), GNOME_SHORTCUT_PATH)
    media.set_strv("custom-keybindings", entries)
    try:
        custom = _gnome_custom(GNOME_SHORTCUT_PATH)
        for key in ("binding", "command", "name"):
            custom.reset(key)
        from gi.repository import Gio  # noqa: PLC0415

        Gio.Settings.sync()
    except Exception as error:  # pylint: disable=broad-exception-caught
        raise ShortcutError(f"Não foi possível remover o atalho: {error}")


def _kde_tool(base: str) -> Optional[str]:
    """kwriteconfig6/kreadconfig6 no Plasma 6, variantes 5 no Plasma 5."""
    for suffix in ("6", "5", ""):
        tool = shutil.which(f"{base}{suffix}")
        if tool:
            return tool
    return None


def _kde_apps_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "").strip() or str(
        Path.home() / ".local" / "share"
    )
    return Path(base) / "applications"


def kde_desktop_entry(command: str) -> str:
    """Conteúdo do .desktop que o kglobalaccel descobre ainda nesta sessão."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Cartridges\n"
        "Comment=Abrir o Cartridges com Super+G\n"
        f"Exec={command}\n"
        "Icon=page.kramo.Cartridges\n"
        "Terminal=false\n"
        "Categories=Game;\n"
        "NoDisplay=true\n"
        f"X-KDE-Shortcuts={BINDING_KDE}\n"
    )


def _kde_bus():
    from gi.repository import Gio  # noqa: PLC0415

    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception as error:  # pylint: disable=broad-exception-caught
        raise ShortcutError(f"Não foi possível falar com a sessão D-Bus: {error}")


def _kde_set_live(action: list[str], keys: list[int]) -> None:
    """Aplica a tecla na sessão atual; o daemon persiste sozinho depois."""
    from gi.repository import Gio, GLib  # noqa: PLC0415

    connection = _kde_bus()
    try:
        connection.call_sync(
            KDE_SERVICE,
            KDE_OBJECT,
            KDE_IFACE,
            "setForeignShortcut",
            GLib.Variant("(asai)", (action, keys)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        raise ShortcutError(f"O KDE não aceitou o atalho: {error}")


def _kde_action_id() -> list[str]:
    return [KDE_DESKTOP_FILE, "_launch", "", ""]


def _kde_run(tool: Optional[str], *args: str, check: bool = True) -> Optional[str]:
    if tool is None:
        return None
    try:
        result = subprocess.run(
            [tool, *args], capture_output=True, text=True, timeout=30
        )
    except (subprocess.SubprocessError, OSError) as error:
        if check:
            raise ShortcutError(f"Falha ao executar {tool}: {error}")
        return None
    if check and result.returncode != 0:
        raise ShortcutError(
            f"Falha ao executar {tool}: {result.stderr.strip() or result.returncode}"
        )
    return result.stdout.strip()


def _kde_is_registered() -> bool:
    tool = _kde_tool("kreadconfig")
    saved = _kde_run(tool, "--file", "kglobalshortcutsrc", "--group", "services",
                     "--group", KDE_DESKTOP_FILE, "--key", "_launch", check=False)
    if saved:
        return saved == BINDING_KDE
    return (_kde_apps_dir() / KDE_DESKTOP_FILE).is_file()


def _kde_conflict() -> Optional[str]:
    """Quem já segura Meta+G segundo o daemon (melhor esforço)."""
    try:
        connection = _kde_bus()
        from gi.repository import Gio, GLib  # noqa: PLC0415

        result = connection.call_sync(
            KDE_SERVICE,
            KDE_OBJECT,
            KDE_IFACE,
            "getGlobalShortcutsByKey",
            GLib.Variant("(i)", (KDE_KEYCODE_G,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    items = result.unpack()[0] if result is not None else []
    owners = [item[0] for item in items if item and item[0] != KDE_DESKTOP_FILE]
    return owners[0] if owners else None


def _kde_register() -> None:
    command = resolve_command()
    apps_dir = _kde_apps_dir()
    apps_dir.mkdir(parents=True, exist_ok=True)
    target = apps_dir / KDE_DESKTOP_FILE
    tmp = target.with_suffix(".desktop.novo")
    tmp.write_text(kde_desktop_entry(command), encoding="utf-8")
    tmp.replace(target)

    sycoca = _kde_tool("kbuildsycoca") or _kde_tool("ksycoca")
    _kde_run(sycoca, "--noincremental", check=False)

    kwrite = _kde_tool("kwriteconfig")
    if kwrite is None:
        raise ShortcutError("kwriteconfig não encontrado; instale as ferramentas do KDE.")
    _kde_run(kwrite, "--file", "kglobalshortcutsrc", "--group", "services",
             "--group", KDE_DESKTOP_FILE, "--key", "_launch", BINDING_KDE)
    _kde_set_live(_kde_action_id(), [KDE_KEYCODE_G])


def _kde_unregister() -> None:
    # Solta a tecla na sessão antes de apagar o serviço, senão o KWin
    # remove o componente mas mantém a captura da tecla na memória.
    try:
        _kde_set_live(_kde_action_id(), [])
    except ShortcutError:
        logging.debug("Tecla já solta ou daemon inalcançável", exc_info=True)
    kwrite = _kde_tool("kwriteconfig")
    _kde_run(kwrite, "--file", "kglobalshortcutsrc", "--group", "services",
             "--group", KDE_DESKTOP_FILE, "--key", "_launch", "--delete", check=False)
    try:
        (_kde_apps_dir() / KDE_DESKTOP_FILE).unlink(missing_ok=True)
    except OSError as error:
        raise ShortcutError(f"Não foi possível apagar o atalho: {error}")
    sycoca = _kde_tool("kbuildsycoca") or _kde_tool("ksycoca")
    _kde_run(sycoca, "--noincremental", check=False)
