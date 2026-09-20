/** Deno KV session TTL, cap, and path isolation. */

import {
  closeKv,
  mintSession,
  sessionMax,
  sessionTtlMs,
  sessionValid,
} from "./session.ts";

function assert(cond: unknown, message: string): asserts cond {
  if (!cond) throw new Error(message);
}

async function withKvEnv(
  env: Record<string, string>,
  fn: () => Promise<void>,
): Promise<void> {
  const previous: Record<string, string | undefined> = {};
  for (const key of Object.keys(env)) {
    previous[key] = Deno.env.get(key);
    Deno.env.set(key, env[key]);
  }
  await closeKv();
  try {
    await fn();
  } finally {
    await closeKv();
    for (const [key, value] of Object.entries(previous)) {
      if (value == null) Deno.env.delete(key);
      else Deno.env.set(key, value);
    }
    await closeKv();
  }
}

Deno.test("minted session is valid until TTL, then rejected", async () => {
  const dir = await Deno.makeTempDir({ prefix: "mcp-session-" });
  await withKvEnv(
    {
      MCP_KV_PATH: `${dir}/kv.sqlite`,
      MCP_SESSION_TTL_SECONDS: "1",
    },
    async () => {
      assert(sessionTtlMs() === 1000, `ttl ${sessionTtlMs()}`);
      const id = await mintSession();
      assert(await sessionValid(id), "fresh session should be valid");
      assert(!(await sessionValid("sess_notreal")), "unknown id");
      assert(!(await sessionValid(null)), "null id");
      await new Promise((resolve) => setTimeout(resolve, 1300));
      assert(!(await sessionValid(id)), "expired session should be invalid");
    },
  );
});

Deno.test("session cap evicts the oldest id", async () => {
  const dir = await Deno.makeTempDir({ prefix: "mcp-session-cap-" });
  await withKvEnv(
    {
      MCP_KV_PATH: `${dir}/kv.sqlite`,
      MCP_SESSION_MAX: "2",
      MCP_SESSION_TTL_SECONDS: "1800",
    },
    async () => {
      assert(sessionMax() === 2, `max ${sessionMax()}`);
      const first = await mintSession();
      await new Promise((resolve) => setTimeout(resolve, 10));
      const second = await mintSession();
      assert(await sessionValid(first), "first still valid at cap");
      await new Promise((resolve) => setTimeout(resolve, 10));
      const third = await mintSession();
      assert(!(await sessionValid(first)), "oldest should be evicted");
      assert(await sessionValid(second), "second kept");
      assert(await sessionValid(third), "third kept");
    },
  );
});

Deno.test("sessions written to a KV file are readable after reopen", async () => {
  const dir = await Deno.makeTempDir({ prefix: "mcp-session-reopen-" });
  const path = `${dir}/kv.sqlite`;
  let id = "";
  await withKvEnv({ MCP_KV_PATH: path, MCP_SESSION_TTL_SECONDS: "1800" }, async () => {
    id = await mintSession();
    assert(await sessionValid(id), "before close");
  });
  await withKvEnv({ MCP_KV_PATH: path, MCP_SESSION_TTL_SECONDS: "1800" }, async () => {
    assert(await sessionValid(id), "same KV file, new openKv handle");
  });
});
