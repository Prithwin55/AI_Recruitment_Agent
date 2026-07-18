import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.config import get_settings
from shared.db import init_db

from .interview_engine.interview_ws import router as interview_ws_router
from .resume_queue import run_forever as run_resume_queue_forever

logging.basicConfig(level=logging.INFO)

settings = get_settings()

app = FastAPI(title="AI Recruitment — AI Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    # ai_service never seeds data — backend owns that. It only needs the
    # tables to exist if it happens to start before the backend has.
    init_db()
    app.state.resume_queue_task = asyncio.create_task(run_resume_queue_forever())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    task = getattr(app.state, "resume_queue_task", None)
    if task is not None:
        task.cancel()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(interview_ws_router)
