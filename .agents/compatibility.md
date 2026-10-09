# Compatibilidade e plataforma

## Escopo suportado

Jolven é um produto Linux. Não há suporte de produto para Windows ou macOS, mesmo
que helpers antigos reconheçam layouts de outros sistemas. Não amplie esses
helpers como se fossem uma promessa multiplataforma sem uma decisão explícita.

O aplicativo desktop deve se comportar em GNOME, KDE e outros ambientes que
ofereçam GTK/portais adequados. Integrações específicas do desktop precisam
detectar suporte e oferecer orientação manual quando indisponíveis.

## Stack mínima

O build de fonte declara aproximadamente:

- Python 3;
- Meson 0.59 ou posterior;
- GTK 4.15 ou posterior;
- libadwaita 1.6 beta ou posterior;
- Blueprint Compiler;
- PyGObject e bibliotecas Python instaladas pelo projeto.

O manifesto Flatpak usa a plataforma GNOME 49 e é a referência mais reprodutível
do ambiente de runtime. Se a versão do manifesto divergir da CI ou dos requisitos
Meson, trate como dívida a ser resolvida, não como três fontes independentes de
verdade.

## Wayland, X11 e Gamescope

Wayland é o caminho primário. O Flatpak ainda expõe sockets Wayland e X11 para
compatibilidade com desktops e launchers. Jolven Session é baseada em Gamescope e
portanto tem requisitos mais estritos que o aplicativo desktop.

O instalador automatiza dependências e a integração com o display manager em
Fedora. Outras distribuições podem executar o desktop e, se fornecerem as
dependências, a sessão; não prometa instalação automática fora do que o script
realmente implementa.

Em sessão dedicada:

- não aninhe Gamescope ao lançar jogos;
- restaure áudio e estado alterado ao encerrar;
- preserve fallback de parâmetros de monitor/FPS;
- nunca execute a sessão como root;
- mantenha a instalação privilegiada pequena e auditável.

## Dependências opcionais

| Capacidade | Dependência | Comportamento sem ela |
| --- | --- | --- |
| xCloud embutido | WebKitGTK 6.0 ou 4.1 | recurso oculto/desabilitado com orientação |
| gamepad | libmanette | teclado e mouse continuam funcionais |
| emulação de entrada | libei/snegg + portal | ação específica indisponível |
| Jolven Session | Gamescope | desktop funciona; sessão não instala/inicia |
| perfil de desempenho | GameMode | jogo inicia sem wrapper |
| overlay | MangoHud | jogo inicia sem overlay |
| controle de áudio | `wpctl`/WirePlumber | não ajustar automaticamente |
| luzes inteligentes | `tinytuya` e Secret Service | integração desabilitada |

Imports dessas capacidades devem ser lazy ou protegidos. Nunca transforme uma
dependência opcional em falha na importação global de `main.py`.

## Flatpak e host

O sandbox recebe permissões de rede, IPC, display, DRI e caminhos necessários
para descobrir launchers e bibliotecas. Antes de ampliar `--filesystem`, sockets
ou acesso a dispositivos, justifique o dado exato e procure um portal.

Um Flatpak não deve tentar gravar diretamente a configuração de sessão gráfica
do host. Integrações de host usam launchers, `flatpak-spawn` quando previsto, ou o
helper instalado fora do sandbox. Teste caminhos nativos e Flatpak separadamente.

## IDs, caminhos e legado

O perfil release usa `io.github.zonaro.Jolven`; desenvolvimento usa um ID com
sufixo `.Devel`. App ID, schema ID, nome de desktop file, D-Bus, prefixo de
GResource e diretórios instalados formam um contrato conjunto.

O projeto migrou de Cartridges para Jolven. Permanecem referências legadas em
variáveis, schemas e diretórios para encontrar dados de usuários existentes.
Regras:

- leia o nome antigo quando necessário;
- escreva no formato/local atual;
- copie antes de migrar e nunca apague automaticamente a fonte;
- torne a migração idempotente;
- não remova aliases apenas porque uma instalação nova não os usa.

## Formato de dados

Jogos têm `spec_version`; backups têm versão própria. A compatibilidade é
conservadora:

- campo novo recebe padrão seguro ao ler registro antigo;
- campo removido não deve destruir dados desconhecidos incidentalmente;
- versão futura é recusada/ignorada, não reinterpretada;
- mudanças incompatíveis exigem migração explícita, teste e documentação;
- arquivos externos e ZIPs passam por validação de caminho e tamanho.

## Desktop e atalhos

Atalhos globais têm backends específicos para GNOME/GSettings e KDE
KGlobalAccel. Em ambientes não reconhecidos, mostre instruções manuais em vez de
alterar arquivos arbitrários. Integração com systemd-logind para energia deve usar
D-Bus e respeitar autorização do sistema.

