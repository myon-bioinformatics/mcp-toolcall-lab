"""Run the Streamable HTTP mock server: python -m mcp_toolcall_lab"""

from .server import mcp, run_server

if __name__ == "__main__":
    run_server(mcp)
