#!/usr/bin/env python3
"""Recebe um PAT pelo terminal sem eco; nunca publica ou imprime credenciais."""
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / ".secrets.local.json"


def main():
    if not sys.stdin.isatty():
        print("Execute no seu terminal interativo; não envie credenciais pelo chat.")
        return 1
    previous = {}
    if DESTINATION.exists():
        previous = json.loads(DESTINATION.read_text())
    token = getpass.getpass("Fine-grained PAT do GitHub (entrada oculta): ").strip()
    if not re.fullmatch(r"github_pat_[A-Za-z0-9_]{20,500}", token):
        print("Formato inválido: use um fine-grained PAT, restrito ao repositório.")
        return 1
    values = {
        "GITHUB_TOKEN": token,
        "WEBSUB_SECRET": previous.get("WEBSUB_SECRET") or secrets.token_hex(32),
        "ADMIN_TOKEN": previous.get("ADMIN_TOKEN") or secrets.token_hex(32),
    }
    fd = os.open(DESTINATION, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(values, handle)
    print("Três secrets preparados no arquivo local ignorado, com permissão 0600.")
    print("Nenhum recurso remoto foi criado. O upload ocorrerá somente no deploy autorizado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
