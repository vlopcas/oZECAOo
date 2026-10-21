#!/usr/bin/env python3
"""Atualiza o feed gerado sem alterar o JSON de reserva versionado.

Usa somente a biblioteca padrão e o Atom público do YouTube. Na indisponibilidade
do feed, pode preservar os últimos dados publicados em vez da reserva versionada.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "site.config.json"
OUTPUT = ROOT / "dist" / "data" / "videos.json"
VERSIONED_FEED = ROOT / "data" / "videos.json"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
VALID_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VALID_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
NETWORK_ERRORS = (OSError, ValueError, urllib.error.URLError, ET.ParseError)
REQUEST_TIMEOUT = 15
FEED_ATTEMPTS = 3


def parse_feed(xml_bytes, limit=2, excluded_id=""):
    """Seleciona publicações válidas na ordem fornecida pelo Atom, sem duplicar."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limite de vídeos deve ser um inteiro positivo")
    root = ET.fromstring(xml_bytes)
    results = []
    seen = set()
    for entry in root.findall(f"{ATOM}entry"):
        id_text = entry.findtext(f"{YT}videoId")
        if not id_text or not VALID_VIDEO_ID.fullmatch(id_text) or id_text == excluded_id or id_text in seen:
            continue
        title = (entry.findtext(f"{ATOM}title") or "Vídeo ZECAO").strip() or "Vídeo ZECAO"
        published = entry.findtext(f"{ATOM}published") or ""
        # O Atom só anuncia HQ. O navegador verifica WebP/JPG maiores e faz
        # fallback; a atualização não precisa de consultas adicionais por vídeo.
        results.append({
            "id": id_text,
            "title": title,
            "published": published,
            "type": "NO YOUTUBE",
            "thumbnail": f"https://i.ytimg.com/vi/{id_text}/maxresdefault.jpg",
        })
        seen.add(id_text)
        if len(results) == limit:
            break
    return results


def validated_videos(items, limit, excluded_id=""):
    """Valida reservas sem confiar em URLs de thumbnails vindas do JSON."""
    if not isinstance(items, list) or not items:
        raise ValueError("lista de vídeos ausente ou vazia")
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("vídeo deve ser um objeto")
        video_id = item.get("id")
        title = item.get("title")
        if not isinstance(video_id, str) or not VALID_VIDEO_ID.fullmatch(video_id):
            raise ValueError("ID de vídeo inválido")
        if video_id == excluded_id:
            continue
        if not isinstance(title, str) or not title.strip():
            raise ValueError("título de vídeo inválido")
        published = item.get("published", "")
        video_type = item.get("type", "NO YOUTUBE")
        if not isinstance(published, str) or not isinstance(video_type, str):
            raise ValueError("metadados de vídeo inválidos")
        if video_id not in seen and len(result) < limit:
            result.append({
                "id": video_id,
                "title": title.strip(),
                "published": published,
                "type": video_type,
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            })
            seen.add(video_id)
    if not result:
        raise ValueError("reserva sem vídeos válidos além da apresentação")
    return result


def validated_payload(payload, channel_id, limit, excluded_id=""):
    """Aceita somente dados válidos do canal configurado."""
    if not isinstance(payload, dict) or payload.get("channel_id") != channel_id:
        raise ValueError("cache pertence a outro canal ou tem formato inválido")
    return {**payload, "videos": validated_videos(payload.get("videos"), limit, excluded_id)}


def load_cached(path, channel_id, limit, excluded_id=""):
    """Retorna apenas um cache válido do canal configurado."""
    return validated_payload(json.loads(path.read_text(encoding="utf-8")), channel_id, limit, excluded_id)


def published_feed_url(config):
    """Deriva a única rota de recuperação permitida da raiz HTTPS do site."""
    site_url = config.get("siteUrl")
    if not isinstance(site_url, str) or site_url != site_url.strip() or any(c.isspace() for c in site_url):
        raise ValueError("siteUrl deve ser uma raiz HTTPS válida")
    parsed = urllib.parse.urlsplit(site_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.netloc != parsed.hostname
            or not re.fullmatch(r"[a-z0-9.-]+", parsed.hostname)
            or parsed.path not in ("", "/") or "?" in site_url or "#" in site_url):
        raise ValueError("siteUrl deve ser uma raiz HTTPS sem credenciais, porta, query ou fragmento")
    if config.get("videosFeedPath") != "data/videos.json":
        raise ValueError("videosFeedPath deve ser data/videos.json")
    return f"https://{parsed.hostname}/data/videos.json"


def read_url(request, maximum):
    """Limita o tempo e o tamanho de cada resposta antes de interpretá-la."""
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        body = response.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError("resposta excede o limite de tamanho")
    return body


def fetch_videos(channel_id, limit, excluded_id):
    request = urllib.request.Request(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
        headers={"User-Agent": "Mozilla/5.0 ZECAO-site-feed/1.0",
                 "Accept": "application/atom+xml,application/xml,text/xml"},
    )
    for attempt in range(FEED_ATTEMPTS):
        try:
            videos = parse_feed(read_url(request, 2_000_000), limit, excluded_id)
            if not videos:
                raise ValueError("feed sem vídeos válidos")
            return videos
        except NETWORK_ERRORS as exc:
            retryable = (not isinstance(exc, urllib.error.HTTPError)
                         or exc.code in (404, 408, 429) or 500 <= exc.code <= 599)
            if not retryable or attempt == FEED_ATTEMPTS - 1:
                raise
            print(f"AVISO: tentativa {attempt + 1}/{FEED_ATTEMPTS} do YouTube falhou ({exc}); nova tentativa.",
                  file=sys.stderr)
            time.sleep(attempt + 1)


def warn(message):
    print(f"AVISO: {message}", file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # Escapa os caracteres de controle do formato de annotations do Actions.
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::warning::{escaped}")


def write_json(path, payload):
    """Substitui o arquivo de destino somente depois de serializá-lo por inteiro."""
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="Atom XML local, sem consulta de rede")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="JSON gerado (padrão: dist/data/videos.json)")
    parser.add_argument("--config", type=Path, default=CONFIG, help="Configuração central do site")
    parser.add_argument("--preserve-published", action="store_true",
                        help="Se o YouTube falhar, usa somente o feed válido do site publicado; falha se indisponível")
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("configuração deve ser um objeto")
        channel_id = config.get("youtubeChannelId")
        if not isinstance(channel_id, str) or not VALID_CHANNEL_ID.fullmatch(channel_id):
            raise ValueError("youtubeChannelId inválido")
        limit = config.get("maxFeedVideos", 2)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8:
            raise ValueError("maxFeedVideos deve ser um inteiro de 1 a 8")
        trailer_id = config.get("trailerVideoId", "")
        if not isinstance(trailer_id, str) or trailer_id and not VALID_VIDEO_ID.fullmatch(trailer_id):
            raise ValueError("trailerVideoId inválido")
        recovery_url = published_feed_url(config) if args.preserve_published else None
    except (OSError, ValueError) as exc:
        print(f"ERRO: configuração inválida: {exc}", file=sys.stderr)
        return 1

    try:
        if args.fixture:
            videos = parse_feed(args.fixture.read_bytes(), limit=limit, excluded_id=trailer_id)
            if not videos:
                raise ValueError("feed sem vídeos válidos")
        else:
            videos = fetch_videos(channel_id, limit, trailer_id)
    except NETWORK_ERRORS as exc:
        warn(f"não foi possível consultar o YouTube ({exc}).")
        if args.preserve_published:
            try:
                if args.fixture:
                    raise ValueError("fixture inválida; recuperação de rede desabilitada")
                request = urllib.request.Request(recovery_url, headers={
                    "User-Agent": "ZECAO-site-feed/1.0", "Accept": "application/json",
                    "Cache-Control": "no-cache",
                })
                payload = validated_payload(json.loads(read_url(request, 256_000).decode("utf-8")),
                                            channel_id, limit, trailer_id)
            except NETWORK_ERRORS as recovery_error:
                print(f"ERRO: não foi possível preservar o feed publicado ({recovery_error}); "
                      "publicação interrompida para manter o site atual.", file=sys.stderr)
                return 1
            write_json(args.output, payload)
            print(f"Preservados {len(payload['videos'])} vídeos do site publicado; "
                  "o YouTube não foi atualizado nesta execução.")
            return 0
        for candidate in (args.output, VERSIONED_FEED):
            try:
                payload = load_cached(candidate, channel_id, limit, trailer_id)
                cached_ids = [item["id"] for item in json.loads(candidate.read_text(encoding="utf-8"))["videos"]]
            except (OSError, ValueError):
                continue
            if candidate != args.output or cached_ids != [item["id"] for item in payload["videos"]]:
                write_json(args.output, payload)
            print(f"Mantidos {len(payload['videos'])} vídeos da reserva válida: {candidate}.")
            return 0
        try:
            videos = validated_videos(config.get("fallbackVideos"), limit, trailer_id)
        except ValueError as fallback_error:
            print(f"ERRO: sem feed e sem reserva válida: {fallback_error}", file=sys.stderr)
            return 1
        payload = {"source": "configured_fallback", "channel_id": channel_id, "videos": videos}
        write_json(args.output, payload)
        print(f"Usados {len(videos)} vídeos de reserva da configuração.")
        return 0

    payload = {
        "source": "youtube_atom",
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "channel_id": channel_id,
        "videos": videos,
    }
    write_json(args.output, payload)
    print(f"Atualizados {len(videos)} vídeos do ZECAO em {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
