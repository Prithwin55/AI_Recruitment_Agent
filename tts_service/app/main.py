import logging
from pathlib import Path

from dotenv import load_dotenv

# Load tts_service/.env BEFORE importing routes/synth — they read their config via os.getenv at
# import time. override=False so real container env (docker-compose `environment:`) wins over the
# file. Runs in the uvicorn reload child too, since that re-imports this module.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI  # noqa: E402

from .routes import router  # noqa: E402
from .synth import synthesizer  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Recruitment — TTS Service")


@app.on_event("startup")
def on_startup() -> None:
    # Load the model once, at startup, so /health only flips to "ok" after the weights are warm and
    # the first interview sentence pays no cold-start cost. Blocking here is fine — it runs before
    # the service starts accepting requests.
    synthesizer.load()


app.include_router(router)
