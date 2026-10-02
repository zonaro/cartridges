# ler_json.py
#
# Copyright 2026 joaomgabaldi
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""JSON do próprio app, lido mesmo quando a gravação de outra thread cruza.

Os arquivos do app são gravados por temporário + ``replace``, às vezes numa
thread (a busca do logo, a do papel de parede). No Windows, abrir o arquivo no
instante da troca dá ``PermissionError``: não é arquivo ilegível, é só o
momento errado, e a leitura seguinte passa.
"""

import json
import time
from pathlib import Path
from typing import Any

_TENTATIVAS = 3
_PAUSA = 0.02


def ler_json(caminho: Path) -> Any:
    """``json.load`` de ``caminho``. Levanta como ``open``/``json.load``."""
    for _tentativa in range(_TENTATIVAS - 1):
        try:
            with caminho.open(encoding="utf-8") as arquivo:
                return json.load(arquivo)
        except PermissionError:
            time.sleep(_PAUSA)
    with caminho.open(encoding="utf-8") as arquivo:
        return json.load(arquivo)
