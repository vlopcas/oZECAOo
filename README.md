# oZECAOo | Jogos, gameplays e lives

Site oficial do **ZECAO / Zecão / @ozecaoo**, com vídeos e lives de jogos no YouTube, bastidores no Instagram e transmissões ocasionais na Twitch. A página mantém as artes originais e o visual preto, branco e vermelho da marca.

O endereço oficial é **https://ozecaoo.com/**. A versão `www.ozecaoo.com` deve redirecionar para esse endereço após a configuração do domínio no GitHub Pages.

## Rodar localmente

É um site estático, sem framework, backend, chave de API ou pacotes de build. O build usa apenas a biblioteca padrão do Python 3. Na raiz do projeto:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/update_videos.py
python3 scripts/build_site.py --feed dist/data/videos.json
python3 -m http.server 8000 --bind 127.0.0.1 --directory dist
```

Abra **http://localhost:8000**. Para parar o servidor, use `Ctrl+C`. Depois de editar a configuração ou os arquivos do site, execute o build novamente. Sirva `dist/`: o `index.html` da raiz é o modelo usado pelo gerador, não a versão final publicada.

A consulta ao YouTube gera `dist/data/videos.json`. Os arquivos gerados em `dist/` ficam ignorados pelo Git; nenhum comando acima atualiza o JSON de reserva versionado. Para um teste sem rede, passe `--fixture caminho/feed.xml` ao atualizador. Ele também aceita `--config caminho/site.config.json` e `--output caminho/videos.json`.

## Configuração e conteúdo

**`site.config.json` é a fonte única** para domínio, nome, aliases, descrições, imagens de compartilhamento, perfis oficiais e limites de vídeos. O build gera `dist/config.js`, mantendo `window.ZECAO_CONFIG` compatível com o navegador, e `dist/site.webmanifest`. Ambos existem somente em `dist/`; não edite esses arquivos gerados.

| Item | Fonte oficial |
| --- | --- |
| YouTube | https://www.youtube.com/channel/UCkS1cj-1C9ahgYVvFGz86qw |
| Instagram | https://www.instagram.com/ozecaoo/ |
| Twitch | https://www.twitch.tv/ozecaoo |
| Apresentação | https://youtu.be/ItDJkbW2M4A |
| Discord | Em breve, sem link ativo |

- `index.html`: estrutura e modelo do conteúdo; os campos de identidade e SEO são preenchidos no build.
- `styles.css`: cores, composição, responsividade e animações.
- `script.js`: player, thumbnails e atualização dos cards no navegador.
- `navigation.js`: menu mobile com os links do desktop e fechamento por seleção, clique fora ou Escape.
- `assets/`: artes e ícones oficiais, usados também pelo manifest gerado.
- `data/videos.json`: reserva revisada e versionada de publicações do canal. Atualizações automáticas não fazem commits nesse arquivo.

O layout usa CSS e SVG inline, sem bibliotecas, fontes externas ou analytics. O player de `youtube-nocookie.com` só é criado após um clique.

## Atualização dos vídeos e build

O workflow `.github/workflows/deploy.yml` executa em pushes para `main`, manualmente e quatro vezes ao dia. A ordem é **testes → consulta do Atom → build em `dist/` → publicação**.

O atualizador consulta uma única vez o Atom público do canal e seleciona até **8 publicações recentes**, sem acumular o histórico. O HTML publicado já contém até **4 cards**, excluindo o vídeo fixo de apresentação: títulos e links podem ser lidos mesmo com JavaScript desabilitado. O navegador mantém as interações e os fallbacks das imagens sem substituir inicialmente os cards atuais por reservas antigas.

As thumbnails priorizam WebP/JPG de alta resolução quando disponíveis; depois tentam SD, HQ e a arte local. A atualização não faz uma requisição adicional por vídeo. O Atom mistura vídeos, lives e Shorts e não informa seu formato com precisão, por isso os itens do feed usam a indicação genérica “NO YOUTUBE”.

Se houver falha de rede, XML inválido ou feed vazio, o atualizador usa, nesta ordem: JSON gerado existente válido do mesmo canal; `data/videos.json` versionado válido do mesmo canal; `fallbackVideos` válidos da configuração. Se nenhuma reserva for válida, a execução falha sem publicar JSON malformado. Cada execução do Actions começa com o checkout, sem recuperar o feed do deploy anterior; o cache gerado só é preservado quando já existe naquela execução ou ambiente local.

A atualização não é em tempo real e os agendamentos podem atrasar. O GitHub pode desativar agendamentos de repositórios públicos após 60 dias sem atividade. O build não usa a hora da consulta do feed como uma alteração da página.

## Domínio e publicação

O repositório é **`vlopcas/oZECAOo`**, a branch principal é `main` e a origem do Pages é **GitHub Actions**. Após commit e push, acompanhe **Actions → ZECAO • Atualização do YouTube e publicação**; a execução também pode ser iniciada em **Run workflow**. Não são necessários secrets para o feed público.

A configuração do domínio vale apenas para este repositório. Preserve `vlopcas.dev`, seus subdomínios e os demais projetos.

1. Em **configurações da conta GitHub → Pages**, adicione/verifique `ozecaoo.com` e publique o TXT de verificação fornecido pelo GitHub. Essa etapa é manual se não houver acesso autenticado ao DNS. Preserve o TXT de verificação do Google; são registros diferentes.
2. Na Cloudflare, mantenha os quatro registros A de `ozecaoo.com` para `185.199.108.153`, `185.199.109.153`, `185.199.110.153` e `185.199.111.153`, e o CNAME de `www` para `vlopcas.github.io`, todos em **DNS only**.
3. Em **repositório → Settings → Pages → Custom domain**, configure **`ozecaoo.com`**, aguarde a validação e a emissão do certificado para raiz e `www`, e habilite **Enforce HTTPS**.
4. Confira o endereço oficial, o redirecionamento de `www`, HTTP → HTTPS e a URL antiga `https://vlopcas.github.io/oZECAOo/`.

Com GitHub Actions, um arquivo `CNAME` no repositório é ignorado: o domínio efetivo fica nas configurações do Pages. O CNAME no DNS é um registro separado. Consulte a [documentação do domínio no GitHub Pages](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site).

O certificado é provisionado de forma assíncrona; pode haver uma janela de indisponibilidade durante a troca. Não considere a migração concluída antes de testar a URL pública e os redirecionamentos.

## SEO, rastreamento e Search Console

O build gera o título, descrição, canonical, Open Graph, Twitter Card e dados estruturados JSON-LD de **WebSite, ProfilePage e Person**, vinculados por identificadores estáveis. Nome, aliases, avatar e links oficiais correspondem à apresentação visível. A página publicada contém a identificação e os vídeos no HTML, sem depender da execução de JavaScript para sua descoberta.

`robots.txt` permite rastreamento geral e os agentes Googlebot, OAI-SearchBot, GPTBot e Google-Extended. O sitemap é gerado em **https://ozecaoo.com/sitemap.xml**, contendo somente a home canônica; seções, JSON e links externos não viram páginas. Ele omite `lastmod`, `priority` e `changefreq`, sem inventar datas a cada execução. Não há `llms.txt` nesta etapa.

Depois da publicação, no Search Console:

- Confirme a propriedade de domínio `ozecaoo.com` e mantenha a verificação TXT no DNS.
- Envie `https://ozecaoo.com/sitemap.xml` em **Sitemaps**.
- Inspecione `https://ozecaoo.com/`, teste a URL publicada e solicite a indexação quando necessário.
- Confira **Settings → Search generative AI**, mantendo a opção de inclusão escolhida. Veja o [controle oficial do Google](https://support.google.com/webmasters/answer/16908024).
- Acompanhe indexação, consultas e eventuais relatórios de IA disponíveis na propriedade; atualize o link nas bios dos canais para o domínio oficial.

Essas configurações apresentam uma fonte oficial clara e rastreável. A verificação no Search Console não garante indexação; posição na busca e citações pelo ChatGPT ou Gemini dependem desses serviços. A documentação relevante está em [ProfilePage](https://developers.google.com/search/docs/appearance/structured-data/profile-page), [sitemaps](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap), [rastreadores da OpenAI](https://developers.openai.com/api/docs/bots) e [busca com IA do Google](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide).

## Imagens e compartilhamento

As artes principais são WebP: banner (1648 × 927, cerca de 190 KB), boas-vindas (1120 × 630, 82 KB), wallpaper (1400 × 788, 101 KB), avatar (600 × 600, 22 KB) e logo (650 × 650, 9 KB). As capas dos vídeos usam carregamento sob demanda. A qualidade máxima disponível varia por vídeo no YouTube.

O favicon usa a logo oficial `assets/z-brand.webp`, com ICO de 16 a 256 px e PNGs de 16, 32, 48, 180 (Apple), 192 e 512 px. Esses formatos são mantidos para compatibilidade dos ícones.

A capa de compartilhamento é **`assets/social-preview-v1.jpg`**, JPEG de 1200 × 630: avatar ilustrado, logo ZECAO e montanhas nas cores da marca. Foi composta com imagegen a partir das artes existentes; o prompt está em `docs/social-preview-v1.prompt.txt`. Open Graph declara URL absoluta, tipo, dimensões e descrição. A capa não integra o carregamento visual da página.
