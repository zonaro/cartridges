# Documentação técnica do Jolven

Esta pasta descreve o sistema como ele existe hoje e registra os contratos que
devem sobreviver a novas implementações. O arquivo [`../AGENTS.md`](../AGENTS.md)
contém as instruções obrigatórias e concisas; os documentos abaixo explicam o
contexto e as decisões.

## Índice

- [Fluxo de trabalho e issues](workflow.md): regra issue-first para
  implementação, correção e validação, conteúdo mínimo e vinculação.
- [Arquitetura e fluxo de dados](architecture.md): inicialização, modelo,
  importação, pipeline, persistência e lançamento.
- [Interface e interação](ui.md): Blueprint, navegação, responsividade,
  acessibilidade, gamepad e regras de thread.
- [Compatibilidade e plataforma](compatibility.md): ambientes suportados,
  dependências opcionais, Flatpak, sessão e legado.
- [Serviços e integrações](integrations.md): APIs, launchers, sistema operacional,
  credenciais, privacidade e falhas.
- [Regras de implementação](implementation.md): padrões de código, segurança,
  dados, importadores, tarefas assíncronas e alterações comuns.
- [Validação e release](validation-release.md): build, testes, inspeção manual,
  instalação e publicação.

## Visão rápida

Jolven é uma biblioteca e launcher de jogos para Linux, construída em Python com
GTK 4/libadwaita. Ela descobre jogos em diferentes launchers, normaliza-os em um
modelo `Game`, enriquece os registros por uma cadeia de gerenciadores e oferece
duas experiências sobre a mesma biblioteca:

- aplicação desktop convencional;
- **Jolven Session**, interface em tela cheia iniciada por Gamescope e dirigida
  prioritariamente por gamepad.

Há ainda um modo de jogo aninhado, usado para testar a experiência de sessão sem
substituir a sessão gráfica atual. Os modos compartilham navegação, persistência e
regras de lançamento, mas diferem em janela, retorno de foco e integração com o
sistema.

## Como manter estes documentos

- Atualize a documentação no mesmo commit que mudar um contrato descrito aqui.
- Escreva fatos verificáveis no código. Separe requisito de intenção futura.
- Prefira apontar para símbolos e arquivos estáveis, sem copiar grandes trechos.
- Não inclua tokens, exemplos de credenciais, caminhos pessoais ou dados reais.
- Se um comportamento for legado mas ainda necessário, documente-o antes de
  removê-lo; compatibilidade silenciosa é parte do produto.

