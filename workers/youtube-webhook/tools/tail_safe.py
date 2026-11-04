#!/usr/bin/env python3
"""Mostra somente logs estruturados permitidos; descarta URL, headers e corpo do tail."""
import argparse
import json
from pathlib import Path
import subprocess
import threading

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {"event", "channel_id", "timestamp", "video_id", "video_ids", "batch_id", "trigger_source",
          "reason", "status", "accepted", "duplicates", "videos", "recovered", "mode", "lease_seconds",
          "dispatch_enabled", "permanent", "count", "detail"}


def filtered(item):
    result = []
    for entry in item.get("logs", []):
        for value in entry.get("message", []):
            try:
                value = json.loads(value) if isinstance(value, str) else value
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get("event"), str):
                result.append({key: field for key, field in value.items() if key in FIELDS
                               and (field is None or isinstance(field, (str, bool, int, float)))})
    return {"outcome": item.get("outcome"), "logs": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=120)
    args = parser.parse_args()
    secrets = json.loads((ROOT / ".secrets.local.json").read_text())
    process = subprocess.Popen(["npx", "wrangler", "tail", "--format", "json"], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    timer = threading.Timer(max(1, min(args.duration, 3600)), process.terminate)
    timer.start()
    buffer = ""
    decoder = json.JSONDecoder()
    try:
        for line in process.stdout:
            if not buffer and not line.lstrip().startswith("{"):
                continue
            buffer += line
            if len(buffer) > 512_000:
                buffer = ""
                continue
            try:
                item, offset = decoder.raw_decode(buffer.lstrip())
            except ValueError:
                continue
            buffer = buffer.lstrip()[offset:].lstrip()
            if not isinstance(item, dict):
                continue
            value = json.dumps(filtered(item), ensure_ascii=False)
            for secret in secrets.values():
                if isinstance(secret, str) and secret:
                    value = value.replace(secret, "[REDACTED]")
            print(value, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        process.terminate()
        process.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
