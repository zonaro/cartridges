# Serviços e integrações

## Política geral

Integrações externas enriquecem a biblioteca, mas não são autoridade sobre os
dados locais. Todas devem seguir estas regras:

- rede fora da thread GTK;
- HTTPS, timeout, limite de resposta e parsing defensivo;
- cache local utilizável durante indisponibilidade quando fizer sentido;
- erro isolado, mensagem amigável e nenhuma falha na inicialização;
- autenticação opcional e armazenamento apropriado à sensibilidade;
- nenhum token, segredo, chave local ou URL autenticada em logs;
- mocks nos testes, sem depender da rede pública;
- validação estrita antes de abrir URI ou executar conteúdo retornado.

## Metadados e arte

### Steam

O projeto consulta endpoints públicos da Steam Store para busca, dados e reviews.
Não há chave de usuário. Respeite o limitador persistente já existente e mantenha
resultados parciais: falha da Steam não pode apagar nome, capa ou metadados locais.

### SteamGridDB

A API v2 fornece capas e logos. A chave é informada pelo usuário e guardada em
GSettings. Não a inclua em logs, backups de diagnóstico ou mensagens de erro.
Trate ausência e erro de autenticação como recurso não configurado.

### TheGamesDB

A API v1 fornece metadados, screenshots, fanart e fallback de capa. Usa chave do
usuário. Respostas incompletas e limites do serviço são normais; preserve dados
existentes e apresente erro de autenticação de forma acionável.

### IGDB / Twitch OAuth

IGDB v4 é usado em seletores de metadados e imagens, autenticado por Client ID e
Client Secret via Twitch OAuth. A implementação atual armazena essas preferências
em GSettings, não no Secret Service. Trate-as como segredo mesmo assim e, se o
armazenamento mudar, inclua migração sem expor valores.

### Wallhaven

Fornece wallpapers. A chave é opcional e necessária a determinados filtros de
pureza. Filtros e preferências ficam em GSettings. Downloads precisam verificar
tipo, tamanho e URL antes de substituir um wallpaper local.

### HowLongToBeat

A consulta usa o site público, sem API oficial ou chave. O formato pode mudar sem
aviso; parsing deve falhar de forma silenciosa/explicável, sem impedir a edição do
jogo. Não considere dados raspados estáveis para migrações ou identidade.

## xCloud e Better xCloud

O catálogo xCloud usa feeds públicos de catálogo/Game Pass e cria variantes
como gratuito, Game Pass e adquirido. O jogo abre em WebKit quando disponível;
sem WebKit, a biblioteca local continua operacional.

Better xCloud é um userscript remoto obtido da versão mais recente no GitHub,
armazenado em cache e injetado na página. Isso é execução de código remoto e deve
ser tratado como uma fronteira de segurança:

- aceite apenas origens e domínios esperados;
- valide status, conteúdo e tamanho antes de gravar/injetar;
- mantenha cache conhecido para indisponibilidade;
- torne a funcionalidade lazy e opcional;
- não amplie a lista de URLs nem permissões WebKit por conveniência;
- mantenha navegação fora do domínio permitido bloqueada ou externalizada.

## Tuya / Smart Life

A integração de fitas usa `tinytuya`. Descoberta inicial pode consultar a nuvem;
o uso diário prefere controle local na LAN. Access ID, secret e região ficam no
Secret Service. Chaves locais por dispositivo ficam no arquivo de configuração
de fitas porque são necessárias ao controle LAN, mas continuam sendo segredos.

Credenciais de nuvem não entram no backup. Logs não podem conter payloads de
autenticação, chaves ou identificadores desnecessários. Ao aplicar iluminação de
sessão, registre estado suficiente para restaurar a condição anterior mesmo após
erro parcial.

## Feed de atualizações e notícias

Há consumo opcional de RSS do FitGirl para avisos de atualização por jogo e
notícias. Por ser conteúdo remoto não confiável e potencialmente sensível a
políticas locais:

- mantenha o acompanhamento opt-in por jogo;
- aceite apenas HTTPS e URLs seguras;
- use parser sem DTD/entidades externas e imponha limites;
- trate título, descrição e links como texto não confiável;
- não faça download de conteúdo ou executáveis a partir do feed;
- falha do feed nunca deve interferir no lançamento de jogos.

## GitHub

GitHub Releases e conteúdo raw são usados na distribuição do aplicativo e na
obtenção do Better xCloud. Fixe repositório/origem esperados, siga redirects com
limite e diferencie “versão não encontrada” de falha de rede. Publicação de
release é responsabilidade dos workflows e scripts do repositório, não de código
executado durante a inicialização do aplicativo.

## Launchers e protocolos

Importadores e launchers integram Steam, Lutris, Heroic, Bottles, itch,
Legendary, RetroArch, Dolphin, Yuzu, TwinTail, Waydroid, Flatpak e arquivos
`.desktop`. Valores vindos dessas fontes são não confiáveis.

- Preserve argumentos como lista sempre que possível; evite `shell=True`.
- Valide esquemas de URI e IDs antes de compor protocolos.
- Não execute o campo genérico de um `.desktop` sem o parser/sanitização existente.
- Resolva instalações nativas e Flatpak pelos helpers de `Locations`.
- Ausência de launcher significa “fonte indisponível”, não biblioteca corrompida.

### Sunshine

Exportação opcional de jogos para o Sunshine (`lizardbyte/sunshine`),
inspirada no LutrisToSunshine, por escrita direta no `apps.json`
(`~/.config/sunshine/apps.json`, com override em
`sunshine-apps-path`). O payload segue os mesmos campos
(`name`, `cmd`, `working-dir`, `image-path`, `auto-detach`,
`exit-timeout`…); o `cmd` reaproveita o executável do jogo com o prefixo
`env` de `run_executable.py` e a capa local quando existir.

- Opt-in duplo: ação “Add to Sunshine” no menu do jogo e toggle
  `sunshine-auto-sync` (desligado por padrão) via `SunshineManager`
  (pós-`FileManager`, sinal `save-ready`).
- Upsert atômico por nome exato, preservando entradas alheias e a chave
  `env`; JSON inválido nunca é sobrescrito.
- Xbox Cloud Gaming (`base_source == xcloud`), jogos removidos e jogos
  sem executável são sempre ignorados.
- Sem rede, sem auth e sem segredos: falha é não fatal (toast/log) e
  nunca impede o uso da biblioteca local.
- A página Sunshine nas Preferências (item Sunshine no menu do
  aplicativo) centraliza exportação e diagnóstico: além do grupo de
  exportação, há conexão com a Web UI local (`sunshine-host`,
  `sunshine-port`, `sunshine-username` em GSettings; senha só em
  memória, nunca persistida nem logada), teste que exibe versão e
  quantidade de apps via `GET /api/apps` + `GET /api/config` com Basic
  auth e sem header `Origin` (isento de CSRF), lista somente-leitura
  dos apps e abertura da Web UI. `POST /api/config`, PIN e clientes
  ficam para uma v2 (config exige GET→merge→POST completo + restart;
  índice de app é racy pois a lista reordena por nome).
- Flatpak: o Jolven confinado não enxerga o `~/.config` do host; nesse
  caso aponte `sunshine-apps-path` para um caminho acessível.

## Integrações do sistema

### GPU Screen Recorder

O overlay de jogo da Jolven Session pode abrir uma instalação local do GPU
Screen Recorder. A integração procura primeiro o desktop app oficial, o que
inclui o pacote Flatpak, e só então os executáveis nativos conhecidos. Ela não
configura gravação, não acessa arquivos de vídeo, não usa rede nem armazena
credenciais. Ausência ou falha do aplicativo externo deve produzir apenas um
aviso recuperável e nunca encerrar o jogo ou a sessão.

| Sistema | Uso | Restrição |
| --- | --- | --- |
| systemd-logind | suspender/desligar/reiniciar | via D-Bus e autorização normal |
| Secret Service | credenciais Tuya | nunca fallback para log ou arquivo público |
| xdg-desktop-portal/libei | emulação autorizada de entrada | sem acesso bruto a dispositivos |
| PipeWire/WirePlumber | volume e sink da sessão | restaurar estado ao sair |
| `/sys/class/drm` | detecção de monitor/VRR | somente leitura |
| polkit | instalar/remover sessão | operações fixas, sem comando arbitrário |
| Gamescope | compositor da Jolven Session | não aninhar em sessão dedicada |
| GameMode/MangoHud | wrappers por jogo | opcionais e composicionalmente testados |
