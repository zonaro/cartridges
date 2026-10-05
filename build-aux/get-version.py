#!/usr/bin/env python3
"""Imprime a versao do Jolven no formato YY.DDD.HHMM.

DDD e o dia do ano (1-366) e HHMM sao hora e minuto (24h) do momento
da compilacao. Quando a variavel de ambiente JOLVEN_VERSION (ou a legada
CARTRIDGES_VERSION) esta definida, ela e usada como-is (apos validacao)
para que o script de publicacao congele uma unica versao para build,
tag e release.
"""

import os
import re
import sys
from datetime import datetime

VERSION_RE = re.compile(r"^\d{2}\.\d{3}\.\d{4}$")


def build_version() -> str:
    override = os.environ.get("JOLVEN_VERSION", "").strip() or os.environ.get(
        "CARTRIDGES_VERSION", ""
    ).strip()
    if override:
        version = override
    else:
        # %y = ano com 2 digitos, %j = dia do ano (001-366), %H%M = hora+minuto 24h
        version = datetime.now().strftime("%y.%j.%H%M")
    if not VERSION_RE.match(version):
        print(
            f"versao invalida: {version!r} (esperado YY.DDD.HHMM, ex: 26.278.1935)",
            file=sys.stderr,
        )
        sys.exit(1)
    return version


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
    else:
        print(build_version())
