# session.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Handshake REST de sessões de streaming xCloud + xHome (somente signalling).

Fase 2 do player nativo: este módulo cuida do signalling — catálogo de títulos,
abertura de sessão, polling de estado, `/connect` com o LPT, `/configuration`,
troca de SDP, troca de candidatos ICE, keepalive e encerramento. A mídia WebRTC
(PeerConnection, codecs, datachannels) fica para a fase seguinte: o SDP offer é
montado pelo chamador e entregue pronto em `exchange_sdp`.

Formas de endpoint foram alinhadas ao Greenlight atual
(`packages/player/src/server` e `packages/desktop/main/helpers/xcloudapi.ts`) e
conferidas também em `OpenXbox/xcloud-python` e `OpenXbox/xcloud-rs`. Duas
divergências deliberadas em relação ao xcloud-python, cujos valores estão
obsoletos:

- offeringId `xgpubeta` não é usado aqui; os offerings atuais (`xgpuweb`,
  `xgpuwebf2p`, `xhome`) já são escolhidos em `auth.py`.
- O POST de ICE usa `{"candidates": ["..."]}` — um array JSON de strings. O
  schema antigo de dicionário indexado por linha de mídia não é aceito.

Este módulo não importa GTK e nunca registra tokens, LPT ou chave SRTP em log:
`ServerDetails.srtp_key` fica fora do `repr` e o logger imprime apenas
sessionId, estado, plataforma e região.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import auth
from .auth import DEFAULT_TIMEOUT_SEC, Region, StreamingTokens

# `_http_request` e `_parse_json` são privados em auth.py. Importados em vez de
# duplicados para existir uma única implementação de urllib no pacote; o
# reexport no namespace deste módulo é também o ponto de mock dos testes.
_http_request = auth._http_request
_parse_json = auth._parse_json

logger = logging.getLogger(__name__)

# Plataforma: segmento usado em /v5/sessions/<platforma>/play e em
# `platform_for_offering`.
PLATFORM_CLOUD = "cloud"
PLATFORM_HOME = "home"

OFFERING_HOME = "xhome"

# `V3;WebrtcTransport.dll` é o nanoVersion aceito atualmente pelo GSSV.
NANO_VERSION = "V3;WebrtcTransport.dll"

STATE_POLL_INTERVAL_SEC = 1.0
PROVISION_TIMEOUT_SEC = 180.0
EXCHANGE_POLL_INTERVAL_SEC = 0.5
EXCHANGE_TIMEOUT_SEC = 30.0

DEFAULT_TITLES_MAX = 25

# Estados em que a sessão já serve para a troca de mídia.
READY_STATES = frozenset({"Provisioned", "ReadyToConnect"})
# Estados terminais de erro; qualquer outro estado é tratado como provisório.
FAILED_STATES = frozenset({"Failed", "Error"})

# Cabeçalhos aceitos como sucesso por operação (o GSSV alterna 200/202).
_ACCEPT_POST = (200, 202)
_ACCEPT_GET = (200, 204)
_ACCEPT_DELETE = (200, 202, 204)

# Dispositivo reportado em X-MS-Device-Info. Mantido igual ao cliente web
# oficial porque `os.name` precisa ser "windows" para o /play; não forjamos
# vendor, e o app se identifica como browser em www.xbox.com.
_DEVICE_INFO = json.dumps(
    {
        "appInfo": {
            "env": {
                "clientAppId": "www.xbox.com",
                "clientAppType": "browser",
                "clientAppVersion": "21.1.98",
                "clientSdkVersion": "8.5.3",
                "httpEnvironment": "prod",
                "sdkInstallId": "",
            }
        },
        "dev": {
            "hw": {"make": "unknown", "model": "unknown", "sdkType": "web"},
            "os": {"name": "windows", "ver": "22631.2715", "platform": "desktop"},
        },
    }
)

SleepFn = Callable[[float], None]


# Erros


class SessionError(Exception):
    """Base dos erros de signalling do xCloud/xHome."""


class SessionHttpError(SessionError):
    """O GSSV respondeu com um status HTTP inesperado."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class SessionStateError(SessionError):
    """A sessão entrou em estado de falha (Failed) com errorDetails."""

    def __init__(self, state: str, detail: str = "") -> None:
        super().__init__(
            f"sessão falhou no estado {state!r}"
            + (f" ({detail})" if detail else "")
        )
        self.state = state
        self.detail = detail


class SessionTimeoutError(SessionError):
    """Um estado ou resposta demorou mais que o timeout permitido."""


class TitleNotInOfferingError(SessionHttpError):
    """O offering não contém o título (tente outro offering, ex. xgpuwebf2p)."""


# Modelos


@dataclass(frozen=True)
class Title:
    title_id: str
    product_id: str = ""
    xbox_title_id: Optional[int] = None
    has_entitlement: bool = True
    blocked_by_family_safety: bool = False


@dataclass(frozen=True)
class TitlesPage:
    titles: Tuple[Title, ...] = ()
    total_items: int = 0
    continuation_token: str = ""


@dataclass(frozen=True)
class WaitTime:
    provisioning_seconds: int = 0
    allocation_seconds: int = 0
    total_seconds: int = 0


@dataclass(frozen=True)
class SessionStart:
    session_id: str
    session_path: str
    state: str = ""


@dataclass(frozen=True)
class ServerDetails:
    ip_address: str = ""
    port: int = 0
    # Chave SRTP: fica só em memória e nunca aparece em repr/log.
    srtp_key: str = field(default="", repr=False)
    ice_exchange_path: str = ""
    stun_server_address: str = ""
    ipv4_address: str = ""
    ipv4_port: int = 0
    ipv6_address: str = ""
    ipv6_port: int = 0


@dataclass(frozen=True)
class SessionConfiguration:
    keepalive_pulse_in_seconds: int = 0
    server_details: ServerDetails = field(default_factory=ServerDetails)


@dataclass(frozen=True)
class SdpAnswer:
    sdp: str
    message_type: str = ""
    status: str = ""
    chat: int = 0
    control: int = 0
    input_version: int = 0
    message: int = 0


@dataclass(frozen=True)
class IceCandidate:
    """Candidato ICE remoto com a linha de mídia preservada."""

    candidate: str
    sdp_mid: str = "0"
    sdp_mline_index: int = 0


@dataclass(frozen=True)
class Keepalive:
    alive_seconds: int = 0
    reason: str = ""


@dataclass(frozen=True)
class PreparedSession:
    """Sessão provisionada e conectada, pronta para a fase de mídia."""

    base: str
    platform: str
    session_id: str
    session_path: str
    state: str
    configuration: SessionConfiguration


# Helpers internos


def _join(base: str, path: str) -> str:
    """Une base e caminho sem depender do quirks de urljoin."""
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _validate_service_base(base: str) -> str:
    """Aceita somente endpoints HTTPS oficiais do serviço Xbox Live."""
    parsed = urllib.parse.urlsplit(base)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname.endswith(".xboxlive.com")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SessionError("região devolveu um endpoint de streaming inválido")
    return base.rstrip("/")


def _session_url(base: str, session_path: str, suffix: str = "") -> str:
    """URL de um recurso da sessão, com ou sem barra inicial no sessionPath."""
    path = session_path.strip("/")
    if suffix:
        path = f"{path}/{suffix.lstrip('/')}"
    return _join(base, path)


def _headers(gs_token: str, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": auth.BROWSER_USER_AGENT,
        "Origin": auth.XBOX_ORIGIN,
        "Referer": auth.XBOX_ORIGIN + "/play",
        "X-Gssv-Client": "XboxComBrowser",
        "X-MS-Device-Info": _DEVICE_INFO,
    }
    if gs_token:
        headers["Authorization"] = f"Bearer {gs_token}"
    if extra:
        headers.update(extra)
    return headers


def _call(
    method: str,
    url: str,
    gs_token: str = "",
    payload: Optional[Any] = None,
    accept: Sequence[int] = _ACCEPT_GET,
    what: str = "",
    timeout: int = DEFAULT_TIMEOUT_SEC,
    empty_body: bool = False,
) -> Tuple[int, bytes]:
    """Executa uma chamada e valida o status, sem vazar token em log."""
    if payload is not None:
        data: Optional[bytes] = json.dumps(payload).encode("utf-8")
    elif empty_body:
        data = b""
    else:
        data = None

    code, body, _ = _http_request(
        method, url, data=data, headers=_headers(gs_token), timeout=timeout
    )
    if code not in accept:
        raise SessionHttpError(
            code, f"{what or method} respondeu HTTP {code}: {body[:200]!r}"
        )
    return code, body


def _decode(body: bytes, what: str) -> Any:
    if not body.strip():
        return {}
    try:
        return _parse_json(body)
    except ValueError as exc:
        raise SessionError(f"resposta de {what} não é JSON válido: {exc}") from exc


def _local_timezone_offset_minutes() -> int:
    """Deslocamento local em minutos (UTC-3 => -180)."""
    seconds = -(time.altzone if time.daylight else time.timezone)
    return int(round(seconds / 60))


def _detail_text(details: Any) -> str:
    """Formata errorDetails sem despejar o corpo inteiro."""
    if isinstance(details, str):
        return details[:200]
    if not isinstance(details, dict):
        return ""
    code = details.get("code")
    message = details.get("message")
    if code and message:
        return f"code={code} message={message}"
    return str(code or message or "")[:200]


def _exchange_payload(payload: Any) -> Any:
    """Desembrulha `exchangeResponse`, que o GSSV devolve como string JSON.

    O conteúdo é um objeto no /sdp e um array no /ice, então o resultado fica
    sem tipo definido; quem chama normaliza.
    """
    if not isinstance(payload, dict) or "exchangeResponse" not in payload:
        return payload
    inner = payload["exchangeResponse"]
    if isinstance(inner, str):
        try:
            return json.loads(inner)
        except ValueError as exc:
            raise SessionError(f"exchangeResponse não é JSON válido: {exc}") from exc
    return inner


def _unwrap_exchange(payload: Any) -> Dict[str, Any]:
    """Normaliza as duas formas da resposta: plana ou embrulhada em exchangeResponse."""
    data = _exchange_payload(payload)
    if not isinstance(data, dict):
        return {}
    if "exchangeResponse" in payload:
        data = dict(data)
        data.setdefault("errorDetails", payload.get("errorDetails"))
    return data


# Regiões e plataformas


def platform_for_offering(offering: str) -> str:
    """Mapa offeringId -> segmento de /play. Só `xhome` usa `home`."""
    return PLATFORM_HOME if offering.strip().lower() == OFFERING_HOME else PLATFORM_CLOUD


def resolve_title_id(
    base: str,
    gs_token: str,
    product_id: str,
    max_pages: int = 40,
) -> str:
    """Mapeia Store product_id -> titleId da nuvem via /v2/titles.

    O catálogo guarda IDs da loja (ex. 9N7TFFRRZCC9); o /play exige o
    titleId do offering (ex. GENSHINIMPACT), ligado via details.productId.
    Devolve "" quando não há correspondência.
    """
    wanted = (product_id or "").strip().upper()
    if not wanted:
        return ""
    continuation = ""
    for _ in range(max(1, max_pages)):
        page = list_titles(base, gs_token, max=100, continuation_token=continuation)
        for title in page.titles:
            if title.title_id.upper() == wanted:
                return title.title_id
            if (title.product_id or "").upper() == wanted:
                return title.title_id
        if not page.continuation_token:
            break
        continuation = page.continuation_token
    return ""


def resolve_base_uri(streaming: StreamingTokens, region_name: str = "") -> str:
    """Escolhe o baseUri da região: nome explícito > isDefault > fallbackPriority.

    `fallbackPriority` é o mecanismo de fallback do próprio login: quando o
    servidor não marca nenhuma região como default, a de menor prioridade é a
    primeira tentativa.
    """
    regions: List[Region] = [
        region for region in streaming.regions if isinstance(region, dict) and region.get("baseUri")
    ]
    if not regions:
        raise SessionError(
            "LoginResult sem regiões com baseUri; refaça o login ou troque de offering"
        )

    if region_name:
        wanted = region_name.strip().lower()
        for region in regions:
            if str(region.get("name", "")).lower() == wanted:
                    return _validate_service_base(str(region["baseUri"]))
        available = ", ".join(sorted(str(r.get("name", "")) for r in regions))
        raise SessionError(f"região {region_name!r} indisponível neste login ({available})")

    for region in regions:
        if region.get("isDefault"):
            return _validate_service_base(str(region["baseUri"]))

    best = min(
        enumerate(regions),
        key=lambda item: (item[1].get("fallbackPriority", 1 << 30), item[0]),
    )[1]
    return _validate_service_base(str(best["baseUri"]))


def platform_for_login(streaming: StreamingTokens, platform: str = "") -> str:
    """Plataforma efetiva: a explícita ou a derivada do offering do login."""
    return platform or platform_for_offering(streaming.offering)


# Catálogo


def _parse_titles(payload: Any) -> TitlesPage:
    if not isinstance(payload, dict):
        return TitlesPage()

    titles: List[Title] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        raw_details = item.get("details")
        details = raw_details if isinstance(raw_details, dict) else {}
        titles.append(
            Title(
                title_id=str(item.get("titleId") or ""),
                product_id=str(details.get("productId") or ""),
                xbox_title_id=details.get("xboxTitleId"),
                has_entitlement=bool(details.get("hasEntitlement", True)),
                blocked_by_family_safety=bool(details.get("blockedByFamilySafety", False)),
            )
        )

    return TitlesPage(
        titles=tuple(titles),
        total_items=int(payload.get("totalItems") or 0),
        continuation_token=str(payload.get("continuationToken") or ""),
    )


def _get_titles(
    base: str, gs_token: str, path: str, count: int, continuation_token: str
) -> TitlesPage:
    params = {"mr": str(count)}
    if continuation_token:
        params["ct"] = continuation_token
    url = f"{_join(base, path)}?{urllib.parse.urlencode(params)}"
    _, body = _call("GET", url, gs_token, what=f"GET {path}")
    return _parse_titles(_decode(body, path))


def list_titles(
    base: str, gs_token: str, max: int = DEFAULT_TITLES_MAX, continuation_token: str = ""
) -> TitlesPage:
    """GET /v2/titles — catálogo disponível para a conta."""
    return _get_titles(
        base, gs_token, "v2/titles", max if max > 0 else 1, continuation_token
    )


def list_mru(
    base: str, gs_token: str, max: int = DEFAULT_TITLES_MAX, continuation_token: str = ""
) -> TitlesPage:
    """GET /v2/titles/mru — títulos mais recentes primeiro."""
    return _get_titles(
        base, gs_token, "v2/titles/mru", max if max > 0 else 1, continuation_token
    )


def waittime(base: str, gs_token: str, title_id: str) -> WaitTime:
    """GET /v1/waittime/{titleId} — estimativa antes de abrir a sessão."""
    quoted = urllib.parse.quote(title_id, safe="")
    _, body = _call("GET", _join(base, f"v1/waittime/{quoted}"), gs_token, what="GET waittime")
    payload = _decode(body, "waittime")
    return WaitTime(
        provisioning_seconds=int(payload.get("estimatedProvisioningTimeInSeconds") or 0),
        allocation_seconds=int(payload.get("estimatedAllocationTimeInSeconds") or 0),
        total_seconds=int(payload.get("estimatedTotalWaitTimeInSeconds") or 0),
    )


# Ciclo de vida da sessão


def start_session(
    base: str,
    gs_token: str,
    title_id: Optional[str] = None,
    server_id: Optional[str] = None,
    platform: str = "",
    locale: str = "pt-BR",
    timezone_offset_minutes: Optional[int] = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> SessionStart:
    """POST /v5/sessions/{cloud|home}/play e devolve sessionId + sessionPath.

    xCloud exige `title_id`; xHome exige `server_id` (o console). Sem `platform`
    explícito ela é derivada do que foi informado.
    """
    resolved = PLATFORM_HOME if server_id else PLATFORM_CLOUD
    if platform:
        resolved = platform
    if resolved == PLATFORM_CLOUD and not title_id:
        raise ValueError("xCloud exige title_id")
    if resolved == PLATFORM_HOME and not server_id:
        raise ValueError("xHome exige server_id (console LiveId)")

    payload = {
        "titleId": title_id or "",
        "systemUpdateGroup": "",
        "serverId": server_id or "",
        "fallbackRegionNames": [],
        "settings": {
            "nanoVersion": NANO_VERSION,
            "enableOptionalDataCollection": False,
            "enableTextToSpeech": False,
            "highContrast": 0,
            "locale": locale,
            "sdkType": "web",
            "osName": "windows",
            "useIceConnection": False,
            "timezoneOffsetMinutes": (
                _local_timezone_offset_minutes()
                if timezone_offset_minutes is None
                else int(timezone_offset_minutes)
            ),
        },
    }

    url = _join(base, f"v5/sessions/{resolved}/play")
    try:
        _, body = _call("POST", url, gs_token, payload=payload, accept=_ACCEPT_POST, what="POST play")
    except SessionHttpError as error:
        if "OfferingDoesNotContainTitle" in str(error):
            raise TitleNotInOfferingError(
                error.status, str(error)
            ) from error
        raise
    decoded = _decode(body, "play")
    session_path = str(decoded.get("sessionPath") or "")
    if not session_path:
        raise SessionError("POST play não devolveu sessionPath")

    started = SessionStart(
        session_id=str(decoded.get("sessionId") or ""),
        session_path=session_path,
        state=str(decoded.get("state") or ""),
    )
    logger.info(
        "Sessão aberta: session_id=%s platform=%s state=%s",
        started.session_id,
        resolved,
        started.state,
    )
    return started


def poll_state(
    base: str,
    gs_token: str,
    session_path: str,
    timeout: float = PROVISION_TIMEOUT_SEC,
    interval: float = STATE_POLL_INTERVAL_SEC,
    sleep: SleepFn = time.sleep,
    on_ready_to_connect: Optional[Callable[[str], None]] = None,
    until: str = "",
) -> str:
    """GET {sessionPath}/state a cada `interval` até um estado pronto.

    Por padrão encerra em `READY_STATES` (Provisioned ou ReadyToConnect); com
    `until` informado, só aquele estado encerra o loop — é o que a fase de mídia
    quer, porque a sessão precisa estar Provisioned para a troca de SDP.

    Estado terminal de erro (`Failed` ou errorDetails) levanta
    `SessionStateError`; esgotar o timeout levanta `SessionTimeoutError`. O
    callback `on_ready_to_connect` roda uma única vez, na primeira leitura de
    ReadyToConnect, que é onde o handshake oficial envia o `/connect`.
    """
    url = _session_url(base, session_path, "state")
    deadline = time.monotonic() + max(0.0, timeout)
    success_states = {until} if until else READY_STATES
    state = ""
    announced = False

    while True:
        _, body = _call("GET", url, gs_token, what="GET state")
        payload = _decode(body, "state")
        state = str(payload.get("state") or "")

        if state == "ReadyToConnect" and not announced and on_ready_to_connect is not None:
            announced = True
            on_ready_to_connect(state)

        if state in success_states:
            logger.info("Sessão no estado %s", state)
            return state
        if state in FAILED_STATES or payload.get("errorDetails"):
            raise SessionStateError(state, _detail_text(payload.get("errorDetails")))

        if time.monotonic() >= deadline:
            raise SessionTimeoutError(
                f"timeout esperando Provisioned (último estado {state or 'desconhecido'!r}, "
                f"esperado em {timeout:g}s)"
            )
        sleep(interval)


def connect(base: str, lpt: str, session_path: str, gs_token: str) -> None:
    """POST {sessionPath}/connect com o LPT como `userToken`.

    Dois tokens distintos: o LPT (`LoginResult.lpt`) é o corpo da requisição; o
    gsToken é apenas o Bearer do cabeçalho. Mandar o gsToken aqui reprova o
    handshake.
    """
    if not lpt:
        raise ValueError("lpt é obrigatório para /connect")
    if not gs_token:
        raise ValueError("gs_token é obrigatório para autenticar /connect")

    _, _ = _call(
        "POST",
        _session_url(base, session_path, "connect"),
        gs_token,
        payload={"userToken": lpt},
        accept=_ACCEPT_POST,
        what="POST connect",
    )
    logger.info("Sessão conectada (LPT aceito)")


def get_configuration(
    base: str, gs_token: str, session_path: str, timeout: int = DEFAULT_TIMEOUT_SEC
) -> SessionConfiguration:
    """GET {sessionPath}/configuration — keepalive, endereço do servidor e chave SRTP."""
    _, body = _call(
        "GET",
        _session_url(base, session_path, "configuration"),
        gs_token,
        what="GET configuration",
        timeout=timeout,
    )
    payload = _decode(body, "configuration")
    raw_details = payload.get("serverDetails")
    details = raw_details if isinstance(raw_details, dict) else {}
    srtp = details.get("srtp")
    srtp = srtp if isinstance(srtp, dict) else {}

    configuration = SessionConfiguration(
        keepalive_pulse_in_seconds=int(payload.get("keepAlivePulseInSeconds") or 0),
        server_details=ServerDetails(
            ip_address=str(details.get("ipAddress") or ""),
            port=int(details.get("port") or 0),
            srtp_key=str(srtp.get("key") or ""),
            ice_exchange_path=str(details.get("iceExchangePath") or ""),
            stun_server_address=str(details.get("stunServerAddress") or ""),
            ipv4_address=str(details.get("ipV4Address") or ""),
            ipv4_port=int(details.get("ipV4Port") or 0),
            ipv6_address=str(details.get("ipV6Address") or ""),
            ipv6_port=int(details.get("ipV6Port") or 0),
        ),
    )
    logger.info(
        "Configuration recebida: keepalive=%ss ip=%s port=%s stun=%s ice_exchange=%s srtp_key_len=%d",
        configuration.keepalive_pulse_in_seconds,
        configuration.server_details.ip_address,
        configuration.server_details.port,
        bool(configuration.server_details.stun_server_address),
        bool(configuration.server_details.ice_exchange_path),
        len(configuration.server_details.srtp_key),
    )
    return configuration


# Sinalização: SDP e ICE


def default_sdp_configuration() -> Dict[str, Any]:
    """Bloco `configuration` do offer, no formato do cliente web oficial.

    `input` 9 habilita os canais `reliableinput`/`unreliableinput` de gamepad.
    """
    return {
        "chatConfiguration": {
            "bytesPerSample": 2,
            "expectedClipDurationMs": 20,
            "format": {"codec": "opus", "container": "webm"},
            "numChannels": 1,
            "sampleFrequencyHz": 24000,
        },
        "chat": {"minVersion": 1, "maxVersion": 1},
        "control": {"minVersion": 1, "maxVersion": 3},
        "input": {"minVersion": 1, "maxVersion": 9},
        "message": {"minVersion": 1, "maxVersion": 1},
        "reliableinput": {"minVersion": 9, "maxVersion": 9},
        "unreliableinput": {"minVersion": 9, "maxVersion": 9},
    }


def _parse_sdp_answer(payload: Any) -> SdpAnswer:
    data = _unwrap_exchange(payload)
    status = str(data.get("status") or "")
    if status and status != "success":
        raise SessionError(
            f"SDP recusado (status={status}) "
            f"debugInfo={str(data.get('debugInfo') or data.get('errorDetails') or '')[:200]}"
        )

    sdp = str(data.get("sdp") or "")
    if not sdp:
        raise SessionError(
            f"resposta de SDP sem offer/answer {_detail_text(data.get('errorDetails'))}".strip()
        )

    return SdpAnswer(
        sdp=sdp,
        message_type=str(data.get("messageType") or ""),
        status=status,
        chat=int(data.get("chat") or 0),
        control=int(data.get("control") or 0),
        input_version=int(data.get("input") or 0),
        message=int(data.get("message") or 0),
    )


def exchange_sdp(
    base: str,
    gs_token: str,
    session_path: str,
    offer_sdp: str,
    configuration_block: Optional[Mapping[str, Any]] = None,
    request_id: Any = 1,
    timeout: float = EXCHANGE_TIMEOUT_SEC,
    interval: float = EXCHANGE_POLL_INTERVAL_SEC,
    sleep: SleepFn = time.sleep,
) -> SdpAnswer:
    """POST do offer em {sessionPath}/sdp e GET/poll até a resposta não-204.

    O SDP é montado pelo chamador (fase de mídia); aqui só o transporte REST.
    Enquanto o servidor ainda não respondeu, o GET devolve 204.
    """
    if not offer_sdp:
        raise ValueError("offer_sdp é obrigatório")

    url = _session_url(base, session_path, "sdp")
    payload = {
        "messageType": "offer",
        "sdp": offer_sdp,
        "requestId": request_id,
        "configuration": dict(configuration_block or default_sdp_configuration()),
    }
    _call("POST", url, gs_token, payload=payload, accept=_ACCEPT_POST, what="POST sdp")

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        code, body = _call("GET", url, gs_token, what="GET sdp")
        if code != 204:
            answer = _parse_sdp_answer(_decode(body, "sdp"))
            logger.info("SDP answer recebido (%d bytes)", len(answer.sdp))
            return answer
        if time.monotonic() >= deadline:
            raise SessionTimeoutError(f"timeout esperando SDP answer (esperado em {timeout:g}s)")
        sleep(interval)


def _parse_ice_candidates(payload: Any) -> List[IceCandidate]:
    raw = _exchange_payload(payload)
    if isinstance(raw, dict):
        raw = raw.get("candidates")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    candidates: List[IceCandidate] = []
    for item in raw:
        if isinstance(item, str):
            # O endpoint usa um array de strings JSON. Aceitamos também uma
            # string ICE crua por compatibilidade com respostas antigas.
            try:
                decoded = json.loads(item)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, dict):
                item = decoded
            elif item:
                candidates.append(IceCandidate(candidate=item))
                continue
        if isinstance(item, dict):
            value = item.get("candidate")
            if not isinstance(value, str) or not value:
                continue
            try:
                mline = int(item.get("sdpMLineIndex") or 0)
            except (TypeError, ValueError):
                mline = 0
            candidates.append(
                IceCandidate(
                    candidate=value,
                    sdp_mid=str(item.get("sdpMid") or "0"),
                    sdp_mline_index=mline,
                )
            )
    return candidates


def exchange_ice(
    base: str,
    gs_token: str,
    ice_path: str,
    candidates: Sequence[str],
    timeout: float = EXCHANGE_TIMEOUT_SEC,
    interval: float = EXCHANGE_POLL_INTERVAL_SEC,
    sleep: SleepFn = time.sleep,
) -> List[IceCandidate]:
    """POST dos candidatos locais em `ice_path` e GET/poll pelos remotos.

    `candidates` é uma lista de strings JSON contendo candidate, sdpMid e
    sdpMLineIndex, no mesmo formato enviado pelo Greenlight. Aceita tanto
    `{sessionPath}/ice` quanto o `serverDetails.iceExchangePath` do xHome.
    """
    if not ice_path:
        raise ValueError("ice_path é obrigatório (use ice_exchange_path())")

    url = _join(base, ice_path)
    payload = {"candidates": [str(candidate) for candidate in candidates]}
    _call("POST", url, gs_token, payload=payload, accept=_ACCEPT_POST, what="POST ice")

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        code, body = _call("GET", url, gs_token, what="GET ice")
        if code != 204:
            remote = _parse_ice_candidates(_decode(body, "ice"))
            logger.info("Recebidos %d candidatos ICE remotos", len(remote))
            return remote
        if time.monotonic() >= deadline:
            raise SessionTimeoutError(
                f"timeout esperando candidatos ICE remotos (esperado em {timeout:g}s)"
            )
        sleep(interval)


def keepalive(base: str, gs_token: str, session_path: str) -> Keepalive:
    """POST {sessionPath}/keepalive com corpo vazio e devolve o prazo acordado."""
    _, body = _call(
        "POST",
        _session_url(base, session_path, "keepalive"),
        gs_token,
        accept=_ACCEPT_POST,
        what="POST keepalive",
        empty_body=True,
    )
    payload = _decode(body, "keepalive")
    pulse = Keepalive(
        alive_seconds=int(payload.get("aliveSeconds") or 0),
        reason=str(payload.get("reason") or ""),
    )
    logger.info("Keepalive: alive_seconds=%s reason=%s", pulse.alive_seconds, pulse.reason)
    return pulse


def stop(base: str, gs_token: str, session_path: str) -> bool:
    """DELETE {sessionPath} — encerra a sessão no servidor."""
    code, _ = _call(
        "DELETE",
        _session_url(base, session_path),
        gs_token,
        accept=_ACCEPT_DELETE,
        what="DELETE sessão",
    )
    logger.info("Sessão encerrada (HTTP %s)", code)
    return code in _ACCEPT_DELETE


# Orquestração


def ice_exchange_path(session: PreparedSession) -> str:
    """Caminho de ICE da sessão: o do servidor quando presente, senão {sessionPath}/ice."""
    advertised = session.configuration.server_details.ice_exchange_path
    if advertised:
        return advertised
    return f"{session.session_path.rstrip('/')}/ice"


def open_session(
    base: str,
    gs_token: str,
    lpt: str,
    title_id: Optional[str] = None,
    server_id: Optional[str] = None,
    platform: str = "",
    locale: str = "pt-BR",
    timeout: float = PROVISION_TIMEOUT_SEC,
    interval: float = STATE_POLL_INTERVAL_SEC,
    sleep: SleepFn = time.sleep,
) -> PreparedSession:
    """Handshake completo até a mídia: play -> state -> connect -> configuration.

    Conecta uma única vez, na primeira transição para ReadyToConnect, e só busca
    a configuração depois de `Provisioned` — mesma ordem do cliente oficial.
    """
    resolved = platform or (PLATFORM_HOME if server_id else PLATFORM_CLOUD)
    started = start_session(
        base,
        gs_token,
        title_id=title_id,
        server_id=server_id,
        platform=resolved,
        locale=locale,
    )

    connected = False

    def _connect_once(_state: str) -> None:
        nonlocal connected
        if connected:
            return
        connected = True
        connect(base, lpt, started.session_path, gs_token)

    state = poll_state(
        base,
        gs_token,
        started.session_path,
        timeout=timeout,
        interval=interval,
        sleep=sleep,
        on_ready_to_connect=_connect_once,
        until="Provisioned",
    )

    configuration = get_configuration(base, gs_token, started.session_path)
    return PreparedSession(
        base=base,
        platform=resolved,
        session_id=started.session_id,
        session_path=started.session_path,
        state=state,
        configuration=configuration,
    )


def open_session_for_login(
    login: auth.LoginResult,
    title_id: Optional[str] = None,
    server_id: Optional[str] = None,
    region_name: str = "",
    platform: str = "",
    timeout: float = PROVISION_TIMEOUT_SEC,
    interval: float = STATE_POLL_INTERVAL_SEC,
    sleep: SleepFn = time.sleep,
) -> PreparedSession:
    """Atalho de `open_session` que resolve região e plataforma a partir do login.

    Usa `LoginResult.streaming` (gsToken + regiões), `LoginResult.lpt` no
    `/connect` e `LoginResult.streaming.offering` para escolher `cloud`/`home`.
    """
    streaming = login.streaming
    return open_session(
        resolve_base_uri(streaming, region_name=region_name),
        streaming.gs_token,
        login.lpt,
        title_id=title_id,
        server_id=server_id,
        platform=platform_for_login(streaming, platform),
        locale=streaming.market or "pt-BR",
        timeout=timeout,
        interval=interval,
        sleep=sleep,
    )
