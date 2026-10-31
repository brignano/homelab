# Clip review page: a stdlib-only HTTP server, so there is nothing to pip install.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv
COPY app ./app

# uid 10003: its own, shares nothing on disk with the other stacks.
RUN useradd --system --uid 10003 clipreview
USER clipreview

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request\ntry:\n    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).status == 200 else 1)\nexcept Exception:\n    sys.exit(1)"]

ENTRYPOINT ["python", "-m", "app"]
