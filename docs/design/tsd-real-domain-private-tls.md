# TSD: Real domain, real certificates, still private

**Status:** ✅ approved / shipped
**Date:** 2026-08-23
**Owner:** Anthony

## Problem

Services live at `stats.home`, `chat.home` and so on — a made-up TLD that only
resolves through AdGuard's rewrites on the tailnet. Two costs:

- **No real certificates are possible.** No public CA will ever issue for
  `.home`, so everything is plain HTTP. `kali.home` needs a secure context for
  Selkies, which forced Caddy's internal CA and a browser trust prompt on every
  device.
- **Everything depends on AdGuard split-DNS.** A device not using it — a guest,
  a phone with private DNS, anything off the tailnet resolver — sees nothing.

The obvious fix, "put it on my real domain", usually implies publishing it. That
is not wanted: `AGENTS.md` explicitly rules out exposing admin services, and
Grafana and Portainer are admin services.

## Goals

- Real domain names with real, publicly-trusted certificates.
- **No inbound exposure whatsoever.** No port forward, no tunnel, no public
  endpoint. The security posture must be identical to before.
- Retire the internal-CA workaround.
- Adding a service later must not require a DNS change.

## Non-goals

- Publishing anything. The tunnel stays undeployed and reserved for things
  genuinely meant to be public (Jellyfin, app previews).
- Removing AdGuard. It stays as the tailnet resolver and ad blocker.
- Breaking existing bookmarks on day one.

## Design

**Public DNS, private address.** A single wildcard record —
`*.<HOMELAB_DOMAIN> → 10.0.0.201`, Cloudflare proxy **off** — makes every
service name resolvable. The address is RFC1918, so the name resolves for
anyone while only LAN and tailnet devices can actually connect. Nothing is
published; the lookup succeeds and the connection does not.

**Certificates via ACME DNS-01.** Caddy proves domain ownership by writing a
TXT record to the Cloudflare zone, never by receiving a connection. This is the
crux: HTTP-01 needs port 80 reachable from the internet, which is exactly what
is being refused. DNS-01 has no such requirement, so a host the internet cannot
reach can still hold genuine Let's Encrypt certificates.

Caddy is already built with `xcaddy` for the Sablier plugin, so this is one
extra `--with github.com/caddy-dns/cloudflare` line.

**Wildcard DNS, per-name certificates.** One DNS record covers every service
forever — adding one is a Caddyfile block and a restart, no DNS change. Caddy
still issues a certificate per hostname, which keeps the Caddyfile readable as
one block per service rather than a single wildcard block with host matchers.
Seven certificates is nothing against Let's Encrypt's limits.

**Legacy names redirect.** Each `*.home` name is kept as an `http://` site that
redirects to its real counterpart. They are declared `http://` deliberately —
`.home` is not a real TLD, so Caddy must never attempt issuance for it. This
avoids a flag day: bookmarks, shell aliases and the dev machines' MCP config
keep working while they are updated at leisure.

## Decisions

**Nested under a subdomain** (`home.example.com`) rather than at the apex.
Keeps homelab names out of the main namespace, and means one wildcard cannot
accidentally shadow a real service on the apex domain later.

**A scoped API token, not the Global API Key.** The token needs exactly
`Zone → DNS → Edit` on the one zone. Writing one TXT record does not justify
account-wide credentials, and this token lives on the box being protected.

**`HOMELAB_DOMAIN` is a variable, not baked in.** The Caddyfile is public in
this repo; the domain lives in `.env` alongside the token.

**Both new vars are `:?required`.** Without the domain Caddy would try to serve
sites literally named `stats.` and issuance would fail confusingly. Failing at
`compose up` is much cheaper than debugging ACME errors.

## Trade-offs accepted

- **Public DNS reveals `10.0.0.201`.** It is RFC1918 and meaningless off the
  network. A very common pattern; noted rather than hidden.
- **Off-tailnet, names resolve then time out.** That looks like a fault but is
  the design working. Documented in the README.
- **Certificate issuance now depends on Cloudflare's API** being reachable at
  renewal time. Caddy renews well before expiry and retries, so a transient
  outage is harmless.

## Rejected alternatives

| Option | Why not |
|--------|---------|
| Cloudflare Tunnel / port forward | Publishes admin services. Ruled out by `AGENTS.md`, and unnecessary — DNS-01 gets the certificates without it. |
| Keep the internal CA, just rename | Every device still needs the CA installed, and the browser warning stays. Half the point is retiring that. |
| Split-horizon DNS (private records only in AdGuard) | Works, but keeps every device dependent on AdGuard for name resolution — one of the two problems being solved. |
| Wildcard certificate instead of per-name | One certificate, but the Caddyfile collapses into a single block with host matchers and becomes harder to read. Rate limits make per-name free. |
| `tls internal` everywhere | Same trust-prompt problem as today, on every device, forever. |

## Revisit if

- **Something genuinely needs publishing** — deploy `cloudflared` for that
  service specifically, with Cloudflare Access in front. This design does not
  block it and does not do it by accident.
- **The DNS provider changes** — swap the Caddy DNS plugin; nothing else moves.
- **`.home` redirects stop being used** — delete that Caddyfile section and the
  AdGuard rewrites. AdGuard stays for ad blocking.
