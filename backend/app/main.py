import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.config import get_settings
from shared.db import init_db

from .admin.routes import router as admin_router
from .auth.routes import router as auth_router
from .auth.seed import seed_default_user
from .candidates.routes import router as candidates_router
from .recruitments.routes import router as recruitments_router
from .scheduling.routes import public_router as interview_public_router
from .scheduling.routes import router as scheduling_router
from .workers.sweep import run_forever as run_sweep_forever

logging.basicConfig(level=logging.INFO)

settings = get_settings()

app = FastAPI(title="AI Recruitment — Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    seed_default_user()
    app.state.sweep_task = asyncio.create_task(run_sweep_forever())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    task = getattr(app.state, "sweep_task", None)
    if task is not None:
        task.cancel()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(recruitments_router)
app.include_router(candidates_router)
app.include_router(scheduling_router)
app.include_router(interview_public_router)
