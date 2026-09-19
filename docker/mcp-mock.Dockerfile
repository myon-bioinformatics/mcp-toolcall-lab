# Serves the same Streamable HTTP mock the README's "Run locally" section documents,
# reachable inside the Compose network as http://mcp-mock:8000/mcp (see docker-compose.yml).
FROM python:3.11-slim
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8000
EXPOSE 8000

CMD ["python", "-m", "mcp_toolcall_lab"]
