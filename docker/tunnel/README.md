# tunnel — the public exposure boundary

`cloudflared` is the only thing on this box that accepts traffic from the
internet. Everything else is reachable solely over the LAN or the tailnet (see
[`AGENTS.md`](../../AGENTS.md) — the `*.$HOMELAB_DOMAIN` names resolve publicly
but point at `10.0.0.201`, a private address).

So this directory owns two questions, not one: *what gets out*, and *what
someone sees when they reach something they cannot open*.

## What someone sees when they are denied

Put Cloudflare Access in front of a hostname and there are three distinct
audiences, which want opposite things:

| Who | What they hit | What they need |
| --- | --- | --- |
| You, session expired | Login page | To not notice it happened |
| Someone forwarded a link by family | Login page, then the block page | To understand, and a way out to the public site |
| Scanners, MCP clients, `curl` | Whatever you configured | A status code, not HTML |

The second row is the one worth designing for. A family member shares a link
without thinking about who can open it, and the recipient lands on something
that reads like an error they caused. They will never have access — the goal is
that they leave understanding that, and knowing where the public site is.

### The order these pages actually appear

This trips people up: **the block page only renders after a successful login.**
Someone with no session gets the *login* page first. Most forwarded strangers
bounce there and never see the block page at all.

That makes the login page the higher-traffic surface of the two, and it is
customizable on every plan. Set it under **Zero Trust → Reusable components →
Custom pages → Access login page**: organization name, logo, header, footer,
background. Whatever you put there is what most people will actually read.

### Free plan vs. paid

Both the **Custom page template** and the **Redirect URL** block-page options
require a **Pay-as-you-go or Enterprise** Zero Trust plan. On the free plan
(50 users) you get exactly one lever: the message string on the default page.

**On the free plan**, per application → **Additional settings → Custom block
pages → Cloudflare default**, and set the message to something like:

> This part of the site is private and shared with a few people. If someone
> sent you this link, they may have meant to share something public — visit
> brignano.io for the main site.

Assume plain text and no clickable link; write the domain out so it is still
useful when it does not render as one. Pair it with a login-page footer
carrying the same message, since that is the page most visitors stop at.

**On Pay-as-you-go or better**, use [`access/block-page.html`](access/block-page.html)
instead — same message, but designed, with a working link out and a
"different account" escape hatch:

1. Substitute `SITE_NAME` and `SITE_URL` in the file.
2. **Zero Trust → Reusable components → Custom pages → Manage → Add a page
   template**, type **identity block page**, paste the HTML.
3. Reference it per application under **Additional settings → Custom block
   pages → Custom page template**.

The file is deliberately self-contained — no stylesheet, no font CDN, no image
— because Cloudflare serves it as a standalone template. Keep it that way.

### The single most valuable element

`/cdn-cgi/access/logout`. The most common real denial is not a stranger; it is
you or a family member signed into the wrong Google account. Without a link to
sign out, that person is stuck in a loop with no affordance at all. It is one
anchor tag and it removes the worst dead end on the page.

## Session duration is the real UX lever

Most Access setups feel hostile purely because everything inherits the 24-hour
default and you re-authenticate constantly on things that do not warrant it.
Set it per application:

| Service | Suggested session | Why |
| --- | --- | --- |
| dashboard, `chat`, `stats`, `alerts` | 1 month | Read-mostly, used daily |
| `apps` (Portainer), `dns` (AdGuard) | 24 hours | Can change the state of the box |
| `kali` | Short | Already gated behind a Sablier cold boot |

Also turn on **Apply instant authentication** where a single identity provider
is configured. It skips Cloudflare's "choose a login method" interstitial and
sends you straight to the provider, so a re-auth reads as a slow page load
rather than a login.

## Non-browser endpoints must not get HTML

`mcp.$HOMELAB_DOMAIN` speaks streamable HTTP to an MCP client, and Caddy
already fails it closed with a plain `401` (see [`../proxy/Caddyfile`](../proxy/Caddyfile)).
If Access ever goes in front of it, turn on **401 Response for Service Auth
policies** on that application. An MCP client handed a styled block page
reports a parse error, not an auth error, and that is an afternoon lost.

For anything doing XHR against a protected origin, sending
`X-Requested-With: XMLHttpRequest` makes Access return `401` on an expired
session instead of an HTML redirect.

## Error 1050 is not a denial

If you see **Error 1050**, no Access policy rejected anyone. It is the
account-level **Require Access protection** deny-by-default page, and it means
the hostname has *no matching Access application at all* — so Cloudflare has
nothing to authenticate against and cannot show a login page.

It is a misconfiguration signal. Fix it by creating an Access application for
the hostname, or by exempting the hostname under **Zero Trust → Settings →
Authentication**. Do not try to restyle it; none of the settings above apply to
it, because it renders before any application is in play.
