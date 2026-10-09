# input.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Codificação/decodificação dos pacotes binários do canal `input` do xCloud.

Porte de `input/packet.ts` do xbox-xcloud-player (unknownskl): todo inteiro
little-endian, exceto o campo VirtualPhysicality do gamepad, que o cliente
oficial grava em big-endian — mantido igual (quirk aceito pelo servidor).

Módulo puro: sem Gst, sem GTK, sem rede. O loop de envio (~60 Hz) fica na UI.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

REPORT_METADATA = 0x001
REPORT_GAMEPAD = 0x002
REPORT_POINTER = 0x004
REPORT_CLIENT_METADATA = 0x008
REPORT_SERVER_METADATA = 0x010
REPORT_MOUSE = 0x020
REPORT_KEYBOARD = 0x040
REPORT_VIBRATION = 0x080
REPORT_SENSOR = 0x100
REPORT_UNRELIABLE_REPORT = 0x200
REPORT_UNRELIABLE_ACK = 0x400

BTN_NEXUS = 0x0002
BTN_MENU = 0x0004
BTN_VIEW = 0x0008
BTN_A = 0x0010
BTN_B = 0x0020
BTN_X = 0x0040
BTN_Y = 0x0080
BTN_DPAD_UP = 0x0100
BTN_DPAD_DOWN = 0x0200
BTN_DPAD_LEFT = 0x0400
BTN_DPAD_RIGHT = 0x0800
BTN_LEFT_SHOULDER = 0x1000
BTN_RIGHT_SHOULDER = 0x2000
BTN_LEFT_THUMB = 0x4000
BTN_RIGHT_THUMB = 0x8000

KEY_SOURCE_UNKNOWN = 0
KEY_SOURCE_KNOWN = 1
KEY_SOURCE_VKEY = 2
KEY_SOURCE_APPCOMMAND = 3

PTR_DOWN = 1
PTR_UP = 2
PTR_MOVE = 3

_HEADER = struct.Struct("<HI d")
_U8 = struct.Struct("<B")
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_I16 = struct.Struct("<h")
_U32BE = struct.Struct(">I")


@dataclass
class GamepadState:
    """Estado de um gamepad para um frame de input."""

    index: int = 0
    buttons: int = 0
    left_x: float = 0.0
    left_y: float = 0.0
    right_x: float = 0.0
    right_y: float = 0.0
    left_trigger: float = 0.0
    right_trigger: float = 0.0


@dataclass
class KeyEvent:
    source: int = KEY_SOURCE_VKEY
    pressed: bool = False
    code: int = 0


@dataclass
class MouseEvent:
    x: int = 0
    y: int = 0
    wheel_x: int = 0
    wheel_y: int = 0
    buttons: int = 0
    relative: bool = False


@dataclass
class PointerEvent:
    x: int = 0
    y: int = 0
    pressure: float = 0.0
    twist: int = 0
    kind: int = PTR_MOVE


@dataclass
class VibrationEvent:
    rumble_type: int = 0
    gamepad_index: int = 0
    left_motor: int = 0
    right_motor: int = 0
    left_trigger_motor: int = 0
    right_trigger_motor: int = 0
    duration_ms: int = 0
    delay_ms: int = 0
    repeat: int = 0


@dataclass
class ServerMetadata:
    width: int = 0
    height: int = 0


class Sequence:
    """Contador de sequência dos pacotes (u32 com wrap)."""

    def __init__(self, start: int = 0) -> None:
        self._value = start & 0xFFFFFFFF

    def next(self) -> int:
        current = self._value
        self._value = (self._value + 1) & 0xFFFFFFFF
        return current


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def header(report_type: int, sequence: int, timestamp_ms: Optional[float] = None) -> bytes:
    if timestamp_ms is None:
        timestamp_ms = time.monotonic() * 1000.0
    return _HEADER.pack(report_type & 0xFFFF, sequence & 0xFFFFFFFF, float(timestamp_ms))


def first_packet(max_touch_points: int = 1, sequence: int = 0) -> bytes:
    """Primeiro pacote obrigatório do canal: ClientMetadata + maxTouchpoints."""
    return header(REPORT_CLIENT_METADATA, sequence) + _U8.pack(max_touch_points & 0xFF)


def encode_gamepad(state: GamepadState, sequence: int) -> bytes:
    lx = int(_clamp(state.left_x, -1.0, 1.0) * 32767)
    ly = int(_clamp(-state.left_y, -1.0, 1.0) * 32767)
    rx = int(_clamp(state.right_x, -1.0, 1.0) * 32767)
    ry = int(_clamp(-state.right_y, -1.0, 1.0) * 32767)
    lt = int(_clamp(state.left_trigger, 0.0, 1.0) * 65535)
    rt = int(_clamp(state.right_trigger, 0.0, 1.0) * 65535)
    frame = b"".join(
        [
            _U8.pack(state.index & 0xFF),
            _U16.pack(state.buttons & 0xFFFF),
            _I16.pack(lx),
            _I16.pack(ly),
            _I16.pack(rx),
            _I16.pack(ry),
            _U16.pack(lt),
            _U16.pack(rt),
            _U32.pack(1),
            _U32BE.pack(1),
        ]
    )
    assert len(frame) == 23
    # Todo grupo de frames começa com um u8 de quantidade. Mesmo quando há
    # somente um gamepad, omitir esse byte desloca todos os campos seguintes e
    # o servidor interpreta o índice do controle como a contagem.
    return header(REPORT_GAMEPAD, sequence) + _U8.pack(1) + frame


def encode_keyboard(event: KeyEvent, sequence: int) -> bytes:
    return header(REPORT_KEYBOARD, sequence) + _U8.pack(1) + bytes(
        [event.source & 0xFF, 0x01 if event.pressed else 0x00, event.code & 0xFF]
    )


def encode_mouse(event: MouseEvent, sequence: int) -> bytes:
    frame = b"".join(
        [
            _U32.pack(event.x & 0xFFFFFFFF),
            _U32.pack(event.y & 0xFFFFFFFF),
            _U32.pack(event.wheel_x & 0xFFFFFFFF),
            _U32.pack(event.wheel_y & 0xFFFFFFFF),
            bytes([event.buttons & 0xFF, 0x01 if event.relative else 0x00]),
        ]
    )
    assert len(frame) == 18
    return header(REPORT_MOUSE, sequence) + _U8.pack(1) + frame


def encode_pointer(events: List[PointerEvent], sequence: int) -> bytes:
    frames = []
    for event in events:
        frames.append(
            b"".join(
                [
                    _U16.pack(event.x & 0xFFFF),
                    _U16.pack(event.y & 0xFFFF),
                    _U8.pack(int(_clamp(event.pressure, 0.0, 1.0) * 255)),
                    _U16.pack(event.twist & 0xFFFF),
                    _U32.pack(0),
                    _U32.pack(event.x & 0xFFFFFFFF),
                    _U32.pack(event.y & 0xFFFFFFFF),
                    bytes([event.kind & 0xFF]),
                ]
            )
        )
    # O protocolo aceita um único conjunto de ponteiros por pacote; o primeiro
    # byte conta conjuntos e o segundo conta eventos dentro do conjunto.
    return (
        header(REPORT_POINTER, sequence)
        + _U8.pack(1)
        + _U8.pack(len(frames) & 0xFF)
        + b"".join(frames)
    )


def decode_server_frame(data: bytes) -> Tuple[int, object]:
    """Decodifica um frame vindo do servidor: (report_type, evento).

    Suporta ServerMetadata (dimensões p/ normalizar mouse/toque) e Vibration.
    Frames do servidor têm só o u16 de report no prefixo (sem o header de
    14 bytes do sentido cliente→servidor). Levanta ValueError em payload
    curto ou tipo desconhecido.
    """
    if len(data) < 2:
        raise ValueError(f"frame curto: {len(data)} bytes")
    report_type = _U16.unpack_from(data, 0)[0]
    body = data[2:]
    if report_type == REPORT_SERVER_METADATA:
        if len(body) < 8:
            raise ValueError("ServerMetadata curto")
        height = _U32.unpack_from(body, 0)[0]
        width = _U32.unpack_from(body, 4)[0]
        return report_type, ServerMetadata(width=width, height=height)
    if report_type == REPORT_VIBRATION:
        if len(body) < 11:
            raise ValueError("Vibration curto")
        return report_type, VibrationEvent(
            rumble_type=body[0],
            gamepad_index=body[1],
            left_motor=body[2],
            right_motor=body[3],
            left_trigger_motor=body[4],
            right_trigger_motor=body[5],
            duration_ms=_U16.unpack_from(body, 6)[0],
            delay_ms=_U16.unpack_from(body, 8)[0],
            repeat=body[10],
        )
    raise ValueError(f"report_type não decodificado: {report_type:#x}")


def describe_reports(report_type: int) -> List[str]:
    """Nomes dos reports ativos na bitmask (diagnóstico)."""
    names = {
        REPORT_METADATA: "metadata",
        REPORT_GAMEPAD: "gamepad",
        REPORT_POINTER: "pointer",
        REPORT_CLIENT_METADATA: "client-metadata",
        REPORT_SERVER_METADATA: "server-metadata",
        REPORT_MOUSE: "mouse",
        REPORT_KEYBOARD: "keyboard",
        REPORT_VIBRATION: "vibration",
        REPORT_SENSOR: "sensor",
        REPORT_UNRELIABLE_REPORT: "unreliable-report",
        REPORT_UNRELIABLE_ACK: "unreliable-ack",
    }
    return [name for bit, name in names.items() if report_type & bit]


def queue_limit_ok(queued: int, limit: int = 30) -> bool:
    """O servidor descarta além de ~30 frames: segure o envio se cheio."""
    return queued < limit
