# fang-upgrade-skill

Operating manual for [**fang-upgrade**](https://github.com/kyzdes/fang-upgrade) — a patched
fork of OpenFang v0.6.9 — written for an AI agent operating a live install.

This was the fork-aware companion to
[openfang-skill](https://github.com/kyzdes/openfang-skill), which documented stock upstream
v0.6.9 — that repo is now archived, so it will not receive further updates. If you are running
unmodified OpenFang, treat its content as a frozen snapshot rather than a maintained source.

## What is in here

| | |
|---|---|
| `SKILL.md` | the manual itself — daemon, agents, hands, cron, providers, channels, the API |
| `references/` | eleven deep-dives loaded on demand: architecture, providers and models, hands, automation, the 65 builtin tools, the security model, skills and ClawHub, channels/MCP/A2A, known issues, a worked pipeline, and a fresh-server runbook |
| `scripts/` | thirteen scripts: `ofctl` (authenticated API calls), `ofdoctor` (health pass with a secret-leak scan), `ofhand`, `ofcron`, `ofbackup`, `oftarget.py` (works out *which* install a tool is about to act on, and refuses when that is not unique), `ofcheck-rs`, `ofgate` (the three cargo commands Fork CI's `check` job runs — **not** a gate; see `scripts/README.md`), `ofmutate` (proves a patch's test goes red without the patch), `ofledger` (rolls up the workflow journals), `ofscrub` (checks the placeholder promise below), plus intake tools for YouTube and RuTube |
| `evals/` | trigger evaluations for the skill description |

## Install from the marketplace

Claude Code:

```text
/plugin marketplace add kyzdes/claude-skills
/plugin install fang-upgrade@claude-skills
```

Codex CLI:

```text
codex plugin marketplace add https://github.com/kyzdes/claude-skills.git
codex plugin add fang-upgrade@claude-skills
```

The packaged skill lives in `skills/fang-upgrade/`. Use the path of the loaded
`SKILL.md` to resolve its scripts and assets. No daemon, MCP server, or updater
starts during installation. Native host update policy controls updates.

The old `openfang@claude-skills` entry migrates to `fang-upgrade`. Existing users
must install the new plugin once; the old upstream skill is an archived snapshot.

## Standalone install (still supported)

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/kyzdes/fang-upgrade-skill.git ~/.claude/skills/fang-upgrade
chmod +x ~/.claude/skills/fang-upgrade/scripts/*
export FANG_SKILL_DIR="$HOME/.claude/skills/fang-upgrade"
export PATH="$FANG_SKILL_DIR/scripts:$PATH"
```

Point the tools at your install. **They do not ship a default target.** `ofdoctor`,
`ofhand`, `ofcron`, `ofbackup` and `ofctl` used to default to a container name and a
volume path — names that `docker compose` derives for *everybody* who follows the upstream
compose file, so the default silently acted on whichever install answered to the name, and
picked the live one for anybody running a staging box beside it. Now the target is
discovered from the running containers and **refused when it is not unique**:

```bash
oftarget.py show                  # which container, which data directory, and how each was decided
export OPENFANG_URL=http://127.0.0.1:4200
export OPENFANG_CONTAINER=<container>     # only needed when more than one is running
ofctl -x version GET /api/health
ofdoctor
```

With one OpenFang container running, nothing needs setting: the tools find it and say which
one they picked. With none, or with two, they exit 2 and list what they saw rather than
guess. `OPENFANG_HOME_HOST` is likewise read off the chosen container's own mount table, so
it cannot name a different install than the container the tool is about to restart.

## Testing a patch against the fork

The fork ships its own harness for exercising LLM-provider and Telegram edge cases against
a real daemon — a scripted fake OpenAI-compatible provider plus a fake Telegram Bot API,
run as containers in the staging box's netns. It lives in the fork's repo, not in this
skill: `tests/fang/harness/` (`fangrig --help`, `tests/fang/harness/README.md`).

## Reading it

Addresses and host names appear as `<tailnet-ip>`, `<public-ip>`, `<tailnet-host>`; the passkey
relying party appears as `<rp-host>` and a passkey slot as `<slot>` — substitute your own. That is
a promise, so it is checked by a run rather than by eye: `scripts/ofscrub` scans the whole tree —
**including its own source**, with no exemption — and exits non-zero on a hit.

It checks *shapes*, not a list of names, and it reports a hit by file and line without printing
the value. A guard for a public repository cannot hold the names it hunts: an earlier version
stored them as `(length, FNV-1a, SHA-256)` and a 1603-word dictionary recovered every one of them
in a fraction of a second. So the rules ask what a value *is*, not whose it is: an IP literal that
is globally routable, a Tailscale carrier-NAT address, a `*.ts.net` name, or any non-placeholder
value standing in a key whose content is the install's identity (`rp_id`, `rp_origin`, `slot`, an
`openfang auth` slot argument, `OPENFANG_URL`). It therefore catches an install that is not this
one — verified against a canary of a foreign deployment's values — and stays silent on loopback,
RFC 1918, the RFC 5737/3849 documentation ranges and `.example`/`.invalid`/`.test` names. What it
cannot see (a bare domain in prose, a container name) is written in its own header, so a green run
is not mistaken for proof of more than it checks. Paths assume the Docker install described in
[fang-upgrade/INSTALL-AGENT.md](https://github.com/kyzdes/fang-upgrade/blob/main/INSTALL-AGENT.md).

**A caveat worth reading before you trust a section.** `SKILL.md`'s "Fork vs stock v0.6.9" table is
the enumerated list of everywhere the fork's behaviour differs from stock; everything **not** in
that table is unchanged from stock and covered once, under Traps — silence there means "same as
stock," not "not yet checked." That said, every line in every file was verified against the code
and/or the live daemon at the time it was written, not against each other, so if a section and the
running daemon still disagree, **the daemon is right**. Reporting the mismatch is more useful than
working around it.

Licence: same as upstream OpenFang, Apache-2.0 OR MIT.

## Package validation

Use Python 3.12.4 or newer for these package checks: older `ipaddress` data can
misclassify reserved addresses in the scrubber. This requirement applies to the
checks, not to all operator scripts.

The root skill remains canonical for existing standalone clones. After editing
`SKILL.md`, `references/`, `scripts/`, or `assets/`, run:

```bash
python3 scripts/build_plugin.py
python3 scripts/build_plugin.py --check
python3 -m pip install PyYAML
python3 scripts/validate_package.py
python3 -m unittest discover -s tests
python3 scripts/ofscrub
claude plugin validate .
```

CI rejects a generated payload that differs from canonical files. Bump both
plugin manifests for each packaged release.
