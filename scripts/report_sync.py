#!/usr/bin/env python3
"""Resume a sincronização do YouTube e o resultado independente da publicação."""
import argparse
import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import sys


STATES = {"UPDATED", "PRESERVED", "FAILED"}
SOURCES = {"youtube_data_api_v3", "youtube_atom", "published_cache"}
RESULTS = {"success", "failure", "skipped", "cancelled", "unknown"}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
MAX_REPORT_BYTES = 256_000


def safe_text(value, limit=320):
    """Mantém conteúdo externo em uma única linha, sem HTML ou Markdown ativo."""
    if not isinstance(value, str):
        return "desconhecido"
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value)
    value = " ".join(value.split())
    # O produtor já fornece erros seguros; esta defesa evita URLs com credenciais.
    value = re.sub(r"(?i)([?&](?:key|api_key|access_token)=)[^&#\s]+", r"\1[REDACTED]", value)
    if len(value) > limit:
        value = value[:limit] + "…"
    value = html.escape(value, quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~>\-])", r"\\\1", value) or "desconhecido"


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def display_time(value):
    parsed = timestamp(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC") if parsed else "desconhecido"


def preserved_age(report):
    if report.get("state") != "PRESERVED":
        return "não se aplica"
    success = timestamp(report.get("last_success_at"))
    attempted = timestamp(report.get("attempted_at"))
    if success is None:
        return "desconhecida — feed sem data de sincronização registrada"
    if attempted is None or attempted < success:
        return "desconhecida — datas ausentes ou inconsistentes"
    seconds = int((attempted - success).total_seconds())
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{days}d {hours}h {minutes}min {seconds}s"


def failed_report(reason):
    return {"state": "FAILED", "source": None, "channel_id": None, "videos": [],
            "attempted_at": None, "last_success_at": None, "fallback_reasons": [reason]}


def load_report(path):
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_REPORT_BYTES + 1)
        if len(data) > MAX_REPORT_BYTES:
            raise ValueError("relatório excede o tamanho permitido")
        report = json.loads(data)
        if not isinstance(report, dict) or report.get("state") not in STATES:
            raise ValueError("estado inválido")
        source = report.get("source")
        if source is not None and source not in SOURCES:
            raise ValueError("fonte inválida")
        videos = report.get("videos")
        if not isinstance(videos, list) or len(videos) > 8:
            raise ValueError("lista de vídeos inválida")
        if any(not isinstance(video, dict) or not isinstance(video.get("id"), str)
               or not VIDEO_ID.fullmatch(video["id"]) or not isinstance(video.get("title"), str)
               for video in videos):
            raise ValueError("vídeo inválido")
        if report["state"] == "UPDATED" and (source not in {"youtube_data_api_v3", "youtube_atom"} or not videos):
            raise ValueError("atualização sem fonte ou vídeos válidos")
        if report["state"] == "PRESERVED" and (source != "published_cache" or not videos):
            raise ValueError("preservação sem cache ou vídeos válidos")
        return report
    except OSError:
        return failed_report("Relatório de sincronização indisponível; consulte os logs da etapa de consulta.")
    except (ValueError, TypeError):
        return failed_report("Relatório de sincronização inválido; consulte os logs da etapa de consulta.")


def render_summary(report, build_result="unknown", deploy_result="unknown"):
    state = report["state"]
    source = report.get("source") or "desconhecida"
    videos = report.get("videos", [])
    descriptions = {
        "UPDATED": "Consulta ao YouTube concluída; o feed foi atualizado ou confirmado como atual.",
        "PRESERVED": "O YouTube não foi atualizado; os vídeos já publicados foram preservados.",
        "FAILED": "Não foi possível obter ou preservar um feed válido.",
    }
    build_result = build_result if build_result in RESULTS else "unknown"
    deploy_result = deploy_result if deploy_result in RESULTS else "unknown"
    lines = [
        "## Sincronização do YouTube", "", f"**Estado: {state}**", "", descriptions[state], "",
        f"- Fonte utilizada: {safe_text(source)}",
        f"- Canal consultado: {safe_text(report.get('channel_id'))}",
        f"- Vídeos selecionados: {len(videos)}",
        f"- Horário da tentativa: {display_time(report.get('attempted_at'))}",
        f"- Última consulta bem-sucedida: {display_time(report.get('last_success_at'))}",
        f"- Idade dos dados preservados: {preserved_age(report)}", "",
        "### Vídeos", "",
    ]
    trigger = report.get("trigger")
    if isinstance(trigger, dict):
        context = [f"- Acionamento: {safe_text(trigger.get('source'))}",
                   f"- Evento GitHub: {safe_text(trigger.get('event'))}"]
        if trigger.get("batch_id"):
            context.append(f"- Lote do Worker: {safe_text(trigger['batch_id'], 80)}")
        ids = trigger.get("video_ids")
        if isinstance(ids, list) and ids:
            context.append("- IDs anunciados (contexto, sem alterar seleção): " + ", ".join(safe_text(value, 11) for value in ids[:50]))
        lines[6:6] = context + [""]
    lines.extend(f"- {safe_text(video['id'], 11)} — {safe_text(video['title'])}" for video in videos)
    if not videos:
        lines.append("Nenhum vídeo selecionado.")
    lines.extend(["", "### Motivos de fallback", ""])
    reasons = report.get("fallback_reasons")
    if isinstance(reasons, list) and reasons:
        lines.extend(f"- {safe_text(reason, 512)}" for reason in reasons[:10])
        if len(reasons) > 10:
            lines.append("- Demais motivos omitidos; consulte os logs da consulta.")
    else:
        lines.append("Nenhum fallback utilizado.")
    lines.extend(["", "### Build e publicação", "",
                  f"- Geração do site: **{build_result}**",
                  f"- Publicação no GitHub Pages: **{deploy_result}**", "",
                  "**Sucesso da publicação não confirma a sincronização do YouTube.**", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="Relatório interno criado pelo atualizador")
    parser.add_argument("--build-result", choices=sorted(RESULTS), default="unknown")
    parser.add_argument("--deploy-result", choices=sorted(RESULTS), default="unknown")
    args = parser.parse_args(argv)
    summary = render_summary(load_report(args.report), args.build_result, args.deploy_result)
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        try:
            with Path(destination).open("a", encoding="utf-8") as handle:
                handle.write(summary)
        except OSError:
            print("Não foi possível gravar o resumo do GitHub Actions.", file=sys.stderr)
            return 1
    else:
        print(summary, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
