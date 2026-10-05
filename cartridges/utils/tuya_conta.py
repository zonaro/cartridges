# tuya_conta.py
#
# Copyright 2026 joaomgabaldi
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A conta de nuvem da Tuya, salva para o assistente não pedir de novo.

O Access ID e o Access Secret vêm do painel de desenvolvedor da Tuya — não do
app Smart Life — e só servem para a busca inicial de dispositivos; o dia a dia
das fitas fala direto com a chave local, gravada à parte em `fitas.json`. Sem
isto salvo, todo IP trocado ou fita nova obrigaria a voltar ao painel da Tuya
para pegar os dois códigos de novo.

Guardados no Secret Service (o cofre do usuário), que só esse usuário
desbloqueia — o mesmo modelo de um navegador guardando senha salva. O segredo
mora no cofre e não na pasta do app, então o backup não o leva: numa
instalação nova, `carregar` devolve ``None`` e o assistente pede os códigos
de novo.
"""

import json
import logging
from typing import NamedTuple, Optional

from gi.repository import Secret

_SCHEMA = Secret.Schema.new(
    "page.redclaw.Cartridges.tuya",
    Secret.SchemaFlags.NONE,
    {"app": Secret.SchemaAttributeType.STRING},
)
_ATRIBUTOS = {"app": "cartridges"}
_ROTULO = "Cartridges: conta da Tuya"


class Conta(NamedTuple):
    """As credenciais da nuvem, do jeito que a tela do assistente as usa."""

    chave: str
    segredo: str
    regiao: str


def salvar(conta: Conta) -> None:
    """Grava as credenciais no cofre. Nunca levanta."""
    try:
        Secret.password_store_sync(
            _SCHEMA,
            _ATRIBUTOS,
            Secret.COLLECTION_DEFAULT,
            _ROTULO,
            json.dumps(conta._asdict()),
            None,
        )
    except Exception as erro:
        logging.warning("Não foi possível guardar as credenciais da Tuya: %s", erro)


def carregar() -> Optional[Conta]:
    """As credenciais salvas, ou ``None``.

    ``None`` sem nada no cofre, sem cofre alcançável, ou com o segredo
    corrompido — em todos os casos o assistente volta a pedir os códigos,
    como se nada tivesse sido salvo.
    """
    try:
        segredo = Secret.password_lookup_sync(_SCHEMA, _ATRIBUTOS, None)
    except Exception as erro:
        logging.warning("Cofre da Tuya ilegível: %s", erro)
        return None
    if not segredo:
        return None
    try:
        bruto = json.loads(segredo)
        return Conta(str(bruto["chave"]), str(bruto["segredo"]), str(bruto["regiao"]))
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        logging.warning("Credenciais da Tuya salvas, mas ilegíveis")
        return None
