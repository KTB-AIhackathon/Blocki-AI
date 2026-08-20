FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

COPY pyproject.toml ./
COPY app ./app
COPY templates ./templates

# Anthropic only. Pipelines still run if the key is missing.
RUN pip install --no-cache-dir -e ".[anthropic]"

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["python", "-m", "app"]
