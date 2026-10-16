#!/usr/bin/env python3
"""Build the public, crawlable site without modifying its source files."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
BUILD_MARKER = ".site-build"
STATIC_FILES = ("styles.css", "navigation.js", "instagram.js", "script.js", "favicon.ico", ".nojekyll")
VERSIONED_FILES = ("styles.css", "navigation.js", "instagram.js", "config.js", "script.js")
BUILD_ENTRIES = {*STATIC_FILES, "index.html", "config.js", "site.webmanifest", "robots.txt", "sitemap.xml", "assets", "data", BUILD_MARKER, "favicon.svg"}


class BuildError(ValueError):
    """A source or configuration error prevented a safe build."""


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_text(value, *, indent=2) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent) + "\n"


def script_json(value) -> str:
    # JSON embedded in HTML must never be able to close its script element.
    return json_text(value).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def required_text(mapping: dict, name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"Configuração obrigatória ausente ou inválida: {name}")
    return value.strip()


def https_url(value, name: str) -> str:
    if not isinstance(value, str):
        raise BuildError(f"URL HTTPS inválida: {name}")
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password and not any(c.isspace() or ord(c) < 32 for c in value)
        parsed.port  # Reject malformed ports as well.
    except ValueError:
        valid = False
    if not valid:
        raise BuildError(f"URL HTTPS inválida: {name}")
    return value


def asset_path(value, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or any(c.isspace() or ord(c) < 32 for c in value):
        raise BuildError(f"Caminho local inválido: {name}")
    parsed = urlsplit(value)
    parts = PurePosixPath(unquote(parsed.path)).parts
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or parsed.path.startswith("/") or ".." in parts or not parts or parts[0] != "assets":
        raise BuildError(f"O caminho deve ficar dentro de assets/: {name}")
    return value


def instagram_embed_url(profile: str) -> str:
    parsed = urlsplit(https_url(profile, "instagram"))
    username = parsed.path.strip("/")
    if parsed.hostname not in ("instagram.com", "www.instagram.com") or parsed.port or parsed.query or parsed.fragment or not re.fullmatch(r"[A-Za-z0-9._]{1,30}", username):
        raise BuildError("instagram deve ser a URL de um perfil oficial do Instagram, sem consulta ou fragmento.")
    return f"https://www.instagram.com/{username}/embed/"


def normalized_videos(items, limit: int) -> list[dict]:
    if not isinstance(items, list):
        return []
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        video_id = item.get("id")
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id) or video_id in seen:
            continue
        seen.add(video_id)
        title = item.get("title")
        kind = item.get("type")
        video = {"id": video_id, "title": title.strip() if isinstance(title, str) and title.strip() else "Vídeo no ZECAO", "published": "", "type": kind.strip() if isinstance(kind, str) and kind.strip() else "NO YOUTUBE"}
        published = item.get("published")
        if isinstance(published, str):
            try:
                parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    video["published"] = parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
            except (ValueError, OverflowError):
                pass
        thumbnail = item.get("thumbnail")
        if isinstance(thumbnail, str):
            try:
                https_url(thumbnail, "thumbnail")
                if urlsplit(thumbnail).netloc == "i.ytimg.com":
                    video["thumbnail"] = thumbnail
            except BuildError:
                pass
        result.append(video)
        if len(result) == limit:
            break
    return result


def load_config(path: Path) -> dict:
    try:
        cfg = read_json(path)
    except (OSError, ValueError) as exc:
        raise BuildError(f"Não foi possível ler a configuração {path}: {exc}") from exc
    if not isinstance(cfg, dict):
        raise BuildError("A configuração deve ser um objeto JSON.")
    for key in ("siteName", "siteTitle", "siteDescription", "language", "locale", "themeColor", "youtubeChannelId"):
        required_text(cfg, key)
    canonical = https_url(cfg.get("siteUrl"), "siteUrl")
    parsed = urlsplit(canonical)
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.port:
        raise BuildError("siteUrl deve ser a raiz HTTPS do domínio, sem caminho, porta, consulta ou fragmento.")
    cfg["siteUrl"] = canonical.rstrip("/") + "/"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", cfg["themeColor"]):
        raise BuildError("themeColor deve ser uma cor hexadecimal de seis dígitos.")
    for key in ("youtube", "instagram", "twitch", "trailer"):
        https_url(cfg.get(key), key)
    instagram_embed_url(cfg["instagram"])
    if cfg.get("discord"):
        https_url(cfg["discord"], "discord")
    if not isinstance(cfg.get("trailerVideoId"), str) or not VIDEO_ID.fullmatch(cfg["trailerVideoId"]):
        raise BuildError("trailerVideoId deve ser um ID válido de vídeo do YouTube.")
    if cfg.get("videosFeedPath") != "data/videos.json":
        raise BuildError("videosFeedPath deve ser data/videos.json.")
    for key, maximum in (("maxFeedVideos", 8), ("maxVisibleVideos", 4)):
        if type(cfg.get(key)) is not int or not 1 <= cfg[key] <= maximum:
            raise BuildError(f"{key} deve ser um inteiro entre 1 e {maximum}.")
    if cfg["maxVisibleVideos"] > cfg["maxFeedVideos"]:
        raise BuildError("maxVisibleVideos não pode exceder maxFeedVideos.")
    creator, image = cfg.get("creator"), cfg.get("socialImage")
    if not isinstance(creator, dict) or not isinstance(image, dict):
        raise BuildError("creator e socialImage devem ser objetos JSON.")
    for key in ("name", "brandName", "handle", "description", "heroDescription"):
        required_text(creator, key)
    asset_path(creator.get("image"), "creator.image")
    aliases = creator.get("alternateNames")
    if not isinstance(aliases, list) or not aliases or any(not isinstance(name, str) or not name.strip() for name in aliases):
        raise BuildError("creator.alternateNames deve conter nomes alternativos válidos.")
    asset_path(image.get("path"), "socialImage.path")
    required_text(image, "alt")
    if image.get("type") not in ("image/jpeg", "image/png", "image/webp") or any(type(image.get(key)) is not int or image[key] <= 0 for key in ("width", "height")):
        raise BuildError("socialImage deve declarar tipo, largura e altura válidos.")
    cfg["fallbackVideos"] = normalized_videos(cfg.get("fallbackVideos"), cfg["maxFeedVideos"])
    if not any(v["id"] != cfg["trailerVideoId"] for v in cfg["fallbackVideos"]):
        raise BuildError("fallbackVideos deve conter ao menos um vídeo válido além do trailer.")
    return cfg


def load_feed(path: Path, cfg: dict) -> dict:
    try:
        feed = read_json(path)
    except (OSError, ValueError):
        feed = None
    items = normalized_videos(feed.get("videos") if isinstance(feed, dict) else None, cfg["maxFeedVideos"])
    # A feed belonging to a different channel is not public content for this site.
    wrong_channel = isinstance(feed, dict) and feed.get("channel_id") not in (None, "", cfg["youtubeChannelId"])
    if wrong_channel or not any(v["id"] != cfg["trailerVideoId"] for v in items):
        items = cfg["fallbackVideos"]
        source = "configured_fallback"
    else:
        supplied_source = feed.get("source")
        source = supplied_source if supplied_source in ("youtube_atom", "configured_fallback") else "youtube_atom"
    # Poll timestamps are intentionally excluded: unchanged content builds identically.
    return {"source": source, "channel_id": cfg["youtubeChannelId"], "videos": items}


def seo_markup(cfg: dict) -> str:
    escape = lambda value: html.escape(str(value), quote=True)
    canonical = cfg["siteUrl"]
    image, creator = cfg["socialImage"], cfg["creator"]
    image_url = urljoin(canonical, image["path"])
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "@id": canonical + "#website", "url": canonical, "name": cfg["siteName"], "description": cfg["siteDescription"], "inLanguage": cfg["language"], "publisher": {"@id": canonical + "#person"}},
            {"@type": "ProfilePage", "@id": canonical + "#webpage", "url": canonical, "name": cfg["siteTitle"], "description": cfg["siteDescription"], "inLanguage": cfg["language"], "isPartOf": {"@id": canonical + "#website"}, "mainEntity": {"@id": canonical + "#person"}},
            {"@type": "Person", "@id": canonical + "#person", "name": creator["name"], "alternateName": creator["alternateNames"], "description": creator["description"], "url": canonical, "image": urljoin(canonical, creator["image"]), "sameAs": [cfg[key] for key in ("youtube", "instagram", "twitch")]},
        ],
    }
    meta = [
        ("name", "description", cfg["siteDescription"]),
        ("name", "theme-color", cfg["themeColor"]),
        ("name", "color-scheme", "dark"),
        ("name", "application-name", cfg["siteName"]),
        ("name", "robots", "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1"),
        ("property", "og:type", "website"),
        ("property", "og:site_name", cfg["siteName"]),
        ("property", "og:url", canonical),
        ("property", "og:locale", cfg["locale"]),
        ("property", "og:title", cfg["siteTitle"]),
        ("property", "og:description", cfg["siteDescription"]),
        ("property", "og:image", image_url),
        ("property", "og:image:type", image["type"]),
        ("property", "og:image:width", image["width"]),
        ("property", "og:image:height", image["height"]),
        ("property", "og:image:alt", image["alt"]),
        ("name", "twitter:card", "summary_large_image"),
        ("name", "twitter:title", cfg["siteTitle"]),
        ("name", "twitter:description", cfg["siteDescription"]),
        ("name", "twitter:image", image_url),
        ("name", "twitter:image:alt", image["alt"]),
    ]
    lines = [f"    <title>{escape(cfg['siteTitle'])}</title>", f'    <link rel="canonical" href="{escape(canonical)}" />']
    lines.extend(f'    <meta {attr}="{name}" content="{escape(value)}" />' for attr, name, value in meta)
    lines.append('    <script type="application/ld+json">\n' + script_json(graph).rstrip() + '\n    </script>')
    return "\n".join(lines)


def video_markup(videos: list[dict], cfg: dict) -> str:
    cards = []
    for video in [v for v in videos if v["id"] != cfg["trailerVideoId"]][:cfg["maxVisibleVideos"]]:
        escape = lambda value: html.escape(str(value), quote=True)
        title, video_id = escape(video["title"]), video["id"]
        date = "Vídeo no YouTube"
        if video["published"]:
            try:
                date = datetime.fromisoformat(video["published"]).astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y")
            except (ValueError, OverflowError):
                pass
        cards.append(f'''          <a class="video-tile" data-video-id="{video_id}" href="https://www.youtube.com/watch?v={video_id}" target="_blank" rel="noopener noreferrer" aria-label="Assistir no YouTube: {title}">
            <div class="video-photo">
              <img data-video-id="{video_id}" src="https://i.ytimg.com/vi_webp/{video_id}/maxresdefault.webp" alt="Capa do vídeo {title}" width="1280" height="720" loading="lazy" decoding="async" />
              <span class="video-play"><svg aria-hidden="true"><use href="#icon-play" /></svg></span>
            </div>
            <div class="video-details"><span class="video-date">{date}</span><h3>{title}</h3></div>
          </a>''')
    return "\n".join(cards)


def replace_block(template: str, name: str, content: str) -> str:
    start, end = f"<!-- site:{name}:start -->", f"<!-- site:{name}:end -->"
    if template.count(start) != 1 or template.count(end) != 1:
        raise BuildError(f"O template deve ter exatamente um bloco site:{name}.")
    before, rest = template.split(start)
    current, after = rest.split(end)
    return before + start + "\n" + content + "\n    " + end + after


def render_index(template: str, cfg: dict, videos: list[dict]) -> str:
    template = replace_block(template, "seo", seo_markup(cfg))
    template = replace_block(template, "videos", video_markup(videos, cfg))
    values = {"site_name": cfg["siteName"], "creator_name": cfg["creator"]["name"], "brand_name": cfg["creator"]["brandName"], "handle": cfg["creator"]["handle"], "hero_description": cfg["creator"]["heroDescription"], "creator_description": cfg["creator"]["description"], "creator_image": cfg["creator"]["image"], "language": cfg["language"], "youtube_videos": cfg["youtube"].rstrip("/") + "/videos", **{key: cfg[key] for key in ("youtube", "instagram", "twitch", "trailer")}}
    values["instagram_embed"] = instagram_embed_url(cfg["instagram"])
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", html.escape(value, quote=True))
    unresolved = re.findall(r"\{\{[a-zA-Z_][a-zA-Z_0-9]*\}\}", template)
    if unresolved:
        raise BuildError("Placeholders desconhecidos no template: " + ", ".join(sorted(set(unresolved))))
    return template


def webmanifest(cfg: dict) -> dict:
    return {"id": "./", "name": cfg["siteTitle"], "short_name": cfg["siteName"], "lang": cfg["language"], "start_url": "./", "scope": "./", "display": "browser", "theme_color": cfg["themeColor"], "background_color": cfg["themeColor"], "icons": [{"src": f"assets/icons/icon-{size}x{size}.png", "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"} for size in (192, 512)]}


def version_assets(page: str, output: Path) -> str:
    # Keep readable filenames, but give changed CSS/JS a new URL in browser caches.
    versions = {name: hashlib.sha256((output / name).read_bytes()).hexdigest()[:12] for name in VERSIONED_FILES}
    filenames = "|".join(re.escape(name) for name in VERSIONED_FILES)
    attribute = re.compile(rf'''(?P<prefix>\b(?i:href|src)\s*=\s*)(?P<quote>["'])(?P<name>{filenames})(?P=quote)''')

    def version_tag(match):
        return attribute.sub(lambda attr: f"{attr['prefix']}{attr['quote']}{attr['name']}?v={versions[attr['name']]}{attr['quote']}", match.group(0))

    return re.sub(r"<(?:link|script)\b[^>]*>", version_tag, page, flags=re.IGNORECASE)


class ReferenceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("src", "href") and value:
                self.references.append(value)


def validate_output(output: Path, cfg: dict) -> None:
    parser = ReferenceParser()
    parser.feed((output / "index.html").read_text(encoding="utf-8"))
    references = parser.references + [cfg["creator"]["image"], cfg["socialImage"]["path"]]
    references.extend(icon["src"] for icon in read_json(output / "site.webmanifest")["icons"])
    for reference in references:
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        path = (output / unquote(parsed.path).lstrip("/")).resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file():
            raise BuildError(f"Referência local não publicada: {reference}")


def validate_output_path(root: Path, output: Path) -> None:
    if output.is_symlink():
        raise BuildError("O diretório de saída não pode ser um link simbólico.")
    resolved = output.resolve()
    if resolved == root or resolved in root.parents:
        raise BuildError("O diretório de saída não pode substituir a raiz ou seus diretórios ancestrais.")
    for name in ("assets", "data", "scripts", "tests", "docs", ".github", ".git"):
        source = root / name
        if resolved == source or resolved.is_relative_to(source):
            raise BuildError(f"O diretório de saída não pode substituir fontes em {name}/.")
    if output.exists():
        if not output.is_dir():
            raise BuildError("O caminho de saída deve ser um diretório.")
        entries = {entry.name for entry in output.iterdir()}
        owned = (output / BUILD_MARKER).is_file()
        legacy_dist = resolved == root / "dist" and entries <= BUILD_ENTRIES
        if entries and not owned and not legacy_dist:
            raise BuildError("Saída não vazia sem marcador de build; use um diretório vazio ou gerado pelo builder.")


def build_site(root: Path, output: Path | None = None, config_path: Path | None = None, feed_path: Path | None = None) -> Path:
    root = Path(root).resolve()
    output = Path(output) if output is not None else root / "dist"
    output = output.absolute()
    validate_output_path(root, output)
    cfg = load_config(Path(config_path) if config_path is not None else root / "site.config.json")
    # Read everything before replacing dist: CI may supply a refreshed feed there.
    feed = load_feed(Path(feed_path) if feed_path is not None else root / cfg["videosFeedPath"], cfg)
    try:
        template = (root / "index.html").read_text(encoding="utf-8")
        rendered = render_index(template, cfg, feed["videos"])
    except OSError as exc:
        raise BuildError(f"Não foi possível ler o template: {exc}") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".site-build-", dir=output.parent) as temporary:
        staged = Path(temporary) / "public"
        staged.mkdir()
        try:
            for filename in STATIC_FILES:
                shutil.copyfile(root / filename, staged / filename)
            for name in ("assets", "data"):
                shutil.copytree(root / name, staged / name)
        except OSError as exc:
            raise BuildError(f"Não foi possível copiar um arquivo público: {exc}") from exc
        runtime = {key: cfg[key] for key in ("youtube", "twitch", "instagram", "trailer", "trailerVideoId", "videosFeedPath", "fallbackVideos", "maxVisibleVideos")}
        runtime["discord"] = cfg.get("discord", "")
        (staged / "config.js").write_text("/* Gerado de site.config.json; não editar. */\nwindow.ZECAO_CONFIG = " + script_json(runtime).rstrip() + ";\n", encoding="utf-8")
        (staged / "index.html").write_text(version_assets(rendered, staged), encoding="utf-8")
        (staged / "site.webmanifest").write_text(json_text(webmanifest(cfg)), encoding="utf-8")
        (staged / cfg["videosFeedPath"]).write_text(json_text(feed), encoding="utf-8")
        robots = "\n\n".join(f"User-agent: {agent}\nAllow: /" for agent in ("*", "Googlebot", "OAI-SearchBot", "GPTBot", "Google-Extended"))
        (staged / "robots.txt").write_text(robots + "\n\nSitemap: " + cfg["siteUrl"] + "sitemap.xml\n", encoding="utf-8")
        namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
        ElementTree.register_namespace("", namespace)
        sitemap = ElementTree.Element(f"{{{namespace}}}urlset")
        ElementTree.SubElement(ElementTree.SubElement(sitemap, f"{{{namespace}}}url"), f"{{{namespace}}}loc").text = cfg["siteUrl"]
        (staged / "sitemap.xml").write_bytes(ElementTree.tostring(sitemap, encoding="utf-8", xml_declaration=True) + b"\n")
        (staged / BUILD_MARKER).write_text("Generated by scripts/build_site.py\n", encoding="utf-8")
        validate_output(staged, cfg)
        if output.exists():
            shutil.rmtree(output)
        staged.replace(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Raiz do projeto (padrão: raiz deste script)")
    parser.add_argument("--output", type=Path, help="Diretório público de saída (padrão: <root>/dist)")
    parser.add_argument("--config", type=Path, help="Configuração JSON alternativa")
    parser.add_argument("--feed", type=Path, help="Feed JSON alternativo; feeds inválidos usam a reserva configurada")
    args = parser.parse_args()
    try:
        output = build_site(args.root, args.output, args.config, args.feed)
    except (BuildError, OSError) as exc:
        print(f"Falha no build: {exc}", file=sys.stderr)
        return 1
    print(f"Site gerado e validado em {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
