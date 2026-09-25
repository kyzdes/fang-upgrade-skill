#!/usr/bin/env python3
"""oftarget — work out WHICH install a tool is about to act on, and refuse when
the answer is not unique.

Why this exists. `ofdoctor`, `ofhand`, `ofcron` and `ofbackup` used to carry a
built-in default target:

    OPENFANG_CONTAINER=openfang-openfang-1
    OPENFANG_HOME_HOST=/var/lib/docker/volumes/openfang_openfang-data/_data

Those are not exotic names — they are exactly what `docker compose` derives for
the upstream compose file (project `openfang`, service `openfang`, volume
`openfang-data`), so every reader of the quick start has a container by that
name if they have one at all. That is precisely the problem: the tools restart a
container and write into a data volume, and the default meant a stranger running
the quick start acted on whichever install happened to answer to the name, and a
reader with two installs (a live one and a staging one) silently got the live
one. Protocol rule 5 — a destructive action proves its target before acting, and
checks the name it acts under — was being broken by a constant.

The replacement does not guess a better name; it stops guessing:

  1. `OPENFANG_CONTAINER` set   → that is the target. Naming it is the proof.
  2. not set                    → look at the running containers and keep the
                                  ones that are recognisably OpenFang. Exactly
                                  one → use it and SAY so. Zero, or more than
                                  one → refuse (exit 2) and list what was seen.
  3. `OPENFANG_HOME_HOST` set   → that is the data directory.
  4. not set                    → read it off the chosen container's own mount
                                  table. Derived from the target, so it cannot
                                  name a different install than the container
                                  the tool is about to restart.

Nothing here has a fallback that keeps going after an ambiguity. A refusal costs
one environment variable; a wrong guess costs somebody else's data.

    oftarget.py container   # print the container name, or refuse
    oftarget.py home        # print the host path of its data volume, or refuse
    oftarget.py show        # print both, with how each was decided
"""

import json
import os
import subprocess
import sys

# Where the compose file mounts the data volume inside the container. This is a
# path in the image, not a name of anybody's install.
HOME_CTR_DEFAULT = os.environ.get("OPENFANG_HOME_CTR", "/data")


class Refused(Exception):
    """The target is not unique. Exit code 2: a tool refusal, not a defect."""


def _docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def looks_like_openfang(name, image):
    """Recognise the daemon by shape, not by a remembered name: an OpenFang
    container is one whose image reference or container name carries the project
    name. It is deliberately loose — a false candidate leads to a refusal that
    lists it, never to a silent wrong target."""
    return "openfang" in name.lower() or "openfang" in image.lower()


def candidates():
    r = _docker("ps", "--format", "{{.Names}}\t{{.Image}}")
    if r.returncode != 0:
        raise Refused("docker ps failed: " + (r.stderr.strip() or "no output"))
    out = []
    for line in r.stdout.splitlines():
        if "\t" not in line:
            continue
        name, image = line.split("\t", 1)
        if looks_like_openfang(name, image):
            out.append((name, image))
    return out


def choose(found):
    """Pure part of rule 2, so that the zero / one / many branches can be run
    without arranging that many containers on a host."""
    if len(found) == 1:
        return found[0][0]
    if not found:
        raise Refused(
            "no running container looks like an OpenFang daemon, and "
            "OPENFANG_CONTAINER is not set. Name the target explicitly:\n"
            "    OPENFANG_CONTAINER=<container> <tool> ...")
    listing = "\n".join(f"    {n}   ({i})" for n, i in found)
    raise Refused(
        f"{len(found)} running containers look like OpenFang daemons, so the "
        "target is ambiguous and nothing will be guessed:\n" + listing +
        "\nName the one you mean:\n    OPENFANG_CONTAINER=<container> <tool> ...")


def container():
    explicit = os.environ.get("OPENFANG_CONTAINER")
    if explicit:
        return explicit, "OPENFANG_CONTAINER"
    return choose(candidates()), "discovered (the only OpenFang container running)"


def home_host(name=None):
    explicit = os.environ.get("OPENFANG_HOME_HOST")
    if explicit:
        return explicit, "OPENFANG_HOME_HOST"
    if name is None:
        name = container()[0]
    r = _docker("inspect", "--format", "{{json .Mounts}}", name)
    if r.returncode != 0:
        raise Refused(f"cannot inspect container {name}: "
                      + (r.stderr.strip() or "no output"))
    try:
        mounts = json.loads(r.stdout or "[]") or []
    except json.JSONDecodeError as exc:
        raise Refused(f"cannot read the mount table of {name}: {exc}")
    for m in mounts:
        if m.get("Destination") == HOME_CTR_DEFAULT and m.get("Source"):
            return m["Source"], f"mount table of {name} ({HOME_CTR_DEFAULT})"
    raise Refused(
        f"container {name} has nothing mounted at {HOME_CTR_DEFAULT}, so its "
        "data directory on the host cannot be derived. Give it explicitly:\n"
        "    OPENFANG_HOME_HOST=<path> <tool> ...")


def main(argv):
    what = argv[1] if len(argv) > 1 else "show"
    try:
        if what == "container":
            print(container()[0])
        elif what == "home":
            print(home_host()[0])
        elif what == "show":
            c, why_c = container()
            h, why_h = home_host(c)
            print(f"container    {c}\n  decided by {why_c}")
            print(f"home on host {h}\n  decided by {why_h}")
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except Refused as exc:
        print(f"oftarget: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
