"""Run the standalone TTS microservice, no build step:

    python main.py            # from inside tts_service/

Serves KittenTTS synthesis on TTS_SERVICE_PORT (default 8200). Unlike ai_service, this service is
STATELESS and horizontally scalable — run as many replicas as you need; each loads its own model
copy. Per-container concurrency is bounded inside the app (see app/routes.py), NOT by worker count,
so a single uvicorn worker per container is the intended shape. RELOAD is for local dev only."""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
# Read this service's own .env for the launch knobs below (the app process re-loads it too, in
# app/main.py, before its config modules import). override=False keeps real env authoritative.
load_dotenv(HERE / ".env")


def main() -> None:
    port = int(os.getenv("TTS_SERVICE_PORT", "8200"))
    reload = os.getenv("RELOAD", "1") not in ("0", "false", "False")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=reload, app_dir=str(HERE))


if __name__ == "__main__":
    main()
