/** Cross-isolate session + rate-limit store (Deno KV, no API keys). */

export const SESSION_PREFIX = "mcp-session";
export const RATE_PREFIX = "mcp-rate";

const DEFAULT_SESSION_TTL_SECONDS = 1800;
const DEFAULT_SESSION_MAX = 256;
const DEFAULT_WIKI_FETCH_LIMIT_PER_MINUTE = 30;

let kvPromise: Promise<Deno.Kv> | null = null;

export function kvPath(): string | undefined {
  const path = Deno.env.get("MCP_KV_PATH")?.trim();
  return path || undefined;
}

export async function getKv(): Promise<Deno.Kv> {
  if (!kvPromise) {
    const path = kvPath();
    kvPromise = path ? Deno.openKv(path) : Deno.openKv();
  }
  return await kvPromise;
}

export async function closeKv(): Promise<void> {
  if (!kvPromise) return;
  const kv = await kvPromise;
  kv.close();
  kvPromise = null;
}

function envInt(name: string, fallback: number, min = 1): number {
  const raw = Number(Deno.env.get(name) || "");
  if (!Number.isFinite(raw)) return fallback;
  return Math.max(min, Math.floor(raw));
}

export function sessionTtlMs(): number {
  return envInt("MCP_SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS) * 1000;
}

export function sessionMax(): number {
  return envInt("MCP_SESSION_MAX", DEFAULT_SESSION_MAX);
}

export function wikiFetchLimitPerMinute(): number {
  return envInt("MCP_WIKI_FETCH_LIMIT_PER_MINUTE", DEFAULT_WIKI_FETCH_LIMIT_PER_MINUTE, 0);
}

export async function mintSession(): Promise<string> {
  const kv = await getKv();
  await evictOldestIfNeeded(kv);
  const id = `sess_${crypto.randomUUID().replaceAll("-", "")}`;
  const createdAt = Date.now();
  await kv.set([SESSION_PREFIX, id], { createdAt }, { expireIn: sessionTtlMs() });
  return id;
}

export async function sessionValid(id: string | null): Promise<boolean> {
  if (!id) return false;
  const kv = await getKv();
  const got = await kv.get<{ createdAt?: number }>([SESSION_PREFIX, id]);
  if (got.value == null) return false;
  const createdAt = Number(got.value.createdAt) || 0;
  if (!createdAt || Date.now() - createdAt > sessionTtlMs()) {
    await kv.delete([SESSION_PREFIX, id]);
    return false;
  }
  return true;
}

async function evictOldestIfNeeded(kv: Deno.Kv): Promise<void> {
  const max = sessionMax();
  const rows: Array<{ key: Deno.KvKey; createdAt: number }> = [];
  for await (const entry of kv.list({ prefix: [SESSION_PREFIX] })) {
    const value = entry.value as { createdAt?: number } | null;
    rows.push({ key: entry.key, createdAt: Number(value?.createdAt) || 0 });
  }
  rows.sort((a, b) => a.createdAt - b.createdAt);
  while (rows.length >= max) {
    const oldest = rows.shift();
    if (oldest) await kv.delete(oldest.key);
  }
}

export function clientKey(req: Request): string {
  const forwarded = req.headers.get("x-forwarded-for") || req.headers.get("cf-connecting-ip") || "";
  const first = forwarded.split(",")[0]?.trim();
  return first || "local";
}

/** Return false when this client has exceeded the per-minute Wikipedia fetch cap. */
export async function allowWikiFetch(req: Request): Promise<boolean> {
  const limit = wikiFetchLimitPerMinute();
  if (limit <= 0) return true;
  const kv = await getKv();
  const window = Math.floor(Date.now() / 60_000);
  const key: Deno.KvKey = [RATE_PREFIX, window, clientKey(req)];
  const got = await kv.get<number>(key);
  const next = (got.value ?? 0) + 1;
  if (next > limit) return false;
  await kv.set(key, next, { expireIn: 60_000 });
  return true;
}
