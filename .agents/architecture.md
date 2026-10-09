# Arquitetura e fluxo de dados

## Mapa de componentes

```text
jolven.in
  └─ CartridgesApplication (main.py)
       ├─ RuntimeContext ── modo desktop/sessão/aninhado
       ├─ CartridgesWindow ── navegação, busca e ações globais
       ├─ Store ── registro de Game por base_source
       │    ├─ Pipeline
       │    │    ├─ SteamAPIManager / TheGamesDBManager / SgdbManager
       │    │    ├─ CoverManager
       │    │    ├─ FileManager ── JSON local
       │    │    └─ DisplayManager ── widgets, grupos e filtros
       │    └─ importers/* ── descoberta por launcher/plataforma
       ├─ ProcessSession ── processo, retorno e tempo jogado
       ├─ xCloud ── catálogo remoto e WebView opcional
       └─ integrações de sistema ── gamepad, logind, áudio, luzes
```

O projeto não segue MVC puro. `Game` é simultaneamente a entidade persistida e
um `Gtk.Box`. Essa decisão reduz adaptação entre modelo e cartões da biblioteca,
mas torna obrigatório separar computação em background de mutações visuais.

## Inicialização

O executável é gerado de `cartridges/jolven.in` e entra em `cartridges/main.py`.
`CartridgesApplication`, uma subclasse de `Adw.Application`, é responsável por:

1. inicializar logging e migrações seguras;
2. restaurar backup pendente e estado de integrações;
3. detectar o `RuntimeContext`;
4. iniciar gamepads e criar a janela;
5. carregar preferências e geometria;
6. criar `Store` e seus gerenciadores;
7. carregar jogos persistidos;
8. registrar e executar os importadores habilitados;
9. sincronizar catálogos opcionais e tarefas em background;
10. ordenar, apresentar e focar a interface conforme o modo.

A aplicação usa o comportamento de instância única de `GApplication`. Os helpers
históricos de single-instance são no-op no Linux e não devem virar uma segunda
trava de processo.

## Modos de execução

`RuntimeContext` centraliza três estados:

- `DESKTOP`: janela normal integrada ao desktop;
- `GAME_SESSION`: processo principal dentro da Jolven Session/Gamescope;
- `NESTED_GAME_MODE`: experiência de sessão executada em janela para teste.

Flags como `--game-mode`, `--session` e `--windowed`, além de
`JOLVEN_GAME_SESSION`, são normalizadas nesse contexto. Variáveis legadas com
prefixo `CARTRIDGES_` ainda podem existir por compatibilidade. Código novo deve
consultar o contexto, nunca reconstruir a detecção localmente.

## Configuração e recursos

`cartridges/shared.py.in` é transformado pelo Meson e reúne IDs da aplicação,
versão, schemas, caminhos de instalação e estado compartilhado. Pontos críticos:

- `PREFIX` é o prefixo lógico do GResource, por exemplo
  `/io/github/zonaro/Jolven`; não representa `/usr` ou `~/.local`;
- o perfil de desenvolvimento usa App ID e prefixo distintos;
- `_detect_prefix` permite uma instalação relocável;
- caminhos de dados devem respeitar XDG e os helpers existentes.

Templates Blueprint são compilados em `.ui` e incluídos no recurso descrito por
`data/jolven.gresource.xml.in`. Classes Python os carregam com
`@Gtk.Template(resource_path=shared.PREFIX + "/gtk/…ui")`.

## Modelo e identidade

`cartridges/game.py` define `Game`. Entre os dados persistidos estão identidade,
nome, executável/URI, fonte, plataformas, arte, tempo jogado, última execução,
estado, avaliação, notas, atualização, tamanho instalado, perfil de lançamento e
plano de fundo.

Identidade tem duas camadas:

- `source`: variante ou origem concreta que pode executar o jogo;
- `base_source`: identidade usada pelo `Store` para registro e agrupamento.

Importadores devem produzir IDs estáveis entre varreduras. Agrupamento de cópias
do mesmo título e escolha da variante de lançamento vivem em
`utils/agrupamento.py`; não devem ser reproduzidos nos importadores.

`SPEC_VERSION` atualmente governa a compatibilidade do JSON de jogos. Arquivos
mais antigos recebem padrões e migrações; registros de uma versão mais nova são
ignorados para impedir perda de campos por regravação.

## Store, gerenciadores e Pipeline

`Store` mantém os jogos indexados por `base_source` e coordena gerenciadores.
Ao adicionar um jogo, ele valida versão, duplicação e substituição, conecta os
sinais `save-ready`/`update-ready` e inicia um `Pipeline`.

Cada gerenciador declara se bloqueia a sequência e quais outros gerenciadores
devem terminar antes. O pipeline atual cobre:

- enriquecimento por Steam, TheGamesDB e SteamGridDB;
- seleção/armazenamento de capa;
- persistência via `FileManager`;
- apresentação, agrupamento e filtros via `DisplayManager`.

Tarefas assíncronas usam `Gio.Task`. Dependências devem ser declaradas no grafo
`run_after`; não use sleeps, polling na UI ou ordem acidental de registro.

## Importação

Os importadores em `cartridges/importer/` derivam de `Source`, `SourceIterable`,
`ExecutableFormatSource` ou `URLExecutableSource`. `Locations` resolve caminhos
candidatos em instalações nativas, Flatpak e layouts conhecidos.

Cada fonte é executada em worker. Um item pode retornar:

- um `Game`;
- `(Game, additional_data)`, para dados como imagem local, ícone ou Steam App ID;
- `None`, quando o item deve ser ignorado.

O worker não pode criar, consultar ou modificar widgets. A reconciliação no
`Store` acontece na thread principal. Remoção de jogos desaparecidos só é segura
para fontes habilitadas que concluíram uma varredura inteira e foram incluídas em
`scanned_source_ids`.

Fontes cobertas hoje incluem Steam, Lutris, Heroic (Epic, GOG, Amazon e sideload),
Bottles, Dolphin, itch, Legendary, RetroArch, Yuzu, TwinTail, Flatpak, arquivos
`.desktop` e Waydroid. xCloud segue um fluxo próprio de catálogo remoto.

## Persistência, migração e backup

O `FileManager` persiste a biblioteca em JSON na área de dados XDG do Jolven.
Capas, logos, wallpapers, fitas e outros artefatos ficam em subdiretórios da
mesma área. Configurações ficam em GSettings e segredos elegíveis ficam no Secret
Service.

Há migrações de nomes antigos do Cartridges para Jolven. A regra é copiar sem
apagar a origem e tornar a operação repetível. Uma falha intermediária não pode
deixar a biblioteca original inutilizável.

O formato de backup tem sua própria versão e validação estrita. A restauração:

- bloqueia path traversal e entradas inválidas;
- impõe limites por arquivo e no total;
- prepara a troca com rollback;
- é concluída em reinicialização quando necessário;
- exclui logs, estado efêmero e credenciais de nuvem da Tuya.

Não relaxe essas garantias para aceitar arquivos “quase válidos”.

## Lançamento e acompanhamento de processos

`game_launch.py` compõe comandos de forma testável. A ordem é relevante:
MangoHud fica dentro de `gamemoderun` e Gamescope, quando habilitado, envolve o
comando resultante. Na Jolven Session não se deve aninhar outra instância de
Gamescope.

O perfil por jogo pode definir GameMode, MangoHud, diretório de trabalho,
variáveis de ambiente, resolução/FPS/escalonamento do Gamescope e rastreamento
explícito do processo.

`Game.launch` trata xCloud separadamente e delega executáveis locais ao launcher.
`ProcessSession` acompanha grupo de processos e descendentes, incluindo padrões
de Steam, Flatpak e instaladores, registra tempo jogado e devolve foco à interface
de sessão após o encerramento.

## Jolven Session

O script `jolven-session` inicia Gamescope em um grupo de processos próprio,
configura ambiente Wayland, monitor, VRR, FPS, cursor e áudio, tenta parâmetros
seguros em caso de falha e restaura o áudio ao sair. Ele nunca deve rodar como
root.

A instalação do marcador de sessão do display manager é feita por um helper
privilegiado limitado, autorizado por polkit. O helper aceita somente operações
e destinos fixos; não o transforme em um executor genérico. Arquivos de sessão
que não sejam reconhecidamente gerenciados pelo Jolven não podem ser removidos.

