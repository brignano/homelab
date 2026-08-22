# Small, single-purpose image: a Discord gateway client that talks to Ollama,
# Prometheus and Loki over the internal Docker networks. No inbound ports.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Declarative Discord server layout, applied by `--provision`.
COPY guild.yml .

# Drop privileges — this process needs nothing but an outbound socket.
RUN useradd --system --create-home --uid 10001 assistant
USER assistant

# A Discord client can lose its gateway socket and keep running, which a plain
# liveness check would call healthy. The bot rewrites this file every 60s, so a
# stale mtime means the event loop is wedged even though the process is up.
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
  CMD ["python", "-c", "import os,sys,time\ntry:\n    fresh = time.time() - os.path.getmtime('/tmp/assistant-heartbeat') < 180\nexcept OSError:\n    fresh = False\nsys.exit(0 if fresh else 1)"]

ENTRYPOINT ["python", "-m", "app"]
