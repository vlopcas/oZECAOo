#!/usr/bin/env python3
"""Inspeção READ-ONLY usando OAuth do Wrangler, sem imprimir o token."""
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.cloudflare.com/client/v4"


def main():
    try:
        result = subprocess.run(["npx", "wrangler", "auth", "token", "--json"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        credentials = json.loads(result.stdout)
        token = next((credentials[k] for k in ("token", "apiToken", "oauthToken", "api_token", "oauth_token")
                      if isinstance(credentials.get(k), str)), None)
        if not token:
            print("Formato de autenticação não reconhecido; use wrangler whoami.")
            return 1

        def get(path):
            try:
                request = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + token})
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = json.loads(response.read(2_000_000))
                return data.get("result") if data.get("success") else {"unavailable": True}
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                return {"unavailable": True, "status": status}

        zones = get("/zones?name=ozecaoo.com")
        if not isinstance(zones, list) or len(zones) != 1:
            print("Zona única ozecaoo.com não encontrada com a autenticação atual.")
            return 1
        zone = zones[0]
        aid, zid = zone["account"]["id"], zone["id"]
        dns = get(f"/zones/{zid}/dns_records?name=hooks.ozecaoo.com")
        domains = get(f"/accounts/{aid}/workers/domains")
        routes = get(f"/zones/{zid}/workers/routes")
        scripts = get(f"/accounts/{aid}/workers/scripts")
        subscriptions = get(f"/accounts/{aid}/subscriptions")
        output = {
            "account_id": aid, "zone_id": zid, "zone": zone["name"], "zone_status": zone["status"],
            "hooks_dns": [{"type": item.get("type"), "name": item.get("name")} for item in dns]
                if isinstance(dns, list) else dns,
            "hooks_workers_domain": [item for item in domains if item.get("hostname") == "hooks.ozecaoo.com"]
                if isinstance(domains, list) else domains,
            "zone_routes": [{"pattern": item.get("pattern"), "script": item.get("script")} for item in routes]
                if isinstance(routes, list) else routes,
            "worker_already_exists": any(item.get("id") == "ozecaoo-youtube-webhook" for item in scripts)
                if isinstance(scripts, list) else scripts,
            "subscriptions": [{"product": item.get("rate_plan", {}).get("public_name"),
                               "id": item.get("rate_plan", {}).get("id")} for item in subscriptions]
                if isinstance(subscriptions, list) else subscriptions,
        }
        (ROOT / ".preflight.local.json").write_text(json.dumps(output, indent=2))
        print(json.dumps(output, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Falha de preflight; nenhuma credencial ou resposta bruta foi registrada.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
