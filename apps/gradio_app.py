"""Gradio reference/diagnostic client -- a thin adapter, not its own MCP client.

Gradio is a reference/diagnostic client in this lab (see README's "Client
roles" section), not a chat product under test. A successful call here while
Open WebUI/LibreChat fail helps tell a product-specific bug apart from an
MCP/server-wide one -- which only holds up if this module and
``streamlit_app.py`` share one MCP client implementation instead of each
growing their own. All discovery/calling/classification logic lives in
``mcp_toolcall_lab.reference_client``; this module only renders it.
"""

from __future__ import annotations

import asyncio
import json
import os

import gradio as gr

from mcp_toolcall_lab.reference_client import call_tool, discover_tools

CLIENT_NAME = "gradio"


def mcp_url() -> str:
    return os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")


def list_tool_names() -> list[str]:
    """Discover tools synchronously, for use from Gradio's sync callbacks."""
    tools = asyncio.run(discover_tools(mcp_url()))
    return [tool.name for tool in tools]


def run_tool_call(prompt: str, tool_name: str, arguments_json: str) -> str:
    """Call one tool and render the normalized result as pretty JSON.

    Returns a plain error string (not a raised exception) for anything the
    user got wrong before a call was even attempted -- no tool selected, or
    arguments that are not valid JSON -- since those are UI-input mistakes,
    not MCP outcomes ``reference_client.call_tool`` would classify.
    """
    if not tool_name:
        return "Select a tool first (click \"Discover tools\")."
    try:
        arguments = json.loads(arguments_json) if arguments_json.strip() else {}
    except json.JSONDecodeError as exc:
        return f"Invalid arguments JSON: {exc}"

    result = asyncio.run(
        call_tool(
            mcp_url(),
            client=CLIENT_NAME,
            tool_name=tool_name,
            arguments=arguments,
            prompt=prompt or None,
        )
    )
    return json.dumps(
        {
            "case_id": result.case_id,
            "outcome": result.outcome,
            "payload": result.payload,
            "error": result.error,
        },
        indent=2,
        default=str,
    )


def build_app() -> gr.Blocks:
    """Build the Gradio Blocks app without launching a server (import-safe)."""
    with gr.Blocks(title="mcp-toolcall-lab -- Gradio reference client") as demo:
        gr.Markdown(
            "**Reference / diagnostic MCP client** -- see the README's "
            "\"Client roles\" section. Not a chat product under test; there is "
            "no LLM in this loop, the tool is picked explicitly below."
        )
        prompt = gr.Textbox(label="Prompt (context only, not sent to a model)")
        tool_name = gr.Dropdown(choices=[], label="Tool", allow_custom_value=True)
        refresh = gr.Button("Discover tools")
        arguments_json = gr.Textbox(label="Arguments (JSON)", value="{}")
        submit = gr.Button("Submit")
        output = gr.Textbox(label="Result", lines=12)

        refresh.click(lambda: gr.Dropdown(choices=list_tool_names()), outputs=tool_name)
        submit.click(run_tool_call, inputs=[prompt, tool_name, arguments_json], outputs=output)
    return demo


def main() -> None:
    app = build_app()
    app.launch(
        server_name=os.environ.get("GRADIO_HOST", "0.0.0.0"),
        server_port=int(os.environ.get("GRADIO_PORT", "7860")),
    )


if __name__ == "__main__":
    main()
