# 0003: Ask Home Assistant's Assist through a private Alexa skill

**Status:** accepted, 2026-11-09 · **TSD:** [`tsd-alexa-assist.md`](../docs/design/tsd-alexa-assist.md) · **Issue:** #363 · **Depends on:** #362 (secure gateway, ADR 0002)

## Decision

- A development-stage (never published) Alexa custom skill, "house helper",
  forwards free-form speech to Home Assistant's Assist.
- The skill's Lambda (`alexa/lambda/`, Python stdlib only, us-east-1) checks
  the skill ID, then calls `POST /api/conversation/process` through the secure
  gateway with its own Access service token. It holds no Home Assistant token.
- The gateway route allows only that path and method for `alexa-lambda` and adds
  the token of a non-admin HA user, `gateway-alexa`.
- Assist uses HA's Anthropic integration (Claude Haiku, local commands first,
  its own API key with a monthly limit).
- Assist exposes the same entities as Matterbridge's Alexa label. Locks, the
  alarm, the garage, the car's controls and the litter box are never exposed.
- A GitHub Action deploys the Lambda code through OIDC, using a role limited to
  `lambda:UpdateFunctionCode` on that one function.

## Why

The Echos stay as they are, gaining questions, multi-step commands and
follow-ups without new hardware or a firmware swap. The gateway keeps the one
public path behind four locks.

## Replaces

Nothing. Matterbridge keeps handling simple commands.
