# TSD — On-demand Kali Linux desktop VM

**Status:** Approved
**Date:** 2026-06-07
**Author:** Anthony Brignano

## Context & goal

I want a full Kali Linux **desktop** for security work, used in bursts (not 24/7).
The homelab is a single 16 GB box (~15 GiB usable after the BIOS iGPU-framebuffer
reclaim) already running the core, AI, monitoring, and proxy stacks — so a
pinned-24/7 desktop VM is tight. The
goal is an **on-demand** Kali VM: start it when needed, reach its desktop
remotely, shut it down after, with memory behaviour that coexists safely with
the existing workloads.

This is a **Proxmox VM** (qemu), not a Docker stack — so it lives outside the
`docker/<name>/` convention. It's documented here and in `docs/setup-log.md`.

## Decisions

| Choice | Decision | Why |
|---|---|---|
| Install | **Prebuilt Kali QEMU image** (qcow2) | Boots straight to a working XFCE desktop; no installer time |
| Remote access | **xrdp / RDP** over Tailscale | Snappy full desktop + clipboard via Microsoft Remote Desktop (Mac) |
| Sizing | **4 vCPU / 6 GB max RAM / 40 GB disk** | Comfortable XFCE desktop |
| RAM safety | **Ballooning: min 2 GB / max 6 GB** | Only holds what it's using (~2–3 GB idle); bursts to 6 only if host has room |
| Lifecycle | **On-demand** (`onboot=0`) | 0 RAM when off; started manually for a session |
| Exposure | **Tailnet-only** | Security tooling never goes public; no Cloudflare Tunnel route |

**VMID:** `110`  ·  **Name:** `kali`  ·  **Storage:** `local-lvm`  ·  **Bridge:** `vmbr0`

## Memory coexistence (the constraint)

After the BIOS iGPU reclaim, total is ~15 GiB with ~11 GiB available — so a 6 GB
Kali VM fits with a healthy buffer. Ballooning + on-demand keep it safe as other
workloads (Ollama especially) grow. Mitigations, in order:

1. **Ballooning (min 2 / max 6 GB)** — guest gives RAM back under host pressure.
   Idle Kali ≈ 2–3 GB; it only climbs toward 6 if the host is free.
2. **On-demand** — when the VM is stopped it uses nothing.
3. **Optional Ollama unload** — the docker LXC's ~4.85 GB is mostly Ollama's
   resident model. Since Kali sessions happen when *not* chatting, letting Ollama
   unload on idle frees ~2–3 GB during Kali use (trade-off: ~40 s cold-load next
   chat — see the 2026-06-07 Ollama setup-log entry). Apply only if RAM gets tight.
4. **Watch it** — the Capacity & Headroom dashboard's "RAM free" stat + the
   existing `hl-mem-low` alert (fires <10% available) give early warning.

## Security

- Change the default `kali` / `kali` password on first boot (`passwd`).
- `onboot=0` — never autostarts.
- RDP reachable only over the tailnet (VM gets a `10.0.0.x` IP on `vmbr0`,
  routed via the m5 Tailscale subnet router). **Not** exposed publicly.
- No Cloudflare Tunnel route. No host port-forwards.

## Implementation (run on the Proxmox host m5)

> All `qm` commands run on **m5** (`ssh root@10.0.0.200`). Exact Kali image URL
> from <https://www.kali.org/get-kali/#kali-virtual-machines> → **QEMU**.

```bash
# 1. Download + extract the prebuilt QEMU image (version string will differ)
cd /var/lib/vz/template/
wget <KALI_QEMU_7Z_URL>          # e.g. kali-linux-2025.x-qemu-amd64.7z
apt install -y p7zip-full
7z x kali-linux-*-qemu-amd64.7z  # yields a .qcow2

# 2. Create the VM shell (4 cores, 6 GB max / 2 GB balloon, virtio net, agent on)
qm create 110 --name kali --memory 6144 --balloon 2048 \
  --cores 4 --cpu host --net0 virtio,bridge=vmbr0 \
  --ostype l26 --agent enabled=1 --scsihw virtio-scsi-single

# 3. Import the disk and attach it
qm importdisk 110 kali-linux-*-qemu-amd64.qcow2 local-lvm
qm set 110 --scsi0 local-lvm:vm-110-disk-0 --boot order=scsi0
qm resize 110 scsi0 40G          # grow to 40 GB
qm set 110 --onboot 0            # on-demand only

# 4. Start it and open the console to get the IP / first login
qm start 110
# Proxmox UI -> VM 110 -> Console (login kali/kali), then:
#   passwd                       # change the default password immediately
#   ip a                         # note the 10.0.0.x address
```

Then, **inside Kali** (console or first SSH):

```bash
sudo apt update
sudo apt install -y xrdp
echo "xfce4-session" > ~/.xsession
sudo systemctl enable --now xrdp
sudo systemctl status xrdp --no-pager | head -5
```

Finally, from the **Mac**: Microsoft Remote Desktop → add PC `10.0.0.x` →
connect over Tailscale → log in as `kali`.

## Verify

- `qm config 110` shows `balloon 2048`, `memory 6144`, `onboot 0`.
- Kali boots; default password changed.
- RDP from the Mac lands on the XFCE desktop over Tailscale.
- Capacity dashboard: with Kali running idle, "RAM free" stays green-ish
  (≥2 GiB); stopping the VM (`qm stop 110`) returns RAM to ~6+ GiB.

## Rollback

```bash
qm stop 110 && qm destroy 110     # removes the VM and its disk
```

## Notes / out of scope

- No GPU passthrough (CPU-only box); fine for Kali's tooling.
- If Kali use becomes frequent/always-on, that's the trigger to buy a matched
  2×16 GB DDR4-3200 SODIMM kit (32 GB) — deferred for now due to the AI-driven
  DRAM price spike.
- Snapshot before big experiments: `qm snapshot 110 clean-base`.
