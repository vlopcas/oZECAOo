import assert from "node:assert/strict";
import { test } from "node:test";
import { parseChallenge, parseNotification, requestSubscription, verifySignature, WebSubError } from "../src/websub.ts";
import { CHANNEL, HUB, MAX_PAYLOAD_BYTES, TOPIC, type Env } from "../src/types.ts";

const encoder = new TextEncoder();
const SECRET = "test-hmac-secret-never-log";
const TOKEN = "pending-random-verification-token";
const VID = "ID000000001";
const OTHER = "UC" + "b".repeat(22);
const ATOM = "http://www.w3.org/2005/Atom";
const YT = "http://www.youtube.com/xml/schemas/2015";
const fixture = (entries: string, extra = "") => encoder.encode(
  `<?xml version="1.0"?><feed xmlns="${ATOM}" xmlns:yt="${YT}" ${extra}>${entries}</feed>`,
);
const entry = (id = VID, title = "Um momento &amp; um jogo", channel = CHANNEL, updated = "2026-11-04T12:01:00Z") =>
  `<entry><yt:videoId>${id}</yt:videoId><yt:channelId>${channel}</yt:channelId><title>${title}</title>`
  + `<published>2026-11-04T12:00:00Z</published><updated>${updated}</updated></entry>`;

async function sign(body: Uint8Array, algorithm = "sha1"): Promise<string> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(SECRET),
    { name: "HMAC", hash: algorithm === "sha1" ? "SHA-1" : "SHA-256" }, false, ["sign"]);
  const digest = await crypto.subtle.sign("HMAC", key, Uint8Array.from(body).buffer);
  return `${algorithm}=${Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

test("SHA-1 and SHA-256 authenticate exact raw bytes, never a decoded/reencoded approximation", async () => {
  const body = fixture(entry());
  for (const algorithm of ["sha1", "sha256"]) {
    const signature = await sign(body, algorithm);
    assert.equal(await verifySignature(body, signature, SECRET), true);
    assert.equal(await verifySignature(encoder.encode(new TextDecoder().decode(body) + " "), signature, SECRET), false);
    assert.equal(await verifySignature(body, signature, "different-secret"), false);
  }
});

test("missing, malformed, unsupported and duplicate signatures are rejected", async () => {
  const body = fixture(entry());
  for (const signature of [null, "", "md5=" + "a".repeat(32), "sha1=" + "0".repeat(39),
    "sha256=" + "g".repeat(64), "sha1=" + "a".repeat(40) + ",sha256=" + "a".repeat(64)]) {
    assert.equal(await verifySignature(body, signature, SECRET), false);
  }
  assert.equal(await verifySignature(body, await sign(body), ""), false);
  assert.equal(await verifySignature(new Uint8Array(MAX_PAYLOAD_BYTES + 1), await sign(body), SECRET), false);
});

test("valid Atom yields a normalized public event and stable hash", async () => {
  const events = await parseNotification(fixture(entry()), CHANNEL);
  assert.equal(events.length, 1);
  assert.equal(events[0].videoId, VID);
  assert.equal(events[0].title, "Um momento & um jogo");
  assert.equal(events[0].published, "2026-11-04T12:00:00.000Z");
  assert.match(events[0].fingerprint, /^[a-f0-9]{64}$/);
});

test("namespace URIs, not a prefix spelling, determine trusted fields", async () => {
  const body = encoder.encode(`<a:feed xmlns:a="${ATOM}" xmlns:v="${YT}">`
    + `<a:entry><v:videoId>${VID}</v:videoId><v:channelId>${CHANNEL}</v:channelId><a:title>Outro</a:title>`
    + `<a:published>2026-11-04T12:00:00+00:00</a:published><a:updated>2026-11-04T12:01:00Z</a:updated></a:entry></a:feed>`);
  assert.equal((await parseNotification(body, CHANNEL))[0].videoId, VID);
  await assert.rejects(parseNotification(fixture(entry(), `xmlns:evil="https://evil.example/"`), OTHER), /wrong_channel/);
  const spoof = new TextDecoder().decode(fixture(entry())).replace(`xmlns:yt="${YT}"`, 'xmlns:yt="https://evil.example/"');
  await assert.rejects(parseNotification(encoder.encode(spoof), CHANNEL), /invalid_video_id/);
});

test("fingerprint ignores envelope, description and updated-only changes but tracks title/publication", async () => {
  const first = (await parseNotification(fixture(entry()), CHANNEL))[0];
  const changedEnvelope = fixture(`<updated>2026-11-05T12:00:00Z</updated>${entry(VID, undefined, CHANNEL, "2026-11-05T12:00:00Z")}`
    .replace("</entry>", "<summary>Descrição nova</summary></entry>"));
  assert.equal((await parseNotification(changedEnvelope, CHANNEL))[0].fingerprint, first.fingerprint);
  assert.notEqual((await parseNotification(fixture(entry(VID, "Título novo")), CHANNEL))[0].fingerprint, first.fingerprint);
  const changedPublished = fixture(entry().replace("<published>2026-11-04T12:00:00Z", "<published>2026-11-03T12:00:00Z"));
  assert.notEqual((await parseNotification(changedPublished, CHANNEL))[0].fingerprint, first.fingerprint);
});

test("duplicate entries coalesce and two distinct videos remain distinct", async () => {
  const result = await parseNotification(fixture(entry() + entry() + entry("ID000000002")), CHANNEL);
  assert.deepEqual(result.map((event) => event.videoId), [VID, "ID000000002"]);
});

test("malformed XML, unsafe declarations and invalid UTF-8 have bounded safe errors", async () => {
  const inputs = [encoder.encode("<broken>private-token"),
    encoder.encode(`<!DOCTYPE feed [<!ENTITY x "SECRET_VALUE">]><feed xmlns="${ATOM}">&x;</feed>`),
    fixture(entry("invalid")), fixture(entry(VID, "Title", OTHER)),
    fixture(entry().replace("2026-11-04T12:00:00Z", "2026-02-30T12:00:00Z")),
    new Uint8Array([0xff, 0xfe]), new Uint8Array(0), new Uint8Array(MAX_PAYLOAD_BYTES + 1)];
  for (const input of inputs) {
    await assert.rejects(parseNotification(input, CHANNEL), (error: unknown) => {
      assert(error instanceof WebSubError);
      assert.doesNotMatch(error.message, /SECRET_VALUE|private-token|<broken>/);
      return true;
    });
  }
});

test("required fields, duplicate fields, nested title markup and root channel mismatch fail closed", async () => {
  for (const body of [fixture(entry().replace(`<yt:channelId>${CHANNEL}</yt:channelId>`, "")),
    fixture(entry().replace("</title>", "</title><title>ambiguous</title>")),
    fixture(entry(VID, "<span>nested</span>")),
    fixture(`<id>yt:channel:${OTHER}</id>${entry()}`),
    fixture(entry().replace(/<updated>.*?<\/updated>/, "")), fixture("")]) {
    await assert.rejects(parseNotification(body, CHANNEL), WebSubError);
  }
});

test("payload, XML nesting, entries and title limits are enforced", async () => {
  const deep = fixture("<x>".repeat(17) + "</x>".repeat(17));
  await assert.rejects(parseNotification(deep, CHANNEL), /xml_depth/);
  await assert.rejects(parseNotification(fixture(entry().repeat(51)), CHANNEL), /entry_limit/);
  await assert.rejects(parseNotification(fixture(entry(VID, "x".repeat(1001))), CHANNEL), /field_too_large/);
});

test("explicit tombstones are ignored without inventing video dispatch", async () => {
  const body = fixture(`<at:deleted-entry ref="yt:video:${VID}" when="2026-11-04T12:00:00Z"/>`,
    'xmlns:at="http://purl.org/atompub/tombstones/1.0"');
  assert.deepEqual(await parseNotification(body, CHANNEL), []);
  await assert.rejects(parseNotification(fixture("<deleted-entry/>"), CHANNEL), /empty_notification/);
});

const challenge = (extra: Record<string, string> = {}) => new URLSearchParams({
  "hub.mode": "subscribe", "hub.topic": TOPIC, "hub.challenge": "safe-challenge_+/.=123",
  "hub.verify_token": TOKEN, "hub.lease_seconds": "864000", ...extra,
});

test("valid challenge parses mode, topic, token and actual lease", () => {
  const result = parseChallenge(challenge(), TOPIC)!;
  assert.equal(result.verifyToken, TOKEN);
  assert.equal(result.leaseSeconds, 864000);
  assert.equal(result.challenge, "safe-challenge_+/.=123");
});

test("challenge rejects wrong topic, unsupported modes, controls, duplicates, absent token and invalid lease", () => {
  for (const bad of [challenge({ "hub.topic": TOPIC.replace(CHANNEL, OTHER) }), challenge({ "hub.mode": "denied" }),
    challenge({ "hub.challenge": "<script>" }), challenge({ "hub.challenge": "x".repeat(1025) }),
    challenge({ "hub.challenge": "bad\nvalue" }), challenge({ "hub.verify_token": "" }),
    challenge({ "hub.lease_seconds": "59" }), challenge({ "hub.lease_seconds": "2592001" }),
    challenge({ "hub.lease_seconds": "not-a-number" }), challenge({ "hub.lease_seconds": "" })]) {
    assert.equal(parseChallenge(bad, TOPIC), null);
  }
  const duplicate = challenge(); duplicate.append("hub.topic", TOPIC);
  assert.equal(parseChallenge(duplicate, TOPIC), null);
  const missing = challenge(); missing.delete("hub.verify_token");
  assert.equal(parseChallenge(missing, TOPIC), null);
});

test("unsubscribe lease is optional and ignored according to the protocol", () => {
  const value = challenge({ "hub.mode": "unsubscribe", "hub.lease_seconds": "0" });
  assert.equal(parseChallenge(value, TOPIC)?.leaseSeconds, null);
  value.delete("hub.lease_seconds");
  assert.equal(parseChallenge(value, TOPIC)?.mode, "unsubscribe");
});

function env(extra: Partial<Env> = {}): Env {
  return { CHANNEL_ID: CHANNEL, CALLBACK_URL: "https://hooks.ozecaoo.com/youtube", WEBSUB_SECRET: SECRET, ...extra } as Env;
}

test("subscription sends fixed HTTPS hub and protocol parameters; 202 and 204 are only pending ACKs", async () => {
  const original = globalThis.fetch;
  try {
    for (const status of [202, 204]) {
      globalThis.fetch = async (input, init) => {
        assert.equal(input, HUB);
        assert.equal(init?.method, "POST");
        assert.equal(init?.redirect, "manual");
        assert(init?.signal instanceof AbortSignal);
        const form = new URLSearchParams(String(init?.body));
        assert.equal(form.get("hub.mode"), "subscribe");
        assert.equal(form.get("hub.topic"), TOPIC);
        assert.equal(form.get("hub.callback"), "https://hooks.ozecaoo.com/youtube");
        assert.equal(form.get("hub.secret"), SECRET);
        assert.equal(form.get("hub.verify"), "async");
        assert.equal(form.get("hub.verify_token"), TOKEN);
        assert.equal(form.get("hub.lease_seconds"), "864000");
        return new Response(null, { status });
      };
      const result = await requestSubscription(env(), "subscribe", TOKEN);
      assert.deepEqual(result, { accepted: true, status, reason: "accepted_pending_verification" });
      assert.doesNotMatch(JSON.stringify(result), new RegExp(`${SECRET}|${TOKEN}`));
    }
  } finally { globalThis.fetch = original; }
});

test("edge-compatible manual redirects never forward the hub secret", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  try {
    globalThis.fetch = async (input, init) => {
      calls++;
      assert.equal(String(input), HUB);
      assert.equal(init?.redirect, "manual");
      return new Response(null, { status: 302, headers: { Location: "https://untrusted.invalid/" } });
    };
    assert.deepEqual(await requestSubscription(env(), "subscribe", TOKEN),
      { accepted: false, status: 302, reason: "hub_rejected" });
    assert.equal(calls, 1);
  } finally { globalThis.fetch = original; }
});

test("unsubscribe omits lease and failures expose only bounded reason codes", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (_input, init) => {
      const body = new URLSearchParams(String(init?.body));
      assert.equal(body.get("hub.mode"), "unsubscribe");
      assert.equal(body.has("hub.lease_seconds"), false);
      return new Response(`${SECRET} ${TOKEN}`, { status: 403 });
    };
    assert.deepEqual(await requestSubscription(env(), "unsubscribe", TOKEN),
      { accepted: false, status: 403, reason: "hub_rejected" });
    globalThis.fetch = async () => { throw new Error(`${SECRET} ${TOKEN} private URL`); };
    assert.deepEqual(await requestSubscription(env(), "subscribe", TOKEN),
      { accepted: false, status: 0, reason: "hub_network_error" });
  } finally { globalThis.fetch = original; }
});

test("unsafe subscription configuration is rejected before any request", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => { assert.fail("must not request the hub"); };
    for (const config of [env({ CHANNEL_ID: OTHER }), env({ WEBSUB_SECRET: "" }),
      env({ WEBSUB_SECRET: "x".repeat(200) }), env({ CALLBACK_URL: "http://hooks.ozecaoo.com/youtube" }),
      env({ CALLBACK_URL: "https://user:pass@hooks.ozecaoo.com/youtube" }),
      env({ CALLBACK_URL: "https://hooks.ozecaoo.com/youtube?hub.mode=subscribe" })]) {
      assert.equal((await requestSubscription(config, "subscribe", TOKEN)).accepted, false);
    }
  } finally { globalThis.fetch = original; }
});

test("subscription timeout is bounded, aborts transport and never reports raw errors", async () => {
  const originalFetch = globalThis.fetch;
  const originalTimer = globalThis.setTimeout;
  const originalClear = globalThis.clearTimeout;
  let timerCleared = false;
  try {
    globalThis.setTimeout = ((callback: () => void, delay: number) => {
      assert.equal(delay, 8_000);
      callback();
      return 123;
    }) as unknown as typeof globalThis.setTimeout;
    globalThis.clearTimeout = (() => { timerCleared = true; }) as typeof globalThis.clearTimeout;
    globalThis.fetch = async (_input, init) => {
      assert.equal(init?.signal?.aborted, true);
      throw new Error(`${SECRET} ${TOKEN}`);
    };
    assert.deepEqual(await requestSubscription(env(), "subscribe", TOKEN),
      { accepted: false, status: 0, reason: "hub_timeout" });
    assert.equal(timerCleared, true);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalTimer;
    globalThis.clearTimeout = originalClear;
  }
});
