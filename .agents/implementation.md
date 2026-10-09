# Regras de implementação

Toda implementação ou correção começa em um issue novo, mesmo sem pedido
explícito. Veja [fluxo de trabalho e issues](workflow.md) antes de editar.

## Organização e estilo

- Use Python idiomático, type hints nos limites importantes e `pathlib` para
  caminhos.
- Preserve a convenção local de `snake_case`. Há nomes históricos em português e
  inglês; não faça tradução/renomeação ampla junto de uma mudança funcional.
- Prefira funções puras para parsing, composição de comandos e decisões. Isso
  permite testes sem inicializar GTK.
- Reutilize `shared`, `Locations`, helpers de UI, logging e segurança existentes.
- Evite estado global novo. O estado compartilhado inevitável fica centralizado
  em `shared.py.in` ou em objetos com ciclo de vida claro.
- Capture exceções específicas. `except Exception` só é aceitável na borda de uma
  integração opcional para degradar com segurança, com logging sem segredos.

## Concorrência

Use `Gio.Task`/workers para rede e I/O pesado. Um worker recebe dados simples,
produz dados simples e agenda a aplicação do resultado na main loop. Ele não pode
reter widget como mecanismo de sincronização nem ler propriedades GTK.

Para o resultado de uma ação explícita do usuário, prefira
`utils.na_tela.entregar_na_tela`. Cancele ou ignore callbacks de uma tela já
destruída. Toda tarefa longa deve ter caminho de sucesso, erro e cancelamento.

Não use `sleep` para ordenar operações. Dependências entre gerenciadores pertencem
ao `Pipeline`; dependências entre processos devem usar sinais, callbacks ou espera
em thread apropriada.

## Dados e migrações

- Escrita deve ser atômica sempre que a perda do arquivo afetar a biblioteca.
- Valide e normalize antes de substituir o arquivo anterior.
- Migrações são idempotentes e mantêm cópia/origem recuperável.
- Nunca regrave registro com versão futura.
- Não use nome traduzido, título do jogo ou posição visual como ID persistente.
- Expansão de backup deve rejeitar caminhos absolutos, `..`, links e tamanhos fora
  dos limites.
- Alterações em formato persistido exigem fixture antiga e teste de round-trip.

Ao adicionar campo em `Game`, revise: padrão do modelo, atributos serializados no
`FileManager`, UI de edição/detalhes, agrupamento, importadores, backup e versão do
schema.

## Importadores

Um importador deve ser determinístico sobre a mesma instalação e tolerante a
arquivos parcialmente gravados pelo launcher. IDs usam `<fonte>_<id-nativo>` e
nunca devem depender só do título visível.

Checklist:

1. fonte e localização detectadas sem assumir um único prefixo;
2. preferência adicionada ao schema e à UI;
3. fonte registrada no fluxo principal;
4. parser isolado de GTK e testável com fixtures;
5. ícone e recursos registrados quando necessários;
6. strings incluídas em gettext/POTFILES;
7. retorno opcional de `additional_data` segue as chaves existentes;
8. varredura incompleta não remove jogos previamente conhecidos;
9. duplicatas genéricas de `.desktop` são evitadas;
10. launcher ausente ou banco bloqueado produz lista vazia/erro recuperável.

## Comandos e processos

Monte argumentos como listas e use os helpers de lançamento existentes. Não use
shell para interpolar nome do jogo, caminho, URI, variável de ambiente ou dado de
launcher. Preserve diretório de trabalho e ambiente explicitamente.

`game_launch.py` é o ponto de composição de wrappers. Mudanças na ordem de
Gamescope, GameMode e MangoHud precisam de testes. Acompanhar o processo correto é
parte da funcionalidade: launchers frequentemente criam filhos e encerram o
processo inicial.

## UI e recursos

Edite `.blp`, nunca o `.ui` gerado. Ao renomear ID de template, altere junto o
`Gtk.Template.Child`. Novas ações seguem os pares/trincas de método e callback já
usados pela aplicação; registre-as no objeto correto, não por monkey patch.

Textos visíveis usam gettext. Conteúdo de jogo/serviço usa texto simples. Imagens
remotas devem ter tamanho/tipo verificados antes de entrar no cache e a UI precisa
de placeholder para ausência ou erro.

## Configuração

Preferências persistentes entram em `data/io.github.zonaro.Jolven.gschema.xml.in`
com tipo, padrão e descrição apropriados. Leia/escreva pelo objeto de settings já
existente. Mantenha o perfil `.Devel` separado e compile schemas nos testes.

Segredos não pertencem ao schema quando o Secret Service já é usado para a
integração. Se uma credencial histórica estiver em GSettings, não a mova sem uma
migração explícita e testada.

## Rede e conteúdo não confiável

- Defina timeout de conexão/leitura e limite máximo de corpo.
- Use User-Agent identificável quando o serviço exigir.
- Não desabilite TLS nem aceite origem alternativa silenciosamente.
- Faça parsing com biblioteca segura; XML não pode resolver entidade externa.
- Valide MIME por conteúdo quando o arquivo será aberto como imagem/script.
- Escreva cache temporário e substitua atomicamente depois da validação.
- Não transforme resposta remota em argumentos de shell, markup ou caminho.
- Retenha dados locais bons quando uma atualização remota falhar.

## Logging e erros

Logs devem explicar operação, fonte e categoria da falha sem dados sensíveis.
Use traceback em falhas inesperadas de background, mas converta o erro apresentado
ao usuário em texto curto e acionável. Erros opcionais não devem gerar loop de
toast a cada inicialização.

Nunca registre:

- Client Secret, API key, token OAuth ou credencial da Tuya;
- chave local de dispositivo;
- cabeçalho `Authorization`;
- URL com query autenticada;
- conteúdo integral de backup ou arquivo de configuração do usuário.

## Artefatos gerados

Não versione nem edite manualmente:

- conteúdo de `build/`;
- `.ui` produzido de Blueprint;
- `shared.py` gerado de `shared.py.in`;
- schemas compilados, `.mo`, `.pyc` e caches;
- pacotes/resultados de release.

Se um arquivo gerado aparentar precisar de correção, encontre sua fonte no Meson,
em `.in`, `.blp`, schema, PO ou script gerador.

