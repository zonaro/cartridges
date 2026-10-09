# xcloud_player.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Player nativo do Xbox Cloud Gaming para a interface do Jolven.

Este módulo orquestra a fase de mídia do xCloud: sessão provisionada em
``session.py`` + WebRTC/GStreamer de ``player.py`` + codificação de input de
``input.py``. O trabalho pesado roda em uma thread daemon própria
(:class:`XCloudPlayerController`), nunca na thread GTK; resultados visuais
chegam por ``entregar_na_tela``.

Contrato de uso (tudo no worker, nesta ordem):

1. ``refresh_streaming_tokens`` + ``open_session_for_login`` (Provisioned);
2. ``player.start()`` cria os canais in-band antes da negociação e sobe o
   pipeline para PLAYING;
3. ``create_offer`` -> ``exchange_sdp`` -> ``set_remote_answer``;
4. drena candidatos locais -> ``exchange_ice`` -> ``add_remote_ice`` mantendo
   o índice da linha de mídia;
5. aguarda os canais abrirem e o ``HandshakeAck`` de ``messageV1``;
6. envia configuração, ``first_packet``, ``authorizationRequest`` e
   ``gamepadChanged``;
7. loop de input a 60 Hz + keepalive no pulso da configuração;
8. ``stop`` encerra a sessão no servidor e fecha o pipeline GStreamer.

Todos os sleeps são abortáveis pelo ``stop_event``, para o ``stop()`` devolver
na hora da UI mesmo no meio de uma espera de polling.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from cartridges.xcloud_native import auth, input, session

logger = logging.getLogger(__name__)

INPUT_RATE_HZ = 60
ICE_COLLECT_TIMEOUT_SEC = 15.0
CHANNEL_HANDSHAKE_TIMEOUT_SEC = 15.0
DEFAULT_SERVER_WIDTH = 1920
DEFAULT_SERVER_HEIGHT = 1080
REQUIRED_DATA_CHANNELS = frozenset({"chat", "control", "input", "message"})
ACCESS_KEY = "4BDB3609-C1F1-4195-9B37-FEFF45DA8B8E"

SleepFn = Callable[[float], None]


class XCloudPlayerError(Exception):
    """Erro genérico de orquestração do player nativo."""


def webrtc_available() -> Tuple[bool, str]:
    """(ok, motivo) do suporte WebRTC/GStreamer, sem levantar exceção."""
    player = _load_player_module()
    if player is None:
        return False, "módulo nativo de player indisponível"
    try:
        return player.webrtc_supported()
    except Exception as error:  # pylint: disable=broad-exception-caught
        return False, f"sondagem do GStreamer falhou: {error}"


def _load_player_module() -> Optional[Any]:
    """Importa o player nativo de forma preguiçosa (depende de GStreamer)."""
    try:
        import cartridges.xcloud_native.player as player  # noqa: PLC0415
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.debug("xCloud: player nativo indisponível: %s", error)
        return None
    return player


class XCloudPlayerController:
    """Controlador do player nativo do xCloud em thread daemon própria.

    A UI envia input pelo ``feed_*`` (seguros para chamar de qualquer thread)
    e recebe ``on_ready(self)`` ou ``on_error(self, message)`` na thread GTK
    quando o streaming começa ou falha.
    """

    def __init__(
        self,
        product_id: str,
        on_ready: Optional[Callable[["XCloudPlayerController"], None]] = None,
        on_error: Optional[Callable[["XCloudPlayerController", str], None]] = None,
        auth_module: Any = None,
        session_module: Any = None,
        player_factory: Optional[Callable[[], Any]] = None,
        sleep: Optional[SleepFn] = None,
        ice_collect_timeout: float = ICE_COLLECT_TIMEOUT_SEC,
        channel_handshake_timeout: float = CHANNEL_HANDSHAKE_TIMEOUT_SEC,
    ) -> None:
        self.product_id = product_id
        self.on_ready = on_ready
        self.on_error = on_error
        self.auth = auth_module or auth
        self.session = session_module or session
        self._player_factory = player_factory
        self._sleep = sleep or self._abortable_sleep
        self._ice_collect_timeout = max(0.0, float(ice_collect_timeout))
        self._channel_handshake_timeout = max(
            0.0, float(channel_handshake_timeout)
        )

        self.player: Any = None
        self._login: Any = None
        self._session: Any = None
        self.streaming = False
        self._title_cache: Dict[Tuple[str, str], str] = {}

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._gamepad = input.GamepadState(index=0)
        self._pending_events: List[Any] = []
        self._seq = input.Sequence()
        self._server_size: Tuple[int, int] = (DEFAULT_SERVER_WIDTH, DEFAULT_SERVER_HEIGHT)
        self._opened_channels: set[str] = set()
        self._message_handshake_sent = False
        self._message_handshake = threading.Event()
        self._protocol_error = ""

    # --- Ciclo de vida ------------------------------------------------------

    def start(self) -> None:
        """Inicia a thread de streaming. No-op se já estiver rodando."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="xcloud-player"
        )
        self._thread.start()

    def stop(self) -> None:
        """Pede o encerramento (não bloqueia; a thread daemon se limpa)."""
        self._stop_event.set()

    def _abortable_sleep(self, seconds: float) -> None:
        self._stop_event.wait(max(0.0, float(seconds)))

    def build_widget(self) -> Any:
        """Cria o widget de vídeo na thread GTK. Retorna None antes do ok."""
        if not self.streaming or self.player is None:
            return None
        player = _load_player_module()
        if player is None:
            return None
        return player.XCloudVideoWidget(self.player.peer if self.player else None)

    # --- Worker principal ------------------------------------------------------

    def _worker(self) -> None:
        try:
            self._run_stream()
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.streaming = False
            logger.warning("xCloud: streaming falhou: %s", error)
            if self.on_error is not None:
                from cartridges.utils.na_tela import entregar_na_tela  # noqa: PLC0415

                entregar_na_tela(self.on_error, self, str(error))
        finally:
            self.streaming = False
            self._teardown()

    def _open_session(self, login: Any, title_id: str) -> Tuple[Any, Any]:
        base = self.session.resolve_base_uri(login.streaming)
        prepared = self.session.open_session_for_login(
            login, title_id=title_id, sleep=self._sleep
        )
        return base, prepared

    def _resolve_title_id(self, login: Any, title_id: str) -> str:
        cached = self._title_cache.get((login.streaming.offering, title_id))
        if cached is not None:
            return cached
        base = self.session.resolve_base_uri(login.streaming)
        resolved = self.session.resolve_title_id(
            base, login.streaming.gs_token, title_id
        )
        self._title_cache[(login.streaming.offering, title_id)] = resolved
        return resolved

    def _run_stream(self) -> None:
        login = self.auth.refresh_streaming_tokens()
        self._login = login
        title_id = self.product_id
        try:
            base, prepared = self._open_session(login, title_id)
        except Exception as error:
            if "OfferingDoesNotContainTitle" not in str(error):
                raise
            # O catálogo guarda ID de loja; o /play quer o titleId da nuvem.
            resolved = self._resolve_title_id(login, title_id)
            if resolved and resolved != title_id:
                logger.info("xCloud: %s -> titleId %s", title_id, resolved)
                title_id = resolved
                base, prepared = self._open_session(login, title_id)
            elif login.streaming.offering == "xgpuweb":
                # Título fora do offering GPU (ex. gratuito): tenta o xgpuwebf2p
                # com login próprio antes de desistir.
                logger.info("xCloud: título fora do xgpuweb, tentando xgpuwebf2p")
                login = self.auth.login_offering(login.refresh_token, "xgpuwebf2p")
                self._login = login
                base, prepared = self._open_session(login, title_id)
            else:
                raise
        self._session = prepared

        player = (self._player_factory or _load_player_module().XCloudPlayer)()
        self.player = player
        player.set_data_channel_handler(self._on_data_channel)
        player.set_data_channel_open_handler(self._on_data_channel_open)

        # Os data channels precisam ser criados antes do offer: são eles que
        # fazem o webrtcbin anunciar m=application/SCTP no SDP.
        player.start()
        offer = player.create_offer()
        answer = self.session.exchange_sdp(
            base, login.streaming.gs_token, prepared.session_path, offer, sleep=self._sleep
        )
        player.set_remote_answer(answer.sdp)

        # Coleta de ICE com o pipeline já em PLAYING e os canais já anunciados
        # no SDP. O endpoint recebe cada candidato como uma string JSON.
        local_candidates = self._collect_ice(player)
        if not local_candidates:
            raise XCloudPlayerError(
                "nenhum candidato ICE local em %gs de coleta"
                % self._ice_collect_timeout
            )
        remote_candidates = self.session.exchange_ice(
            base,
            login.streaming.gs_token,
            self.session.ice_exchange_path(prepared),
            [
                json.dumps(
                    {
                        "candidate": candidate,
                        "sdpMid": str(mline),
                        "sdpMLineIndex": mline,
                    },
                    separators=(",", ":"),
                )
                for mline, candidate in local_candidates
            ],
            sleep=self._sleep,
        )
        for candidate in remote_candidates:
            if isinstance(candidate, str):
                texto = candidate
                mline = 0
            else:
                texto = candidate.candidate
                mline = candidate.sdp_mline_index
            if texto == "a=end-of-candidates":
                continue
            texto = texto[2:] if texto.startswith("a=") else texto
            player.add_remote_ice(mline, texto)

        self._wait_for_message_handshake()

        self.streaming = True

        if self.on_ready is not None:
            from cartridges.utils.na_tela import entregar_na_tela  # noqa: PLC0415

            entregar_na_tela(self.on_ready, self)

        self._start_keepalive(prepared, login.streaming.gs_token)
        self._io_loop(prepared, login.streaming.gs_token)

    def _collect_ice(
        self, player: Any, timeout: Optional[float] = None
    ) -> List[Tuple[int, str]]:
        timeout = self._ice_collect_timeout if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + timeout
        collected: List[Tuple[int, str]] = []
        while time.monotonic() < deadline:
            collected.extend(player.poll_outgoing_ice(max_count=10))
            if collected:
                return collected
            self._sleep(0.2)
        return collected

    def _wait_for_message_handshake(self) -> None:
        deadline = time.monotonic() + self._channel_handshake_timeout
        while not self._message_handshake.is_set():
            if self._stop_event.is_set():
                raise XCloudPlayerError("streaming cancelado durante o handshake")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                opened = ", ".join(sorted(self._opened_channels)) or "nenhum"
                raise XCloudPlayerError(
                    "timeout aguardando handshake dos data channels "
                    f"(abertos: {opened})"
                )
            self._message_handshake.wait(min(0.2, remaining))
        if self._protocol_error:
            raise XCloudPlayerError(self._protocol_error)

    def _io_loop(self, prepared: Any, gs_token: str) -> None:
        frame_interval = 1.0 / INPUT_RATE_HZ
        while not self._stop_event.is_set():
            started = time.monotonic()
            self._send_input_tick()
            elapsed = time.monotonic() - started
            self._sleep(max(0.0, frame_interval - elapsed))

    def _start_keepalive(self, prepared: Any, gs_token: str) -> None:
        pulse = int(prepared.configuration.keepalive_pulse_in_seconds or 0)
        if pulse <= 0:
            return
        threading.Thread(
            target=self._keepalive_loop,
            args=(prepared, gs_token, pulse),
            daemon=True,
            name="xcloud-keepalive",
        ).start()

    def _keepalive_loop(self, prepared: Any, gs_token: str, pulse: int) -> None:
        while not self._stop_event.is_set():
            self._sleep(float(pulse))
            if self._stop_event.is_set():
                return
            try:
                self.session.keepalive(prepared.base, gs_token, prepared.session_path)
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.debug("xCloud: keepalive falhou: %s", error)

    def _teardown(self) -> None:
        login = self._login
        prepared = self._session
        if prepared is not None and login is not None:
            try:
                self.session.stop(
                    prepared.base, login.streaming.gs_token, prepared.session_path
                )
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.debug("xCloud: encerrar sessão falhou: %s", error)
        player = self.player
        if player is not None:
            try:
                player.close()
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.debug("xCloud: fechar player falhou: %s", error)

    # --- Input ---------------------------------------------------------------

    def feed_button(self, button: int, pressed: bool) -> None:
        """Ajusta um bit de botão xCloud (``input.BTN_*``)."""
        bit = int(button)
        with self._lock:
            if pressed:
                self._gamepad.buttons |= bit
            else:
                self._gamepad.buttons &= ~bit

    def feed_axis(
        self,
        left_x: float = 0.0,
        left_y: float = 0.0,
        right_x: float = 0.0,
        right_y: float = 0.0,
    ) -> None:
        """Analógicos em -1..1 (eixo Y invertido fica com o encodador)."""
        with self._lock:
            self._gamepad.left_x = float(left_x)
            self._gamepad.left_y = float(left_y)
            self._gamepad.right_x = float(right_x)
            self._gamepad.right_y = float(right_y)

    def feed_trigger(self, left: float = 0.0, right: float = 0.0) -> None:
        """Gatilhos em 0..1."""
        with self._lock:
            self._gamepad.left_trigger = float(left)
            self._gamepad.right_trigger = float(right)

    def feed_hat(self, dx: int = 0, dy: int = 0) -> None:
        """Direcional (d-pad): dx,dy em -1..1. dy positivo é para cima."""
        bits = 0
        if dy > 0:
            bits |= input.BTN_DPAD_UP
        elif dy < 0:
            bits |= input.BTN_DPAD_DOWN
        if dx < 0:
            bits |= input.BTN_DPAD_LEFT
        elif dx > 0:
            bits |= input.BTN_DPAD_RIGHT
        with self._lock:
            self._gamepad.buttons &= ~(
                input.BTN_DPAD_UP | input.BTN_DPAD_DOWN | input.BTN_DPAD_LEFT | input.BTN_DPAD_RIGHT
            )
            self._gamepad.buttons |= bits

    def feed_keyboard(
        self, code: int, pressed: bool, source: int = input.KEY_SOURCE_VKEY
    ) -> None:
        """Enfileira um evento de teclado para o próximo tick."""
        event = input.KeyEvent(
            source=int(source) & 0xFF,
            pressed=bool(pressed),
            code=int(code) & 0xFF,
        )
        with self._lock:
            self._pending_events.append(event)

    def feed_mouse(
        self,
        x_fraction: float = 0.0,
        y_fraction: float = 0.0,
        buttons: int = 0,
        wheel_x: int = 0,
        wheel_y: int = 0,
        relative: bool = False,
    ) -> None:
        """Mouse em fração do canvas (0..1 cada eixo), convertido via ServerMetadata."""
        width, height = self._server_size
        event = input.MouseEvent(
            x=int(round(float(x_fraction) * width)),
            y=int(round(float(y_fraction) * height)),
            wheel_x=int(wheel_x),
            wheel_y=int(wheel_y),
            buttons=int(buttons) & 0xFF,
            relative=bool(relative),
        )
        with self._lock:
            self._pending_events.append(event)

    def _send_input_tick(self) -> None:
        player = self.player
        if player is None:
            return
        with self._lock:
            snapshot = input.GamepadState(
                index=self._gamepad.index,
                buttons=self._gamepad.buttons,
                left_x=self._gamepad.left_x,
                left_y=self._gamepad.left_y,
                right_x=self._gamepad.right_x,
                right_y=self._gamepad.right_y,
                left_trigger=self._gamepad.left_trigger,
                right_trigger=self._gamepad.right_trigger,
            )
            events = self._pending_events
            self._pending_events = []
        player.send_data("input", input.encode_gamepad(snapshot, self._seq.next()))
        for event in events:
            if isinstance(event, input.KeyEvent):
                packet = input.encode_keyboard(event, self._seq.next())
            elif isinstance(event, input.MouseEvent):
                packet = input.encode_mouse(event, self._seq.next())
            else:
                continue
            player.send_data("input", packet)

    # --- Canais de dados -------------------------------------------------------

    def _on_data_channel(self, label: str, data: bytes) -> None:
        # Frames binários do servidor (ServerMetadata, Vibration) chegam pelo
        # canal `input`; os demais canais carregam JSON/texto e não decodificam.
        if label == "message":
            self._on_message_channel(data)
            return
        if label != "input":
            return
        try:
            report_type, event = input.decode_server_frame(data)
        except ValueError:
            return
        if isinstance(event, input.ServerMetadata) and event.width > 0 and event.height > 0:
            self._server_size = (event.width, event.height)
        elif isinstance(event, input.VibrationEvent):
            logger.debug(
                "xCloud: vibração gamepad=%s left=%s right=%s",
                event.gamepad_index,
                event.left_motor,
                event.right_motor,
            )

    def _on_data_channel_open(self, label: str) -> None:
        with self._lock:
            self._opened_channels.add(label)
            should_handshake = (
                not self._message_handshake_sent
                and REQUIRED_DATA_CHANNELS.issubset(self._opened_channels)
            )
            if should_handshake:
                self._message_handshake_sent = True
        if not should_handshake:
            return
        payload = {
            "type": "Handshake",
            "version": "messageV1",
            "id": "be0bfc6d-1e83-4c8a-90ed-fa8601c5a179",
            "cv": "0",
        }
        if not self.player.send_string(
            "message", json.dumps(payload, separators=(",", ":"))
        ):
            self._fail_protocol("não foi possível enviar o handshake messageV1")

    def _on_message_channel(self, data: bytes) -> None:
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(message, dict) or message.get("type") != "HandshakeAck":
            return

        # A autorização e o primeiro frame de input só são aceitos depois do
        # HandshakeAck do canal messageV1.
        ok = self.player.send_authorization_request(ACCESS_KEY)
        ok = self.player.send_gamepad_changed(0, was_added=True) and ok
        ok = self.player.send_data("input", input.first_packet()) and ok
        ok = self._send_message_configuration() and ok
        if not ok:
            self._fail_protocol("um data channel fechou durante a inicialização")
            return
        self._message_handshake.set()

    def _send_message_configuration(self) -> bool:
        messages = (
            ("/streaming/systemUi/configuration", {"version": [0, 2, 0], "systemUis": []}),
            (
                "/streaming/properties/clientappinstallidchanged",
                {"clientAppInstallId": "c97d7ee0-73b2-4239-bf1d-9d805a338429"},
            ),
            ("/streaming/characteristics/orientationchanged", {"orientation": 0}),
            (
                "/streaming/characteristics/touchinputenabledchanged",
                {"touchInputEnabled": False},
            ),
            ("/streaming/characteristics/clientdevicecapabilities", {}),
            (
                "/streaming/characteristics/dimensionschanged",
                {
                    "horizontal": DEFAULT_SERVER_WIDTH,
                    "vertical": DEFAULT_SERVER_HEIGHT,
                    "preferredWidth": DEFAULT_SERVER_WIDTH,
                    "preferredHeight": DEFAULT_SERVER_HEIGHT,
                    "safeAreaLeft": 0,
                    "safeAreaTop": 0,
                    "safeAreaRight": DEFAULT_SERVER_WIDTH,
                    "safeAreaBottom": DEFAULT_SERVER_HEIGHT,
                    "supportsCustomResolution": True,
                },
            ),
        )
        success = True
        for target, content in messages:
            payload = {
                "type": "Message",
                "content": json.dumps(content, separators=(",", ":")),
                "id": str(uuid.uuid4()),
                "target": target,
                "cv": "",
            }
            success = self.player.send_string(
                "message", json.dumps(payload, separators=(",", ":"))
            ) and success
        return success

    def _fail_protocol(self, message: str) -> None:
        self._protocol_error = message
        self._message_handshake.set()
