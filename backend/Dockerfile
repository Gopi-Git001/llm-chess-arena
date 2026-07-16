# Backend image. Build context is the REPO ROOT (not backend/), because the app
# reads ../config.yaml relative to backend/app/ — the compose file sets this.
FROM python:3.13-slim

# Faster, quieter Python in a container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first so the layer caches unless requirements change.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

# App code + the config file it loads. config.yaml must sit at /app/config.yaml
# so backend/app/config.py's REPO_ROOT (parents[2]) resolves to /app.
COPY backend/ ./backend/
COPY config.yaml ./config.yaml

# The SQLite DB lives on a mounted volume, not in the code tree.
ENV ARENA_DB_PATH=/app/data/arena.db

WORKDIR /app/backend
EXPOSE 8000

# 0.0.0.0 so the frontend container can reach it across the compose network.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
