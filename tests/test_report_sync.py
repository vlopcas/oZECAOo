"""Verifica o resumo operacional sem acessar rede ou credenciais."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import report_sync as reporter


def report(state="UPDATED", source="youtube_data_api_v3", **changes):
    return {"state": state, "source": source, "channel_id": "UC" + "a" * 22,
            "videos": [{"id": "ID000000001", "title": "Último vídeo"}],
            "attempted_at": "2026-11-04T16:00:00Z", "last_success_at": "2026-11-04T16:00:00Z",
            "fallback_reasons": [], **changes}


class ReportSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "sync.json"

    def write_report(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_summary_distinguishes_websub_controlled_test_and_manual_sources(self):
        for source in ("schedule", "manual", "websub", "websub_test"):
            with self.subTest(source=source):
                value = report(trigger={"source": source, "event": "workflow_dispatch", "batch_id": "batch-123",
                                        "video_ids": ["ID000000001"]})
                summary = reporter.render_summary(value)
                self.assertIn("Acionamento: " + reporter.safe_text(source), summary)
                self.assertIn("Lote do Worker: batch\\-123", summary)
                self.assertIn("IDs anunciados (contexto, sem alterar seleção)", summary)

    def test_updated_summary_reports_source_videos_and_independent_deployment_failure(self):
        self.write_report(report())
        summary = reporter.render_summary(reporter.load_report(self.path), "success", "failure")
        self.assertIn("**Estado: UPDATED**", summary)
        self.assertIn("youtube\\_data\\_api\\_v3", summary)
        self.assertIn("Vídeos selecionados: 1", summary)
        self.assertIn("ID000000001", summary)
        self.assertIn("Último vídeo", summary)
        self.assertIn("2026-11-04 16:00:00 UTC", summary)
        self.assertIn("Geração do site: **success**", summary)
        self.assertIn("Publicação no GitHub Pages: **failure**", summary)

    def test_successful_deployment_does_not_turn_preservation_into_updated(self):
        self.write_report(report("PRESERVED", "published_cache", last_success_at="2026-11-02T13:30:00Z",
                                 fallback_reasons=["API: quotaExceeded", "Atom: HTTP 404"]))
        summary = reporter.render_summary(reporter.load_report(self.path), "success", "success")
        self.assertIn("**Estado: PRESERVED**", summary)
        self.assertNotIn("**Estado: UPDATED**", summary)
        self.assertIn("2d 2h 30min 0s", summary)
        self.assertIn("O YouTube não foi atualizado", summary)
        self.assertIn("quotaExceeded", summary)
        self.assertIn("HTTP 404", summary)
        self.assertIn("Publicação no GitHub Pages: **success**", summary)

    def test_legacy_preservation_has_unknown_age_and_no_invented_last_success(self):
        self.write_report(report("PRESERVED", "published_cache", last_success_at=None))
        summary = reporter.render_summary(reporter.load_report(self.path))
        self.assertIn("Última consulta bem-sucedida: desconhecido", summary)
        self.assertIn("Idade dos dados preservados: desconhecida", summary)
        self.assertIn("feed sem data de sincronização registrada", summary)

    def test_age_uses_utc_and_rejects_future_or_naive_success_dates(self):
        preserved = report("PRESERVED", "published_cache", attempted_at="2026-11-04T13:00:00-03:00",
                           last_success_at="2026-11-04T14:00:00Z")
        self.assertEqual(reporter.preserved_age(preserved), "0d 2h 0min 0s")
        preserved["last_success_at"] = "2026-11-05T14:00:00Z"
        self.assertIn("inconsistentes", reporter.preserved_age(preserved))
        preserved["last_success_at"] = "2026-11-04T14:00:00"
        self.assertIn("desconhecida", reporter.preserved_age(preserved))

    def test_titles_and_fallback_messages_cannot_insert_html_markdown_or_extra_rows(self):
        title = '<script>alert("x")</script> [click](https://example.test) `code`\n## fake | row'
        self.write_report(report(videos=[{"id": "ID000000001", "title": title}],
                                 fallback_reasons=['<img src=x>\n- **fake** | row']))
        summary = reporter.render_summary(reporter.load_report(self.path))
        self.assertNotIn("<script>", summary)
        self.assertNotIn("<img", summary)
        self.assertIn("&lt;script&gt;", summary)
        self.assertIn(r"\[click\]\(https://example\.test\)", summary)
        self.assertIn(r"\`code\`", summary)
        self.assertIn(r"\| row", summary)
        self.assertNotIn("\n## fake", summary)
        self.assertNotIn("\n- **fake**", summary)

    def test_missing_or_invalid_report_records_failed_even_if_deploy_succeeded(self):
        payloads = [None, b"not JSON", b"[]", b'{"state":"UPDATED","videos":[]}',
                    json.dumps(report("UPDATED", "published_cache")).encode(),
                    json.dumps(report(videos=[{"id": "invalid", "title": "Bad"}])).encode(),
                    b" " * (reporter.MAX_REPORT_BYTES + 1)]
        for payload in payloads:
            with self.subTest(payload_type=type(payload).__name__):
                self.path.unlink(missing_ok=True)
                if payload is not None:
                    self.path.write_bytes(payload)
                normalized = reporter.load_report(self.path)
                summary = reporter.render_summary(normalized, "success", "success")
                self.assertIn("**Estado: FAILED**", summary)
                self.assertNotIn("**Estado: UPDATED**", summary)
                self.assertIn("Horário da tentativa: desconhecido", summary)
                self.assertIn("Publicação no GitHub Pages: **success**", summary)

    def test_failed_sync_can_be_reported_with_skipped_build_and_deploy(self):
        self.write_report(report("FAILED", None, videos=[], last_success_at=None,
                                 fallback_reasons=["Todas as fontes falharam"]))
        summary = reporter.render_summary(reporter.load_report(self.path), "skipped", "skipped")
        self.assertIn("**Estado: FAILED**", summary)
        self.assertIn("Vídeos selecionados: 0", summary)
        self.assertIn("Geração do site: **skipped**", summary)
        self.assertIn("Publicação no GitHub Pages: **skipped**", summary)
        self.assertIn("Todas as fontes falharam", summary)

    def test_long_fields_are_bounded_and_credential_query_values_are_redacted(self):
        self.write_report(report(videos=[{"id": "ID000000001", "title": "x" * 50_000}],
                                 fallback_reasons=["https://example.test/?key=secret-value&x=1"] * 50))
        summary = reporter.render_summary(reporter.load_report(self.path))
        self.assertNotIn("secret-value", summary)
        self.assertIn("REDACTED", summary)
        self.assertIn("Demais motivos omitidos", summary)
        self.assertLess(len(summary), 8000)

    def test_cli_appends_step_summary_and_keeps_existing_content(self):
        self.write_report(report("PRESERVED", "published_cache", last_success_at=None))
        destination = self.root / "summary.md"
        destination.write_text("Resumo anterior\n\n", encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(destination)}), \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = reporter.main(["--report", str(self.path), "--build-result", "success",
                                  "--deploy-result", "success"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        saved = destination.read_text()
        self.assertTrue(saved.startswith("Resumo anterior\n\n"))
        self.assertIn("**Estado: PRESERVED**", saved)

    def test_cli_prints_summary_without_actions_and_handles_missing_report(self):
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}), \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = reporter.main(["--report", str(self.path), "--build-result", "failure",
                                  "--deploy-result", "skipped"])
        self.assertEqual(code, 0)
        self.assertIn("**Estado: FAILED**", stdout.getvalue())
        self.assertIn("Geração do site: **failure**", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
