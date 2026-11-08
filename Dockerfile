FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY contest ./contest
COPY scripts/setup_native.py ./scripts/setup_native.py
COPY vendor/mattervis-disorder-fixes.patch ./vendor/mattervis-disorder-fixes.patch
RUN python -m venv /app/.venv && /app/.venv/bin/python scripts/setup_native.py

ENV HOST=0.0.0.0 PORT=50001 PATH="/app/.venv/bin:$PATH"
EXPOSE 50001
CMD ["python", "-m", "contest.native_app"]
