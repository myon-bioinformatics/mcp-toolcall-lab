# Gradio reference/diagnostic client -- a thin adapter over reference_client.py,
# not its own MCP client. See apps/gradio_app.py and the README's "Client roles" section.
FROM python:3.11-slim
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY apps ./apps
RUN pip install --no-cache-dir -e ".[reference-clients]"

ENV MCP_URL=http://mcp-mock:8000/mcp \
    GRADIO_HOST=0.0.0.0 \
    GRADIO_PORT=7860
EXPOSE 7860

CMD ["python", "-m", "apps.gradio_app"]
