# The mock MCP server, built from the same package source every client below points at.
# See docs/e2e_foundation.md for the network model this image is meant to run under.
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000
EXPOSE 8000

CMD ["python", "-m", "mcp_toolcall_lab"]
