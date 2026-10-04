Você está trabalhando no meu fork do Cartridges.

Seu objetivo é transformar o aplicativo em um launcher que possa funcionar em DOIS MODOS distintos, sem quebrar o comportamento desktop existente:

1. DESKTOP MODE
2. GAME SESSION MODE

O aplicativo deve continuar funcionando normalmente como um agregador de bibliotecas dentro do GNOME, KDE ou outro desktop, exatamente como já funciona hoje.

Além disso, ele deve poder ser instalado como uma sessão gráfica independente selecionável no GDM, semelhante ao SteamOS Game Mode / Steam Big Picture Session.

Antes de alterar qualquer código, analise completamente o projeto atual, sua linguagem, framework gráfico, sistema de build, arquitetura, entrypoints, gerenciamento de processos e suporte atual a controle/gamepad.

Não reescreva o projeto desnecessariamente. Preserve a arquitetura existente sempre que possível.

# OBJETIVO FINAL

Quero poder ligar o computador e, na tela do GDM, escolher algo semelhante a:

GNOME
Cartridges Game Mode

Ao entrar em "Cartridges Game Mode", NÃO quero carregar GNOME Shell.

A arquitetura desejada é aproximadamente:

GDM
└── Cartridges Game Mode
    └── Gamescope
        └── Cartridges
            ├── Steam
            ├── Heroic
            ├── Lutris
            ├── jogos nativos
            └── emuladores

O Cartridges deve ser a interface principal dessa sessão.

Ao fechar o Cartridges no modo sessão, a sessão gráfica deve terminar e retornar ao GDM.

O aplicativo deve continuar podendo ser iniciado normalmente dentro do GNOME:

cartridges

ou pelo menu de aplicativos.

# PRINCÍPIO FUNDAMENTAL

NÃO crie dois aplicativos separados.

Deve existir um único projeto e, idealmente, um único binário/aplicativo com modos diferentes de inicialização.

Exemplos aceitáveis:

cartridges
cartridges --game-mode
cartridges --session

ou arquitetura equivalente, caso o projeto possua outra convenção mais adequada.

Detecte automaticamente quando estiver executando dentro da sessão dedicada sempre que possível.

# 1. MODO DESKTOP

Preserve integralmente o comportamento atual.

Neste modo:

- deve funcionar dentro do GNOME;
- deve funcionar como aplicação desktop normal;
- não deve iniciar Gamescope;
- não deve assumir controle da sessão;
- não deve alterar resolução;
- não deve impedir multitarefa;
- não deve alterar configurações globais do sistema;
- fechar o aplicativo deve simplesmente fechar o aplicativo.

A instalação do Game Mode NÃO pode prejudicar esse comportamento.

# 2. GAME SESSION MODE

Implemente um modo apropriado para execução como shell de uma sessão gamer.

Quando executado neste modo:

- iniciar em fullscreen;
- interface navegável por gamepad;
- ocultar elementos de janela desktop quando possível;
- evitar dependências do GNOME Shell;
- funcionar corretamente dentro do Gamescope;
- assumir que é o frontend principal da sessão;
- manter foco de maneira previsível;
- recuperar foco quando jogos forem encerrados;
- retornar à interface depois que um jogo fechar;
- oferecer ações de energia;
- encerrar a sessão quando o frontend for encerrado.

# 3. GAMESCOPE

Utilize Gamescope como compositor da sessão gamer.

Não rode:

GNOME -> Gamescope -> Cartridges

na sessão gamer.

A estrutura deve ser:

login manager -> Gamescope -> Cartridges

Implemente um launcher/script de sessão robusto.

Algo conceitualmente semelhante a:

gamescope <opções adequadas> -- cartridges --game-mode

MAS NÃO copie isso cegamente.

Antes, descubra:

- versão atual do Gamescope instalada;
- parâmetros suportados;
- ambiente gráfico utilizado;
- GPU;
- Wayland/DRM disponível;
- melhores opções compatíveis.

Evite parâmetros experimentais obrigatórios que possam impedir o login.

Recursos opcionais como HDR e VRR devem possuir fallback seguro.

# 4. GAMEMODE

Adicione integração com Feral GameMode.

Verifique primeiro se GameMode está instalado.

Caso não esteja, o instalador deve oferecer/realizar a instalação das dependências adequadas no Fedora.

Use os mecanismos corretos para Fedora.

Não faça downloads arbitrários da internet se os pacotes existirem nos repositórios do sistema.

A integração deve permitir que jogos executados pelo launcher utilizem GameMode.

Investigue qual estratégia é mais correta:

gamemoderun <game>

ou API/libgamemode

e escolha a solução mais apropriada para a arquitetura do projeto.

Evite aplicar GameMode indiscriminadamente ao sistema inteiro.

# 5. DEPENDÊNCIAS

Implemente um processo de instalação da sessão gamer.

No Fedora, detecte e instale, quando necessário, dependências como:

- gamescope
- gamemode
- componentes Wayland necessários
- bibliotecas necessárias para gamepad
- outras dependências justificadas pela implementação

ANTES de instalar, verifique se cada pacote já existe.

Use o gerenciador de pacotes correto do sistema.

Não reinstale pacotes desnecessariamente.

Não remova pacotes do usuário.

Não altere configurações de GPU globalmente.

Se alguma dependência for opcional, trate como opcional e informe adequadamente.

# 6. INSTALAÇÃO DA SESSÃO

Implemente uma forma oficial e reproduzível de instalar o Cartridges Game Mode como sessão gráfica.

Crie os arquivos corretos para o display manager, conforme necessário.

Investigue a convenção atual do Fedora e do GDM em vez de assumir caminhos antigos.

Possíveis locais podem envolver:

/usr/share/wayland-sessions/

ou equivalente atual.

Crie algo equivalente a:

Cartridges Game Mode

com um Exec apontando para um script/entrypoint controlado pelo projeto.

Exemplo conceitual:

[Desktop Entry]
Name=Cartridges Game Mode
Comment=Gaming session powered by Cartridges
Exec=/usr/bin/cartridges-session
Type=Application

Adapte os campos ao padrão correto encontrado no sistema.

Considere o usuario logado como um perfil de jogo oficial, modificando a interface e exibindo o Nome do usuario e o Avatar do usuário, se possível.

# 7. SCRIPT/ENTRYPOINT DE SESSÃO

Crie um entrypoint dedicado e robusto, por exemplo:

cartridges-session

Responsabilidades:

1. configurar apenas as variáveis necessárias;
2. iniciar Gamescope;
3. iniciar Cartridges em Game Mode;
4. encaminhar sinais corretamente;
5. não deixar processos órfãos;
6. encerrar Gamescope quando Cartridges terminar;
7. encerrar a sessão corretamente;
8. retornar ao display manager quando necessário.

Evite scripts frágeis contendo apenas:

gamescope -- cartridges

Implemente signal handling e cleanup apropriados.

# 8. CICLO DE VIDA DOS JOGOS

Este ponto é crítico.

Quando um jogo for iniciado:

Cartridges
↓
launcher/jogo
↓
jogo executando
↓
jogo termina
↓
Cartridges volta para primeiro plano

O Cartridges NÃO deve fechar quando um jogo for aberto.

Implemente gerenciamento adequado de processos.

Considere jogos iniciados através de:

- Steam
- Heroic
- Lutris
- executáveis Linux
- Wine
- Proton
- emuladores
- scripts personalizados

Não dependa apenas do PID inicial se o launcher criar subprocessos.

Crie uma abstração de "game session" / "running game" adequada ao projeto.

# 9. FOCO

Implemente comportamento consistente de foco dentro do Gamescope.

Quando um jogo abrir:

- o jogo deve assumir foco.

Quando fechar:

- Cartridges deve reassumir foco.

Evite hacks baseados em sleeps fixos.

Utilize mecanismos do compositor, Wayland, subprocessos ou framework gráfico quando apropriado.

# 10. GAMEPAD

O objetivo do fork é suportar navegação completa por controle.

Garanta que no Game Mode todas as áreas essenciais possam ser utilizadas sem teclado/mouse:

- biblioteca;
- busca;
- filtros;
- configurações;
- detalhes do jogo;
- iniciar jogo;
- voltar;
- menu;
- power menu;
- diálogos;
- mensagens de erro.

Crie uma camada de input coerente.

Mapeamento conceitual:

D-pad / analog stick = navegação
A / Cross = selecionar
B / Circle = voltar
Start = menu/contexto
Guide/Home = menu global

Não codifique somente para Xbox.

Use abstração de gamepad adequada, preferencialmente SDL GameController/Gamepad ou a solução já utilizada pelo projeto.

Suporte hotplug.

# 11. GAMEPAD HOME / GUIDE BUTTON

Implemente suporte ao botão Guide/Home quando tecnicamente possível.

Ele deve abrir um menu global do Game Mode.

Esse menu deverá futuramente permitir recursos como:

- voltar à biblioteca;
- fechar jogo;
- FPS;
- performance;
- brilho;
- áudio;
- energia.

Nesta etapa, pelo menos implemente a infraestrutura corretamente.

Não capture dispositivos diretamente via /dev/input se existir uma API de nível superior melhor e mais segura.

# 12. POWER MENU

No Game Mode, adicione menu contendo:

- Sleep/Suspend
- Restart
- Shut Down
- Logout / Exit Game Mode

Utilize APIs do sistema apropriadas, preferencialmente systemd-logind / D-Bus.

NÃO use:

sudo shutdown
sudo reboot

dentro da aplicação.

Não peça senha administrativa para ações normais já autorizadas pela sessão.

No Desktop Mode esse menu pode ser ocultado ou adaptado.

# 13. CONFIGURAÇÕES POR JOGO

Prepare a arquitetura para futuras configurações específicas por jogo.

Exemplo:

GameProfile {
    executable
    arguments
    working_directory

    use_gamemode
    gamescope_options

    fps_limit
    resolution
    scaling_mode

    environment
}


# 14. CONFIGURAÇÕES DO GAME MODE

Crie uma seção de configurações relacionada à sessão gamer.

Exemplos:

Game Mode

[ ] Enable GameMode for games
[ ] Enable Gamescope FPS limiter
[ ] Enable VRR when supported
[ ] Enable MangoHud
[ ] Start in Library
[ ] Hide mouse cursor when using controller

Evite mostrar configurações que não sejam suportadas pelo hardware.

Adicione aqui configurações para qual monitor usar e qual saida de áudio usar e qual entrada de audio usar caso o sistema possua mais de uma opção.


# 15. MANGOHUD

Detecte MangoHud se disponível.

Ofereça integração opcional.

Não faça MangoHud uma dependência obrigatória.

Se não estiver instalado, o launcher deve funcionar normalmente.

# 16. STEAM

Steam deve ser tratado como backend/launcher, não como shell obrigatório.

Não inicie Steam Big Picture automaticamente.

Jogos Steam devem continuar funcionando.

Quando necessário, execute Steam silenciosamente/em background.

Não tente substituir Steam Input neste momento.

Garanta compatibilidade com Steam Input quando jogos Steam forem iniciados.

# 17. HEROIC / LUTRIS / EMULADORES

Mantenha a arquitetura aberta.

Não crie caminhos hardcoded como:

/usr/bin/steam

sem antes descobrir executáveis usando mecanismos adequados.

Considere:

- pacotes nativos;
- Flatpak;
- instalações alternativas.

Crie adaptadores/backends quando necessário.

# 18. FLATPAK

O Cartridges original pode utilizar ou integrar aplicativos Flatpak.

Investigue o impacto disso.

Não assuma que Steam/Heroic/Lutris estarão instalados de uma única maneira.

Quando necessário, suporte:

flatpak run <app-id>

de maneira estruturada.

# 19. LOGS E DEBUG

Crie logging adequado para a sessão gamer.

Exemplo:

journalctl --user ...

ou mecanismo equivalente adequado ao projeto.

Preciso conseguir diagnosticar:

- Gamescope não iniciou;
- gamepad não detectado;
- jogo não iniciou;
- launcher não encontrado;
- sessão caiu;
- subprocesso ficou preso.

Evite logs excessivos em uso normal.

# 20. MODO DE DESENVOLVIMENTO

Implemente um modo que permita testar a interface Game Mode SEM precisar fazer logout do GNOME.

Por exemplo:

cartridges --game-mode --windowed

ou:

cartridges --game-mode --nested

Neste caso pode usar Gamescope nested/windowed.

Isso é apenas para desenvolvimento.

O comportamento da sessão real deve continuar separado.

# 21. INSTALADOR

Crie scripts ou targets apropriados.

Exemplos aceitáveis:

./scripts/install-game-session.sh
./scripts/uninstall-game-session.sh

ou integração com:

meson
cmake
make
just
cargo
python packaging

dependendo da arquitetura existente.

O instalador deve:

- detectar Fedora;
- verificar dependências;
- instalar dependências necessárias;
- instalar entrypoint;
- instalar sessão GDM;
- validar arquivos;
- informar sucesso.

O uninstall deve remover APENAS arquivos criados por este projeto.

Jamais remova Gamescope/GameMode automaticamente no uninstall, pois eles podem estar sendo usados por outros programas.

# 22. PRIVILÉGIOS

Minimize o uso de root.

Root deve ser usado somente quando realmente necessário para:

- instalar pacotes;
- escrever arquivos de sistema da sessão.

O launcher nunca deve executar como root.

O Gamescope nunca deve executar como root.

Jogos nunca devem executar como root.

# 23. SEGURANÇA

Não:

- chmod 777;
- desabilite SELinux;
- altere regras globais de udev sem forte justificativa;
- desabilite segurança do sistema;
- altere PAM;
- modifique GDM além do necessário;
- sobrescreva arquivos existentes sem backup/verificação.

Se precisar instalar uma regra de sistema, documente exatamente por quê.

# 24. RECUPERAÇÃO DE FALHAS

Se Gamescope não iniciar:

- encerre a sessão de forma limpa;
- produza log útil.

Se Cartridges crashar:

- encerre Gamescope;
- retorne ao GDM.

Se um jogo crashar:

- retorne ao Cartridges.

Se um launcher externo crashar:

- Cartridges deve continuar vivo.

# 25. DETECÇÃO DO AMBIENTE

Crie uma abstração como:

RuntimeMode.DESKTOP
RuntimeMode.GAME_SESSION
RuntimeMode.NESTED_GAME_MODE

ou equivalente apropriado à linguagem do projeto.

Evite espalhar verificações como:

if os.getenv(...)

por dezenas de arquivos.

Centralize a detecção do ambiente.

# 26. ARQUITETURA

Quero separação clara de responsabilidades.

Algo conceitualmente próximo a:

core/
    library
    games
    launchers

platform/
    linux
    systemd
    gamescope
    gamemode

input/
    gamepad

session/
    runtime
    lifecycle
    power

ui/
    desktop
    game_mode

Não copie essa estrutura literalmente se não combinar com o projeto.

Use a arquitetura atual como base.

# 27. NÃO DUPLICAR UI

Desktop Mode e Game Mode devem compartilhar o máximo possível da UI e lógica.

Evite criar:

DesktopLibraryView
GameModeLibraryView

se 90% for igual.

Prefira comportamento responsivo/modos de apresentação.

# 28. UX

Game Mode deve transmitir uma experiência semelhante a console:

- controles grandes;
- foco visual extremamente claro;
- nenhum elemento inacessível por gamepad;
- transições rápidas;
- biblioteca como tela principal;
- ausência de janelas desktop aparecendo por cima;
- feedback de loading;
- mensagens de erro utilizáveis com controle.

Não tente copiar visualmente Steam Big Picture.

Preserve a identidade do Cartridges/fork.

# 29. TESTES

Crie testes automatizados para componentes que permitirem.

No mínimo:

- detecção de runtime mode;
- geração de comandos de execução;
- GameMode wrapper;
- backend Steam;
- processo de jogo;
- encerramento de processos;
- configuração;
- parsing de gamepad, se aplicável.

Não teste apenas happy path.

# 30. TESTE REAL DA SESSÃO

Depois da implementação:

1. compile/build o projeto;
2. execute testes existentes;
3. execute novos testes;
4. instale localmente;
5. valide o Desktop Mode;
6. valide Game Mode nested dentro do desktop;
7. valide instalação dos arquivos de sessão;
8. valide sintaxe dos .desktop;
9. valide dependências;
10. valide logs.

Se possuir acesso ao ambiente gráfico/testes via navegador ou ferramenta equivalente, use os recursos disponíveis.

Não considere a tarefa concluída apenas porque o código compila.

# 31. COMPATIBILIDADE

Prioridade inicial:

Fedora Workstation atual
Wayland
GDM

Mas evite acoplamentos que impeçam suporte futuro a:

- Fedora KDE
- outros display managers
- outras distribuições Linux.

Crie abstrações onde fizer sentido.

# 32. DOCUMENTAÇÃO

Atualize o README.

Adicione seção:

Game Mode

Explique:

- requisitos;
- instalação;
- desinstalação;
- como selecionar a sessão no GDM;
- como iniciar nested para desenvolvimento;
- troubleshooting;
- logs;
- limitações conhecidas.

Inclua exemplos de comandos.

# 33. NÃO QUEBRAR O APLICATIVO EXISTENTE

Esta é uma exigência absoluta.

Antes e depois da implementação compare:

- inicialização normal;
- biblioteca;
- imports;
- integração Steam;
- execução dos jogos;
- configurações;
- build;
- packaging.

O aplicativo desktop existente não pode virar somente um frontend de console.

Ele deve continuar sendo um excelente agregador de biblioteca desktop.

# 34. FLUXO DE EXECUÇÃO

Trabalhe de forma iterativa.

Primeiro faça:

FASE 1
- análise da arquitetura;
- proposta técnica;
- identificação dos pontos de extensão.

Depois implemente:

FASE 2
- Runtime Mode.

FASE 3
- entrada Game Mode.

FASE 4
- integração Gamescope.

FASE 5
- sessão GDM.

FASE 6
- lifecycle de jogos.

FASE 7
- GameMode.

FASE 8
- gamepad/focus.

FASE 9
- power menu.

FASE 10
- instalador.

FASE 11
- testes.

FASE 12
- documentação.

Você pode ajustar a ordem se encontrar dependências arquiteturais.

# 35. AUTONOMIA

Não pare para me perguntar detalhes pequenos.

Analise o repositório e tome decisões técnicas fundamentadas.

Se houver duas alternativas:

- escolha a mais idiomática para o projeto;
- escolha a mais segura;
- escolha a que mantém melhor compatibilidade desktop;
- documente a decisão.

Só interrompa se houver um bloqueio impossível de resolver tecnicamente.

# 36. RESULTADO ESPERADO

Ao final eu quero conseguir fazer algo como:

./scripts/install-game-session.sh

e receber algo equivalente a:

Cartridges Game Mode installed.

Dependencies:
✓ gamescope
✓ gamemode
✓ SDL
✓ Wayland

Session:
✓ Cartridges Game Mode

Então fazer logout.

No GDM selecionar:

Cartridges Game Mode

e obter:

Gamescope
↓
Cartridges fullscreen
↓
navegação por gamepad
↓
selecionar jogo
↓
jogar
↓
fechar jogo
↓
retornar ao Cartridges

E o modo desktop continua funcionando normalmente:

GNOME
↓
Cartridges
↓
janela desktop normal

# 37. ENTREGA FINAL

Quando terminar, entregue um relatório contendo:

## Arquitetura
Explique o que foi criado.

## Arquivos alterados
Liste os arquivos principais.

## Arquivos novos
Liste os arquivos adicionados.

## Dependências
Liste dependências obrigatórias e opcionais.

## Game Mode
Explique como funciona.

## Desktop Mode
Confirme como preservou o comportamento existente.

## Instalação
Mostre exatamente como instalar.

## Desinstalação
Mostre exatamente como remover a sessão.

## Testes executados
Liste cada teste e resultado.

## Limitações
Liste problemas conhecidos.

## Próximos passos
Sugira melhorias futuras, especialmente:

- Quick Access Menu;
- FPS/TDP controls;
- MangoHud integrado;
- VRR;
- HDR;
- Steam Input;
- suspend/resume de jogos;
- overlays;
- perfis de performance por jogo.

Não encerre a tarefa deixando apenas código parcial ou TODOs nos componentes essenciais.

Implemente uma primeira versão funcional de ponta a ponta.

# 38. Landing Page 

no final, entregeue uma Landing Page completa em portugues, ingles e espanhol (i18n) explicando o que é o Cartridges e o Cartridges Game Mode, com oo comando oneliner para isntalação a partir do repositorio. a landing page deve seguir o visual do projeto, com cores, fontes e estilo coerentes. A landing page não é tecnica, mas sim de marketing/conversão de usuarios, explicando os benefícios do Cartridges Game Mode, como ele funciona, e como ele se integra com o desktop. Deve conter seções de FAQ