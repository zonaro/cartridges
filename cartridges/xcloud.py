# xcloud.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tela separada do Xbox Cloud Gaming (https://www.xbox.com/play).

WebView dedicada com o userscript Better xCloud
(https://github.com/redphx/better-xcloud) baixado sob demanda — sempre a
versão mais recente do GitHub Releases, com fallback para o cache local
quando offline.

O import do WebKit é opcional/lazy: sem WebKitGTK instalado o app continua
funcionando, só a tela do xCloud fica indisponível (com toast explicativo).
"""

import logging
import re
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Optional

XCLOUD_HOME_URL = "https://www.xbox.com/play"
BETTER_XCLOUD_URL = (
    "https://github.com/redphx/better-xcloud/releases/latest/download/"
    "better-xcloud.user.js"
)
XCLOUD_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
FILL_CSS = "html,body{margin:0!important;padding:0!important;border:0!important}"
DETECT_JS = "typeof window.BX_EXPOSED!=='undefined'&&window.BX_EXPOSED!==null"

_WebKit = None
_webkit_tried = False


def _cache_file() -> Path:
    from cartridges import shared

    return shared.cache_dir / "cartridges" / "better-xcloud.user.js"


def webkit_available() -> bool:
    """True se o WebKitGTK (6.0 ou 4.1) estiver importável."""
    global _WebKit, _webkit_tried
    if _WebKit is not None:
        return True
    if _webkit_tried:
        return False
    _webkit_tried = True
    try:
        import gi

        try:
            gi.require_version("WebKit", "6.0")
        except ValueError:
            gi.require_version("WebKit", "4.1")
        from gi.repository import WebKit

        _WebKit = WebKit
        return True
    except (ValueError, ImportError) as error:
        logging.debug("WebKit indisponível para o Xbox Cloud Gaming: %s", error)
        return False


def get_cached_script() -> Optional[str]:
    try:
        cache = _cache_file()
        if cache.is_file():
            return cache.read_text(encoding="utf-8")
    except OSError as error:
        logging.debug("Não foi possível ler o cache do Better xCloud: %s", error)
    return None


def parse_version(script: Optional[str]) -> Optional[str]:
    if not script:
        return None
    match = re.search(r"@version\s+(\S+)", script)
    return match.group(1) if match else None


def get_cached_version() -> Optional[str]:
    return parse_version(get_cached_script())


def fetch_better_xcloud(timeout: int = 20) -> Optional[str]:
    """Baixa o better-xcloud.user.js mais recente (síncrono).

    Salva no cache em caso de sucesso; em falha retorna o cache.
    """
    try:
        request = urllib.request.Request(
            BETTER_XCLOUD_URL,
            headers={"User-Agent": XCLOUD_USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            script = response.read().decode("utf-8", errors="replace")
        if "Better xCloud" not in script and "better" not in script.lower():
            raise ValueError("conteúdo inesperado no download do Better xCloud")
        try:
            cache = _cache_file()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(script, encoding="utf-8")
        except OSError as error:
            logging.debug("Não foi possível salvar o cache do Better xCloud: %s", error)
        logging.info(
            "Better xCloud atualizado (v%s, %d bytes)",
            parse_version(script),
            len(script),
        )
        return script
    except Exception as error:  # rede indisponível, release movido, etc.
        logging.warning("Falha ao baixar o Better xCloud mais recente: %s", error)
        cached = get_cached_script()
        if cached is not None:
            logging.info(
                "Better xCloud: usando cache local (v%s)", parse_version(cached)
            )
        return cached


def fetch_better_xcloud_async(
    callback: Callable[[Optional[str], bool], None],
) -> None:
    """Baixa o script numa thread; chama callback(script, veio_da_rede)."""
    cached = get_cached_script()

    def work() -> None:
        from gi.repository import GLib

        script = fetch_better_xcloud()
        from_network = script is not None and script != cached
        GLib.idle_add(callback, script, from_network)

    threading.Thread(target=work, daemon=True).start()


def create_xcloud_webview(script_js: Optional[str] = None):
    """Cria o WebKit.WebView do xCloud com o Better xCloud pré-injetado.

    Retorna None se o WebKit não estiver disponível.
    """
    if not webkit_available():
        return None
    assert _WebKit is not None
    WebKit = _WebKit

    # WebKitGTK 6 removed new_with_user_content_manager().  Creating the
    # view first and configuring its own manager works on both 6.0 and 4.1.
    webview = WebKit.WebView.new()
    manager = webview.get_user_content_manager()
    try:
        stylesheet = WebKit.UserStyleSheet.new(
            FILL_CSS,
            WebKit.UserContentInjectedFrames.ALL_FRAMES,
            WebKit.UserStyleLevel.AUTHOR,
            None,
            None,
        )
        manager.add_style_sheet(stylesheet)
    except Exception as error:
        logging.debug("Não foi possível injetar o CSS de preenchimento: %s", error)
    if script_js:
        try:
            userscript = WebKit.UserScript.new(
                script_js,
                WebKit.UserContentInjectedFrames.ALL_FRAMES,
                WebKit.UserScriptInjectionTime.START,
                None,
                None,
            )
            manager.add_script(userscript)
            logging.info(
                "Better xCloud v%s registrado para injeção no document-start",
                parse_version(script_js),
            )
        except Exception as error:
            logging.warning("Não foi possível pré-injetar o Better xCloud: %s", error)

    try:
        from gi.repository import Gdk

        webview.set_background_color(Gdk.RGBA(red=0, green=0, blue=0, alpha=1))
    except Exception as error:
        logging.debug("Não foi possível definir o fundo do WebView: %s", error)

    try:
        settings = webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_javascript_can_open_windows_automatically(True)
        settings.set_enable_media_stream(True)
        settings.set_enable_webaudio(True)
        settings.set_enable_mediasource(True)
        settings.set_enable_encrypted_media(True)
        settings.set_enable_fullscreen(True)
        # Gamepad API: expõe os controles do libmanette/SDL à página.
        if hasattr(settings, "set_enable_gamepad"):
            settings.set_enable_gamepad(True)
        settings.set_user_agent(XCLOUD_USER_AGENT)
    except Exception as error:
        logging.debug("Ajuste parcial das settings do WebView do xCloud: %s", error)
    return webview


def load_xcloud_home(webview) -> None:
    webview.load_uri(XCLOUD_HOME_URL)


def check_script_active(webview, callback: Callable[[bool], None]) -> None:
    try:

        def _done(view, result, _user_data=None) -> None:
            try:
                value = view.evaluate_javascript_finish(result)
                callback(bool(value is not None and value.to_boolean()))
            except Exception:
                callback(False)

        try:
            webview.evaluate_javascript(DETECT_JS, -1, None, _done, None)
        except TypeError:
            webview.evaluate_javascript(DETECT_JS, None, _done, None)
    except Exception:
        callback(False)


def watch_script_active(webview, on_result: Callable[[bool], None]) -> None:
    try:
        WebKit = _WebKit
        checked = False

        def _changed(view, event) -> None:
            nonlocal checked
            if checked or event != WebKit.LoadEvent.FINISHED:
                return
            try:
                uri = view.get_uri() or ""
            except Exception:
                return
            if "xbox.com" not in uri or ("/play" not in uri and "auth" not in uri):
                return
            checked = True
            check_script_active(view, on_result)

        webview.connect("load-changed", _changed)
    except Exception as error:
        logging.debug("Não foi possível observar o load do xCloud: %s", error)
