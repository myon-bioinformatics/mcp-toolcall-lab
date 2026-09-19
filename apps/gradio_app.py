"""Thin Gradio adapter over the shared MCP layer in ``reference_client.py``.

Gradio is a reference/diagnostic client (see README's "Client roles"), not a
product under test. It lets a human pick a tool from the server's actual
``tools/list`` and inspect the raw ``isError``/``content`` result directly —
no model in the loop — so a working call here helps tell a product-specific
failure (Open WebUI, LibreChat) apart from an MCP/server-wide one.

All discovery/calling logic lives in ``mcp_toolcall_lab.reference_client``;
this file only builds the UI and wires its callbacks to that module. Do not
add MCP client code here — it belongs in the shared layer so Gradio and
Streamlit never grow diverging implementations.
"""

from __future__ import annotations

import asyncio
import json
import os

import gradio as gr

from mcp_toolcall_lab.reference_client import call_tool_for_prompt, discover_tools

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")


def _tool_choices(mcp_url: str) -> list[str]:
    tools = asyncio.run(discover_tools(mcp_url))
    return [tool.name for tool in tools]


def run_case(prompt: str, tool_name: str, arguments_json: str) -> str:
    if not tool_name:
        return "select a tool first"
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        return f"invalid arguments JSON: {exc}"

    case = asyncio.run(
        call_tool_for_prompt(
            MCP_URL,
            client="gradio",
            prompt=prompt,
            tool_name=tool_name,
            arguments=arguments,
        )
    )
    return (
        f"case_id: {case.case_id}\n"
        f"mcp_outcome: {case.mcp_outcome}\n"
        f"classification: {case.classification}\n\n"
        f"{case.result_text}"
    )


def build_app(mcp_url: str = MCP_URL) -> gr.Blocks:
    with gr.Blocks(title="mcp-toolcall-lab reference client (Gradio)") as demo:
        gr.Markdown(
            "**Reference/diagnostic client** — picks a tool manually instead of a model. "
            "See README's Client roles."
        )
        prompt = gr.Textbox(label="Prompt (free text, recorded but not sent to any model)")
        tool_name = gr.Dropdown(choices=_tool_choices(mcp_url), label="Tool")
        arguments = gr.Textbox(label="Arguments (JSON)", value="{}")
        submit = gr.Button("Send")
        output = gr.Textbox(label="Result", lines=10)
        submit.click(run_case, inputs=[prompt, tool_name, arguments], outputs=output)
    return demo


if __name__ == "__main__":
    build_app().launch(
        server_name=os.environ.get("GRADIO_HOST", "127.0.0.1"),
        server_port=int(os.environ.get("GRADIO_PORT", "7860")),
    )
