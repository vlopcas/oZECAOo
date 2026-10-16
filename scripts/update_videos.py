#!/usr/bin/env python3
"""Consulta Atom público do YouTube e gera data/videos.json para o GitHub Pages.
Sem chave de API, banco ou backend de execução. Caso a rede falhe, mantém o último JSON.
"""
import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

CHANNEL_ID = "UCkS1cj-1C9ahgYVvFGz86qw"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "videos.json"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
VALID_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    results = []
    seen = set()
    for entry in root.findall(f"{ATOM}entry"):
        id_text = entry.findtext(f"{YT}videoId")
        if not id_text or not VALID_VIDEO_ID.fullmatch(id_text) or id_text in seen:
            continue
        title = (entry.findtext(f"{ATOM}title") or "Vídeo ZECAO").strip()
        published = entry.findtext(f"{ATOM}published") or ""
        # O Atom anuncia apenas hqdefault (480 × 360). Prefira a versão maior;
        # o navegador tenta sddefault e hqdefault se ela não estiver disponível.
        # Isso mantém o build sem consultas adicionais por vídeo ou chave de API.
        thumb = f"https://i.ytimg.com/vi/{id_text}/maxresdefault.jpg"
        # RSS não informa com precisão se é live, Shorts ou vídeo longo.
        results.append({"id":id_text,"title":title,"published":published,
                        "type":"NO YOUTUBE","thumbnail":thumb})
        seen.add(id_text)
    # O YouTube normalmente retorna até 15 entradas, incluindo lives/Shorts quando aparecem no feed.
    return results[:8]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path, help='XML local para teste')
    args = parser.parse_args()
    try:
        if args.fixture:
            body = args.fixture.read_bytes()
        else:
            req = urllib.request.Request(FEED_URL, headers={"User-Agent":"Mozilla/5.0 ZECAO-site-feed/1.0","Accept":"application/atom+xml,application/xml,text/xml"})
            with urllib.request.urlopen(req, timeout=25) as response:
                body = response.read(2_000_000)
        videos = parse_feed(body)
        if not videos:
            raise RuntimeError('feed sem vídeos; preservando arquivo existente')
    except Exception as exc:
        if OUTPUT.exists():
            print(f'AVISO: não foi possível atualizar o YouTube ({exc}); preservando o JSON de fallback/cache.', file=sys.stderr)
            return 0
        print(f'ERRO: sem feed e sem fallback: {exc}',file=sys.stderr)
        return 1
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    result = {"source":"youtube_atom", "updated_at":dt.datetime.now(dt.timezone.utc).isoformat(), "channel_id":CHANNEL_ID, "videos":videos}
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Atualizados {len(videos)} vídeos do ZECAO.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
