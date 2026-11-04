#!/usr/bin/env python3
"""Atualiza o feed gerado sem alterar o JSON de reserva versionado.

API v3 principal, Atom validado como alternativa e recuperação do site publicado.
Credenciais existem somente na variável de ambiente do passo de sincronização.
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

if __package__:
    from . import youtube_api
else:
    import youtube_api

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


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def safe_text(value):
    text = str(value)
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    return text.replace(key, "[REDACTED]") if key else text


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
            video = {
                "id": video_id,
                "title": title.strip(),
                "published": published,
                "type": video_type,
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            }
            if timestamp(item.get("sort_at")):
                video["sort_at"] = item["sort_at"]
            result.append(video)
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
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read(maximum + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise
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
    message = safe_text(message)
    print(f"AVISO: {message}", file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # Escapa os caracteres de controle do formato de annotations do Actions.
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::warning::{escaped}")


def write_json(path, payload):
    """Substitui o arquivo de destino somente depois de serializá-lo por inteiro."""
    serialized = safe_text(json.dumps(payload, ensure_ascii=False, indent=2)) + "\n"
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


def last_success(payload):
    metadata = payload.get("sync", {})
    candidate = metadata.get("last_success_at") if isinstance(metadata, dict) else None
    # Legacy published files may have lost the query timestamp in the old build.
    candidate = candidate or payload.get("updated_at")
    return candidate if timestamp(candidate) else None


def retrieve_published(url, channel_id, limit, trailer_id):
    request = urllib.request.Request(url, headers={
        "User-Agent": "ZECAO-site-feed/1.0", "Accept": "application/json",
        "Cache-Control": "no-cache",
    })
    return validated_payload(json.loads(read_url(request, 256_000).decode("utf-8")),
                             channel_id, limit, trailer_id)


def atom_recordings(channel_id, limit, trailer_id, key, now, baseline, can_validate=True):
    # Atom alone cannot prove that an announced live has finished.
    candidates = fetch_videos(channel_id, 50, trailer_id)
    if not key or not can_validate:
        raise ValueError("Atom não confirma estado das lives; API indisponível para validar gravações")
    ids = [v["id"] for v in candidates]
    if baseline:
        ids.extend(v["id"] for v in baseline["videos"])
    recordings = youtube_api.fetch_video_details(key, channel_id, ids,
                                                excluded_id=trailer_id, now=now)
    if len(recordings) < limit:
        raise ValueError("Atom sem gravações públicas confirmadas suficientes")
    return recordings[:limit]


def emit_result(args, state, source, channel_id, videos, attempted_at,
                successful_at, reasons):
    report = {
        "state": state, "source": source, "channel_id": channel_id,
        "videos": videos, "attempted_at": attempted_at,
        "last_success_at": successful_at,
        "fallback_reasons": [safe_text(reason) for reason in reasons],
    }
    if args.report:
        # Runtime report lives outside dist; it is never a Pages artifact.
        write_json(args.report, report)
    print(safe_text(f"{state}: fonte={source or 'nenhuma'}; vídeos={len(videos)}."))
    return 1 if state == "FAILED" else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="Atom XML local, sem consulta de rede")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="JSON gerado (padrão: dist/data/videos.json)")
    parser.add_argument("--config", type=Path, default=CONFIG, help="Configuração central do site")
    parser.add_argument("--report", type=Path, help="Relatório de sincronização interno, fora de dist")
    parser.add_argument("--preserve-published", action="store_true",
                        help="Se o YouTube falhar, usa somente o feed válido do site publicado; falha se indisponível")
    args = parser.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    attempted_at = now.isoformat(timespec="seconds")
    reasons = []
    channel_id = "desconhecido"
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
        reason = safe_text(f"configuração inválida: {exc}")
        print(f"ERRO: {reason}", file=sys.stderr)
        return emit_result(args, "FAILED", None, channel_id, [], attempted_at, None, [reason])

    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    baseline = None
    baseline_error = None
    # Read the public baseline only for comparison/recovery, never as a source
    # of new uploads. Fixture execution is entirely offline.
    if args.preserve_published and not args.fixture:
        try:
            baseline = retrieve_published(recovery_url, channel_id, limit, trailer_id)
        except NETWORK_ERRORS as exc:
            baseline_error = safe_text(f"JSON publicado indisponível ou inválido: {exc}")

    videos = None
    source = None
    if args.fixture:
        try:
            videos = parse_feed(args.fixture.read_bytes(), limit=limit, excluded_id=trailer_id)
            if not videos:
                raise ValueError("feed sem vídeos válidos")
            source = "youtube_atom"
        except NETWORK_ERRORS as exc:
            reasons.append(safe_text(f"fixture Atom inválida: {exc}"))
    else:
        try:
            known_ids = [v["id"] for v in baseline["videos"]] if baseline else []
            videos = youtube_api.fetch_recent_videos(key, channel_id, limit, trailer_id,
                                                     known_ids=known_ids, now=now)
            source = "youtube_data_api_v3"
        except (youtube_api.APIError, OSError, ValueError) as exc:
            reason = safe_text(f"API v3: {exc}")
            reasons.append(reason)
            warn(reason)
            try:
                videos = atom_recordings(channel_id, limit, trailer_id, key, now, baseline,
                                         getattr(exc, "can_validate_fallback", True))
                source = "youtube_atom"
            except (*NETWORK_ERRORS, youtube_api.APIError) as atom_error:
                if isinstance(atom_error, urllib.error.HTTPError):
                    atom_error.close()
                reason = safe_text(f"Atom/validação de gravações: {atom_error}")
                reasons.append(reason)
                warn(reason)

    if videos:
        videos = json.loads(safe_text(json.dumps(videos, ensure_ascii=False)))
        payload = {"source": source, "channel_id": channel_id, "videos": videos,
                   "updated_at": attempted_at,
                   "sync": {"state": "UPDATED", "attempted_at": attempted_at,
                            "last_success_at": attempted_at, "last_success_source": source}}
        write_json(args.output, payload)
        print(f"Atualizados/confirmados {len(videos)} vídeos em {args.output}.")
        return emit_result(args, "UPDATED", source, channel_id, videos, attempted_at,
                           attempted_at, reasons)

    if args.preserve_published:
        if baseline:
            successful_at = last_success(baseline)
            previous_sync = baseline.get("sync", {})
            previous_source = (previous_sync.get("last_success_source")
                               if isinstance(previous_sync, dict) else None)
            if previous_source not in ("youtube_data_api_v3", "youtube_atom"):
                previous_source = baseline.get("source")
            payload = {"source": "published_cache", "channel_id": channel_id,
                       "videos": baseline["videos"],
                       "sync": {"state": "PRESERVED", "attempted_at": attempted_at,
                                "last_success_at": successful_at,
                                "last_success_source": previous_source if previous_source in
                                ("youtube_data_api_v3", "youtube_atom") else None}}
            if successful_at:
                payload["updated_at"] = successful_at
            write_json(args.output, json.loads(safe_text(json.dumps(payload, ensure_ascii=False))))
            warn("PRESERVED: consultas falharam; vídeos publicados preservados sem atualizar a sincronização.")
            return emit_result(args, "PRESERVED", "published_cache", channel_id,
                               payload["videos"], attempted_at, successful_at, reasons)
        if baseline_error:
            reasons.append(baseline_error)
        print("ERRO: FAILED; publicação interrompida para manter o site atual.", file=sys.stderr)
        return emit_result(args, "FAILED", None, channel_id, [], attempted_at, None, reasons)

    # Offline preview keeps its original local reserve behavior. It is always
    # reported as PRESERVED, not as a successful query to YouTube.
    if not videos:
        for candidate in (args.output, VERSIONED_FEED):
            try:
                payload = load_cached(candidate, channel_id, limit, trailer_id)
                cached_ids = [item["id"] for item in json.loads(candidate.read_text(encoding="utf-8"))["videos"]]
            except (OSError, ValueError):
                continue
            if candidate != args.output or cached_ids != [item["id"] for item in payload["videos"]]:
                write_json(args.output, payload)
            print(f"Mantidos {len(payload['videos'])} vídeos da reserva válida: {candidate}.")
            return emit_result(args, "PRESERVED", payload.get("source"), channel_id,
                               payload["videos"], attempted_at, last_success(payload), reasons)
        try:
            videos = validated_videos(config.get("fallbackVideos"), limit, trailer_id)
        except ValueError as fallback_error:
            print(f"ERRO: sem feed e sem reserva válida: {fallback_error}", file=sys.stderr)
            return emit_result(args, "FAILED", None, channel_id, [], attempted_at, None,
                               reasons + [safe_text(str(fallback_error))])
        payload = {"source": "configured_fallback", "channel_id": channel_id, "videos": videos}
        write_json(args.output, payload)
        print(f"Usados {len(videos)} vídeos de reserva da configuração.")
        return emit_result(args, "PRESERVED", "configured_fallback", channel_id,
                           videos, attempted_at, None, reasons)


if __name__ == "__main__":
    raise SystemExit(main())
