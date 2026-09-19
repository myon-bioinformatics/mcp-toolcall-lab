"""Thin UI adapters for the reference/diagnostic clients (Gradio, Streamlit).

Neither module in this package may talk to MCP directly -- both call into
``mcp_toolcall_lab.reference_client`` so tool discovery/calling/tracing stays
identical between the two UI frameworks. See the README's "Client roles"
section and docs/e2e_foundation.md.
"""
