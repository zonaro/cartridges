# player.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Player WebRTC para xCloud/xHome com GStreamer + GTK4.

Módulo com lógica de pipeline WebRTC (recvonly H.264 + Opus), transceivers,
canais de dados, munging de SDP, ponte de sinalização manual e widget GTK4
para renderização de vídeo.

Requisitos: GStreamer 1.0 + GstWebRTC + GstSdp (gi). Gtk 4.0 + Gdk + GLib
para o widget. Nenhum token/segredo é logado.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstWebRTC", "1.0")
    gi.require_version("GstSdp", "1.0")
    gi.require_version("GstVideo", "1.0")
    from gi.repository import GLib, Gst, GstSdp, GstVideo, GstWebRTC
except (ImportError, ValueError) as e:  # pragma: no cover
    Gst = None  # type: ignore[assignment]
    GstWebRTC = None  # type: ignore[assignment]
    GstSdp = None  # type: ignore[assignment]
    GstVideo = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
    _GI_ERROR = e
else:
    _GI_ERROR = None

try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk
except (ImportError, ValueError):  # pragma: no cover
    Gtk = None  # type: ignore[assignment]
    Gdk = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Caps H.264 recvonly conforme especificação
H264_CAPS = (
    "application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,"
    "payload=127,packetization-mode=(string)1,profile-level-id=(string)42002a"
)

# Caps Opus recvonly
OPUS_CAPS = (
    "application/x-rtp,media=audio,clock-rate=48000,encoding-params=(string)2,"
    "payload=111"
)

# Data channels: labels/protocols/ids conforme especificado
DATA_CHANNELS: List[Dict[str, Any]] = [
    {"label": "input", "protocol": "1.0", "ordered": True},
    {"label": "control", "protocol": "controlV1", "ordered": True},
    {"label": "message", "protocol": "messageV1", "ordered": True},
    {"label": "chat", "protocol": "chatV1", "ordered": True},
]

DEFAULT_BANDWIDTH_KBPS = 5000


class PlayerError(Exception):
    """Erro genérico do player."""


class GstMissingError(PlayerError):
    """GStreamer/gi indisponíveis."""


class NoDecoderError(PlayerError):
    """Nenhum decodificador H.264 disponível."""


class IceTimeoutError(PlayerError):
    """Timeout na troca/coleta de ICE."""


@dataclass
class IceCandidate:
    mline: int
    candidate: str


@dataclass
class DataChannelConfig:
    label: str
    protocol: str
    id: int
    ordered: bool = True


def _ensure_gst() -> None:
    if Gst is None or GstWebRTC is None or GstSdp is None or _GI_ERROR:
        raise GstMissingError(f"GStreamer/gi não disponível: {_GI_ERROR}")


def webrtc_supported() -> Tuple[bool, str]:
    """Diz se toda a cadeia WebRTC, SCTP, mídia e codecs está disponível."""
    if Gst is None or _GI_ERROR:
        return False, f"GStreamer/gi indisponível: {_GI_ERROR}"
    try:
        Gst.init(None)
        registry = Gst.Registry.get()
        required = (
            "webrtcbin",
            "nicesrc",
            "nicesink",
            "sctpenc",
            "sctpdec",
            "rtph264depay",
            "h264parse",
            "rtpopusdepay",
            "opusdec",
            "videoconvert",
            "audioconvert",
            "appsink",
            "autoaudiosink",
        )
        missing = [
            name
            for name in required
            if registry.find_feature(name, Gst.ElementFactory) is None
        ]
        if missing:
            return False, "elementos GStreamer ausentes: " + ", ".join(missing)
        try:
            _find_decoder()
        except NoDecoderError as error:
            return False, str(error)
        return True, "ok"
    except Exception as error:
        return False, f"falha sondando GStreamer: {error}"


def _parse_sdp_media(sdp_text: str) -> List[str]:
    if not sdp_text:
        return []
    media_lines: List[str] = []
    for line in sdp_text.splitlines():
        if line.startswith("m="):
            media_lines.append(line)
    return media_lines


def _new_sdp_message() -> Any:
    """Cria SDPMessage normalizando o retorno em tupla do PyGObject."""
    created = GstSdp.SDPMessage.new()
    if isinstance(created, tuple):
        result, message = created
        if result != GstSdp.SDPResult.OK or message is None:
            raise PlayerError(f"não foi possível criar SDPMessage: {result}")
        return message
    return created


def _parse_sdp_message(sdp_text: str) -> Any:
    message = _new_sdp_message()
    result = GstSdp.sdp_message_parse_buffer(sdp_text.encode("utf-8"), message)
    if result != GstSdp.SDPResult.OK:
        raise PlayerError(f"SDP inválido: {result}")
    return message


def _filter_h264_only(sdp_text: str) -> str:
    if not sdp_text:
        return sdp_text
    lines = sdp_text.splitlines(True)
    out_lines: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("m=video"):
            j = i
            has_h264 = False
            while j < len(lines):
                l = lines[j]
                if l.startswith("m=") and j > i:
                    break
                if "H264" in l or "h264" in l:
                    has_h264 = True
                j += 1
            if has_h264:
                for k in range(i, j):
                    out_lines.append(lines[k])
            i = j
            continue
        elif line.startswith("m="):
            j = i
            while j < len(lines):
                l = lines[j]
                if l.startswith("m=") and j > i:
                    break
                j += 1
            for k in range(i, j):
                out_lines.append(lines[k])
            i = j
            continue
        else:
            out_lines.append(line)
            i += 1
    return "".join(out_lines)


def munge_sdp(sdp_text: str, bandwidth_kbps: int = DEFAULT_BANDWIDTH_KBPS) -> str:
    if not sdp_text:
        return sdp_text
    sdp_text = _filter_h264_only(sdp_text)
    lines = sdp_text.splitlines(True)
    out: List[str] = []
    i = 0
    in_audio = False
    while i < len(lines):
        line = lines[i]
        if line.startswith("m=audio"):
            in_audio = True
            out.append(line)
            j = i + 1
            has_b = False
            while j < len(lines):
                l = lines[j]
                if l.startswith("m="):
                    break
                if l.startswith("b=AS:") or l.startswith("b=TIAS:"):
                    has_b = True
                j += 1
            if not has_b:
                out.append(f"b=AS:{bandwidth_kbps}\r\n")
            i += 1
            continue
        elif line.startswith("m=video"):
            in_audio = False
            out.append(line)
            j = i + 1
            has_b = False
            while j < len(lines):
                l = lines[j]
                if l.startswith("m="):
                    break
                if l.startswith("b=AS:") or l.startswith("b=TIAS:"):
                    has_b = True
                j += 1
            if not has_b:
                out.append(f"b=AS:{bandwidth_kbps}\r\n")
            i += 1
            continue
        elif line.startswith("m="):
            in_audio = False
            out.append(line)
            i += 1
            continue
        if in_audio and line.startswith("a=fmtp:"):
            low = line.lower()
            if "stereo=" not in low:
                if line.endswith("\r\n"):
                    out.append(line.rstrip("\r\n") + ";stereo=1\r\n")
                elif line.endswith("\n"):
                    out.append(line.rstrip("\n") + ";stereo=1\n")
                else:
                    out.append(line + ";stereo=1\r\n")
                i += 1
                continue
        out.append(line)
        i += 1
    return "".join(out)


def _find_decoder() -> str:
    """Encontra decodificador H.264 preferindo HW accel."""
    _ensure_gst()
    factories_order = ["vah264dec", "nvh264dec", "avdec_h264", "openh264dec"]
    for name in factories_order:
        f = Gst.ElementFactory.find(name)
        if f is not None:
            return name
    # try any h264 decoder
    registry = Gst.Registry.get()
    for f in registry.get_feature_list(Gst.ElementFactory):
        klass = f.get_klass()
        if "Decoder" in klass and "H.264" in f.get_longname():
            return f.get_name()
    raise NoDecoderError("Nenhum decodificador H.264 encontrado")


class WebRTCPeer:
    """Peer WebRTC com webrtcbin, transceivers de mídia e canais de dados."""

    def __init__(
        self,
        video_sink: Optional[Any] = None,
        audio_sink: Optional[Any] = None,
        name: str = "webrtc-peer",
        bandwidth_kbps: int = DEFAULT_BANDWIDTH_KBPS,
    ) -> None:
        _ensure_gst()
        self._name = name
        self._bandwidth_kbps = bandwidth_kbps
        self._video_sink = video_sink
        self._audio_sink = audio_sink
        self._pipeline: Optional[Gst.Pipeline] = None
        self._webrtc: Optional[GstWebRTC.WebRTCBin] = None
        self._bus_watch_id: Optional[int] = None
        self._ice_queue: "queue.Queue[IceCandidate]" = queue.Queue()
        self._ice_out_callback: Optional[Callable[[int, str], None]] = None
        self._data_channels: Dict[str, GstWebRTC.WebRTCDataChannel] = {}
        self._on_data_channel: Optional[Callable[[str, bytes], None]] = None
        self._on_data_channel_open: Optional[Callable[[str], None]] = None
        self._on_ice: Optional[Callable[[int, str], None]] = None
        self._appsink: Optional[Gst.Element] = None
        self._frame_count = 0
        self._frame_handler: Optional[Callable[[Any], None]] = None
        self._lock = threading.Lock()
        self._local_desc: Optional[str] = None

        self._setup_pipeline()

    def _setup_pipeline(self) -> None:
        Gst.init(None)

        pipe = Gst.Pipeline.new(f"{self._name}-pipeline")
        self._pipeline = pipe

        webrtc = Gst.ElementFactory.make("webrtcbin", f"{self._name}-webrtc")
        if webrtc is None:
            raise PlayerError("Falha ao criar webrtcbin")
        webrtc.set_property("bundle-policy", GstWebRTC.WebRTCBundlePolicy.MAX_BUNDLE)
        pipe.add(webrtc)
        self._webrtc = webrtc

        # Video: recvonly
        # Transceivers explícitos ANTES de qualquer offer: sem eles o SDP
        # sai sem m-lines e o xCloud rejeita. Direção + caps por tipo.
        video_caps = Gst.Caps.from_string(H264_CAPS)
        webrtc.emit(
            "add-transceiver",
            GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY,
            video_caps,
        )
        audio_caps = Gst.Caps.from_string(OPUS_CAPS)
        webrtc.emit(
            "add-transceiver",
            GstWebRTC.WebRTCRTPTransceiverDirection.SENDRECV,
            audio_caps,
        )
        # Add video sink via decodebin on pad-added

        # Audio: recvonly
        webrtc.connect("pad-added", self._on_pad_added)
        webrtc.connect("on-negotiation-needed", self._on_negotiation_needed)
        webrtc.connect("on-ice-candidate", self._on_ice_candidate)
        webrtc.connect("on-data-channel", self._on_data_channel_cb)

        # Setup appsink for video frames (internal)
        appsink = Gst.ElementFactory.make("appsink", f"{self._name}-appsink")
        if appsink is None:
            raise PlayerError("Falha ao criar appsink")
        appsink.set_property("emit-signals", True)
        appsink.set_property("sync", False)
        appsink.set_property("drop", True)
        appsink.set_property("max-buffers", 2)
        appsink.set_property("caps", Gst.Caps.from_string("video/x-raw,format=RGBx"))
        appsink.connect("new-sample", self._on_new_sample)
        self._appsink = appsink

        # Bus watch
        bus = pipe.get_bus()
        bus.add_signal_watch()
        self._bus_watch_id = bus.connect("message", self._on_bus_message)

    def _on_negotiation_needed(self, webrtc: GstWebRTC.WebRTCBin, user_data: Any = None) -> None:
        logger.debug("%s: negotiation needed", self._name)

    def _on_ice_candidate(self, webrtc: GstWebRTC.WebRTCBin, mlineindex: int, candidate: str) -> None:
        cand = IceCandidate(mline=mlineindex, candidate=candidate)
        with self._lock:
            cb = self._ice_out_callback
        if cb is not None:
            try:
                GLib.idle_add(cb, mlineindex, candidate) if GLib else cb(mlineindex, candidate)
            except Exception:
                logger.debug("erro ao entregar ice via callback", exc_info=True)
        self._ice_queue.put(cand)

    def _on_data_channel_cb(self, webrtc: GstWebRTC.WebRTCBin, channel: GstWebRTC.WebRTCDataChannel) -> None:
        label = channel.get_property("label")
        logger.debug("%s: data channel recebido %s", self._name, label)
        self._data_channels[label] = channel
        self._wire_data_channel(channel)

    def _wire_data_channel(self, channel: GstWebRTC.WebRTCDataChannel) -> None:
        channel.connect("on-open", self._on_dc_open)
        channel.connect("on-message-data", self._on_dc_message_data)
        channel.connect("on-message-string", self._on_dc_message_string)

    def _on_dc_open(self, channel: GstWebRTC.WebRTCDataChannel) -> None:
        label = str(channel.get_property("label") or "")
        logger.debug("%s: data channel aberto %s", self._name, label)
        with self._lock:
            callback = self._on_data_channel_open
        if callback is not None:
            callback(label)

    def _on_dc_message_data(self, channel: GstWebRTC.WebRTCDataChannel, data: Gst.Buffer) -> None:
        # extrai bytes
        try:
            success, mapinfo = data.map(Gst.MapFlags.READ)
            if success:
                b = bytes(mapinfo.data)
                data.unmap(mapinfo)
                label = channel.get_property("label")
                if self._on_data_channel:
                    self._on_data_channel(label, b)
                return
        except Exception:
            logger.debug("erro ao ler dc data", exc_info=True)

    def _on_dc_message_string(self, channel: GstWebRTC.WebRTCDataChannel, data: str) -> None:
        label = channel.get_property("label")
        if self._on_data_channel:
            try:
                self._on_data_channel(label, data.encode("utf-8", errors="replace"))
            except Exception:
                logger.debug("erro ao entregar dc string", exc_info=True)

    def _on_pad_added(self, webrtc: GstWebRTC.WebRTCBin, pad: Gst.Pad) -> None:
        if pad.direction != Gst.PadDirection.SRC:
            return
        caps = pad.get_current_caps() or pad.query_caps(None)
        if caps is None:
            return
        struct = caps.get_structure(0)
        name = struct.get_name()
        media = str(struct.get_string("media") or "") if struct.has_field("media") else ""
        if name.startswith("video") or media == "video":
            self._link_video(pad)
        elif name.startswith("audio") or media == "audio":
            self._link_audio(pad)

    def _link_video(self, pad: Gst.Pad) -> None:
        dec_name = _find_decoder()
        q = Gst.ElementFactory.make("queue")
        depay = Gst.ElementFactory.make("rtph264depay")
        parse = Gst.ElementFactory.make("h264parse")
        dec = Gst.ElementFactory.make(dec_name)
        conv = Gst.ElementFactory.make("videoconvert")
        if q is None or depay is None or parse is None or dec is None or conv is None:
            logger.error("elementos de vídeo ausentes")
            return
        pipe = self._pipeline
        assert pipe is not None
        pipe.add(q)
        pipe.add(depay)
        pipe.add(parse)
        pipe.add(dec)
        pipe.add(conv)
        pipe.add(self._appsink)  # type: ignore[arg-type]
        q.sync_state_with_parent()
        depay.sync_state_with_parent()
        parse.sync_state_with_parent()
        dec.sync_state_with_parent()
        conv.sync_state_with_parent()
        self._appsink.sync_state_with_parent()  # type: ignore[union-attr]
        pad.link(q.get_static_pad("sink"))
        q.link(depay)
        depay.link(parse)
        parse.link(dec)
        dec.link(conv)
        conv.link(self._appsink.get_static_pad("sink"))

    def _link_audio(self, pad: Gst.Pad) -> None:
        q = Gst.ElementFactory.make("queue")
        depay = Gst.ElementFactory.make("rtpopusdepay")
        dec = Gst.ElementFactory.make("opusdec")
        aconv = Gst.ElementFactory.make("audioconvert")
        asink = self._audio_sink or Gst.ElementFactory.make("autoaudiosink")
        if q is None or depay is None or dec is None or aconv is None or asink is None:
            logger.error("elementos de áudio ausentes")
            return
        pipe = self._pipeline
        assert pipe is not None
        pipe.add(q)
        pipe.add(depay)
        pipe.add(dec)
        pipe.add(aconv)
        pipe.add(asink)
        q.sync_state_with_parent()
        depay.sync_state_with_parent()
        dec.sync_state_with_parent()
        aconv.sync_state_with_parent()
        asink.sync_state_with_parent()
        pad.link(q.get_static_pad("sink"))
        q.link(depay)
        depay.link(dec)
        dec.link(aconv)
        aconv.link(asink)

    def _on_new_sample(self, appsink: Gst.Element) -> Gst.FlowReturn:
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        with self._lock:
            self._frame_count += 1
            callback = self._frame_handler
        if callback is not None:
            callback(sample)
        return Gst.FlowReturn.OK

    def _on_bus_message(self, bus: Gst.Bus, msg: Gst.Message) -> int:
        if msg.type == Gst.MessageType.ERROR:
            error, debug = msg.parse_error()
            logger.error("%s: erro no pipeline: %s (%s)", self._name, error, debug or "")
        elif msg.type == Gst.MessageType.WARNING:
            warning, debug = msg.parse_warning()
            logger.warning(
                "%s: aviso no pipeline: %s (%s)", self._name, warning, debug or ""
            )
        return 1  # continue

    def start(self) -> None:
        if self._pipeline is None:
            raise PlayerError("pipeline não inicializado")
        self._pipeline.set_state(Gst.State.PLAYING)

    def _ensure_playing(self) -> None:
        """webrtcbin exige PLAYING para offer ITSELF answer e data channels."""
        if self._pipeline is None:
            raise PlayerError("pipeline não inicializado")
        _state, current, _pending = self._pipeline.get_state(0)
        if current != Gst.State.PLAYING:
            logger.debug("%s: subindo pipeline para PLAYING", self._name)
            self.start()

    def pause(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.PAUSED)

    def stop(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
        if self._bus_watch_id is not None:
            bus = self._pipeline.get_bus() if self._pipeline else None
            if bus is not None:
                try:
                    bus.disconnect(self._bus_watch_id)
                except Exception:
                    pass
            self._bus_watch_id = None

    def close(self) -> None:
        self.stop()
        self._pipeline = None
        self._webrtc = None
        self._appsink = None
        with self._lock:
            self._ice_queue = queue.Queue()
            self._ice_out_callback = None
            self._on_data_channel = None
            self._on_data_channel_open = None
            self._frame_handler = None

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._frame_count

    def set_ice_out_callback(self, cb: Callable[[int, str], None]) -> None:
        with self._lock:
            self._ice_out_callback = cb

    def set_data_channel_handler(self, cb: Callable[[str, bytes], None]) -> None:
        with self._lock:
            self._on_data_channel = cb

    def set_data_channel_open_handler(self, cb: Callable[[str], None]) -> None:
        with self._lock:
            self._on_data_channel_open = cb

    def set_frame_handler(self, cb: Optional[Callable[[Any], None]]) -> None:
        with self._lock:
            self._frame_handler = cb

    def create_data_channels(self) -> None:
        if self._webrtc is None:
            raise PlayerError("webrtcbin não inicializado")
        self._ensure_playing()
        for cfg in DATA_CHANNELS:
            # O Greenlight usa canais in-band (negotiated=false) e deixa o
            # SCTP atribuir os IDs. Eles precisam existir antes do offer para
            # que a seção m=application apareça no SDP.
            options = Gst.Structure.new_empty("data-channel-options")
            options.set_value("ordered", bool(cfg["ordered"]))
            options.set_value("protocol", str(cfg["protocol"]))
            chan = self._webrtc.emit("create-data-channel", cfg["label"], options)
            if chan is None:
                ok, reason = webrtc_supported()
                raise PlayerError(
                    f"webrtcbin recusou o canal {cfg['label']!r} ({reason})"
                    if not ok
                    else f"webrtcbin recusou o canal {cfg['label']!r}"
                )
            self._data_channels[cfg["label"]] = chan
            self._wire_data_channel(chan)

    def _run_promise(
        self,
        emit_fn: Any,
        what: str,
        timeout: float = 30.0,
        allow_empty: bool = False,
    ) -> Any:
        """Emite com promise de callback + timeout (wait() puro pendura)."""
        concluido = threading.Event()
        caixa: Dict[str, Any] = {}

        def _on_done(promise: Any) -> None:
            try:
                caixa["result"] = promise.wait()
            except Exception:
                caixa["result"] = None
            try:
                caixa["reply"] = promise.get_reply()
            except Exception:
                caixa["reply"] = None
            concluido.set()

        promise = Gst.Promise.new_with_change_func(_on_done)
        emit_fn(promise)
        if not concluido.wait(timeout=max(1.0, timeout)):
            raise PlayerError(f"timeout aguardando {what} do webrtcbin")
        result = caixa.get("result")
        if result is not None and result != Gst.PromiseResult.REPLIED:
            raise PlayerError(f"webrtcbin recusou {what}: {result}")
        reply = caixa.get("reply")
        if reply is None and not allow_empty:
            raise PlayerError(f"resposta vazia em {what} do webrtcbin")
        return reply

    def create_offer(self) -> str:
        """Cria offer e fixa local description. BLOQUEIA (com timeout): chamar fora da GTK."""
        if self._webrtc is None:
            raise PlayerError("webrtcbin não inicializado")
        self._ensure_playing()
        logger.info("%s: criando offer SDP", self._name)
        reply = self._run_promise(
            lambda promise: self._webrtc.emit("create-offer", None, promise),
            "create-offer",
        )
        offer = reply.get_value("offer")
        sdp_text = offer.sdp.as_text()
        munged = munge_sdp(sdp_text, self._bandwidth_kbps)
        local_sdp = _parse_sdp_message(munged)
        local_offer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.OFFER, local_sdp
        )
        # A descrição fixada localmente deve ser exatamente a enviada ao GSSV.
        self._run_promise(
            lambda promise: self._webrtc.emit(
                "set-local-description", local_offer, promise
            ),
            "set-local-description",
            allow_empty=True,
        )
        self._local_desc = munged
        logger.info("%s: offer pronta (%d bytes)", self._name, len(munged))
        return munged

    def set_remote_answer(self, sdp_text: str) -> None:
        if self._webrtc is None:
            raise PlayerError("webrtcbin não inicializado")
        sdp = _parse_sdp_message(sdp_text)
        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, sdp
        )
        self._run_promise(
            lambda promise: self._webrtc.emit("set-remote-description", answer, promise),
            "set-remote-description",
            allow_empty=True,
        )

    def set_remote_offer(self, sdp_text: str) -> None:
        if self._webrtc is None:
            raise PlayerError("webrtcbin não inicializado")
        sdp = _parse_sdp_message(sdp_text)
        offer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.OFFER, sdp
        )
        self._run_promise(
            lambda promise: self._webrtc.emit("set-remote-description", offer, promise),
            "set-remote-description",
            allow_empty=True,
        )

    def create_answer(self) -> str:
        """Cria answer e fixa local description. BLOQUEIA (com timeout): chamar fora da GTK."""
        if self._webrtc is None:
            raise PlayerError("webrtcbin não inicializado")
        self._ensure_playing()
        reply = self._run_promise(
            lambda promise: self._webrtc.emit("create-answer", None, promise),
            "create-answer",
        )
        answer = reply.get_value("answer")
        munged = munge_sdp(answer.sdp.as_text(), self._bandwidth_kbps)
        local_answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, _parse_sdp_message(munged)
        )
        self._run_promise(
            lambda promise: self._webrtc.emit(
                "set-local-description", local_answer, promise
            ),
            "set-local-description",
            allow_empty=True,
        )
        self._local_desc = munged
        return munged

    def add_remote_ice(self, mline: int, candidate: str) -> None:
        if self._webrtc is None:
            raise PlayerError("webrtcbin não inicializado")
        self._webrtc.emit("add-ice-candidate", mline, candidate)

    def get_local_description(self) -> Optional[str]:
        return self._local_desc

    def send_data(self, label: str, data: bytes) -> bool:
        chan = self._data_channels.get(label)
        if chan is None:
            return False
        try:
            if (
                hasattr(chan, "get_property")
                and chan.get_property("ready-state")
                != GstWebRTC.WebRTCDataChannelState.OPEN
            ):
                return False
            chan.emit("send-data", Gst.Buffer.new_wrapped(bytes(data)))
            return True
        except Exception:
            return False

    def send_string(self, label: str, text: str) -> bool:
        chan = self._data_channels.get(label)
        if chan is None:
            return False
        try:
            if (
                hasattr(chan, "get_property")
                and chan.get_property("ready-state")
                != GstWebRTC.WebRTCDataChannelState.OPEN
            ):
                return False
            chan.emit("send-string", text)
            return True
        except Exception:
            return False

    # Control channel helpers (plumbing + JSON send). Formato message-keyed
    # do cliente web oficial: {"message": ..., ...}.
    def send_control_json(self, payload: Dict[str, Any]) -> bool:
        return self.send_string("control", json.dumps(payload))

    def send_authorization_request(self, access_key: str) -> bool:
        return self.send_control_json(
            {"message": "authorizationRequest", "accessKey": access_key}
        )

    def send_gamepad_changed(self, gamepad_index: int, was_added: bool) -> bool:
        return self.send_control_json(
            {
                "message": "gamepadChanged",
                "gamepadIndex": gamepad_index,
                "wasAdded": was_added,
            }
        )

    def send_video_keyframe_requested(self) -> bool:
        return self.send_control_json(
            {"message": "videoKeyframeRequested", "ifrRequested": True}
        )

    @property
    def webrtcbin(self) -> Optional[GstWebRTC.WebRTCBin]:
        return self._webrtc

    @property
    def pipeline(self) -> Optional[Gst.Pipeline]:
        return self._pipeline

    @property
    def appsink(self) -> Optional[Gst.Element]:
        return self._appsink


class XCloudPlayer:
    """Player de alto nível com ponte de sinalização manual."""

    def __init__(
        self,
        video_sink: Optional[Any] = None,
        audio_sink: Optional[Any] = None,
    ) -> None:
        self._peer = WebRTCPeer(video_sink=video_sink, audio_sink=audio_sink)
        self._ice_out_queue: "queue.Queue[Tuple[int, str]]" = queue.Queue()
        self._peer.set_ice_out_callback(self._on_ice_out)
        self._on_data_channel_handler: Optional[Callable[[str, bytes], None]] = None

    def _on_ice_out(self, mline: int, candidate: str) -> None:
        self._ice_out_queue.put((mline, candidate))

    def set_data_channel_handler(self, cb: Callable[[str, bytes], None]) -> None:
        self._on_data_channel_handler = cb
        self._peer.set_data_channel_handler(cb)

    def set_data_channel_open_handler(self, cb: Callable[[str], None]) -> None:
        self._peer.set_data_channel_open_handler(cb)

    def start(self) -> None:
        self._peer.create_data_channels()
        self._peer.start()

    def stop(self) -> None:
        self._peer.stop()

    def close(self) -> None:
        self._peer.close()

    def create_offer(self) -> str:
        return self._peer.create_offer()

    def set_remote_answer(self, sdp: str) -> None:
        self._peer.set_remote_answer(sdp)

    def add_remote_ice(self, mline: int, candidate: str) -> None:
        self._peer.add_remote_ice(mline, candidate)

    def poll_outgoing_ice(self, max_count: int = 10) -> List[Tuple[int, str]]:
        res: List[Tuple[int, str]] = []
        for _ in range(max_count):
            try:
                res.append(self._ice_out_queue.get_nowait())
            except queue.Empty:
                break
        return res

    def send_data(self, label: str, data: bytes) -> bool:
        return self._peer.send_data(label, data)

    def send_string(self, label: str, text: str) -> bool:
        return self._peer.send_string(label, text)

    def send_control_json(self, payload: Dict[str, Any]) -> bool:
        return self._peer.send_control_json(payload)

    def send_authorization_request(self, access_key: str) -> bool:
        return self._peer.send_authorization_request(access_key)

    def send_gamepad_changed(self, gamepad_index: int, was_added: bool) -> bool:
        return self._peer.send_gamepad_changed(gamepad_index, was_added)

    def send_video_keyframe_requested(self) -> bool:
        return self._peer.send_video_keyframe_requested()

    @property
    def frame_count(self) -> int:
        return self._peer.frame_count

    @property
    def peer(self) -> WebRTCPeer:
        return self._peer


# Signaling bridge API names as specified
def create_offer(player: XCloudPlayer) -> str:
    return player.create_offer()


def set_remote_answer(player: XCloudPlayer, sdp: str) -> None:
    player.set_remote_answer(sdp)


def add_remote_ice(player: XCloudPlayer, mline: int, candidate: str) -> None:
    player.add_remote_ice(mline, candidate)


if Gtk is not None and Gdk is not None and GLib is not None:

    class XCloudVideoWidget(Gtk.Widget):
        """Widget GTK4 para exibição de vídeo xCloud."""

        def __init__(self, peer: Optional[WebRTCPeer] = None) -> None:
            super().__init__()
            self._peer = peer
            self._texture: Optional[Gdk.Texture] = None
            self._last_sample: Optional[Gst.Sample] = None
            self._paintable: Optional[Gdk.Paintable] = None
            if peer is not None:
                peer.set_frame_handler(self._on_sample)

        def set_peer(self, peer: WebRTCPeer) -> None:
            self._peer = peer
            peer.set_frame_handler(self._on_sample)

        def _on_sample(self, sample: Gst.Sample) -> None:
            buf = sample.get_buffer()
            if buf is None:
                return
            caps = sample.get_caps()
            if caps is None:
                return

            # Extract RGBx/GstVideo info
            struct = caps.get_structure(0)
            width = struct.get_int("width")[1] if struct.has_field("width") else 0
            height = struct.get_int("height")[1] if struct.has_field("height") else 0

            success, mapinfo = buf.map(Gst.MapFlags.READ)
            if not success:
                return
            try:
                data = bytes(mapinfo.data)
            finally:
                buf.unmap(mapinfo)

            if width <= 0 or height <= 0 or len(data) == 0:
                return

            GLib.idle_add(self._present_frame, data, width, height)

        def _present_frame(self, data: bytes, width: int, height: int) -> bool:
            """Cria a textura apenas na thread GTK."""

            bytes_per_row = width * 4
            texture = Gdk.MemoryTexture.new(
                width,
                height,
                Gdk.MemoryFormat.R8G8B8X8,
                GLib.Bytes.new(data),
                bytes_per_row,
            )
            self._texture = texture
            self.queue_draw()
            return False

        def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
            if self._texture is not None:
                w = self.get_width()
                h = self.get_height()
                if w > 0 and h > 0:
                    rect = Graphene.Rect().init(0, 0, w, h)
                    snapshot.append_texture(self._texture, rect)

        def do_unrealize(self) -> None:
            if self._peer is not None:
                self._peer.set_frame_handler(None)
            Gtk.Widget.do_unrealize(self)

else:
    class XCloudVideoWidget:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise GstMissingError("GTK/Gdk não disponível")


try:
    from gi.repository import Graphene  # noqa: F401
except Exception:  # pragma: no cover
    Graphene = None  # type: ignore[assignment]
