"""Public YouTube metadata, with bounded discovery and no credential-bearing URLs.

Only the standard library is used. The caller supplies an API key from its
environment; this module neither persists it nor prints requests or responses.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request


API_ROOT = "https://www.googleapis.com/youtube/v3/"
TIMEOUT = 15
MAX_RESPONSE_BYTES = 2_000_000
MAX_PLAYLIST_PAGES = 10
PAGE_SIZE = 50
ATTEMPTS = 3
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?\Z")
_REASONS = {
    "api_key_missing", "api_key_invalid", "invalid_arguments", "bad_request",
    "unauthorized", "forbidden", "quota_exceeded", "rate_limited",
    "channel_not_found", "playlist_not_found", "not_found", "server_error",
    "request_timeout", "network_error", "invalid_json", "response_too_large",
    "response_incomplete", "channel_mismatch", "playlist_empty",
    "pagination_inconsistent", "discovery_truncated", "insufficient_videos", "redirect_blocked",
}


class APIError(Exception):
    """A bounded, safe reason code; never carries a URL, key or response body."""

    def __init__(self, reason: str, retryable: bool = False):
        self.reason = reason if reason in _REASONS else "response_incomplete"
        self.code = self.reason
        self.reason_code = self.reason
        self.retryable = bool(retryable)
        self.can_validate_fallback = self.reason not in {
            "api_key_missing", "api_key_invalid", "unauthorized", "forbidden", "quota_exceeded",
        }
        super().__init__(f"YouTube Data API: {self.reason}")


class _NoAuthenticatedRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect must never forward the credential-bearing request headers."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            fp.close()
        except OSError:
            pass
        raise APIError("redirect_blocked")


# This transport is private to the API client; Atom/public recovery keep their
# existing unauthenticated urllib behavior. Tests replace this callable only.
_urlopen = urllib.request.build_opener(_NoAuthenticatedRedirect()).open


def _http_failure(exc: urllib.error.HTTPError, endpoint: str) -> APIError:
    reasons = set()
    try:
        payload = json.loads(exc.read(65_537))
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            for detail in error.get("errors", []) + error.get("details", []):
                if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
                    reasons.add(detail["reason"])
    except (OSError, ValueError, TypeError):
        pass
    finally:
        # Closing also prevents cleanup warnings from rendering a server-supplied
        # HTTPError reason (which is never safe to include in workflow logs).
        exc.close()
    if reasons & {"keyInvalid", "API_KEY_INVALID", "API_KEY_EXPIRED"}:
        return APIError("api_key_invalid")
    if reasons & {"quotaExceeded", "dailyLimitExceeded", "dailyLimitExceededUnreg"}:
        return APIError("quota_exceeded")
    if reasons & {"rateLimitExceeded", "userRateLimitExceeded", "backendError"}:
        return APIError("rate_limited", True)
    if exc.code == 400:
        return APIError("bad_request")
    if exc.code == 401:
        return APIError("unauthorized")
    if exc.code == 403:
        return APIError("forbidden")
    if exc.code == 404:
        reason = {"channels": "channel_not_found", "playlistItems": "playlist_not_found"}.get(endpoint, "not_found")
        return APIError(reason)
    if exc.code == 408:
        return APIError("request_timeout", True)
    if exc.code == 429:
        return APIError("rate_limited", True)
    if 500 <= exc.code <= 599:
        return APIError("server_error", True)
    return APIError("bad_request")


def _request_json(api_key: str, endpoint: str, params: dict) -> dict:
    request = urllib.request.Request(
        API_ROOT + endpoint + "?" + urllib.parse.urlencode(params),
        headers={"X-Goog-Api-Key": api_key, "Accept": "application/json",
                 "User-Agent": "ZECAO-site-youtube/1.0"},
    )
    for attempt in range(ATTEMPTS):
        try:
            with _urlopen(request, timeout=TIMEOUT) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise APIError("response_too_large")
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                raise APIError("invalid_json") from None
            if not isinstance(payload, dict) or "error" in payload:
                raise APIError("response_incomplete")
            return payload
        except urllib.error.HTTPError as exc:
            failure = _http_failure(exc, endpoint)
        except (TimeoutError, OSError):
            failure = APIError("network_error", True)
        except APIError as exc:
            failure = exc
        if not failure.retryable or attempt == ATTEMPTS - 1:
            raise failure from None
        time.sleep(attempt + 1)
    raise APIError("network_error")  # Defensive: ATTEMPTS is a positive constant.


def _items(payload: dict) -> list:
    items = payload.get("items")
    if not isinstance(items, list):
        raise APIError("response_incomplete")
    return items


def _timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise APIError("response_incomplete")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, OverflowError):
        raise APIError("response_incomplete") from None


def _clock(now: dt.datetime | None) -> dt.datetime:
    value = now if now is not None else dt.datetime.now(dt.timezone.utc)
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise APIError("invalid_arguments")
    return value.astimezone(dt.timezone.utc)


def _arguments(api_key, channel_id, excluded_id):
    if not isinstance(api_key, str) or not api_key.strip():
        raise APIError("api_key_missing")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", api_key):
        raise APIError("api_key_invalid")
    if (not isinstance(channel_id, str) or not CHANNEL_ID.fullmatch(channel_id)
            or not isinstance(excluded_id, str) or excluded_id and not VIDEO_ID.fullmatch(excluded_id)):
        raise APIError("invalid_arguments")


def _ids(values) -> list[str]:
    if not isinstance(values, (tuple, list)) or len(values) > MAX_PLAYLIST_PAGES * PAGE_SIZE + PAGE_SIZE:
        raise APIError("invalid_arguments")
    if any(not isinstance(value, str) or not VIDEO_ID.fullmatch(value) for value in values):
        raise APIError("invalid_arguments")
    return list(dict.fromkeys(values))


def _eligible_video(item: dict, channel_id: str, now: dt.datetime) -> dict | None:
    status = item.get("status")
    if not isinstance(status, dict) or status.get("privacyStatus") not in ("public", "private", "unlisted"):
        raise APIError("response_incomplete")
    if status["privacyStatus"] != "public":
        return None
    upload_status = status.get("uploadStatus")
    if upload_status not in ("processed", "uploaded", "deleted", "failed", "rejected"):
        raise APIError("response_incomplete")
    if upload_status != "processed":
        return None
    snippet = item.get("snippet")
    details = item.get("contentDetails")
    if not isinstance(snippet, dict) or not isinstance(details, dict):
        raise APIError("response_incomplete")
    if snippet.get("channelId") != channel_id:
        raise APIError("channel_mismatch")
    title = snippet.get("title")
    live_state = snippet.get("liveBroadcastContent")
    if not isinstance(title, str) or not title.strip() or live_state not in ("none", "live", "upcoming"):
        raise APIError("response_incomplete")
    if live_state != "none":
        return None
    published = _timestamp(snippet.get("publishedAt"))
    if published > now:
        return None
    duration = details.get("duration")
    match = _DURATION.fullmatch(duration) if isinstance(duration, str) else None
    if match is None or not any(value is not None for value in match.groups()):
        raise APIError("response_incomplete")
    if not any(float(value) > 0 for value in match.groups() if value is not None):
        return None
    region = details.get("regionRestriction")
    if isinstance(region, dict) and region.get("allowed") == []:
        return None
    recency = published
    kind = "NO YOUTUBE"
    if "liveStreamingDetails" in item:
        live = item["liveStreamingDetails"]
        if not isinstance(live, dict):
            raise APIError("response_incomplete")
        if not live.get("actualEndTime"):
            return None
        started = _timestamp(live.get("actualStartTime"))
        ended = _timestamp(live["actualEndTime"])
        if started > ended:
            raise APIError("response_incomplete")
        if ended > now:
            return None
        recency = max(published, ended)
        kind = "LIVE GRAVADA"
    return {"id": item["id"], "title": title.strip(), "published": snippet["publishedAt"],
            "sort_at": recency.isoformat(timespec="seconds"), "type": kind,
            "thumbnail": f"https://i.ytimg.com/vi/{item['id']}/maxresdefault.jpg"}


def fetch_video_details(api_key, channel_id, ids, excluded_id="", now=None) -> list[dict]:
    """Validate candidate IDs and return eligible videos; fewer items are allowed.

    A missing videos.list item is unavailable to this public API request. A
    returned item with missing required metadata is an inconsistent response.
    """
    _arguments(api_key, channel_id, excluded_id)
    clock = _clock(now)
    candidates = [value for value in _ids(ids) if value != excluded_id]
    result = []
    for offset in range(0, len(candidates), PAGE_SIZE):
        batch = candidates[offset:offset + PAGE_SIZE]
        payload = _request_json(api_key, "videos", {
            "part": "snippet,status,contentDetails,liveStreamingDetails", "id": ",".join(batch),
        })
        seen = set()
        for item in _items(payload):
            if (not isinstance(item, dict) or item.get("id") not in batch
                    or not VIDEO_ID.fullmatch(item["id"])):
                raise APIError("response_incomplete")
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            video = _eligible_video(item, channel_id, clock)
            if video is not None:
                result.append(video)
    return sorted(result, key=lambda video: (_timestamp(video["sort_at"]), video["id"]), reverse=True)


def fetch_recent_videos(api_key, channel_id, limit=2, excluded_id="", known_ids=(), now=None) -> list[dict]:
    """Discover every upload within a finite cap, then sort verified public media.

    Live replays sort by max(publication, actual end); the original publication
    remains in ``published``. An unfinished crawl cannot claim to find the latest.
    """
    _arguments(api_key, channel_id, excluded_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise APIError("invalid_arguments")
    clock = _clock(now)
    known = _ids(known_ids)
    if len(known) > PAGE_SIZE:
        raise APIError("invalid_arguments")
    channels = _items(_request_json(api_key, "channels", {"part": "contentDetails", "id": channel_id}))
    if not channels:
        raise APIError("channel_not_found")
    if len(channels) != 1 or not isinstance(channels[0], dict) or channels[0].get("id") != channel_id:
        raise APIError("channel_mismatch")
    try:
        playlist = channels[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except (KeyError, TypeError):
        raise APIError("response_incomplete") from None
    if not isinstance(playlist, str) or not PLAYLIST_ID.fullmatch(playlist):
        raise APIError("response_incomplete")
    candidates, seen_tokens = [], set()
    token = ""
    for _ in range(MAX_PLAYLIST_PAGES):
        params = {"part": "contentDetails,snippet", "playlistId": playlist, "maxResults": PAGE_SIZE}
        if token:
            params["pageToken"] = token
        payload = _request_json(api_key, "playlistItems", params)
        items = _items(payload)
        if not items:
            raise APIError("playlist_empty" if not candidates else "response_incomplete")
        if len(items) > PAGE_SIZE:
            raise APIError("response_incomplete")
        for item in items:
            try:
                video_id = item["contentDetails"]["videoId"]
            except (KeyError, TypeError):
                raise APIError("response_incomplete") from None
            if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
                raise APIError("response_incomplete")
            if video_id != excluded_id and video_id not in candidates:
                candidates.append(video_id)
        token = payload.get("nextPageToken", "")
        if not isinstance(token, str) or len(token) > 1_000:
            raise APIError("pagination_inconsistent")
        if not token:
            break
        if token in seen_tokens:
            raise APIError("pagination_inconsistent")
        seen_tokens.add(token)
    else:
        raise APIError("discovery_truncated")
    if not candidates:
        raise APIError("insufficient_videos")
    candidates.extend(value for value in known if value not in candidates and value != excluded_id)
    videos = fetch_video_details(api_key, channel_id, candidates, excluded_id, clock)
    if len(videos) < limit:
        raise APIError("insufficient_videos")
    return videos[:limit]
