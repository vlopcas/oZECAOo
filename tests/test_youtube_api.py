"""Deterministic public API discovery, availability and credential-safety checks."""
import datetime as dt
import email.message
import io
import json
import traceback
import unittest
import urllib.error
import urllib.response
from unittest.mock import patch

from scripts import youtube_api as api


CHANNEL = "UC" + "a" * 22
OTHER_CHANNEL = "UC" + "b" * 22
KEY = "dummy-api-key-never-persist"
TRAILER = "ItDJkbW2M4A"
NOW = dt.datetime(2026, 11, 4, 12, tzinfo=dt.timezone.utc)


def video(number=1, published="2026-11-02T12:00:00Z", **changes):
    item = {
        "id": f"ID{number:09}",
        "snippet": {"channelId": CHANNEL, "title": f"Vídeo {number}",
                    "publishedAt": published, "liveBroadcastContent": "none"},
        "status": {"privacyStatus": "public", "uploadStatus": "processed", "embeddable": True},
        "contentDetails": {"duration": "PT10M"},
    }
    item.update(changes)
    return item


def channels():
    return {"items": [{"id": CHANNEL, "contentDetails": {"relatedPlaylists": {"uploads": "UUuploads"}}}]}


def playlist(ids, token=None):
    payload = {"items": [{"contentDetails": {"videoId": value}} for value in ids]}
    if token is not None:
        payload["nextPageToken"] = token
    return payload


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def http_error(status, reason="unknown", message="sensitive server message"):
    payload = {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}
    return urllib.error.HTTPError("https://example.invalid/?key=" + KEY, status, message,
                                  None, response(payload))


class YouTubeAPITests(unittest.TestCase):
    def setUp(self):
        sleep = patch.object(api.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def fetch(self, **kwargs):
        return api.fetch_recent_videos(KEY, CHANNEL, now=NOW, **kwargs)

    def successful_responses(self, videos):
        return [response(channels()), response(playlist([item["id"] for item in videos])),
                response({"items": videos})]

    def details(self, items, requested=None):
        ids = requested if requested is not None else [item["id"] for item in items]
        with patch.object(api, "_urlopen", return_value=response({"items": items})):
            return api.fetch_video_details(KEY, CHANNEL, ids, now=NOW)

    def test_public_api_success_uses_three_requests_and_key_header_only(self):
        videos = [video(1), video(2, "2026-11-03T12:00:00Z")]
        with patch.object(api, "_urlopen", side_effect=self.successful_responses(videos)) as opener:
            result = self.fetch()
        self.assertEqual([item["id"] for item in result], [videos[1]["id"], videos[0]["id"]])
        self.assertEqual(opener.call_count, 3)
        for call in opener.call_args_list:
            request = call.args[0]
            self.assertNotIn(KEY, request.full_url)
            self.assertEqual(request.get_header("X-goog-api-key"), KEY)
            self.assertEqual(call.kwargs["timeout"], 15)
            self.assertTrue(request.full_url.startswith(api.API_ROOT))
        self.sleep.assert_not_called()

    def test_channel_not_found_or_missing_uploads_does_not_query_playlist(self):
        for payload, reason in (({"items": []}, "channel_not_found"),
                                ({"items": [{"id": CHANNEL}]}, "response_incomplete"),
                                ({"items": [{"id": OTHER_CHANNEL}]}, "channel_mismatch")):
            with self.subTest(reason=reason), patch.object(api, "_urlopen", return_value=response(payload)) as opener:
                with self.assertRaises(api.APIError) as caught:
                    self.fetch()
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(opener.call_count, 1)

    def test_empty_playlist_cannot_confirm_known_cache_as_updated(self):
        with patch.object(api, "_urlopen", side_effect=[response(channels()), response(playlist([]))]):
            with self.assertRaisesRegex(api.APIError, "playlist_empty"):
                self.fetch(known_ids=(video()["id"],))

    def test_later_pages_are_considered_and_result_is_not_playlist_order(self):
        first, newest = video(1), video(2, "2026-11-04T01:00:00Z")
        responses = [response(channels()), response(playlist([first["id"]], "page-2")),
                     response(playlist([newest["id"]])), response({"items": [first, newest]})]
        with patch.object(api, "_urlopen", side_effect=responses) as opener:
            result = self.fetch()
        self.assertEqual(result[0]["id"], newest["id"])
        self.assertIn("pageToken=page-2", opener.call_args_list[2].args[0].full_url)

    def test_truncated_or_looping_discovery_is_rejected(self):
        for responses, reason in (
            ([response(channels()), response(playlist([video()["id"]], "more"))], "discovery_truncated"),
            ([response(channels()), response(playlist([video()["id"]], "same")),
              response(playlist([video(2)["id"]], "same"))], "pagination_inconsistent"),
        ):
            cap = 1 if reason == "discovery_truncated" else 10
            with self.subTest(reason=reason), patch.object(api, "MAX_PLAYLIST_PAGES", cap), \
                 patch.object(api, "_urlopen", side_effect=responses):
                with self.assertRaisesRegex(api.APIError, reason):
                    self.fetch()

    def test_duplicates_and_trailer_do_not_take_two_slots(self):
        videos = [video(1), video(2)]
        responses = [response(channels()), response(playlist([TRAILER, videos[0]["id"], videos[0]["id"], videos[1]["id"]])),
                     response({"items": videos + [videos[0]]})]
        with patch.object(api, "_urlopen", side_effect=responses) as opener:
            result = self.fetch(excluded_id=TRAILER)
        self.assertEqual(len(result), 2)
        self.assertNotIn(TRAILER, opener.call_args.args[0].full_url)
        self.assertEqual(opener.call_args.args[0].full_url.count(videos[0]["id"]), 1)

    def test_known_ids_missing_from_uploads_are_revalidated_and_sorted(self):
        first, second = video(1), video(2)
        known = video(3, "2026-11-04T01:00:00Z")
        responses = [response(channels()), response(playlist([first["id"], second["id"]])),
                     response({"items": [first, second, known]})]
        with patch.object(api, "_urlopen", side_effect=responses) as opener:
            result = self.fetch(known_ids=(known["id"],))
        self.assertEqual(result[0]["id"], known["id"])
        self.assertIn(known["id"], opener.call_args.args[0].full_url)

    def test_details_are_batched_at_fifty_ids(self):
        videos = [video(number) for number in range(51)]
        with patch.object(api, "_urlopen", side_effect=[response({"items": videos[:50]}), response({"items": videos[50:]})]) as opener:
            result = api.fetch_video_details(KEY, CHANNEL, [item["id"] for item in videos], now=NOW)
        self.assertEqual(len(result), 51)
        self.assertEqual(opener.call_count, 2)

    def test_private_unlisted_removed_and_unprocessed_videos_are_not_eligible(self):
        items = []
        for number, (privacy, upload) in enumerate((("private", "processed"), ("unlisted", "processed"),
                                                  ("public", "deleted"), ("public", "uploaded"),
                                                  ("public", "failed"), ("public", "rejected"))):
            item = video(number)
            item["status"].update(privacyStatus=privacy, uploadStatus=upload)
            items.append(item)
        self.assertEqual(self.details(items, [item["id"] for item in items] + [video(100)["id"]]), [])

    def test_live_and_scheduled_items_are_excluded_even_if_public_and_processed(self):
        items = [video(1), video(2), video(3)]
        items[0]["snippet"]["liveBroadcastContent"] = "live"
        items[1]["snippet"]["liveBroadcastContent"] = "upcoming"
        items[2]["liveStreamingDetails"] = {"scheduledStartTime": "2026-11-05T12:00:00Z"}
        self.assertEqual(self.details(items), [])

    def test_ended_live_is_ranked_by_end_without_changing_publication_date(self):
        replay = video(1, "2026-10-01T12:00:00Z")
        replay["liveStreamingDetails"] = {"actualStartTime": "2026-11-03T10:00:00Z",
                                         "actualEndTime": "2026-11-03T15:00:00Z"}
        regular = video(2, "2026-11-03T14:00:00Z")
        result = self.details([regular, replay])
        self.assertEqual([item["id"] for item in result], [replay["id"], regular["id"]])
        self.assertEqual(result[0]["published"], "2026-10-01T12:00:00Z")
        self.assertEqual(result[0]["sort_at"], "2026-11-03T15:00:00+00:00")
        self.assertEqual(result[0]["type"], "LIVE GRAVADA")

    def test_live_started_without_end_future_publication_and_zero_duration_are_excluded(self):
        running, future, processing = video(1), video(2, "2026-11-05T00:00:00Z"), video(3)
        running["liveStreamingDetails"] = {"actualStartTime": "2026-11-03T12:00:00Z"}
        processing["contentDetails"]["duration"] = "PT0S"
        self.assertEqual(self.details([running, future, processing]), [])

    def test_live_end_in_future_is_not_yet_eligible(self):
        item = video()
        item["liveStreamingDetails"] = {"actualStartTime": "2026-11-03T12:00:00Z",
                                       "actualEndTime": "2026-11-05T12:00:00Z"}
        self.assertEqual(self.details([item]), [])

    def test_embeddable_false_does_not_remove_public_youtube_link(self):
        item = video()
        item["status"]["embeddable"] = False
        self.assertEqual(self.details([item])[0]["id"], item["id"])

    def test_all_country_blocked_video_is_unavailable(self):
        item = video()
        item["contentDetails"]["regionRestriction"] = {"allowed": []}
        self.assertEqual(self.details([item]), [])

    def test_required_metadata_missing_or_invalid_is_not_silently_skipped(self):
        variants = []
        for field in ("snippet", "status", "contentDetails"):
            item = video()
            item.pop(field)
            variants.append(item)
        for field, value in (("title", " "), ("channelId", OTHER_CHANNEL),
                             ("publishedAt", "not-a-date"), ("publishedAt", "2026-11-02T10:00:00"),
                             ("liveBroadcastContent", "unknown")):
            item = video(); item["snippet"][field] = value; variants.append(item)
        item = video(); item["contentDetails"]["duration"] = "unknown"; variants.append(item)
        item = video(); item["liveStreamingDetails"] = {"actualStartTime": "2026-11-03T12:00:00Z", "actualEndTime": "2026-11-02T12:00:00Z"}; variants.append(item)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(api.APIError):
                self.details([item])

    def test_insufficient_eligible_results_are_not_declared_complete(self):
        with patch.object(api, "_urlopen", side_effect=self.successful_responses([video(1)])):
            with self.assertRaisesRegex(api.APIError, "insufficient_videos"):
                self.fetch()

    def test_missing_key_or_invalid_inputs_fail_without_network(self):
        cases = [("", CHANNEL, {}), (None, CHANNEL, {}), ("chave\nheader", CHANNEL, {}),
                 ("chave não ASCII", CHANNEL, {}), (KEY, "wrong", {}),
                 (KEY, CHANNEL, {"limit": 0}), (KEY, CHANNEL, {"limit": True}),
                 (KEY, CHANNEL, {"excluded_id": "invalid"}),
                 (KEY, CHANNEL, {"known_ids": ("invalid",)})]
        for key, channel, kwargs in cases:
            with self.subTest(key_present=bool(key), kwargs=kwargs), \
                 patch.object(api, "_urlopen") as opener, self.assertRaises(api.APIError):
                api.fetch_recent_videos(key, channel, now=NOW, **kwargs)
            opener.assert_not_called()

    def test_credential_and_permanent_http_errors_do_not_retry(self):
        for status, reason, expected in ((400, "keyInvalid", "api_key_invalid"),
                                         (401, "unknown", "unauthorized"),
                                         (403, "quotaExceeded", "quota_exceeded"),
                                         (403, "unknown", "forbidden"),
                                         (404, "unknown", "channel_not_found"),
                                         (400, "unknown", "bad_request")):
            with self.subTest(status=status, reason=reason), \
                 patch.object(api, "_urlopen", side_effect=http_error(status, reason)) as opener:
                with self.assertRaises(api.APIError) as caught:
                    self.fetch()
                self.assertEqual(caught.exception.reason, expected)
                if expected in ("api_key_invalid", "unauthorized", "quota_exceeded", "forbidden"):
                    self.assertFalse(caught.exception.can_validate_fallback)
                self.assertEqual(opener.call_count, 1)
        self.sleep.assert_not_called()

    def test_transient_http_and_network_failures_retry_and_can_recover(self):
        failures = [http_error(408), http_error(429), http_error(503),
                    http_error(403, "rateLimitExceeded"), http_error(403, "userRateLimitExceeded"),
                    TimeoutError("timeout " + KEY), urllib.error.URLError("DNS " + KEY)]
        for failure in failures:
            with self.subTest(kind=type(failure).__name__), \
                 patch.object(api, "_urlopen", side_effect=[failure] + self.successful_responses([video(1), video(2)])) as opener:
                result = self.fetch()
                self.assertEqual(len(result), 2)
                self.assertEqual(opener.call_count, 4)
        self.assertEqual(self.sleep.call_count, len(failures))

    def test_retries_are_bounded_and_error_never_carries_secret_or_server_body(self):
        for failure in (http_error(500, "backendError", KEY), urllib.error.URLError("URL?key=" + KEY)):
            with self.subTest(kind=type(failure).__name__), \
                 patch.object(api, "_urlopen", side_effect=failure) as opener:
                with self.assertRaises(api.APIError) as caught:
                    self.fetch()
                self.assertEqual(opener.call_count, 3)
                rendered = "".join(traceback.format_exception(caught.exception))
                self.assertNotIn(KEY, rendered)
                self.assertNotIn("https://", rendered)
                self.assertTrue(caught.exception.retryable)

    def test_authenticated_redirect_never_opens_second_url_or_forwards_header(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                observed = []

                class RedirectingTransport(api.urllib.request.HTTPSHandler):
                    def https_open(self, request):
                        observed.append(request)
                        headers = email.message.Message()
                        headers["Location"] = "https://untrusted.example/?key=" + KEY
                        result = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, status)
                        result.msg = "Found"
                        return result

                opener = api.urllib.request.build_opener(api._NoAuthenticatedRedirect(), RedirectingTransport())
                with patch.object(api, "_urlopen", opener.open), self.assertRaises(api.APIError) as caught:
                    self.fetch()
                self.assertEqual(caught.exception.reason, "redirect_blocked")
                self.assertFalse(caught.exception.retryable)
                self.assertEqual(len(observed), 1)
                self.assertTrue(observed[0].full_url.startswith(api.API_ROOT))
                self.assertNotIn("untrusted.example", str(caught.exception))
                self.assertNotIn(KEY, str(caught.exception))
        self.sleep.assert_not_called()

    def test_playlist_not_found_is_a_distinct_permanent_failure(self):
        with patch.object(api, "_urlopen", side_effect=[response(channels()), http_error(404)]) as opener:
            with self.assertRaisesRegex(api.APIError, "playlist_not_found"):
                self.fetch()
        self.assertEqual(opener.call_count, 2)
        self.sleep.assert_not_called()

    def test_invalid_json_incomplete_response_and_oversized_body_fail_safely(self):
        for body, expected in ((b"<html>" + KEY.encode(), "invalid_json"),
                               (b"[]", "response_incomplete"), (b"{}", "response_incomplete"),
                               (b" " * (api.MAX_RESPONSE_BYTES + 1), "response_too_large")):
            with self.subTest(expected=expected), patch.object(api, "_urlopen", return_value=io.BytesIO(body)) as opener:
                with self.assertRaisesRegex(api.APIError, expected) as caught:
                    self.fetch()
                self.assertNotIn(KEY, str(caught.exception))
                self.assertEqual(opener.call_count, 1)

    def test_unrequested_video_or_invalid_playlist_id_cannot_enter_result(self):
        with patch.object(api, "_urlopen", return_value=response({"items": [video(2)]})):
            with self.assertRaises(api.APIError):
                api.fetch_video_details(KEY, CHANNEL, [video(1)["id"]], now=NOW)
        for bad_id in ("invalid", "../unsafe", KEY):
            with self.subTest(bad_id=bad_id), patch.object(api, "_urlopen", side_effect=[response(channels()), response(playlist([bad_id]))]):
                with self.assertRaises(api.APIError):
                    self.fetch()


if __name__ == "__main__":
    unittest.main()
