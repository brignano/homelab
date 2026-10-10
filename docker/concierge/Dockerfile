# The act lane: a small aiohttp server that runs one Claude Agent SDK task per
# request from the assistant bot. No inbound ports — see docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Its own uid, not the assistant's 10001: the two lanes hold different secrets
# and share nothing on disk. /srv/work is the agent's cwd — empty, because the
# agent has no file tools; it exists so the CLI never starts in /srv.
RUN useradd --system --create-home --uid 10002 concierge \
 && mkdir -p /srv/work && chown concierge /srv/work
USER concierge

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request\ntry:\n    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=5).status == 200 else 1)\nexcept Exception:\n    sys.exit(1)"]

ENTRYPOINT ["python", "-m", "app"]
