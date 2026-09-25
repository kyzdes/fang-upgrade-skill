#!/usr/bin/env python3
"""Validate this repository's dual-host package contract without running tools."""
import json
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(root=ROOT):
    manifests = []
    for host in ("claude", "codex"):
        path = root / f".{host}-plugin/plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        require(isinstance(manifest, dict), f"{host}: manifest must be an object")
        require(manifest.get("name") == "fang-upgrade", f"{host}: wrong plugin name")
        version = manifest.get("version")
        require(isinstance(version, str) and SEMVER.fullmatch(version),
                f"{host}: version must be semver")
        require(isinstance(manifest.get("description"), str)
                and manifest["description"].strip(), f"{host}: missing description")
        require(isinstance(manifest.get("author"), dict)
                and isinstance(manifest["author"].get("name"), str)
                and manifest["author"]["name"].strip(), f"{host}: missing author.name")
        require("hooks" not in manifest and "mcpServers" not in manifest,
                f"{host}: operator package must not start hooks or MCP servers")
        manifests.append(manifest)
    require(manifests[0]["version"] == manifests[1]["version"],
            "Claude and Codex package versions differ")
    codex = manifests[1]
    require(codex.get("skills") == "./skills/", "Codex: skills must point to ./skills/")
    interface = codex.get("interface")
    require(isinstance(interface, dict), "Codex: missing interface")
    for key in ("displayName", "shortDescription", "longDescription",
                "developerName", "category"):
        require(isinstance(interface.get(key), str) and interface[key].strip(),
                f"Codex: missing interface.{key}")
    capabilities = interface.get("capabilities")
    require(isinstance(capabilities, list) and capabilities
            and all(isinstance(value, str) and value.strip() for value in capabilities),
            "Codex: capabilities must be non-empty strings")
    prompts = interface.get("defaultPrompt")
    require(isinstance(prompts, list) and 1 <= len(prompts) <= 3
            and all(isinstance(value, str) and 0 < len(value) <= 128 for value in prompts),
            "Codex: defaultPrompt must contain 1-3 short strings")
    require(not (root / "hooks").exists() and not (root / ".mcp.json").exists(),
            "Operator package must not auto-discover hooks or MCP servers")
    skill_dirs = sorted(path.name for path in (root / "skills").iterdir() if path.is_dir())
    require(skill_dirs == ["fang-upgrade"], "Expected one packaged skill directory")
    for skill in (root, root / "skills/fang-upgrade"):
        contents = (skill / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", contents, re.DOTALL)
        require(match, f"{skill}: missing YAML frontmatter")
        frontmatter = yaml.safe_load(match.group(1))
        require(isinstance(frontmatter, dict), f"{skill}: frontmatter must be an object")
        require(frontmatter.get("name") == "fang-upgrade", f"{skill}: wrong skill name")
        description = frontmatter.get("description")
        require(isinstance(description, str) and 0 < len(description) <= 1024,
                f"{skill}: description must be 1-1024 characters")
        for resource in ("scripts/oftarget.py", "assets/youtube-insights-hand/HAND.toml",
                         "assets/youtube-insights-hand/SKILL.md", "references/hands.md"):
            require((skill / resource).is_file(), f"{skill}: missing {resource}")


if __name__ == "__main__":
    try:
        validate()
    except (ValueError, OSError, yaml.YAMLError) as error:
        raise SystemExit(f"Package validation failed: {error}")
    print("Package contract validated for Claude and Codex")
