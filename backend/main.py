"""Run the recruiter-facing backend API directly, no build step:

    python main.py            # from inside backend/
    python backend/main.py    # from the repo root

Reads THIS folder's .env (backend/.env) and serves on BACKEND_PORT with autoreload. The `shared`
package is installed editable in the venv, so it imports from anywhere.
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Point shared.config at this service's own env file before anything imports it.
os.environ.setdefault("APP_ENV_FILE", str(HERE / ".env"))

import uvicorn  # noqa: E402  (must follow the APP_ENV_FILE line)
from shared.config import get_settings  # noqa: E402


def main() -> None:
    port = get_settings().backend_port
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True, app_dir=str(HERE))


if __name__ == "__main__":
    main()
