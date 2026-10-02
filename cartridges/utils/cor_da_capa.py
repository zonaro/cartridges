# cor_da_capa.py
#
# Copyright 2026 joaomgabaldi
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A cor que resume a capa de um jogo, para vestir as fitas de LED.

Dominante aqui não é "a que ocupa mais pixels": numa capa a cor mais comum
costuma ser o preto do fundo ou o cinza de uma moldura, e nenhum dos dois diz
nada sobre o jogo. O que vence é a mais viva entre as que aparecem bastante —
frequência vezes saturação —, e cinzas, pretos e brancos nem entram na conta.

Só matiz e saturação saem daqui. O brilho é do usuário: ele vem do jogo ou do
padrão das Preferências, nunca da imagem.
"""

import colorsys
import logging
from pathlib import Path
from typing import Optional

from PIL import Image

# Abaixo disto a cor é um cinza (pouca saturação) ou quase preta (pouco valor),
# e nos dois casos ela viraria uma luz sem graça na parede.
MIN_SATURACAO = 0.25
MIN_VALOR = 0.15

# A capa cabe em 64x64 para a conta: a cor dominante não muda com o tamanho, e
# oito cores são suficientes para separar o fundo do que salta aos olhos.
AMOSTRA = (64, 64)
CORES = 8


def dominante(capa: Path) -> Optional[tuple[int, int]]:
    """Matiz (0–359) e saturação (0–1000) da capa, ou ``None`` sem cor útil.

    Nunca levanta: quem chama está na thread de UI, e um enfeite de sessão não
    pode derrubar a tela.
    """
    try:
        with Image.open(capa) as imagem:
            amostra = imagem.convert("RGB").resize(AMOSTRA)
    # A captura é larga de propósito. As capas vêm de fora — download da
    # SteamGridDB, da Wallhaven, arquivo que o usuário escolheu — e a Pillow
    # tem um jeito diferente de reclamar de cada defeito: arquivo truncado,
    # formato estranho, imagem gigante (``DecompressionBombError``, que nem
    # herda de ``OSError``). Listar as exceções seria apostar que a lista está
    # completa; qualquer uma delas quer dizer a mesma coisa, "sem cor útil".
    except Exception as erro:
        logging.info("Capa sem cor legível (%s): %s", capa.name, erro)
        return None

    paleta = amostra.quantize(colors=CORES, method=Image.Quantize.FASTOCTREE)
    contagens = paleta.convert("RGB").getcolors(AMOSTRA[0] * AMOSTRA[1]) or []

    melhor: Optional[tuple[float, float]] = None
    melhor_peso = 0.0
    for quantidade, (vermelho, verde, azul) in contagens:
        matiz, saturacao, valor = colorsys.rgb_to_hsv(
            vermelho / 255, verde / 255, azul / 255
        )
        if saturacao < MIN_SATURACAO or valor < MIN_VALOR:
            continue
        peso = quantidade * saturacao
        if peso > melhor_peso:
            melhor, melhor_peso = (matiz, saturacao), peso

    if melhor is None:
        return None
    return round(melhor[0] * 360) % 360, round(melhor[1] * 1000)
