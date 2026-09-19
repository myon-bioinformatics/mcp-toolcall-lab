"""Streamlit reference/diagnostic client -- a thin adapter, not its own MCP client.

See ``gradio_app.py``'s module docstring: the same shared-layer rule applies
here. All discovery/calling/classification logic lives in
``mcp_toolcall_lab.reference_client``; this module only renders it.

Everything lives inside ``main()`` (only called under the ``__main__`` guard,
which ``streamlit run`` and ``streamlit.testing.v1.AppTest`` both set) so this
module can be imported for a plain import-level check without a Streamlit
``ScriptRunContext``.
"""

from __future__ import annotations

import asyncio
import json
import os

from mcp_toolcall_lab.reference_client import call_tool, discover_tools

CLIENT_NAME = "streamlit"


def mcp_url() -> str:
    return os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="mcp-toolcall-lab -- Streamlit reference client")
    st.title("mcp-toolcall-lab -- Streamlit reference client")
    st.caption(
        'Reference / diagnostic MCP client -- see the README\'s "Client roles" '
        "section. Not a chat product under test; there is no LLM in this loop, "
        "the tool is picked explicitly below."
    )

    if "tool_names" not in st.session_state:
        st.session_state.tool_names = []

    if st.button("Discover tools"):
        st.session_state.tool_names = [tool.name for tool in asyncio.run(discover_tools(mcp_url()))]

    prompt = st.text_input("Prompt (context only, not sent to a model)")
    tool_name = st.selectbox("Tool", options=st.session_state.tool_names or [""])
    arguments_json = st.text_area("Arguments (JSON)", value="{}")

    if st.button("Submit"):
        if not tool_name:
            st.error('Select a tool first (click "Discover tools").')
            return
        try:
            arguments = json.loads(arguments_json) if arguments_json.strip() else {}
        except json.JSONDecodeError as exc:
            st.error(f"Invalid arguments JSON: {exc}")
            return

        result = asyncio.run(
            call_tool(
                mcp_url(),
                client=CLIENT_NAME,
                tool_name=tool_name,
                arguments=arguments,
                prompt=prompt or None,
            )
        )
        st.json(
            {
                "case_id": result.case_id,
                "outcome": result.outcome,
                "payload": result.payload,
                "error": result.error,
            }
        )


if __name__ == "__main__":
    main()
