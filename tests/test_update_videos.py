"""Exercita o feed sem acesso de rede e sem modificar reservas versionadas."""
import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from scripts import update_videos as updater


CHANNEL_ID = "UC" + "a" * 22
OTHER_CHANNEL_ID = "UC" + "b" * 22


def atom(entries):
    children = "".join(
        f"<entry><yt:videoId>{escape(video_id)}</yt:videoId>"
        f"<title>{escape(title)}</title><published>2026-10-10T19:00:00Z</published></entry>"
        for video_id, title in entries
    )
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
        f"{children}</feed>"
    ).encode()


def video(video_id="EpEauQtpjZ4", title="Jogando & conversando <ao vivo>"):
    return {"id": video_id, "title": title, "published": "2026-10-10T19:00:00Z", "type": "NO YOUTUBE"}


class ParseFeedTests(unittest.TestCase):
    def test_filters_invalid_ids_duplicates_and_applies_limit(self):
        entries = [("inválido", "Não deve entrar"), ("ID000000000", "Primeiro"),
                   ("ID000000000", "Duplicado")]
        entries.extend((f"ID{number:09}", f"Vídeo {number}") for number in range(1, 12))
        parsed = updater.parse_feed(atom(entries), limit=3)
        self.assertEqual([item["id"] for item in parsed], ["ID000000000", "ID000000001", "ID000000002"])
        self.assertTrue(all(item["thumbnail"].endswith("/maxresdefault.jpg") for item in parsed))
        self.assertEqual(len(updater.parse_feed(atom(entries))), 8)

    def test_retains_plain_text_special_characters(self):
        title = '<img src=x onerror="alert(1)"> & “Zecão”'
        self.assertEqual(updater.parse_feed(atom([("EpEauQtpjZ4", title)]))[0]["title"], title)

    def test_empty_title_gets_readable_fallback(self):
        self.assertEqual(updater.parse_feed(atom([("EpEauQtpjZ4", " ")]))[0]["title"], "Vídeo ZECAO")

    def test_invalid_limit_is_rejected(self):
        for limit in (0, -1, True, "8"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                updater.parse_feed(atom([]), limit=limit)


class UpdateVideosTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "dist" / "data" / "videos.json"
        self.versioned = self.root / "data" / "videos.json"
        self.config_path = self.root / "site.config.json"
        self.config = {"youtubeChannelId": CHANNEL_ID, "maxFeedVideos": 8,
                       "fallbackVideos": [video()]}
        self.save_config()
        self.mock_versioned = patch.object(updater, "VERSIONED_FEED", self.versioned)
        self.mock_versioned.start()
        self.addCleanup(self.mock_versioned.stop)

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def write_cache(self, path, videos=None, channel_id=CHANNEL_ID):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"source": "youtube_atom", "channel_id": channel_id,
                                   "videos": videos if videos is not None else [video()]}), encoding="utf-8")

    def run_update(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return updater.main(["--config", str(self.config_path), "--output", str(self.output), *extra])

    def test_network_request_uses_configured_channel_and_limit(self):
        self.config["youtubeChannelId"] = OTHER_CHANNEL_ID
        self.config["maxFeedVideos"] = 2
        self.save_config()
        body = atom([(f"ID{number:09}", f"Vídeo {number}") for number in range(4)])
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(body)) as urlopen:
            self.assertEqual(self.run_update(), 0)
        self.assertEqual(urlopen.call_args.args[0].full_url,
                         f"https://www.youtube.com/feeds/videos.xml?channel_id={OTHER_CHANNEL_ID}")
        generated = json.loads(self.output.read_text())
        self.assertEqual(generated["channel_id"], OTHER_CHANNEL_ID)
        self.assertEqual(len(generated["videos"]), 2)

    def test_fixture_writes_only_generated_output(self):
        self.write_cache(self.versioned)
        original = self.versioned.read_bytes()
        fixture = self.root / "fixture.xml"
        fixture.write_bytes(atom([("l3k6mkxiYPI", "Novo & <especial>")]))
        with patch.object(updater.urllib.request, "urlopen") as urlopen:
            self.assertEqual(self.run_update("--fixture", str(fixture)), 0)
            urlopen.assert_not_called()
        self.assertEqual(self.versioned.read_bytes(), original)
        self.assertEqual(json.loads(self.output.read_text())["videos"][0]["id"], "l3k6mkxiYPI")

    def test_default_destination_is_generated_dist(self):
        with patch.object(updater, "OUTPUT", self.output), \
             patch.object(updater.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(updater.main(["--config", str(self.config_path)]), 0)
        self.assertTrue(self.output.exists())
        self.assertFalse(self.versioned.exists())
        self.assertEqual(updater.ROOT / "dist/data/videos.json", updater.OUTPUT)

    def test_network_failure_preserves_valid_generated_cache_byte_for_byte(self):
        self.write_cache(self.output, [video("l3k6mkxiYPI", "Reserva gerada mais recente")])
        self.write_cache(self.versioned)
        original = self.output.read_bytes()
        with patch.object(updater.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
            self.assertEqual(self.run_update(), 0)
        self.assertEqual(self.output.read_bytes(), original)

    def test_invalid_generated_cache_recovers_from_versioned_reserve(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_text('{"videos": [', encoding="utf-8")
        self.write_cache(self.versioned, [video("l3k6mkxiYPI", "Reserva versionada")])
        original = self.versioned.read_bytes()
        with patch.object(updater.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
            self.assertEqual(self.run_update(), 0)
        self.assertEqual(json.loads(self.output.read_text())["videos"][0]["id"], "l3k6mkxiYPI")
        self.assertEqual(self.versioned.read_bytes(), original)

    def test_wrong_channel_and_invalid_ids_cannot_be_used_as_cache(self):
        self.write_cache(self.output, channel_id=OTHER_CHANNEL_ID)
        self.write_cache(self.versioned, [{"id": "invalid", "title": "Descartar"}])
        with patch.object(updater.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
            self.assertEqual(self.run_update(), 0)
        generated = json.loads(self.output.read_text())
        self.assertEqual(generated["source"], "configured_fallback")
        self.assertEqual(generated["channel_id"], CHANNEL_ID)
        self.assertEqual(generated["videos"][0]["id"], "EpEauQtpjZ4")
        self.assertNotIn("updated_at", generated)

    def test_empty_and_invalid_xml_use_valid_configured_fallback(self):
        for body in (b"<broken>", atom([]), atom([("invalid", "Inválido")])):
            with self.subTest(body=body):
                self.output.unlink(missing_ok=True)
                fixture = self.root / "fixture.xml"
                fixture.write_bytes(body)
                self.assertEqual(self.run_update("--fixture", str(fixture)), 0)
                self.assertEqual(json.loads(self.output.read_text())["source"], "configured_fallback")

    def test_fails_without_valid_feed_or_reserve_without_writing_bad_json(self):
        self.config["fallbackVideos"] = [{"id": "invalid", "title": "Inválido"}]
        self.save_config()
        with patch.object(updater.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
            self.assertEqual(self.run_update(), 1)
        self.assertFalse(self.output.exists())

    def test_config_error_does_not_query_network_or_modify_output(self):
        self.config["youtubeChannelId"] = "not-a-channel"
        self.save_config()
        self.write_cache(self.output)
        original = self.output.read_bytes()
        with patch.object(updater.urllib.request, "urlopen") as urlopen:
            self.assertEqual(self.run_update(), 1)
            urlopen.assert_not_called()
        self.assertEqual(self.output.read_bytes(), original)

    def test_config_cannot_exceed_eight_feed_items(self):
        self.config["maxFeedVideos"] = 9
        self.save_config()
        with patch.object(updater.urllib.request, "urlopen") as urlopen:
            self.assertEqual(self.run_update(), 1)
            urlopen.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
