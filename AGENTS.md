# Guia de trabalho no Jolven

Este arquivo é a referência operacional para pessoas e agentes que alteram este
repositório. Ele vale para toda a árvore. A documentação detalhada está em
[`.agents/`](.agents/README.md).

## Antes de alterar código

1. Toda solicitação de implementação, correção ou validação gera um issue
   novo, mesmo sem pedido explícito. Crie-o antes de qualquer edição,
   conforme [fluxo de trabalho e issues](.agents/workflow.md). Só reutilize
   um issue existente quando o solicitante indicá-lo explicitamente.
2. Leia [arquitetura](.agents/architecture.md) e o documento específico da área
   que será modificada.
3. Verifique `git status` e preserve alterações que não pertencem à tarefa.
4. Procure primeiro os padrões já usados pelo projeto. Evite criar um segundo
   mecanismo para configuração, navegação, persistência ou trabalho assíncrono.
5. Trate dados locais, credenciais, processos iniciados e integrações do sistema
   como recursos do usuário: não apague, migre ou substitua silenciosamente.

## Invariantes do projeto

- Jolven é uma aplicação Linux em Python, GTK 4 e libadwaita. A interface é
  escrita em Blueprint e empacotada como GResource pelo Meson.
- `CartridgesApplication` coordena o ciclo de vida. `RuntimeContext` é a fonte
  autoritativa para distinguir desktop, Jolven Session e modo aninhado. Não
  espalhe novas verificações de variáveis de ambiente pela base.
- `Store` é o registro de jogos. Importadores descobrem jogos, gerenciadores
  enriquecem/persistem/exibem e `Pipeline` ordena suas dependências.
- `Game` ainda combina modelo persistido e widget GTK. Considere esse acoplamento
  ao mover trabalho para threads: propriedades visuais só podem ser tocadas na
  thread principal.
- `shared.PREFIX` é o prefixo de recursos GResource, não um caminho do disco.
  Caminhos instalados devem vir das constantes e helpers de `shared.py.in`.
- Dados legados são copiados ou migrados de forma compatível; a origem nunca é
  removida automaticamente. Jogos com `spec_version` mais nova que a suportada
  devem ser ignorados, não regravados.
- Integrações externas são opcionais. Falha de rede, biblioteca ausente, serviço
  indisponível ou credencial inválida não deve impedir o uso da biblioteca local.

## Regras obrigatórias de implementação

- Nunca bloqueie a thread GTK com rede, varredura de disco ou espera de processo.
- Nunca acesse widgets GTK a partir de um worker. Entregue resultados à UI com
  `utils.na_tela.entregar_na_tela` quando o usuário estiver aguardando a ação.
- Use `pathlib`, type hints e os helpers existentes. Mantenha nomes públicos e
  formatos persistidos compatíveis; não faça renomeações amplas incidentalmente.
- Novas preferências persistentes pertencem ao schema GSettings. Não invente um
  arquivo de configuração paralelo.
- Todo texto visível deve passar por gettext (`_()`). Atualize `po/POTFILES`
  quando adicionar um arquivo com strings traduzíveis.
- Não use markup com nomes, notas ou metadados não confiáveis. Valide URLs,
  protocolos, caminhos de arquivo, conteúdo de arquivos e respostas remotas.
- Nunca registre segredos, tokens, chaves locais da Tuya ou URLs com credenciais.
- Use timeout, limite de resposta e fallback de cache nas integrações HTTP.
- Edite fontes, não artefatos gerados: `.blp`, `.in`, schemas e fontes de tradução.
  Não edite `.ui`, `shared.py`, schemas compilados, `.mo`, `.pyc` ou arquivos em
  `build/` gerados pela compilação.
- Operações destrutivas precisam de alvo exato e validação. Instalação e remoção
  da sessão privilegiada devem continuar restritas ao helper via polkit.

## Contrato de UI

- Toda ação primária precisa funcionar com mouse, teclado e gamepad.
- Preserve foco visível e previsível. Ao fechar diálogos ou trocar de página,
  devolva o foco a um elemento útil.
- No modo sessão, controles interativos devem ter área mínima de aproximadamente
  44 px e funcionar em tela cheia a distância.
- Respeite temas escuro e alto contraste, cores semânticas do libadwaita e ícones
  simbólicos. Cores fixas só são aceitáveis para identidade visual deliberada.
- Verifique layouts a partir de 360 px de largura e não dependa de WebKit,
  libmanette ou de um controle conectado para a janela abrir.
- Use `Adw.NavigationView`, `Adw.OverlaySplitView`, breakpoints e os componentes
  existentes antes de introduzir navegação própria.

Detalhes: [`.agents/ui.md`](.agents/ui.md).

## Alterações recorrentes

### Novo importador

- Crie um identificador estável e único no formato `<fonte>_<id-nativo>`.
- Registre a preferência no schema, a opção na UI, o importador na lista central
  e o ícone/recurso quando necessário.
- Faça parsing no worker sem GTK e retorne `Game`, `(Game, additional_data)` ou
  `None`, conforme o contrato atual.
- Só marque a fonte como totalmente varrida quando for seguro remover entradas
  desaparecidas. Não duplique atalhos genéricos que outro importador já cobre.
- Teste parsing, ausência do launcher, dados incompletos e IDs duplicados.

### Novo campo persistente de jogo

- Defina um padrão compatível em `Game`.
- Inclua o campo na serialização do `FileManager` e na edição/visualização quando
  aplicável.
- Leia arquivos antigos sem o campo e preserve campos desconhecidos sempre que o
  fluxo atual permitir.
- Avalie backup/restauração, agrupamento de duplicatas e incremento da versão do
  formato. Só aumente `SPEC_VERSION` quando a incompatibilidade justificar.

### Nova tela ou alteração de Blueprint

- Mantenha a classe Python, os `Gtk.Template.Child` e os IDs do `.blp` alinhados.
- Registre novos recursos em `data/jolven.gresource.xml.in` e no Meson quando o
  padrão da pasta exigir.
- Inclua strings em tradução, estados vazio/carregando/erro, foco inicial,
  navegação de retorno e operação sem gamepad.

### Nova integração externa

- Torne-a opt-in quando houver envio de dados, credenciais ou conteúdo sensível.
- Faça import opcional/lazy, rede fora da thread principal e falha não fatal.
- Documente origem dos dados, autenticação, armazenamento de segredos, timeout,
  cache e implicações de privacidade em [`.agents/integrations.md`](.agents/integrations.md).

## Validação mínima

Para uma alteração comum:

```bash
meson setup build --prefix="$HOME/.local" --buildtype=release -Dprofile=release
meson compile -C build
meson test -C build --print-errorlogs
```

Se `build/` já estiver configurado, não repita `meson setup`. Rode também o teste
específico durante o desenvolvimento. Para mudanças de UI, valide desktop e
modo sessão em janela; para persistência, use dados antigos e backup/restauração;
para rede, use mocks e cubra timeout/erro.

Não considere a tarefa pronta apenas porque Python importa. A compilação valida
Blueprint, recursos, schemas, desktop file e metainfo. Veja a matriz completa em
[`.agents/validation-release.md`](.agents/validation-release.md).

## Commits e releases

- Faça commits focados, sem incluir arquivos gerados ou mudanças alheias.
- Vincule cada commit e PR ao issue da tarefa (`Fixes #N` ou `Relates to #N`).
  Descobertas que configurem nova tarefa geram issue novo, não extensão de escopo.
- Atualize README, landing page, metainfo e notas de release quando a alteração
  for pública.
- Não crie tag nem publique release sem pedido explícito. Tags acionam o fluxo de
  publicação do repositório.
- Antes de instalar, compile e teste. Instalação local padrão usa
  `meson install -C build` com o prefixo configurado.

