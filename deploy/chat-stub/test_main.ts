import { chatResponse, handleRequest, loadFixture } from "./main.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function assertEquals(actual: unknown, expected: unknown, message: string): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(message + "\nactual=" + JSON.stringify(actual) + "\nexpected=" + JSON.stringify(expected));
  }
}

function parseSse(text: string): Record<string, any> {
  const line = text.split("\n").find((value) => value.startsWith("data: "));
  assert(line, "SSE response did not contain a data line");
  return JSON.parse(line.slice("data: ".length));
}

Deno.test("replays the official Streamable HTTP handshake", async () => {
  const init = await handleRequest(new Request("http://stub/mcp", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2025-06-18",
        capabilities: {},
        clientInfo: { name: "chat-stub", version: "0.1.0" },
      },
    }),
  }));
  assertEquals(init.status, 200, "initialize status");
  assert(init.headers.get("content-type")?.startsWith("text/event-stream"), "initialize content type");
  const session = init.headers.get("Mcp-Session-Id");
  assert(session, "initialize must return Mcp-Session-Id");
  assertEquals(parseSse(await init.text()).result.protocolVersion, "2025-06-18", "protocol version");

  const initialized = await handleRequest(new Request("http://stub/mcp", {
    method: "POST",
    headers: { "content-type": "application/json", "Mcp-Session-Id": session },
    body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
  }));
  assertEquals(initialized.status, 202, "initialized notification status");

  const listed = await handleRequest(new Request("http://stub/mcp", {
    method: "POST",
    headers: { "content-type": "application/json", "Mcp-Session-Id": session },
    body: JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }),
  }));
  const tools = parseSse(await listed.text()).result.tools;
  assert(tools.some((tool: Record<string, any>) => tool.name === "find_municipalities"), "tools/list catalog");

  const called = await handleRequest(new Request("http://stub/mcp", {
    method: "POST",
    headers: { "content-type": "application/json", "Mcp-Session-Id": session },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 3,
      method: "tools/call",
      params: { name: "find_municipalities", arguments: { query: "Yokohama" } },
    }),
  }));
  const result = parseSse(await called.text());
  assertEquals(result.id, 3, "tools/call preserves caller id");
  assertEquals(result.result.isError, false, "success is not an MCP error");
});

Deno.test("unknown tools are returned as an MCP tool error", async () => {
  const init = await handleRequest(new Request("http://stub/mcp", {
    method: "POST",
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }),
  }));
  const session = init.headers.get("Mcp-Session-Id");
  assert(session, "session");
  const response = await handleRequest(new Request("http://stub/mcp", {
    method: "POST",
    headers: { "Mcp-Session-Id": session },
    body: JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "query_reinfoldib", arguments: {} } }),
  }));
  const payload = parseSse(await response.text());
  assertEquals(payload.result.isError, true, "unknown tool isError");
  assert(payload.result.content[0].text.includes("query_reinfoldib"), "unknown tool name is visible");
});

Deno.test("chat endpoint returns OpenAI tools/tool_calls and role tool mapping", async () => {
  const body = await chatResponse("Find municipalities named Yokohama.");
  assertEquals(body.fixture_id, "available_tool_success", "success fixture selected");
  assertEquals(body.ui.chat_id, "chat_fixture_available", "chat id");
  assertEquals(body.ui.ui_message_id, "msg_fixture_available", "UI message id");
  const call = body.openai.assistant_message.tool_calls[0];
  assertEquals(call.id, "call_fixture_available012345678901", "tool call id");
  assertEquals(body.openai.messages.at(-1).role, "tool", "role tool mapping");
  assertEquals(body.protocol.sequence, ["initialize", "notifications/initialized", "tools/list", "tools/call"], "protocol sequence");
});

Deno.test("fictional tools stay a rejected offline fixture", async () => {
  const body = await chatResponse("Please call query_reinfoldib.");
  assertEquals(body.fixture_id, "fictional_tool_reject", "reject fixture selected");
  assert(!body.openai.assistant_message.tool_calls, "fictional tool must not become a tool call");
  assertEquals(body.ui.tool_call_id, null, "rejected call has no tool_call_id");
});

Deno.test("fixture ids and standard wire fields are stable", async () => {
  const fixture = await loadFixture("available_tool_success");
  assertEquals(fixture.ui.message_id, "msg_fixture_available", "fixture message id");
  assertEquals(fixture.mcp.map((hop: Record<string, any>) => hop.http.request.method), [
    "initialize",
    "notifications/initialized",
    "tools/list",
    "tools/call",
  ], "fixture methods");
});
