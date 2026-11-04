#!/usr/bin/env python3
"""Operações explícitas do webhook, com Bearer local e sem imprimir credenciais."""
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "subscribe", "unsubscribe", "maintenance", "test-dispatch"))
    parser.add_argument("--confirm-real-dispatch", action="store_true")
    args = parser.parse_args()
    if args.action == "test-dispatch" and not args.confirm_real_dispatch:
        print("Teste real exige autorização do responsável e --confirm-real-dispatch.")
        return 1
    try:
        secrets = json.loads((ROOT / ".secrets.local.json").read_text())
        config = json.loads((ROOT / "wrangler.jsonc").read_text())
        callback = config["vars"]["CALLBACK_URL"]
        from urllib.parse import urlsplit
        origin = urlsplit(callback)
        if origin.scheme != "https" or origin.username or origin.password or origin.port:
            raise ValueError
        body = {}
        if args.action == "test-dispatch":
            with urllib.request.urlopen("https://ozecaoo.com/data/videos.json", timeout=15) as response:
                feed = json.load(response)
            if feed.get("channel_id") != config["vars"]["CHANNEL_ID"]:
                raise ValueError
            import re
            ids = [item.get("id") for item in feed.get("videos", [])]
            if not ids or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", value) for value in ids):
                raise ValueError
            body = {"videoIds": ids[:50]}
        url = f"https://{origin.hostname}/admin/{args.action}"
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + secrets["ADMIN_TOKEN"], "Content-Type": "application/json",
            "User-Agent": "ozecaoo-websub-admin/1.0",
        }, method="GET" if args.action == "status" else "POST",
            data=None if args.action == "status" else json.dumps(body).encode())
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
            payload = json.loads(response.read(65_536))
        # Status and public identifiers only; redact defensively before display.
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        for value in secrets.values():
            if isinstance(value, str) and value:
                text = text.replace(value, "[REDACTED]")
        print(text)
        return 0
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        print(f"Operação retornou HTTP {status}; consulte logs estruturados sem credenciais.")
    except (OSError, ValueError, KeyError, TypeError):
        print("Operação indisponível: confira arquivo local de secrets, configuração e endpoint.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
