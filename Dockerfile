# syntax=docker/dockerfile:1
#
# F7 worker image: Stage 1 CLI + FFmpeg + every model pre-downloaded (M3a: no downloads at job start).
# Runs on AWS Batch GPU instances (g4dn.xlarge, NVIDIA T4). The NVIDIA driver comes from the host;
# CUDA and cuDNN come from PyTorch's pip packages (see LD_LIBRARY_PATH below).
#
# Normally built by AWS CodeBuild (infra/build-image.sh), on x86 with HF_TOKEN from SSM.
# Building locally needs ~30 GB free disk and x86:
#   docker buildx build --platform linux/amd64 --secret id=hf_token,env=HF_TOKEN -t lifestream-worker .
# The pyannote model is gated, so the build needs HF_TOKEN. It's passed as a build secret:
# it is never written into an image layer.

# Docker Hub's official image via AWS's public mirror (CodeBuild often hits Docker Hub's pull limits).
FROM public.ecr.aws/docker/library/python:3.11-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first (cached unless pyproject.toml / uv.lock change), then the project itself.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev

ENV PATH=/app/.venv/bin:$PATH \
    HF_HOME=/opt/models/hf \
    TORCH_HOME=/opt/models/torch \
    NLTK_DATA=/opt/models/nltk

# ---- models (M3a) ----
ARG WHISPER_MODEL=large-v3
ARG DIARIZATION_MODEL=pyannote/speaker-diarization-community-1
RUN --mount=type=secret,id=hf_token,env=HF_TOKEN \
    python -m lifestream.cloud.prefetch \
      --whisper-model "$WHISPER_MODEL" --diarization-model "$DIARIZATION_MODEL"
# Fail the build now, not at job time, if anything would need a download.
RUN HF_HUB_OFFLINE=1 python -m lifestream.cloud.prefetch --verify \
      --whisper-model "$WHISPER_MODEL" --diarization-model "$DIARIZATION_MODEL"

# ---- run-time settings for the GPU worker ----
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    WHISPER_MODEL=$WHISPER_MODEL \
    DIARIZATION_MODEL=$DIARIZATION_MODEL \
    DEVICE=cuda \
    COMPUTE_TYPE=float16 \
    DIARIZATION_DEVICE=cuda \
    LLM_PROVIDER=claude \
    LD_LIBRARY_PATH=/app/.venv/lib/python3.11/site-packages/nvidia/cudnn/lib:/app/.venv/lib/python3.11/site-packages/nvidia/cublas/lib \
    MPLCONFIGDIR=/tmp/matplotlib \
    NUMBA_CACHE_DIR=/tmp/numba

RUN useradd --create-home --uid 1000 worker
USER worker
ENTRYPOINT ["lifestream"]
CMD ["--help"]
