#!/usr/bin/env python3
"""Script de validação da autenticação xCloud nativa (passos A–F).

ATENÇÃO: Este script precisa de interação real com usuário (após passo A)
para autorizar no navegador. Nunca imprime valores de tokens.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

try:
    from cartridges.xcloud_native import auth
except Exception as e:
    print(f"ERRO: Não foi possível importar auth: {e}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida fluxo xCloud auth (A–F)")
    parser.add_argument("--device-code", action="store_true", help="Executa fluxo completo via device code (interativo)")
    parser.add_argument("--refresh", action="store_true", help="Tenta refresh com token armazenado")
    parser.add_argument("--browser", action="store_true", help="Testa login_with_browser_tokens (requer --rt)")
    parser.add_argument("--rt", help="refresh_token para teste browser", default=None)
    parser.add_argument("--at", help="access_token para teste browser", default=None)
    args = parser.parse_args()

    if args.refresh:
        try:
            result = auth.refresh_streaming_tokens()
            st = result.streaming
            print("SUCESSO: refresh_streaming_tokens() OK")
            print(f"- offering: {st.offering}")
            print(f"- market: {st.market}")
            print(f"- regions_count: {len(st.regions)}")
            print(f"- expires_at: {st.expires_at.isoformat().replace('+00:00','Z')}")
            print(f"- lpt_len: {len(result.lpt)}")
            return 0
        except Exception as e:
            print(f"FALHA: {e}", file=sys.stderr)
            return 1

    if args.browser:
        if not args.rt and not args.at:
            print("ERRO: --browser requer --rt e/ou --at", file=sys.stderr)
            return 1
        try:
            result = auth.login_with_browser_tokens(args.at or "", refresh_token=args.rt)
            st = result.streaming
            print("SUCESSO: login_with_browser_tokens() OK")
            print(f"- offering: {st.offering}")
            print(f"- market: {st.market}")
            print(f"- regions_count: {len(st.regions)}")
            print(f"- lpt_len: {len(result.lpt)}")
            return 0
        except Exception as e:
            print(f"FALHA: {e}", file=sys.stderr)
            return 1

    if args.device_code:
        try:
            dc = auth.begin_device_flow()
            print("PASSO A OK:")
            print(f"- verification_uri: {dc['verification_uri']}")
            print(f"- user_code: {dc['user_code']}")
            print(f"- expires_in: {dc['expires_in']}")
            print(f"- interval: {dc['interval']}")
            print("\nAcesse a URL e insira o código. Aguarde autorização...")
            result = auth.complete_device_flow(dc)
            st = result.streaming
            print("\nPASSOS B–F OK:")
            print(f"- offering: {st.offering}")
            print(f"- market: {st.market}")
            print(f"- regions_count: {len(st.regions)}")
            print(f"- expires_at: {st.expires_at.astimezone(timezone.utc).isoformat().replace('+00:00','Z')}")
            print(f"- lpt_len: {len(result.lpt)}")
            print("SUCESSO: fluxo completo validado")
            return 0
        except Exception as e:
            print(f"FALHA: {e}", file=sys.stderr)
            return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
