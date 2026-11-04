# Validação da integração YouTube WebSub — 4 de novembro de 2026

## Instalação publicada

Publicação, secrets, um Durable Object SQLite Free, inscrição e teste real foram autorizados pelo responsável antes das mutações. O site continua em GitHub Pages; o Python mantém a YouTube Data API v3 como fonte principal, Atom validado como fallback e recuperação do JSON publicado.

| Item | Resultado observado |
| --- | --- |
| Worker | `ozecaoo-youtube-webhook`, versão `01c59cb8-f216-4e71-b92d-68a2605a99d1` |
| Callback | `https://hooks.ozecaoo.com/youtube` |
| Saúde | `https://hooks.ozecaoo.com/health`, HTTPS 200 |
| Persistência | Um objeto `ChannelCoordinator`, SQLite, migration `v1` |
| Secrets | Três bindings `secret_text`; valores locais ignorados pelo Git, permissão 0600 |
| Disparos | `DISPATCH_ENABLED=true`, somente `vlopcas/oZECAOo`, workflow `deploy.yml`, branch `main` |
| Manutenção | Cron Cloudflare `23 */6 * * *` em UTC, mais alarmes duráveis |
| Reconciliação | GitHub `17 1,7,13,19 * * *` em UTC, quatro vezes ao dia |
| DNS e site | Apex e `www` preservados; HTTPS, home e redirecionamentos públicos conferidos |
| Custo | Nenhuma mudança para plano pago; recursos compatíveis com Workers/DO SQLite Free |

O endpoint recusa POST sem HMAC (403), administração sem Bearer (401) e GET sem desafio válido (404). O build publica uma lista explícita de arquivos e não copia `workers/` para o site. Nenhum secret foi encontrado no bundle, conteúdo público ou arquivos preparados para commit.

## Confirmação real da assinatura

O hub Google recebeu o pedido e enviou o desafio GET. O Worker validou tópico, modo e nonce do pedido pendente, respondeu ao desafio e registrou a assinatura como ativa. O ACK do pedido isoladamente não foi usado como confirmação.

| Marco | Data em Brasília (UTC−03:00) |
| --- | --- |
| Confirmação | 04/11/2026, 14:21:26 |
| Renovação programada (75% do lease) | 12/11/2026, 02:21:26 |
| Expiração informada pelo hub | 14/11/2026, 14:21:26 |

O lease observado foi de 864.000 segundos. A renovação e recuperação de falhas passaram nos testes automatizados; uma renovação real futura ainda não ocorreu nesta validação.

## Testes locais e CI

- TypeScript: typecheck e **64 testes aprovados**, incluindo HMAC, XML, desafio, armazenamento, rajadas, deduplicação, inscrição/renovação, GitHub indisponível, autenticação, resposta ambígua e JSON com limites.
- Python: **92 testes aprovados** na primeira publicação desta integração. Inputs do Worker servem somente para diagnóstico, sem substituir a seleção da API.
- [Worker CI inicial](https://github.com/vlopcas/oZECAOo/actions/runs/41875399687): concluído com sucesso.
- [Worker CI após correção do runtime](https://github.com/vlopcas/oZECAOo/actions/runs/41877038807): concluído com sucesso (61 testes, typecheck e dry-run). As três regressões finais adicionais passaram localmente, totalizando 64.
- [Publicação inicial do site](https://github.com/vlopcas/oZECAOo/actions/runs/41875399511): API v3 `UPDATED`, build e Pages concluídos com sucesso.

A integração real identificou dois ajustes que os mocks não revelaram: o runtime do Worker aceita `redirect: "manual"`, mas não `"error"`; e a listagem GitHub com trinta runs excedeu 256 KB. A implementação agora recusa redirecionamentos manualmente, consulta dez runs e limita o JSON em streaming a 512 KB. Ambos os cenários têm cobertura de regressão.

## Teste controlado com GitHub

Foi criado um único lote administrativo `websub_test`, com os dois IDs já publicados (`YIgeTMh2cp0`, `HWXZF5rBk1o`), sem publicar vídeos de teste ou simular uma entrega espontânea do YouTube.

- Lote: `784fc048-39da-4697-a8e7-8232e4ae4e36`.
- GitHub aceitou o dispatch às 14:34:30 de Brasília.
- [Execução controlada 41878656335](https://github.com/vlopcas/oZECAOo/actions/runs/41878656335), com origem e UUID no título.
- Estado do Worker após aceitação: zero pendentes e lote encerrado; nenhuma segunda chamada de teste foi necessária.
- **Teste ponta a ponta concluído com sucesso.** Os logs vinculam `workflow_dispatch`, origem `websub_test` e o UUID acima à execução do Python: 92 testes aprovados, API v3 `UPDATED` com dois vídeos, build e deploy bem-sucedidos.
- Consulta da API às 14:46:30 de Brasília; o `deploy-pages` informou `Reported success!` às 14:47:01.
- Artefato `github-pages` de ID `13347429549`, criado às 14:46:34. O JSON e o HTML públicos tiveram `Last-Modified` às 14:46:51; o JSON confirma fonte `youtube_data_api_v3`, estado `UPDATED`, sincronização às 14:46:30 e os mesmos dois IDs. Assim, a conclusão não depende apenas do status verde do Actions.

Às 14:40 de Brasília, o GitHub abriu um [incidente de indisponibilidade parcial do Actions](https://www.githubstatus.com/incidents/h02n9572mx9y). A API também mostrou atualizações tardias de estado: uma checagem com todos os passos concluídos permaneceu em andamento por vários minutos, e o cancelamento de uma publicação anterior só apareceu depois. O cancelamento forçado dessa publicação foi recusado com HTTP 409. Esse cenário é compatível com a [situação de runs fora de sincronia documentada pelo GitHub](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows#canceling-workflows), sem provar a causa específica do teste pendente.

O ambiente `github-pages` permite a branch `main`, sem revisores obrigatórios, temporizador ou regras de proteção personalizadas. Não foi identificada aprovação manual acionável; as configurações do ambiente foram preservadas. Mesmo depois da conclusão, o endpoint de jobs ainda mostrava passos vazios, enquanto logs, artefato e página pública já confirmavam a publicação.

O checkpoint de código `44bb86d` usou `[skip ci]` na mensagem para não substituir o teste ainda pendente no grupo `pages` naquele momento; o complemento documental também dispensa uma nova publicação. O código final já está publicado no Worker e passou nos 64 testes locais. O cron, as execuções manuais e os próximos pushes continuam habilitados normalmente.

## Limitações e acompanhamento

Nenhuma notificação POST espontânea autenticada do YouTube foi observada até a última conferência (`seen_events=0`). A assinatura real e o teste administrativo são evidências distintas; nenhum deles é apresentado como entrega espontânea de novo vídeo.

A reconciliação continua a cada seis horas: WebSub documenta uploads e alterações de título/descrição, sem garantir início/fim de live ou disponibilidade imediata da gravação. Reduzir para duas vezes por dia alongaria essa recuperação até doze horas.

O PAT expira conforme a data escolhida pelo responsável. A renovação WebSub não renova credenciais GitHub. O outbox reduz duplicatas, mas não oferece exactly-once absoluto entre Cloudflare e GitHub; a recuperação consulta os dez runs mais recentes.

Operação, rotação de secrets, testes, reinscrição, logs e desativação: [manual do Worker](../workers/youtube-webhook/README.md).
