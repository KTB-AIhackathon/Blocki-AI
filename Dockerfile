FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

COPY pyproject.toml ./
COPY app ./app
COPY templates ./templates

# Both extras stay in the image so local Codex and prod Anthropic are a
# provider env flip, not a rebuild. Pipelines still run if neither key is set.
RUN pip install --no-cache-dir -e ".[anthropic,codex]"

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["python", "-m", "app"]
