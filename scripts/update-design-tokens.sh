#!/usr/bin/env sh
#
# Pull the shared design system into the dashboard, and keep the two in step.
#
# Why this exists
# ---------------
# The dashboard is a brignano surface, so its colour, type and shape come from
# @brignano/design (github.com/brignano/design) rather than from choices made
# here. Two things stand between a published package and a container that runs
# `next start` on a config directory:
#
#   1. **Nothing on the box runs npm.** So the package's `tokens.css` is
#      vendored into docker/dashboard/assets/ and committed, pinned to the
#      version recorded beside it. `git pull` is the whole install.
#   2. **Homepage has no token layer.** It themes itself from ten CSS
#      variables, `--color-50` … `--color-900`, each holding *RGB channels*
#      rather than a colour ("250 250 250", not "#fafafa"). CSS cannot convert
#      one to the other, so something has to translate — and the moment that
#      translation is written by hand, the dashboard owns a private copy of the
#      palette and the design system has stopped being the source of truth.
#      So homepage-palette.css is generated from the vendored tokens, and CI
#      diffs it.
#
# Geist is vendored too, from @fontsource-variable/geist: `--sans` names it
# first and no one has it installed, so without the file the dashboard silently
# falls back to the system face and looks like a different family of site.
#
# Usage:
#   ./scripts/update-design-tokens.sh            # refresh at the pinned versions
#   ./scripts/update-design-tokens.sh 0.2.0      # move tokens to a version, or `latest`
#   ./scripts/update-design-tokens.sh --check    # regenerate and diff; no network
#
# --check is what CI runs: it proves the generated palette still matches the
# vendored tokens, which is the only drift a hand edit can introduce.
set -eu

PKG="@brignano/design"
FONT_PKG="@fontsource-variable/geist"
FONT_FILE="geist-latin-wght-normal.woff2"

REPO=$(cd "$(dirname "$0")/.." && pwd)
ASSETS="$REPO/docker/dashboard/assets"
TOKENS="$ASSETS/design-tokens.css"
PALETTE="$ASSETS/homepage-palette.css"
VERSION_FILE="$ASSETS/design-tokens.version"
FONT_VERSION_FILE="$ASSETS/fonts/geist.version"

mode=update
version=""
case "${1:-}" in
  --check) mode=check ;;
  "")      version=$([ -f "$VERSION_FILE" ] && cat "$VERSION_FILE" || echo latest) ;;
  -*)      echo "usage: $0 [version|--check]" >&2; exit 2 ;;
  *)       version=$1 ;;
esac

# Resolve a dist-tag to a concrete version, so what lands in the repo is always
# pinned even when the caller asked for `latest`.
resolve() {
  if [ "$2" = latest ]; then
    curl -fsSL "https://registry.npmjs.org/$1" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["dist-tags"]["latest"])'
  else
    printf '%s\n' "$2"
  fi
}

# fetch <package> <version> <path-inside-tarball> <destination>
fetch() {
  _name=$(printf '%s' "$1" | sed 's|.*/||')
  _tmp=$(mktemp -d)
  curl -fsSL "https://registry.npmjs.org/$1/-/$_name-$2.tgz" -o "$_tmp/pkg.tgz"
  tar -xzf "$_tmp/pkg.tgz" -C "$_tmp" "package/$3"
  mkdir -p "$(dirname "$4")"
  cp "$_tmp/package/$3" "$4"
  rm -rf "$_tmp"
}

if [ "$mode" = update ]; then
  command -v curl >/dev/null || { echo "update-design-tokens: curl is required" >&2; exit 1; }

  version=$(resolve "$PKG" "$version")
  echo "fetching $PKG@$version"
  fetch "$PKG" "$version" "tokens.css" "$TOKENS"
  printf '%s\n' "$version" > "$VERSION_FILE"
  echo "vendored $TOKENS"

  font_version=$(resolve "$FONT_PKG" "$([ -f "$FONT_VERSION_FILE" ] && cat "$FONT_VERSION_FILE" || echo latest)")
  echo "fetching $FONT_PKG@$font_version"
  # Latin only. The other subsets are another 100 kB for glyphs this dashboard
  # will never render, and it is served over a LAN to a phone.
  fetch "$FONT_PKG" "$font_version" "files/$FONT_FILE" "$ASSETS/fonts/$FONT_FILE"
  fetch "$FONT_PKG" "$font_version" "LICENSE" "$ASSETS/fonts/LICENSE-Geist.txt"
  printf '%s\n' "$font_version" > "$FONT_VERSION_FILE"
  echo "vendored $ASSETS/fonts/$FONT_FILE"
fi

[ -f "$TOKENS" ] || { echo "update-design-tokens: $TOKENS not found — run without --check first" >&2; exit 1; }
version=$(cat "$VERSION_FILE" 2>/dev/null || echo unknown)

out=$PALETTE
[ "$mode" = check ] && out=$(mktemp)

TOKENS="$TOKENS" OUT="$out" VERSION="$version" python3 - <<'PY'
import os, re, sys

tokens = open(os.environ["TOKENS"], encoding="utf-8").read()

def ramp(selector):
    """The `--n-*` steps declared in one theme scope of tokens.css."""
    start = tokens.index(selector)
    body = tokens[start:tokens.index("\n}", start)]
    found = dict(re.findall(r"--n-(\d+):\s*(#[0-9a-fA-F]{6})", body))
    if len(found) < 12:
        sys.exit(f"update-design-tokens: only {len(found)} neutral steps under {selector!r}")
    return found

light = ramp(":root {")
dark = ramp(":root[data-theme='dark']")

def channels(hexcolour):
    h = hexcolour.lstrip("#")
    return " ".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))

# Homepage's ramp always runs light -> dark, in BOTH themes: in light it takes
# surfaces from the low end and ink from the high end, and in dark it does the
# reverse (page background from --color-800, ink from --color-200). The design
# system's ramp inverts between themes instead, so the two cannot be mapped
# step for step. Each line below is chosen from what Homepage *does* with that
# step, not from the number it carries.
LIGHT = [
    ("50", "25", "page background (--bg-color)"),
    ("100", "50", "faintest wash — bg-theme-100/20"),
    ("200", "100", "card wash and hairline — bg-theme-200/50"),
    ("300", "200", "hover surface"),
    ("400", "300", "strong border"),
    ("500", "500", "muted label, focus ring"),
    ("600", "600", "scrollbar thumb"),
    ("700", "700", "secondary text — text-theme-700"),
    ("800", "900", "primary ink — text-theme-800 is the main text class in light"),
    ("900", "900", "shadows, and the darkest wash"),
]
DARK = [
    ("50", "900", "lightest step"),
    ("100", "800", ""),
    ("200", "900", "primary ink — dark:text-theme-200 is the main text class"),
    ("300", "800", "hover ink"),
    ("400", "700", "secondary ink"),
    ("500", "500", "muted label"),
    ("600", "400", "scrollbar thumb"),
    ("700", "300", "scrollbar track"),
    ("800", "0", "page background (--bg-color) — the system's --bg"),
    ("900", "0", "washes and shadows — see the note above"),
]

DARK_NOTE = """  /* Dark surfaces are NOT darker than the page. The system builds elevation in
     dark by getting *lighter*, and Homepage already agrees where it matters —
     service cards are `dark:bg-white/5`. So --color-900 sits at the page colour
     and its washes read as nothing, rather than sinking containers below the
     background and contradicting the rule. */"""

def block(selector, mapping, ramp_, note=None):
    lines = [selector + " {"]
    if note:
        lines.append(note)
    width = max(len(f"  --color-{step}: {channels(ramp_[n])};") for step, n, _ in mapping)
    for step, n, why in mapping:
        decl = f"  --color-{step}: {channels(ramp_[n])};"
        comment = f"n-{n}" + (f" — {why}" if why else "")
        lines.append(f"{decl:<{width + 2}} /* {comment} */")
    lines.append("}")
    return "\n".join(lines)

header = f"""/* GENERATED FILE — do not edit.
   Rebuilt from design-tokens.css (@brignano/design@{os.environ['VERSION']}) by
   scripts/update-design-tokens.sh, which is also what CI diffs it against.

   Homepage themes itself from ten variables holding RGB *channels*, so this is
   the design system's neutral ramp in the only form it can read. The selectors
   carry an attribute match so they outrank Homepage's own `.theme-<colour>`
   block whichever palette the `color:` setting happens to name. */"""

open(os.environ["OUT"], "w", encoding="utf-8").write("\n\n".join([
    header,
    block('html[class*="theme-"]', LIGHT, light),
    block('html.dark[class*="theme-"]', DARK, dark, DARK_NOTE),
]) + "\n")
PY

if [ "$mode" = check ]; then
  if diff -u "$PALETTE" "$out" >/dev/null 2>&1; then
    echo "ok       homepage-palette.css matches design-tokens.css ($PKG@$version)"
    rm -f "$out"
  else
    echo "STALE    homepage-palette.css does not match design-tokens.css" >&2
    diff -u "$PALETTE" "$out" >&2 || true
    rm -f "$out"
    echo >&2
    echo "Run ./scripts/update-design-tokens.sh to regenerate it." >&2
    exit 1
  fi
else
  echo "generated $PALETTE from $PKG@$version"
fi
