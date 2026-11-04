import { ChannelCoordinator } from "./coordinator.ts";
import { parseChallenge, parseNotification, verifySignature } from "./websub.ts";
import { CHANNEL, MAX_PAYLOAD_BYTES, TOPIC, log, type Env } from "./types.ts";

export { ChannelCoordinator };

function reply(body: string, status: number, type = "text/plain"): Response {
  return new Response(status === 204 ? null : body, { status, headers: {
    "Content-Type": `${type}; charset=utf-8`, "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
  } });
}

async function readBounded(request: Request, limit: number): Promise<Uint8Array> {
  const declared = request.headers.get("content-length");
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > limit)) throw new Error("payload_size");
  if (!request.body) return new Uint8Array();
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new Error("payload_size"); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}

async function authenticated(request: Request, token: string): Promise<boolean> {
  if (!token || token.length < 32) return false;
  const supplied = request.headers.get("authorization");
  if (!supplied?.startsWith("Bearer ") || supplied.length > 600) return false;
  const encoder = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(token)),
    crypto.subtle.digest("SHA-256", encoder.encode(supplied.slice(7))),
  ]);
  const left = new Uint8Array(a), right = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < left.length; i++) diff |= left[i] ^ right[i];
  return diff === 0;
}

function stub(env: Env): DurableObjectStub {
  return env.COORDINATOR.get(env.COORDINATOR.idFromName(CHANNEL));
}

async function internal(env: Env, path: string, body?: unknown): Promise<Response> {
  return stub(env).fetch(`https://coordinator${path}`, body === undefined
    ? { method: "GET" }
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.protocol !== "https:" && url.hostname !== "localhost" && url.hostname !== "127.0.0.1") {
      return reply("HTTPS required", 400);
    }
    if (url.pathname === "/health" && request.method === "GET") {
      return reply(JSON.stringify({ service: "ozecaoo-youtube-webhook", ok: true }), 200, "application/json");
    }
    if (env.CHANNEL_ID !== CHANNEL) return reply("Service configuration unavailable", 503);
    try {
      if (url.pathname === "/youtube" && request.method === "GET") {
        const challenge = parseChallenge(url.searchParams, TOPIC);
        if (!challenge) return reply("Unknown verification", 404);
        return await internal(env, "/challenge", challenge);
      }
      if (url.pathname === "/youtube" && request.method === "POST") {
        if (!env.WEBSUB_SECRET || env.WEBSUB_SECRET.length < 32) return reply("Service unavailable", 503);
        const body = await readBounded(request, MAX_PAYLOAD_BYTES);
        if (!await verifySignature(body, request.headers.get("x-hub-signature"), env.WEBSUB_SECRET)) {
          log("notification_rejected", { reason: "signature_invalid" });
          return reply("Invalid authentication", 403);
        }
        let events;
        try { events = await parseNotification(body, CHANNEL); }
        catch { log("notification_rejected", { reason: "xml_invalid" }); return reply("Invalid notification", 400); }
        if (!events.length) return reply("", 204);
        // Persist first; never ACK a notification that only exists in waitUntil memory.
        const response = await internal(env, "/events", { events });
        if (!response.ok) return reply("Temporary persistence failure", 503);
        return reply("", 204);
      }
      if (url.pathname.startsWith("/admin/")) {
        if (!await authenticated(request, env.ADMIN_TOKEN)) return reply("Unauthorized", 401);
        if (url.pathname === "/admin/status" && request.method === "GET") return await internal(env, "/status");
        if (request.method !== "POST") return reply("Method not allowed", 405);
        const routes: Record<string, string> = {
          "/admin/subscribe": "/subscribe", "/admin/unsubscribe": "/unsubscribe",
          "/admin/maintenance": "/tick", "/admin/test-dispatch": "/test-dispatch",
        };
        const target = routes[url.pathname];
        if (!target) return reply("Not found", 404);
        const bytes = await readBounded(request, 4096);
        let body: unknown = {};
        if (bytes.length) {
          try { body = JSON.parse(new TextDecoder().decode(bytes)); } catch { return reply("Invalid JSON", 400); }
        }
        if (target === "/test-dispatch") {
          const ids = (body as { videoIds?: unknown })?.videoIds;
          if (!Array.isArray(ids) || !ids.length || ids.length > 50 || ids.some(id => typeof id !== "string" || !/^[A-Za-z0-9_-]{11}$/.test(id))) {
            return reply("Valid videoIds required", 400);
          }
          body = { videoIds: [...new Set(ids)] };
        } else { body = {}; }
        return await internal(env, target, body);
      }
      return reply("Not found", 404);
    } catch (error) {
      const tooLarge = error instanceof Error && error.message === "payload_size";
      log("request_failed", { reason: tooLarge ? "payload_size" : "storage_unavailable" });
      return reply(tooLarge ? "Payload too large" : "Temporary service failure", tooLarge ? 413 : 503);
    }
  },
  async scheduled(_controller: ScheduledController, env: Env, context: ExecutionContext): Promise<void> {
    context.waitUntil(internal(env, "/tick", {}).then(response => {
      if (!response.ok) log("maintenance_failed", { reason: "coordinator_unavailable" });
    }).catch(() => log("maintenance_failed", { reason: "storage_unavailable" })));
  },
} satisfies ExportedHandler<Env>;
