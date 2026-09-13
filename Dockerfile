# Conversational 360° — demo deployment.
#
# 3.13, not the 3.14 in .venv360: hosts lag the newest release, and the suite
# passes on both. Pin rather than `python:3-slim` so a rebuild six months from
# now produces the image that was tested, not whatever is current then.
FROM python:3.13-slim

# Fail fast and log straight through, so a crash-loop on the platform shows the
# traceback instead of an empty log buffer.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    C360_LLM_MODE=stub \
    C360_SEED_ON_BOOT=1 \
    C360_DEMO_BANNER=1

WORKDIR /app

# Dependencies first: this layer is cached unless requirements.txt changes, so
# an edit to a template rebuilds in seconds rather than reinstalling LangGraph.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY poc/ ./poc/
COPY app/ ./app/
COPY inputs/ ./inputs/

# `data/` is generated — the round is seeded on first boot by the lifespan hook.
RUN mkdir -p data

# Offline by default. The demo must never depend on an API key being present,
# and stub mode is the deterministic path the test suite exercises.
#
# $PORT is supplied by the platform (Render, Railway, Fly all set it); the
# fallback keeps `docker run -p 8000:8000` working locally. Shell form so the
# variable actually expands.
EXPOSE 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
