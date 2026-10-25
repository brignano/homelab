#!/usr/bin/env python3
"""The order the stacks have to come up in, read from the compose files.

Why this exists
---------------
Compose will not create an external network: a stack that joins `ai_ai` fails
at `up` with "network ai_ai declared as external, but could not be found"
until the `ai` stack has created it. So the stacks have a start order, and it
is written down nowhere except in their `networks:` blocks. `/bootstrap-stack`
used to carry a copy of it — `core → monitoring → ai → tunnel` — which was
wrong the day `proxy` arrived and stayed wrong through five more stacks. That
is the same hand-maintained-list shape check-dashboard.sh, check-probes.sh and
deploy-plan.sh exist to remove, so this derives it instead.

The cycle
---------
A plain topological sort is not enough, because the stacks are not acyclic:
`monitoring`'s blackbox-exporter joins `proxy_proxy` to probe through Caddy,
and `proxy`'s Caddy joins `monitoring_monitoring` to route to Grafana. Neither
stack can come up whole before the other. Both edges are wanted, so the plan
breaks the cycle instead of the design: it brings up the part of one stack
whose networks already exist, which creates that stack's own network, and
finishes the stack once the other side is up.

That relies on `docker compose up <service>...` creating only the networks the
named services use and leaving the rest — external ones included — unchecked,
which Compose v2 does (the project is trimmed to the selected services before
any network is touched). Networks a partial step creates are counted from the
services it starts, not from the whole stack, so the plan never assumes more
than the partial step made.

Usage
-----
    ./scripts/bootstrap-plan.py                 every stack under docker/
    ./scripts/bootstrap-plan.py core ai proxy   only these (e.g. the deployed ones)

Prints one step per line, `<stack>` or `<stack> <service>...` for a partial
step, with `#` lines saying why. Exits 1, naming the missing network and its
owner, when the named stacks cannot all come up — including when a stack needs
a network owned by a stack left off the command line. It reads files only and
never touches Docker, so it answers the same on the Mac, in CI and on CT 100.

It needs PyYAML. `docker compose config` is not a substitute: it will not
render a file whose ${VAR:?required} is unset (true of every stack before its
.env exists, which is when a bootstrap plan is wanted), and --no-interpolate
fails validation instead on a volume named by a variable.
"""

import pathlib
import sys

try:
    import yaml
except ImportError:
    sys.exit("bootstrap-plan.py needs PyYAML: apt-get install python3-yaml "
             "(CT 100) or pip install pyyaml")

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCKER = REPO / "docker"


def load(path):
    return yaml.safe_load(path.read_text())


def stack_model(stack, doc):
    """Per service: the (network name, is external) pairs it joins, its deps."""
    declared = doc.get("networks") or {}
    project = doc.get("name") or stack

    def resolve(key):
        spec = declared.get(key) or {}
        external = spec.get("external")
        if isinstance(external, dict):          # legacy `external: {name: x}`
            return external.get("name") or key, True
        name = spec.get("name") or (key if external else "%s_%s" % (project, key))
        return name, bool(external)

    services = {}
    for svc, spec in (doc.get("services") or {}).items():
        # A service with a profile is not started by `up -d`, only by
        # `docker compose run` (docker/photos/ runs from cron), so it has no
        # place in a start order.
        if spec.get("profiles"):
            continue
        if spec.get("network_mode"):
            nets = []
        else:
            nets = spec.get("networks") or ["default"]
        deps = spec.get("depends_on") or []
        services[svc] = {"nets": [resolve(n) for n in nets], "deps": list(deps)}
    return services


def main(argv):
    every = sorted(p.parent.name for p in DOCKER.glob("*/docker-compose.yml"))
    wanted = argv or every
    unknown = [s for s in wanted if s not in every]
    if unknown:
        sys.exit("no such stack: %s (have: %s)" % (" ".join(unknown), " ".join(every)))

    model = {s: stack_model(s, load(DOCKER / s / "docker-compose.yml")) for s in every}

    # Who creates each network, over every stack, so a missing one can be
    # blamed on its owner even when that owner was left out of the plan.
    owner = {n: s for s, svcs in model.items()
             for v in svcs.values() for n, ext in v["nets"] if not ext}

    # A stack with nothing `up` would start (every service has a profile) is
    # not a step.
    wanted = [s for s in wanted if model[s]]

    exists = set()
    pending = {s: set(model[s]) for s in wanted}
    steps = []          # (stack, services or None for "the rest", why or None)

    def missing(stack, svc):
        return sorted(n for n, ext in model[stack][svc]["nets"]
                      if ext and n not in exists)

    def startable(stack):
        """Pending services whose networks exist, and whose deps' do too."""
        svcs, ok = model[stack], {}

        def check(svc, seen=()):
            if svc not in ok:
                ok[svc] = (svc in svcs and svc not in seen
                           and not missing(stack, svc)
                           and all(check(d, seen + (svc,)) for d in svcs[svc]["deps"]))
            return ok[svc]

        return {s for s in pending[stack] if check(s)}

    def creates(stack, svcs):
        return {n for v in svcs for n, ext in model[stack][v]["nets"] if not ext}

    def start(stack, svcs, why=None):
        exists.update(creates(stack, svcs))
        whole = svcs == pending[stack]
        steps.append((stack, None if whole else sorted(svcs), why))
        pending[stack] -= svcs
        if not pending[stack]:
            del pending[stack]

    while pending:
        # Whole stacks first, alphabetically, rescanning after each one.
        whole = next((s for s in sorted(pending) if startable(s) == pending[s]), None)
        if whole:
            start(whole, set(pending[whole]))
            continue

        # Stuck: every pending stack needs a network nothing has made yet.
        # Start the part of one stack that can start AND creates one of those
        # networks — any other partial step is churn that unblocks nothing —
        # preferring the stack that leaves the fewest services behind.
        wanted_nets = {n for s in pending for v in pending[s] for n in missing(s, v)}
        partial = sorted((len(pending[s]) - len(startable(s)), s)
                         for s in pending
                         if creates(s, startable(s)) & wanted_nets)
        if not partial:
            for s in sorted(pending):
                for svc in sorted(pending[s]):
                    for n in missing(s, svc):
                        by = owner.get(n)
                        why = ("owned by %s, which is not in this plan" % by
                               if by and by not in wanted else
                               "owned by %s" % by if by else
                               "no stack in docker/ creates it")
                        print("cannot start %s/%s: needs %s (%s)" % (s, svc, n, why),
                              file=sys.stderr)
            return 1
        s = partial[0][1]
        ready = startable(s)
        held = sorted(pending[s] - ready)
        need = sorted({n for v in held for n in missing(s, v)})
        start(s, ready, "%s needs %s, so %s starts without %s for now"
                        % (", ".join(held), ", ".join(need), s, ", ".join(held)))

    started = set()
    for stack, svcs, why in steps:
        if why:
            print("# " + why)
        elif stack in started:
            print("# %s again, now that the rest of its networks exist" % stack)
        print(stack if svcs is None else "%s %s" % (stack, " ".join(svcs)))
        started.add(stack)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
