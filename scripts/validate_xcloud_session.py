#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Valida o handshake de sessão xCloud/xHome (signalling, sem mídia).

Recebe um LoginResult válido, lista títulos, abre a sessão de um titleId (ou de
um console no xHome), espera `Provisioned`, conecta com o LPT, lê a
`/configuration`, opcionalmente exercita a troca de SDP e de ICE e — sempre —
encerra a sessão com DELETE.

    scripts/validate_xcloud_session.py --title-id <GUID>
    scripts/validate_xcloud_session.py --server-id <LiveId>   # xHome

Requer interação real no passo de device code (--device-code). Nunca imprime
tokens, LPT ou a chave SRTP: apenas tamanhos.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from cartridges.xcloud_native import auth, session
except Exception as e:  # pragma: no cover - depende do ambiente
    print(f"ERRO: Não foi possível importar xcloud_native: {e}", file=sys.stderr)
    sys.exit(1)


def _login(args: argparse.Namespace) -> auth.LoginResult:
    if args.device_code:
        device = auth.begin_device_flow()
        print(f"\n1. Autorize em {device['verification_uri']}")
        print(f"   código: {device['user_code']}")
        return auth.complete_device_flow(device)
    return auth.refresh_streaming_tokens()


def _report_login(result: auth.LoginResult) -> None:
    streaming = result.streaming
    print("\nLOGIN OK:")
    print(f"- offering: {streaming.offering}")
    print(f"- market: {streaming.market}")
    print(f"- regiões: {[r.get('name') for r in streaming.regions]}")
    print(f"- gs_token_len: {len(streaming.gs_token)}")
    print(f"- lpt_len: {len(result.lpt)}")


def _list_titles(base: str, gs_token: str, args: argparse.Namespace) -> None:
    page = session.list_titles(base, gs_token)
    print(f"\nTÍTULOS: {len(page.titles)} de {page.total_items}")
    for title in page.titles:
        print(f"- {title.title_id}  product={title.product_id}")
    if args.mru:
        recent = session.list_mru(base, gs_token)
        print(f"MRU: {len(recent.titles)}")


def _report_configuration(prepared: session.PreparedSession) -> None:
    configuration = prepared.configuration
    details = configuration.server_details
    print("\nCONFIGURATION OK:")
    print(f"- keepAlivePulseInSeconds: {configuration.keepalive_pulse_in_seconds}")
    print(f"- ip:port: {details.ip_address}:{details.port}")
    print(f"- stun: {details.stun_server_address or '(ausente)'}")
    print(f"- iceExchangePath: {details.ice_exchange_path or '(ausente)'}")
    print(f"- srtp_key_len: {len(details.srtp_key)}")
    print(f"- ice em uso: {session.ice_exchange_path(prepared)}")


def _exercise_signalling(
    prepared: session.PreparedSession, gs_token: str, args: argparse.Namespace
) -> None:
    if args.offer_sdp_file:
        offer = Path(args.offer_sdp_file).read_text(encoding="utf-8")
        answer = session.exchange_sdp(
            prepared.base,
            gs_token,
            prepared.session_path,
            offer,
            request_id=args.request_id,
        )
        print("\nSDP OK:")
        print(f"- message_type: {answer.message_type}")
        print(f"- status: {answer.status}")
        print(f"- versões control/input: {answer.control}/{answer.input_version}")
        print(f"- answer_len: {len(answer.sdp)}")

    if args.ice_candidate:
        remote = session.exchange_ice(
            prepared.base,
            gs_token,
            session.ice_exchange_path(prepared),
            args.ice_candidate,
        )
        print("\nICE OK:")
        print(f"- locais enviados: {len(args.ice_candidate)}")
        print(f"- remotos recebidos: {len(remote)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida o handshake de sessão xCloud/xHome (signalling)"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--device-code", action="store_true",
        help="Login interativo via device code (padrão: refresh do token guardado)",
    )
    source.add_argument(
        "--refresh", action="store_true",
        help="Usa o refresh_token guardado (comportamento padrão)",
    )
    parser.add_argument("--title-id", help="GUID do título no xCloud")
    parser.add_argument("--server-id", help="LiveId do console no xHome")
    parser.add_argument("--region", default="", help="Nome da região (padrão: isDefault)")
    parser.add_argument("--mru", action="store_true", help="Lista também o MRU")
    parser.add_argument(
        "--timeout", type=float, default=session.PROVISION_TIMEOUT_SEC,
        help=f"Timeout de provisioning em segundos (padrão {session.PROVISION_TIMEOUT_SEC:g})",
    )
    parser.add_argument(
        "--offer-sdp-file", help="Arquivo com um SDP offer para testar a troca",
    )
    parser.add_argument(
        "--ice-candidate", action="append", default=[],
        help="Candidato ICE local (repetível) para testar o POST /ice",
    )
    parser.add_argument("--request-id", type=int, default=1, help="requestId do offer")
    args = parser.parse_args()

    if not args.title_id and not args.server_id:
        parser.error("informe --title-id (xCloud) ou --server-id (xHome)")

    streaming: auth.StreamingTokens | None = None
    prepared: session.PreparedSession | None = None

    try:
        result = _login(args)
        _report_login(result)

        streaming = result.streaming
        base = session.resolve_base_uri(streaming, region_name=args.region)
        platform = session.platform_for_login(streaming)
        print(f"\nRegião: {args.region or '(padrão)'} -> {base} ({platform})")

        _list_titles(base, streaming.gs_token, args)

        if args.title_id:
            wait = session.waittime(base, streaming.gs_token, args.title_id)
            print(f"\nEspera estimada: {wait.total_seconds}s")

        print(f"\nAbrindo sessão ({platform})...")
        prepared = session.open_session_for_login(
            result,
            title_id=args.title_id,
            server_id=args.server_id,
            region_name=args.region,
            timeout=args.timeout,
        )
        print(f"- session_id: {prepared.session_id}")
        print(f"- state: {prepared.state}")

        _report_configuration(prepared)
        _exercise_signalling(prepared, streaming.gs_token, args)

        pulse = session.keepalive(base, streaming.gs_token, prepared.session_path)
        print(f"\nKeepalive: alive_seconds={pulse.alive_seconds} reason={pulse.reason}")

        print("\nSUCESSO: handshake de sessão validado")
        return 0
    except Exception as e:
        print(f"\nFALHA: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if prepared is not None and streaming is not None:
            try:
                session.stop(prepared.base, streaming.gs_token, prepared.session_path)
                print("\nSessão encerrada (DELETE)")
            except Exception as e:
                print(f"\nAVISO: falha ao encerrar a sessão: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())