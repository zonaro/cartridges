# Interface e interação

## Tecnologia e composição

A UI usa GTK 4, libadwaita e templates Blueprint em `data/gtk/*.blp`. O Meson
compila Blueprint para `.ui`, empacota esses arquivos no GResource e as classes
Python os carregam por `Gtk.Template`.

As telas principais incluem janela/biblioteca, cartão de jogo, preferências,
detalhes, seletores de capa/logo/wallpaper, assistente de fitas, histórico de
sessão e visualização de tarefas. Antes de criar um novo padrão, procure um
componente com o mesmo papel nessas telas.

## Navegação

- Use `Adw.NavigationView` para pilhas de páginas e `Adw.OverlaySplitView` para a
  relação sidebar/conteúdo.
- A ação de voltar fecha primeiro popovers/diálogos e depois navega para trás.
- Ao remover a página focada, restaure foco na origem ou no primeiro item útil.
- Mantenha a mesma hierarquia de ações em desktop e sessão. Diferenças devem vir
  do `RuntimeContext`, não de duas árvores de UI independentes.
- Busca em modo sessão deve continuar acessível com o teclado na tela.

## Gamepad e teclado

Jolven Session é gamepad-first, não gamepad-only. O mapeamento esperado é:

| Entrada | Resultado padrão |
| --- | --- |
| D-pad / analógico | mover foco GTK |
| botão sul | ativar/confirmar |
| botão leste | voltar/fechar |
| X | menu ou ações do jogo |
| Y | busca |
| Start | menu principal/global |
| Guide | abrir ou focar sidebar |

Novas ações primárias precisam ser focáveis e ativáveis sem ponteiro. Não capture
botões globalmente quando o controle nativo do GTK já resolve o caso. Preserve
atalhos existentes e verifique conflito antes de adicionar outro.

`libmanette` é opcional e carregado de forma defensiva. A aplicação precisa abrir
e funcionar com teclado mesmo quando a biblioteca ou o controle não existir. A
emulação de teclado via libei/snegg também é opcional e mediada pelo portal; não
leia dispositivos de `/dev/input` diretamente.

## Foco e acessibilidade

- Todo controle interativo precisa de nome/descrição acessível quando seu rótulo
  visual não for suficiente.
- Não remova o anel de foco. Estados selecionado, focado, desabilitado e em erro
  devem ser visualmente distinguíveis.
- Em sessão, use alvos com cerca de 44 px ou mais de altura.
- Ordem de foco deve acompanhar a ordem visual e não entrar em áreas invisíveis.
- Não comunique estado apenas por cor; combine texto, ícone ou forma.
- Use `set_use_markup(False)` ou texto simples para nomes e conteúdo remoto.

## Responsividade e temas

A largura mínima prática é 360 px. Use breakpoints do libadwaita e deixe grades,
sidebar e detalhes adaptarem-se sem corte horizontal. Cartões de capa usam como
referência a proporção 200 × 300; preserve a proporção ao redimensionar.

Use variáveis e classes semânticas do libadwaita, ícones simbólicos e os estados
de estilo existentes. Valide tema claro, escuro e alto contraste. Cores fixas são
reservadas a identidade deliberada, como a experiência xCloud, e ainda precisam
de contraste adequado.

Transições de foco podem atualizar background, logo e metadados, mas devem ser
canceláveis e baratas. Não decodifique imagens grandes nem faça rede a cada evento
de foco.

## Threads e feedback

GTK só pode ser acessado na thread principal. Rede, descoberta de launchers,
leitura pesada, hashing e acompanhamento de processo pertencem a workers.

Quando uma ação assíncrona tem um usuário esperando, entregue o resultado por
`utils.na_tela.entregar_na_tela`, que usa prioridade adequada para não ficar atrás
de atualizações contínuas da interface. `GLib.idle_add` continua aceitável para
atualizações de baixa prioridade sem espera humana.

Toda operação demorada precisa de estado de progresso ou ocupação, prevenção de
duplo disparo e conclusão clara. Erros recuperáveis devem gerar toast ou mensagem
acionável, não traceback nem janela travada.

## Texto e localização

Todo texto visível passa por gettext (`_()`). Prefira frases curtas, específicas e
consistentes com a terminologia existente. O nome público da experiência é
**Jolven Session**; “game mode” pode ser usado internamente, mas não deve sugerir
que Jolven é uma distribuição ou sistema operacional.

Ao adicionar strings em novo arquivo, inclua-o em `po/POTFILES`. Não concatene
fragmentos traduzíveis para formar frases e não use o texto traduzido como chave
de lógica.

## Checklist de nova tela

- Template compila e todos os `Gtk.Template.Child` existem.
- Novo `.blp` está no GResource/Meson conforme o padrão da pasta.
- Há estados inicial, vazio, carregando, sucesso e erro aplicáveis.
- Foco inicial, voltar, fechamento e restauração de foco funcionam.
- Mouse, teclado e gamepad alcançam as ações primárias.
- 360 px, tela larga, desktop, sessão em janela, escuro e alto contraste foram
  inspecionados.
- Strings estão traduzíveis e conteúdo externo não usa markup.
- Biblioteca opcional ausente não impede a criação da tela.

