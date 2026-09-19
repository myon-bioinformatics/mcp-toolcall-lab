# Deterministic OpenAI-compatible chat-completions mock -- see mock_llm.py.
# Stdlib-only on purpose: no pip install step needed.
FROM python:3.11-slim
WORKDIR /app

COPY docker/mock_llm.py ./mock_llm.py

ENV MOCK_LLM_HOST=0.0.0.0 \
    MOCK_LLM_PORT=8081
EXPOSE 8081

CMD ["python", "mock_llm.py"]
