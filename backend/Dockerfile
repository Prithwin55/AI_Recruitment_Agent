# Recruiter-facing backend API — self-contained (vendors its own shared/ package).
#   docker build -f backend/Dockerfile -t airecruit-backend .   (build from the repo root)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 RELOAD=0

WORKDIR /app
COPY backend/ ./backend/           # includes backend/shared/ (vendored)

WORKDIR /app/backend
RUN pip install -r requirements.txt

# Config is injected as env vars at runtime (compose env_file), never baked into the image.
EXPOSE 8000
CMD ["python", "main.py"]
