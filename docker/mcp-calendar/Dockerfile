# Read-only MCP server over the calendars Home Assistant can see (iCloud via
# CalDAV). Reached only through Caddy's bearer-gated mcp.$HOMELAB_DOMAIN site.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Its own uid: it holds an HA token and shares nothing on disk with the other lanes.
RUN useradd --system --uid 10003 calendar
USER calendar

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request\ntry:\n    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).status == 200 else 1)\nexcept Exception:\n    sys.exit(1)"]

ENTRYPOINT ["python", "-m", "app"]
