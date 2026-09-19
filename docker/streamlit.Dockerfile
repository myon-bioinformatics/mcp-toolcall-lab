# Reference/diagnostic client (see docs/e2e_foundation.md's "Client roles") — a thin
# adapter over mcp_toolcall_lab.reference_client, not a chat product under test.
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY apps ./apps
RUN pip install --no-cache-dir -e ".[reference-ui]"

EXPOSE 8501

CMD ["streamlit", "run", "apps/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
