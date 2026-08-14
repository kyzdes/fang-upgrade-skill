# fang-upgrade-skill

Operating manual for [**fang-upgrade**](https://github.com/kyzdes/fang-upgrade) — a patched
fork of OpenFang v0.6.9 — written for an AI agent operating a live install.

This is the fork-aware companion to
[openfang-skill](https://github.com/kyzdes/openfang-skill), which documents stock upstream
v0.6.9. If you are running unmodified OpenFang, use that one instead.

## What is in here

| | |
|---|---|
| `SKILL.md` | the manual itself — daemon, agents, hands, cron, providers, channels, the API |
| `references/` | eleven deep-dives loaded on demand: architecture, providers and models, hands, automation, the 65 builtin tools, the security model, skills and ClawHub, channels/MCP/A2A, known issues, a worked pipeline, and a fresh-server runbook |
| `scripts/` | `ofctl` (authenticated API calls), `ofdoctor` (health pass with a secret-leak scan), `ofhand`, `ofcron`, `ofbackup`, `ofcheck-rs`, plus intake tools for YouTube and RuTube |
| `evals/` | trigger evaluations for the skill description |

## Install

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/kyzdes/fang-upgrade-skill.git ~/.claude/skills/fang-upgrade
chmod +x ~/.claude/skills/fang-upgrade/scripts/*
export PATH="$HOME/.claude/skills/fang-upgrade/scripts:$PATH"
```

Point the tools at your install:

```bash
export OPENFANG_URL=http://127.0.0.1:4200
export OPENFANG_CONFIG=/var/lib/docker/volumes/openfang_openfang-data/_data/config.toml
ofctl -x version GET /api/health
ofdoctor
```

## Testing a patch against the fork

The fork ships its own harness for exercising LLM-provider and Telegram edge cases against
a real daemon — a scripted fake OpenAI-compatible provider plus a fake Telegram Bot API,
run as containers in the staging box's netns. It lives in the fork's repo, not in this
skill: `tests/fang/harness/` (`fangrig --help`, `tests/fang/harness/README.md`).

## Reading it

Addresses and host names appear as `<tailnet-ip>`, `<public-ip>`, `<tailnet-host>` — substitute
your own. Paths assume the Docker install described in
[fang-upgrade/INSTALL-AGENT.md](https://github.com/kyzdes/fang-upgrade/blob/main/INSTALL-AGENT.md).

**A caveat worth reading before you trust a section.** `SKILL.md`'s "Fork vs stock v0.6.9" table is
the enumerated list of everywhere the fork's behaviour differs from stock; everything **not** in
that table is unchanged from stock and covered once, under Traps — silence there means "same as
stock," not "not yet checked." That said, every line in every file was verified against the code
and/or the live daemon at the time it was written, not against each other, so if a section and the
running daemon still disagree, **the daemon is right**. Reporting the mismatch is more useful than
working around it.

Licence: same as upstream OpenFang, Apache-2.0 OR MIT.
