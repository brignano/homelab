# Runbook — switching ISP (Comcast/Xfinity → Frontier Fiber)

A checklist for install day. Written to be followed from a phone.

**Why this is mostly a router job:** nothing in the lab accepts inbound
connections except one Tailscale port. Remote access is Tailscale, the tunnel is
outbound-only, and certificates use DNS-01. So the ISP barely matters. What
matters is that the new router reproduces what the Xfinity router does today.
Do that and **no file in this repo changes**.

**Safety net:** keep the Xfinity router plugged in and Comcast active until
the end. If anything below fails, moving the m5's Ethernet cable back to the
Xfinity router restores everything as it was.

---

## Before install day

- [ ] **Book Frontier for a date that overlaps Comcast** by a few days. Do not
      cancel Comcast first. Check whether Comcast has an early-termination fee.
- [ ] **Ask Frontier which router they supply** (often an eero), and check it can
      set its **LAN subnet to `10.0.0.0/24` with the router at `10.0.0.1`**. This
      is the one hard requirement. See [If the router can't do
      10.0.0.0/24](#if-the-router-cant-do-1000024).
- [ ] **Write down the current Wi-Fi name and password**, exactly, including
      capitals. The new router reuses them so every smart-home device that Home
      Assistant reads rejoins on its own.
- [ ] **Move anything that logs in with `@comcast.net`.** That address is on the
      SSH keys (setup log) and may be the reset email for other accounts.
      Switch those to Gmail now. Do not count on Comcast keeping the mailbox
      after you leave.
- [ ] **Hold off on the iCloud mirror's first download** (~1.5 TB) until after
      the switch. Fiber has no data cap; some Xfinity plans cap at about
      1.2 TB a month.

## Install day — set up the new router *before* moving the m5

Do all of this on the Frontier router while the lab is still on the Xfinity
one. On an eero these live under *Settings → Network settings*.

- [ ] **LAN:** subnet `10.0.0.0/24`, router address `10.0.0.1`.
- [ ] **DHCP pool:** `10.0.0.2`–`10.0.0.199`. This keeps `.200` (Proxmox host)
      and `.201` (CT 100) out of the pool, because both set their own address
      and the router must never hand either to a phone.
- [ ] **DHCP reservation:** MAC `84:47:09:86:96:A4` → `10.0.0.200` (the m5).
- [ ] **Port forward:** UDP `41641` → `10.0.0.200`. This is Tailscale's direct
      path. Without it remote access still works, but through the slow relay
      that wedged in June (setup log, "*.home goes flaky").
- [ ] **Wi-Fi:** the same name and password as the Xfinity network.
- [ ] Leave the router's DNS at its default for now. Pointing DHCP DNS at
      AdGuard is a later step (see After).

## Cutover

`#alerts` will page while the box is offline: Healthchecks sees `heartbeat.sh`
go quiet. That is the dead man's switch working. Ignore it for the few minutes
this takes, or pause the checks in Healthchecks first.

- [ ] Turn the Xfinity router's Wi-Fi off, or unplug it from the wall, so
      devices move to the new network rather than staying on the old one.
- [ ] Move the **m5's Ethernet cable** to the Frontier router.
- [ ] Give it a minute. The host and CT 100 have static addresses, so nothing
      needs a reboot.

## Verify — in this order

On the Proxmox host (console or `ssh root@10.0.0.200` from a device on the new
network):

- [ ] `ping -c3 10.0.0.1` — the new router answers.
- [ ] `ping -c3 1.1.1.1` — the internet is reachable.
- [ ] `tailscale status | grep m5` — reads **direct**, not `relay`. If it says
      relay, check the UDP 41641 forward. `systemctl restart tailscaled` is the
      known fix for a stuck session.
- [ ] `dig @10.0.0.201 google.com +short` — AdGuard answers.

In CT 100 (`pct enter 100`, or `ssh root@10.0.0.201`):

- [ ] `cd /root/homelab && ./scripts/health.sh` — all green, or the same as
      before the switch.

From your phone:

- [ ] On home Wi-Fi: open one `*.$HOMELAB_DOMAIN` service, e.g. Grafana.
- [ ] **On cellular, Wi-Fi off, with Tailscale on:** open the same service. This
      is the test that proves remote access works through the new ISP.
- [ ] Home Assistant shows its devices online. If a device is missing, it did
      not rejoin the Wi-Fi. Check the network name and password match exactly.
- [ ] `#alerts` has sent its "back up" messages, and Healthchecks is green.

**If any step fails and you can't fix it quickly:** move the m5's cable back to
the Xfinity router and turn its Wi-Fi back on. You're back where you started,
and you can try again another day.

## After — once it has been stable for a few days

- [ ] **Return Comcast's equipment** (gateway and any cable box) and keep the
      receipt. Rented equipment gets billed if it isn't returned.
- [ ] **Cancel Comcast.** Confirm the final bill date.
- [ ] Add a `docs/setup-log.md` entry: the new router model, the settings
      above, and the new public IP. Tailscale picks the IP up by itself, so
      it's for the record only.
- [ ] *(Optional, now possible)* Set the router's DHCP DNS to AdGuard
      (`10.0.0.201`). Ad blocking and `*.home` names then work for every device
      at home, not just tailnet ones. This has been pending since June. Do it a
      week later, separately: if CT 100 is ever down, the whole house loses DNS.
- [ ] Start the iCloud mirror's first download.

---

## If the router can't do 10.0.0.0/24

Don't improvise. The `10.0.0.x` addresses are hardcoded in about 20 files and
80 places: the Proxmox host's static IP, CT 100's static IP, Tailscale's
advertised route, the Cloudflare wildcard DNS record, Prometheus, Home
Assistant, Caddy, AdGuard rewrites, the shell aliases, and the `ai-tools`
installers. Changing the subnet is one coordinated PR plus host-side edits,
made **before** the cutover.

Options, best first:
1. Ask Frontier for a router that can, or put your own router behind their
   ONT. Most consumer routers can set the LAN subnet.
2. Plan the renumbering as its own change. Ask Claude to generate the full list
   of edits, then run this runbook against the new addresses.
