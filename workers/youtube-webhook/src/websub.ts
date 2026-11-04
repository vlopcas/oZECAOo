import { SaxesParser, type SaxesTagNS } from "saxes";
import {
  CHANNEL, HUB, MAX_PAYLOAD_BYTES, TOPIC, log,
  type Challenge, type Env, type VideoEvent,
} from "./types.ts";

const ATOM = "http://www.w3.org/2005/Atom";
const YOUTUBE = "http://www.youtube.com/xml/schemas/2015";
const TOMBSTONE = "http://purl.org/atompub/tombstones/1.0";
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const CHANNEL_ID = /^UC[A-Za-z0-9_-]{22}$/;
const VERIFY_TOKEN = /^[A-Za-z0-9_-]{1,256}$/;
const MAX_DEPTH = 16;
const MAX_ENTRIES = 50;
const MAX_TITLE = 1_000;
const encoder = new TextEncoder();

export class WebSubError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "WebSubError";
  }
}

function fail(code: string): never {
  throw new WebSubError(code);
}

function bytes(body: Uint8Array): ArrayBuffer {
  return Uint8Array.from(body).buffer;
}

function hex(value: ArrayBuffer): string {
  return Array.from(new Uint8Array(value), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Authenticate the exact delivered bytes, before XML decoding or parsing. */
export async function verifySignature(body: Uint8Array, signature: string | null, secret: string): Promise<boolean> {
  if (body.byteLength > MAX_PAYLOAD_BYTES || !signature || !secret || encoder.encode(secret).byteLength >= 200) return false;
  const match = /^(sha1|sha256)=([a-fA-F0-9]+)$/.exec(signature);
  if (!match || match[2].length !== (match[1] === "sha1" ? 40 : 64)) return false;
  const digest = Uint8Array.from(match[2].match(/.{2}/g)!, (pair) => Number.parseInt(pair, 16));
  try {
    const key = await crypto.subtle.importKey(
      "raw", bytes(encoder.encode(secret)),
      { name: "HMAC", hash: match[1] === "sha1" ? "SHA-1" : "SHA-256" }, false, ["verify"],
    );
    return await crypto.subtle.verify("HMAC", key, bytes(digest), bytes(body));
  } catch {
    return false;
  }
}

/** RFC 3339 timestamps from the YouTube Atom contract, normalized for hashing. */
function date(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match) return fail("invalid_date");
  const [, year, month, day, hour, minute, second, zone] = match;
  const numbers = [year, month, day, hour, minute, second].map(Number);
  const [y, m, d, h, min, sec] = numbers;
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  if (m < 1 || m > 12 || d < 1 || d > days || h > 23 || min > 59 || sec > 59) return fail("invalid_date");
  if (zone !== "Z" && (Number(zone.slice(1, 3)) > 23 || Number(zone.slice(4)) > 59)) return fail("invalid_date");
  const instant = Date.parse(value);
  if (!Number.isFinite(instant)) return fail("invalid_date");
  return new Date(instant).toISOString();
}

type Field = "videoId" | "channelId" | "title" | "published" | "updated";
type Entry = Partial<Record<Field, string>>;
type Tag = Pick<SaxesTagNS, "uri" | "local">;

/**
 * Parse a signed, bounded Atom notification. Prefix spelling is irrelevant;
 * namespace URI, direct-child location, channel ownership and IDs are not.
 */
export async function parseNotification(body: Uint8Array, channelId: string): Promise<VideoEvent[]> {
  if (!CHANNEL_ID.test(channelId)) return fail("invalid_channel");
  if (!body.byteLength || body.byteLength > MAX_PAYLOAD_BYTES) return fail("payload_size");
  let document: string;
  try {
    document = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(body);
  } catch {
    return fail("invalid_utf8");
  }
  // Reject declarations even before the parser sees them. No external entity
  // fetch, user-defined expansion or DTD is needed for a YouTube notification.
  if (/<!\s*(?:DOCTYPE|ENTITY)\b/i.test(document)) return fail("forbidden_xml_declaration");

  const stack: Tag[] = [];
  const entries: Entry[] = [];
  let current: Entry | null = null;
  let activeField: Field | null = null;
  let text = "";
  let feedField: "id" | "channelId" | null = null;
  let feedText = "";
  let tombstones = 0;
  let rootSeen = false;
  const parser = new SaxesParser({ xmlns: true });
  const field = (tag: Tag): Field | null => {
    if (tag.uri === YOUTUBE && (tag.local === "videoId" || tag.local === "channelId")) return tag.local;
    if (tag.uri === ATOM && (tag.local === "title" || tag.local === "published" || tag.local === "updated")) return tag.local;
    return null;
  };
  const addText = (value: string): void => {
    if (activeField) {
      text += value;
      if (text.length > (activeField === "title" ? MAX_TITLE : 128)) fail("field_too_large");
    } else if (feedField) {
      feedText += value;
      if (feedText.length > 128) fail("field_too_large");
    }
  };
  parser.on("error", () => fail("invalid_xml"));
  parser.on("doctype", () => fail("forbidden_xml_declaration"));
  parser.on("processinginstruction", () => fail("forbidden_xml_instruction"));
  parser.on("text", addText);
  parser.on("cdata", addText);
  parser.on("opentag", (tag) => {
    if (stack.length >= MAX_DEPTH) fail("xml_depth");
    if (activeField || feedField) fail("nested_field");
    stack.push({ uri: tag.uri, local: tag.local });
    if (stack.length === 1) {
      if (tag.uri !== ATOM || tag.local !== "feed" || rootSeen) fail("invalid_feed");
      rootSeen = true;
    }
    if (tag.uri === ATOM && tag.local === "entry") {
      if (stack.length !== 2 || current) fail("invalid_entry_location");
      if (entries.length >= MAX_ENTRIES) fail("entry_limit");
      current = {};
    } else if (tag.uri === TOMBSTONE && tag.local === "deleted-entry") {
      if (stack.length !== 2 || ++tombstones > MAX_ENTRIES) fail("invalid_tombstone");
      const ref = tag.attributes.ref?.value;
      if (!ref?.startsWith("yt:video:") || !VIDEO_ID.test(ref.slice(9))) fail("invalid_tombstone");
    } else if (current && stack.length === 3) {
      activeField = field(tag);
      if (activeField) {
        if (current[activeField] !== undefined) fail("duplicate_field");
        text = "";
      }
    } else if (!current && stack.length === 2) {
      if (tag.uri === ATOM && tag.local === "id") feedField = "id";
      if (tag.uri === YOUTUBE && tag.local === "channelId") feedField = "channelId";
      feedText = "";
    }
  });
  parser.on("closetag", (tag) => {
    if (activeField && stack.length === 3 && current) {
      current[activeField] = text.trim();
      activeField = null;
    }
    if (feedField && stack.length === 2) {
      const value = feedText.trim();
      if ((feedField === "channelId" && value !== channelId)
          || (feedField === "id" && value.startsWith("yt:channel:") && value !== `yt:channel:${channelId}`)) fail("wrong_channel");
      feedField = null;
    }
    if (tag.uri === ATOM && tag.local === "entry" && stack.length === 2) {
      if (!current) fail("invalid_entry");
      entries.push(current);
      current = null;
    }
    stack.pop();
  });
  try {
    parser.write(document).close();
  } catch (error) {
    if (error instanceof WebSubError) throw error;
    return fail("invalid_xml");
  }
  if (!rootSeen || stack.length || current) return fail("invalid_xml");
  if (!entries.length && !tombstones) return fail("empty_notification");
  if (tombstones) log("websub_tombstones_ignored", { count: tombstones });

  const result: VideoEvent[] = [];
  const seen = new Set<string>();
  for (const entry of entries) {
    if (!entry.videoId || !VIDEO_ID.test(entry.videoId)) fail("invalid_video_id");
    if (entry.channelId !== channelId) fail("wrong_channel");
    if (!entry.title || entry.title.length > MAX_TITLE) fail("invalid_title");
    if (!entry.published || !entry.updated) fail("missing_date");
    const published = date(entry.published);
    const updated = date(entry.updated);
    const title = entry.title.trim();
    const fingerprint = hex(await crypto.subtle.digest("SHA-256", bytes(encoder.encode(
      JSON.stringify([entry.videoId, channelId, title, published]),
    ))));
    if (seen.has(fingerprint)) continue;
    seen.add(fingerprint);
    result.push({ videoId: entry.videoId, channelId, title, published, updated, fingerprint });
  }
  return result;
}

/** Syntax validation only; the coordinator validates the pending token/state. */
export function parseChallenge(search: URLSearchParams, topic: string): Challenge | null {
  if (search.toString().length > 8_192) return null;
  const allowed = new Set(["hub.mode", "hub.topic", "hub.challenge", "hub.verify_token", "hub.lease_seconds"]);
  for (const key of search.keys()) {
    if (search.getAll(key).length !== 1 || (key.startsWith("hub.") && !allowed.has(key))) return null;
  }
  const mode = search.get("hub.mode");
  const receivedTopic = search.get("hub.topic");
  const challenge = search.get("hub.challenge");
  const verifyToken = search.get("hub.verify_token");
  if ((mode !== "subscribe" && mode !== "unsubscribe") || receivedTopic !== topic
      || !challenge || !/^[A-Za-z0-9_+./=\-]{1,1024}$/.test(challenge)
      || !verifyToken || !VERIFY_TOKEN.test(verifyToken)) return null;
  let leaseSeconds: number | null = null;
  if (mode === "subscribe") {
    const value = search.get("hub.lease_seconds");
    if (!value || !/^[1-9]\d{0,6}$/.test(value)) return null;
    leaseSeconds = Number(value);
    if (leaseSeconds < 60 || leaseSeconds > 2_592_000) return null;
  }
  return { mode, topic: receivedTopic, challenge, verifyToken, leaseSeconds };
}

/** 2xx means the request was accepted; activation requires the GET handshake. */
export async function requestSubscription(env: Env, mode: "subscribe" | "unsubscribe", verifyToken: string): Promise<{ accepted: boolean; status: number; reason: string }> {
  const invalid = { accepted: false, status: 0, reason: "invalid_subscription_configuration" };
  if (env.CHANNEL_ID !== CHANNEL || !VERIFY_TOKEN.test(verifyToken)
      || !env.WEBSUB_SECRET || encoder.encode(env.WEBSUB_SECRET).byteLength >= 200
      || (mode !== "subscribe" && mode !== "unsubscribe")) return invalid;
  let callback: URL;
  try {
    callback = new URL(env.CALLBACK_URL);
    if (callback.protocol !== "https:" || callback.username || callback.password || callback.port
        || callback.hash || [...callback.searchParams.keys()].some((key) => key.startsWith("hub."))) return invalid;
  } catch {
    return invalid;
  }
  const body = new URLSearchParams({
    "hub.mode": mode, "hub.topic": TOPIC, "hub.callback": callback.href,
    "hub.verify": "async", "hub.verify_token": verifyToken, "hub.secret": env.WEBSUB_SECRET,
  });
  if (mode === "subscribe") body.set("hub.lease_seconds", "864000");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8_000);
  try {
    const response = await fetch(HUB, {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body, redirect: "error", signal: controller.signal,
    });
    const status = response.status;
    await response.body?.cancel();
    if (status >= 200 && status < 300) return { accepted: true, status, reason: "accepted_pending_verification" };
    return { accepted: false, status, reason: status === 429 || status >= 500 ? "hub_unavailable" : "hub_rejected" };
  } catch {
    return { accepted: false, status: 0, reason: controller.signal.aborted ? "hub_timeout" : "hub_network_error" };
  } finally {
    clearTimeout(timer);
  }
}
