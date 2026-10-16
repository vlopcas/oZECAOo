# oZECAO - Jogos, Lives e etc

Página estática para o canal **ZECAO / @ozecaoo**, feita para substituir o Linktree sem virar uma aplicação pesada. O visual da V4 reduz padrões repetidos de templates: sem numeração de seções, sem badges decorativas, sem setas externas repetidas. Cada rede tem uma ação verbal própria, a apresentação tem composição assimétrica e os vídeos são valorizados pela capa e pelo título. A identidade usa as artes originais do ZECAO. Na V4.1, o avatar fica abaixo do banner e alinhado com a saudação; o YouTube aparece primeiro e inclui lives ao vivo, o Instagram em segundo, e a Twitch é um canal ocasional.

## Rodar localmente

O site não precisa de Node.js, instalação de pacotes ou build local. Na pasta do projeto, inicie um servidor para carregar também a lista de vídeos:

```bash
python3 -m http.server 8000 --bind 127.0.0.1
```

Abra `http://localhost:8000`.

Para consultar os vídeos atuais do canal antes de testar, rode `python3 scripts/update_videos.py`. Para parar o servidor, use `Ctrl+C` no terminal em que ele estiver rodando.

## Integrações existentes

| Item | Fonte |
| --- | --- |
| YouTube | https://www.youtube.com/channel/UCkS1cj-1C9ahgYVvFGz86qw |
| Twitch | https://www.twitch.tv/ozecaoo |
| Instagram | https://www.instagram.com/ozecaoo/ |
| Discord | Em breve, sem link ativo |
| Apresentação | https://youtu.be/ItDJkbW2M4A |

Os links fixos ficam em `config.js`.

## Novos vídeos sem edição manual

O repositório contém `.github/workflows/deploy.yml`, um workflow executado a cada push, manualmente e cerca de 4 vezes ao dia pelo agendamento do GitHub.

1. `scripts/update_videos.py` consulta o feed público Atom do canal no YouTube (não usa chave de API).
2. Constrói `data/videos.json` com até 8 publicações recentes.
3. O workflow monta os arquivos estáticos e publica no GitHub Pages.
4. `script.js` exibe até 4 vídeos, com título, data e thumbnail oficial do YouTube. Prioriza `maxresdefault` em WebP (1280 × 720 quando disponível), depois JPG na mesma resolução, SD e HQ. Vídeos antigos podem ter apenas a versão HQ (480 × 360). Se todas as versões externas falharem, aparece a paisagem do ZECAO como reserva. Placeholders de 120 × 90 também são descartados.
5. O vídeo de apresentação não listado permanece fixo e é reproduzido dentro da página após um clique.

**Limitações:** o feed do YouTube mistura vídeos, lives e Shorts e não informa com precisão o formato. A atualização não acontece em tempo real. O GitHub pode desativar agendamentos em repositórios públicos após 60 dias sem atividade no próprio repositório. Se a consulta ao feed falhar, o JSON pré-carregado no projeto é usado como fallback naquela execução. As atualizações do workflow são publicadas no site, sem fazer commits no repositório; cada execução começa com o JSON versionado, sem recuperar o arquivo do deploy anterior.

Para diferenciar automaticamente lives, Shorts e vídeos gravados no futuro, será necessária uma lógica complementar de enriquecimento dos metadados (por exemplo, YouTube Data API com chave guardada no GitHub Actions). Esta V4 não depende disso.

## Publicar no GitHub Pages

O repositório deste projeto é `vlopcas/oZECAOo`, com a branch principal `main`. A origem do Pages já está configurada como **GitHub Actions**.

1. Faça commit e push dos arquivos extraídos na raiz, incluindo `.github` e `.nojekyll`. O ZIP original e `dist/` ficam ignorados pelo Git.
2. Acompanhe o workflow **ZECAO • Atualização do YouTube e publicação** na aba **Actions**.
3. Depois de o deploy terminar, acesse **https://vlopcas.github.io/oZECAOo/**.

O workflow também pode ser executado manualmente em **Actions → Run workflow**. Não são necessários secrets ou chave de API para a consulta ao feed público.

A compra de um domínio é opcional e independente do código. Os caminhos relativos de estilos, scripts e imagens permitem usar tanto `/oZECAOo/` quanto a raiz de um domínio próprio.

### Adicionar domínio próprio depois

1. Em **Settings → Pages → Custom domain**, informe o domínio escolhido e salve.
2. Configure o DNS conforme a [documentação do GitHub Pages](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site). Para `www` ou outro subdomínio, o CNAME no DNS aponta para `vlopcas.github.io`, sem `/oZECAOo/`. Para o domínio raiz, use os registros indicados na documentação.
3. Aguarde a validação do DNS e a emissão do certificado, e confirme **Enforce HTTPS**.
4. Atualize `og:url` e `og:image` no `index.html` com as URLs absolutas da página e da imagem no domínio definitivo.

Com publicação por GitHub Actions, não é necessário adicionar um arquivo `CNAME` ao repositório. O registro CNAME no DNS é uma configuração separada.

## Onde editar

- `index.html`: textos e estrutura.
- `favicon.ico` e `assets/icons/`: ícones derivados da logo oficial `assets/z-brand.webp`, a mesma do cabeçalho. O ICO contém 16, 24, 32, 48, 64, 128 e 256 px; os PNGs incluem 16, 32, 48, 180 (Apple), 192 e 512 px.
- `site.webmanifest`: nome do site, cores e ícones para atalhos. Os caminhos relativos funcionam na URL do GitHub Pages e no domínio próprio.
- `styles.css`: cores, composição, responsividade e animações.
- `config.js`: redes sociais, apresentação e vídeos de fallback.
- `script.js`: player e renderização dos vídeos.
- `navigation.js`: menu hambúrguer do mobile, com os mesmos links do desktop e fechamento por seleção, clique fora ou Escape.
- `assets/`: artes do ZECAO (banner, avatar, Z, thumb, wallpaper).
- `scripts/update_videos.py`: atualização automática via feed Atom.

O layout usa CSS e SVG inline, sem bibliotecas, fontes externas ou analytics. As thumbnails são carregadas de `i.ytimg.com`; o player do YouTube em `youtube-nocookie.com` só é criado após um clique. `og:url` e `og:image` usam a URL inicial do GitHub Pages; depois de configurar o domínio definitivo, atualize essas URLs para manter a prévia em redes sociais.

## Imagens

As artes principais já são WebP: banner (1648 × 927, cerca de 190 KB), boas-vindas (1120 × 630, 82 KB), wallpaper (1400 × 788, 101 KB), avatar (600 × 600, 22 KB) e logo (650 × 650, 9 KB). Os PNGs são reservados aos ícones de navegador, Apple e atalhos; o favicon também tem versão ICO. WebM é um formato de vídeo.

As thumbnails mais nítidas dependem da resolução disponível no YouTube. Nos dois vídeos recentes verificados, o WebP de 1280 × 720 pesa cerca de 98–105 KB, aproximadamente metade do JPG equivalente. As capas usam carregamento sob demanda (`loading="lazy"`).

A imagem de compartilhamento é `assets/social-preview-v1.jpg`, em JPEG de 1200 × 630: avatar ilustrado em primeiro plano, logo ZECAO e montanhas nas cores da marca. Foi composta com imagegen a partir de `avatar.webp`, `hero.webp` e `wallpaper-wide.webp`; o prompt está em `docs/social-preview-v1.prompt.txt`. Os metadados Open Graph declaram URL absoluta, tipo, dimensões e descrição da imagem. A imagem não é carregada como parte do layout da página.
