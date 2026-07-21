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

# CORS: allow the single frontend origin. Credentials stay on so the Authorization bearer works
# when the frontend is served from a different origin than the API (absolute VITE_API_BASE_URL /
# direct service hits); same-origin behind a proxy this is a no-op.
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


# Every API route is namespaced under /api — nginx/the dev proxy then forwards /api/* straight
# through with NO path rewriting (simpler and less error-prone than stripping a prefix). /health
# stays unprefixed at root, the conventional path for infra health checks (Docker/k8s/LB probes).
API_PREFIX = "/api"
app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(admin_router, prefix=API_PREFIX)
app.include_router(recruitments_router, prefix=API_PREFIX)
app.include_router(candidates_router, prefix=API_PREFIX)
app.include_router(scheduling_router, prefix=API_PREFIX)
app.include_router(interview_public_router, prefix=API_PREFIX)
