"""Thin Streamlit adapter over the shared MCP layer in ``reference_client.py``.

Streamlit is a reference/diagnostic client (see README's "Client roles"), not
a product under test — see ``apps/gradio_app.py`` for the same layering
rationale. All discovery/calling logic lives in
``mcp_toolcall_lab.reference_client``; this script only renders widgets and
calls that module. Do not add MCP client code here.

Run with: ``streamlit run apps/streamlit_app.py``
"""

from __future__ import annotations

import asyncio
import json
import os

import streamlit as st

from mcp_toolcall_lab.reference_client import call_tool_for_prompt, discover_tools

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")

st.title("mcp-toolcall-lab reference client (Streamlit)")
st.caption(
    "Reference/diagnostic client — picks a tool manually instead of a model. "
    "See README's Client roles."
)

tool_names = [tool.name for tool in asyncio.run(discover_tools(MCP_URL))]

prompt = st.text_input("Prompt (free text, recorded but not sent to any model)")
tool_name = st.selectbox("Tool", tool_names)
arguments_json = st.text_area("Arguments (JSON)", value="{}")

if st.button("Send"):
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        st.error(f"invalid arguments JSON: {exc}")
    else:
        case = asyncio.run(
            call_tool_for_prompt(
                MCP_URL,
                client="streamlit",
                prompt=prompt,
                tool_name=tool_name,
                arguments=arguments,
            )
        )
        st.write(f"case_id: {case.case_id}")
        st.write(f"mcp_outcome: {case.mcp_outcome}")
        st.write(f"classification: {case.classification}")
        st.code(case.result_text or "")
