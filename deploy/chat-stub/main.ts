/**
 * Deterministic chat UI reference for the lab.
 *
 * The browser UI is deliberately a stub: it selects one of the checked-in
 * OpenAI/MCP fixtures and replays the official wire. No model, API key, or
 * external network call is made. The Python contract tests consume the same
 * JSON fixtures.
 */

type JsonObject = Record<string, any>;
type Fixture = JsonObject & {
  id: string;
  ui: { chat_id: string; message_id: string; tool_call_id: string | null; mcp_session_id: string };
  openai: JsonObject;
  mcp: JsonObject[];
};

const FIXTURE_IDS = ["available_tool_success", "fictional_tool_reject"] as const;
type FixtureId = (typeof FIXTURE_IDS)[number];

const fixtureUrl = (id: FixtureId): URL =>
  new URL("../../fixtures/prompt_experiments/" + id + ".json", import.meta.url);

export async function loadFixture(id: FixtureId): Promise<Fixture> {
  if (!FIXTURE_IDS.includes(id)) throw new Error("unknown fixture: " + id);
  return JSON.parse(await Deno.readTextFile(fixtureUrl(id))) as Fixture;
}

function json(body: unknown, status = 200, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...extra },
  });
}

function sse(payload: unknown, session: string): Response {
  return new Response("event: message\ndata: " + JSON.stringify(payload) + "\n\n", {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "Mcp-Session-Id": session,
    },
  });
}

function sessionHeaders(session: string): Record<string, string> {
  return { "Mcp-Session-Id": session };
}

function methodOf(request: JsonObject): string {
  return typeof request.method === "string" ? request.method : "";
}

function firstHop(fixture: Fixture, method: string): JsonObject | undefined {
  return fixture.mcp.find((hop) => methodOf(hop.http?.request ?? {}) === method);
}

function requestId(request: JsonObject): string | number | null {
  return request.id ?? null;
}

function responsePayload(hop: JsonObject, id: string | number | null): JsonObject {
  const response = hop.http.response as JsonObject;
  return { ...response, id };
}

function toolError(id: string | number | null, name: string): JsonObject {
  return {
    jsonrpc: "2.0",
    id,
    result: {
      content: [{ type: "text", text: "Unknown tool: " + name }],
      isError: true,
    },
  };
}

function html(): string {
  return [
    "<!doctype html>",
    '<meta charset="utf-8">',
    "<title>Offline MCP chat stub</title>",
    "<style>body{font:16px system-ui;max-width:48rem;margin:2rem auto;padding:0 1rem}textarea{width:100%;min-height:5rem}button{margin:.5rem 0;padding:.5rem 1rem}pre{white-space:pre-wrap;background:#f5f5f5;padding:1rem}</style>",
    "<h1>Offline MCP chat stub</h1>",
    "<p>Deterministic fixture replay. No model, API key, or external API is used.</p>",
    '<textarea id="prompt">Find municipalities named Yokohama.</textarea>',
    '<button id="send">Send</button><pre id="out"></pre>',
    "<script>",
    'const out=document.querySelector("#out");',
    'document.querySelector("#send").onclick=async()=>{const prompt=document.querySelector("#prompt").value; const r=await fetch("/api/chat",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({prompt})}); out.textContent=JSON.stringify(await r.json(),null,2)};',
    "</script>",
  ].join("\n");
}

function fixtureForPrompt(prompt: string): FixtureId {
  return prompt.toLowerCase().includes("reinfoldib")
    ? "fictional_tool_reject"
    : "available_tool_success";
}

export async function chatResponse(prompt: string): Promise<JsonObject> {
  const id = fixtureForPrompt(prompt);
  const fixture = await loadFixture(id);
  const request = fixture.openai.request as JsonObject;
  const completion = fixture.openai.completion as JsonObject;
  const assistant = completion.choices[0].message as JsonObject;
  const messages = (request.messages as JsonObject[]).slice(0, 2);
  messages.push(assistant);
  const followup = fixture.openai.followup as JsonObject | undefined;
  const toolMessage = followup?.request?.messages?.find?.((item: JsonObject) => item.role === "tool");
  if (toolMessage) messages.push(toolMessage);
  return {
    fixture_id: id,
    ui: {
      chat_id: fixture.ui.chat_id,
      ui_message_id: fixture.ui.message_id,
      tool_call_id: fixture.ui.tool_call_id,
    },
    openai: {
      tools: request.tools,
      completion,
      messages,
      assistant_message: assistant,
      followup_completion: followup?.completion ?? null,
    },
    protocol: {
      endpoint: "/mcp",
      // Rejected fixtures stop before tools/call; report the recorded sequence.
      sequence: fixture.mcp.map((hop: JsonObject) => methodOf(hop.http.request)),
      mcp_session_id: fixture.ui.mcp_session_id,
    },
  };
}

export async function handleRequest(request: Request): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/") return new Response(html(), {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
  if (request.method === "GET" && url.pathname === "/api/contract") {
    return json({ fixtures: FIXTURE_IDS, protocol: ["initialize", "notifications/initialized", "tools/list", "tools/call"] });
  }
  if (request.method === "POST" && url.pathname === "/api/chat") {
    let body: JsonObject;
    try {
      body = await request.json();
    } catch {
      return json({ error: "request body must be JSON" }, 400);
    }
    return json(await chatResponse(typeof body.prompt === "string" ? body.prompt : ""));
  }
  if (request.method !== "POST" || url.pathname !== "/mcp") {
    return json({ error: "not found" }, 404);
  }

  let body: JsonObject;
  try {
    body = await request.json();
  } catch {
    return json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }, 400);
  }
  if (body.jsonrpc !== "2.0" || typeof body.method !== "string") {
    return json({ jsonrpc: "2.0", id: body.id ?? null, error: { code: -32600, message: "Invalid Request" } }, 400);
  }

  const fixture = await loadFixture("available_tool_success");
  const method = body.method as string;
  const id = requestId(body);
  if (method === "initialize") {
    const hop = firstHop(fixture, "initialize");
    if (!hop) return json({ jsonrpc: "2.0", id, error: { code: -32603, message: "fixture missing initialize" } }, 500);
    return sse(responsePayload(hop, id), fixture.ui.mcp_session_id);
  }
  const session = request.headers.get("Mcp-Session-Id");
  if (session !== fixture.ui.mcp_session_id) {
    return json({ jsonrpc: "2.0", id, error: { code: -32000, message: "Missing or invalid Mcp-Session-Id" } }, 400);
  }
  if (method === "notifications/initialized") {
    return new Response(null, { status: 202, headers: { "Content-Type": "application/json", ...sessionHeaders(session) } });
  }
  if (method === "tools/list") {
    const hop = firstHop(fixture, "tools/list");
    if (!hop) return json({ jsonrpc: "2.0", id, error: { code: -32603, message: "fixture missing tools/list" } }, 500);
    return sse(responsePayload(hop, id), session);
  }
  if (method === "tools/call") {
    const name = String(body.params?.name ?? "");
    if (name !== "find_municipalities") return sse(toolError(id, name), session);
    const hop = firstHop(fixture, "tools/call");
    if (!hop) return json({ jsonrpc: "2.0", id, error: { code: -32603, message: "fixture missing tools/call" } }, 500);
    return sse(responsePayload(hop, id), session);
  }
  return json({ jsonrpc: "2.0", id, error: { code: -32601, message: "Method not found: " + method } }, 404, sessionHeaders(session));
}

if (import.meta.main) {
  const port = Number(Deno.env.get("PORT") ?? "8787");
  Deno.serve({ port, hostname: "0.0.0.0" }, handleRequest);
}
