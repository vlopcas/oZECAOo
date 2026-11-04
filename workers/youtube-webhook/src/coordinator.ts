import { CHANNEL, REPOSITORY, TOPIC, log, type Challenge, type Env, type VideoEvent } from "./types";
import { requestSubscription } from "./websub";

const KEY = "coordinator-v1";
const DEBOUNCE = 30_000;
const COOLDOWN = 120_000;
const HOUR = 3_600_000;
const VERIFY_WINDOW = 600_000;
const ID = /^[A-Za-z0-9_-]{11}$/;
const ACTIVE_RUNS = new Set(["queued", "in_progress", "requested", "waiting", "pending"]);

type Source = "websub" | "websub_test";
interface Pending {
  fingerprint: string; source: Source; receivedAt: number;
  title?: string; published?: string; isNew?: boolean;
}
interface Subscription {
  enabled: boolean; active: boolean; expiresAt: number | null; renewAt: number | null;
  pending: { mode: "subscribe" | "unsubscribe"; token: string; deadline: number } | null;
  confirmed: { mode: "subscribe" | "unsubscribe"; token: string; deadline: number } | null;
  desiredMode: "subscribe" | "unsubscribe" | null;
  retryAt: number | null; failures: number; lastReason: string | null;
}
interface Batch {
  id: string; ids: string[]; fingerprints: Record<string, string>; source: Source;
  ambiguous: boolean; preparedAt: number; sentAt: number | null; filtered?: boolean;
}
interface PublishedVideo { id: string; title: string; published: number; sortAt: number }
interface Store {
  seen: Record<string, { fingerprint: string; at: number }>;
  pending: Record<string, Pending>; pendingSince: number | null;
  history: number[]; lastDispatchAt: number | null; batch: Batch | null;
  retryAt: number | null; failures: number; blockedReason: string | null;
  subscription: Subscription;
}

function initial(): Store {
  return { seen: {}, pending: {}, pendingSince: null, history: [], lastDispatchAt: null,
    batch: null, retryAt: null, failures: 0, blockedReason: null,
    subscription: { enabled: false, active: false, expiresAt: null, renewAt: null,
      pending: null, confirmed: null, desiredMode: null, retryAt: null, failures: 0, lastReason: null } };
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: { "Cache-Control": "no-store" } });
}

function dispatchDue(data: Store, now: number): number | null {
  const candidates = Object.entries(data.pending).filter(([, item]) =>
    item.source === "websub_test" || data.subscription.enabled);
  if (!candidates.length || data.blockedReason) return null;
  const firstReceived = Math.min(...candidates.map(([, item]) => item.receivedAt));
  let due = Math.max(firstReceived + DEBOUNCE, data.retryAt ?? 0,
    data.lastDispatchAt === null ? 0 : data.lastDispatchAt + COOLDOWN);
  const history = data.history.filter(time => time > now - HOUR);
  if (history.length >= 12) due = Math.max(due, history[0] + HOUR + 1);
  return due;
}

export class ChannelCoordinator {
  private running: Promise<void> | null = null;
  constructor(private readonly ctx: DurableObjectState, private readonly env: Env) {}

  private async read(): Promise<Store> {
    return await this.ctx.storage.get<Store>(KEY) ?? initial();
  }

  private async change<T>(fn: (data: Store) => T): Promise<T> {
    return this.ctx.storage.transaction(async txn => {
      const data = await txn.get<Store>(KEY) ?? initial();
      const result = fn(data);
      await txn.put(KEY, data);
      const now = Date.now();
      const sub = data.subscription;
      const due: number[] = [];
      if (sub.pending) due.push(sub.pending.deadline);
      else if (sub.retryAt !== null) due.push(sub.retryAt);
      else if (sub.enabled) due.push(sub.renewAt ?? now);
      const dispatch = this.env.DISPATCH_ENABLED === "true" ? dispatchDue(data, now) : null;
      if (dispatch !== null) due.push(dispatch);
      if (due.length) await txn.setAlarm(Math.max(now + 1, Math.min(...due)));
      else await txn.deleteAlarm();
      return result;
    });
  }

  async fetch(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    try {
      if (request.method === "GET" && path === "/status") return await this.status();
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      if (path === "/events") return await this.events(await request.json());
      if (path === "/challenge") return await this.challenge(await request.json() as Challenge);
      if (path === "/subscribe") {
        await this.prepareSubscription("subscribe", true);
        return json({ requested: true }, 202);
      }
      if (path === "/unsubscribe") {
        await this.prepareSubscription("unsubscribe", true);
        return json({ requested: true }, 202);
      }
      if (path === "/test-dispatch") return await this.testDispatch(await request.json());
      if (path === "/tick") {
        await this.change(data => { data.blockedReason = null; data.failures = 0; data.retryAt = null; });
        await this.process();
        return await this.status();
      }
      return json({ error: "not_found" }, 404);
    } catch (error) {
      if (error instanceof SyntaxError) return json({ error: "invalid_json" }, 400);
      log("coordinator_failure", { reason: "state_or_processing_failure" });
      return json({ error: "state_or_processing_failure" }, 503);
    }
  }

  private async status(): Promise<Response> {
    const data = await this.read();
    const sub = data.subscription;
    return json({ channel_id: this.env.CHANNEL_ID, dispatch_enabled: this.env.DISPATCH_ENABLED === "true",
      pending_videos: Object.keys(data.pending).length, seen_events: Object.keys(data.seen).length,
      batch_id: data.batch?.id ?? null, blocked_reason: data.blockedReason,
      last_dispatch_at: data.lastDispatchAt, next_retry_at: data.retryAt,
      subscription: { enabled: sub.enabled, active: sub.active, expires_at: sub.expiresAt,
        renew_at: sub.renewAt, pending_mode: sub.pending?.mode ?? null,
        verification_deadline: sub.pending?.deadline ?? null, last_reason: sub.lastReason } });
  }

  private async events(body: unknown): Promise<Response> {
    const events = (body as { events?: unknown })?.events;
    if (!Array.isArray(events) || events.length < 1 || events.length > 50) return json({ error: "invalid_events" }, 400);
    if (events.some((event: VideoEvent) => !event || !ID.test(event.videoId) || event.channelId !== CHANNEL
      || event.channelId !== this.env.CHANNEL_ID || typeof event.title !== "string"
      || typeof event.fingerprint !== "string" || event.fingerprint.length < 1 || event.fingerprint.length > 128)) {
      return json({ error: "invalid_events" }, 400);
    }
    const result = await this.change(data => {
      if (!data.subscription.enabled) return { accepted: 0, duplicates: 0, ignored: "subscription_disabled" };
      let accepted = 0, duplicates = 0;
      const knownIds = new Set(Object.keys(data.seen).map(key => key.slice(0, key.indexOf(":"))));
      for (const event of events as VideoEvent[]) {
        const seenKey = `${event.videoId}:${event.fingerprint}`;
        if (Object.hasOwn(data.seen, seenKey)) { duplicates++; continue; }
        if (!Object.hasOwn(data.pending, event.videoId) && Object.keys(data.pending).length >= 200) throw new Error("pending_capacity");
        const isNew = data.pending[event.videoId]?.isNew === true || !knownIds.has(event.videoId);
        data.seen[seenKey] = { fingerprint: event.fingerprint, at: Date.now() };
        data.pending[event.videoId] = { fingerprint: event.fingerprint, source: "websub", receivedAt: Date.now(),
          title: event.title, published: event.published, isNew };
        data.pendingSince ??= Date.now(); accepted++;
      }
      const seen = Object.entries(data.seen).sort((a, b) => b[1].at - a[1].at).slice(0, 2_000);
      data.seen = Object.fromEntries(seen);
      return { accepted, duplicates, ignored: null };
    });
    log("events_recorded", { accepted: result.accepted, duplicates: result.duplicates,
      reason: result.ignored ?? "pending", video_ids: (events as VideoEvent[]).map(event => event.videoId).join(",") });
    return json(result, 202);
  }

  private async testDispatch(body: unknown): Promise<Response> {
    const ids = (body as { videoIds?: unknown })?.videoIds;
    if (!Array.isArray(ids) || ids.length < 1 || ids.length > 50 || ids.some(id => typeof id !== "string" || !ID.test(id))) {
      return json({ error: "invalid_video_ids" }, 400);
    }
    const accepted = await this.change(data => {
      if (data.batch?.source === "websub" || Object.values(data.pending).some(item => item.source === "websub")) return false;
      for (const id of new Set(ids as string[])) {
        if (!Object.hasOwn(data.pending, id) && Object.keys(data.pending).length >= 200) throw new Error("pending_capacity");
        data.pending[id] = { fingerprint: `test-${crypto.randomUUID()}`, source: "websub_test", receivedAt: Date.now() };
      }
      data.pendingSince ??= Date.now();
      return true;
    });
    if (!accepted) return json({ error: "normal_events_pending" }, 409);
    log("controlled_test_queued", { videos: ids.length, dispatch_enabled: this.env.DISPATCH_ENABLED === "true" });
    return json({ queued: ids.length, dispatch_enabled: this.env.DISPATCH_ENABLED === "true" }, 202);
  }

  private async challenge(value: Challenge): Promise<Response> {
    if (!value || value.topic !== TOPIC || !["subscribe", "unsubscribe"].includes(value.mode)
      || typeof value.challenge !== "string" || value.challenge.length < 1 || value.challenge.length > 2_048
      || typeof value.verifyToken !== "string"
      || (value.leaseSeconds !== null && (!Number.isSafeInteger(value.leaseSeconds) || value.leaseSeconds < 60 || value.leaseSeconds > 31_536_000))) {
      return json({ error: "invalid_challenge" }, 400);
    }
    const valid = await this.change(data => {
      const sub = data.subscription, pending = sub.pending;
      if (sub.confirmed?.mode === value.mode && sub.confirmed.token === value.verifyToken && sub.confirmed.deadline >= Date.now()) return true;
      if (!pending || pending.mode !== value.mode || pending.token !== value.verifyToken || pending.deadline < Date.now()) return false;
      if (value.mode === "subscribe") {
        sub.enabled = true; sub.active = true;
        sub.expiresAt = value.leaseSeconds === null ? null : Date.now() + value.leaseSeconds * 1_000;
        sub.renewAt = value.leaseSeconds === null ? Date.now() + 6 * HOUR : Date.now() + value.leaseSeconds * 750;
      } else { sub.enabled = false; sub.active = false; sub.expiresAt = null; sub.renewAt = null; sub.desiredMode = null; }
      sub.confirmed = { mode: value.mode, token: value.verifyToken, deadline: Date.now() + VERIFY_WINDOW };
      sub.pending = null; sub.retryAt = null; sub.failures = 0; sub.lastReason = "verified";
      return true;
    });
    if (!valid) return json({ error: "unexpected_challenge" }, 403);
    log("subscription_verified", { mode: value.mode, lease_seconds: value.leaseSeconds });
    return new Response(value.challenge, { headers: { "Content-Type": "application/octet-stream",
      "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store" } });
  }

  private async prepareSubscription(mode: "subscribe" | "unsubscribe", manual = false): Promise<void> {
    const token = await this.change(data => {
      const sub = data.subscription;
      if (manual) sub.enabled = mode === "subscribe";
      if (sub.pending && sub.pending.deadline > Date.now() && sub.pending.mode === mode) return null;
      const token = crypto.randomUUID();
      sub.desiredMode = mode;
      sub.pending = { mode, token, deadline: Date.now() + VERIFY_WINDOW };
      sub.retryAt = null; sub.lastReason = "verification_pending";
      return token;
    });
    if (!token) return;
    const outcome = await requestSubscription(this.env, mode, token);
    await this.change(data => {
      const sub = data.subscription;
      if (sub.pending?.token !== token) return; // The challenge may have arrived before this response.
      if (outcome.accepted) { sub.lastReason = "hub_accepted_verification_pending"; return; }
      sub.pending = null; sub.failures++;
      sub.retryAt = Date.now() + Math.min(HOUR, 30_000 * 2 ** Math.min(sub.failures - 1, 7));
      sub.lastReason = outcome.reason;
    });
    log("subscription_request", { mode, status: outcome.status, accepted: outcome.accepted, reason: outcome.reason });
  }

  async alarm(): Promise<void> { await this.process(); }

  private async process(): Promise<void> {
    if (this.running) return this.running;
    this.running = this.perform();
    try { await this.running; } finally { this.running = null; }
  }

  private async perform(): Promise<void> {
    const mode = await this.change(data => {
      const sub = data.subscription, now = Date.now();
      if (sub.expiresAt !== null && sub.expiresAt <= now) sub.active = false;
      if (sub.pending && sub.pending.deadline <= now) {
        const previous = sub.pending.mode;
        sub.pending = null; sub.lastReason = "verification_timeout";
        sub.failures++; sub.retryAt = now + Math.min(HOUR, 30_000 * 2 ** Math.min(sub.failures - 1, 7));
        if (previous === "unsubscribe") sub.enabled = false;
      }
      if (sub.pending || (sub.retryAt !== null && sub.retryAt > now)) return null;
      if (sub.enabled && (!sub.active || sub.renewAt === null || sub.renewAt <= now || sub.retryAt !== null)) return "subscribe";
      if (!sub.enabled && sub.retryAt !== null && sub.desiredMode === "unsubscribe") return "unsubscribe";
      return null;
    });
    if (mode) await this.prepareSubscription(mode);
    await this.dispatch();
  }

  private async github(path: string, method = "GET", body?: unknown): Promise<Response> {
    if (!this.env.GITHUB_TOKEN) throw new Error("github_credential_missing");
    return fetch(`https://api.github.com/repos/${this.env.GITHUB_OWNER}/${this.env.GITHUB_REPO}/actions/workflows/${this.env.GITHUB_WORKFLOW}${path}`, {
      method, redirect: "manual", signal: AbortSignal.timeout(10_000),
      headers: { Authorization: `Bearer ${this.env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json",
        "Content-Type": "application/json", "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "ozecaoo-youtube-webhook" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  private async failure(reason: string, status: number | null, permanent = false, ambiguous: boolean | null = null, delay = 0): Promise<void> {
    await this.change(data => {
      data.failures++;
      if (data.batch && ambiguous !== null) {
        data.batch.ambiguous = ambiguous;
        if (!ambiguous) data.batch.sentAt = null;
      }
      data.blockedReason = permanent ? reason : data.failures >= 8 ? "github_retry_exhausted" : null;
      data.retryAt = data.blockedReason ? null : Date.now() + Math.min(HOUR, Math.max(delay, 30_000 * 2 ** Math.min(data.failures - 1, 7)));
    });
    log("github_deferred", { reason, status, permanent });
  }

  private async completed(batch: Batch, recovered: boolean): Promise<void> {
    await this.change(data => {
      if (data.batch?.id !== batch.id) return;
      for (const id of batch.ids) if (data.pending[id]?.fingerprint === batch.fingerprints[id]) delete data.pending[id];
      data.history = [...data.history.filter(time => time > Date.now() - HOUR), Date.now()];
      data.lastDispatchAt = Date.now(); data.retryAt = null; data.failures = 0;
      data.blockedReason = null; data.batch = null;
      data.pendingSince = Object.values(data.pending).length ? Math.min(...Object.values(data.pending).map(item => item.receivedAt)) : null;
    });
    log("github_dispatched", { batch_id: batch.id, videos: batch.ids.length,
      video_ids: batch.ids.join(","), trigger_source: batch.source, recovered });
  }

  private async dispatch(): Promise<void> {
    if (this.env.DISPATCH_ENABLED !== "true") return;
    let batch = await this.change(data => {
      const now = Date.now(), due = dispatchDue(data, now);
      if (due === null || due > now) return null;
      if (!data.batch) {
        const eligible = Object.entries(data.pending).filter(([, item]) => item.source === "websub_test" || data.subscription.enabled)
          .sort((a, b) => a[1].receivedAt - b[1].receivedAt || a[0].localeCompare(b[0]));
        const source = eligible[0][1].source;
        const ids = eligible.filter(([, item]) => item.source === source).slice(0, 50).map(([id]) => id);
        data.batch = { id: crypto.randomUUID(), ids, fingerprints: Object.fromEntries(ids.map(id => [id, data.pending[id].fingerprint])),
          source, ambiguous: false, preparedAt: now, sentAt: null };
      }
      return structuredClone(data.batch);
    });
    if (!batch) return;
    batch = await this.filterMetadataEdits(batch);
    if (!batch) return;
    if (!this.env.GITHUB_TOKEN) { await this.failure("github_credential_missing", null, true); return; }
    if (`${this.env.GITHUB_OWNER}/${this.env.GITHUB_REPO}` !== REPOSITORY || this.env.GITHUB_WORKFLOW !== "deploy.yml" || this.env.GITHUB_REF !== "main") {
      await this.failure("github_configuration_invalid", null, true); return;
    }
    let listing: Response;
    try { listing = await this.github(`/runs?branch=${encodeURIComponent(this.env.GITHUB_REF)}&per_page=30`); }
    catch { await this.failure("github_run_lookup_unavailable", null); return; }
    if (!listing.ok) {
      const rateLimit = this.rateLimit(listing);
      await listing.body?.cancel();
      await this.failure(rateLimit ? "github_rate_limited" : "github_run_lookup_failed", listing.status,
        !rateLimit && ([401, 403, 404].includes(listing.status) || listing.status >= 300 && listing.status < 400), null, rateLimit ?? 0); return;
    }
    let runs: Array<{ display_title?: string; status?: string }>;
    try {
      const text = await listing.text();
      if (text.length > 256_000) throw new Error("large_response");
      const body = JSON.parse(text) as { workflow_runs?: unknown };
      if (!Array.isArray(body.workflow_runs)) throw new Error("invalid_runs");
      runs = body.workflow_runs;
    } catch { await this.failure("github_run_lookup_invalid", null); return; }
    if (runs.some(run => typeof run.display_title === "string" && run.display_title.includes(batch.id))) {
      await this.completed(batch, true); return;
    }
    if (runs.some(run => typeof run.status === "string" && ACTIVE_RUNS.has(run.status))) {
      await this.change(data => { data.retryAt = Date.now() + 30_000; });
      log("github_deferred", { reason: "workflow_active", batch_id: batch.id }); return;
    }
    if (batch.ambiguous && Date.now() < (batch.sentAt ?? batch.preparedAt) + 60_000) {
      await this.change(data => { data.retryAt = (batch.sentAt ?? batch.preparedAt) + 60_000; }); return;
    }
    // Persist intent before the external side effect. A crash after GitHub
    // accepts but before our commit must re-enter lookup/ambiguity recovery.
    await this.change(data => {
      if (data.batch?.id === batch.id) { data.batch.ambiguous = true; data.batch.sentAt = Date.now(); }
    });
    let response: Response;
    try {
      response = await this.github("/dispatches", "POST", { ref: this.env.GITHUB_REF,
        inputs: { trigger_source: batch.source, batch_id: batch.id, video_ids: batch.ids.join(",") } });
    } catch { await this.failure("github_dispatch_ambiguous", null, false, true); return; }
    const status = response.status;
    const rateLimit = this.rateLimit(response);
    await response.body?.cancel();
    if (status === 200 || status === 204) { await this.completed(batch, false); return; }
    await this.failure(rateLimit ? "github_rate_limited" : "github_dispatch_failed", status,
      !rateLimit && ([400, 401, 403, 404, 422].includes(status) || status >= 300 && status < 400), status >= 500, rateLimit ?? 0);
  }

  private rateLimit(response: Response): number | null {
    const limited = response.status === 429 || (response.status === 403
      && (response.headers.get("x-ratelimit-remaining") === "0" || response.headers.has("retry-after")));
    if (!limited) return null;
    const retry = response.headers.get("retry-after");
    const explicit = retry && /^\d+$/.test(retry) ? Number(retry) * 1_000 : retry ? Date.parse(retry) - Date.now() : 0;
    const reset = Number(response.headers.get("x-ratelimit-reset")) * 1_000 - Date.now();
    return Math.min(HOUR, Math.max(30_000, Number.isFinite(explicit) ? explicit : 0, Number.isFinite(reset) ? reset : 0));
  }

  private async publishedVideos(): Promise<PublishedVideo[] | null> {
    try {
      const response = await fetch("https://ozecaoo.com/data/videos.json", {
        redirect: "manual", signal: AbortSignal.timeout(5_000),
        headers: { Accept: "application/json", "Cache-Control": "no-cache" },
      });
      if (!response.ok) { await response.body?.cancel(); return null; }
      const reader = response.body?.getReader();
      if (!reader) return null;
      const chunks: Uint8Array[] = [];
      let length = 0;
      try {
        while (true) {
          const item = await reader.read();
          if (item.done) break;
          length += item.value.byteLength;
          if (length > 256_000) { await reader.cancel(); return null; }
          chunks.push(item.value);
        }
      } finally { reader.releaseLock(); }
      const bytes = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      const payload = JSON.parse(new TextDecoder().decode(bytes)) as { channel_id?: unknown; videos?: unknown };
      if (!payload || payload.channel_id !== CHANNEL || !Array.isArray(payload.videos) || payload.videos.length !== 2) return null;
      const videos: PublishedVideo[] = [];
      for (const item of payload.videos) {
        if (!item || typeof item.id !== "string" || !ID.test(item.id) || videos.some(video => video.id === item.id)
          || typeof item.title !== "string" || !item.title.trim() || item.title.length > 1_000
          || typeof item.published !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(item.published)) return null;
        const published = Date.parse(item.published);
        const sortAt = item.sort_at === undefined ? published : typeof item.sort_at === "string"
          && /(?:Z|[+-]\d{2}:\d{2})$/.test(item.sort_at) ? Date.parse(item.sort_at) : NaN;
        if (!Number.isFinite(published) || !Number.isFinite(sortAt)) return null;
        videos.push({ id: item.id, title: item.title.trim(), published, sortAt });
      }
      return videos;
    } catch {
      log("published_filter_unavailable", { reason: "public_feed_unavailable_or_invalid" });
      return null;
    }
  }

  private async filterMetadataEdits(batch: Batch): Promise<Batch | null> {
    if (batch.source !== "websub" || batch.filtered || batch.ambiguous || batch.sentAt !== null) return batch;
    const published = await this.publishedVideos();
    const ignored: Array<{ id: string; reason: string }> = [];
    const result = await this.change(data => {
      if (data.batch?.id !== batch.id) return null;
      data.batch.filtered = true;
      if (published) {
        const cutoff = Math.min(...published.map(video => video.sortAt));
        for (const id of batch.ids) {
          const pending = data.pending[id];
          // A new version received during this best-effort network read belongs
          // to a later batch and is never discarded by an older snapshot.
          if (!pending || pending.fingerprint !== batch.fingerprints[id] || pending.isNew !== false) continue;
          const visible = published.find(video => video.id === id);
          const date = typeof pending.published === "string" ? Date.parse(pending.published) : NaN;
          const reason = visible && visible.title === pending.title?.trim() ? "already_reflected"
            : !visible && Number.isFinite(date) && date <= cutoff ? "metadata_not_visible" : null;
          if (reason) { delete data.pending[id]; ignored.push({ id, reason }); }
        }
        data.batch.ids = batch.ids.filter(id => !ignored.some(item => item.id === id));
        if (!data.batch.ids.length) {
          data.batch = null; data.retryAt = null; data.failures = 0;
        }
        data.pendingSince = Object.values(data.pending).length
          ? Math.min(...Object.values(data.pending).map(item => item.receivedAt)) : null;
      }
      return data.batch ? structuredClone(data.batch) : null;
    });
    for (const item of ignored) log("events_ignored", { video_id: item.id, reason: item.reason });
    return result;
  }
}
