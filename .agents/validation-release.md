# Validação, instalação e release

Toda validação solicitada parte de um issue novo, mesmo sem pedido explícito.
Veja [fluxo de trabalho e issues](workflow.md); o issue só encerra após a
validação aplicável abaixo.

## Build local

Primeira configuração de release no prefixo do usuário:

```bash
meson setup build --prefix="$HOME/.local" --buildtype=release -Dprofile=release
```

Em um diretório já configurado:

```bash
meson compile -C build
meson test -C build --print-errorlogs
```

O build valida mais do que Python: compila Blueprint, GResources e schemas e roda
validadores de desktop file e AppStream quando disponíveis. Corrija warnings
novos relacionados à mudança mesmo que o executável ainda abra.

A versão é gerada por `build-aux/get-version.py` no formato baseado em data/hora.
Builds reprodutíveis podem definir `JOLVEN_VERSION`; `CARTRIDGES_VERSION` existe
por compatibilidade. Não codifique versão manualmente em módulos derivados.

## Testes automatizados

A suíte usa `unittest` e testes de integração do Meson em `tests/`. Rode o teste
específico durante a implementação e a suíte completa antes de concluir.

Áreas que devem permanecer testáveis sem GTK completo:

- parsing de importadores;
- composição de comando de lançamento;
- agrupamento e IDs;
- validação de backup/caminhos;
- parsing e segurança de feeds;
- runtime context e migrações;
- limitadores e decisões de integrações.

Para rede, use fixtures e mocks. Testes não devem depender de API key, catálogo ao
vivo, DNS ou disponibilidade de launcher instalado na máquina.

## Matriz por tipo de mudança

| Mudança | Validação adicional |
| --- | --- |
| Blueprint/UI | desktop + sessão em janela, 360 px, foco, teclado/gamepad, temas |
| importador | fixtures válidas/corrompidas, launcher ausente, ID estável, duplicata |
| persistência | JSON antigo, versão futura, round-trip e falha no meio da escrita |
| backup | arquivo malicioso, limites, rollback e reinício da restauração |
| lançamento | ordem dos wrappers, espaços no caminho, Flatpak/URI, processo filho |
| integração HTTP | timeout, status, corpo grande, auth inválida, cache e offline |
| sessão | Gamescope ausente, fallback, áudio restaurado e saída limpa |
| Flatpak | permissões mínimas, caminhos sandbox/host e dependência no manifesto |

## Inspeção manual de UI

Para mudanças visuais, confirme:

- janela abre sem controle e sem bibliotecas opcionais;
- navegação completa funciona por teclado;
- gamepad move foco e ativa/volta conforme o contrato;
- foco retorna após diálogo, busca e lançamento;
- estado vazio, loading e erro não deslocam a interface de forma destrutiva;
- nomes longos, caracteres especiais e conteúdo remoto não viram markup;
- claro, escuro e alto contraste preservam legibilidade;
- nenhuma chamada de rede congela animação ou entrada.

Um smoke test útil depois do build é executar o binário gerado com `--help` e,
quando houver display disponível, abrir o perfil de desenvolvimento ou o modo de
sessão em janela. Não inicie uma sessão gráfica dedicada durante teste automático.

## Instalação local

Depois de a compilação e os testes serem aprovados:

```bash
meson install -C build
```

O destino depende do `--prefix` usado na configuração. Para o padrão acima, o
launcher fica sob `~/.local`. Reconfigure explicitamente se o prefixo desejado for
outro; não copie partes do build manualmente.

A instalação da Jolven Session é separada porque envolve arquivos do sistema e
polkit. Use apenas os scripts/helper fornecidos e verifique propriedade do arquivo
antes de remover. A instalação do aplicativo não concede autorização implícita
para modificar a sessão gráfica do host.

## CI e Flatpak

`.github/workflows/ci.yml` constrói e valida o Flatpak em pushes/PRs para `main`.
O manifesto em `build-aux/flatpak/` enumera runtime, módulos Python e dependências
nativas como libmanette/libei. Toda nova dependência de runtime precisa aparecer
tanto no ambiente de fonte quanto no Flatpak, ou ser realmente opcional.

Antes de ampliar permissões do sandbox, documente por que portal ou diretório mais
restrito não atende. Verifique que a aplicação continua iniciando offline.

## Documentação pública

Uma funcionalidade visível pode exigir atualização de:

- `README.md` e `docs/README.md`;
- landing page em `docs/`;
- `docs/llms.txt` e `docs/llms-full.txt`;
- metainfo/AppStream e screenshots;
- notas da próxima release.

Documentação técnica interna e pública têm papéis diferentes: `.agents/` explica
contratos de manutenção; a landing page descreve benefícios e uso para pessoas.

## Release

O workflow de publicação reage a tags e usa `scripts/publish-release.sh` e os
artefatos configurados no repositório. Uma release deve partir de árvore limpa e
de um commit já validado.

Checklist:

1. suíte completa e build release aprovados;
2. Flatpak/instalação local verificados conforme o escopo;
3. metainfo e documentação pública atualizados;
4. migrações e compatibilidade avaliadas;
5. nenhum segredo ou arquivo gerado entrou no commit;
6. release notes descrevem mudança e impacto ao usuário;
7. tag/publicação somente após autorização explícita.
