---
name: home-assistant
description: How Home Assistant is run in this homelab — what git owns versus the UI, host networking, tokens and users, adding a device or integration (HomeKit, Govee, Blink, Tesla, CalDAV), Alexa through Matterbridge and Echos through Alexa Media Player, the Home dashboard, and updating. Use when touching docker/homeassistant/ or docker/matterbridge/, when adding a smart-home device, automation, script or dashboard card, or when an HA, Alexa or Echo question comes up.
---

# Home Assistant

The full setup lives in [`docker/homeassistant/README.md`](../../../docker/homeassistant/README.md)
and [`docker/matterbridge/README.md`](../../../docker/matterbridge/README.md).
Read the section that matches the task before answering; this skill is the
short list of things that are easy to get wrong.

## What git owns, and what it doesn't

- **Git owns `config/configuration.yaml` and nothing else in `/config`.** It is
  a read-only single-file mount, so a change needs
  `up -d --force-recreate homeassistant`, never `restart`.
- **Everything else is UI state in the `homeassistant_config` volume:**
  integrations, users, areas, automations made in the UI, `scripts.yaml`
  (Tesla and Jax reports), HACS and its integrations. None of it is in git and
  there are no backups yet, so never suggest a step that wipes the volume.
- **Dashboards are pasted, not read.** `dashboards/home.yaml` is the source you
  paste into the Raw configuration editor. When the UI version changes, copy it
  back into the file.
- **Prefer the UI for anything the UI can do.** Put YAML in
  `configuration.yaml` only when it has to be right before the UI loads, or
  when it is an automation the repo should own (Blink, Sonarr airing).

## Networking and access

- **Host networking** (`network_mode: host`) is what makes HomeKit, mDNS and
  discovery work. Nothing reaches HA by container name: other containers use
  `host.docker.internal:8123` with `extra_hosts`. Never move it back to a
  bridge to "tidy up".
- **Never on the Cloudflare Tunnel.** HA is LAN and tailnet only, which rules
  out any route that needs public HTTPS (a self-built Alexa skill, Lambda).
- **No HA token is read-only.** Each reader gets its own non-admin user
  (`digest`, `concierge`) and long-lived token, kept in that stack's `.env`.
- **IP banning sees every tailnet device as one address** (Tailscale SNATs on
  the Proxmox host), so keep the threshold at 10 or more.

## Adding a device

Work down this list and stop at the first route that fits:

1. **A native local integration** (HomeKit Device, Matter once a Matter Server
   exists, a vendor's LAN API). With host networking, discovery usually finds
   it.
2. **A cloud integration** with a sign-in (Blink, Tesla Fleet, most locks and
   cameras).
3. **HACS**, for community integrations. They are not pinned and can break on
   an HA update, so nothing important should depend on one.

Known devices:

- **Honeywell T6 Pro thermostat:** HomeKit Device only (Resideo would not issue
  API access). Remove it from Apple Home before pairing.
- **Govee H617C strip:** Bluetooth-only, with no LAN Control and no Wi-Fi, so
  neither "Govee lights local" nor the cloud route works. The options are an
  ESP32 flashed as an ESPHome Bluetooth proxy plus the HACS "Govee BLE lights"
  integration, or a Wi-Fi/Matter strip. Check the model in the Govee app before
  answering for any other Govee device.
- **Tesla:** Fleet integration; the car is named **Spaceship**
  (`sensor.spaceship_*`). Readings are the car's last report, since Fleet does
  not wake it.
- **Jax (cat):** the `jax_*` template sensors in `configuration.yaml`. The
  `cat_litter_box_m2_*` sensors were all unavailable on 2026-10-14.

## Alexa and Echos

- **Alexa controls HA through Matterbridge** (entities labelled **Alexa**). A
  "Discovered: matter" card in HA is Matterbridge: **Ignore**, never Add.
- **HA makes Echos speak through Alexa Media Player** (HACS, unofficial Amazon
  API, holds the Amazon password). It logs out now and then, so it is for
  nice-to-haves only. Silent announcements mean Communications is off or Do
  Not Disturb is on.
- **Spoken questions need a script, the Alexa label and a routine per phrase.**
  Use command phrases ("car report"), not questions, which Alexa answers itself.
- **An Echo stuck on a spinning blue ring** is the Echo or Wi-Fi, not HA. Power
  cycle first. After a factory reset, put it back in its room group and check
  the reports still answer on it.

## Updating

By hand only: `homeassistant` is on `HL_NO_AUTOPULL` because its database
migrations are one-way. Read the release notes, `pull`, `up -d`, then check
HACS for updates (Alexa Media Player usually needs one).
