"""The CI guard must fail on each mistake it exists for, and pass a correct
upstream route. Needs a caddy binary: CADDY=/path/to/caddy python tests/check_smoke.py
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parents[1]
REAL = (HERE.parent / "Caddyfile").read_text()
CADDY = os.environ.get("CADDY", "caddy")

# The commented example route in the Caddyfile, uncommented: the shape every
# real route copies, so it must pass.
example = "\n".join(re.sub(r"^# ?", "", line) for line in REAL.split("# route: alexa", 1)[1].splitlines()
                    if line.startswith("#"))
HEAD = REAL.split("# --- routes", 1)[0]


def route(body: str, name: str = "alexa") -> str:
    return HEAD + f"\nhttp://{{$GW_HOST_{name.upper()}:{name}.invalid}}:8080 {{\n\timport gateway_log\n{body}\n}}\n"


CASES = [
    ("the real Caddyfile", REAL, None),
    ("the example upstream route", HEAD + "\n# route: alexa\n" + example + "\n", None),
    ("answers before verifying", route("\troute {\n\t\trespond \"hi\" 200\n\t\timport verify alexa\n\t}"),
     "answers before forward_auth"),
    ("no forward_auth at all", route("\treverse_proxy host.docker.internal:8123"), "before forward_auth"),
    ("denylisted upstream", route("\troute {\n\t\timport verify alexa\n\t\t@a {\n\t\t\tmethod GET\n\t\t\tpath /x\n\t\t}"
                                   "\n\t\thandle @a {\n\t\t\trequest_body {\n\t\t\t\tmax_size 1KB\n\t\t\t}\n"
                                   "\t\t\timport strip_caller_credentials\n\t\t\treverse_proxy budget:5006\n\t\t}\n\t}"),
     "denylist"),
    ("wildcard path, no method, no limit, no strip",
     route("\troute {\n\t\timport verify alexa\n\t\t@a path /api/*\n\t\thandle @a {\n"
           "\t\t\treverse_proxy host.docker.internal:8123\n\t\t}\n\t}"),
     "without exact paths"),
    ("verifies the wrong route", route("\troute {\n\t\timport verify selftest\n\t\trespond 200\n\t}"),
     "expected `/verify/alexa`"),
    ("committed hostname", HEAD + "\nhttp://k7q2m9.example.com:8080 {\n\trespond 404\n}\n", "must be http://"),
    ("mismatched placeholder", route("\trespond 404").replace("{$GW_HOST_ALEXA:alexa.invalid}",
                                                           "{$GW_HOST_ALEXA:selftest.invalid}"), "different routes"),
]

failed = 0
with tempfile.TemporaryDirectory() as tmp:
    for name, text, expect in CASES:
        f = pathlib.Path(tmp) / "Caddyfile"
        f.write_text(text)
        out = subprocess.run([sys.executable, str(HERE / "check_gateway.py"), "--caddy", CADDY,
                              "--caddyfile", str(f), "--only-caddy"], capture_output=True, text=True)
        text_out = out.stdout + out.stderr
        ok = out.returncode == 0 if expect is None else (out.returncode != 0 and expect in text_out)
        print(f"{'ok  ' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n{text_out}"))
        failed += not ok

if failed:
    print(f"\n{failed} case(s) failed")
    sys.exit(1)
print("\nthe gateway guard catches every mistake it is for")
