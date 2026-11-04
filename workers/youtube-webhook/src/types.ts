export interface Env {
  COORDINATOR: DurableObjectNamespace;
  CHANNEL_ID: string;
  CALLBACK_URL: string;
  GITHUB_OWNER: string;
  GITHUB_REPO: string;
  GITHUB_WORKFLOW: string;
  GITHUB_REF: string;
  DISPATCH_ENABLED: string;
  WEBSUB_SECRET: string;
  ADMIN_TOKEN: string;
  GITHUB_TOKEN: string;
}

export interface VideoEvent {
  videoId: string;
  channelId: string;
  title: string;
  published: string;
  updated: string;
  fingerprint: string;
}

export interface Challenge {
  mode: "subscribe" | "unsubscribe";
  topic: string;
  challenge: string;
  verifyToken: string;
  leaseSeconds: number | null;
}

export const CHANNEL = "UCkS1cj-1C9ahgYVvFGz86qw";
export const REPOSITORY = "vlopcas/oZECAOo";
export const HUB = "https://pubsubhubbub.appspot.com/";
export const MAX_PAYLOAD_BYTES = 65_536;
export const TOPIC = `https://www.youtube.com/xml/feeds/videos.xml?channel_id=${CHANNEL}`;

export function log(event: string, fields: Record<string, string | number | boolean | null> = {}): void {
  // Callers supply only reason codes and public identifiers, never request/body/headers.
  console.log(JSON.stringify({ event, channel_id: CHANNEL, timestamp: new Date().toISOString(), ...fields }));
}
