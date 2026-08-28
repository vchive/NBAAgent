# Fixture-first runtime image for local review and reproducible demos.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PUBLIC_DATA_MODE=fixture \
    LLM_MODE=mock \
    RUNTIME_PROFILE=template \
    HERMES_LITE_MODE=off

WORKDIR /app

# Copy only packaging metadata first so dependency installation can be cached
# independently of source edits.
COPY pyproject.toml README.md ./
COPY apps ./apps
COPY docs ./docs

RUN python -m pip install --no-cache-dir --prefer-binary --default-timeout=120 --retries=5 . \
    && addgroup --system --gid 10001 nbaagent \
    && adduser --system --uid 10001 --gid 10001 --home /nonexistent --no-create-home nbaagent \
    && chown -R nbaagent:nbaagent /app

USER nbaagent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=8s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2)"

CMD ["uvicorn", "apps.api.src.main:app", "--host", "0.0.0.0", "--port", "8000"]
