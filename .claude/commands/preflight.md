# Preflight Check

Before bringing up any stack, run this preflight to catch common setup issues early.

## What to check

For each stack the user wants to bring up (or all stacks if unspecified), verify:

### 1. `.env` file exists

For each stack directory (`docker/core`, `docker/monitoring`, `docker/ai`, `docker/tunnel`):
```bash
ls docker/<stack>/.env
```
If missing, remind the user to copy from `.env.example` and fill in values.

### 2. Required environment variables are set

Parse the stack's `docker-compose.yml` for `${VAR:?required}` patterns and confirm each is present and non-empty in the corresponding `.env` file.

### 3. External Docker networks exist

Check that any `external: true` networks referenced in the compose file exist:
```bash
docker network ls --format '{{.Name}}'
```
If a required network is missing, tell the user which stack creates it and that it must be brought up first.

### 4. Tailscale is connected

```bash
tailscale status
```
Confirm the output shows `logged in` and at least one peer. If Tailscale is not connected, warn the user — services will be unreachable from other devices until it is.

## Output format

Report each check as PASS or FAIL with a one-line explanation. If any check fails, stop and list all failures before suggesting next steps. Do not proceed to bring up the stack if a FAIL is present — surface the issues for the user to resolve first.
