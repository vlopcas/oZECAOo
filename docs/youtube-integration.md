# Integração YouTube: operação e validação

O site permanece estático: Python da biblioteca padrão no GitHub Actions produz HTML e JSON para o GitHub Pages. Não há backend, banco de dados, OAuth ou chamadas autenticadas no navegador. O layout e os cards permanecem inalterados.

## Arquivos desta alteração

| Arquivo | Responsabilidade |
| --- | --- |
| `scripts/youtube_api.py` (novo) | Cliente API, paginação, disponibilidade, lives, ordenação e erros seguros. |
| `scripts/update_videos.py` | Prioridade das fontes, preservação, estados e relatório interno. |
| `scripts/build_site.py` | Procedência e metadados públicos permitidos, com os mesmos cards. |
| `scripts/report_sync.py` (novo) | Step Summary com sincronização/build/deploy separados. |
| `.github/workflows/deploy.yml` | Secret isolado na sincronização e resumo final. |
| `tests/test_youtube_api.py` (novo) | Contratos e erros da API, paginação, lives e segurança. |
| `tests/test_video_sync.py` (novo) | Orquestração e integração completa com HTTP simulado. |
| `tests/test_report_sync.py` (novo) | Estados, idade, escaping e resumo após publicação. |
| `tests/test_update_videos.py` | Resiliência Atom, recuperação e compatibilidade de prévia local. |
| `tests/test_build_site.py` | Metadados, SSR, datas e exclusão de campos privados. |
| `README.md` e este guia | Uso, limites, validação e diagnóstico operacional. |

## Diagnóstico

O [run 41793080967](https://github.com/vlopcas/oZECAOo/actions/runs/41793080967) concluiu a publicação, mas o Atom retornou HTTP 404 nas três tentativas e o script preservou os vídeos anteriores. A proteção evitou a regressão para a reserva antiga; ela não conseguiu atualizar o YouTube. O endereço do feed é o mesmo e o log não demonstra a causa interna do 404 no provedor. Atrasos do cron não explicam uma resposta HTTP do YouTube.

O workflow estava ativo, com `workflow_dispatch`, cron `17 1,7,13,19 * * *` em UTC e concorrência `pages`, `cancel-in-progress: false`. Isso mantém quatro execuções previstas por dia (04:17, 10:17, 16:17 e 22:17 em Brasília), serializando publicações. O agendamento é sujeito aos [atrasos documentados pelo GitHub](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Fontes e seleção

1. **`youtube_data_api_v3`**: `channels.list(contentDetails)` obtém a playlist de uploads; `playlistItems.list` percorre as páginas; `videos.list` verifica os IDs em lotes. Os IDs do feed anteriormente publicado também são revalidados para evitar perder vídeos recentes por uma omissão da playlist.
2. **`youtube_atom`**: usado apenas se a descoberta principal falhar. Os IDs anunciados no XML e os IDs anteriores passam pela mesma validação de `videos.list`. O título e os metadados usados no card vêm do recurso validado. Se a chave estiver ausente/inválida ou a quota esgotada, não se repete uma consulta autenticada sabidamente inútil.
3. **`published_cache`**: se não for possível confirmar gravações elegíveis pelas fontes anteriores, mantém o último JSON publicado válido. Canal, IDs, títulos, limite e exclusão do trailer são validados. Se esse JSON também estiver indisponível ou inválido, retorna código 1 antes do build/deploy. A reserva versionada antiga nunca substitui o site em produção.

O Atom não expõe metadados suficientes para garantir que uma live terminou. A política aprovada é conservadora: **sem confirmação de disponibilidade e encerramento, preservar os anteriores**. Assim, o Atom pode recuperar uma falha da descoberta de canal/playlist quando a consulta de detalhes ainda funciona; uma indisponibilidade completa da API mantém o cache.

Critérios de elegibilidade:

- ID válido, pertencente ao canal configurado, sem duplicatas e diferente de `trailerVideoId`.
- `privacyStatus=public`, `uploadStatus=processed`, título e datas válidos e duração positiva.
- Excluir `liveBroadcastContent=live` e `upcoming`, publicação futura e processamento incompleto.
- Havendo `liveStreamingDetails`, exigir início e encerramento reais coerentes e já ocorridos.
- IDs omitidos por `videos.list` são indisponíveis para aquela consulta pública. Um recurso retornado sem partes obrigatórias torna a resposta inconsistente e aciona a alternativa.
- `embeddable=false` não remove o vídeo: os cards abrem o YouTube, não incorporam esses vídeos.

A seleção considera todos os candidatos descobertos e ordena por `sort_at` decrescente, com desempate por ID. Vídeos comuns usam `snippet.publishedAt`; lives encerradas usam o máximo entre publicação e `actualEndTime`. O campo `published` preserva a publicação original, exibida pelo site em Brasília. A data de adição à playlist não determina recência.

São até 50 itens por página, dez páginas de uploads e lotes de 50 IDs para detalhes. Se a paginação não terminar nesse teto, a descoberta é considerada incompleta. Uma seleção com menos itens que o limite configurado também não confirma uma atualização completa. O limite de produção continua sendo dois cards. Canal e playlist válidos costumam exigir três chamadas; cada uma dessas operações custa uma unidade segundo a documentação oficial. O teto de descoberta e a revalidação dos IDs conhecidos podem ampliar esse custo; retries também consomem quota. Não se usa `search.list`.

## Credenciais e resiliência

O repository secret existente `YOUTUBE_API_KEY` é disponibilizado somente como variável de ambiente da etapa Python de sincronização. Não copie a chave para arquivos, documentação, fixtures ou navegador. O cliente envia a chave no cabeçalho `X-Goog-Api-Key`, nunca na URL. Erros autenticados são convertidos em códigos seguros, sem imprimir corpo, cabeçalhos ou URLs com credenciais.

As respostas têm tamanho e tempo limitados. A API faz até três tentativas com pausas de 1 e 2 segundos para conexão/DNS/timeout, HTTP 408/429/5xx e erros transitórios de limite de taxa. HTTP 400/401/403 permanentes, chave inválida, quota diária esgotada, canal/playlist inexistente e respostas incompletas não entram em retries desnecessários. O Atom mantém até três tentativas, incluindo seu HTTP 404 intermitente. JSON/XML inválidos e listas vazias não são aceitos como feed atual.

O JSON publicado é lido como referência antes da descoberta autenticada e como recuperação se as fontes falharem. Essa leitura não muda a prioridade das fontes nem comprova uma sincronização nova. A ausência dessa referência não impede uma consulta principal completa e válida.

## Estados e metadados

| Estado | Significado | Publicação |
| --- | --- | --- |
| `UPDATED` | Consulta válida atualizou ou confirmou a seleção; não precisa haver um vídeo novo. | Pode continuar. |
| `PRESERVED` | Fontes não confirmaram gravações atuais; manteve o JSON publicado validado. | Pode continuar, com warning explícito. |
| `FAILED` | Não conseguiu consultar nem preservar dados válidos. | Código 1; build/deploy não executam. |

`source`, `channel_id` e `videos` continuam disponíveis. O bloco público `sync` contém `state`, `attempted_at`, `last_success_at` e `last_success_source`. `sort_at` é opcional por vídeo; o frontend continua usando seus campos anteriores. O build copia apenas campos permitidos: respostas brutas, credenciais e o relatório interno não são artefatos públicos.

`attempted_at` registra a tentativa atual. `last_success_at` só muda após consulta e seleção válidas. Ao preservar, mantém a última data conhecida; cache legado que perdeu esse dado usa `null` e idade desconhecida. A data de publicação de um vídeo não substitui a data de sincronização. O timestamp legado `updated_at`, quando conhecido na recuperação, não é renovado artificialmente.

O relatório interno fica em `runner.temp`, fora de `dist/`. Ao terminar o job, inclusive em falhas, `report_sync.py` escreve no GitHub Actions Step Summary:

- Estado, fonte, canal, quantidade, IDs e títulos selecionados.
- Horário da tentativa e última sincronização bem-sucedida, em UTC.
- Idade dos dados preservados ou indicação de idade desconhecida.
- Motivos seguros de fallback.
- Resultados separados do build e da publicação, incluindo `skipped`.

**Deploy com `success` e sincronização `PRESERVED` significa que o site foi publicado com dados anteriores. Não significa que o YouTube atualizou.**

## Testes e prévia local

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
git diff --check
```

Os testes usam respostas sintéticas e mocks; não precisam de chave nem fazem chamadas reais ao YouTube. Testam API, lives, paginação, seleção, erros transitórios/permanentes, fallback validado, preservação, estados, resumo, build e ausência de credenciais em logs/artefatos. `--fixture caminho/feed.xml` é uma ferramenta offline de teste do contrato do feed, sem consultas autenticadas ou de recuperação.

Para conferir a página offline com a reserva revisada, sem comprovar atualização do YouTube:

```bash
python3 scripts/build_site.py --feed data/videos.json
python3 -m http.server 8000 --bind 127.0.0.1 --directory dist
```

## Validação realizada

Não foi disponibilizada uma chave ao ambiente local; a credencial permanece no repository secret. Após aprovação explícita, o commit `8ffcd49` foi enviado e a integração autenticada foi validada no [run 41867212276](https://github.com/vlopcas/oZECAOo/actions/runs/41867212276). A evidência real complementa os testes locais com mocks.

Validação local realizada em 04/11/2026:

| Verificação | Resultado |
| --- | --- |
| Testes determinísticos | 89 passaram, incluindo integração HTTP simulada → parser → updater → build → dois cards SSR. |
| Compilação Python e `git diff --check` | Passaram. |
| Workflow YAML | Cron, execução manual, permissões, secret isolado e resumo `always()` conferidos. |
| Recuperação do JSON público real | HTTP 200; canal e dois IDs válidos; última sincronização desconhecida no cache legado. |
| Build com esse JSON real | Dois cards, IDs compatíveis; scripts Python internos fora dos artefatos. |
| Segurança | Chave sentinela ausente dos logs, relatório e de todos os artefatos públicos do teste integrado; redirects autenticados bloqueados. |
| Consulta autenticada real | **UPDATED**, fonte **youtube_data_api_v3**, em 04/11/2026 às 16:28:14 UTC (13:28:14 em Brasília). Sem fallback na etapa de sincronização. |
| Build, Pages e resumo final | **success** no mesmo run; os 89 testes também passaram no Actions. |
| Seleção real | `YIgeTMh2cp0` (Titanfall 2, vídeo publicado) e `HWXZF5rBk1o` (The Witcher 3, live encerrada). Comparação independente com as abas públicas `/videos` e `/streams` do canal. Trailer excluído. |
| Artefato e site público | 26 arquivos auditados. JSON/HTML com os mesmos dois IDs; JSON público com SHA-256 idêntico ao artefato: `ee8ec2232eb06ff7c642f0e315644fb7f80489e2adbbe13e7fd8ea588a3c3117`. |
| Varredura pública | Nenhum padrão de chave Google, variável do secret ou cabeçalho autenticado nos 26 arquivos do artefato e nos quatro scripts públicos. Scripts Python internos ausentes. |

Para verificar as próximas execuções:

1. Em **Actions → ZECAO • Atualização do YouTube e publicação**, use **Run workflow** na `main` ou acompanhe a execução do push aprovado.
2. Confira **`UPDATED` + `youtube_data_api_v3`** no Step Summary. Isso deve comprovar consulta ao canal configurado, descoberta da uploads, validação dos recursos e seleção; `PRESERVED` ainda indica um problema de atualização.
3. Confira os dois IDs/títulos apresentados e compare com os vídeos públicos e lives encerradas do canal. Verifique a ausência de trailer, transmissão futura e live em andamento.
4. Confira build e deploy como `success`. Abra [o JSON público](https://ozecaoo.com/data/videos.json), verifique `source`, `sync` e os mesmos IDs no HTML da [home](https://ozecaoo.com/).
5. Inspecione os logs e o artefato de Pages sem revelar o secret. Nenhum valor de credencial deve estar no HTML, JS, JSON ou relatório público.

O secret já está configurado e funcionou na execução real; não há configuração manual de credenciais pendente. As limitações operacionais abaixo continuam aplicáveis a futuras indisponibilidades do provedor.

## Diagnóstico operacional e limitações

Consulte primeiro o **estado de sincronização**, depois a fonte e os motivos de fallback. `api_key_missing`, `api_key_invalid`, `quota_exceeded` e `forbidden` indicam configuração/autorização/quota; não devem ser confundidos com Atom 404. Falha temporária de rede/servidor tem retries limitados. `discovery_truncated`, `response_incomplete` e `insufficient_videos` indicam que a API não comprovou a seleção completa.

O cache pode permanecer antigo durante uma indisponibilidade prolongada, mas sua idade e o estado `PRESERVED` ficam explícitos. O script não inventa data de atualização nem vídeos. Metadados públicos não garantem reprodução em todos os países, disponibilidade imediata da transcodificação ou restrições particulares de cada espectador; o controle final é do YouTube. O teto de descoberta e a política conservadora podem preservar o cache quando não é possível comprovar a seleção.

Referências oficiais: [channels.list](https://developers.google.com/youtube/data/reference/rest/v3/channels/list), [playlistItems.list](https://developers.google.com/youtube/data/reference/rest/v3/playlistItems/list), [videos.list](https://developers.google.com/youtube/data/reference/rest/v3/videos/list), [campos de vídeo e lives](https://developers.google.com/youtube/data/reference/rest/v3/videos), [erros da API](https://developers.google.com/youtube/data/reference/rest/v3/errors) e [API key em cabeçalho](https://docs.cloud.google.com/apis/docs/system-parameters).
