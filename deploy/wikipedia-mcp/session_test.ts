import {
  mintSessionToken,
  SESSION_PREFIX,
  verifySessionToken,
} from "./session.ts";

function assertEquals(actual: unknown, expected: unknown) {
  const same = actual === expected || JSON.stringify(actual) === JSON.stringify(expected);
  if (!same) {
    throw new Error(`Expected ${Deno.inspect(expected)}, got ${Deno.inspect(actual)}`);
  }
}

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

const SECRET_A = new TextEncoder().encode("lab-test-session-secret-a");
const SECRET_B = new TextEncoder().encode("lab-test-session-secret-b");
const NOW = 1_700_000_000;

Deno.test("minted token is accepted without an in-memory Set", async () => {
  const token = await mintSessionToken({ secret: SECRET_A, nowSeconds: NOW, ttlSeconds: 60 });
  assert(token.startsWith(SESSION_PREFIX), "token must use sess_ prefix");
  assertEquals(await verifySessionToken(token, { secret: SECRET_A, nowSeconds: NOW + 1 }), true);
  // A second verifier with the same secret and no shared store still accepts it.
  assertEquals(await verifySessionToken(token, { secret: SECRET_A, nowSeconds: NOW + 30 }), true);
});

Deno.test("tampered token is rejected", async () => {
  const token = await mintSessionToken({ secret: SECRET_A, nowSeconds: NOW, ttlSeconds: 60 });
  const flippedMac = token.replace(/\.[^.]+$/, (mac) => {
    const body = mac.slice(1);
    return `.${body.startsWith("A") ? "B" : "A"}${body.slice(1)}`;
  });
  assertEquals(await verifySessionToken(flippedMac, { secret: SECRET_A, nowSeconds: NOW }), false);

  const parts = token.split(".");
  parts[1] = String(Number(parts[1]) + 10_000);
  const tweakedExp = parts.join(".");
  assertEquals(await verifySessionToken(tweakedExp, { secret: SECRET_A, nowSeconds: NOW }), false);
});

Deno.test("expired token is rejected even with a valid MAC", async () => {
  const token = await mintSessionToken({ secret: SECRET_A, nowSeconds: NOW, ttlSeconds: 60 });
  assertEquals(await verifySessionToken(token, { secret: SECRET_A, nowSeconds: NOW + 60 }), false);
  assertEquals(await verifySessionToken(token, { secret: SECRET_A, nowSeconds: NOW + 61 }), false);
});

Deno.test("token signed with a different secret is rejected", async () => {
  const token = await mintSessionToken({ secret: SECRET_A, nowSeconds: NOW, ttlSeconds: 60 });
  assertEquals(await verifySessionToken(token, { secret: SECRET_B, nowSeconds: NOW }), false);
});

Deno.test("malformed tokens are rejected", async () => {
  assertEquals(await verifySessionToken("", { secret: SECRET_A, nowSeconds: NOW }), false);
  assertEquals(await verifySessionToken("sess_not-a-token", { secret: SECRET_A, nowSeconds: NOW }), false);
  assertEquals(await verifySessionToken("totally-random", { secret: SECRET_A, nowSeconds: NOW }), false);
});
