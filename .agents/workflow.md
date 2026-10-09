# Fluxo de trabalho e issues

Toda solicitação de implementação, correção ou validação é uma tarefa nova e
toda tarefa nova começa em um issue novo no GitHub, mesmo quando o pedido
original não mencionar issue explicitamente.

## Regra obrigatória

- Não inicie alteração de código, correção de bug ou ciclo de validação sem
  um issue correspondente.
- Cada pedido distinto gera um issue distinto. Não agrupe tarefas diferentes
  em um único issue e não execute tarefa sem issue para "adiantar".
- A única exceção é quando o solicitante indica explicitamente um issue
  existente pelo número ou URL. Nesse caso, use esse issue e não crie outro.

## Antes de alterar código

1. Interprete o pedido e classifique como implementação, correção ou
   validação.
2. Verifique se o solicitante indicou um issue existente. Se indicou, leia-o
   e vincule o trabalho a ele.
3. Se não indicou, crie um issue novo antes de qualquer edição, seguindo os
   templates em `.github/ISSUE_TEMPLATE/` e as orientações de
   [`CONTRIBUTING.md`](../CONTRIBUTING.md#code):
   - `feature_request.md` para implementação/melhoria;
   - `bug_report.md` para correção, com passos de reprodução, comportamento
     esperado, sistema, versão e logs quando aplicável.
   - Validação avulsa (build, matriz de testes, inspeção manual, release)
     abre issue de correção ou melhoria conforme o objetivo; descreva o
     escopo a validar e os critérios de aceite.
4. Trabalhe a partir do issue: mencione o número no plano, nos commits
   (`Fixes #N` ou `Relates to #N`) e no PR, conforme
   [`CONTRIBUTING.md`](../CONTRIBUTING.md#code).

## Conteúdo mínimo do issue

- Título claro com verbo e objeto.
- Contexto e motivação verificáveis no código, sem copiar grandes trechos.
- Para correção: reprodução, esperado x obtido, ambiente e versão.
- Para implementação: o que muda para o usuário e o que está fora do escopo.
- Para validação: matriz aplicável de
  [validação e release](validation-release.md) e resultado esperado.
- Nunca inclua tokens, segredos, credenciais, chaves locais da Tuya, URLs
  autenticadas ou dados pessoais, seguindo as regras de
  [implementação](implementation.md) e [integrações](integrations.md).

## Vinculação e encerramento

- Um PR fecha ou referencia exatamente os issues que resolve; não use um PR
  para resolver silenciosamente um pedido sem issue.
- Questões descobertas no meio do trabalho que configurem nova
  implementação, correção ou validação geram um issue novo, não um escopo
  estendido do issue atual.
- O issue só é considerado concluído após a validação correspondente em
  [validação e release](validation-release.md).
