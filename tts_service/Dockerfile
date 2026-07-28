# Standalone, stateless TTS microservice. Default engine is Pocket TTS (kyutai flow-matching, natural
# voice, ~real-time on CPU); KittenTTS (tiny, fast, grainier) is selectable via TTS_ENGINE=kitten.
# Build from the REPO ROOT:  docker build -f tts_service/Dockerfile -t airecruit-tts .
# Scale horizontally with replicas — each container loads its own model copy; per-container
# concurrency is bounded in-app (TTS_MAX_CONCURRENCY). Pocket lets onnxruntime use all cores per
# synthesis (near real-time); set TTS_ORT_THREADS to cap it.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 RELOAD=0

# espeak-ng: KittenTTS phonemizes text through it. libgomp1: onnxruntime's OpenMP runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates espeak-ng libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/tts_service

# Install deps first (cached layer), then copy the app.
COPY tts_service/requirements.txt ./
RUN pip install -r requirements.txt

COPY tts_service/ ./

# Bake BOTH engines' weights into the image so runtime does ZERO Hugging Face fetching (fast, offline
# starts, identical replicas) and switching TTS_ENGINE is instant. Pocket = the ONNX bundle from the
# HF Space; Kitten = the nano checkpoint.
RUN python -c "from app.engines.pocket import PocketEngine; PocketEngine().load()" \
 && python -c "from app.engines.kitten import KittenEngine; KittenEngine().load()"

EXPOSE 8200
CMD ["python", "main.py"]
