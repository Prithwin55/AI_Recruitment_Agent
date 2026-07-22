import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.config import get_settings
from shared.db import init_db

from .interview_engine.interview_ws import router as interview_ws_router
from .post_interview.reprocess import rescore_pending
from .resume_queue import run_forever as run_resume_queue_forever

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(title="AI Recruitment — AI Service")

# CORS (same single-origin policy as backend). Needed when the browser hits :8100 cross-origin
# (absolute VITE_AI_SERVICE_WS_URL / no proxy).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _rescore_pending_on_startup() -> None:
    # Score any interviews that completed but were never scored (a prior scoring failure, or the
    # service dying between finalize and scoring). Wrapped so a backlog error can never take down
    # startup, and dispatched as a background task so it runs IN PARALLEL — the app finishes
    # starting (and serves interviews) immediately; the backlog fills in behind it.
    try:
        await rescore_pending()
    except Exception:  # noqa: BLE001
        logger.exception("Startup backlog scoring failed")


@app.on_event("startup")
async def on_startup() -> None:
    # ai_service never seeds data — backend owns that. It only needs the
    # tables to exist if it happens to start before the backend has.
    init_db()
    app.state.resume_queue_task = asyncio.create_task(run_resume_queue_forever())
    # Single-worker service, so this runs exactly once per start — no duplicate scoring.
    app.state.rescore_task = asyncio.create_task(_rescore_pending_on_startup())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    for attr in ("resume_queue_task", "rescore_task"):
        task = getattr(app.state, attr, None)
        if task is not None:
            task.cancel()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# The interview WebSocket is namespaced under /ai so nginx/the dev proxy can forward /ai/* straight
# through with NO path rewriting. /health stays unprefixed at root for infra health checks.
app.include_router(interview_ws_router, prefix="/ai")
