#!/usr/bin/env python3
"""Package the canonical standalone skill without breaking existing clones."""
import argparse
from pathlib import Path
import shutil
import stat

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "skills/fang-upgrade"
PACKAGING_SCRIPTS = {"build_plugin.py", "validate_package.py"}


def payload():
    result = {}
    for part in ["SKILL.md", "references", "scripts", "assets"]:
        path = ROOT / part
        candidates = [path] if path.is_file() else path.rglob("*")
        for source in candidates:
            if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".pyc":
                continue
            if source.parent == ROOT / "scripts" and source.name in PACKAGING_SCRIPTS:
                continue
            result[source.relative_to(ROOT)] = source
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    sources = payload()
    if args.check:
        actual = {p.relative_to(TARGET) for p in TARGET.rglob("*") if p.is_file()}
        bad = [
            str(rel)
            for rel, source in sources.items()
            if not (TARGET / rel).is_file()
            or (TARGET / rel).read_bytes() != source.read_bytes()
            or bool((TARGET / rel).stat().st_mode & stat.S_IXUSR)
            != bool(source.stat().st_mode & stat.S_IXUSR)
        ]
        if actual != set(sources) or bad:
            raise SystemExit("plugin payload differs; run python3 scripts/build_plugin.py")
    else:
        # Remove only obsolete generated payload files, never the canonical source.
        for path in TARGET.rglob("*"):
            if path.is_file() and path.relative_to(TARGET) not in sources:
                path.unlink()
        for rel, source in sources.items():
            target = TARGET / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    print(f"plugin payload: {len(sources)} files verified" if args.check else f"plugin payload: {len(sources)} files generated")


if __name__ == "__main__":
    main()
