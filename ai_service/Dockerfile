# AI/realtime service (resume scoring + live interview). Build from the REPO ROOT:
#   docker build -f ai_service/Dockerfile -t airecruit-ai .
# MUST run as a single worker (interview turn-taking state is in-process) — RELOAD=0 keeps 1 worker.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 RELOAD=0

# Runtime libs: libasound2 + libssl for the Azure Speech SDK (Arabic mode); build-essential covers
# any dep without a prebuilt wheel for this platform. Drop build-essential if your build doesn't need it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libasound2 libssl3 build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY ai_service/ ./ai_service/     # includes ai_service/shared/ (vendored)

WORKDIR /app/ai_service
RUN pip install -r requirements.txt

EXPOSE 8100
CMD ["python", "main.py"]
