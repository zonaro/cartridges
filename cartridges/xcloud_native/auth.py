# auth.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Autenticação Microsoft/Xbox para xCloud + xHome (sem webview).

Implementa o fluxo moderno MSAL (Device Code) seguindo os passos A–F
definidos em xal-node (unknownskl/xal-node) e com base em endpoints
do xcloud-python (OpenXbox/xcloud-python) apenas para forma de endpoints.

Este módulo não faz importações GTK e não registra tokens em log.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict

logger = logging.getLogger(__name__)

MSAL_CLIENT_ID = "1f907974-e22b-4810-a9de-d9647380c97e"
DEVICE_CODE_SCOPE = "xboxlive.signin openid profile offline_access"
LPT_SCOPE = "service::http://Passport.NET/purpose::PURPOSE_XBOX_CLOUD_CONSOLE_TRANSFER_TOKEN"
# O gateway (WAF) da Microsoft barra UA de bot: tudo no xboxlive.com finge
# ser o Edge para passar como navegador comum.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0"
)
XBOX_ORIGIN = "https://www.xbox.com"


DEFAULT_TIMEOUT_SEC = 20
MAX_HTTP_RESPONSE_BYTES = 8 * 1024 * 1024


class DeviceCodeAuth(TypedDict):
    user_code: str
    device_code: str
    verification_uri: str
    expires_in: int
    interval: int
    message: str


class DeviceCodeVerify(TypedDict):
    access_token: str
    expires_in: int
    ext_expires_in: int
    id_token: str
    refresh_token: str
    scope: str
    token_type: str


class Region(TypedDict, total=False):
    name: str
    baseUri: str
    networkTestHostname: str
    isDefault: bool
    systemUpdateGroups: Any
    fallbackPriority: int


class StreamingTokenData(TypedDict):
    offeringSettings: Dict[str, Any]
    market: str
    gsToken: str
    tokenType: str
    durationInSeconds: int


@dataclass
class StreamingTokens:
    gs_token: str
    market: str
    regions: List[Region]
    offering: str
    expires_at: datetime


@dataclass
class LoginResult:
    """Tudo que o login entrega: sessão de streaming + LPT do connect + refresh."""

    streaming: StreamingTokens
    lpt: str
    refresh_token: str


def _http_request(
    method: str,
    url: str,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> Tuple[int, bytes, Dict[str, str]]:
    """Faz requisição HTTP com timeout e limite de resposta."""
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_HTTP_RESPONSE_BYTES + 1)
            if len(body) > MAX_HTTP_RESPONSE_BYTES:
                raise Exception(
                    f"Resposta HTTP excedeu {MAX_HTTP_RESPONSE_BYTES} bytes"
                )
            return resp.getcode(), body, dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read(MAX_HTTP_RESPONSE_BYTES + 1)
        if len(body) > MAX_HTTP_RESPONSE_BYTES:
            body = body[:MAX_HTTP_RESPONSE_BYTES]
        return e.code, body, dict(e.headers)
    except urllib.error.URLError as e:
        raise Exception(f"Erro de rede ao acessar {url}: {e}")


def _parse_json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8", errors="replace"))


def _xbox_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Headers de navegador para os endpoints xboxlive.com (foge do WAF)."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "User-Agent": BROWSER_USER_AGENT,
        "Origin": XBOX_ORIGIN,
        "Referer": XBOX_ORIGIN + "/",
        "x-xbl-contract-version": "1",
        "Cache-Control": "no-cache",
    }
    if extra:
        headers.update(extra)
    return headers


def begin_device_flow() -> DeviceCodeAuth:
    """Etapa A: Inicia fluxo Device Code."""
    url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
    params = {
        "client_id": MSAL_CLIENT_ID,
        "scope": DEVICE_CODE_SCOPE,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    code, body, _ = _http_request("POST", url, data=data, headers=headers)
    if code != 200:
        raise Exception(f"Falha ao iniciar device code (HTTP {code}): {body[:200]!r}")

    resp = _parse_json(body)
    logger.debug(
        "Device code obtido: user_code_len=%d, expires_in=%s, interval=%s",
        len(resp.get("user_code", "")),
        resp.get("expires_in"),
        resp.get("interval"),
    )

    return DeviceCodeAuth(
        user_code=str(resp["user_code"]),
        device_code=str(resp["device_code"]),
        verification_uri=str(resp["verification_uri"]),
        expires_in=int(resp.get("expires_in", 900)),
        interval=int(resp.get("interval", 5)),
        message=str(resp.get("message", "")),
    )


def poll_device_flow(
    device_code: str, timeout_sec: int = 900, interval_sec: int = 5
) -> DeviceCodeVerify:
    """Etapa B: Poll para obter tokens do device code."""
    start = datetime.now(timezone.utc)
    interval = max(1, interval_sec)

    while True:
        if (datetime.now(timezone.utc) - start).total_seconds() >= timeout_sec:
            raise Exception("Timeout ao aguardar autorização do device code")

        url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
        params = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": MSAL_CLIENT_ID,
            "device_code": device_code,
        }
        data = urllib.parse.urlencode(params).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        code, body, _ = _http_request("POST", url, data=data, headers=headers)

        if code == 200:
            resp = _parse_json(body)
            logger.debug(
                "Tokens MS obtidos: expires_in=%s, has_refresh=%s",
                resp.get("expires_in"),
                bool(resp.get("refresh_token")),
            )
            return DeviceCodeVerify(
                access_token=str(resp["access_token"]),
                expires_in=int(resp.get("expires_in", 3600)),
                ext_expires_in=int(resp.get("ext_expires_in", resp.get("expires_in", 3600))),
                id_token=str(resp.get("id_token", "")),
                refresh_token=str(resp.get("refresh_token", "")),
                scope=str(resp.get("scope", DEVICE_CODE_SCOPE)),
                token_type=str(resp.get("token_type", "Bearer")),
            )
        elif code == 400:
            err = _parse_json(body)
            error_code = err.get("error", "")
            if error_code == "authorization_pending":
                time.sleep(interval)
                continue
            if error_code == "slow_down":
                interval += 5
                time.sleep(interval)
                continue
            if error_code == "expired_token" or error_code == "invalid_grant":
                raise Exception(f"Token expirado/inválido: {error_code}")
            raise Exception(f"Erro no polling: {error_code} - {err.get('error_description')}")
        else:
            raise Exception(f"Falha inesperada no polling (HTTP {code}): {body[:200]!r}")


def _ms_refresh_tokens(refresh_token: str) -> Tuple[str, str, datetime]:
    """Renova access_token via refresh_token (MSAL). Retorna (access_token, refresh_token, expires_at)."""
    url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
    params = {
        "client_id": MSAL_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": DEVICE_CODE_SCOPE,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-store, must-revalidate, no-cache"}

    code, body, _ = _http_request("POST", url, data=data, headers=headers)
    if code != 200:
        raise Exception(f"Falha ao renovar token (HTTP {code}): {body[:200]!r}")

    resp = _parse_json(body)
    expires_in = int(resp.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    new_refresh = resp.get("refresh_token") or refresh_token
    return str(resp["access_token"]), str(new_refresh), expires_at


def _get_lpt_for_refresh(refresh_token_ms: str) -> Tuple[str, Optional[str], datetime]:
    """Etapa C: LPT via login.live.com a partir do refresh token MSAL."""
    url = "https://login.live.com/oauth20_token.srf"
    params = {
        "client_id": MSAL_CLIENT_ID,
        "scope": LPT_SCOPE,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_ms,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    code, body, _ = _http_request("POST", url, data=data, headers=headers)
    if code != 200:
        raise Exception(f"Falha ao obter LPT (HTTP {code}): {body[:200]!r}")

    resp = _parse_json(body)
    expires_in = int(resp.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return str(resp["access_token"]), resp.get("refresh_token"), expires_at


def _get_xau(access_token_ms: str) -> str:
    """Etapa D: user.auth.xboxlive.com/user/authenticate."""
    url = "https://user.auth.xboxlive.com/user/authenticate"
    payload = {
        "Properties": {
            "AuthMethod": "RPS",
            "RpsTicket": f"d={access_token_ms}",
            "SiteName": "user.auth.xboxlive.com",
        },
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    }
    body = json.dumps(payload).encode("utf-8")
    headers = _xbox_headers()

    code, resp_body, _ = _http_request("POST", url, data=body, headers=headers)
    if code != 200:
        raise Exception(f"Falha ao obter XAU (HTTP {code}): {resp_body[:200]!r}")

    resp = _parse_json(resp_body)
    return str(resp["Token"])


def _get_xsts(user_token: str, relying_party: str) -> Tuple[str, Dict[str, Any]]:
    """Etapa E: xsts.auth.xboxlive.com/xsts/authorize."""
    url = "https://xsts.auth.xboxlive.com/xsts/authorize"
    payload = {
        "Properties": {
            "SandboxId": "RETAIL",
            "UserTokens": [user_token],
        },
        "RelyingParty": relying_party,
        "TokenType": "JWT",
    }
    body = json.dumps(payload).encode("utf-8")
    headers = _xbox_headers()

    code, resp_body, _ = _http_request("POST", url, data=body, headers=headers)
    if code != 200:
        raise Exception(f"Falha ao obter XSTS ({relying_party}) (HTTP {code}): {resp_body[:200]!r}")

    resp = _parse_json(resp_body)
    token = str(resp["Token"])
    return token, resp


def _get_streaming_token(xsts_gssv_token: str, offering: str) -> StreamingTokenData:
    """Etapa F: obtém gsToken para offering."""
    url = f"https://{offering}.gssv-play-prod.xboxlive.com/v2/login/user"
    payload = {
        "token": xsts_gssv_token,
        "offeringId": offering,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = _xbox_headers(
        {
            "Cache-Control": "no-store, must-revalidate, no-cache",
            "x-gssv-client": "XboxComBrowser",
        }
    )

    code, resp_body, _ = _http_request("POST", url, data=body, headers=headers)
    if code != 200:
        raise Exception(f"Falha ao obter streaming token para {offering} (HTTP {code}): {resp_body[:200]!r}")

    return _parse_json(resp_body)  # type: ignore[return-value]


def login_with_browser_tokens(
    access_token: str = "", refresh_token: Optional[str] = None
) -> LoginResult:
    """Login com tokens capturados no browser embutido. Passos C–F.

    O XAU (D) exige o access_token MS; o LPT (C) exige o refresh_token.
    Quando só o refresh foi capturado, renova o access via MSAL antes.
    """
    rt = refresh_token or ""
    if not access_token:
        if not rt:
            raise ValueError("informe access_token ou refresh_token")
        access_token, rt, _ = _ms_refresh_tokens(rt)
    if not rt:
        raise ValueError("refresh_token é necessário para obter o LPT e relogar")
    lpt, _, _ = _get_lpt_for_refresh(rt)
    streaming = _exchange_chain(access_token)
    _store_refresh_token(rt)
    return LoginResult(streaming=streaming, lpt=lpt, refresh_token=rt)


CLOUD_OFFERINGS = ("xgpuweb", "xgpuwebf2p")
ALL_OFFERINGS = ("xgpuweb", "xgpuwebf2p", "xhome")


def _exchange_chain(
    ms_access_token: str, offerings: Sequence[str] = ALL_OFFERINGS
) -> StreamingTokens:
    """Passos D, E, F a partir do access_token Microsoft."""
    xau = _get_xau(ms_access_token)
    xsts_gssv, _ = _get_xsts(xau, "http://gssv.xboxlive.com/")
    last_err = None
    for offering in offerings:
        try:
            st = _get_streaming_token(xsts_gssv, offering)
            regions = st.get("offeringSettings", {}).get("regions", []) or []
            logger.info(
                "xCloud: login GSSV ok (%s, %d regiões)",
                offering,
                len(regions),
            )
            return StreamingTokens(
                gs_token=str(st["gsToken"]),
                market=str(st.get("market", "")),
                regions=[r for r in regions if isinstance(r, dict)],
                offering=offering,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=int(st.get("durationInSeconds", 3600))),
            )
        except Exception as e:
            logger.warning("xCloud: login GSSV falhou (%s): %.200s", offering, e)
            last_err = e
            continue
    if last_err:
        raise last_err
    raise Exception("Falha ao obter tokens de streaming para nenhum offering")


def login_offering(refresh_token: str, offering: str) -> LoginResult:
    """Login direcionado a um offering (ex. fallback xgpuwebf2p).

    Renova os tokens MS, tira LPT novo e roda a cadeia D–F só nesse offering.
    """
    access_ms, rt_new, _ = _ms_refresh_tokens(refresh_token)
    lpt, _, _ = _get_lpt_for_refresh(rt_new)
    streaming = _exchange_chain(access_ms, (offering,))
    _store_refresh_token(rt_new)
    return LoginResult(streaming=streaming, lpt=lpt, refresh_token=rt_new)


def complete_device_flow(device: DeviceCodeAuth) -> LoginResult:
    """Completa o fluxo A–F a partir do device code. Retorna LoginResult."""
    verify = poll_device_flow(device["device_code"], interval_sec=device.get("interval", 5))
    access_ms = verify["access_token"]
    refresh_ms = verify["refresh_token"]
    if not refresh_ms:
        raise Exception("device flow não devolveu refresh_token (scope offline_access?)")

    lpt, _, _ = _get_lpt_for_refresh(refresh_ms)
    streaming = _exchange_chain(access_ms)
    _store_refresh_token(refresh_ms)
    return LoginResult(streaming=streaming, lpt=lpt, refresh_token=refresh_ms)


def refresh_streaming_tokens() -> LoginResult:
    """Atualiza tokens via refresh_token armazenado. Repete C–F sem interação."""
    rt = _load_refresh_token()
    if not rt:
        raise Exception("Nenhum refresh_token armazenado. Faça login via device code primeiro.")

    access_ms_new, rt_new, _ = _ms_refresh_tokens(rt)
    lpt, _, _ = _get_lpt_for_refresh(rt_new)
    streaming = _exchange_chain(access_ms_new)
    _store_refresh_token(rt_new)
    return LoginResult(streaming=streaming, lpt=lpt, refresh_token=rt_new)


def _get_secret_store():
    """Retorna helper de storage de segredos (Secret Service)."""
    try:
        import gi

        try:
            gi.require_version("Secret", "1")
        except Exception:
            pass
        from gi.repository import Secret
    except Exception as e:
        logger.debug("Secret Service não disponível: %s", e)
        return None, None

    schema = Secret.Schema.new(
        "io.github.zonaro.Jolven.xcloud",
        Secret.SchemaFlags.NONE,
        {"app": Secret.SchemaAttributeType.STRING},
    )
    attrs = {"app": "jolven"}
    return Secret, {"schema": schema, "attrs": attrs, "label": "Jolven: xCloud tokens"}


def _store_refresh_token(refresh_token: str) -> None:
    if not refresh_token:
        return
    Secret, cfg = _get_secret_store()
    if not Secret or not cfg:
        logger.debug("Sem Secret Service; pulando armazenamento de refresh_token")
        return
    try:
        Secret.password_store_sync(
            cfg["schema"],
            cfg["attrs"],
            Secret.COLLECTION_DEFAULT,
            cfg["label"],
            refresh_token,
            None,
        )
        logger.debug("refresh_token armazenado no Secret Service")
    except Exception as e:
        logger.warning("Não foi possível armazenar refresh_token: %s", e)


def _load_refresh_token() -> Optional[str]:
    Secret, cfg = _get_secret_store()
    if not Secret or not cfg:
        return None
    try:
        tok = Secret.password_lookup_sync(cfg["schema"], cfg["attrs"], None)
        return tok
    except Exception as e:
        logger.warning("Não foi possível ler refresh_token: %s", e)
        return None


def _clear_refresh_token() -> None:
    Secret, cfg = _get_secret_store()
    if not Secret or not cfg:
        return
    try:
        Secret.password_clear_sync(cfg["schema"], cfg["attrs"], None)
    except Exception as e:
        logger.debug("Não foi possível limpar refresh_token: %s", e)
