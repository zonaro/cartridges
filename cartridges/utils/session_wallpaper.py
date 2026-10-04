# session_wallpaper.py
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

"""Veste os outros monitores com a arte do jogo enquanto a sessão corre.

Os outros são todos menos o principal, em pé ou deitados. O jogo roda no
principal, e ali a arte ficaria atrás dele. Quem é o principal é o GNOME quem
diz, então ligar, desligar ou girar uma tela não pede configuração nenhuma.

A troca é lida e aplicada pelo GSettings (``org.gnome.desktop.background``),
que vale para a área de trabalho inteira: cada monitor-alvo ganha o seu
quadro em disco, e o que vai para a parede é um deles.

O papel de parede de antes é guardado no GSettings, e não em memória, porque
o caminho de volta tem de sobreviver ao processo: o app fechado no meio da
sessão (ou morto) deixaria as telas vestidas de um jogo que já acabou.
Com a chave em disco, o arranque seguinte desfaz — é o que :func:`restaurar`
faz quando :func:`restaurar_orfaos` a chama.
"""

import json
import logging
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any, NamedTuple, Optional, TYPE_CHECKING
from urllib.parse import urlparse
from uuid import uuid4

from gi.repository import Gdk, Gio, GLib
from PIL import Image, ImageFilter, ImageOps

from cartridges import shared
from cartridges.utils.download import download_bytes
from cartridges.utils.ler_json import ler_json
from cartridges.utils.wallhaven import IMAGE_SUFFIXES, melhor_para

if TYPE_CHECKING:
    from cartridges.game import Game

# O que a `enquadrar` usa quando o corte é vertical. Não é o meio: numa arte de
# jogo o que importa costuma estar na metade de cima (rosto, título), e centrar
# come a testa antes de comer o chão.
CENTRO_VERTICAL = 0.4

# Onde a tela de escolha cai quando não há monitor-alvo ligado — ela precisa de
# uma proporção mesmo com tudo desconectado. Deitado porque é o formato de quase
# toda arte e de quase todo monitor.
PAISAGEM_PADRAO = (1920, 1080)

_PAREDE_SCHEMA = "org.gnome.desktop.background"
_PAREDE_CHAVE = "picture-uri"
_PAREDE_ESCURA = "picture-uri-dark"


def _display() -> Optional[Gdk.Display]:
    try:
        return Gdk.Display.get_default()
    except Exception:
        return None


class Monitor(NamedTuple):
    """Um monitor ligado: um id estável, onde ele começa, o tamanho e se é o principal."""

    id: str
    x: int
    y: int
    largura: int
    altura: int
    principal: bool = False

    @property
    def em_pe(self) -> bool:
        return self.altura > self.largura


class _Fundo:
    """O papel de parede do GNOME, lido e trocado via GSettings.

    Use como gerenciador de contexto, como a classe que substitui: nada a
    abrir ou fechar, só a mesma forma de usar nos pontos de chamada.
    """

    def __init__(self) -> None:
        try:
            self._config = Gio.Settings.new(_PAREDE_SCHEMA)
        except Exception:
            self._config = None

    def __enter__(self) -> "_Fundo":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def ligados(self) -> list[Monitor]:
        """Os monitores ligados agora, cada um com o seu retângulo."""
        try:
            display = _display()
            monitores = display.get_monitors() if display is not None else None
        except Exception:
            return []
        if display is None or monitores is None:
            return []
        try:
            primario = display.get_primary_monitor()
            conector = primario.get_connector() if primario is not None else None
        except Exception:
            conector = None
        saida = []
        for indice in range(monitores.get_n_items()):
            try:
                monitor = monitores.get_item(indice)
                geo = monitor.get_geometry()
            except Exception:
                continue
            if geo.width <= 0 or geo.height <= 0:
                continue
            try:
                nome = monitor.get_connector() or f"monitor-{indice}"
            except Exception:
                nome = f"monitor-{indice}"
            principal = (nome == conector) if conector else indice == 0
            saida.append(
                Monitor(nome, geo.x, geo.y, geo.width, geo.height, principal)
            )
        return saida

    def papel(self, monitor: str) -> Optional[str]:
        """O papel de parede agora.

        Vazio quando não mostra imagem nenhuma, e ``None`` quando a pergunta
        falhou. As duas respostas não podem se confundir: a primeira é um
        estado a devolver no fim da sessão, a segunda é não saber qual era —
        e aí o monitor não deve ser vestido.
        """
        _ = monitor
        if self._config is None:
            return None
        try:
            return self._config.get_string(_PAREDE_CHAVE) or ""
        except Exception:
            return None

    def em_apresentacao(self) -> bool:
        """Sempre False: sem apresentação de slides para respeitar."""
        return False

    def vestir(self, monitor: str, caminho: str) -> bool:
        """Troca a parede. Diz se o GNOME aceitou."""
        _ = monitor
        if self._config is None or not caminho:
            return False
        try:
            uri = Path(caminho).as_uri()
        except Exception:
            return False
        try:
            self._config.set_string(_PAREDE_CHAVE, uri)
            try:
                self._config.set_string(_PAREDE_ESCURA, uri)
            except Exception:
                pass
            Gio.Settings.sync()
        except Exception as erro:
            logging.warning("Não foi possível trocar o papel de parede: %s", erro)
            return False
        return True


# region Monitores-alvo


def alvos(monitores: list[Monitor]) -> list[Monitor]:
    """Os monitores que recebem a arte: todos menos o principal."""
    return [monitor for monitor in monitores if not monitor.principal]


class Formatos(NamedTuple):
    """O tamanho do maior monitor-alvo de cada orientação; None se não houver."""

    retrato: Optional[tuple[int, int]]
    paisagem: Optional[tuple[int, int]]

    @property
    def grade(self) -> tuple[int, int]:
        """O formato da tela de escolha.

        Em pé só quando todos os alvos estão em pé. No arranjo misto a grade é
        deitada: quase toda arte é deitada, a miniatura a mostra quase inteira,
        e o corte agressivo fica para o ajuste em pé.
        """
        return self.paisagem or self.retrato or PAISAGEM_PADRAO

    @property
    def ratio(self) -> str:
        """Por qual formato a busca começa: o da grade."""
        largura, altura = self.grade
        return "portrait" if altura > largura else "landscape"

    @property
    def minimo(self) -> tuple[int, int]:
        """O ``atleast`` da busca: a imagem que nenhum dos cortes precisa esticar."""
        tamanhos = [tamanho for tamanho in (self.retrato, self.paisagem) if tamanho]
        if not tamanhos:
            return self.grade
        return (
            max(largura for largura, _altura in tamanhos),
            max(altura for _largura, altura in tamanhos),
        )


def formatos(monitores: list[Monitor]) -> Formatos:
    """Os formatos dos monitores-alvo entre ``monitores``.

    Aceita a lista completa ou só os alvos: :func:`alvos` é filtro puro.
    """

    def maior(orientacao: list[Monitor]) -> Optional[tuple[int, int]]:
        if not orientacao:
            return None
        monitor = max(orientacao, key=lambda item: item.largura * item.altura)
        return monitor.largura, monitor.altura

    alvo = alvos(monitores)
    return Formatos(
        maior([monitor for monitor in alvo if monitor.em_pe]),
        maior([monitor for monitor in alvo if not monitor.em_pe]),
    )


def formatos_ligados() -> Formatos:
    """Os formatos dos monitores-alvo ligados agora. Usada pela tela de escolha."""
    try:
        with _Fundo() as area:
            return formatos(area.ligados())
    except OSError:
        return Formatos(None, None)


# endregion
# region Enquadramento


def enquadrar(
    imagem: Image.Image, largura: int, altura: int, posicao: float = 0.5
) -> Image.Image:
    """Recorta ``imagem`` para ``largura`` x ``altura``, na faixa ``posicao``.

    A única regra de enquadramento do recurso, e o motivo de ser uma função
    só: a grade da tela de escolha corta a miniatura de 432px por aqui e o
    arquivo final corta o original de 4K por aqui. Como o ``ImageOps.fit``
    trabalha em proporção, as duas dão o MESMO quadro — o que se vê ao
    escolher é o que vai para o monitor, e não uma aproximação dele.

    ``posicao`` corre no eixo que sobra (0 = esquerda/topo, 1 = direita/pé).
    Qual eixo é esse depende das duas proporções, e é o que
    :func:`eixo_do_corte` responde para quem desenha a barrinha.
    """
    if eixo_do_corte(imagem.width, imagem.height, largura, altura):
        centro = (posicao, CENTRO_VERTICAL)
    else:
        centro = (0.5, posicao)
    return ImageOps.fit(imagem, (largura, altura), Image.LANCZOS, centering=centro)


def eixo_do_corte(
    origem_largura: int, origem_altura: int, largura: int, altura: int
) -> bool:
    """True quando o corte come as laterais; False quando come topo e pé."""
    if not origem_altura or not altura:
        return True
    # Empate devolve lateral: com as duas proporções iguais nada é cortado, e
    # o eixo só decide o texto da dica — que fala do caso comum, a arte larga.
    return (origem_largura / origem_altura) >= (largura / altura)


def corta(origem_largura: int, origem_altura: int, largura: int, altura: int) -> bool:
    """True quando enquadrar a origem no monitor remove alguma parte dela.

    Com meio por cento de folga: a cópia reduzida que a tela de ajuste recorta
    arredonda um pixel aqui e ali, e 1564x880 é a mesma arte 16:9 de 3840x2160.
    """
    if not origem_altura or not altura:
        return False
    alvo = largura / altura
    return abs(origem_largura / origem_altura - alvo) > 0.005 * alvo


def da_capa(capa: Path, largura: int, altura: int) -> Image.Image:
    """A capa do jogo virando papel de parede: inteira, sobre ela mesma borrada.

    Reservado ao caso em que o wallhaven não tem nada: a capa é 2:3 e o monitor
    9:16, e cortar 30% da largura de uma capa apaga metade do título. Aqui o
    corte vai para o FUNDO, que já vai ser borrado, e a capa aparece inteira
    por cima.
    """
    with Image.open(capa) as arquivo:
        imagem = arquivo.convert("RGB")
        fundo = enquadrar(imagem, largura, altura).filter(ImageFilter.GaussianBlur(60))
        escala = min(largura / imagem.width, altura / imagem.height)
        frente = imagem.resize(
            (round(imagem.width * escala), round(imagem.height * escala)), Image.LANCZOS
        )
    fundo.paste(frente, ((largura - frente.width) // 2, (altura - frente.height) // 2))
    return fundo


# endregion
# region Escolha por jogo


class Posicoes(NamedTuple):
    """A faixa escolhida para cada orientação de monitor, de 0 a 1.

    Por orientação e não por monitor: dois monitores em pé mostram o mesmo
    corte, e o id de um monitor muda quando ele troca de porta.
    """

    retrato: float = 0.5
    paisagem: float = 0.5

    def para(self, monitor: Monitor) -> float:
        return self.retrato if monitor.em_pe else self.paisagem


def _sidecar(game_id: str) -> Path:
    return shared.wallpapers_dir / f"{game_id}.json"


def _ler_sidecar(game_id: str) -> Optional[dict[str, Any]]:
    try:
        dados = ler_json(_sidecar(game_id))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as erro:
        logging.warning("Escolha de papel de parede ilegível (%s): %s", _sidecar(game_id).name, erro)
        return None
    return dados if isinstance(dados, dict) else None


def _posicoes(dados: Optional[dict[str, Any]]) -> Posicoes:
    if not dados:
        return Posicoes()
    return Posicoes(
        float(dados.get("position_portrait", 0.5)),
        float(dados.get("position_landscape", 0.5)),
    )


def _gravar_sidecar(
    game_id: str, name: str, arquivo: Optional[str], posicoes: Posicoes, travado: bool
) -> None:
    dados = {
        "name": name,
        "file": arquivo,
        "position_portrait": posicoes.retrato,
        "position_landscape": posicoes.paisagem,
        "timestamp": int(time.time()),
        # Travado é escolha de gente. A busca automática nunca mexe nele, nem
        # quando o jogo é renomeado — foi o usuário que casou aquela arte com
        # aquele jogo, e isso vale mais que qualquer palpite de busca.
        "locked": travado,
    }
    # tmp + replace: uma queda no meio não pode deixar um sidecar truncado,
    # que se lê como "automático" e entrega a escolha travada à busca.
    destino = _sidecar(game_id)
    tmp = destino.with_name(f"{destino.name}.{uuid4().hex}.tmp")
    try:
        shared.wallpapers_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(dados), encoding="utf-8")
        tmp.replace(destino)
    except OSError as erro:
        logging.warning("Não foi possível gravar a escolha de parede: %s", erro)
    finally:
        tmp.unlink(missing_ok=True)


def _arquivo_do_sidecar(dados: Optional[dict[str, Any]]) -> Optional[Path]:
    if not dados or not dados.get("file"):
        return None
    caminho = shared.wallpapers_dir / str(dados["file"])
    return caminho if caminho.is_file() else None


def escolha(game: "Game") -> str:
    """Como a parede deste jogo está decidida: ``auto``, ``manual`` ou ``none``.

    "Não trocar" é o sidecar travado SEM arquivo. Travado com um arquivo que
    sumiu do disco não é essa decisão, é uma escolha que se perdeu — e aí o
    jogo volta à busca automática em vez de ficar sem parede em silêncio.
    """
    dados = _ler_sidecar(game.game_id)
    if not dados or not dados.get("locked"):
        return "auto"
    if not dados.get("file"):
        return "none"
    return "manual" if _arquivo_do_sidecar(dados) else "auto"


def imagem_escolhida(game_id: str) -> Optional[Path]:
    """A imagem que está valendo para ``game_id``, se houver uma em disco."""
    return _arquivo_do_sidecar(_ler_sidecar(game_id))


def posicoes_escolhidas(game_id: str) -> Posicoes:
    """As posições de recorte gravadas para ``game_id``, ou o padrão (0.5,
    0.5) se não houver escolha. Usado pela exportação do backup, que precisa
    ler o que já está decidido sem duplicar o parse do sidecar."""
    return _posicoes(_ler_sidecar(game_id))


def salvar_escolha(
    game_id: str, name: str, origem: Path, posicoes: Posicoes
) -> Optional[Path]:
    """Adota ``origem`` como a parede de ``game_id``. Trava a escolha."""
    sufixo = origem.suffix.lower()
    if sufixo not in IMAGE_SUFFIXES:
        sufixo = ".jpg"

    destino = shared.wallpapers_dir / f"{game_id}{sufixo}"
    try:
        shared.wallpapers_dir.mkdir(parents=True, exist_ok=True)
        _apagar_imagens(game_id, manter=destino.name)
        destino.write_bytes(origem.read_bytes())
    except OSError as erro:
        logging.warning("Não foi possível guardar a parede escolhida: %s", erro)
        return None

    _gravar_sidecar(game_id, name, destino.name, posicoes, travado=True)
    return destino


def nao_trocar(game_id: str, name: str) -> None:
    """Este jogo não mexe na parede. Travado, para a busca não o reencontrar."""
    _apagar_imagens(game_id)
    _gravar_sidecar(game_id, name, None, Posicoes(), travado=True)


def redefinir(game_id: str) -> None:
    """Devolve o jogo à escolha automática."""
    _apagar_imagens(game_id)
    _sidecar(game_id).unlink(missing_ok=True)


def _apagar_imagens(game_id: str, manter: str = "") -> None:
    for sufixo in IMAGE_SUFFIXES:
        arquivo = shared.wallpapers_dir / f"{game_id}{sufixo}"
        if arquivo.name != manter:
            arquivo.unlink(missing_ok=True)


def _fonte(
    game: "Game", largura: int, altura: int, formato: str
) -> Optional[tuple[Path, Posicoes]]:
    """A imagem de partida deste jogo e a faixa dela, baixando se precisar.

    ``largura`` x ``altura`` é o mínimo que os cortes precisam, e ``formato`` é
    por onde a busca começa — o da grade da tela de escolha.

    ``None`` quando o jogo está marcado para não trocar a parede, ou quando
    não sobrou nem busca nem capa.
    """
    dados = _ler_sidecar(game.game_id)
    posicoes = _posicoes(dados)

    if dados and dados.get("locked"):
        if not dados.get("file"):
            return None  # "não trocar"
        if arquivo := _arquivo_do_sidecar(dados):
            return arquivo, posicoes
        # A escolha à mão apontava para um arquivo que sumiu: segue para a
        # busca, como `escolha` já mostra na tela de edição.

    # Uma busca automática que já deu certo fica em disco: a sessão seguinte
    # do mesmo jogo não toca a rede, e a parede aparece no instante em que o
    # bloqueador aparece.
    if (arquivo := _arquivo_do_sidecar(dados)) and dados.get("name") == game.name:
        return arquivo, posicoes

    if achado := melhor_para(game.name, largura, altura, formato):
        try:
            conteudo = download_bytes(str(achado["path"]), timeout=30)
            sufixo = Path(str(achado["path"])).suffix.lower()
            if sufixo not in IMAGE_SUFFIXES:
                sufixo = ".jpg"
            destino = shared.wallpapers_dir / f"{game.game_id}{sufixo}"
            shared.wallpapers_dir.mkdir(parents=True, exist_ok=True)
            _apagar_imagens(game.game_id, manter=destino.name)
            destino.write_bytes(conteudo)
        except Exception as erro:  # pylint: disable=broad-except
            logging.info("Não foi possível baixar a parede do jogo: %s", erro)
        else:
            _gravar_sidecar(
                game.game_id, game.name, destino.name, Posicoes(), travado=False
            )
            return destino, Posicoes()

    # TheGamesDB fanart is the artwork fallback when Wallhaven has no useful
    # result (or is unavailable). It is already associated with the exact game
    # selected during metadata lookup, so no second fuzzy search is needed.
    for url in game.tgdb_fanart or []:
        try:
            conteudo = download_bytes(str(url), timeout=30)
            sufixo = Path(urlparse(str(url)).path).suffix.lower()
            if sufixo not in IMAGE_SUFFIXES:
                sufixo = ".jpg"
            destino = shared.wallpapers_dir / f"{game.game_id}{sufixo}"
            shared.wallpapers_dir.mkdir(parents=True, exist_ok=True)
            _apagar_imagens(game.game_id, manter=destino.name)
            destino.write_bytes(conteudo)
        except Exception as erro:  # pylint: disable=broad-except
            logging.info("Não foi possível baixar a fanart do TheGamesDB: %s", erro)
            continue
        _gravar_sidecar(
            game.game_id, game.name, destino.name, Posicoes(), travado=False
        )
        return destino, Posicoes()

    # Último degrau: a capa. Não fica guardada como fonte — ela já está em
    # `covers`, e uma cópia aqui envelheceria sozinha quando a capa mudasse.
    capa = game.get_cover_path()
    return (capa, Posicoes()) if capa else None


# endregion
# region Aplicar e restaurar

_CHAVE_ORIGINAIS = "session-wallpaper-saved"

# Cada sessão ganha um número, e só a corrente pode vestir as telas. A arte é
# preparada numa thread (rede e Pillow levam segundos), e uma sessão curta
# acaba antes disso: sem o número, a preparação vestiria os monitores DEPOIS da
# devolução, e eles ficariam com a arte de um jogo já fechado até o próximo
# arranque. Só a thread de UI mexe nele; a de trabalho só o carrega de volta.
_sessao = 0


def _cache() -> Path:
    return shared.wallpapers_dir / "cache"


def _limpar_cache() -> None:
    try:
        for arquivo in _cache().glob("*.jpg"):
            arquivo.unlink(missing_ok=True)
    except OSError:
        pass


def comecar(game: "Game") -> None:
    """Começa a vestir as telas para ``game``. Chamar da thread de UI.

    Sem monitor além do principal, desliga a opção em vez de começar: não há
    tela para vestir, e quando um segundo monitor voltar quem religa é o
    usuário, nas Preferências.
    """
    if len(_Fundo().ligados()) < 2:
        shared.schema.set_boolean("session-wallpaper", False)
        return
    threading.Thread(target=aplicar, args=(game, _sessao), daemon=True).start()


def aplicar(game: "Game", sessao: int) -> None:
    """Prepara a arte de ``game`` para cada monitor-alvo. Roda fora da UI.

    Só prepara: os arquivos saem daqui prontos, e quem os põe na parede é
    :func:`_vestir`, de volta na thread de UI, onde a sessão é conferida.

    Nunca levanta: isto é enfeite de sessão, e uma sessão de jogo não pode
    falhar porque um monitor foi desligado ou porque o site saiu do ar.
    """
    try:
        with _Fundo() as area:
            if area.em_apresentacao():
                logging.info("Apresentação de slides ligada; parede intocada")
                return
            monitores = alvos(area.ligados())
        if not monitores:
            return

        arranjo = formatos(monitores)
        if not (fonte := _fonte(game, *arranjo.minimo, formato=arranjo.ratio)):
            return
        origem, posicoes = fonte

        _cache().mkdir(parents=True, exist_ok=True)
        carimbo = int(time.time())
        # A capa entra pela composição com fundo borrado; tudo o mais é
        # arte larga o bastante para o corte.
        da_capa_ = origem.parent == shared.covers_dir
        quadros = []
        for monitor in monitores:
            if da_capa_:
                quadro = da_capa(origem, monitor.largura, monitor.altura)
            else:
                with Image.open(origem) as arquivo:
                    quadro = enquadrar(
                        arquivo.convert("RGB"),
                        monitor.largura,
                        monitor.altura,
                        posicoes.para(monitor),
                    )
            # Nome novo a cada sessão: a parede é lida por caminho, e
            # reescrever o mesmo arquivo com outro conteúdo pode deixar a
            # tela com a imagem antiga.
            destino = _cache() / (
                f"{game.game_id}-{monitor.largura}x{monitor.altura}-{carimbo}.jpg"
            )
            quadro.save(destino, quality=92)
            quadros.append((monitor.id, destino))
    except Exception as erro:  # pylint: disable=broad-except
        logging.warning("Não foi possível preparar a arte da sessão: %s", erro)
        return

    GLib.idle_add(_vestir, sessao, quadros)


def _vestir(sessao: int, quadros: list[tuple[str, Path]]) -> bool:
    """Põe na parede o que :func:`aplicar` preparou. Roda na thread de UI.

    Aqui, e não na thread que preparou, porque é onde a sessão pode ser
    conferida sem corrida com :func:`restaurar`, que também roda aqui.
    """
    if sessao != _sessao:
        # A sessão acabou enquanto a arte era preparada.
        for _monitor, arquivo in quadros:
            try:
                arquivo.unlink(missing_ok=True)
            except OSError:
                pass
        return GLib.SOURCE_REMOVE

    try:
        originais = json.loads(shared.schema.get_string(_CHAVE_ORIGINAIS) or "{}")
    except ValueError:
        originais = {}
    if not isinstance(originais, dict):
        originais = {}

    try:
        with _Fundo() as area:
            # O original de cada monitor é gravado ANTES de vestir: com a chave
            # em disco, um app morto no meio da sessão ainda deixa o caminho de
            # volta. Um monitor que já consta nela (uma devolução anterior que
            # falhou) fica com o valor de lá, porque o que ele mostra agora
            # pode ser a nossa arte.
            for monitor, _arquivo in quadros:
                if monitor not in originais:
                    if (atual := area.papel(monitor)) is not None:
                        originais[monitor] = atual
            if not originais:
                return GLib.SOURCE_REMOVE
            shared.schema.set_string(_CHAVE_ORIGINAIS, json.dumps(originais))

            for monitor, arquivo in quadros:
                # Sem o original não há como voltar, então não se veste.
                if monitor in originais:
                    area.vestir(monitor, str(arquivo))
    except Exception as erro:  # pylint: disable=broad-except
        logging.warning("Não foi possível vestir os monitores: %s", erro)
    return GLib.SOURCE_REMOVE


def restaurar() -> None:
    """Devolve cada monitor ao papel de parede que tinha. Seguro chamar à toa.

    Encerra a sessão corrente antes de tudo, mesmo sem nada a devolver: é
    justamente enquanto a arte ainda está sendo preparada que a chave está
    vazia, e sem isso a preparação vestiria as telas depois desta volta.
    """
    global _sessao  # pylint: disable=global-statement
    _sessao += 1

    guardados = shared.schema.get_string(_CHAVE_ORIGINAIS)
    if not guardados:
        return

    try:
        originais = json.loads(guardados)
    except ValueError:
        originais = {}
    if not isinstance(originais, dict):
        originais = {}

    try:
        with _Fundo() as area:
            # Vazio não volta: sem imagem na chave não há URI para devolver,
            # e o monitor fica com a arte da sessão até a próxima troca.
            recusados = {
                monitor: caminho
                for monitor, caminho in originais.items()
                if not area.vestir(monitor, str(caminho))
            }
    except OSError as erro:
        # A chave fica. Ela é o único registro dos originais, a sessão
        # seguinte não a sobrescreve (só acrescenta monitores que faltem) e o
        # próximo arranque tenta a volta de novo.
        logging.warning("Não foi possível devolver o papel de parede: %s", erro)
        return

    # Pela mesma razão, o que o GNOME recusou fica na chave. Só sai dela
    # quem voltou de fato. O cache vai embora assim mesmo, e mantê-lo à
    # espera de um monitor que nunca mais for ligado o faria crescer a cada
    # sessão.
    shared.schema.set_string(
        _CHAVE_ORIGINAIS, json.dumps(recusados) if recusados else ""
    )
    _limpar_cache()


def restaurar_orfaos() -> None:
    """Desfaz no arranque a troca que uma sessão anterior não desfez."""
    if shared.schema.get_string(_CHAVE_ORIGINAIS):
        logging.info("Papel de parede de uma sessão anterior encontrado; desfazendo")
        restaurar()


# endregion


def imagem_para_textura_bytes(imagem: Image.Image) -> bytes:
    """PNG em memória, que é como o GTK aceita uma imagem do Pillow."""
    buffer = BytesIO()
    # Nível 1: a compressão aqui é pura perda de tempo — o PNG vive alguns
    # milissegundos, entre o Pillow e a textura.
    imagem.save(buffer, "png", compress_level=1)
    return buffer.getvalue()
