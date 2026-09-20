/** Streamable HTTP MCP: JSON-RPC 2.0 + SSE + Mcp-Session-Id + CORS. */

import catalog from "./catalog.generated.json" with { type: "json" };
import { allowWikiFetch, mintSession, sessionValid } from "./session.ts";
import {
  DEFAULT_LANG,
  WikipediaFetchError,
  fetchWikipediaArticle,
  fetchWikipediaSection,
} from "./wikipedia.ts";

export const PROTOCOL_VERSION = "2025-06-18";
export const SERVER_NAME = "mcp-toolcall-lab-wikipedia";
export const SERVER_VERSION = "0.1.0";
export const MCP_PATH = "/mcp";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type JsonRpcId = string | number | null;
type JsonRpcRequest = {
  jsonrpc?: string;
  id?: JsonRpcId;
  method?: string;
  params?: Record<string, Json> | Json[];
};

type CatalogTool = (typeof catalog.tools)[number];

const WIKI_FETCH_TOOLS = new Set(["fetch_wikipedia_article", "fetch_wikipedia_section"]);

function corsHeaders(): Record<string, string> {
  let origin = "*";
  try {
    origin = Deno.env.get("MCP_CORS_ORIGIN")?.trim() ||
      Deno.env.get("MCP_ALLOWED_ORIGIN")?.trim() ||
      "*";
  } catch {
    origin = "*";
  }
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers":
      "Content-Type, Accept, Mcp-Session-Id, MCP-Protocol-Version, Last-Event-ID",
    "Access-Control-Expose-Headers": "Mcp-Session-Id",
    "Access-Control-Max-Age": "86400",
  };
}

function header(req: Request, name: string): string | null {
  return req.headers.get(name);
}

function sessionOf(req: Request): string | null {
  return header(req, "Mcp-Session-Id") || header(req, "mcp-session-id");
}

function jsonResponse(status: number, body: unknown, extra: Record<string, string> = {}): Response {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...corsHeaders(),
      ...extra,
    },
  });
}

function sseMessage(payload: unknown, extra: Record<string, string> = {}): Response {
  const data = JSON.stringify(payload);
  const body = `event: message\ndata: ${data}\n\n`;
  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      ...corsHeaders(),
      ...extra,
    },
  });
}

function rpcResult(id: JsonRpcId, result: unknown, extra: Record<string, string> = {}): Response {
  return sseMessage({ jsonrpc: "2.0", id, result }, extra);
}

function rpcError(
  id: JsonRpcId,
  code: number,
  message: string,
  extra: Record<string, string> = {},
): Response {
  return sseMessage({ jsonrpc: "2.0", id, error: { code, message } }, extra);
}

function wrapToolResult(value: unknown, wrap: boolean): Record<string, unknown> {
  const text = JSON.stringify(value);
  const result: Record<string, unknown> = {
    content: [{ type: "text", text }],
    structuredContent: wrap ? { result: value } : value,
    isError: false,
  };
  if (wrap) {
    result._meta = { fastmcp: { wrap_result: true } };
  }
  return result;
}

function toolError(message: string): Record<string, unknown> {
  return {
    content: [{ type: "text", text: message }],
    isError: true,
  };
}

function findTool(name: string): CatalogTool | undefined {
  return catalog.tools.find((tool) => tool.name === name);
}

function defaultFor(schema: CatalogTool["inputSchema"], key: string): unknown {
  const prop = schema.properties[key as keyof typeof schema.properties] as
    | { default?: unknown }
    | undefined;
  return prop && "default" in prop ? prop.default : undefined;
}

function validateArguments(
  tool: CatalogTool,
  raw: unknown,
): { ok: true; value: Record<string, unknown> } | { ok: false; message: string } {
  if (raw == null) {
    raw = {};
  }
  if (typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, message: "arguments must be an object" };
  }
  const args = raw as Record<string, unknown>;
  const schema = tool.inputSchema;
  if (schema.additionalProperties === false) {
    for (const key of Object.keys(args)) {
      if (!(key in schema.properties)) {
        return { ok: false, message: `Unexpected argument ${key}` };
      }
    }
  }
  const value: Record<string, unknown> = { ...args };
  for (const key of schema.required) {
    if (value[key] === undefined || value[key] === null) {
      return { ok: false, message: `missing required argument ${key}` };
    }
  }
  for (const key of Object.keys(schema.properties)) {
    if (value[key] === undefined) {
      const fallback = defaultFor(schema, key);
      if (fallback !== undefined) value[key] = fallback;
    }
    if (value[key] === undefined) continue;
    const expected = (schema.properties[key as keyof typeof schema.properties] as { type: string })
      .type;
    const actual = value[key];
    if (expected === "string" && typeof actual !== "string") {
      return { ok: false, message: `${key} must be a string` };
    }
  }
  return { ok: true, value };
}

function wrapsResult(tool: CatalogTool): boolean {
  const schema = tool.outputSchema as { "x-fastmcp-wrap-result"?: boolean } | undefined;
  return Boolean(schema && schema["x-fastmcp-wrap-result"]);
}

async function callTool(name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const tool = findTool(name);
  if (!tool) {
    return toolError(`Unknown tool: ${name}`);
  }
  const checked = validateArguments(tool, args);
  if (!checked.ok) {
    return toolError(checked.message);
  }
  try {
    if (name === "fetch_wikipedia_article") {
      const result = await fetchWikipediaArticle(
        String(checked.value.title),
        String(checked.value.lang || DEFAULT_LANG),
      );
      return wrapToolResult(result, wrapsResult(tool));
    }
    if (name === "fetch_wikipedia_section") {
      const result = await fetchWikipediaSection(
        String(checked.value.title),
        String(checked.value.heading ?? ""),
      );
      return wrapToolResult(result, wrapsResult(tool));
    }
    return toolError(`Unknown tool: ${name}`);
  } catch (err) {
    const message = err instanceof WikipediaFetchError ? err.message : String(err);
    return toolError(message);
  }
}

function initializeResult() {
  return {
    protocolVersion: PROTOCOL_VERSION,
    capabilities: { tools: { listChanged: false } },
    serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
  };
}

export async function handleRequest(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders() });
  }

  if (req.method === "GET" && (path === "/" || path === "/health")) {
    return jsonResponse(200, { ok: true, server: SERVER_NAME });
  }

  if (path !== MCP_PATH) {
    return jsonResponse(404, { error: "not found" });
  }

  if (req.method !== "POST") {
    return jsonResponse(405, { error: "method not allowed" });
  }

  let parsed: JsonRpcRequest;
  try {
    parsed = await req.json();
  } catch {
    return jsonResponse(400, {
      jsonrpc: "2.0",
      id: null,
      error: { code: -32700, message: "Parse error" },
    });
  }

  if (parsed.jsonrpc !== "2.0" || typeof parsed.method !== "string") {
    return jsonResponse(400, {
      jsonrpc: "2.0",
      id: parsed.id ?? null,
      error: { code: -32600, message: "Invalid Request" },
    });
  }

  const method = parsed.method;
  const id = parsed.id ?? null;
  const params = (parsed.params && !Array.isArray(parsed.params) ? parsed.params : {}) as Record<
    string,
    Json
  >;

  if (method === "initialize") {
    const sessionId = await mintSession();
    return rpcResult(id, initializeResult(), { "Mcp-Session-Id": sessionId });
  }

  const sessionId = sessionOf(req);
  if (!sessionId || !(await sessionValid(sessionId))) {
    return jsonResponse(400, {
      jsonrpc: "2.0",
      id,
      error: { code: -32000, message: "Missing or invalid Mcp-Session-Id" },
    });
  }
  const sessionHeader = { "Mcp-Session-Id": sessionId };

  if (method === "notifications/initialized" || method.startsWith("notifications/")) {
    return new Response(null, {
      status: 202,
      headers: { "Content-Type": "application/json", ...corsHeaders(), ...sessionHeader },
    });
  }

  if (method === "tools/list") {
    return rpcResult(id, { tools: catalog.tools }, sessionHeader);
  }

  if (method === "tools/call") {
    const name = String(params.name || "");
    const args = (params.arguments && typeof params.arguments === "object" &&
        !Array.isArray(params.arguments)
      ? params.arguments
      : {}) as Record<string, unknown>;
    if (WIKI_FETCH_TOOLS.has(name) && !(await allowWikiFetch(req))) {
      return rpcResult(id, toolError("Wikipedia fetch rate limit exceeded"), sessionHeader);
    }
    const result = await callTool(name, args);
    return rpcResult(id, result, sessionHeader);
  }

  if (id === null || id === undefined) {
    return new Response(null, {
      status: 202,
      headers: { "Content-Type": "application/json", ...corsHeaders(), ...sessionHeader },
    });
  }
  return rpcError(id, -32601, `Method not found: ${method}`, sessionHeader);
}
