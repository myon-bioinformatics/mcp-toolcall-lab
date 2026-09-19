# Streamlit reference/diagnostic client -- a thin adapter over reference_client.py,
# not its own MCP client. See apps/streamlit_app.py and the README's "Client roles" section.
FROM python:3.11-slim
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY apps ./apps
RUN pip install --no-cache-dir -e ".[reference-clients]"

ENV MCP_URL=http://mcp-mock:8000/mcp
EXPOSE 8501

CMD ["streamlit", "run", "apps/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
