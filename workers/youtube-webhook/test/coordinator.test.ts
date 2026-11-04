import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { ChannelCoordinator } from "../src/coordinator";
import { CHANNEL, HUB, TOPIC, type Env, type VideoEvent } from "../src/types";

const KEY = "coordinator-v1";
const BASE = 1_800_000_000_000;
let now = BASE;

class MemoryStorage {
  values = new Map<string, any>();
  alarm: number | null = null;
  fail = false;
  private queue = Promise.resolve();
  async get<T>(key: string): Promise<T | undefined> { return structuredClone(this.values.get(key)); }
  async put(key: string, value: unknown): Promise<void> {
    if (this.fail) throw new Error("storage_failure");
    this.values.set(key, structuredClone(value));
  }
  async setAlarm(time: number): Promise<void> { if (this.fail) throw new Error("alarm_failure"); this.alarm = time; }
  async deleteAlarm(): Promise<void> { this.alarm = null; }
  async transaction<T>(fn: (txn: MemoryStorage) => Promise<T>): Promise<T> {
    const previous = this.queue;
    let release!: () => void;
    this.queue = new Promise<void>(resolve => { release = resolve; });
    await previous;
    const original = structuredClone(this.values), alarm = this.alarm;
    try { return await fn(this); }
    catch (error) { this.values = original; this.alarm = alarm; throw error; }
    finally { release(); }
  }
}

function env(enabled = true): Env {
  return { COORDINATOR: {} as DurableObjectNamespace, CHANNEL_ID: CHANNEL,
    CALLBACK_URL: "https://hooks.ozecaoo.com/youtube", GITHUB_OWNER: "vlopcas", GITHUB_REPO: "oZECAOo",
    GITHUB_WORKFLOW: "deploy.yml", GITHUB_REF: "main", DISPATCH_ENABLED: String(enabled),
    WEBSUB_SECRET: "s".repeat(64), ADMIN_TOKEN: "a".repeat(64), GITHUB_TOKEN: "github-test-secret" };
}

function request(path: string, body?: unknown): Request {
  return new Request(`https://coordinator${path}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
}

function video(number = 1, fingerprint = `fingerprint-${number}`): VideoEvent {
  return { videoId: `ID${String(number).padStart(9, "0")}`, channelId: CHANNEL,
    title: `Video ${number}`, published: "2026-11-04T12:00:00Z", updated: "2026-11-04T12:00:00Z", fingerprint };
}

async function harness(enabled = true, subscribed = true) {
  const storage = new MemoryStorage(), configuration = env(enabled);
  const state = { storage } as unknown as DurableObjectState;
  const coordinator = new ChannelCoordinator(state, configuration);
  await coordinator.fetch(request("/tick", {}));
  if (subscribed) {
    const value = await storage.get<any>(KEY);
    value.subscription.enabled = true; value.subscription.active = true;
    value.subscription.expiresAt = now + 48 * 3_600_000;
    value.subscription.renewAt = now + 24 * 3_600_000;
    await storage.put(KEY, value);
  }
  return { storage, configuration, coordinator, state };
}

function githubStub(options: { runs?: unknown[]; post?: (body: any) => Response | Promise<Response>;
  published?: unknown; publicFetches?: string[] } = {}) {
  const calls: Array<{ url: string; method: string; body: any }> = [];
  mock.method(globalThis, "fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    // Workers supports follow/manual, not the browser's redirect:error mode.
    assert.equal(init?.redirect, "manual");
    const url = String(input), method = init?.method ?? "GET";
    if (url === "https://ozecaoo.com/data/videos.json") {
      options.publicFetches?.push(url);
      if (options.published instanceof Error) throw options.published;
      return options.published === undefined ? new Response(null, { status: 404 }) : Response.json(options.published);
    }
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, method, body });
    if (method === "POST") return options.post ? options.post(body) : new Response(null, { status: 204 });
    return Response.json({ workflow_runs: options.runs ?? [] });
  });
  return calls;
}

beforeEach(() => {
  now = BASE;
  mock.method(Date, "now", () => now);
  mock.method(console, "log", () => {});
});
afterEach(() => mock.restoreAll());

test("duplicate concurrent deliveries persist only one pending event and survive restart", async () => {
  const { coordinator, storage, state, configuration } = await harness();
  const body = { events: [video()] };
  const responses = await Promise.all([coordinator.fetch(request("/events", body)), coordinator.fetch(request("/events", body))]);
  const results = await Promise.all(responses.map(response => response.json() as Promise<any>));
  assert.equal(results.reduce((sum, value) => sum + value.accepted, 0), 1);
  assert.equal(results.reduce((sum, value) => sum + value.duplicates, 0), 1);
  assert.equal(storage.alarm, now + 30_000);
  const restarted = new ChannelCoordinator(state, configuration);
  const response = await restarted.fetch(request("/events", body));
  assert.equal((await response.json() as any).duplicates, 1);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 1);
});

test("different videos in a burst become one dispatch with correct fixed workflow and inputs", async () => {
  const { coordinator, storage } = await harness();
  const calls = githubStub();
  await coordinator.fetch(request("/events", { events: [video(1)] }));
  now += 10_000;
  await coordinator.fetch(request("/events", { events: [video(2)] }));
  now += 20_000;
  await coordinator.alarm();
  const posts = calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, "https://api.github.com/repos/vlopcas/oZECAOo/actions/workflows/deploy.yml/dispatches");
  assert.equal(posts[0].body.ref, "main");
  assert.equal(posts[0].body.inputs.trigger_source, "websub");
  assert.equal(posts[0].body.inputs.video_ids, "ID000000001,ID000000002");
  assert.match(posts[0].body.inputs.batch_id, /^[a-f0-9-]{36}$/);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 0);
});

test("updated-only duplicate does not reset debounce or enqueue another workflow", async () => {
  const { coordinator, storage } = await harness();
  await coordinator.fetch(request("/events", { events: [video()] }));
  const alarm = storage.alarm;
  now += 15_000;
  const event = { ...video(), updated: "2026-11-04T12:10:00Z" };
  const response = await coordinator.fetch(request("/events", { events: [event] }));
  assert.equal((await response.json() as any).duplicates, 1);
  assert.equal(storage.alarm, alarm);
});

test("disabled dispatch retains queue without touching GitHub", async () => {
  const { coordinator, storage } = await harness(false);
  const calls = githubStub();
  await coordinator.fetch(request("/events", { events: [video()] }));
  now += 600_000;
  await coordinator.alarm();
  assert.equal(calls.length, 0);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 1);
});

test("cooldown defers a different legitimate video instead of deleting it", async () => {
  const { coordinator, storage } = await harness();
  const calls = githubStub();
  await coordinator.fetch(request("/events", { events: [video(1)] })); now += 30_000; await coordinator.alarm();
  await coordinator.fetch(request("/events", { events: [video(2)] })); now += 30_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 1);
  now += 90_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 2);
});

test("active workflow defers the batch and makes no duplicate dispatch", async () => {
  const { coordinator, storage } = await harness();
  const calls = githubStub({ runs: [{ status: "in_progress", display_title: "manual run" }] });
  await coordinator.fetch(request("/events", { events: [video()] })); now += 30_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 0);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 1);
  assert.equal(storage.alarm, now + 30_000);
});

test("one explicit administrative test can enqueue while automatic events remain deferred", async () => {
  const { coordinator, storage } = await harness(true, false);
  const calls = githubStub({ runs: [{ status: "queued", display_title: "an older push" }] });
  await coordinator.fetch(request("/test-dispatch", { videoIds: [video().videoId] }));
  now += 30_000; await coordinator.alarm();
  const posts = calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 1);
  assert.equal(posts[0].body.inputs.trigger_source, "websub_test");
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 0);
});

test("realistic GitHub run metadata above 256KB is accepted with bounded pagination", async () => {
  const { coordinator, storage } = await harness();
  const calls = githubStub({ runs: [{ status: "completed", display_title: "older run",
    repository: { description: "x".repeat(356_000) } }] });
  await coordinator.fetch(request("/events", { events: [video()] }));
  now += 30_000; await coordinator.alarm();
  assert(calls.find(call => call.method === "GET")?.url.includes("per_page=10"));
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 0);
});

test("oversized or malformed GitHub responses keep the durable batch pending", async () => {
  const { coordinator, storage } = await harness();
  const calls = githubStub({ runs: [{ status: "completed", display_title: "older run",
    repository: { description: "x".repeat(512_000) } }] });
  await coordinator.fetch(request("/events", { events: [video()] }));
  now += 30_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 0);
  const saved = await storage.get<any>(KEY);
  assert.equal(Object.keys(saved.pending).length, 1);
  assert(saved.retryAt > now);
});

test("authentication failure blocks alarm retries but maintenance can recover after secret repair", async () => {
  const { coordinator, storage } = await harness();
  let repaired = false;
  const calls = githubStub({ post: () => new Response(null, { status: repaired ? 204 : 401 }) });
  await coordinator.fetch(request("/events", { events: [video()] })); now += 30_000; await coordinator.alarm();
  assert.equal((await storage.get<any>(KEY)).blockedReason, "github_dispatch_failed");
  now += 3_600_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  repaired = true; await coordinator.fetch(request("/tick", {}));
  assert.equal(calls.filter(call => call.method === "POST").length, 2);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 0);
});

test("temporary GitHub failure persists pending batch and retries via alarm", async () => {
  const { coordinator, storage } = await harness();
  let failures = 1;
  const calls = githubStub({ post: () => new Response(null, { status: failures-- > 0 ? 500 : 204 }) });
  await coordinator.fetch(request("/events", { events: [video()] })); now += 30_000; await coordinator.alarm();
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 1);
  now += 60_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 2);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 0);
});

test("ambiguous timeout finds its batch in runs and never blindly posts again", async () => {
  const { coordinator, storage } = await harness();
  const runs: unknown[] = [];
  const calls = githubStub({ runs, post: async body => {
    runs.push({ status: "queued", display_title: `ZECAO · websub · ${body.inputs.batch_id}` });
    throw new Error("timeout after server accepted request");
  } });
  await coordinator.fetch(request("/events", { events: [video()] })); now += 30_000; await coordinator.alarm();
  assert.equal((await storage.get<any>(KEY)).batch.ambiguous, true);
  now += 30_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 0);
});

test("storage failure returns 503 and never ACKs unpersisted events", async () => {
  const { coordinator, storage } = await harness(); storage.fail = true;
  const response = await coordinator.fetch(request("/events", { events: [video()] }));
  assert.equal(response.status, 503);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 0);
});

test("initial maintenance does not create a remote subscription", async () => {
  const calls = githubStub();
  const { coordinator } = await harness(true, false);
  await coordinator.fetch(request("/tick", {}));
  assert.equal(calls.length, 0);
});

test("subscription acceptance is pending until exact nonce/topic/mode challenge with lease", async () => {
  const { coordinator, storage } = await harness(true, false);
  const calls: string[] = [];
  mock.method(globalThis, "fetch", async (url: RequestInfo | URL) => { calls.push(String(url)); return new Response(null, { status: 202 }); });
  await coordinator.fetch(request("/subscribe", {}));
  let data = await storage.get<any>(KEY);
  assert.equal(data.subscription.active, false); assert.equal(calls[0], HUB);
  const challenge = { mode: "subscribe", topic: TOPIC, challenge: "challenge123", verifyToken: data.subscription.pending.token, leaseSeconds: 86_400 };
  assert.equal((await coordinator.fetch(request("/challenge", { ...challenge, topic: "https://other.invalid" }))).status, 400);
  assert.equal((await coordinator.fetch(request("/challenge", { ...challenge, verifyToken: "wrong" }))).status, 403);
  const response = await coordinator.fetch(request("/challenge", challenge));
  assert.equal(await response.text(), "challenge123");
  data = await storage.get<any>(KEY);
  assert.equal(data.subscription.active, true);
  assert.equal(data.subscription.expiresAt, now + 86_400_000);
  assert.equal(data.subscription.renewAt, now + 64_800_000);
  const status = await (await coordinator.fetch(request("/status"))).text();
  assert(!status.includes(challenge.verifyToken));
});

test("renewal occurs once at lease threshold and not at every tick", async () => {
  const { coordinator, storage } = await harness();
  let hubCalls = 0;
  mock.method(globalThis, "fetch", async () => { hubCalls++; return new Response(null, { status: 202 }); });
  await coordinator.fetch(request("/tick", {})); assert.equal(hubCalls, 0);
  const data = await storage.get<any>(KEY); now = data.subscription.renewAt;
  await coordinator.alarm(); assert.equal(hubCalls, 1);
  await coordinator.alarm(); assert.equal(hubCalls, 1);
});

test("expired subscription renews while verification timeout recovers with backoff", async () => {
  const { coordinator, storage } = await harness();
  let hubCalls = 0;
  mock.method(globalThis, "fetch", async () => { hubCalls++; return new Response(null, { status: 202 }); });
  const data = await storage.get<any>(KEY); now = data.subscription.expiresAt + 1;
  await coordinator.alarm(); assert.equal(hubCalls, 1);
  now += 600_001; await coordinator.alarm();
  assert.equal((await storage.get<any>(KEY)).subscription.lastReason, "verification_timeout");
  assert.equal(hubCalls, 1);
  now += 30_000; await coordinator.alarm(); assert.equal(hubCalls, 2);
});

test("unsubscribe disables renewal and normal events even before hub confirms", async () => {
  const { coordinator, storage } = await harness();
  mock.method(globalThis, "fetch", async () => new Response(null, { status: 202 }));
  await coordinator.fetch(request("/unsubscribe", {}));
  const response = await coordinator.fetch(request("/events", { events: [video()] }));
  assert.equal((await response.json() as any).ignored, "subscription_disabled");
  const data = await storage.get<any>(KEY);
  const responseChallenge = await coordinator.fetch(request("/challenge", { mode: "unsubscribe", topic: TOPIC,
    challenge: "unsub", verifyToken: data.subscription.pending.token, leaseSeconds: null }));
  assert.equal(responseChallenge.status, 200);
  assert.equal((await storage.get<any>(KEY)).subscription.active, false);
});

test("hourly dispatch cap defers without removing legitimate IDs", async () => {
  const { coordinator, storage } = await harness();
  const calls = githubStub();
  const data = await storage.get<any>(KEY); data.history = Array.from({ length: 12 }, (_, index) => now - 1_000 + index);
  await storage.put(KEY, data);
  await coordinator.fetch(request("/events", { events: [video()] }));
  now += 30_000; await coordinator.alarm();
  assert.equal(calls.length, 0); assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 1);
  now += 3_600_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
});

test("a changed title arriving during dispatch is retained for a later batch", async () => {
  const { coordinator, storage } = await harness();
  githubStub({ post: async () => {
    await coordinator.fetch(request("/events", { events: [video(1, "new-title-fingerprint")] }));
    return new Response(null, { status: 204 });
  } });
  await coordinator.fetch(request("/events", { events: [video()] })); now += 30_000; await coordinator.alarm();
  assert.equal((await storage.get<any>(KEY)).pending.ID000000001.fingerprint, "new-title-fingerprint");
});

test("controlled administrative test works without lease and remains explicitly marked", async () => {
  const { coordinator } = await harness(true, false);
  const calls = githubStub();
  await coordinator.fetch(request("/test-dispatch", { videoIds: [video().videoId] }));
  now += 30_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST")[0].body.inputs.trigger_source, "websub_test");
});

test("wrong channel, malformed internal payload and capacity overflow fail safely", async () => {
  const { coordinator, storage } = await harness();
  assert.equal((await coordinator.fetch(request("/events", { events: [{ ...video(), channelId: "wrong" }] }))).status, 400);
  assert.equal((await coordinator.fetch(request("/test-dispatch", { videoIds: ["invalid"] }))).status, 400);
  for (let offset = 0; offset < 200; offset += 50) {
    assert.equal((await coordinator.fetch(request("/events", { events: Array.from({ length: 50 }, (_, index) => video(offset + index)) }))).status, 202);
  }
  assert.equal((await coordinator.fetch(request("/events", { events: [video(999)] }))).status, 503);
  assert.equal((await coordinator.fetch(request("/events", { events: [{ ...video(999), videoId: "constructor" }] }))).status, 503);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 200);
});

test("replay of an older title version remains duplicate after a newer title arrived", async () => {
  const { coordinator, storage } = await harness();
  await coordinator.fetch(request("/events", { events: [video(1, "original")] }));
  await coordinator.fetch(request("/events", { events: [video(1, "new-title")] }));
  const replay = await coordinator.fetch(request("/events", { events: [video(1, "original")] }));
  assert.equal((await replay.json() as any).duplicates, 1);
  assert.equal((await storage.get<any>(KEY)).pending.ID000000001.fingerprint, "new-title");
});

test("duplicate subscription challenge echoes again without extending lease", async () => {
  const { coordinator, storage } = await harness(true, false);
  mock.method(globalThis, "fetch", async () => new Response(null, { status: 202 }));
  await coordinator.fetch(request("/subscribe", {}));
  const pending = (await storage.get<any>(KEY)).subscription.pending;
  const challenge = { mode: "subscribe", topic: TOPIC, challenge: "repeatable", verifyToken: pending.token, leaseSeconds: 86_400 };
  await coordinator.fetch(request("/challenge", challenge));
  const original = (await storage.get<any>(KEY)).subscription;
  now += 60_000;
  const repeated = await coordinator.fetch(request("/challenge", challenge));
  assert.equal(repeated.status, 200); assert.equal(await repeated.text(), "repeatable");
  assert.equal(repeated.headers.get("x-content-type-options"), "nosniff");
  const after = (await storage.get<any>(KEY)).subscription;
  assert.equal(after.expiresAt, original.expiresAt); assert.equal(after.renewAt, original.renewAt);
  now += 600_001;
  assert.equal((await coordinator.fetch(request("/challenge", challenge))).status, 403);
});

test("failed unsubscribe before active lease retries after backoff without busy loop", async () => {
  const { coordinator, storage } = await harness(true, false);
  let requests = 0;
  mock.method(globalThis, "fetch", async () => { requests++; return new Response(null, { status: requests === 1 ? 503 : 202 }); });
  await coordinator.fetch(request("/unsubscribe", {}));
  assert.equal(storage.alarm, now + 30_000);
  now += 30_000; await coordinator.alarm();
  assert.equal(requests, 2);
  assert.equal(storage.alarm, now + 600_000);
  assert.equal((await storage.get<any>(KEY)).subscription.pending.mode, "unsubscribe");
});

test("administrative test is rejected while normal events are pending", async () => {
  const { coordinator, storage } = await harness();
  await coordinator.fetch(request("/events", { events: [video()] }));
  const response = await coordinator.fetch(request("/test-dispatch", { videoIds: [video(2).videoId] }));
  assert.equal(response.status, 409);
  assert.deepEqual(Object.keys((await storage.get<any>(KEY)).pending), [video().videoId]);
});

test("normal event arriving after a queued test is dispatched in a separate source batch", async () => {
  const { coordinator, storage } = await harness();
  const calls = githubStub();
  await coordinator.fetch(request("/test-dispatch", { videoIds: [video(1).videoId] }));
  now += 1;
  await coordinator.fetch(request("/events", { events: [video(2)] }));
  now += 30_000; await coordinator.alarm();
  const first = calls.find(call => call.method === "POST")!;
  assert.equal(first.body.inputs.trigger_source, "websub_test");
  assert.equal(first.body.inputs.video_ids, video(1).videoId);
  assert.deepEqual(Object.keys((await storage.get<any>(KEY)).pending), [video(2).videoId]);
  now += 120_000; await coordinator.alarm();
  const second = calls.filter(call => call.method === "POST")[1];
  assert.equal(second.body.inputs.trigger_source, "websub");
  assert.equal(second.body.inputs.video_ids, video(2).videoId);
});

test("GitHub 403 rate limit is deferred while ordinary 403 remains blocked", async () => {
  const { coordinator, storage } = await harness();
  const calls = githubStub({ post: () => new Response(null, { status: 403,
    headers: { "x-ratelimit-remaining": "0", "retry-after": "180" } }) });
  await coordinator.fetch(request("/events", { events: [video()] })); now += 30_000; await coordinator.alarm();
  const data = await storage.get<any>(KEY);
  assert.equal(data.blockedReason, null); assert.equal(data.retryAt, now + 180_000);
  assert.equal(data.batch.ambiguous, false);
  now += 60_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
});

test("crash after accepted dispatch leaves persistent intent and recovers after restart", async () => {
  const { coordinator, storage, state, configuration } = await harness();
  const runs: unknown[] = [];
  const calls = githubStub({ runs, post: async body => {
    const stored = await storage.get<any>(KEY);
    assert.equal(stored.batch.ambiguous, true); assert.equal(stored.batch.sentAt, now);
    assert.equal(Object.keys(stored.pending).length, 1);
    runs.push({ status: "completed", display_title: `ZECAO · websub · ${body.inputs.batch_id}` });
    storage.fail = true;
    return new Response(null, { status: 204 });
  } });
  await coordinator.fetch(request("/events", { events: [video()] })); now += 30_000;
  await assert.rejects(coordinator.alarm());
  storage.fail = false;
  const restarted = new ChannelCoordinator(state, configuration);
  now += 30_000; await restarted.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 0);
});

test("ambiguity visibility window uses sentAt after a long prepared-batch wait", async () => {
  const { coordinator, storage } = await harness();
  const calls = githubStub({ post: () => { throw new Error("ambiguous network timeout"); } });
  await coordinator.fetch(request("/events", { events: [video()] })); now += 30_000; await coordinator.alarm();
  const data = await storage.get<any>(KEY);
  data.batch.preparedAt = now - 3_600_000;
  await storage.put(KEY, data);
  now += 30_000; await coordinator.alarm();
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  assert.equal(storage.alarm, data.batch.sentAt + 60_000);
});

test("state and alarm failure roll back together before notification ACK", async () => {
  const { coordinator, storage } = await harness();
  mock.method(storage, "setAlarm", async () => { throw new Error("alarm storage failure"); });
  const response = await coordinator.fetch(request("/events", { events: [video()] }));
  assert.equal(response.status, 503);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 0);
});

function publishedFeed() {
  return { channel_id: CHANNEL, videos: [
    { id: video(1).videoId, title: "Video 1", published: "2026-11-04T12:00:00Z", sort_at: "2026-11-04T15:00:00Z" },
    { id: video(2).videoId, title: "Video 2", published: "2026-11-03T12:00:00Z" },
  ] };
}

async function previouslySeen(storage: MemoryStorage, id: string) {
  const data = await storage.get<any>(KEY);
  data.seen[`${id}:earlier-fingerprint`] = { fingerprint: "earlier-fingerprint", at: now - 86_400_000 };
  await storage.put(KEY, data);
}

test("known old title edit outside the visible cards is ignored without GitHub or cooldown", async () => {
  const { coordinator, storage } = await harness();
  const publicFetches: string[] = [];
  const calls = githubStub({ published: publishedFeed(), publicFetches });
  await previouslySeen(storage, video(3).videoId);
  await coordinator.fetch(request("/events", { events: [{ ...video(3), published: "2026-10-01T12:00:00Z" }] }));
  now += 30_000; await coordinator.alarm();
  assert.equal(publicFetches.length, 1); assert.equal(calls.length, 0);
  const data = await storage.get<any>(KEY);
  assert.equal(Object.keys(data.pending).length, 0); assert.equal(data.batch, null);
  assert.equal(data.history.length, 0); assert.equal(data.lastDispatchAt, null);
});

test("known title already reflected in the public card needs no dispatch", async () => {
  const { coordinator, storage } = await harness();
  const calls = githubStub({ published: publishedFeed() });
  await previouslySeen(storage, video(1).videoId);
  await coordinator.fetch(request("/events", { events: [video(1)] }));
  now += 30_000; await coordinator.alarm();
  assert.equal(calls.length, 0);
  assert.equal(Object.keys((await storage.get<any>(KEY)).pending).length, 0);
});

test("new IDs are retained regardless of old publication date and the filter runs only once per batch", async () => {
  const { coordinator, storage } = await harness();
  const publicFetches: string[] = [];
  let attempts = 0;
  const calls = githubStub({ published: publishedFeed(), publicFetches,
    post: () => new Response(null, { status: attempts++ === 0 ? 500 : 204 }) });
  await coordinator.fetch(request("/events", { events: [{ ...video(3), published: "2026-10-01T12:00:00Z" }] }));
  now += 30_000; await coordinator.alarm();
  assert.equal((await storage.get<any>(KEY)).pending[video(3).videoId].isNew, true);
  now += 60_000; await coordinator.alarm();
  assert.equal(publicFetches.length, 1); assert.equal(calls.filter(call => call.method === "POST").length, 2);
});

test("invalid or unavailable public cache never discards a known metadata edit", async () => {
  for (const payload of [new Error("offline"), { channel_id: "wrong", videos: [] }, { channel_id: CHANNEL, videos: [] }]) {
    const { coordinator, storage } = await harness();
    await previouslySeen(storage, video(3).videoId);
    const calls = githubStub({ published: payload });
    await coordinator.fetch(request("/events", { events: [{ ...video(3), published: "2026-10-01T12:00:00Z" }] }));
    now += 30_000; await coordinator.alarm();
    assert.equal(calls.filter(call => call.method === "POST").length, 1);
    mock.restoreAll(); mock.method(Date, "now", () => now); mock.method(console, "log", () => {});
  }
});
