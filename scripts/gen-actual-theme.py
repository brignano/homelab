#!/usr/bin/env python3
"""Generate Actual Budget's pasteable theme from the design tokens (#313).

Actual accepts custom CSS only as a single `:root { --var: value; }` block:
no @import, no @font-face, no other selector. So the theme cannot point at
tokens.css the way the dashboard does; the values have to be in the block.

This resolves docker/budget/theme/actual.css (Actual variable -> design token,
the hand-written mapping and the only thing to edit) against the vendored
docker/dashboard/assets/design-tokens.css, and writes one block per mode:

    docker/budget/theme/actual-light.css
    docker/budget/theme/actual-dark.css

Usage:
    scripts/gen-actual-theme.py            # regenerate
    scripts/gen-actual-theme.py --check    # CI: fail if the files are stale
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "docker/dashboard/assets/design-tokens.css"
MAPPING = ROOT / "docker/budget/theme/actual.css"
OUT = {m: ROOT / f"docker/budget/theme/actual-{m}.css" for m in ("light", "dark")}


def tokens():
    css = re.sub(r"/\*.*?\*/", "", TOKENS.read_text(), flags=re.S)
    out = {}
    for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", css):
        out.setdefault(name, " ".join(value.split()))
    return out


def split_args(s):
    depth, cur, parts = 0, "", []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    return parts


def resolve(value, toks, mode):
    # var(--x) -> its value, recursively
    while True:
        m = re.search(r"var\((--[\w-]+)\)", value)
        if not m:
            break
        if m.group(1) not in toks:
            sys.exit(f"gen-actual-theme: unknown token {m.group(1)}")
        value = value[: m.start()] + toks[m.group(1)] + value[m.end():]
    # light-dark(a, b) -> the side for this mode (innermost last, balanced)
    while "light-dark(" in value:
        i = value.index("light-dark(")
        j, depth = i + len("light-dark("), 1
        while depth:
            depth += {"(": 1, ")": -1}.get(value[j], 0)
            j += 1
        args = split_args(value[i + len("light-dark("): j - 1])
        if len(args) != 2:
            sys.exit(f"gen-actual-theme: bad light-dark() in {value}")
        value = value[:i] + args[0 if mode == "light" else 1] + value[j:]
    return value


def build(mode, toks, mapping):
    # No comment in the output: Actual accepts only a :root block of variables.
    head = ""
    body = "".join(f"  {k}: {resolve(v, toks, mode)};\n" for k, v in mapping)
    return head + ":root {\n" + body + "}\n"


def main():
    toks = tokens()
    src = MAPPING.read_text()
    mapping = re.findall(r"^\s*(--color-\w+)\s*:\s*([^;]+);", src, flags=re.M)
    if len(mapping) < 150:
        sys.exit(f"gen-actual-theme: only {len(mapping)} mappings in {MAPPING}")
    stale = False
    for mode, path in OUT.items():
        text = build(mode, toks, [(k, v.strip()) for k, v in mapping])
        if "--check" in sys.argv:
            if not path.exists() or path.read_text() != text:
                print(f"gen-actual-theme: {path.relative_to(ROOT)} is stale", file=sys.stderr)
                stale = True
        else:
            path.write_text(text)
            print(f"wrote {path.relative_to(ROOT)}")
    sys.exit(1 if stale else 0)


main()
