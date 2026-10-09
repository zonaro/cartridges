# xcloud_login.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Login da conta Xbox para o player nativo do xCloud.

Dois caminhos, sem webview na tela principal:

- **Device code** (recomendado): o usuário abre o endereço em qualquer
  navegador e informa o código; aqui só fazemos o polling.
- **Browser embutido**: um ``WebKit.WebView`` separado carrega o authorize
  da Microsoft com PKCE; o redirect é interceptado em ``decide-policy``
  antes de navegar e o ``code`` trocado por tokens.

A parte headless (PKCE, parsing, :class:`LoginFlow`) não importa GTK e
pode rodar nos testes sem display. A UI fica em :func:`show_login_dialog`,
com imports lazzy de gi.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import urllib.parse
from typing import Any, Callable, Dict, Optional, Tuple

from cartridges.xcloud_native import auth

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
# Redirect comprovado do AutoXGP (AneryCoft/AutoXGP): a Microsoft valida o
# redirect_uri exato na troca do code, então copiamos a string registrada.
REDIRECT_URI = "https://www.xbox.com/auth/msa?action=loggedIn&locale_hint=zh-HK"
AUTHORIZE_SCOPE = "xboxlive.signin openid profile offline_access"
MS_ORIGIN = "https://www.xbox.com"

_WebKit = None
_webkit_tried = False


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
        logging.debug("WebKit indisponível para o login do xCloud: %s", error)
        return False


def make_pkce() -> Tuple[str, str, str]:
    """Gera (verifier, challenge, state) para o authorization code + PKCE."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(24)
    return verifier, challenge, state


def build_authorize_url(redirect_uri: str, state: str, code_challenge: str) -> str:
    """Monta a URL do authorize com PKCE S256."""
    params = {
        "client_id": auth.MSAL_CLIENT_ID,
        "response_type": "code",
        "response_mode": "fragment",
        "redirect_uri": redirect_uri,
        "scope": AUTHORIZE_SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def parse_redirect_tokens(uri: str, expected_state: Optional[str] = None) -> Dict[str, str]:
    """Extrai code/state/tokens do redirect de login.

    Aceita ``response_mode=fragment`` ou ``query``. Lança ``ValueError`` em
    erro do provedor (``error``), state divergente ou ausência de code/token.
    """
    parsed = urllib.parse.urlsplit(uri or "")
    params: Dict[str, str] = {}
    for chunk in (parsed.query, parsed.fragment):
        if not chunk:
            continue
        for key, values in urllib.parse.parse_qs(chunk, keep_blank_values=True).items():
            params[key] = values[0] if values else ""
    if "error" in params:
        detail = params.get("error_description") or params.get("error")
        raise ValueError(f"autorização recusada: {detail}")
    if expected_state is not None and params.get("state", "") != expected_state:
        raise ValueError("state do redirect não confere com o pedido")
    if not params.get("code") and not params.get("access_token"):
        raise ValueError("redirect sem código de autorização")
    return params


def exchange_code(
    code: str,
    redirect_uri: str,
    code_verifier: str,
    auth_module: Any = None,
) -> Tuple[str, str]:
    """Troca o authorization code por access_token + refresh_token (PKCE).

    Retorna ``(access_token, refresh_token)``.
    """
    module = auth_module or auth
    data = urllib.parse.urlencode(
        {
            "client_id": module.MSAL_CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "scope": AUTHORIZE_SCOPE,
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": MS_ORIGIN,
        "Cache-Control": "no-store",
    }
    status, body, _ = module._http_request(  # pylint: disable=protected-access
        "POST", TOKEN_URL, data=data, headers=headers
    )
    if status != 200:
        raise Exception(f"Troca do código de autorização falhou (HTTP {status}): {body[:200]!r}")
    resp = json.loads(body.decode("utf-8", errors="replace"))
    access = str(resp.get("access_token", ""))
    refresh = str(resp.get("refresh_token", ""))
    if not access:
        raise Exception("Troca do código não devolveu access_token")
    return access, refresh


def has_saved_login() -> bool:
    """True quando existe refresh_token guardado no Secret Service."""
    try:
        return bool(auth._load_refresh_token())  # pylint: disable=protected-access
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def clear_saved_login() -> None:
    """Remove o refresh_token do Secret Service."""
    try:
        auth._clear_refresh_token()  # pylint: disable=protected-access
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.debug("Não foi possível limpar o refresh_token: %s", error)


def _is_redirect_uri(uri: str) -> bool:
    """True quando o redirect pertence ao nosso login (scheme/netloc/path)."""
    target = urllib.parse.urlsplit(REDIRECT_URI)
    got = urllib.parse.urlsplit(uri or "")
    return (got.scheme, got.netloc, got.path) == (target.scheme, target.netloc, target.path)


class LoginFlow:
    """Orquestra o login sem importar GTK.

    Estágios emitidos via ``on_stage(flow, stage, dados)``: ``idle``,
    ``working``, ``device``, ``browser``, ``error`` e ``done``. Com
    ``blocking=True`` os estágios são entregues em linha (testes); caso
    contrário ``working``/``error``/``done`` chegam pela thread da UI.
    """

    def __init__(
        self,
        auth_module: Any = None,
        on_stage: Optional[Callable[["LoginFlow", str, Dict[str, Any]], None]] = None,
        blocking: bool = False,
    ) -> None:
        self.auth = auth_module or auth
        self.on_stage = on_stage
        self.blocking = blocking
        self.stage = "idle"
        self.data: Dict[str, Any] = {}
        self.error: Optional[str] = None
        self.result: Any = None
        self.last_mode: Optional[str] = None
        self._pkce: Optional[Tuple[str, str]] = None
        self._cancelled = False
        self._lock = threading.Lock()

    def _emit(self, stage: str, **data: Any) -> None:
        with self._lock:
            if self._cancelled:
                return
            snapshot = dict(data)
            self.stage = stage
            self.data = snapshot
        if self.on_stage is None:
            return
        if self.blocking:
            self.on_stage(self, stage, snapshot)
        else:
            from cartridges.utils.na_tela import entregar_na_tela

            entregar_na_tela(self.on_stage, self, stage, snapshot)

    def _reset(self, mode: str) -> None:
        with self._lock:
            self._cancelled = False
        self.error = None
        self.result = None
        self.last_mode = mode

    def _spawn(self, worker: Callable[[], None]) -> None:
        if self.blocking:
            worker()
        else:
            threading.Thread(target=worker, daemon=True).start()

    def start_device(self) -> None:
        """Inicia o fluxo device code em thread."""
        self._reset("device")
        self._spawn(self._device_worker)

    def _device_worker(self) -> None:
        self._emit("working")
        try:
            device = self.auth.begin_device_flow()
        except Exception as error:  # pylint: disable=broad-exception-caught
            self._emit("error", message=str(error))
            return
        self._emit(
            "device",
            user_code=str(device.get("user_code", "")),
            verification_uri=str(device.get("verification_uri", "")),
            message=str(device.get("message", "")),
        )
        try:
            result = self.auth.complete_device_flow(device)
        except Exception as error:  # pylint: disable=broad-exception-caught
            self._emit("error", message=str(error))
            return
        self._emit("done", result=result)

    def start_browser(self) -> str:
        """Prepara o authorize URL do browser. Sem I/O; retorna a URL."""
        self._reset("browser")
        verifier, challenge, state = make_pkce()
        self._pkce = (verifier, state)
        url = build_authorize_url(REDIRECT_URI, state, challenge)
        self._emit("browser", url=url)
        return url

    def handle_redirect(self, uri: str) -> bool:
        """Processa o redirect interceptado no webview.

        ``True`` quando consumiu o redirect (nosso redirect: code, tokens,
        erro ou state divergente). ``False`` para deixar o WebView navegar.
        """
        if not _is_redirect_uri(uri):
            return False
        with self._lock:
            pkce = self._pkce
        verifier = pkce[0] if pkce else ""
        expected_state = pkce[1] if pkce else None
        try:
            tokens = parse_redirect_tokens(uri, expected_state=expected_state)
        except ValueError as error:
            self._emit("error", message=str(error))
            return True
        code = tokens.get("code", "")
        access = tokens.get("access_token", "")
        refresh = tokens.get("refresh_token", "")
        if code and verifier:
            self._spawn(lambda: self._exchange_and_login(code, verifier))
        elif access:
            self._spawn(lambda: self._login_with(access, refresh))
        else:
            self._emit("error", message="Redirect sem código ou token de acesso")
        return True

    def _exchange_and_login(self, code: str, verifier: str) -> None:
        try:
            access, refresh = exchange_code(code, REDIRECT_URI, verifier, auth_module=self.auth)
        except Exception as error:  # pylint: disable=broad-exception-caught
            self._emit("error", message=str(error))
            return
        self._login_with(access, refresh)

    def _login_with(self, access: str, refresh: str) -> None:
        try:
            result = self.auth.login_with_browser_tokens(access, refresh or None)
        except Exception as error:  # pylint: disable=broad-exception-caught
            self._emit("error", message=str(error))
            return
        self._emit("done", result=result)

    def retry(self) -> Optional[str]:
        """Repete o último modo. Para browser devolve a nova URL de login."""
        mode = self.last_mode
        if mode == "browser":
            return self.start_browser()
        self.start_device()
        return None

    def cancel(self) -> None:
        """Suprime os próximos estágios (threads daemon continuam e morrem)."""
        with self._lock:
            self._cancelled = True


def show_login_dialog(parent=None, on_finished=None, auth_module=None) -> None:
    """Caixa de login da conta Xbox (device code + browser opcional).

    ``on_finished(resultado_ou_None)`` é chamado no login (LoginResult) ou no
    fechamento sem login (None).
    """
    from gi.repository import Adw, Gdk, Gtk  # noqa: PLC0415

    from cartridges.utils.dialog_backdrop import block_window_drag  # noqa: PLC0415

    if parent is None:
        try:
            from cartridges import shared

            parent = getattr(shared, "win", None)
        except Exception:  # pylint: disable=broad-exception-caught
            parent = None

    concluido = False
    dialogo = Adw.Dialog(title=_("Entrar com a conta Xbox"), content_width=460)
    block_window_drag(dialogo)

    cabecalho = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
    cancelar = Gtk.Button(label=_("Cancelar"))
    cabecalho.pack_start(cancelar)

    pilha = Gtk.Stack()
    toolbar = Adw.ToolbarView(content=pilha)
    toolbar.add_top_bar(cabecalho)
    dialogo.set_child(toolbar)

    display = Gdk.Display.get_default()
    icones = None
    if display is not None:
        try:
            icones = Gtk.IconTheme.get_for_display(display)
        except Exception:  # pylint: disable=broad-exception-caught
            icones = None
    icone_xbox = next(
        (
            nome
            for nome in ("xbox-cloud-symbolic", "xbox-symbolic", "avatar-default-symbolic")
            if icones is None or icones.has_icon(nome)
        ),
        "avatar-default-symbolic",
    )

    # --- Página inicial: escolha do método ---------------------------------
    pagina_intro = Adw.StatusPage(
        title=_("Entrar com a conta Xbox"),
        description=_("Conecte sua conta Microsoft para jogar no Xbox Cloud Gaming."),
        icon_name=icone_xbox,
    )
    botoes_intro = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=8,
        halign=Gtk.Align.CENTER,
        margin_top=24,
    )
    botao_dispositivo = Gtk.Button(
        label=_("Usar código de dispositivo"),
        css_classes=["suggested-action", "pill"],
        halign=Gtk.Align.CENTER,
    )
    botao_dispositivo.connect("clicked", lambda *_: flow.start_device())
    botoes_intro.append(botao_dispositivo)
    if webkit_available():
        botao_navegador = Gtk.Button(
            label=_("Entrar pelo navegador"),
            css_classes=["pill"],
            halign=Gtk.Align.CENTER,
        )
        botao_navegador.connect("clicked", lambda *_: abrir_sub_navegador(flow.start_browser()))
        botoes_intro.append(botao_navegador)
    pagina_intro.child = botoes_intro
    pilha.add_named(pagina_intro, "intro")

    # --- Página do device code ----------------------------------------------
    pagina_dispositivo = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=12,
        halign=Gtk.Align.CENTER,
        valign=Gtk.Align.CENTER,
        margin_top=12,
        margin_bottom=12,
        margin_start=24,
        margin_end=24,
    )
    rotulo_introcao = Gtk.Label(wrap=True, halign=Gtk.Align.CENTER)
    rotulo_codigo = Gtk.Label(
        css_classes=["title-1", "monospace"],
        selectable=True,
        halign=Gtk.Align.CENTER,
    )
    botao_site = Gtk.Button(label=_("Abrir site"), css_classes=["suggested-action", "pill"])
    botao_copiar = Gtk.Button(label=_("Copiar código"), css_classes=["pill"])
    botao_navegador_2 = Gtk.Button(label=_("Ou entrar pelo navegador"), css_classes=["pill"])
    botao_voltar = Gtk.Button(label=_("Voltar"), css_classes=["flat"])
    espera = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
    spinner_dispositivo = Gtk.Spinner()
    rotulo_aguardando = Gtk.Label(label=_("Aguardando autorização…"))
    espera.append(spinner_dispositivo)
    espera.append(rotulo_aguardando)
    botoes_dispositivo = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
    botoes_dispositivo.append(botao_site)
    botoes_dispositivo.append(botao_copiar)
    for filho in (
        rotulo_introcao,
        rotulo_codigo,
        botoes_dispositivo,
        espera,
        botao_navegador_2,
        botao_voltar,
    ):
        pagina_dispositivo.append(filho)
    pilha.add_named(pagina_dispositivo, "dispositivo")

    def ao_voltar(*_args: Any) -> None:
        flow.cancel()
        pilha.set_visible_child_name("intro")

    botao_voltar.connect("clicked", ao_voltar)

    verificacao_uri = {"atual": ""}

    def abrir_site(*_args: Any) -> None:
        from cartridges.utils.open_uri import open_uri  # noqa: PLC0415

        uri = verificacao_uri["atual"] or flow.data.get("verification_uri", "")
        logger.info("xCloud login: abrindo site de pareamento")
        if not uri:
            from cartridges import shared as _shared  # noqa: PLC0415

            try:
                win = _shared.win
                if win is not None:
                    from gi.repository import Adw as _Adw  # noqa: PLC0415

                    win.toast_queue.add(
                        _Adw.Toast.new(_("Código ainda não gerado. Aguarde…"))
                    )
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            return
        if not open_uri(uri):
            logger.warning("xCloud login: open_uri recusou %r", uri[:60])

    def copiar_codigo(*_args: Any) -> None:
        codigo = rotulo_codigo.get_text()
        if not codigo:
            return
        try:
            if display is not None:
                display.get_clipboard().set(codigo)
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.debug("Não foi possível copiar o código: %s", error)

    botao_site.connect("clicked", abrir_site)
    botao_copiar.connect("clicked", copiar_codigo)

    # --- Página "entrando" ------------------------------------------------------
    pagina_trabalho = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=8,
        halign=Gtk.Align.CENTER,
        valign=Gtk.Align.CENTER,
        margin_top=12,
        margin_bottom=12,
    )
    spinner_trabalho = Gtk.Spinner()
    rotulo_trabalho = Gtk.Label(label=_("Entrando…"))
    pagina_trabalho.append(spinner_trabalho)
    pagina_trabalho.append(rotulo_trabalho)
    pilha.add_named(pagina_trabalho, "trabalho")

    # --- Página de erro ------------------------------------------------------
    pagina_erro = Adw.StatusPage(
        title=_("Não foi possível entrar"),
        description=_("Confira a conexão e tente de novo."),
        icon_name="dialog-error-symbolic",
    )
    rotulo_erro = Gtk.Label(selectable=True, wrap=True, halign=Gtk.Align.CENTER)
    botao_tentar = Gtk.Button(label=_("Tentar de novo"), css_classes=["suggested-action", "pill"])
    botao_outro = Gtk.Button(label=_("Escolher outro método"), css_classes=["pill"])
    caixa_erro = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=8,
        margin_top=24,
        halign=Gtk.Align.CENTER,
    )
    caixa_erro.append(rotulo_erro)
    caixa_erro.append(botao_tentar)
    caixa_erro.append(botao_outro)
    pagina_erro.child = caixa_erro
    pilha.add_named(pagina_erro, "erro")

    def ao_tentar(*_args: Any) -> None:
        url = flow.retry()
        if url:
            abrir_sub_navegador(url)

    def ao_outro(*_args: Any) -> None:
        pilha.set_visible_child_name("intro")

    botao_tentar.connect("clicked", ao_tentar)
    botao_outro.connect("clicked", ao_outro)

    def ao_estagio(_fluxo: LoginFlow, stage: str, dados: Dict[str, Any]) -> None:
        nonlocal concluido
        logger.info("xCloud login: estágio %s", stage)
        if concluido:
            return
        if stage == "done":
            concluido = True
            if on_finished is not None:
                on_finished(dados.get("result"))
            dialogo.close()
        elif stage == "working":
            spinner_trabalho.start()
            pilha.set_visible_child_name("trabalho")
            cancelar.grab_focus()
        elif stage == "device":
            verificacao_uri["atual"] = str(dados.get("verification_uri", ""))
            rotulo_introcao.set_text(
                _("Acesse %s no seu navegador e informe o código abaixo:") % dados.get("verification_uri", "")
            )
            rotulo_codigo.set_text(dados.get("user_code", ""))
            spinner_dispositivo.start()
            pilha.set_visible_child_name("dispositivo")
            botao_site.grab_focus()
        elif stage == "error":
            mensagem = str(dados.get("message", ""))
            logger.warning("xCloud login: falha: %.300s", mensagem)
            rotulo_erro.set_text(mensagem)
            pilha.set_visible_child_name("erro")
            botao_tentar.grab_focus()
        # "browser": o sub-diálogo do navegador cuida sozinho.

    flow = LoginFlow(auth_module=auth_module, on_stage=ao_estagio)

    # --- Sub-diálogo do navegador embutido -----------------------------------
    def abrir_sub_navegador(url: str) -> None:
        assert _WebKit is not None
        WebKit = _WebKit

        sub = Adw.Dialog(title=_("Entrar pela Microsoft"), content_width=800, content_height=600)
        block_window_drag(sub)
        sub_cabecalho = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        fechar = Gtk.Button(label=_("Cancelar"))
        sub_cabecalho.pack_start(fechar)
        webview = WebKit.WebView.new()
        barra = Adw.ToolbarView(content=webview)
        barra.add_top_bar(sub_cabecalho)
        sub.set_child(barra)

        def _uri_decisao(decision: Any) -> str:
            try:
                acao = decision.get_navigation_action()
            except Exception:  # pylint: disable=broad-exception-caught
                return ""
            try:
                if acao is None:
                    return ""
                pedido = acao.get_request()
                return pedido.get_uri() if pedido is not None else ""
            except Exception:  # pylint: disable=broad-exception-caught
                return ""

        def _ao_decidir(view: Any, decision: Any, decision_type: int) -> bool:
            # Só navegações de página passam por aqui. Decisões de RESPOSTA
            # (MIME/download) devem seguir o padrão: ignorá-las com ignore()
            # interrompe o carregamento e deixa a página em branco.
            try:
                tipos_pagina = (
                    WebKit.PolicyDecisionType.NAVIGATION_ACTION,
                    WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                tipos_pagina = ()
            if tipos_pagina and decision_type not in tipos_pagina:
                return False
            uri = _uri_decisao(decision) or ""
            if not uri.startswith(("http://", "https://")):
                try:
                    decision.ignore()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                return True
            if flow.handle_redirect(uri):
                try:
                    decision.ignore()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                sub.close()
                return True
            lowered = uri.lower()
            interno = any(
                dominio in lowered
                for dominio in ("microsoftonline.com", "login.live.com", "microsoft.com", "xbox.com")
            )
            if interno:
                if decision_type == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
                    try:
                        decision.ignore()
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
                    view.load_uri(uri)
                    return True
                return False
            try:
                decision.ignore()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            try:
                from cartridges.utils.open_uri import open_uri  # noqa: PLC0415

                open_uri(uri, parent)
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.debug("Não foi possível abrir link externo do login: %s", error)
            return True

        webview.connect("decide-policy", _ao_decidir)

        def _ao_criar(_view: Any, _acao: Any) -> None:
            return None

        webview.connect("create", _ao_criar)
        fechar.connect("clicked", lambda *_: sub.close())
        sub.present(dialogo)
        webview.grab_focus()
        webview.load_uri(url)

    def ao_fechar(*_args: Any) -> None:
        if concluido:
            return
        flow.cancel()
        if on_finished is not None:
            on_finished(None)

    dialogo.connect("closed", ao_fechar)
    cancelar.connect("clicked", lambda *_: dialogo.close())
    if webkit_available():
        def _ir_navegador(*_args: Any) -> None:
            flow.cancel()
            abrir_sub_navegador(flow.start_browser())

        botao_navegador_2.connect("clicked", _ir_navegador)
    else:
        botao_navegador_2.set_visible(False)
    pilha.set_visible_child_name("intro")
    dialogo.present(parent if parent is not None else None)
    botao_dispositivo.grab_focus()
    # O código aparece sozinho: o fluxo device já sai pedindo o pareamento.
    logger.info("xCloud login: diálogo aberto, iniciando device flow")
    flow.start_device()