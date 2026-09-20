/** HMAC-signed short-TTL MCP session tokens. Stateless across Deno Deploy isolates. */

export const SESSION_PREFIX = "sess_";
export const SESSION_SECRET_ENV = "MCP_SESSION_SECRET";
export const SESSION_TTL_ENV = "MCP_SESSION_TTL_SECONDS";
export const DEFAULT_SESSION_TTL_SECONDS = 3600;

const encoder = new TextEncoder();

/** Process-local fallback when MCP_SESSION_SECRET is unset (local/dev/tests only). */
let processLocalSecret: Uint8Array | undefined;

export type MintSessionOptions = {
  secret?: Uint8Array;
  ttlSeconds?: number;
  nowSeconds?: number;
  nonce?: Uint8Array;
};

export type VerifySessionOptions = {
  secret?: Uint8Array;
  nowSeconds?: number;
};

function unixNowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function envGet(name: string): string | undefined {
  try {
    return Deno.env.get(name);
  } catch {
    return undefined;
  }
}

export function defaultSessionTtlSeconds(): number {
  const raw = envGet(SESSION_TTL_ENV)?.trim();
  if (!raw) return DEFAULT_SESSION_TTL_SECONDS;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_SESSION_TTL_SECONDS;
  return Math.floor(parsed);
}

/**
 * Secret material for HMAC.
 *
 * Deploy must set `MCP_SESSION_SECRET` so every isolate shares the same key.
 * If unset, a random 32-byte secret is derived once per process — fine for
 * local/dev and tests, not for production (tokens would not verify on another isolate).
 * Never hardcode a production secret here.
 */
export function sessionSecretBytes(secret = envGet(SESSION_SECRET_ENV)): Uint8Array {
  const trimmed = secret?.trim();
  if (trimmed) return encoder.encode(trimmed);
  if (!processLocalSecret) {
    processLocalSecret = crypto.getRandomValues(new Uint8Array(32));
  }
  return processLocalSecret;
}

function b64url(bytes: Uint8Array): string {
  let bin = "";
  for (const byte of bytes) bin += String.fromCharCode(byte);
  return btoa(bin).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function b64urlDecode(text: string): Uint8Array | null {
  try {
    const padded = text.replaceAll("-", "+").replaceAll("_", "/");
    const pad = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
    const bin = atob(padded + pad);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

function bytesToBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(copy).set(bytes);
  return copy;
}

async function hmacSha256(secret: Uint8Array, message: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    bytesToBuffer(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, bytesToBuffer(encoder.encode(message)));
  return new Uint8Array(mac);
}

function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

/** Token shape: `sess_<nonce>.<expUnixSeconds>.<mac>` — no server-side session store. */
export async function mintSessionToken(options: MintSessionOptions = {}): Promise<string> {
  const secret = options.secret ?? sessionSecretBytes();
  const ttl = options.ttlSeconds ?? defaultSessionTtlSeconds();
  const now = options.nowSeconds ?? unixNowSeconds();
  const nonce = options.nonce ?? crypto.getRandomValues(new Uint8Array(16));
  const exp = now + Math.max(1, Math.floor(ttl));
  const body = `${b64url(nonce)}.${exp}`;
  const mac = await hmacSha256(secret, body);
  return `${SESSION_PREFIX}${body}.${b64url(mac)}`;
}

/**
 * Verify signature + TTL. Does not consult any in-memory Set; any isolate with
 * the same secret can validate the token.
 */
export async function verifySessionToken(
  token: string,
  options: VerifySessionOptions = {},
): Promise<boolean> {
  if (typeof token !== "string" || !token.startsWith(SESSION_PREFIX)) return false;
  const rest = token.slice(SESSION_PREFIX.length);
  const parts = rest.split(".");
  if (parts.length !== 3) return false;
  const [nonceB64, expRaw, macB64] = parts;
  if (!nonceB64 || !expRaw || !macB64) return false;
  if (!/^[0-9]+$/.test(expRaw)) return false;
  const nonce = b64urlDecode(nonceB64);
  const mac = b64urlDecode(macB64);
  if (!nonce || !mac || nonce.length === 0 || mac.length === 0) return false;
  if (b64url(nonce) !== nonceB64 || b64url(mac) !== macB64) return false;
  const secret = options.secret ?? sessionSecretBytes();
  const expected = await hmacSha256(secret, `${nonceB64}.${expRaw}`);
  if (!timingSafeEqual(mac, expected)) return false;
  const exp = Number(expRaw);
  const now = options.nowSeconds ?? unixNowSeconds();
  return Number.isFinite(exp) && exp > now;
}
