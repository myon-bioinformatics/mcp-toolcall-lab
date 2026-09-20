import { handleRequest } from "./mcp.ts";

const port = Number(Deno.env.get("PORT") || Deno.env.get("MCP_PORT") || "8000");
const hostname = Deno.env.get("MCP_HOST") || "0.0.0.0";

if (import.meta.main) {
  Deno.serve({ port, hostname }, handleRequest);
}
