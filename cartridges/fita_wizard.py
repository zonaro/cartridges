# fita_wizard.py
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

"""Onde as fitas de LED são configuradas, uma vez.

O módulo Tuya só aceita comandos de quem tem a chave local dele, e essa chave
não está no app Smart Life — ela sai da conta de desenvolvedor da Tuya, pela
nuvem. Este assistente faz essa viagem uma vez: pede as credenciais da conta,
lista os dispositivos e grava a chave dos que o usuário apontar como fitas.

O Access Secret fica salvo — no cofre do usuário, só para pré-preencher
esta tela na próxima vez, e só depois de uma busca que prova que os dois
códigos são válidos. O dia a dia das fitas nunca usa isso: quem acende uma
fita é a chave local, achada aqui e gravada à parte, em claro, no `fitas.json`.
"""

import logging
import threading
from typing import Any, Optional

from gi.repository import Adw, Gtk

from cartridges import shared
from cartridges.utils import tuya_conta
from cartridges.utils.na_tela import entregar_na_tela
from cartridges.utils.session_fita import (
    Fita,
    devolver_removidas,
    enderecos_na_rede,
    fitas,
    gravar_fitas,
)

# As regiões que a Tuya oferece, na mesma ordem da lista do `.blp`. As duas
# listas precisam andar juntas: o índice escolhido na tela é o índice aqui.
REGIOES = ("us", "eu", "cn", "in")


def credencial_valida(nuvem: Any, resposta: Any) -> bool:
    """A busca provou que os dois códigos funcionam?

    A tinytuya devolve lista vazia também quando o token é recusado (o erro
    fica em `nuvem.error`), então a lista sozinha não prova nada.
    """
    return bool(getattr(nuvem, "token", None)) and not getattr(
        nuvem, "error", None
    ) and isinstance(resposta, list)


# Categorias da nuvem da Tuya que falam cor pelos pontos 20/21/24: fita de LED
# (dd) e lâmpada (dj). O resto da conta — interruptor, tomada, sensor, controle
# IR — não tem o que fazer aqui.
CATEGORIAS = ("dd", "dj")


def fitas_da_nuvem(resposta: Any) -> list[Fita]:
    """Converte a lista que a nuvem devolve em fitas.

    Só fita de LED e lâmpada entram (``CATEGORIAS``); dispositivo sem categoria
    na resposta entra também. Dispositivo sem chave local fica de fora: sem ela
    não há conversa possível.
    Sem IP, entra com o campo vazio — a nuvem devolve o IP público do roteador,
    que não serve para nada aqui. Quem acha o IP da fita é a varredura que este
    assistente roda logo depois (``com_enderecos``), uma vez só; o app nunca
    varre sozinho.

    Aceita qualquer coisa como entrada de propósito: quando a busca falha, a
    ``tinytuya`` devolve um dicionário de erro no lugar da lista, e isso não
    pode virar exceção dentro da thread.
    """
    if not isinstance(resposta, list):
        return []

    encontradas = []
    for item in resposta:
        if not isinstance(item, dict):
            continue
        # Sem categoria entra: resposta que não traz o campo não é prova de
        # que o dispositivo não fala cor.
        categoria = item.get("category")
        if categoria and categoria not in CATEGORIAS:
            continue
        chave = str(item.get("key") or "")
        identificador = str(item.get("id") or "")
        if not chave or not identificador:
            continue
        encontradas.append(
            Fita(
                str(item.get("name") or identificador),
                identificador,
                str(item.get("ip") or ""),
                chave,
                str(item.get("version") or "3.3"),
            )
        )
    return encontradas


def com_enderecos(
    encontradas: list[Fita],
    mapa: dict[str, str],
    versoes: Optional[dict[str, str]] = None,
) -> list[Fita]:
    """Põe em cada fita o endereço que ela tem na rede de casa, e a versão do
    protocolo que ela anunciou.

    A nuvem não sabe o IP local das fitas; quem sabe é a varredura por
    broadcast, que roda uma única vez, aqui. O endereço achado fica gravado e o
    app não procura mais: o IP das fitas é fixo, e se um dia mudar é só rodar
    este assistente de novo. A fita que a varredura não achou mantém o endereço
    que já tinha na configuração, se tinha um.

    A versão vem da mesma varredura: a nuvem costuma não dizê-la, e um módulo
    3.4 ou 3.5 falado como 3.3 nunca responde.
    """
    configuradas = {fita.id: fita for fita in fitas()}
    versoes = versoes or {}
    return [
        fita._replace(
            ip=mapa.get(fita.id)
            or fita.ip
            or getattr(configuradas.get(fita.id), "ip", ""),
            versao=versoes.get(fita.id) or fita.versao,
            # A nuvem não sabe do brilho por dispositivo: ele é daqui.
            brilho=getattr(configuradas.get(fita.id), "brilho", fita.brilho),
        )
        for fita in encontradas
    ]


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/fita-wizard.ui")
class FitaWizard(Adw.Dialog):
    __gtype_name__ = "FitaWizard"

    stack: Adw.ViewStack = Gtk.Template.Child()
    api_key_row: Adw.EntryRow = Gtk.Template.Child()
    api_secret_row: Adw.PasswordEntryRow = Gtk.Template.Child()
    regiao_row: Adw.ComboRow = Gtk.Template.Child()
    buscar_button: Gtk.Button = Gtk.Template.Child()
    aviso_label: Gtk.Label = Gtk.Template.Child()
    dispositivos_group: Adw.PreferencesGroup = Gtk.Template.Child()
    salvar_button: Gtk.Button = Gtk.Template.Child()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._linhas: list[tuple[Adw.SwitchRow, Fita]] = []
        # A busca corre numa thread e volta depois. Se o usuário fechou o
        # assistente nesse meio-tempo, não há tela para pintar — é a mesma
        # proteção que o sgdb_picker, o steam_picker e o logo_picker usam.
        self._closed = False
        self.buscar_button.connect("clicked", self.buscar)
        self.salvar_button.connect("clicked", self.salvar)
        self.connect("closed", self._on_closed)
        self._preencher_credenciais()

    def _on_closed(self, *_args: Any) -> None:
        self._closed = True

    def _preencher_credenciais(self) -> None:
        """Pré-preenche com a conta salva da vez anterior, se houver."""
        conta = tuya_conta.carregar()
        if conta is None:
            return
        self.api_key_row.set_text(conta.chave)
        self.api_secret_row.set_text(conta.segredo)
        try:
            indice = REGIOES.index(conta.regiao)
        except ValueError:
            indice = 0
        self.regiao_row.set_selected(indice)

    def buscar(self, *_args: Any) -> None:
        """Vai à nuvem numa thread; a Secret não sai desta chamada."""
        chave = self.api_key_row.get_text().strip()
        segredo = self.api_secret_row.get_text().strip()
        escolhida = self.regiao_row.get_selected()
        regiao = REGIOES[escolhida] if escolhida < len(REGIOES) else REGIOES[0]
        if not chave or not segredo:
            return

        self.stack.set_visible_child_name("buscando")
        threading.Thread(
            target=self._buscar_na_nuvem, args=(chave, segredo, regiao), daemon=True
        ).start()

    def _buscar_na_nuvem(self, chave: str, segredo: str, regiao: str) -> None:
        try:
            # Dentro do try: sem a biblioteca instalada, um ImportError aqui
            # deixaria a tela presa no "Buscando…" para sempre.
            import tinytuya  # noqa: PLC0415

            nuvem = tinytuya.Cloud(apiRegion=regiao, apiKey=chave, apiSecret=segredo)
            resposta = nuvem.getdevices()
            # A tinytuya devolve lista vazia também quando o token é recusado
            # (o erro fica em `nuvem.error`), então só a lista não prova que a
            # credencial funcionou — e uma credencial que não funcionou não
            # merece ficar salva para a próxima vez.
            if credencial_valida(nuvem, resposta):
                tuya_conta.salvar(tuya_conta.Conta(chave, segredo, regiao))
            encontrados = fitas_da_nuvem(resposta)
        except Exception as erro:  # a tinytuya levanta de tudo aqui também
            logging.warning("Busca na nuvem da Tuya falhou: %s", erro)
            encontrados = []
        entregar_na_tela(
            self._mostrar, com_enderecos(encontrados, *enderecos_na_rede())
        )

    def _mostrar(self, encontrados: list[Fita]) -> None:
        if self._closed:
            return

        for linha, _fita in self._linhas:
            self.dispositivos_group.remove(linha)
        self._linhas = []

        # Busca vazia fica nas credenciais, com o aviso ali. A página dos
        # dispositivos só tem o botão "Aplicar", e aplicar uma lista vazia
        # apagaria as fitas já configuradas — num clique, e sem caminho de
        # volta para corrigir a credencial errada.
        if not encontrados:
            self.aviso_label.set_visible(True)
            self.stack.set_visible_child_name("credenciais")
            return

        self.aviso_label.set_visible(False)
        ja_configuradas = {fita.id for fita in fitas()}
        for fita in encontrados:
            linha = Adw.SwitchRow(
                title=fita.nome,
                subtitle=fita.ip or _("Não encontrado na rede. Verifique se o dispositivo está ligado."),
            )
            # Sem endereço não há como falar com a fita: marcá-la seria gravar
            # uma fita que nunca acende.
            linha.set_sensitive(bool(fita.ip))
            linha.set_active(bool(fita.ip) and fita.id in ja_configuradas)
            self.dispositivos_group.add(linha)
            self._linhas.append((linha, fita))

        self.stack.set_visible_child_name("dispositivos")

    def salvar(self, *_args: Any) -> None:
        escolhidas = [fita for linha, fita in self._linhas if linha.get_active()]
        marcadas = {fita.id for fita in escolhidas}
        # Quem sai da configuração sai antes de o arquivo mudar: depois da
        # gravação o app não conhece mais essa fita, não tem como devolvê-la ao
        # estado de antes, e ela ficaria na cor do Cartridges para sempre.
        saindo = [fita for fita in fitas() if fita.id not in marcadas]
        gravar_fitas(escolhidas)
        if saindo:
            # Rede: fora da thread de UI, como todo o resto da conversa.
            threading.Thread(
                target=devolver_removidas, args=(saindo,), daemon=True
            ).start()
        self.close()
