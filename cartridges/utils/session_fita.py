# session_fita.py
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

"""As fitas de LED atrás dos monitores, acompanhando o app e a sessão.

O ciclo tem quatro momentos: o app abre e as fitas acendem na cor dele, o jogo
abre e elas vestem a cor daquele jogo, o jogo fecha e elas voltam à do app, o app
fecha e elas voltam exatamente ao que eram antes de tudo — inclusive apagadas.

O estado de antes fica no GSettings, e não em memória, pela mesma razão do papel
de parede de sessão: o app morto no meio da sessão deixaria as três fitas
vestidas de um jogo que já acabou, e só o arranque seguinte pode desfazer isso.

A conversa é local. A nuvem da Tuya entra uma única vez, no assistente, para
buscar a chave de cada módulo; daí em diante é o PC falando direto com a fita.
"""

import colorsys
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, NamedTuple, Optional, TYPE_CHECKING

from cartridges import shared
from cartridges.utils.cor_da_capa import dominante
from cartridges.utils.ler_json import ler_json

if TYPE_CHECKING:
    from cartridges.game import Game

# Matiz e saturação do #9141ac, o roxo da paleta do app. É o padrão da "cor do
# app" — a das fitas quando não há jogo correndo —, que o usuário pode trocar
# nas Preferências. O brilho não entra: ele é do usuário.
ROXO_DO_APP = (284, 620)


class Fita(NamedTuple):
    """Um módulo Tuya, do jeito que ele fica no `fitas.json`."""

    nome: str
    id: str
    ip: str
    key: str
    versao: str = "3.3"
    # O teto deste dispositivo, em porcentagem: o brilho do app e o de cada
    # jogo valem sobre ele. Um brilho de 50% num dispositivo em 50 acende a 25.
    brilho: int = 100


class Cor(NamedTuple):
    """Matiz (0–359), saturação (0–1000) e brilho (0–1000)."""

    matiz: int
    saturacao: int
    brilho: int


def _gravar_json(caminho: Path, dados: dict[str, Any]) -> None:
    """Grava um JSON num temporário e troca, para não perder o arquivo se a
    energia cair no meio.

    Nunca levanta: quem chama está na thread de UI, e um enfeite de sessão não
    pode derrubar a tela. A pasta entra no mesmo ``try`` da escrita porque ela
    falha pelo mesmo motivo — permissão negada, disco cheio, caminho ocupado.
    """
    temporario = caminho.with_name(caminho.name + ".tmp")
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario.write_text(json.dumps(dados), encoding="utf-8")
        temporario.replace(caminho)
    except OSError as erro:
        logging.warning("Não foi possível gravar %s: %s", caminho.name, erro)


# region Configuração


def fitas() -> list[Fita]:
    """As fitas configuradas. Lista vazia quando não há configuração válida."""
    try:
        dados = json.loads(shared.fitas_arquivo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as erro:
        logging.warning("Lista de fitas ilegível (%s): %s", shared.fitas_arquivo.name, erro)
        return []
    if not isinstance(dados, dict):
        return []
    achadas = []
    for item in dados.get("fitas", []):
        try:
            achadas.append(
                Fita(
                    str(item["nome"]),
                    str(item["id"]),
                    str(item["ip"]),
                    str(item["key"]),
                    str(item.get("versao", "3.3")),
                    _teto(item.get("brilho")),
                )
            )
        except (KeyError, TypeError):
            logging.warning("Fita ignorada por estar incompleta no arquivo")
    return achadas


def _teto(valor: Any) -> int:
    """O brilho por dispositivo lido do arquivo. Valor mexido à mão vale 100,
    e não tira o dispositivo da lista."""
    try:
        return max(1, min(100, int(valor)))
    except (TypeError, ValueError):
        return 100


def gravar_fitas(lista: list[Fita]) -> None:
    """Grava a configuração das fitas."""
    conteudo = {"fitas": [fita._asdict() for fita in lista]}
    _gravar_json(shared.fitas_arquivo, conteudo)


# endregion
# region Cor por jogo


def _sidecar(game_id: str):
    return shared.fitas_dir / f"{game_id}.json"


def _ler_sidecar(game_id: str) -> Optional[dict[str, Any]]:
    try:
        dados = ler_json(_sidecar(game_id))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as erro:
        logging.warning("Cor da fita ilegível (%s): %s", _sidecar(game_id).name, erro)
        return None
    return dados if isinstance(dados, dict) else None


def escolhida(game_id: str) -> bool:
    """Se a cor deste jogo foi escolhida por gente, e não pela capa."""
    dados = _ler_sidecar(game_id)
    return bool(dados and dados.get("locked"))


def cor_escolhida(game_id: str) -> Optional[Cor]:
    """A cor manual gravada para ``game_id``, ou ``None`` se a cor for
    automática (segue a capa). Usado pela exportação do backup."""
    dados = _ler_sidecar(game_id)
    if not dados or not dados.get("locked"):
        return None
    try:
        return Cor(int(dados["matiz"]), int(dados["saturacao"]), int(dados["brilho"]))
    except (KeyError, TypeError, ValueError):
        return None


def salvar_cor(game_id: str, name: str, cor: Cor) -> None:
    """Guarda a escolha manual de um jogo.

    Pelo temporário como o resto: um sidecar truncado por uma queda no meio da
    escrita é lido como corrompido, e o jogo voltaria em silêncio para a cor
    automática — o usuário perderia a escolha sem nenhum aviso.
    """
    dados = {
        "name": name,
        "matiz": cor.matiz,
        "saturacao": cor.saturacao,
        "brilho": cor.brilho,
        "locked": True,
        "timestamp": int(time.time()),
    }
    _gravar_json(_sidecar(game_id), dados)


def redefinir(game_id: str) -> None:
    """Devolve o jogo à cor automática."""
    try:
        _sidecar(game_id).unlink(missing_ok=True)
    except OSError as erro:
        logging.warning("Não foi possível apagar a cor da fita: %s", erro)


# O módulo conta o brilho de 0 a 1000; gente conta de 0 a 100. A conversão mora
# aqui, num lugar só, e as telas falam sempre em porcentagem.
BRILHO_CHEIO = 1000

# O mínimo que ainda acende. Abaixo disto o módulo apaga, e "1%" na tela tem de
# continuar sendo luz.
BRILHO_MINIMO = 10


def por_cento(brilho: int) -> int:
    """O brilho do módulo (0–1000) como a tela mostra (0–100)."""
    return round(brilho * 100 / BRILHO_CHEIO)


def de_por_cento(valor: float) -> int:
    """O caminho de volta, sem deixar a fita pedir um brilho que apaga."""
    return max(BRILHO_MINIMO, min(BRILHO_CHEIO, round(valor * BRILHO_CHEIO / 100)))


def brilho_padrao() -> int:
    return shared.schema.get_int("fita-brilho-padrao")


def na_fita(cor: Cor, fita: Fita, teto: Optional[int] = None) -> Cor:
    """A cor com o brilho proporcional ao teto do dispositivo.

    ``teto`` troca o do arquivo, para a prévia da janela de brilho por
    dispositivo mostrar o que ainda não foi salvo.
    """
    teto = fita.brilho if teto is None else teto
    return cor._replace(brilho=max(BRILHO_MINIMO, round(cor.brilho * teto / 100)))


def cor_do_jogo(game: "Game", ignorar_escolha: bool = False) -> Cor:
    """A cor que este jogo veste: a escolhida, a da capa, ou o roxo do app.

    ``ignorar_escolha`` pula o sidecar e devolve o automático mesmo havendo
    escolha gravada. É o que a tela de detalhes precisa mostrar depois do clique
    em "voltar ao automático": ali a escolha ainda está em disco, e só o Aplicar
    a apaga.
    """
    dados = None if ignorar_escolha else _ler_sidecar(game.game_id)
    if dados and dados.get("locked"):
        # Sidecar mexido à mão com tipo errado (``"matiz": null``) não pode
        # levantar aqui: isto roda na thread de UI, abrindo os detalhes. Vale o
        # automático, como para o sidecar corrompido.
        try:
            return Cor(
                int(dados.get("matiz", tom_do_app()[0])),
                int(dados.get("saturacao", tom_do_app()[1])),
                int(dados.get("brilho", brilho_padrao())),
            )
        except (TypeError, ValueError):
            logging.warning("Cor da fita de %s ilegível; vale a automática", game.game_id)

    capa = game.get_cover_path()
    da_capa = dominante(capa) if capa else None
    matiz, saturacao = da_capa if da_capa else tom_do_app()
    return Cor(matiz, saturacao, brilho_padrao())


def cor_para_rgba(cor: Cor) -> Any:
    """A cor do módulo como o GTK mostra num seletor.

    O seletor mostra a cor cheia, não a cor no brilho da fita: um roxo a 18%
    aparece quase preto no quadradinho, e ninguém escolhe cor assim.
    """
    from gi.repository import Gdk  # noqa: PLC0415

    vermelho, verde, azul = colorsys.hsv_to_rgb(cor.matiz / 360, cor.saturacao / 1000, 1)
    return Gdk.RGBA(red=vermelho, green=verde, blue=azul, alpha=1.0)


def rgba_para_cor(rgba: Any, brilho: int) -> Cor:
    """O caminho de volta, descartando o brilho que o seletor mostrou."""
    matiz, saturacao, _valor = colorsys.rgb_to_hsv(rgba.red, rgba.green, rgba.blue)
    return Cor(round(matiz * 360) % 360, round(saturacao * 1000), brilho)


# endregion
# region Conversa com o módulo

# Os pontos de dado do controlador RGB, medidos nos módulos: 20 liga e desliga,
# 21 é o modo ("colour" é cor fixa, contra "scene" e "music"), 24 é a cor como
# matiz, saturação e brilho em hexadecimal de quatro dígitos cada.
DP_LIGADA = "20"
DP_MODO = "21"
DP_COR = "24"

# Cinco segundos é folgado para uma resposta que costuma vir em milissegundos, e
# curto o bastante para a thread não ficar pendurada quando a fita sumiu.
ESPERA = 5


def hsv_hex(cor: Cor) -> str:
    """A cor do jeito que o módulo aceita: HHHHSSSSVVVV."""
    return f"{cor.matiz:04x}{cor.saturacao:04x}{cor.brilho:04x}"


def cor_de_hex(valor: str) -> Optional[Cor]:
    """O caminho de volta, para ler o que o módulo respondeu."""
    if len(valor) != 12:
        return None
    try:
        return Cor(int(valor[0:4], 16), int(valor[4:8], 16), int(valor[8:12], 16))
    except ValueError:
        return None


def _dispositivo(fita: Fita) -> Any:
    """O objeto da tinytuya para esta fita. Trocado nos testes.

    Importado aqui dentro de propósito: a biblioteca só faz falta quando há
    fita configurada, e o resto do app (e os testes) não paga por ela.
    """
    import tinytuya  # noqa: PLC0415

    # Sem retentativa: a tinytuya tenta cinco vezes com cinco segundos entre
    # elas, e uma fita que não respondeu na primeira não vai responder na
    # quinta. O caminho de fechamento do app é síncrono — insistir custaria
    # dezenas de segundos de encerramento travado por uma fita fora da tomada.
    # Com isto, o teto por fita é o ESPERA do soquete.
    #
    # Persistente: a conexão fica aberta enquanto o app vive. Abrir custa uns
    # 225 ms por comando e mandar pela conexão aberta, uns 11 — é a diferença
    # entre o brilho acompanhar o dedo no controle e correr atrás dele.
    modulo = tinytuya.BulbDevice(
        fita.id,
        fita.ip,
        fita.key,
        version=float(fita.versao),
        persist=True,
        connection_retry_limit=1,
        connection_retry_delay=0,
    )
    modulo.set_socketTimeout(ESPERA)
    return modulo


# O intervalo do batimento, que mantém as conexões vivas. Os módulos derrubam
# conexão parada em torno de trinta segundos; dez dá margem de sobra, e menos
# que isso seria só pacote a mais sem ganho nenhum.
BATIMENTO = 10

# Quantas falhas seguidas até a fita ser dada como fora do ar. Uma só pode ser
# engasgo da rede; três é fita desligada da tomada. Suspensa, ela para de custar
# o ESPERA do soquete a cada troca de cor.
FALHAS_PARA_SUSPENDER = 3

# As conexões abertas, por id da fita, com o IP em que foram abertas: se o
# assistente gravar a fita num endereço novo, a conexão velha não serve mais.
_conexoes: dict[str, tuple[str, Any]] = {}
_falhas: dict[str, int] = {}
_suspensas: set[str] = set()
_TRAVA_CONEXOES = threading.Lock()

# Cada leva de conexões tem um número. O batimento de uma leva morre sozinho
# quando o número muda — é assim que fechar tudo desliga também o batimento, sem
# precisar esperar a thread acordar.
_geracao = 0
_batimento_vivo = False


# Uma conversa de cada vez com cada módulo, e não uma de cada vez no total: as
# três fitas falam ao mesmo tempo, cada uma com a sua trava. O módulo Tuya
# aceita uma sessão por vez, então dois comandos simultâneos para a MESMA fita
# disputam o soquete e voltam com erro de endereço em uso. Reentrante porque o
# fade segura a fita do primeiro degrau ao comando final, e o comando final
# passa de novo pela trava.
_TRAVAS_FITA: dict[str, threading.RLock] = {}
_TRAVA_DAS_TRAVAS = threading.Lock()


def _trava_da(fita: Fita) -> threading.RLock:
    """A trava daquela fita, criada na primeira vez que alguém fala com ela."""
    with _TRAVA_DAS_TRAVAS:
        return _TRAVAS_FITA.setdefault(fita.id, threading.RLock())


def _conexao(fita: Fita) -> tuple[Any, bool]:
    """A conexão aberta desta fita, e se ela já existia.

    Abre na primeira vez, e de novo quando o IP da fita mudou. Quem chama
    precisa saber se a conexão é nova: falha numa conexão guardada costuma ser
    soquete que morreu em silêncio, e vale abrir outra na hora; falha numa
    conexão recém-aberta é a fita que não responde, e insistir só dobraria a
    espera.
    """
    global _batimento_vivo  # noqa: PLW0603

    with _TRAVA_CONEXOES:
        guardada = _conexoes.get(fita.id)
        if guardada is not None and guardada[0] == fita.ip:
            return guardada[1], True

    if guardada is not None:
        _fechar_conexao(guardada[1])
    nova = _dispositivo(fita)
    with _TRAVA_CONEXOES:
        _conexoes[fita.id] = (fita.ip, nova)
        if not _batimento_vivo:
            _batimento_vivo = True
            _em_thread(lambda geracao=_geracao: _bater(geracao))
    return nova, False


def _fechar_conexao(modulo: Any) -> None:
    try:
        modulo.close()
    except Exception:  # fechar uma conexão já morta não é erro de ninguém
        pass


def _esvaziar(modulo: Any) -> None:
    """Joga fora o que chegou no soquete sem ninguém pedir.

    A tinytuya não confere se a resposta é do pedido que ela fez: lê a mais
    antiga que houver. Numa conexão que fica aberta, uma mensagem sobrando — a
    fita avisando de uma troca feita pelo Smart Life, por exemplo — vira a
    resposta do comando seguinte, e daí em diante toda resposta é a do
    anterior. O comando volta vazio (e o "Testar" diz que a fita não
    respondeu), e o fechamento sai com a resposta do último comando por ler: o
    Windows fecha com RST, e a fita joga fora o comando que acabou de chegar.
    Chamar com a trava da fita: aí nada que está no soquete é resposta de
    alguém.
    """
    soquete = getattr(modulo, "socket", None)
    if soquete is None:
        return
    espera = soquete.gettimeout()
    soquete.settimeout(0)
    try:
        while soquete.recv(4096):
            pass
    except OSError:  # BlockingIOError: acabou o que havia por ler
        pass
    finally:
        soquete.settimeout(espera)


def _descartar(fita: Fita, modulo: Any) -> None:
    """Joga fora esta conexão da fita; a próxima conversa abre outra.

    Chamar com a trava da fita, e só tira do mapa se a guardada ainda for esta:
    fora da trava, o batimento podia pegar o objeto no meio do descarte, e a
    tinytuya reabria o soquete dele — uma conexão que ninguém mais fechava.
    """
    with _TRAVA_CONEXOES:
        guardada = _conexoes.get(fita.id)
        if guardada is not None and guardada[1] is modulo:
            del _conexoes[fita.id]
    _fechar_conexao(modulo)


def _conversar(fita: Fita, acao: Any) -> Optional[Any]:
    """Uma conversa com a fita pela conexão aberta. ``None`` quando falha.

    Conexão guardada que falha é descartada e tentada de novo uma vez, já com
    uma conexão nova — é o caso do roteador que reiniciou ou do PC que voltou
    da suspensão, e o comando segue de onde parou. Três falhas seguidas
    suspendem a fita até o botão "Testar" pedir, o fechamento do app tentar
    devolvê-la, ou o app abrir de novo.
    """
    if fita.id in _suspensas:
        return None

    # Nunca entregar uma fita sem IP para a tinytuya. Sem endereço ela faz uma
    # varredura por conta própria ao abrir a conexão — e com as três fitas em
    # paralelo, são três varreduras disputando a mesma porta de broadcast: uma
    # ganha, as outras voltam com "apenas uma utilização de cada endereço de
    # soquete". Quem acha o IP é a nossa varredura, que tem trava. Isto não
    # conta como falha: a fita não está fora do ar, só ainda não foi achada.
    if not fita.ip:
        logging.info("Fita %s ainda sem endereço na rede", fita.nome)
        return None

    for _tentativa in range(2):
        guardada = False
        with _trava_da(fita):
            modulo = None
            try:
                modulo, guardada = _conexao(fita)
                _esvaziar(modulo)
                resposta = acao(modulo)
            except Exception as erro:  # a tinytuya levanta de tudo: socket, struct, json
                resposta = {"Error": str(erro)}
            falhou = isinstance(resposta, dict) and bool(resposta.get("Error"))
            if falhou and modulo is not None:
                _descartar(fita, modulo)

        if not falhou:
            _falhas.pop(fita.id, None)
            return resposta
        if not guardada:
            break

    logging.warning("Fita %s não respondeu: %s", fita.nome, resposta.get("Error"))
    _contar_falha(fita)
    return None


def _contar_falha(fita: Fita) -> None:
    _falhas[fita.id] = _falhas.get(fita.id, 0) + 1
    if _falhas[fita.id] >= FALHAS_PARA_SUSPENDER and fita.id not in _suspensas:
        _suspensas.add(fita.id)
        logging.info(
            "Fita %s suspensa depois de %s falhas seguidas", fita.nome, _falhas[fita.id]
        )


def retomar(ids: Optional[set[str]] = None) -> None:
    """Tira fitas da suspensão. Sem argumento, tira todas.

    Chamado pelo botão "Testar", onde o usuário pediu para tentar de novo, e
    pelo fechamento do app: a fita suspensa no meio do jogo (o roteador que
    reiniciou) ainda tem um estado de antes para receber.
    """
    alvo = set(_suspensas) if ids is None else ids & _suspensas
    for identificador in alvo:
        _suspensas.discard(identificador)
        _falhas.pop(identificador, None)


def _bater(geracao: int) -> None:
    """Mantém as conexões vivas enquanto esta leva de conexões existir."""
    global _batimento_vivo  # noqa: PLW0603

    while True:
        time.sleep(BATIMENTO)
        with _TRAVA_CONEXOES:
            if geracao != _geracao or not _conexoes:
                if geracao == _geracao:
                    _batimento_vivo = False
                return
            abertas = list(_conexoes.items())
        for identificador, (_ip, modulo) in abertas:
            trava = _TRAVAS_FITA.get(identificador)
            # Fita ocupada numa conversa de verdade não precisa de batimento: a
            # própria conversa mantém a conexão viva.
            if trava is None or not trava.acquire(blocking=False):
                continue
            # Uma consulta, e não o `heartbeat` da tinytuya: ele manda sem ler
            # a resposta, e ela sobrava no soquete — ver `_esvaziar`. A
            # consulta lê a própria resposta, e de quebra diz se a fita sumiu.
            try:
                # Reconferido já com a trava: a conexão da lista pode ter sido
                # descartada depois que ela foi tirada, e usar o objeto fechado
                # faria a tinytuya reabrir um soquete que ninguém mais fecharia.
                with _TRAVA_CONEXOES:
                    if _conexoes.get(identificador, (None, None))[1] is not modulo:
                        continue
                _esvaziar(modulo)
                resposta = modulo.status()
                if isinstance(resposta, dict) and resposta.get("Error"):
                    raise ConnectionError(resposta["Error"])
            except Exception:  # a tinytuya levanta de tudo
                with _TRAVA_CONEXOES:
                    _conexoes.pop(identificador, None)
                _fechar_conexao(modulo)
            finally:
                trava.release()


def fechar_conexoes() -> None:
    """Fecha todas as conexões e desliga o batimento. Seguro chamar à toa."""
    global _geracao, _batimento_vivo  # noqa: PLW0603

    with _TRAVA_CONEXOES:
        abertas = list(_conexoes.values())
        _conexoes.clear()
        _geracao += 1
        _batimento_vivo = False
    for _ip, modulo in abertas:
        _fechar_conexao(modulo)


def ler_estado(fita: Fita) -> Optional[dict[str, Any]]:
    """Se a fita está acesa e em que cor. ``None`` quando ela não responde.

    Espera a resposta da consulta pelo tipo, e não a primeira mensagem que
    chegar: no arranque com órfãos, a leitura vem logo depois da devolução,
    com a segunda cópia do último aviso ainda a caminho — e um aviso só com a
    cor, lido como estado, faria a fita acesa passar por apagada.
    """

    def consultar(modulo: Any) -> Any:
        resposta = modulo.status(nowait=True)
        if isinstance(resposta, dict) and resposta.get("Error"):
            return resposta
        dps = _esperar(modulo, lambda cmd, _dps: cmd in RESPOSTAS_DA_CONSULTA)
        return {"Error": "a fita não respondeu à consulta"} if dps is None else {"dps": dps}

    resposta = _conversar(fita, consultar)
    dps = (resposta or {}).get("dps") if isinstance(resposta, dict) else None
    if not isinstance(dps, dict):
        return None
    estado = {
        "ligada": bool(dps.get(DP_LIGADA, False)),
        "cor": str(dps.get(DP_COR, "")),
    }
    _mostrada[fita.id] = (estado["ligada"], estado["cor"])
    return estado


def aplicar(fita: Fita, ligada: bool, cor_hex: str) -> bool:
    """Manda cor e estado para uma fita. Nunca levanta; devolve se deu certo.

    Sem cor, só o liga/desliga vai. A fita que estava apagada às vezes não
    reporta o ponto da cor, e o estado guardado dela sai com a cor vazia;
    mandar ``24: ""`` faz o módulo recusar o comando inteiro — e aí a fita não
    apaga, que é justamente o que a devolução do fechamento promete.

    Apagar vai em dois passos, o desliga antes da cor: junto, a cor chegava
    primeiro e a fita acendia nela por um instante — a piscada no brilho de
    antes no fim do fade do fechamento. A cor vai mesmo assim, depois: sem ela
    a fita guardaria o último degrau do fade, a 1%, e religada por fora
    acenderia quase apagada. Desligada, a fita troca a cor guardada sem
    acender — medido.

    Acender vai em dois passos, a cor antes do liga: a fita guarda a última cor
    que teve, e mandar tudo junto deixa o módulo acender no vermelho de ontem,
    no brilho de ontem, antes de obedecer à cor de agora. A piscada dura um
    piscar de olhos e é justamente o que se vê num quarto escuro.
    """
    if not cor_hex:
        feito = _mandar(fita, {DP_LIGADA: ligada})
    elif not ligada:
        feito = _mandar(fita, {DP_LIGADA: False}) and _mandar(
            fita, {DP_MODO: "colour", DP_COR: cor_hex}
        )
    else:
        feito = _mandar(fita, {DP_MODO: "colour", DP_COR: cor_hex}) and _mandar(
            fita, {DP_LIGADA: True}
        )

    # Deu errado no meio, não se sabe o que a fita mostra: o próximo fade não
    # tem de onde partir e vai direto.
    if feito:
        _mostrada[fita.id] = (ligada, cor_hex or _mostrada.get(fita.id, (False, ""))[1])
    else:
        _mostrada.pop(fita.id, None)
    return feito


def _mandar(fita: Fita, valores: dict[str, Any]) -> bool:
    """Um comando para uma fita. Nunca levanta; devolve se deu certo.

    Confirmado pelo aviso em que a fita diz que está nestes valores, e não pela
    resposta que a tinytuya leria (ver ``_esperar``). Com a confirmação, o
    comando foi aplicado de verdade: fechar a conexão depois disso não perde
    nada, mesmo com a cópia do aviso ainda a caminho.
    """

    def mandar(modulo: Any) -> Any:
        # De vez em quando a fita joga fora um comando, sem aviso — mais logo
        # depois de um fade. O mesmo comando reenviado pega; medido.
        for _envio in range(ENVIOS):
            resposta = modulo.set_multiple_values(valores, nowait=True)
            if isinstance(resposta, dict) and resposta.get("Error"):
                return resposta
            if _alcancou(modulo, valores, ESPERA_AVISO / ENVIOS):
                return True
        return {"Error": "a fita não avisou que aplicou o comando"}

    return _conversar(fita, mandar) is not None


# endregion
# region Fade

# A fita não sabe fazer fade sozinha: o ponto 28 ("gradiente") é aceito e
# ignorado nestes módulos. Então o fade é o app mandando as cores do meio, uma
# atrás da outra, sem esperar resposta. Medido com as três fitas juntas: a 30
# degraus por segundo elas acompanham com meio segundo de atraso constante; a
# 40 o atraso cresce e elas começam a jogar degraus fora; a 60 param no meio
# do caminho. Os degraus menores vêm da duração: 2 s a 30/s dá degraus do
# tamanho dos de 1,5 s a 40/s. O degrau perdido não aparece; o que não pode se
# perder é o fim, e por isso o fim vai sempre num comando que espera resposta.
RITMO_FADE = 30
DURACAO_FADE = 2

# O que cada fita está mostrando agora, por id: se está acesa, e a cor em
# hexadecimal. Sem isso o fade não tem de onde partir — e ler a fita antes de
# cada troca custaria uma volta de rede a cada play. Fita que não está aqui
# muda direto, sem fade.
_mostrada: dict[str, tuple[bool, str]] = {}

# Cada troca pedida para uma fita ganha um número. O fade que vê o número da
# sua fita mudar para de mandar degraus: outra troca chegou (o play no meio do
# fade da abertura), e ela parte de onde este parou.
_geracoes: dict[str, int] = {}


def _degraus(origem: Cor, destino: Cor, passos: int) -> list[str]:
    """As cores do caminho, sem a origem e com o destino, em hexadecimal.

    A matiz vai pelo lado mais curto do círculo: do vermelho ao roxo passa pelo
    magenta, e não dá a volta pelo verde e pelo azul.
    """
    matiz = (destino.matiz - origem.matiz + 180) % 360 - 180
    caminho = []
    anterior = hsv_hex(origem)
    for passo in range(1, passos + 1):
        andado = passo / passos
        cor_hex = hsv_hex(
            Cor(
                round(origem.matiz + matiz * andado) % 360,
                round(origem.saturacao + (destino.saturacao - origem.saturacao) * andado),
                round(origem.brilho + (destino.brilho - origem.brilho) * andado),
            )
        )
        # Degrau igual ao anterior não muda nada na fita, e o fim do fade é
        # reconhecido pelo aviso do último degrau: repetido, o aviso da
        # primeira cópia pareceria o do fim.
        if cor_hex != anterior:
            caminho.append(cor_hex)
            anterior = cor_hex
    return caminho


def _vigente(fita: Fita, geracao: int) -> bool:
    return _geracoes.get(fita.id) == geracao


# Os códigos das mensagens da fita no protocolo Tuya que importam aqui: o
# aviso de estado, que ela manda depois de aplicar um comando (o ``STATUS`` da
# tinytuya), e a resposta a uma consulta — o ``DP_QUERY`` (10) até a versão
# 3.3 do protocolo, o ``DP_QUERY_NEW`` (16) da 3.4 em diante. Os "recebi" são 7.
AVISO_DE_ESTADO = 8
RESPOSTAS_DA_CONSULTA = (10, 16)

# Quanto esperar pela mensagem certa. Medido a 30 degraus/s com as três fitas
# juntas: o aviso do último degrau chega até meio segundo depois do fim da
# rajada, e uma vez chegou em 1,75 s. Um comando solto é avisado em bem menos
# de um segundo, e por isso o comando divide a espera em três envios.
ESPERA_AVISO = 3
ENVIOS = 3


def _esperar(
    modulo: Any, aceita: Any, prazo: float = ESPERA_AVISO
) -> Optional[dict[str, Any]]:
    """Lê o que a fita manda até chegar a mensagem que ``aceita(cmd, dps)``
    reconhece, e devolve os valores dela. ``None`` quando ela não vem no prazo.

    Leitura crua, e não a da tinytuya, que pega a mensagem mais antiga seja ela
    qual for — a fita manda cada aviso duas vezes, e a segunda cópia virava a
    resposta do pedido seguinte. Aqui, o que não é a resposta procurada é
    pulado.
    """
    soquete = modulo.socket
    espera = soquete.gettimeout()
    limite = time.monotonic() + prazo
    try:
        while (falta := limite - time.monotonic()) > 0:
            soquete.settimeout(falta)
            mensagem = modulo._receive()
            if not mensagem.payload:
                continue
            dps = (modulo._decode_payload(mensagem.payload) or {}).get("dps") or {}
            if aceita(mensagem.cmd, dps):
                return dps
    except Exception:  # prazo do soquete, ou mensagem que não se deixou ler
        pass
    finally:
        soquete.settimeout(espera)
    return None


def _alcancou(
    modulo: Any, valores: dict[str, Any], prazo: float = ESPERA_AVISO
) -> bool:
    """Lê o que a fita manda até ela avisar que está nestes valores.

    Os avisos chegam na ordem dos comandos: quando chega o destes valores, os
    de antes já chegaram. O aviso não traz tudo o que foi mandado — o modo
    (21) nunca vem —, então vale o aviso que traz algum dos valores e em que
    todos os que traz batem. A cópia do aviso de um comando anterior traz
    outro valor e é pulada.
    """

    def aceita(cmd: int, dps: dict[str, Any]) -> bool:
        trazidos = [chave for chave in valores if chave in dps]
        return (
            cmd == AVISO_DE_ESTADO
            and bool(trazidos)
            and all(str(dps[chave]).lower() == str(valores[chave]).lower() for chave in trazidos)
        )

    return _esperar(modulo, aceita, prazo) is not None


def _rajada(fita: Fita, caminho: list[str], geracao: int) -> None:
    """Manda os degraus no ritmo do fade, sem esperar resposta de nenhum.

    No fim espera a fita alcançar o último degrau: ela aplica mais devagar do
    que recebe, e avisa de cada degrau aos lotes, até um segundo depois do
    último — medido. Sem essa espera, o comando final entraria na fila atrás
    dos degraus e estouraria a própria espera dele. O marco é o aviso do
    último degrau: nem o silêncio nem uma consulta de estado servem, porque a
    fita fica calada no meio dos lotes e responde à consulta com o degrau em
    que está, antes dos avisos que faltam. Às vezes ela joga fora o fim da
    rajada e o aviso não vem; segue-se assim mesmo, e quem garante a cor final
    é o comando final, que é reenviado até ser avisado.
    """

    def degraus(modulo: Any) -> Any:
        inicio = time.monotonic()
        ultimo = None
        for indice, cor_hex in enumerate(caminho):
            if not _vigente(fita, geracao):
                break
            resposta = modulo.set_multiple_values({DP_COR: cor_hex}, nowait=True)
            if isinstance(resposta, dict) and resposta.get("Error"):
                return resposta
            ultimo = cor_hex
            _mostrada[fita.id] = (True, cor_hex)
            falta = inicio + (indice + 1) / RITMO_FADE - time.monotonic()
            if falta > 0:
                time.sleep(falta)
        if ultimo is not None:
            _alcancou(modulo, {DP_COR: ultimo})
        return True

    _conversar(fita, degraus)


def _reservar(alvos: list[Fita]) -> dict[str, int]:
    """O número da próxima troca de cada fita, tirado agora.

    Quem pede a troca na thread de UI reserva ali: a ordem das trocas é a dos
    pedidos, e não a de quem terminou de calcular a cor primeiro.
    """
    with _TRAVA_DAS_TRAVAS:
        for fita in alvos:
            _geracoes[fita.id] = _geracoes.get(fita.id, 0) + 1
        return {fita.id: _geracoes[fita.id] for fita in alvos}


def _transitar(
    fita: Fita, ligada: bool, cor_hex: str, geracao: Optional[int] = None
) -> bool:
    """Leva a fita ao estado pedido deslizando, e confirma o fim.

    Síncrona: segura a fita do primeiro degrau ao comando final, para duas
    trocas nunca se misturarem na mesma fita. Devolve se o fim deu certo, e
    falso também quando outra troca tomou o lugar desta no meio do caminho.
    ``geracao`` é o número que ``_reservar`` tirou; sem ele, tira agora.
    """
    if geracao is None:
        geracao = _reservar([fita])[fita.id]

    with _trava_da(fita):
        if not _vigente(fita, geracao):
            return False

        passos = round(DURACAO_FADE * RITMO_FADE)
        antes = _mostrada.get(fita.id)
        alvo = cor_de_hex(cor_hex)
        # Só fita acesa desliza. A apagada acende direto, a cor antes do liga:
        # acender do escuro com fade deixava fita apagada no teste com as fitas
        # de verdade.
        origem = cor_de_hex(antes[1]) if antes is not None and antes[0] else None
        if passos and origem is not None:
            # Apagar é escurecer até o mínimo e só então desligar.
            destino = alvo if ligada else origem._replace(brilho=BRILHO_MINIMO)
            if destino is not None and destino != origem:
                _rajada(fita, _degraus(origem, destino, passos), geracao)

        if not _vigente(fita, geracao):
            return False
        return aplicar(fita, ligada, cor_hex)


# endregion
# region Varredura


# A varredura é broadcast: ela acha as fitas na rede de casa pelo id, e é o
# assistente quem a usa, uma vez, para descobrir o endereço de cada fita. O app
# não varre sozinho depois disso — o IP das fitas é fixo, e se um dia mudar é
# só rodar o assistente de novo. Doze segundos é o que basta para todo mundo
# responder; o padrão da tinytuya é dezoito.
ESPERA_VARREDURA = 12

# Uma varredura de cada vez no processo inteiro. Ela abre um soquete de
# broadcast numa porta fixa, e o Windows não tem `SO_REUSEPORT`: duas ao mesmo
# tempo fazem a segunda morrer com "apenas uma utilização de cada endereço de
# soquete".
_TRAVA_VARREDURA = threading.Lock()


def ips_da_varredura(achados: dict[str, Any]) -> dict[str, str]:
    """O que a varredura encontrou, como um mapa de id do módulo para IP."""
    mapa = {}
    for ip, dados in (achados or {}).items():
        identificador = (dados or {}).get("gwId")
        if identificador:
            mapa[str(identificador)] = str(ip)
    return mapa


def versoes_da_varredura(achados: dict[str, Any]) -> dict[str, str]:
    """A versão do protocolo de cada módulo que a varredura achou, por id.

    O assistente grava a fita com ela: um módulo 3.4 ou 3.5 falado como 3.3
    nunca responde.
    """
    mapa = {}
    for dados in (achados or {}).values():
        identificador = (dados or {}).get("gwId")
        versao = (dados or {}).get("version")
        if identificador and versao:
            mapa[str(identificador)] = str(versao)
    return mapa


def enderecos_na_rede() -> tuple[dict[str, str], dict[str, str]]:
    """O endereço e a versão do protocolo de cada módulo Tuya que respondeu
    na rede, cada um num mapa por id.

    Sem enquete: ``poll=True`` iria perguntar o estado de cada aparelho achado —
    inclusive dos que não são nossos — e aqui só o endereço e a versão
    interessam. Nunca levanta; sem rede ou sem biblioteca, devolve dois mapas
    vazios.
    """
    with _TRAVA_VARREDURA:
        try:
            # Dentro do `try`: sem a biblioteca instalada, o `ImportError` cru
            # derrubaria a thread do assistente e a tela ficaria em
            # "Buscando…" para sempre.
            import tinytuya  # noqa: PLC0415

            achados = tinytuya.deviceScan(False, ESPERA_VARREDURA, poll=False)
            return ips_da_varredura(achados), versoes_da_varredura(achados)
        except Exception as erro:  # a tinytuya levanta de tudo: socket, struct, json
            logging.warning("Varredura das fitas falhou: %s", erro)
            return {}, {}

# endregion
# region Ciclo de vida

CHAVE_ESTADO = "fita-estado-anterior"

# Quanto o fechamento do app pode esperar pela devolução. Com as fitas falando
# ao mesmo tempo, o custo do conjunto é o da mais lenta, e a mais lenta possível
# é uma fita muda: o ESPERA do soquete. O prazo fica um segundo acima disso, e
# não abaixo — com quatro segundos, uma única fita fora da tomada fazia o
# fechamento desistir e as três ficavam com a cor do app até o arranque
# seguinte. O fade vem antes da espera, e entra na conta. Estourado o prazo,
# quem termina o serviço ainda é o arranque.
PRAZO_FECHAMENTO = ESPERA + DURACAO_FADE + 1

# Um arranque de cada vez. Guardar o estado é "lê a chave, conversa com as
# fitas, grava a chave", e o miolo disso leva segundos de rede: sem a trava,
# dois arranques próximos leriam a chave vazia ao mesmo tempo e o segundo
# gravaria por cima o roxo que o primeiro acabou de pintar.
_TRAVA_ARRANQUE = threading.Lock()


def ligada() -> bool:
    """Se há o que fazer: recurso ligado nas Preferências e fita configurada."""
    return bool(shared.schema.get_boolean("session-fita") and fitas())


def _em_thread(tarefa: Any) -> threading.Thread:
    """Toda conversa com as fitas sai da thread de UI por aqui."""
    linha = threading.Thread(target=tarefa, daemon=True)
    linha.start()
    return linha


def _em_paralelo(itens: list[Any], tarefa: Any) -> list[Any]:
    """Roda ``tarefa`` para cada item ao mesmo tempo e espera todas.

    As fitas têm de mudar juntas. Em fila, cada uma só começa depois de a
    anterior ter aberto conexão, mandado o comando e respondido — e a troca de
    cor corre visivelmente de um monitor para o outro, que é exatamente o que a
    imersão não pode ter. Em paralelo, o custo do conjunto é o da fita mais
    lenta, e não a soma delas.

    Cada fita tem a sua trava lá embaixo, então o paralelo aqui nunca faz duas
    conversas com o mesmo módulo.
    """
    if not itens:
        return []

    resultados: dict[int, Any] = {}

    def correr(indice: int, item: Any) -> None:
        resultados[indice] = tarefa(item)

    linhas = [
        threading.Thread(target=correr, args=(indice, item), daemon=True)
        for indice, item in enumerate(itens)
    ]
    for linha in linhas:
        linha.start()
    for linha in linhas:
        linha.join()
    return [resultados.get(indice) for indice in range(len(itens))]


def _vestir(
    cor: Cor,
    alvos: Optional[list[Fita]] = None,
    geracoes: Optional[dict[str, int]] = None,
) -> None:
    """Acende as fitas na cor pedida, ao mesmo tempo e deslizando.
    Síncrono; nunca levanta.

    Sem ``alvos``, acende todas as configuradas. ``geracoes`` são os números
    que ``_reservar`` tirou na hora do pedido; sem eles, cada fita tira o seu
    agora.

    Fita que não responde fica como está — e depois de três falhas seguidas é
    suspensa, para não atrasar as outras. O endereço de cada fita é o que o
    assistente achou: IP mudou, roda-se o assistente de novo.
    """
    _em_paralelo(
        fitas() if alvos is None else alvos,
        lambda fita: _transitar(
            fita, True, hsv_hex(na_fita(cor, fita)), (geracoes or {}).get(fita.id)
        ),
    )


def _pintar(cor: Cor, tetos: Optional[dict[str, int]] = None) -> None:
    """Só a cor, sem mexer no liga/desliga e sem fade. Para a prévia ao vivo."""

    def pintar(fita: Fita) -> None:
        cor_hex = hsv_hex(na_fita(cor, fita, (tetos or {}).get(fita.id)))
        if _mandar(fita, {DP_MODO: "colour", DP_COR: cor_hex}) and fita.id in _mostrada:
            _mostrada[fita.id] = (_mostrada[fita.id][0], cor_hex)

    _em_paralelo(fitas(), pintar)


# A cor que a prévia ainda deve mostrar, e a thread que a serve. Arrastar o
# controle do brilho gera dezenas de valores por segundo, e cada um deles é uma
# conversa de rede com três fitas: em vez de enfileirar todos, guardamos só o
# último e a thread pega o valor mais recente quando termina o anterior. Os
# passos do meio se perdem, que é exatamente o que se quer — o olho só precisa
# ver onde o controle parou.
_previa_alvo: Optional[tuple[Cor, Optional[dict[str, int]]]] = None
_previa_viva = False
_PREVIA = threading.Condition()


def previa(cor: Cor, tetos: Optional[dict[str, int]] = None) -> None:
    """Mostra esta cor nas fitas agora. Chamar da thread de UI, à vontade.

    ``tetos`` troca o brilho por dispositivo do arquivo (id → porcentagem).
    """
    global _previa_alvo, _previa_viva  # noqa: PLW0603

    if not ligada():
        return
    with _PREVIA:
        _previa_alvo = (cor, tetos)
        if not _previa_viva:
            _previa_viva = True
            _em_thread(_servir_previa)


def _servir_previa() -> None:
    """Pinta o alvo mais recente até não haver mais nada novo."""
    global _previa_alvo, _previa_viva  # noqa: PLW0603

    while True:
        with _PREVIA:
            alvo = _previa_alvo
            _previa_alvo = None
            if alvo is None:
                _previa_viva = False
                return
        _pintar(*alvo)


def tom_do_app() -> tuple[int, int]:
    """Matiz e saturação da cor do app: a escolhida, ou o roxo de fábrica."""
    return (
        shared.schema.get_int("fita-matiz-app"),
        shared.schema.get_int("fita-saturacao-app"),
    )


def salvar_tom_do_app(matiz: int, saturacao: int) -> None:
    """Grava a cor do app escolhida nas Preferências."""
    shared.schema.set_int("fita-matiz-app", matiz % 360)
    shared.schema.set_int("fita-saturacao-app", max(0, min(1000, saturacao)))


def redefinir_tom_do_app() -> None:
    """Volta a cor do app ao roxo de fábrica."""
    salvar_tom_do_app(*ROXO_DO_APP)


def cor_do_app() -> Cor:
    """A cor das fitas quando não há jogo correndo, no brilho padrão.

    Pública porque as Preferências e a tela de detalhes precisam da mesma cor,
    e ela mora num lugar só.
    """
    return Cor(*tom_do_app(), brilho_padrao())


def _estado_de(alvos: list[Fita]) -> dict[str, dict[str, Any]]:
    """O estado de cada uma destas fitas, por id.

    Quem não respondeu fica de fora: devolver uma fita ao estado que só foi
    chutado seria pior que não devolver nada.
    """
    lidos = _em_paralelo(alvos, ler_estado)
    return {
        fita.id: lido for fita, lido in zip(alvos, lidos) if lido is not None
    }


def _guardar_e_vestir() -> None:
    """Guarda o estado das fitas que ainda não têm um e acende na cor do app
    as que têm. Chamar com a ``_TRAVA_ARRANQUE``.

    Só é lida a fita que falta na chave. A que já está nela tem guardado o
    primeiro estado, o de antes do app — relê-la agora gravaria por cima a cor
    que o próprio app pintou, e o fechamento devolveria as fitas à cor do app
    para sempre.

    Só é pintada a fita que está na chave: fita que não respondeu à leitura e
    responde ao pintar ficaria na cor do app sem estado para voltar. Ela é lida
    de novo no próximo arranque ou reacender. Nenhuma lida, nada é gravado — a
    chave nunca vira ``"{}"``.
    """
    if not ligada():
        return

    configuradas = fitas()
    estados = _estados_guardados()
    novos = _estado_de([fita for fita in configuradas if fita.id not in estados])
    if novos:
        estados.update(novos)
        shared.schema.set_string(CHAVE_ESTADO, json.dumps(estados))

    _vestir(cor_do_app(), [fita for fita in configuradas if fita.id in estados])


def _estados_guardados() -> dict[str, Any]:
    """A chave lida como dicionário. Chave mexida à mão vira dicionário vazio."""
    try:
        estados = json.loads(shared.schema.get_string(CHAVE_ESTADO) or "{}")
    except ValueError:
        return {}
    return estados if isinstance(estados, dict) else {}


def _guardadas() -> list[Fita]:
    """As fitas com estado de antes guardado: as únicas que o app pinta, por
    serem as únicas que o fechamento sabe devolver."""
    estados = _estados_guardados()
    return [fita for fita in fitas() if fita.id in estados]


def _repor(fita: Fita, estado: Any) -> None:
    """Devolve uma fita ao estado guardado dela, deslizando.

    Estado que não é dicionário é chave mexida à mão, e não pode virar
    ``AttributeError`` cru aqui dentro.
    """
    if not isinstance(estado, dict):
        return
    _transitar(fita, bool(estado.get("ligada")), str(estado.get("cor", "")))


def devolver_removidas(removidas: list[Fita]) -> None:
    """Devolve ao estado guardado as fitas que saem da configuração.

    Sem isto, a fita desmarcada no assistente some do arquivo e, com ela, a
    única referência que o fechamento do app tinha para devolvê-la: ela ficaria
    na cor do Jolven para sempre. É rede — chamar de fora da thread de UI.

    Com a ``_TRAVA_ARRANQUE``: o assistente fecha logo depois de chamar isto, e
    o reacender que o fechamento dele dispara também lê e grava a chave. Sem a
    trava, a última gravação apagaria a outra.
    """
    with _TRAVA_ARRANQUE:
        estados = _estados_guardados()
        if not estados:
            return

        saindo = [(fita, estados.pop(fita.id, None)) for fita in removidas]
        _em_paralelo(
            [par for par in saindo if par[1] is not None],
            lambda par: _repor(par[0], par[1]),
        )

        shared.schema.set_string(CHAVE_ESTADO, json.dumps(estados) if estados else "")


def _devolver() -> None:
    """Devolve cada fita ao estado guardado e limpa a chave."""
    if not shared.schema.get_string(CHAVE_ESTADO):
        return

    por_id = {fita.id: fita for fita in fitas()}
    devolver = [
        (por_id[identificador], estado)
        for identificador, estado in _estados_guardados().items()
        if identificador in por_id
    ]
    _em_paralelo(devolver, lambda par: _repor(par[0], par[1]))

    # Limpa mesmo quando alguma fita não respondeu: a chave diz "há uma troca
    # pendente", e insistir eternamente numa fita que saiu da tomada deixaria o
    # app tentando desfazer isso em todo arranque.
    shared.schema.set_string(CHAVE_ESTADO, "")


def restaurar_orfaos() -> None:
    """Desfaz a troca que uma execução anterior não desfez.

    Síncrona: ``_devolver`` conversa com cada fita, e isso é rede. Chamar de
    dentro da thread do arranque (é o que ``_arrancar`` faz), nunca da thread
    de UI — ali seguraria a tela por segundos toda vez que houvesse órfão.
    """
    if shared.schema.get_string(CHAVE_ESTADO):
        logging.info("Fitas de uma sessão anterior encontradas; desfazendo")
        _devolver()


def _arrancar() -> None:
    """Os dois passos do arranque, já fora da thread de UI.

    A trava não espera: se alguém a segura, é outra conversa do ciclo em
    curso (a devolução das fitas removidas no assistente, um reacender), e o
    arranque não tem o que disputar com ela.
    """
    if not _TRAVA_ARRANQUE.acquire(blocking=False):
        logging.info("Arranque das fitas já em curso; segunda chamada ignorada")
        return
    try:
        restaurar_orfaos()
        if ligada():
            _guardar_e_vestir()
    finally:
        _TRAVA_ARRANQUE.release()


def abrir() -> None:
    """O app abriu: desfaz o que ficou de antes e acende na cor do app.

    Só para o arranque do app. Com o app já aberto, a chave é a desta execução
    e não um órfão: quem religa o ciclo com o app aberto chama ``reacender``.

    Chamar da thread de UI. Os dois passos correm na MESMA thread, e nesta
    ordem: o estado que uma execução anterior deixou pendurado precisa ser
    devolvido antes de guardarmos o estado novo, senão a cor do próprio app
    viraria "o estado de antes" do usuário.
    """
    # A devolução de órfãos acontece mesmo com o recurso desligado nas
    # Preferências: quem desligou a opção no meio do caminho continua com as
    # fitas vestidas de uma sessão que já acabou.
    if not ligada() and not shared.schema.get_string(CHAVE_ESTADO):
        return
    _em_thread(_arrancar)


def _reacender() -> None:
    with _TRAVA_ARRANQUE:
        _guardar_e_vestir()


def reacender() -> None:
    """O ciclo foi religado com o app aberto: o interruptor foi ligado, ou o
    assistente fechou. Chamar da thread de UI.

    Não desfaz órfão nenhum — a chave, se houver, é desta execução, e
    devolvê-la faria as fitas piscarem no estado de antes. Só guarda o estado
    das fitas que ainda não têm um (a primeira vez que o recurso liga, a fita
    nova do assistente) e veste a cor do app. A trava espera aqui, ao contrário
    do arranque: o reacender que chega junto com a devolução das fitas
    removidas no assistente tem trabalho a fazer depois dela.
    """
    if not ligada():
        return
    _em_thread(_reacender)


def comecar(game: "Game") -> None:
    """A sessão começou: veste a cor do jogo. Chamar da thread de UI.

    A cor é calculada dentro da thread, e não antes dela: tirar a cor da capa
    abre e quantiza uma imagem, e isso não pode acontecer enquanto o jogo abre.
    A vez de cada fita, essa sim, é tirada aqui: um "Já terminei" logo depois
    tem de vencer a cor do jogo que ainda estava sendo calculada.
    """
    if not ligada():
        return
    alvos = _guardadas()
    geracoes = _reservar(alvos)
    _em_thread(lambda: _vestir(cor_do_jogo(game), alvos, geracoes))


def voltar() -> None:
    """A sessão acabou: de volta à cor do app. Chamar da thread de UI."""
    if not ligada():
        return
    alvos = _guardadas()
    geracoes = _reservar(alvos)
    _em_thread(lambda: _vestir(cor_do_app(), alvos, geracoes))


def _devolver_no_fechamento() -> None:
    """A devolução do fechamento, na vez dela.

    Espera um arranque ou reacender em curso terminar: correndo junto, o
    vestir dele e a devolução disputariam cada fita, e se o vestir vencesse a
    fita ficaria na cor do app com a chave já limpa. A espera cabe no prazo do
    fechamento; estourado, a chave fica para o próximo arranque.

    As fitas suspensas são tentadas também: suspensão poupa espera na troca
    de cor, mas aqui é a última chance de devolvê-las antes de a chave ser
    limpa.
    """
    if not _TRAVA_ARRANQUE.acquire(timeout=PRAZO_FECHAMENTO):
        return
    try:
        retomar()
        _devolver()
    finally:
        _TRAVA_ARRANQUE.release()


def fechar() -> None:
    """O app está fechando: devolve o estado de antes, com hora marcada.

    Aqui se espera, ao contrário dos outros: é a última janela em que ainda há
    processo para desfazer. Mas não se espera para sempre. Estourado o
    ``PRAZO_FECHAMENTO``, o app sai assim mesmo — a chave continua gravada e o
    ``restaurar_orfaos`` do próximo arranque termina o serviço. É exatamente
    para isso que a chave existe.
    """
    linha = _em_thread(_devolver_no_fechamento)
    linha.join(PRAZO_FECHAMENTO)
    if linha.is_alive():
        logging.info(
            "Fitas não devolvidas dentro de %ss; fica para o próximo arranque",
            PRAZO_FECHAMENTO,
        )
        # A devolução ainda está usando as conexões: fechar agora a derrubaria
        # no meio. O processo está saindo e leva os soquetes junto.
        return
    fechar_conexoes()


# endregion
