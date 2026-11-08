#!/usr/bin/env python3
"""Keep private Jellyfin libraries off every user's home screen.

The Photo Archive (the iCloud mirror, docker/jellyfin/README.md) is personal.
By default Jellyfin puts every library on the home screen twice: as a tile
under "My Media" (its cover is a collage of the library's own photos) and as a
"Recently added in ..." row, which after a nightly mirror sync is last night's
photos. Anyone using the TV sees both.

This sets, for every Jellyfin user, the two per-user display settings that
hide a library from the home screen (Settings -> Home in the web UI):

  LatestItemsExcludes  no "Recently added in <library>" row
  MyMediaExcludes      no tile under "My Media" (and not in the side menu)

Nothing is deleted or rescanned, and access is unchanged: the library is still
there for anyone allowed to see it, through Search or a bookmark, and each user
can turn it back on under Settings -> Home. `--undo` turns it back on for all.

Which libraries: every library whose name starts with one of the
comma-separated prefixes in HL_JELLYFIN_PRIVATE_LIBRARIES (default
"Photo Archive", which also covers "Photo Archive (HEIC previews)").
Case-insensitive.

Run on CT 100 (needs no packages, only python3 and docker):

  ./scripts/jellyfin-hide-libraries.py --dry-run   # show what would change
  ./scripts/jellyfin-hide-libraries.py             # apply (idempotent)
  ./scripts/jellyfin-hide-libraries.py --undo      # put them back

The API key is the concierge's admin-scoped key (JELLYFIN_API_KEY in
docker/concierge/.env), or JELLYFIN_API_KEY from the environment. It is never
printed. Jellyfin publishes no port, so the script talks to the container's
address on the proxy_proxy bridge (override with JELLYFIN_URL).
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.environ.get(
    "HL_REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
ENV_FILE = os.environ.get(
    "HL_JELLYFIN_KEY_FILE", os.path.join(REPO, "docker/concierge/.env")
)
PREFIXES = [
    p.strip().lower()
    for p in os.environ.get("HL_JELLYFIN_PRIVATE_LIBRARIES", "Photo Archive").split(",")
    if p.strip()
]
SETTINGS = ("LatestItemsExcludes", "MyMediaExcludes")


def die(msg):
    print(f"jellyfin-hide-libraries: {msg}", file=sys.stderr)
    sys.exit(1)


def api_key():
    key = os.environ.get("JELLYFIN_API_KEY", "")
    if not key and os.path.exists(ENV_FILE):
        with open(ENV_FILE) as fh:
            for line in fh:
                name, _, value = line.strip().partition("=")
                if name == "JELLYFIN_API_KEY":
                    key = value.strip().strip("'\"")
    if not key:
        die(f"no JELLYFIN_API_KEY (environment or {ENV_FILE})")
    return key


def base_url():
    if os.environ.get("JELLYFIN_URL"):
        return os.environ["JELLYFIN_URL"].rstrip("/")
    try:
        ip = subprocess.run(
            ["docker", "inspect", "-f",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", "jellyfin"],
            check=True, capture_output=True, text=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError) as exc:
        die(f"can't find the jellyfin container ({exc}); set JELLYFIN_URL")
    if not ip:
        die("the jellyfin container has no network address; is it running?")
    return f"http://{ip[0]}:8096"


class Jellyfin:
    def __init__(self, url, key):
        self.url = url
        self.auth = f'MediaBrowser Client="homelab", Device="ct100", DeviceId="homelab-hide-libraries", Version="1", Token="{key}"'

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", self.auth)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def save_config(self, user_id, config):
        # Current route first; older servers only have the path form.
        try:
            self.call("POST", f"/Users/Configuration?userId={user_id}", config)
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 405):
                raise
            self.call("POST", f"/Users/{user_id}/Configuration", config)


def norm(guid):
    return str(guid).replace("-", "").lower()


def main(argv):
    flags = set(argv)
    unknown = flags - {"--dry-run", "--undo"}
    if unknown:
        die(f"usage: {sys.argv[0]} [--dry-run] [--undo]  (unknown: {' '.join(unknown)})")
    dry, undo = "--dry-run" in flags, "--undo" in flags

    jf = Jellyfin(base_url(), api_key())
    try:
        folders = jf.call("GET", "/Library/VirtualFolders")
        users = jf.call("GET", "/Users")
    except urllib.error.HTTPError as exc:
        die(f"Jellyfin answered {exc.code} {exc.reason}; is the key admin-scoped?")
    except urllib.error.URLError as exc:
        die(f"can't reach Jellyfin at {jf.url}: {exc.reason}")

    private = {
        norm(f["ItemId"]): f["Name"]
        for f in folders
        if any(f["Name"].lower().startswith(p) for p in PREFIXES)
    }
    if not private:
        die(f"no library name starts with {PREFIXES}; found: "
            + ", ".join(f["Name"] for f in folders))
    print("libraries: " + ", ".join(sorted(private.values())))

    changed = 0
    for user in users:
        config = user["Configuration"]
        notes = []
        for setting in SETTINGS:
            current = list(config.get(setting) or [])
            have = {norm(g) for g in current}
            if undo:
                new = [g for g in current if norm(g) not in private]
            else:
                new = current + [i for i in private if i not in have]
            if len(new) != len(current):
                notes.append(setting)
                config[setting] = new
        if not notes:
            print(f"  ok       {user['Name']}: already {'shown' if undo else 'hidden'}")
            continue
        changed += 1
        verb = "would set" if dry else "set"
        print(f"  {'dry-run' if dry else 'changed'}  {user['Name']}: {verb} {', '.join(notes)}")
        if not dry:
            jf.save_config(user["Id"], config)

    print(f"{changed} of {len(users)} user(s) {'to change' if dry else 'changed'}")


if __name__ == "__main__":
    main(sys.argv[1:])
