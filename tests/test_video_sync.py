"""Orquestra API, Atom, preservação e build sem rede nem chave real."""
import contextlib
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

from scripts import build_site as builder
from scripts import update_videos as updater
from scripts import youtube_api as api


REPOSITORY = Path(__file__).resolve().parents[1]
CHANNEL = "UC" + "a" * 22
KEY = "fake-youtube-api-key-never-publish-123"
TRAILER = "ItDJkbW2M4A"
NOW = dt.datetime(2026, 11, 4, 16, tzinfo=dt.timezone.utc)
LAST_SUCCESS = "2026-11-03T13:00:00Z"


def video(number, title=None, published="2026-11-02T12:00:00Z"):
    return {"id": f"ID{number:09}", "title": title or f"Vídeo {number}",
            "published": published, "sort_at": published, "type": "NO YOUTUBE",
            "thumbnail": f"https://i.ytimg.com/vi/ID{number:09}/maxresdefault.jpg"}


def baseline(videos=None, legacy=False):
    payload = {"source": "youtube_data_api_v3", "channel_id": CHANNEL,
               "videos": videos if videos is not None else [video(2), video(1)]}
    if not legacy:
        payload["updated_at"] = LAST_SUCCESS
        payload["sync"] = {"state": "UPDATED", "last_success_at": LAST_SUCCESS,
                           "last_success_source": "youtube_data_api_v3"}
    return payload


class VideoSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config_path = self.root / "site.config.json"
        self.output = self.root / "dist" / "data" / "videos.json"
        self.report_path = self.root / "internal" / "sync.json"
        self.versioned = self.root / "data" / "videos.json"
        self.config = {"siteUrl": "https://ozecaoo.com/", "youtubeChannelId": CHANNEL,
                       "maxFeedVideos": 2, "maxVisibleVideos": 2, "trailerVideoId": TRAILER,
                       "videosFeedPath": "data/videos.json", "fallbackVideos": [video(0)]}
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.versioned.parent.mkdir()
        self.versioned.write_text(json.dumps(baseline([video(0, "Reserva antiga", "2020-01-01T00:00:00Z")], legacy=True)))
        self.environment = patch.dict(os.environ, {"YOUTUBE_API_KEY": KEY, "GITHUB_ACTIONS": "true"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.reserve_patch = patch.object(updater, "VERSIONED_FEED", self.versioned)
        self.reserve_patch.start()
        self.addCleanup(self.reserve_patch.stop)
        clock = Mock(wraps=dt.datetime)
        clock.now.return_value = NOW
        clock_patch = patch.object(updater, "dt", SimpleNamespace(datetime=clock, timezone=dt.timezone))
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        # Every network entry point is blocked unless a test supplies a response.
        self.recent_patch = patch.object(api, "fetch_recent_videos", side_effect=api.APIError("server_error", True))
        self.recent = self.recent_patch.start()
        self.addCleanup(self.recent_patch.stop)
        self.details_patch = patch.object(api, "fetch_video_details", side_effect=api.APIError("response_incomplete"))
        self.details = self.details_patch.start()
        self.addCleanup(self.details_patch.stop)
        atom_patch = patch.object(updater, "fetch_videos", side_effect=urllib.error.URLError("offline"))
        self.atom = atom_patch.start()
        self.addCleanup(atom_patch.stop)
        published_patch = patch.object(updater, "retrieve_published", return_value=baseline())
        self.published = published_patch.start()
        self.addCleanup(published_patch.stop)

    def run_sync(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = updater.main(["--config", str(self.config_path), "--output", str(self.output),
                                   "--report", str(self.report_path), "--preserve-published"])
        self.logs = stdout.getvalue() + stderr.getvalue()
        return result

    def feed(self):
        return json.loads(self.output.read_text())

    def report(self):
        return json.loads(self.report_path.read_text())

    def test_trigger_context_does_not_override_api_selection_or_enter_public_feed(self):
        self.recent.side_effect = None
        self.recent.return_value = [video(3), video(2)]
        with patch.dict(os.environ, {"SYNC_GITHUB_EVENT": "workflow_dispatch", "SYNC_TRIGGER_SOURCE": "websub",
                                     "SYNC_BATCH_ID": "batch-123", "SYNC_VIDEO_IDS": "ID000000099,invalid,ID000000099"}):
            self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.report()["trigger"], {"event": "workflow_dispatch", "source": "websub",
                                                  "batch_id": "batch-123", "video_ids": ["ID000000099"]})
        self.assertEqual([v["id"] for v in self.feed()["videos"]], ["ID000000003", "ID000000002"])
        self.assertNotIn("trigger", self.feed())

    def test_schedule_source_cannot_be_spoofed_by_dispatch_context(self):
        with patch.dict(os.environ, {"SYNC_GITHUB_EVENT": "schedule", "SYNC_TRIGGER_SOURCE": "websub",
                                     "SYNC_BATCH_ID": "<script>", "SYNC_VIDEO_IDS": ""}):
            self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.report()["trigger"]["source"], "schedule")
        self.assertIsNone(self.report()["trigger"]["batch_id"])

    def test_primary_api_updates_without_atom_and_receives_published_known_ids(self):
        selected = [video(3), video(2)]
        self.recent.side_effect = None
        self.recent.return_value = selected
        self.assertEqual(self.run_sync(), 0)
        self.atom.assert_not_called()
        self.details.assert_not_called()
        args, kwargs = self.recent.call_args
        self.assertEqual(args, (KEY, CHANNEL, 2, TRAILER))
        self.assertEqual(kwargs["known_ids"], [video(2)["id"], video(1)["id"]])
        self.assertEqual(kwargs["now"], NOW)
        self.assertEqual([v["id"] for v in self.feed()["videos"]], [v["id"] for v in selected])
        self.assertEqual(self.feed()["source"], "youtube_data_api_v3")
        self.assertEqual(self.feed()["sync"]["state"], "UPDATED")
        self.assertEqual(self.report()["state"], "UPDATED")
        self.assertEqual(self.report()["last_success_at"], NOW.isoformat(timespec="seconds"))

    def test_identical_current_selection_is_confirmed_updated_without_requiring_new_video(self):
        current = baseline()
        self.published.return_value = copy.deepcopy(current)
        self.recent.side_effect = None
        self.recent.return_value = copy.deepcopy(current["videos"])
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.report()["state"], "UPDATED")
        self.assertEqual(self.report()["source"], "youtube_data_api_v3")
        self.assertEqual([v["id"] for v in self.feed()["videos"]], [v["id"] for v in current["videos"]])
        self.assertNotEqual(self.report()["last_success_at"], LAST_SUCCESS)
        self.atom.assert_not_called()

    def test_api_discovery_failure_uses_atom_ids_but_confirms_recording_metadata(self):
        self.recent.side_effect = api.APIError("playlist_not_found")
        self.atom.side_effect = None
        self.atom.return_value = [video(3), video(2)]
        selected = [video(3, "Gravação pública"), video(2)]
        self.details.side_effect = None
        self.details.return_value = selected
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.report()["state"], "UPDATED")
        self.assertEqual(self.report()["source"], "youtube_atom")
        self.assertEqual(self.feed()["source"], "youtube_atom")
        args, kwargs = self.details.call_args
        self.assertEqual(args[:2], (KEY, CHANNEL))
        self.assertTrue({video(3)["id"], video(2)["id"], video(1)["id"]}.issubset(args[2]))
        self.assertEqual(kwargs["excluded_id"], TRAILER)
        self.assertEqual(kwargs["now"], NOW)
        self.assertIn("playlist_not_found", self.logs)

    def test_missing_invalid_or_quota_key_preserves_without_claiming_unverified_atom_updated(self):
        for reason in ("api_key_missing", "api_key_invalid", "quota_exceeded"):
            with self.subTest(reason=reason), patch.dict(os.environ, {"YOUTUBE_API_KEY": "" if reason == "api_key_missing" else KEY}):
                self.recent.side_effect = api.APIError(reason)
                self.atom.side_effect = None
                self.atom.return_value = [video(3), video(2)]
                self.details.reset_mock()
                self.assertEqual(self.run_sync(), 0)
                self.assertEqual(self.report()["state"], "PRESERVED")
                self.assertEqual(self.report()["source"], "published_cache")
                self.assertIn(reason, self.logs)
                self.details.assert_not_called()
                self.assertEqual([v["id"] for v in self.feed()["videos"]], [video(2)["id"], video(1)["id"]])

    def test_atom_cannot_claim_updated_when_recording_validation_fails(self):
        self.recent.side_effect = api.APIError("playlist_not_found")
        self.atom.side_effect = None
        self.atom.return_value = [video(3), video(2)]
        self.details.side_effect = api.APIError("response_incomplete")
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.report()["state"], "PRESERVED")
        self.assertEqual(self.report()["source"], "published_cache")
        self.assertIn("response_incomplete", self.logs)
        self.assertEqual(self.feed()["sync"]["last_success_at"], LAST_SUCCESS)

    def test_atom_404_invalid_xml_and_empty_feed_preserve_published_selection(self):
        errors = [urllib.error.HTTPError("https://www.youtube.com/feeds/videos.xml", 404, "Not Found", None, None),
                  ET.ParseError("invalid XML"), ValueError("feed sem vídeos válidos")]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.recent.side_effect = api.APIError("playlist_not_found")
                self.atom.side_effect = error
                self.assertEqual(self.run_sync(), 0)
                self.assertEqual(self.report()["state"], "PRESERVED")
                self.assertEqual([v["id"] for v in self.feed()["videos"]], [video(2)["id"], video(1)["id"]])
        self.details.assert_not_called()

    def test_known_last_success_is_preserved_and_legacy_age_is_not_invented(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.published.return_value = baseline(legacy=legacy)
                self.assertEqual(self.run_sync(), 0)
                expected = None if legacy else LAST_SUCCESS
                self.assertEqual(self.feed()["sync"]["last_success_at"], expected)
                self.assertEqual(self.report()["last_success_at"], expected)
                self.assertEqual(self.report()["attempted_at"], NOW.isoformat(timespec="seconds"))
                self.assertNotEqual(self.report()["attempted_at"], expected)
                if legacy:
                    self.assertNotIn("updated_at", self.feed())
                else:
                    self.assertEqual(self.feed()["updated_at"], LAST_SUCCESS)

    def test_published_baseline_wins_over_old_reserve_without_mutating_sources(self):
        reserve_bytes = self.versioned.read_bytes()
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual([v["id"] for v in self.feed()["videos"]], [video(2)["id"], video(1)["id"]])
        self.assertNotIn(video(0)["id"], [v["id"] for v in self.feed()["videos"]])
        self.assertEqual(self.versioned.read_bytes(), reserve_bytes)
        self.assertEqual(self.report()["state"], "PRESERVED")

    def test_unavailable_or_invalid_published_cache_fails_and_never_changes_output(self):
        errors = [urllib.error.URLError("site unavailable"), ValueError("invalid JSON"),
                  ValueError("cache pertence a outro canal")]
        for existing in (False, True):
            for error in errors:
                with self.subTest(existing=existing, error=str(error)):
                    self.output.unlink(missing_ok=True)
                    if existing:
                        self.output.parent.mkdir(parents=True, exist_ok=True)
                        self.output.write_bytes(b"previous bytes must remain intact")
                    original = self.output.read_bytes() if existing else None
                    self.published.side_effect = error
                    self.assertEqual(self.run_sync(), 1)
                    self.assertEqual(self.report()["state"], "FAILED")
                    self.assertIsNone(self.report()["source"])
                    self.assertEqual(self.report()["videos"], [])
                    self.assertIn("publicado", " ".join(self.report()["fallback_reasons"]))
                    if existing:
                        self.assertEqual(self.output.read_bytes(), original)
                    else:
                        self.assertFalse(self.output.exists())

    def test_credential_in_error_or_untrusted_cached_title_never_reaches_logs_or_output(self):
        failure = api.APIError("playlist_not_found")
        failure.args = (f"unsafe upstream URL https://example.invalid/?key={KEY}",)
        self.recent.side_effect = failure
        self.atom.side_effect = ValueError("untrusted error containing " + KEY)
        self.published.return_value = baseline([video(2, "Título " + KEY), video(1)])
        self.assertEqual(self.run_sync(), 0)
        for content in (self.logs, self.output.read_text(), self.report_path.read_text()):
            self.assertNotIn(KEY, content)
        self.assertIn("REDACTED", self.output.read_text())
        self.assertEqual(self.report()["state"], "PRESERVED")

    def test_mock_http_api_through_updater_and_build_keeps_two_safe_ssr_cards(self):
        # The real API parser, selector, updater and builder participate; only
        # HTTPS responses are mocked. Public source files are copied to tmp.
        self.recent_patch.stop()
        self.details_patch.stop()
        public_root = self.root / "site"
        public_root.mkdir()
        cfg = json.loads((REPOSITORY / "site.config.json").read_text())
        cfg.update(self.config)
        cfg["fallbackVideos"] = [video(0)]
        (public_root / "site.config.json").write_text(json.dumps(cfg))
        for name in (*builder.STATIC_FILES, "index.html"):
            shutil.copyfile(REPOSITORY / name, public_root / name)
        shutil.copytree(REPOSITORY / "assets", public_root / "assets")
        shutil.copytree(REPOSITORY / "data", public_root / "data")
        self.config_path = public_root / "site.config.json"
        self.output = public_root / "dist" / "data" / "videos.json"
        self.published.return_value = baseline([video(2), video(1)])

        def item(number, published, title, live=None):
            result = {"id": video(number)["id"], "snippet": {"channelId": CHANNEL,
                      "title": title, "publishedAt": published, "liveBroadcastContent": "none"},
                      "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                      "contentDetails": {"duration": "PT20M"}}
            if live:
                result["liveStreamingDetails"] = live
            return result

        raw = [item(1, "2026-11-01T12:00:00Z", "Vídeo anterior"),
               item(2, "2026-11-03T12:00:00Z", "Jogo <especial> & gameplay"),
               item(3, "2026-10-01T12:00:00Z", "Replay " + KEY,
                    {"actualStartTime": "2026-11-04T12:00:00Z", "actualEndTime": "2026-11-04T14:00:00Z"})]
        requested = []

        def http(request, **kwargs):
            requested.append(request)
            parsed = urllib.parse.urlsplit(request.full_url)
            self.assertNotIn(KEY, request.full_url)
            self.assertEqual(request.get_header("X-goog-api-key"), KEY)
            if parsed.path.endswith("/channels"):
                payload = {"items": [{"id": CHANNEL, "contentDetails": {"relatedPlaylists": {"uploads": "UUtest"}}}]}
            elif parsed.path.endswith("/playlistItems"):
                payload = {"items": [{"contentDetails": {"videoId": value}} for value in
                                     [TRAILER, video(1)["id"], video(2)["id"], video(3)["id"]]]}
            elif parsed.path.endswith("/videos"):
                payload = {"items": raw}
            else:
                self.fail("Unexpected endpoint")
            return io.BytesIO(json.dumps(payload).encode())

        with patch.object(api, "_urlopen", side_effect=http):
            self.assertEqual(self.run_sync(), 0)
        self.atom.assert_not_called()
        self.assertEqual(len(requested), 3)
        output = builder.build_site(public_root, feed_path=self.output)
        feed = json.loads((output / "data" / "videos.json").read_text())
        page = (output / "index.html").read_text()
        self.assertEqual([v["id"] for v in feed["videos"]], [video(3)["id"], video(2)["id"]])
        self.assertEqual(feed["videos"][0]["published"], "2026-10-01T12:00:00+00:00")
        self.assertEqual(feed["source"], "youtube_data_api_v3")
        self.assertEqual(feed["sync"]["state"], "UPDATED")
        self.assertEqual(page.count('class="video-tile"'), 2)
        self.assertIn("Jogo &lt;especial&gt; &amp; gameplay", page)
        self.assertNotIn(f'data-video-id="{TRAILER}"', page)
        self.assertNotIn("{{", page)
        for path in output.rglob("*"):
            if path.is_file():
                self.assertNotIn(KEY.encode(), path.read_bytes(), str(path.relative_to(output)))
        self.assertNotIn(KEY, self.logs)
        self.assertNotIn(KEY, self.report_path.read_text())


if __name__ == "__main__":
    unittest.main()
