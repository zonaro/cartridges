# xcloud_deps.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependências de sistema do player nativo do xCloud (GStreamer WebRTC/ICE).

Detecta a cadeia completa de WebRTC/SCTP, RTP, codecs e sinks e instala via o
helper privilegiado `jolven-xcloud-deps` (pkexec abre o diálogo de senha do
sistema). Sem GTK aqui: o chamador entrega o resultado à UI.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

REQUIRED_ELEMENTS = (
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
H264_DECODERS = ("vah264dec", "nvh264dec", "avdec_h264", "openh264dec")
HELPER_OPERATION = "install-xcloud-deps"


def build_restart_command(argv: List[str]) -> List[str]:
    """Relança o app após 2s via shell destacada (o processo atual sai antes).

    Puro e testável: recebe argv, devolve a lista para Popen.
    """
    relauncher = "sleep 2; exec " + " ".join(shlex.quote(arg) for arg in argv)
    return ["setsid", "nohup", "sh", "-c", relauncher]

try:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst as _Gst
except (ImportError, ValueError):
    _Gst = None  # type: ignore[assignment]


def _shared_libexecdir() -> Optional[str]:
    try:
        from cartridges import shared  # noqa: PLC0415

        return str(shared.LIBEXECDIR)
    except Exception as error:
        logger.debug("shared indisponível para localizar helper: %s", error)
        return None


def _helper_path() -> Optional[Path]:
    libexecdir = _shared_libexecdir()
    if libexecdir:
        candidate = Path(libexecdir) / "jolven-xcloud-deps"
        if candidate.is_file():
            return candidate
    repo_session = Path(__file__).resolve().parents[1] / "session"
    for name in ("jolven-xcloud-deps", "jolven-xcloud-deps.in"):
        fallback = repo_session / name
        if fallback.is_file():
            return fallback
    which = shutil.which("jolven-xcloud-deps")
    return Path(which) if which else None


def missing_elements(elements: Tuple[str, ...] = REQUIRED_ELEMENTS) -> List[str]:
    """Elementos GStreamer ausentes. Lista vazia = tudo presente."""
    if _Gst is None:
        return list(elements)
    try:
        _Gst.init(None)
        registry = _Gst.Registry.get()
        missing = [
            name
            for name in elements
            if registry.find_feature(name, _Gst.ElementFactory) is None
        ]
        if elements == REQUIRED_ELEMENTS and not any(
            registry.find_feature(name, _Gst.ElementFactory) is not None
            for name in H264_DECODERS
        ):
            missing.append("h264-decoder")
        return missing
    except Exception as error:
        logger.debug("Falha sondando GStreamer: %s", error)
        return list(elements)


def policy_registered() -> bool:
    """True se a action polkit do helper existe no sistema."""
    for base in ("/usr/share/polkit-1/actions", "/etc/polkit-1/actions"):
        matches = list(Path(base).glob("*XCloudDeps.policy"))
        if matches:
            return True
    return False


def install_async(
    callback: Callable[[bool, str], None],
    on_output: Optional[Callable[[str], None]] = None,
) -> bool:
    """Roda o helper via pkexec numa thread; callback(ok, mensagem) no fim.

    on_output(linha) recebe cada linha do helper (thread worker — o chamador
    entrega à UI). Retorna False se nem tentou (helper ausente) — nesse caso
    o callback não é chamado e o chamador deve orientar o setup manual.
    """
    helper = _helper_path()
    if helper is None:
        return False
    pkexec = shutil.which("pkexec")
    if pkexec is None:
        callback(False, "pkexec indisponível neste sistema")
        return True

    def work() -> None:
        proc = None
        try:
            proc = subprocess.Popen(
                [pkexec, str(helper), HELPER_OPERATION],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            lines: List[str] = []
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                lines.append(line)
                if on_output is not None:
                    try:
                        on_output(line)
                    except Exception:
                        pass
            code = proc.wait(timeout=600)
            message = lines[-1] if lines else ""
            if code == 0:
                callback(True, message or "Dependências instaladas.")
            elif "dismissed" in message.lower() or "cancel" in message.lower():
                callback(False, "Instalação cancelada.")
            else:
                callback(False, message or f"Falha na instalação (código {code}).")
        except subprocess.TimeoutExpired:
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
            callback(False, "Tempo esgotado instalando dependências.")
        except Exception as error:
            callback(False, f"Não foi possível instalar: {error}")

    threading.Thread(target=work, daemon=True, name="xcloud-deps").start()
    return True
