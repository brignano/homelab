# Caddy rebuilt with three plugins:
#   sablier    — starts on-demand desktops (docker/desktops/) on first request
#                and shows a loading page while they boot.
#   cloudflare — solves the ACME DNS-01 challenge by writing a TXT record to the
#                zone. That's what lets these sites hold real Let's Encrypt
#                certificates while remaining unreachable from the internet:
#                ownership is proven through DNS, so no inbound connection is
#                ever required. HTTP-01 could not work here — it needs port 80
#                reachable from outside, which is exactly what we refuse to do.
#   replace-response — the `replace` directive, which rewrites a proxied
#                response body. The budget site uses it to add a small phone
#                stylesheet to Actual's page (see the Caddyfile).
FROM caddy:2-builder AS builder
RUN xcaddy build \
      --with github.com/sablierapp/sablier-caddy-plugin@v1.0.2 \
      --with github.com/caddy-dns/cloudflare \
      --with github.com/caddyserver/replace-response@80962887e4c680abfd1b11ce1403fa8cc0fdd3d6

FROM caddy:2-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
