import assert from "node:assert/strict";
import { test } from "node:test";
import worker from "../src/index.ts";
import { CHANNEL, TOPIC, type Env } from "../src/types.ts";

const SECRET = "test-hmac-secret-32-characters-long";
const ADMIN = "test-admin-secret-32-characters-long";
const XML = `<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>video000001</yt:videoId><yt:channelId>${CHANNEL}</yt:channelId><title>Um jogo &amp; outro</title><published>2026-11-04T10:00:00Z</published><updated>2026-11-04T10:00:00Z</updated></entry></feed>`;

function setup(result?: (request: Request) => Promise<Response>) {
  const calls: Request[] = [];
  const env = {
    CHANNEL_ID: CHANNEL, CALLBACK_URL: "https://hooks.ozecaoo.com/youtube",
    WEBSUB_SECRET: SECRET, ADMIN_TOKEN: ADMIN, GITHUB_TOKEN: "test-fine-grained-token",
    GITHUB_OWNER: "vlopcas", GITHUB_REPO: "oZECAOo", GITHUB_REF: "main", GITHUB_WORKFLOW: "deploy.yml",
    DISPATCH_ENABLED: "false",
    COORDINATOR: { idFromName: () => "one-channel", get: () => ({
      fetch: async (url: string, init: RequestInit) => {
        const request = new Request(url, init);
        calls.push(request);
        return result ? result(request) : Response.json({ accepted: true }, { status: 202 });
      },
    }) },
  } as unknown as Env;
  return { env, calls };
}

async function signed(body = XML): Promise<Request> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(SECRET),
    { name: "HMAC", hash: "SHA-1" }, false, ["sign"]);
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  const hex = [...new Uint8Array(signature)].map(value => value.toString(16).padStart(2, "0")).join("");
  return new Request("https://hooks.ozecaoo.com/youtube", { method: "POST", body,
    headers: { "X-Hub-Signature": "sha1=" + hex } });
}

test("health is minimal and does not access storage", async () => {
  const { env, calls } = setup();
  const response = await worker.fetch(new Request("https://hooks.ozecaoo.com/health"), env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { service: "ozecaoo-youtube-webhook", ok: true });
  assert.equal(calls.length, 0);
});

test("remote plaintext and unexpected routes never reach the coordinator", async () => {
  const { env, calls } = setup();
  assert.equal((await worker.fetch(new Request("http://hooks.ozecaoo.com/youtube"), env)).status, 400);
  assert.equal((await worker.fetch(new Request("https://hooks.ozecaoo.com/arbitrary"), env)).status, 404);
  assert.equal(calls.length, 0);
});

test("missing or invalid authentication is rejected before parsing/storage", async () => {
  const { env, calls } = setup();
  for (const signature of [null, "sha1=" + "0".repeat(40), "sha512=" + "a".repeat(128)]) {
    const headers = signature ? { "X-Hub-Signature": signature } : undefined;
    const response = await worker.fetch(new Request("https://hooks.ozecaoo.com/youtube", {
      method: "POST", body: XML, headers,
    }), env);
    assert.equal(response.status, 403);
  }
  assert.equal(calls.length, 0);
});

test("valid notification is persisted before returning a bodyless ACK", async () => {
  const { env, calls } = setup();
  const response = await worker.fetch(await signed(), env);
  assert.equal(response.status, 204);
  assert.equal(await response.text(), "");
  assert.equal(calls.length, 1);
  assert.equal(new URL(calls[0].url).pathname, "/events");
  const body = await calls[0].json() as { events: Array<{ videoId: string; title: string }> };
  assert.equal(body.events[0].videoId, "video000001");
  assert.equal(body.events[0].title, "Um jogo & outro");
  const serialized = JSON.stringify(body);
  assert(!serialized.includes(SECRET));
  assert(!serialized.includes(ADMIN));
});

test("persistence failures ask the hub to retry rather than acknowledge loss", async () => {
  const { env } = setup(async () => new Response("unavailable", { status: 503 }));
  assert.equal((await worker.fetch(await signed(), env)).status, 503);
});

test("thrown persistence errors are handled and do not leak response details", async () => {
  const { env } = setup(async () => { throw new Error("private-storage-details"); });
  const response = await worker.fetch(await signed(), env);
  assert.equal(response.status, 503);
  assert(!((await response.text()).includes("private-storage-details")));
});

test("signed malformed or wrong-channel XML never reaches storage", async () => {
  const { env, calls } = setup();
  for (const body of ["<broken>", XML.replace(CHANNEL, "UC" + "a".repeat(22))]) {
    assert.equal((await worker.fetch(await signed(body), env)).status, 400);
  }
  assert.equal(calls.length, 0);
});

test("payload limit applies to actual bytes even without a declared length", async () => {
  const { env, calls } = setup();
  const request = new Request("https://hooks.ozecaoo.com/youtube", { method: "POST", body: "x".repeat(65_537) });
  assert.equal((await worker.fetch(request, env)).status, 413);
  assert.equal(calls.length, 0);
});

test("valid challenge syntax is forwarded but unexpected topics are rejected", async () => {
  const { env, calls } = setup(async request => {
    const value = await request.json() as { challenge: string };
    return new Response(value.challenge, { headers: { "Content-Type": "application/octet-stream" } });
  });
  const params = new URLSearchParams({ "hub.mode": "subscribe", "hub.topic": TOPIC,
    "hub.challenge": "challenge-123", "hub.verify_token": "pending-token", "hub.lease_seconds": "864000" });
  const response = await worker.fetch(new Request("https://hooks.ozecaoo.com/youtube?" + params), env);
  assert.equal(await response.text(), "challenge-123");
  assert.equal(new URL(calls[0].url).pathname, "/challenge");
  params.set("hub.topic", "https://other.example/topic");
  assert.equal((await worker.fetch(new Request("https://hooks.ozecaoo.com/youtube?" + params), env)).status, 404);
  assert.equal(calls.length, 1);
});

test("administration requires the independent bearer token", async () => {
  const { env, calls } = setup();
  for (const token of ["", SECRET, ADMIN + "bad"]) {
    const response = await worker.fetch(new Request("https://hooks.ozecaoo.com/admin/subscribe", {
      method: "POST", headers: { Authorization: "Bearer " + token },
    }), env);
    assert.equal(response.status, 401);
  }
  assert.equal(calls.length, 0);
  const response = await worker.fetch(new Request("https://hooks.ozecaoo.com/admin/subscribe", {
    method: "POST", headers: { Authorization: "Bearer " + ADMIN },
  }), env);
  assert.equal(response.status, 202);
  assert.equal(new URL(calls[0].url).pathname, "/subscribe");
});

test("controlled dispatch validates IDs and never accepts arbitrary workflow names", async () => {
  const { env, calls } = setup();
  for (const body of [{ videoIds: [] }, { videoIds: ["invalid"] }, { videoIds: Array(51).fill("video000001") }]) {
    const response = await worker.fetch(new Request("https://hooks.ozecaoo.com/admin/test-dispatch", {
      method: "POST", headers: { Authorization: "Bearer " + ADMIN }, body: JSON.stringify(body),
    }), env);
    assert.equal(response.status, 400);
  }
  assert.equal(calls.length, 0);
  await worker.fetch(new Request("https://hooks.ozecaoo.com/admin/test-dispatch", {
    method: "POST", headers: { Authorization: "Bearer " + ADMIN },
    body: JSON.stringify({ videoIds: ["video000001", "video000001"], workflow: "arbitrary.yml" }),
  }), env);
  assert.deepEqual(await calls[0].json(), { videoIds: ["video000001"] });
});
