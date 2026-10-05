# backup.py
#
# Copyright 2026 joaomgabaldi
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""O backup: um retrato completo do app num .zip.

Leva a pasta do app inteira (jogos, capas, logos, papéis de parede, cores das
fitas, lista de fitas e histórico de sessões) e as configurações do GSettings.
Fica fora só o que não pode ser restaurado com sentido: os logs, o
caminho de volta de uma sessão em andamento e o estado da janela.

Restaurar substitui tudo, com o app fechado: `agendar` copia o .zip para a
pasta do app e Preferências reinicia o app; `aplicar_pendente`, na abertura,
antes de qualquer leitura, troca a pasta e as configurações e cria as
pendências da restauração (`utils/restauracao.py`). Se algo falhar, tudo volta
como estava.
"""

import json
import logging
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from time import time
from typing import Any, Iterable, Optional

from gi.repository import Gio, GLib

from cartridges import shared
from cartridges.utils import game_logo, restauracao, save_cover, session_wallpaper

VERSAO = 5

_CONFIGURACOES = "configuracoes.json"
_AGENDADO = "restaurar.zip"
# Arquivos soltos na raiz da pasta do app que vão no backup. Sem a conta da
# Tuya: ela mora no cofre do usuário, fora da pasta, e não há arquivo para levar.
_SOLTOS = ("fitas.json", "sessions.jsonl")
# O que fica na pasta do app durante a troca: os logs são desta máquina e desta
# execução, e o .zip agendado é a própria fonte da troca.
_FICAM = frozenset({"logs", _AGENDADO})
# Marca, dentro de `.anterior`, que todos os dados de antes já saíram da pasta
# do app: a partir dali, o que estiver nela veio do backup.
_COMPLETO = ".completo"
# Marca, dentro de `.anterior`, que a troca deu certo: o que sobrar da pasta
# (um arquivo que o antivírus segurou) é descarte, nunca dado a devolver.
_CONCLUIDO = ".concluido"

# O caminho de volta de uma sessão em andamento nesta máquina: as telas e as
# fitas como estavam antes do jogo. Restaurado depois, "desfaria" uma troca
# que nunca aconteceu.
_CHAVES_DE_SESSAO = frozenset({"session-wallpaper-saved", "fita-estado-anterior"})

# Do schema de estado, só a ordenação é escolha de alguém. Tamanho e posição da
# janela, o balde do limitador da Steam e a última novidade vista são da máquina.
_CHAVES_DE_ESTADO = ("sort-mode",)

# Nenhum arquivo do app chega perto disso; acima é zip malicioso ou corrompido.
_TAMANHO_MAXIMO_POR_ENTRADA = 500 * 1024 * 1024
_TAMANHO_MAXIMO_TOTAL = 4 * 1024 * 1024 * 1024


def _extensoes() -> dict[str, tuple[str, ...]]:
    """Pasta do app -> extensões que o backup leva dela. Reaproveita as listas
    de cada módulo: uma extensão nova aceita por eles entra aqui sozinha."""
    return {
        "games": (".json",),
        "covers": (*save_cover.ANIMATED_SUFFIXES, ".tiff"),
        "logos": (".json", *game_logo.IMAGE_SUFFIXES),
        "wallpapers": (".json", *session_wallpaper.IMAGE_SUFFIXES),
        "fitas": (".json",),
    }


class BackupInvalido(ValueError):
    """O arquivo não é um backup válido do Jolven (ou está corrompido)."""


# region Configurações


def _filtrar_chaves(chaves: Iterable[str]) -> list[str]:
    """``chaves`` sem as de sessão em andamento. Separada de `_chaves_do_app`
    para poder ser testada sem um `Gio.Settings` de verdade."""
    return [chave for chave in chaves if chave not in _CHAVES_DE_SESSAO]


def _chaves_do_app() -> list[str]:
    return _filtrar_chaves(shared.schema.props.settings_schema.list_keys())


def ler_configuracoes() -> dict[str, Any]:
    """As configurações como vão no backup. Chamar na thread principal.

    Genérico de propósito: uma chave nova no schema entra no backup sozinha, em
    vez de depender de alguém lembrar de acrescentá-la a uma lista.
    """
    return {
        "settings": {
            chave: shared.schema.get_value(chave).unpack() for chave in _chaves_do_app()
        },
        "state": {
            chave: shared.state_schema.get_value(chave).unpack()
            for chave in _CHAVES_DE_ESTADO
        },
    }


def _variante(chave: Gio.SettingsSchemaKey, valor: Any) -> Optional[GLib.Variant]:
    """``valor`` como o schema o guarda, ou ``None`` se o schema não o aceita."""
    tipo = chave.get_value_type().dup_string()
    # GLib.Variant("b", ...) aceita qualquer coisa e a torna verdadeira; um
    # "sim" digitado à mão ligaria a opção em vez de ser descartado.
    if tipo == "b" and not isinstance(valor, bool):
        return None
    try:
        variante = GLib.Variant(tipo, valor)
    except (TypeError, ValueError, OverflowError):
        return None
    # Fora das opções do schema (um `sort-mode` que não existe): o GSettings
    # recusaria com um aviso crítico e deixaria o valor de antes no lugar.
    return variante if chave.range_check(variante) else None


def _aplicar(settings: Any, valores: Any, chaves: Iterable[str]) -> None:
    valores = valores if isinstance(valores, dict) else {}
    schema = settings.props.settings_schema
    for chave in chaves:
        variante = None
        if chave in valores:
            variante = _variante(schema.get_key(chave), valores[chave])
            if variante is None:
                logging.warning("Configuração %s ignorada no backup: valor inválido", chave)
        if variante is None:
            # Idêntico ao backup: o que ele não tem, ou tem num valor que esta
            # versão não aceita, fica no padrão.
            settings.reset(chave)
        else:
            settings.set_value(chave, variante)


def aplicar_configuracoes(configuracoes: dict[str, Any]) -> None:
    """Deixa as configurações como estão em ``configuracoes``."""
    _aplicar(shared.schema, configuracoes.get("settings"), _chaves_do_app())
    _aplicar(shared.state_schema, configuracoes.get("state"), _CHAVES_DE_ESTADO)
    Gio.Settings.sync()


# endregion

# region Exportar


def _entradas() -> list[tuple[Path, str]]:
    """(arquivo no disco, nome no zip) de tudo o que o backup leva."""
    entradas = [
        (shared.app_dir / nome, nome)
        for nome in _SOLTOS
        if (shared.app_dir / nome).is_file()
    ]
    for pasta, extensoes in _extensoes().items():
        diretorio = shared.app_dir / pasta
        if not diretorio.is_dir():
            continue
        for caminho in sorted(diretorio.iterdir()):
            if caminho.is_file() and caminho.suffix.lower() in extensoes:
                entradas.append((caminho, f"{pasta}/{caminho.name}"))
    return entradas


def _incluir(arquivo: zipfile.ZipFile, caminho: Path, nome: str) -> None:
    # As imagens já vêm comprimidas: passar deflate nelas custa segundos e não
    # ganha nada. Só o texto é comprimido.
    compressao = (
        zipfile.ZIP_DEFLATED if caminho.suffix in (".json", ".jsonl") else zipfile.ZIP_STORED
    )
    try:
        arquivo.write(caminho, nome, compress_type=compressao)
    except FileNotFoundError:
        # Apagado entre a listagem e a leitura (uma capa trocada agora): o
        # backup sai sem ele, em vez de não sair.
        logging.debug("%s sumiu durante o backup", caminho)


def exportar(destino: Path, configuracoes: dict[str, Any]) -> None:
    """Grava o backup em ``destino``. Pode rodar fora da thread principal;
    ``configuracoes`` vem de `ler_configuracoes`, lida na principal."""
    manifesto = {
        "version": VERSAO,
        "app_version": shared.VERSION,
        "created": int(time()),
        **configuracoes,
    }
    # Temporário + troca: exportar por cima de um backup antigo e cair no meio
    # deixaria o antigo perdido e o novo ilegível.
    temporario = destino.with_name(destino.name + ".tmp")
    try:
        # strict_timestamps=False: um arquivo com data anterior a 1980 (que o
        # formato zip não representa) entra com 1980, em vez de abortar tudo.
        with zipfile.ZipFile(temporario, "w", strict_timestamps=False) as arquivo:
            arquivo.writestr(
                _CONFIGURACOES,
                json.dumps(manifesto, indent=2, ensure_ascii=False),
                compress_type=zipfile.ZIP_DEFLATED,
            )
            for caminho, nome in _entradas():
                _incluir(arquivo, caminho, nome)
        temporario.replace(destino)
    finally:
        temporario.unlink(missing_ok=True)


# endregion

# region Validar


def _nome_valido(nome: str, extensoes_por_pasta: dict[str, tuple[str, ...]]) -> bool:
    """Só o que `exportar` grava: o manifesto, um arquivo solto conhecido, ou
    ``<pasta conhecida>/<arquivo com extensão conhecida>``. Nada absoluto,
    nada com ``..``, nada em subpasta."""
    if nome in (_CONFIGURACOES, *_SOLTOS):
        return True
    if "\\" in nome or ":" in nome:
        return False
    partes = PurePosixPath(nome).parts
    if len(partes) != 2:
        return False
    pasta, arquivo = partes
    extensoes = extensoes_por_pasta.get(pasta)
    if extensoes is None or arquivo.startswith("."):
        return False
    return PurePosixPath(arquivo).suffix.lower() in extensoes


def validar(caminho: Path) -> dict[str, Any]:
    """O manifesto de um backup. ``BackupInvalido`` se não for um."""
    try:
        with zipfile.ZipFile(caminho) as arquivo:
            total = 0
            extensoes = _extensoes()
            for info in arquivo.infolist():
                if not _nome_valido(info.filename, extensoes):
                    raise BackupInvalido(f"entrada inesperada no backup: {info.filename}")
                if info.file_size > _TAMANHO_MAXIMO_POR_ENTRADA:
                    raise BackupInvalido(f"entrada grande demais no backup: {info.filename}")
                total += info.file_size
                if total > _TAMANHO_MAXIMO_TOTAL:
                    raise BackupInvalido("backup grande demais")
            # O CRC de cada entrada: um byte trocado numa capa passaria daqui e
            # só estouraria no meio da troca.
            if (corrompida := arquivo.testzip()) is not None:
                raise BackupInvalido(f"entrada corrompida no backup: {corrompida}")
            manifesto = json.loads(arquivo.read(_CONFIGURACOES).decode("utf-8"))
    except BackupInvalido:
        raise
    except Exception as erro:  # pylint: disable=broad-exception-caught
        raise BackupInvalido(str(erro)) from erro

    if not isinstance(manifesto, dict) or manifesto.get("version") != VERSAO:
        raise BackupInvalido("o arquivo não é um backup desta versão")
    if not isinstance(manifesto.get("settings"), dict):
        raise BackupInvalido("backup sem o bloco de configurações")
    return manifesto


# endregion

# region Restaurar


def agendar(origem: Path) -> None:
    """Copia ``origem`` para a pasta do app. A troca acontece na próxima
    abertura (`aplicar_pendente`); quem chama reinicia o app."""
    destino = shared.app_dir / _AGENDADO
    temporario = destino.with_name(destino.name + ".tmp")
    try:
        shutil.copyfile(origem, temporario)
        temporario.replace(destino)
    finally:
        temporario.unlink(missing_ok=True)


def _irma(sufixo: str) -> Path:
    return shared.app_dir.with_name(shared.app_dir.name + sufixo)


def _devolver(anterior: Path) -> None:
    """Põe de volta na pasta do app os dados que a troca tirou dela.

    Antes da marca `_COMPLETO`, nada do backup entrou ainda: só se movem de
    volta os que já tinham saído. Depois dela, tudo o que está na pasta do app
    (fora `_FICAM`) veio do backup e sai primeiro.
    """
    if not anterior.is_dir():
        return
    if (anterior / _CONCLUIDO).exists():
        # Sobra de uma troca que deu certo: devolver apagaria a biblioteca
        # restaurada e tudo o que veio depois dela.
        shutil.rmtree(anterior, ignore_errors=True)
        return
    if (anterior / _COMPLETO).exists():
        for item in list(shared.app_dir.iterdir()):
            if item.name in _FICAM:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    for item in list(anterior.iterdir()):
        if item.name != _COMPLETO:
            item.replace(shared.app_dir / item.name)
    shutil.rmtree(anterior)


def aplicar_pendente() -> Optional[bool]:
    """Na abertura, antes de carregar a biblioteca: aplica o backup agendado.

    ``None``: nada agendado. ``True``: restaurado. ``False``: falhou, e os
    dados e as configurações de antes voltaram ao lugar.
    """
    anterior, extraido, lixo = _irma(".anterior"), _irma(".restaurando"), _irma(".lixo")
    shutil.rmtree(lixo, ignore_errors=True)
    # Uma troca que caiu no meio (app morto, PC desligado) deixa `.anterior`:
    # os dados de antes voltam antes de qualquer outra coisa.
    try:
        _devolver(anterior)
    except OSError:
        logging.exception("Não foi possível recuperar a troca interrompida do backup")
        return False

    agendado = shared.app_dir / _AGENDADO
    if not agendado.is_file():
        return None

    configuracoes_antes = ler_configuracoes()
    try:
        manifesto = validar(agendado)
        shutil.rmtree(extraido, ignore_errors=True)
        with zipfile.ZipFile(agendado) as arquivo:
            arquivo.extractall(extraido)
        (extraido / _CONFIGURACOES).unlink()

        anterior.mkdir()
        for item in list(shared.app_dir.iterdir()):
            if item.name not in _FICAM:
                item.replace(anterior / item.name)
        (anterior / _COMPLETO).touch()
        for item in list(extraido.iterdir()):
            item.replace(shared.app_dir / item.name)

        restauracao.gravar(
            restauracao.ids_que_dependem_de_atalho(shared.games_dir), int(time())
        )
        aplicar_configuracoes(manifesto)
    except Exception:  # pylint: disable=broad-exception-caught
        logging.exception("Não foi possível restaurar o backup")
        try:
            _devolver(anterior)
            aplicar_configuracoes(configuracoes_antes)
        except Exception:  # pylint: disable=broad-exception-caught
            logging.exception("Não foi possível desfazer a restauração do backup")
        resultado = False
    else:
        # Marcada e renomeada antes de apagar: uma queda no meio do rmtree, ou
        # um arquivo preso que impeça a renomeação, deixaria um `.anterior`
        # que a próxima abertura tomaria por uma troca interrompida. Com a
        # marca, ela só descarta; o que sobrar em `.lixo` sai na abertura
        # seguinte.
        try:
            (anterior / _CONCLUIDO).touch()
            anterior.replace(lixo)
        except OSError:
            logging.exception("Não foi possível descartar os dados de antes da restauração")
        shutil.rmtree(lixo, ignore_errors=True)
        resultado = True
    finally:
        shutil.rmtree(extraido, ignore_errors=True)
        agendado.unlink(missing_ok=True)
    return resultado


# endregion
