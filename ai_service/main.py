"""Run the AI/realtime service directly, no build step:

    python main.py               # from inside ai_service/
    python ai_service/main.py    # from the repo root

Reads THIS folder's .env (ai_service/.env) and serves on AI_SERVICE_PORT with autoreload. The
`shared` package is installed editable in the venv, so it imports from anywhere.

NOTE: this service must run as a SINGLE worker — interview turn-taking state lives in-process per
session. `reload=True` already runs one worker, so `python main.py` is correct; never add workers.
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Point shared.config at this service's own env file before anything imports it.
os.environ.setdefault("APP_ENV_FILE", str(HERE / ".env"))

import uvicorn  # noqa: E402  (must follow the APP_ENV_FILE line)
from shared.config import get_settings  # noqa: E402


def main() -> None:
    port = get_settings().ai_service_port
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True, app_dir=str(HERE))


if __name__ == "__main__":
    main()
