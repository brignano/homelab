# TSD: A local mirror of iCloud Photos, and a cleanup that keeps it under 2 TB

**Status:** 📝 draft, awaiting approval. Phase 1 waits on the 5 TB drive and the ISP switch.
**Date:** 2026-10-10
**Owner:** Anthony

## The one-paragraph version

iCloud Photos holds about 1.5 TB on the 2 TB plan, and it is the **only copy**.
iCloud is a sync service, not a backup: a deletion, a bad sync or a locked
account takes a photo away everywhere at once. The plan has two halves.
**(1) Mirror:** the homelab downloads every photo nightly to the 5 TB drive and
never deletes anything, which makes it the second copy, while iCloud itself
stays the offsite one. That is 3-2-1 for photos at no new monthly cost.
**(2) Cleanup:** on the Mac, use Apple's own duplicate detection and
quality scores to *propose* deletions into review albums, which you approve by
hand. That keeps the library under 2 TB, because the next iCloud tier is 6 TB
at about three times the price.

## Goals

1. **A second, independent copy of every photo and video**, current to within
   a day, on hardware you own.
2. **Stay on the 2 TB plan** by removing duplicates and junk, without ever
   deleting something you would have kept.
3. Nothing new to pay for each month, and nothing new reachable from the
   internet.

## Non-goals

- **Automatic deletion.** No script deletes a photo. Ever. Scripts propose, you
  decide, and Photos' *Recently Deleted* gives 30 days to change your mind.
- **Archiving off iCloud.** Moving old photos out of iCloud would save nothing
  today (the 2 TB plan is the cheapest that fits) and would make the homelab
  the only copy, which then needs its own backup and paid offsite storage.
  Revisit only if the library approaches 2 TB *after* cleanup. See the
  decision rule below.
- **A model that judges photos.** Apple already scores every photo on the
  device. The cleanup reads those scores; it does not train or run anything.

## Architecture

```mermaid
flowchart LR
  subgraph Apple["Apple"]
    IC[("iCloud Photos<br/>~1.5 TB · primary + offsite")]
  end
  subgraph Mac["Mac (Photos library)"]
    DUP["1 · Duplicates album<br/>(built in, merge)"]
    SC["2 · osxphotos script<br/>reads Apple's scores"]
    RA["3 · Review albums<br/>you delete by hand"]
    DUP --> SC --> RA
  end
  subgraph Lab["CT 100 · docker/photos"]
    ICPD["icloudpd<br/>nightly, download-only"]
    HC["Healthchecks ping<br/>+ textfile metrics"]
  end
  DRIVE[("5 TB drive<br/>/media/photos<br/>backup=0")]
  IC <-->|sync| Mac
  IC -->|read-only download| ICPD --> DRIVE
  ICPD --> HC
  RA -->|Recently Deleted, 30 days| IC
```

### Half 1 — the mirror (`docker/photos/`)

- **[`icloudpd`](https://github.com/icloud-photos-downloader/icloud_photos_downloader)**
  in a container, run nightly at **01:00** (before the 02:00 Postgres dump and
  the 02:30 vzdump, so the three never compete for disk).
- **Download only.** `--auto-delete` and `--delete-after-download` are **never**
  set. A photo deleted in iCloud stays in the mirror. That is the point: the
  mirror is what protects you from a deletion you didn't mean. The cost is
  that the mirror keeps what cleanup removes; see Open questions.
- **Layout:** `--folder-structure {:%Y/%m}` under `/media/photos/icloud/`, so
  the files are useful without any software: a folder per month, originals as
  Apple stores them (HEIC, MOV, and the live-photo pairs).
- **Storage:** the 5 TB drive, mounted into CT 100 with **`backup=0`** (see
  `tsd-backups-and-monitoring.md`, Phase 1 step 7). The mirror needs no backup
  of its own, because iCloud is the primary copy. If the photos ever left
  iCloud, that would stop being true.
- **No ports, no Caddy route.** It only makes outbound requests to Apple.
- **First sync:** about 1.5 TB. Do it **after the Frontier switch**
  (`docs/runbook-isp-switch.md`), because some Xfinity plans cap at about
  1.2 TB a month. After that, nightly runs move only what is new.

### Half 2 — the cleanup (on the Mac)

The deletions have to happen in Photos, so the tooling runs on the Mac. It
lives in this repo under `tools/photos/`, because the mirror and the cleanup
are one design, but it is run by hand on the Mac, never from cron.

1. **Duplicates, first and free.** Photos → *Duplicates* (macOS Ventura and
   later) finds exact and near duplicates, and *Merge* keeps the best-quality
   copy and combines their metadata. This step alone may be most of what
   "remove duplicates" needs.
2. **Score, then propose.** A Python script using
   [`osxphotos`](https://github.com/RhetTbull/osxphotos) reads what Photos has
   already computed for every photo: overall and aesthetic scores, blur and
   noise, screenshots, bursts and which frame was picked. It works from the
   library's database, so originals do not need to be downloaded to the Mac.
   Rules are code and thresholds, not a model: *Python decides, people approve.*
3. **Review albums.** Candidates go into albums, never into the trash:
   - `Review: duplicates Photos missed` (same capture time and similar score)
   - `Review: unpicked burst frames`
   - `Review: low quality` (blurred or poorly scored, below a threshold)
   - `Review: old screenshots` (older than N months)
   You open each album, select what to delete, and delete it in Photos.
4. **Every run starts as a dry run** that prints counts and estimated bytes per
   album, so the thresholds can be tuned before anything is added.

## Monitoring

Same rules as every other job here (the `alerting` skill):

- **Healthchecks ping** with the exit code, `HEALTHCHECKS_PHOTOS_URL`
  (period 1 day, grace 6 hours). This is the "did it run" signal.
- **Textfile metrics:** time of the last successful sync, photos in the mirror,
  bytes on disk, new items in the last run. A sync that "succeeds" but adds
  nothing for weeks is visible on a panel.
- **Re-authentication is the known failure.** Apple sessions expire. Reports
  say roughly every two months, and Advanced Data Protection adds conditions.
  When it expires, icloudpd fails and Healthchecks pages. The runbook fix is
  one command, `docker compose run --rm icloudpd --auth-only`, which asks for
  the 2FA code. The alert message names that command, so the 4 am page says
  what to do.

## Security

- **The session cookie is the credential.** After the first login, icloudpd
  keeps a session that can read your whole iCloud Photos library. It lives in a
  named volume, never in the repo. That volume is inside CT 100, so it is in
  the nightly vzdump, which is one more reason the backup drive should be
  encrypted (LUKS, `tsd-backups-and-monitoring.md` decision 2).
- **The Apple ID password** is needed only for the first login and each
  re-authentication. It is entered interactively, never stored in `.env`.
- **Advanced Data Protection:** if it is on, icloudpd needs *Settings → Apple ID
  → iCloud → Access iCloud Data on the Web* enabled. Turning that on is a real
  trade-off: it reopens web access to the account. Decide it deliberately.

## Storage math

| | Now | In 3 years (est.) |
|---|---|---|
| iCloud library | ~1.5 TB | capped at 2 TB by the plan |
| Mirror on the 5 TB drive | ~1.5 TB | ≤ 2 TB, plus whatever cleanup removed |
| Left for Jellyfin | ~3 TB | ~2–2.5 TB |

## Decision rule for archiving

Revisit archiving only if **after** cleanup the library is above **1.8 TB**. At
that point the choices are: pay for 6 TB (about $30/month), or archive the
oldest years to the homelab and buy offsite storage for that archive (about
$6/TB/month on B2) plus a backup drive large enough to hold a copy of it. Do
the arithmetic then, with the real numbers.

## Phases

| Phase | Needs | What |
|---|---|---|
| 0 | nothing | You: open Photos → *Duplicates* and merge. Note how much it saved. |
| 1 | 5 TB drive, ISP switch done | `docker/photos/` stack, first sync, Healthchecks check, metrics, alert. |
| 2 | Phase 1 | `tools/photos/` dry-run report: what each review album would hold. |
| 3 | You approve thresholds | The script fills the review albums; you delete by hand. |

## Open questions

- [ ] **Where is the rest of the "3 TB of photos"?** iCloud holds about 1.5 TB.
      If the rest lives only on a Mac or an external drive, it has **no** second
      copy and should move onto the 5 TB drive before anything else in this
      document happens.
- [ ] **Is Advanced Data Protection on?** See Security.
- [ ] **Should the mirror ever forget?** Download-only keeps every photo you
      delete in cleanup. Options: keep everything forever (simplest, safest),
      or a yearly, manually reviewed prune of files deleted from iCloud more
      than a year ago.
- [ ] **Does the Mac keep originals or "Optimize Mac Storage"?** osxphotos reads
      scores either way; it only matters if a rule ever needs the image itself.

## Rejected alternatives

- **Immich or PhotoPrism as the photo library.** Both are good, but they
  replace Photos rather than back it up, and they bring a database, machine
  learning workers and a web UI to run and patch on a 16 GB box. The goal is a
  second copy, not a second app. They can always be pointed at the mirror later.
- **Running cleanup on the homelab against the mirror.** The homelab can find
  duplicates in files, but it cannot delete anything in iCloud. Deletions have
  to happen in Photos, so the decisions are made where they are carried out.
- **Using a local LLM to judge photo quality.** Apple's on-device scores
  already exist for every photo, cost nothing to read, and a 3B text model
  cannot see images anyway.
