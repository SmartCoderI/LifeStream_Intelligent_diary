# syntax=docker/dockerfile:1
#
# Stage 3 dashboard server: the FastAPI app (API + built web app) in cloud mode. No GPU models:
# speech and diarization run in the Batch worker; this image only needs the small embedding model
# for search. Built on the server itself (arm64, Graviton) by infra/server/start.sh.
# Build context: the tarball infra/deploy.sh uploads (pyproject.toml, uv.lock, src/, web/dist/, ...).

FROM public.ecr.aws/docker/library/python:3.11-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# torchcodec has no Linux arm64 wheel; only the worker's diarization (pyannote) uses it.
# On arm64, PyPI's torch is the small CPU-only build.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-dev --no-install-project --no-install-package torchcodec
COPY src ./src
RUN uv sync --locked --no-dev --no-install-package torchcodec

ENV PATH=/app/.venv/bin:$PATH \
    HF_HOME=/opt/models/hf

# The search embedding model, so the first question doesn't wait for a download.
ARG EMBED_MODEL=BAAI/bge-small-en-v1.5
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('$EMBED_MODEL')"

COPY web/dist ./web/dist

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    EMBED_MODEL=$EMBED_MODEL \
    SPEAKERS_FILE=/app/data/speakers.yaml \
    MPLCONFIGDIR=/tmp/matplotlib

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app /app/data
USER app
EXPOSE 8000
CMD ["lifestream", "serve", "--host", "0.0.0.0", "--port", "8000", "--out", "/app/data/out"]
