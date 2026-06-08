# Caddy rebuilt with the Sablier plugin so it can start on-demand desktops
# (docker/desktops/) on first request and show a loading page while they boot.
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/sablierapp/sablier-caddy-plugin@v1.0.2

FROM caddy:2-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
