#!/usr/bin/env python3
"""Gera version.py a partir de version.py.in com a versao de compilacao.

Usado pelo custom_target em cartridges/meson.build com build_always_stale,
portanto executa a cada `meson compile` e reflete a data de compilacao
(YY.DDD.HHMM, ano 2 digitos, dia do ano, hora+minuto 24h), nao a do
`meson setup`. Respeita JOLVEN_VERSION/CARTRIDGES_VERSION como get-version.py.

Uso: write-version.py <template-in> <output-py>
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

VERSION_RE = re.compile(r"^\d{2}\.\d{3}\.\d{4}$")


def build_version() -> str:
    override = os.environ.get("JOLVEN_VERSION", "").strip() or os.environ.get(
        "CARTRIDGES_VERSION", ""
    ).strip()
    if override:
        version = override
    else:
        version = datetime.now().strftime("%y.%j.%H%M")
    if not VERSION_RE.match(version):
        print(
            f"versao invalida: {version!r} (esperado YY.DDD.HHMM, ex: 26.278.1935)",
            file=sys.stderr,
        )
        sys.exit(1)
    return version


def main() -> None:
    template = Path(sys.argv[1])
    output = Path(sys.argv[2])
    content = template.read_text(encoding="utf-8")
    content = content.replace("@VCS_TAG@", build_version())
    output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
