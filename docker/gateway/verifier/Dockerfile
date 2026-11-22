# Lock 3 of the secure gateway: re-checks the Cloudflare Access token per route.
# Reached only by the gateway Caddy, over gateway_verifier. See verifier.py.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY verifier.py .

# Its own uid: it holds nothing secret, but it decides who gets in.
RUN useradd --system --uid 10004 verifier
USER verifier

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request\ntry:\n    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/healthz', timeout=5).status == 204 else 1)\nexcept Exception:\n    sys.exit(1)"]

ENTRYPOINT ["python", "verifier.py"]
