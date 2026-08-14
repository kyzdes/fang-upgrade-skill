# OpenFang — Known Issues & Project Status (GitHub-mined, as of 2026-08-09)

Scope: `RightNow-AI/openfang`, evaluated against the **exact code we run**: tag `v0.6.9`, commit
`acf2587e` — mirrored on this repo's `main` branch, live in container `openfang-openfang-1`
(`openfang 0.6.9`) until 2026-08-09. **Not** `/opt/openfang`: that path runs our fork (`ours`) now,
not a bare `acf2587e` checkout — see below.

Every claim below is either (a) a direct quote of an issue/PR, or (b) verified by reading v0.6.9
source at the cited `file:line`. Where the issue text and the v0.6.9 code **disagree**, the code wins
and the disagreement is called out explicitly.

**Prod no longer runs this checkout.** Since 2026-08-09, `http://127.0.0.1:4200` runs our fork
(`/root/src/openfang`, branch `ours`) three sprints ahead of `acf2587e`, not the bare tag. The
triage below still describes real upstream behavior — none of the 73 issues or 45 PRs surveyed
here overlap with what the fork changed (checked by keyword against every fix in `FORK-NOTES.md`:
per-call metering/`model_used`/`fallback`, the fallback `base_url` fix, prompt-section tags,
`PUT /agents/{id}/update` honesty, `remove_custom_model`'s `model_count`, hot-reload's
applied-vs-deferred split, the Telegram token leaks, six adapters' `reqwest`-error leaks,
`file_read` paging, and `openfang_tool_calls_total` — none is a numbered issue or PR title in this
tracker). So every row here is still an accurate description of a gap you'd hit on the fork too,
**except** wherever a `file:line` citation points into `tool_runner.rs`, `agent_loop.rs`,
`routes.rs`, or `kernel.rs` — those four files carry the fork's edits, so their line numbers below
have been re-verified against `/root/src/openfang` on `ours` (not the untouched `acf2587e` tree)
and updated where the fork's insertions shifted them. Citations into any other file are unchanged
from the original `acf2587e` research; check them against this repo's `main` branch (the `acf2587e`
mirror), not against `/opt/openfang` — that path runs `ours` now, not a bare-tag checkout.

Reproduce the raw data with:

```bash
gh issue list --repo RightNow-AI/openfang --state open  --limit 300 \
  --json number,title,labels,createdAt,author \
  --jq '.[]|"\(.number)|\(.createdAt[0:10])|\(.title)"'
gh pr    list --repo RightNow-AI/openfang --state open  --limit 300 --json number,title,createdAt
gh api   "repos/RightNow-AI/openfang/commits?per_page=100&sha=main"   # quote it — a bare & backgrounds the command
gh api   repos/RightNow-AI/openfang/stats/participation --jq '.all'
```

---

## Table of contents

- [0. Verdict in one paragraph](#0-verdict-in-one-paragraph)
- [1. Project status — the hard numbers](#1-project-status--the-hard-numbers)
- [2. Nothing landed after v0.6.9 — verified three ways](#2-nothing-landed-after-v069--verified-three-ways)
- [3. #1254 — GHCR image is 401/403, and why (Docker deployment)](#3-1254--ghcr-image-is-401403-and-why-docker-deployment)
- [4. Triaged open issues (all 73), grouped by theme](#4-triaged-open-issues-all-73-grouped-by-theme)
- [5. The unmerged patch queue — 45 open PRs, 0 reviews](#5-the-unmerged-patch-queue--45-open-prs-0-reviews)
- [6. Issues that are already fixed in the v0.6.9 you are running](#6-issues-that-are-already-fixed-in-the-v069-you-are-running)
- [Gotchas (verified, not repeated from docs)](#gotchas-verified-not-repeated-from-docs)

## 0. Verdict in one paragraph

**OpenFang is, as of 2026-08-09, an unmaintained-in-practice project.** `main` is bit-identical to
the `v0.6.9` tag (`acf2587e`); the last non-dependabot commit and the last release are both
**2026-05-12**, i.e. **89 days ago**. `stats/participation` returns `0` for each of the last 12
weeks. The sole maintainer (`jaberjaber23`) has **zero** GitHub activity of any kind since
2026-05-12 and has not answered the three explicit "is this alive?" issues (**#1214** 2026-05-26,
**#1240** 2026-06-08, **#1275** 2026-07-31). There are **73 open issues** and **45 open PRs**,
including ~10 clean, mergeable, single-purpose bug fixes that have received **zero reviews**. The
project is usable and stable at v0.6.9 — nothing is on fire — but you should plan on **being your
own maintainer**: no security patches, no provider-catalog refreshes, no upstream fix for any bug
listed here. A community continuation, **`librefang/librefang`** (referenced in #1240), is under
daily active development.

---

## 1. Project status — the hard numbers

| Signal | Value | Source |
|---|---|---|
| `main` HEAD | `acf2587e` "bump v0.6.9" | `git log`; `gh api .../branches` |
| `main` vs `v0.6.9` tag | **identical SHA** — main is *not* ahead | `gh api .../tags` |
| Last commit of any kind on `main` | **2026-05-12** | commits API |
| Last release | **v0.6.9 "security patches", 2026-05-12** | `latestRelease` |
| Commits in last 12 weeks | **0** (`participation.all` tail is twelve zeros) | `stats/participation` |
| Non-dependabot branches ahead of main | **none** — all 7 extra branches are `dependabot/*` | branches API |
| Open issues / open PRs | **73 / 45** | issue+pr list |
| Merged PRs since 2026-05-12 | **0** | `gh pr list --state merged` |
| Closed issues since 2026-05-12 | **0** (the 42 with `closedAt` on 2026-05-12 were the final triage burst) | issue list |
| Maintainer's last comment anywhere | 2026-05-12 (issues #1161–#1189 batch) | `gh search issues --commenter jaberjaber23` |
| Stars / forks | 18,095 / 2,282 | repo view |
| License | **Dual: `Apache-2.0 OR MIT`** (`Cargo.toml:23`; both `LICENSE-APACHE` and `LICENSE-MIT` are in the tree; `README.md:485` says "MIT. Use it however you want.", and the README badge says MIT). `release.yml:243` labelling the image `licenses=MIT` is therefore **consistent**, not a contradiction. | `Cargo.toml:23`, repo view |
| Repo archived? | **No** — it is dormant, not formally sunset | `isArchived: false` |

### Release cadence, before it stopped

`v0.4.1` (2026-03-14) → `v0.6.9` (2026-05-12): **29 releases in 59 days**, often several per day
(`v0.6.5`–`v0.6.9` all shipped on 2026-05-12). This was an extremely high-velocity single-maintainer
project that stopped **abruptly and completely**, mid-triage, with no announcement. There is no
`archived` flag, no `SECURITY.md` sunset note, no pinned issue. The failure mode is "maintainer
disappeared", not "project wound down".

### The three status issues

- **#1214 "Still maintained?"** (Hypn0sis, 2026-05-26) — empty body. Three community replies, no
  maintainer reply. `socketa4techx7`: *"looks dropped for couple of weeks. Hope author is fine"*.
  `wking1986`: *"Maybe look at librefang?"*. `frankdaza`: *"Hey @jaberjaber23. Are you OK? Is this
  project still alive?"*.
- **#1240 "Is Openfang still an active project?"** (Bandit253, 2026-06-08) — one reply, from
  **@houko (association: contributor)**, consisting solely of the link
  `https://github.com/librefang/librefang`. No maintainer reply.
- **#1275 "Project status / roadmap after v0.6.9"** (jlacour-git, 2026-07-31) — a polite, precise
  request from someone evaluating OpenFang for long-term self-hosting, asking for a v1.0 outlook.
  **Zero comments. Zero reaction. Unanswered for 9 days and counting.**

**There is no post-v0.6.9 roadmap.** #1275 is the only place one was requested and it was never
answered. The closest thing to a forward plan in-tree is `docs/launch-roadmap.md` (pre-v0.6.9).

### Maintenance-risk verdict for a self-hosted operator

| Risk | Rating | Why |
|---|---|---|
| Will an upstream fix land for a bug you hit? | **Very low** | 0 merges in 89 days; 45 PRs, 0 reviews |
| Will a security advisory be patched upstream? | **Very low** | Last release was literally titled "security patches"; nothing since |
| Will the provider/model catalog stay current? | **No** | `model_catalog.rs` is a hardcoded table; new models require a code change (see #995, #1267, #1272, #1210 — all open provider-catalog PRs) |
| Will v0.6.9 keep working as-is? | **Yes** | It is a self-contained Rust binary; nothing phones home for licensing. Breakage will come from *provider APIs* changing under it (see #1149, #1157-class issues), not from OpenFang |
| Can you self-maintain? | **Yes, realistically** | Apache-2.0, single workspace, builds from source, and ~10 of the highest-value fixes already exist as mergeable PRs you can cherry-pick |

**Recommended posture:** fork `RightNow-AI/openfang@acf2587e`, cherry-pick the small PR set in §5,
pin your provider config, and treat upstream as a read-only source of patches. Do not build a
roadmap that depends on upstream shipping anything.

### The `librefang` fork (mentioned in #1240)

`https://github.com/librefang/librefang` — the only continuation anyone in the issue tracker points
at. Verified 2026-08-09 via `gh api repos/librefang/librefang`:

| | |
|---|---|
| Created | 2026-03-12 |
| Last push | **2026-08-09 (today)** |
| Stars / forks | 353 / 68 |
| License | **MIT** (OpenFang is dual `Apache-2.0 OR MIT`, so MIT-only is a narrowing, not a change of family) |
| Open issues / open PRs | **12 / 51** (re-verified 2026-08-10 via `search/issues`; the repo's `open_issues_count: 63` is issues+PRs, which is where a "128" would have come from) |
| `fork: false` | It is a **detached / hard fork**, not a GitHub-linked fork |
| Topics | `openclaw`, `openfang`, `zeroclaw` |
| Homepage | `https://librefang.ai` (live demo at `https://flyio.librefang.ai`) |
| Governance | has `MAINTAINERS.md`, `GOVERNANCE.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `changelog.d/` |

Crate topology is a 1:1 rename of OpenFang's — `librefang-kernel`, `librefang-runtime`,
`librefang-hands`, `librefang-skills`, `librefang-channels`, `librefang-types`, `librefang-api`,
`librefang-cli`, `librefang-memory`, plus new ones OpenFang lacks (`librefang-acp`,
`librefang-telemetry`, `librefang-rl-export`, `librefang-runtime-sandbox-docker`,
`librefang-kernel-metering`, `librefang-kernel-router`, `librefang-wire`).

Activity (last 15 commits, all 2026-08-05→08-09): daily `chore(openrouter): update model snapshot`,
`feat(config): add managed configuration mode (#6717)`, grouped dependabot bumps, CI work. PR
numbers are in the **#6700s**. Releases are CalVer and roughly weekly: `v2026.7.31`, `v2026.7.27`,
`v2026.7.21`, `v2026.7.11`, `v2026.6.29` (re-verified 2026-08-10; newest PR is **#6874**).

**Caveats before you migrate:** different licence (MIT vs Apache-2.0), different crate names,
different config surface (`managed configuration mode` is new), no documented OpenFang→LibreFang
migration path, and it is a *different project with a different maintainer* — it inherits OpenFang's
architecture, not its config compatibility guarantees. It is the pragmatic destination if you need
an actively maintained agent OS in this family; it is **not** a drop-in upgrade for a v0.6.9
install.

---

## 2. Nothing landed after v0.6.9 — verified three ways

1. **Tag == branch.** `v0.6.9` and `main` are both `acf2587e`. `git describe --tags` on
   `/opt/openfang` returns exactly `v0.6.9` with no `-N-g<sha>` suffix.
   Note when checking via the API: `v0.6.9` is an **annotated** tag, so
   `gh api repos/RightNow-AI/openfang/git/ref/tags/v0.6.9 --jq .object.sha` returns the tag object
   (`ea66f34b…`), not the commit. Dereference it —
   `gh api repos/RightNow-AI/openfang/git/tags/ea66f34b… --jq .object.sha` → `acf2587e…`, matching
   `branches/main`. Comparing the two raw values makes it look like main is ahead; it is not.
2. **No merged PRs.** `gh pr list --state merged` — the newest merge is **#1176** on
   **2026-05-12** ("fix(chat): support Shift+Enter"). All nine merges dated 2026-05-12 (#1045,
   #1054, #1135, #1143, #1146, #1147, #1168, #1175, #1176) are *in* v0.6.9.
3. **No branches ahead.** The only non-`main` branches are seven `dependabot/*` bumps
   (`axum-0.8.9`, `libc-0.2.186`, `rmcp-1.7.0`, `tauri-build-2.6.1`,
   `tauri-plugin-updater-2.10.1`, `actions/checkout-7`, `tauri-action-1`). Each has an open PR
   (#1198–#1202, #1255, #1263), none merged.

**Consequence: there is no "newer than v0.6.9" build to upgrade to.** Every fix you want must be
cherry-picked from an open PR or written yourself. The 42 issues closed on 2026-05-12 are all
*already in* the v0.6.9 you are running.

---

## 3. #1254 — GHCR image is 401/403, and why (Docker deployment)

**#1254 "Pulling GHCR 401 unauthenticated pull"** (BitFis, 2026-06-24, label `bug`) — a re-open of
**#961**, which itself references the ancient **#12**. Still broken today; verified live:

```bash
$ curl -sS -o /dev/null -w '%{http_code}\n' https://ghcr.io/v2/rightnow-ai/openfang/manifests/latest
401
# even with an anonymous pull token, the registry refuses to mint one:
$ curl -sS 'https://ghcr.io/token?scope=repository:rightnow-ai/openfang:pull&service=ghcr.io'
# → no "token" field; the subsequent manifest HEAD returns 403
```

### Root cause, read from the workflow

`.github/workflows/release.yml`, the `docker` job:

- L219–224: logs into GHCR with `password: ${{ secrets.GITHUB_TOKEN }}`.
- L232–240: builds and **pushes** `ghcr.io/rightnow-ai/openfang:latest` and `:<version>`
  multi-arch (amd64+arm64). *The push succeeds* — the image exists.
- **L247–254** is the broken step, "Set GHCR package visibility to public":

```yaml
- name: Set GHCR package visibility to public
  run: |
    curl -fsSL -X PATCH \
      -H "Authorization: Bearer ${{ secrets.GITHUB_TOKEN }}" \
      ... https://api.github.com/orgs/RightNow-AI/packages/container/openfang \
      -d '{"visibility":"public"}'
```

The default `GITHUB_TOKEN` **cannot** change org package visibility — that requires a PAT with
`admin:packages` (or a manual toggle in the org's package settings). So every release pushes a
**private** image and then fails (or silently no-ops) on the visibility flip. This is a
credentials/permissions problem in CI, not a build problem; it will never self-heal now that CI
isn't running.

The repo already knows: **`docker-compose.yml` line 1** says
`# NOTE: The GHCR image (ghcr.io/rightnow-ai/openfang) is not yet public.` and line 9 keeps
`image:` commented out with `# Uncomment when GHCR is public`.

### Working deployment paths

1. **Build from source (what the shipped compose file does):**
   `docker compose up --build` — `docker-compose.yml` sets `build: .`, exposes `4200:4200`, mounts
   named volume `openfang-data:/data`, `restart: unless-stopped`, and passes through
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`,
   `DISCORD_BOT_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`. (This is how our `/opt/openfang`
   instance was built.)
2. **Prebuilt native binaries** — the v0.6.9 *GitHub Release* assets are public and complete, which
   is the reliable distribution channel:
   `openfang-x86_64-unknown-linux-gnu.tar.gz`, `openfang-aarch64-unknown-linux-gnu.tar.gz`,
   `openfang-armv7-unknown-linux-gnueabihf.tar.gz`, `openfang-{x86_64,aarch64}-apple-darwin.tar.gz`,
   `openfang-{x86_64,aarch64}-pc-windows-msvc.zip`, `.deb`, `.rpm`, `.AppImage`, `.dmg`, `.msi` —
   each with a `.sha256`.
3. **Don't** wait for GHCR. It requires a maintainer action that nobody is performing.

### Other Docker notes from the tracker

- `docker-compose.yml` still declares `version: "3.8"` — obsolete and warned about by modern
  Compose. Harmless; delete the line.
- **Host services from inside the container**: compose ships `extra_hosts:
  - "host.docker.internal:host-gateway"` **commented out**. Uncomment it (or run with
  `--add-host=host-gateway`) to reach host Ollama / whisper.cpp / Postgres. Documented at
  `docs/troubleshooting.md#connecting-to-host-services-from-docker`; this came out of closed
  issue **#1173**, which also asked for a curl-equipped reference image — **our container still has
  no `curl`, `git`, `ffmpeg` or `ffprobe`**, so drive the API from the host. (`yt-dlp` *is* present,
  but only because it was `pip3 install`ed into the writable layer — it does not survive an image
  rebuild; see `youtube-pipeline.md` §2.2.)
- Closed **#1169** ("shell_exec only receives HOME/PATH/PWD in Docker") is fixed *in* v0.6.9 — env
  passthrough works in the version we run.
- Open PR **#1273** (kobihikri, 2026-07-28) would attach SBOM + provenance attestations to the
  published image. Unreviewed. Moot while the image is private.

---

## 4. Triaged open issues (all 73), grouped by theme

**Severity is calibrated for a self-hosted, single-user/single-tenant operator** — not for the
project at large. Ratings:

- **HIGH** — will cost you money, corrupt state, leak data, or block a core workflow on a default install.
- **MED** — real functional gap you will hit if you use that subsystem.
- **LOW** — annoyance, cosmetic, or narrow.
- **N/A-MT** — multi-tenant / SaaS concern only; irrelevant to a single operator (but read §4.9 if you expose the API).
- **STALE** — already fixed in the v0.6.9 code we run, or unreproducible against it; left open only because triage stopped.

### 4.1 Cost & runaway-agent traps (read this section first)

| # | Title | Sev | Impact | Workaround |
|---|---|---|---|---|
| **#1206** | Sample agents ship aggressive `[schedule]`s that activate under v0.6.9 auto-spawn | **HIGH** | Verified in tree: `agents/orchestrator/agent.toml:53` `continuous = { check_interval_secs = 120 }`, `agents/ops/agent.toml:32` `periodic = { cron = "every 5m" }`, `agents/health-tracker/agent.toml:59` `periodic = { cron = "every 1h" }`. Commit `efbefa1` ("chat agents", closes #1140) made *every* agent in `~/.openfang/agents/` auto-spawn at boot, so these formerly-dead schedules go live. Reporter measured **~43 LLM calls/hr** with nobody using the system — which is **1032/day**, not the ~630/day quoted in the issue (720 from `orchestrator` at 120 s + 288 from `ops` at 5 m + 24 from `health-tracker` at 1 h); `orchestrator` hits its own `max_llm_tokens_per_hour = 500000` cap in ~2h. | **Do this before first boot.** Delete or comment out the `[schedule]` blocks in `orchestrator`, `ops`, `health-tracker` under `$OPENFANG_HOME/agents/*/agent.toml`, or delete the agent dirs entirely, then restart. `security-auditor` is safe — its schedule is `proactive` on `event:agent_spawned`/`event:agent_terminated`, not a timer. |
| **#1252** | `[heartbeat] default_timeout_secs` "ignored" — agents always time out at 60s | **HIGH** | Same blast radius: agents get flagged `Crashed` → auto-recovery fires → each recovery is an LLM subprocess call → reporter saw **~570 provider calls/day** from pure idle churn. **But the reporter's diagnosis is wrong — see the code note below.** | See below; the fix is per-agent, not global. |

#### #1252 — what the v0.6.9 code actually does (issue text is misleading)

The config key **is** wired: `crates/openfang-kernel/src/kernel.rs:4961-4963` builds
`HeartbeatConfig { default_timeout_secs: self.config.heartbeat.default_timeout_secs, ..default() }`,
and `crates/openfang-types/src/config.rs:1303-1322` defines `[heartbeat] default_timeout_secs` with
a **default of 180**, not 60.

The real mechanism is `crates/openfang-kernel/src/heartbeat.rs:161-166`:

```rust
let timeout_secs = entry_ref
    .manifest
    .autonomous
    .as_ref()
    .map(|a| a.heartbeat_interval_secs * UNRESPONSIVE_MULTIPLIER)
    .unwrap_or(config.default_timeout_secs) as i64;
```

with `UNRESPONSIVE_MULTIPLIER = 2` (`heartbeat.rs:23`) and
`AutonomousConfig::default().heartbeat_interval_secs = 30`
(`crates/openfang-types/src/agent.rs:81, 91`). **30 × 2 = 60.**

So: **any agent whose `agent.toml` has an `[autonomous]` section gets `30 × 2 = 60s` and the global
`[heartbeat] default_timeout_secs` is never consulted for it.** That is exactly the `timeout_secs=60`
the reporter sees, and why raising the global value changed nothing. The global key only governs
agents *without* an `[autonomous]` block.

**Workaround (verified from source, not in the issue):** set the per-agent value.

```toml
# $OPENFANG_HOME/agents/<name>/agent.toml
[autonomous]
heartbeat_interval_secs = 43200   # → unresponsive threshold = 86400s
```

…or remove the `[autonomous]` block entirely so the agent falls back to
`[heartbeat] default_timeout_secs` (whose real default is **180**, `heartbeat.rs:64` — not 60 and not
300). Mitigating factors already in v0.6.9, both of which gate whether the crash/recover oscillation
can start at all: `IDLE_GRACE_SECS = 10` (`heartbeat.rs:130`, applied at `:161-192`) skips any
`Running` agent whose `last_active` is within 10 s of `created_at`, i.e. one that never processed a
message (fix for #844); and `should_exempt_idle_reactive_agent` (`heartbeat.rs:139-141`) exempts idle
`Reactive` agents outright. `DEFAULT_MAX_RECOVERY_ATTEMPTS = 3` /
`DEFAULT_RECOVERY_COOLDOWN_SECS = 60` bound the recovery loop.

### 4.2 Workflows & scheduling

| # | Title | Sev | Impact | Workaround |
|---|---|---|---|---|
| **#1192** | Deleted workflow reappears after daemon restart | **HIGH** | Confirmed: `crates/openfang-kernel/src/workflow.rs:235-238` — `remove_workflow()` is `self.workflows.write().await.remove(&id).is_some()` and **never touches disk**. `load_workflows_from_dir()` (`kernel.rs:4468-4484`) re-imports every `.json` at boot. A workflow you "deleted" (possibly a scheduled, cost-incurring one) comes back. | After deleting in the UI, `rm $OPENFANG_HOME/workflows/<id>.json` **before** restarting. Fix exists as unmerged PR **#1193**. |
| **#1253** | `collect` joins pre-fan-out outputs, not just the preceding fan-out group | **MED** | Confirmed: `workflow.rs:635-638` — `StepMode::Collect => { current_input = all_outputs.join("\n\n---\n\n"); all_outputs.clear(); all_outputs.push(current_input) }`. `all_outputs` is the *global* buffer (declared L469, pushed at L515/L595/L689/L780), so earlier `Sequential` outputs are folded into the merge. Contradicts `docs/workflows.md`. Downstream synthesis steps silently receive stale context; multi-phase workflows keep carrying it forward. | Insert a no-op transform step, or put fan-out groups in separate workflows and chain them. Fix exists as unmerged PR **#1277**. |
| **#739** | Scheduler entries not editable after creation in the web UI | LOW | Delete + recreate only. | Edit the on-disk definition and restart, or use the CLI/API. |
| **#1194** | Proposal: PR-Reviewer Hand (autonomous review + CI monitoring) | LOW | Feature proposal, no code. | — |

### 4.3 Providers, models & embeddings

| # | Title | Sev | Impact | Workaround |
|---|---|---|---|---|
| **#1212** | Embedding driver hard-codes 6 cloud providers; `base_url` override ignored | **HIGH** if you use memory-recall | **This is the real one.** The *chat* path honours `OLLAMA_HOST`/`OLLAMA_BASE_URL`, `LMSTUDIO_*`, `VLLM_*`, `LEMONADE_*` and `[provider_urls]` (`model_catalog.rs:340-360`, `drivers/mod.rs:52-82`). The *embedding* path does not honour `[provider_urls]`/`base_url`. Precisely (this is narrower than the issue's phrasing): `[memory] embedding_provider` wins if set (`kernel.rs:1012`); otherwise `kernel.rs:1040-1047` tries six cloud key env vars **in priority order** — `OPENAI_API_KEY → GROQ_API_KEY → MISTRAL_API_KEY → TOGETHER_API_KEY → FIREWORKS_API_KEY → COHERE_API_KEY` — and only if **none** is set does it fall through to local `ollama → vllm → lmstudio` (`kernel.rs:1074-1076`). **Net effect: on a box that has `OPENAI_API_KEY` set for any other reason, chat stays local while embedding-recall silently ships your text to api.openai.com and bills you.** The hole is the priority order, not an absolute requirement. | Disable embedding-recall, or front a local embedding server with a reverse proxy at `https://api.openai.com`-shaped routing, or patch `embedding.rs`. Partial fixes in unmerged PRs **#1248** and **#997** (native Gemini embeddings). |
| **#1251** | Embedding `base_url` force-appends `/v1`; dimensions from a hardcoded name table | MED | Confirmed at `embedding.rs:197-210`: `needs_v1 = matches!(provider, "openai"\|"groq"\|"together"\|"fireworks"\|"mistral"\|"ollama"\|"vllm"\|"lmstudio")` then `if needs_v1 && !trimmed.ends_with("/v1") { format!("{trimmed}/v1") }` — so `http://host:8004/v3` becomes `/v3/v1`. Separately `infer_dimensions()` (`embedding.rs:106`) is a `match` on model *name* with `_ => 1536`, so an off-table model (Qwen3-Embedding, etc.) silently gets the wrong dimension. | Only reachable via an external rewriting proxy. Issue includes a 6-line patch. |
| **#1195** | OpenAI-compatible custom `base_url` strips `openai/` from Featherless model IDs | MED — **REAL and reproducible in v0.6.9** | **Confirmed.** An earlier pass of this file wrongly said "unconfirmed" after grepping for a hardcoded literal `"openai/"`. There is none, because `strip_provider_prefix` (`crates/openfang-runtime/src/agent_loop.rs:211-225`) **builds** the prefix: `let slash_prefix = format!("{}/", provider)`. The reporter's config is `provider="openai"`, `model="openai/gpt-oss-120b"`, `base_url=https://api.featherless.ai/v1`, and the error is `The model gpt-oss-120b does not exist` — exactly `strip_provider_prefix("openai/gpt-oss-120b","openai")`. It fires at `agent_loop.rs:593` and `:1873` on **every** request, and the stripped id is *persisted* at `kernel.rs:1699` (registration) / `:3435` (model switch). The only literal model-prefix strips are `drivers/vertex.rs:182` (`models/`), `drivers/qwen_code.rs:140` (`qwen-code/`), `drivers/claude_code.rs:199` (`claude-code/`) — that part of the earlier note was right. Our instance is safe only because its provider is `hyperfusion`, so `"openai/gpt-oss-120b"` does not start with `"hyperfusion/"`. | Never name the provider so that it equals the first path segment of your model ids. Use a custom provider name (as we do) rather than `provider = "openai"`. Unmerged PR **#1248** "preserve custom OpenAI-compatible model IDs" targets this shape. |
| **#1154** | "LM STUDIO / OLLAMA is not setup to allow for any use case" (localhost only) | **STALE** | **Fixed in v0.6.9.** `model_catalog.rs:345-352` explicitly cites *"See issue #1154"* and honours `OLLAMA_HOST`/`OLLAMA_BASE_URL`, `LMSTUDIO_HOST`/`LMSTUDIO_BASE_URL`, `VLLM_HOST`/`VLLM_BASE_URL`, `LEMONADE_HOST`/`LEMONADE_BASE_URL`; `drivers/mod.rs:52-82` normalises bare hosts (adds `http://`, appends `/v1`). Issue stayed open because triage stopped. The *embedding* half is genuinely still broken — that's #1212. | Use the env vars, or `[provider_urls]` in `config.toml`. |
| #1149 | Migrate OpenAI support to Responses API | MED (future) | Chat Completions is legacy; OpenAI recommends Responses for agentic/tool loops, with 40–80% better cache utilisation. Nobody will do this migration now. | None. Expect gradual drift as OpenAI deprecates. |
| #1033 | Support OpenAI Codex App Server as a model backend | LOW | Use a ChatGPT sub instead of an API key, via the official app-server protocol. | Unmerged PR **#1216** implements a `codex_app_server` driver (1 review, unmerged). |
| #1021 | Subscription auth (OAuth) for Codex / Gemini | LOW | API keys only. | — |
| #995 | Add Requesty as a provider | LOW | Catalog entry missing. | Configure as a generic OpenAI-compatible provider with `base_url`. |
| #981 | MiniMax Coding Plan → 401 invalid api key | LOW | MiniMax coding-plan auth shape not handled. | — |
| #679 | OAuth token migration/lifecycle in `openfang migrate` | LOW | Migrating from OpenClaw drops OAuth profiles with an unhelpful "skipped for security"; the vault is key-value only, no refresh. | Re-auth manually; use API-key providers. |
| #1190 | RFC: MongoDB support in `openfang-memory` | LOW | Design discussion (successor to closed #733/#998). No RFC answer ever came. | SQLite stays the only backend. |

Also relevant: **six** open provider/catalog PRs — **#1272** (Atlas Cloud), **#1267** (MiniMax M3),
**#1248** (preserve custom OpenAI-compatible model IDs), **#1216** (`codex_app_server` driver),
**#1210** (NEAR AI Cloud), **#1093** (Volcano Engine). All unmerged. **Any new model you want in the
picker requires a source patch**, because `crates/openfang-runtime/src/model_catalog.rs` is a
hardcoded table — **4,866 lines** exactly. (The runtime escape hatch that does *not* need a rebuild
is `POST /api/models/custom` → `$OPENFANG_HOME/custom_models.json`; see `providers-and-models.md` §6.3.)

### 4.4 Tools

| # | Title | Sev | Impact | Workaround |
|---|---|---|---|---|
| **#1271** | `web_fetch` injects raw PDF binary into agent context | **HIGH** | Confirmed at `crates/openfang-runtime/src/web_fetch.rs`: L106 size guard, L115-117 reads `content-type`, **L131 uses it only for `is_html()`** — everything else falls through to `resp.text()`. A PDF becomes ~617K chars of FlateDecode garbage in context; blows the window, and with a local model the poisoned history is re-sent every turn, corrupting the whole thread. **Second bug at the same site:** the size guard only fires when the server sends `Content-Length`; chunked responses buffer unbounded into memory before `max_chars` truncation (`web_fetch.rs:144-147`) — a memory-exhaustion vector. The legacy fallback `tool_web_fetch_legacy` (`tool_runner.rs:1593-1626`) has the identical shape (10MB `content_length()` guard, `resp.text()`, 50 000-char truncate). | Never point `web_fetch` at a PDF. The bundled `pdf-reader` skill **cannot** help — it is prompt-only, and the binary is already in context before any skill logic runs. Reporter has a working `pdf-extract` patch against `acf2587` and offered a PR; nobody answered. |
| **#1270** | ClawHub installs fail on ambiguous slugs (`ownerHandle` not forwarded) | MED | Confirmed: `crates/openfang-skills/src/clawhub.rs:529` calls `/api/v1/download?slug=<slug>` with no owner. Upstream returns `409 Conflict` → OpenFang surfaces `502` → the dashboard shows a **misleading "daemon unavailable"**. Repro: Skills → ClawHub → install `weather`. | Install the skill manually into `$OPENFANG_HOME/skills/<name>/`. Fix exists as unmerged PR **#1274**. |
| **#1038** | Global skills in `~/.openfang/skills/` unusable by agents | MED | Global-level skills are *listed* but not *reachable*: `file_read` resolves relative to the workspace, so `~/.openfang/skills/x/SKILL.md` → `…/workspaces/<agent>/skills/x/SKILL.md` (missing). Precisely: `resolve_sandbox_path` (`workspace_sandbox.rs:15-60`) rejects `..` outright but **accepts absolute paths whose canonical form is inside the workspace root** — only paths resolving outside are refused, and `$OPENFANG_HOME/skills` always is. The agent then flails at `shell_exec`, which blocks pipes and redirects (`tool_runner.rs:38-41`, `subprocess_sandbox::contains_shell_metacharacters` — metacharacters are rejected **even in Full exec mode**, `tool_runner.rs:247-255`). | Install skills at the **workspace** level (`$OPENFANG_HOME/workspaces/<agent>/skills/`) rather than globally. |
| #1001 | ClawHub skills default to `prompt_only`; bundled scripts can't run | MED | Many ClawHub skills ship `curl`/`python` scripts that never execute. Compounded here because **our container has no `curl` and no `git`**. | Convert the skill to an executable type by hand; use `python3` (present) instead of `curl`. |
| #1028 | Skill callback-to-URL not honoured | LOW | Vague report, no repro. | — |
| #883 | Custom Skills Hub URL (ClawHub slow / mirrors) | LOW | Hub URL is not configurable. | Install skills from disk. |
| #1204 | `shell_exec` capped at 120s total / 30s per call | MED | Blocks long-running commands from agents. Note the *model* asserted this cap in its own words in the issue; treat the exact numbers as reported-not-verified. | Background the work (`nohup`/systemd) and poll; or run it outside the agent. Unmerged PR **#1209** adds configurable timeouts + busy-agent queueing. |
| #1256 | How to upload a file >64KB? | LOW | No documented file-upload API; a 64KB conversation limit is reported. Unanswered. | Write the file to the agent workspace out-of-band and have the agent `file_read` it. |
| #1070 | Download generated reports as a file from any channel | MED | Reports (5–50KB+) can only be retrieved by SSH-ing to the box. | Read from `$OPENFANG_HOME/workspaces/<agent>/`. Unmerged PR **#1217** adds a workspace listing/download endpoint + `/download` channel command. |
| #1097 | Workspaces outside `~/.openfang` get polluted with agent dirs | **STALE** | **Fixed in v0.6.9.** `crates/openfang-kernel/src/kernel.rs:292-296` — *"Lives under `~/.openfang/workspaces/{name}/` regardless of where the user pointed the user-facing workspace. **See issue #1097.**"* Private state (`sessions/`, `logs/`, `memory/`, `AGENT.json`) is separated from the user-facing workspace; `kernel.rs:2163-2169` backfills existing agents. Left open only because triage stopped. | — |

### 4.5 Security (all reported, none fixed; no maintainer response to any)

| # | Title | Sev (self-hosted) | Impact | Workaround |
|---|---|---|---|---|
| **#1234** | WhatsApp gateway HTTP API: **no auth + `Access-Control-Allow-Origin: *`** | **HIGH if WhatsApp enabled** | `/login/start`, `/login/status`, `/message/send`, `/health` require zero authentication and return wildcard CORS. Binding to 127.0.0.1 does **not** save you — any web page you visit can `fetch('http://127.0.0.1:3009/message/send', …)` and send arbitrary WhatsApp messages as your linked account. Classic localhost-CSRF / DNS-rebinding with full impersonation. | **Do not enable the WhatsApp channel.** If you must: firewall port 3009 from the browser (it's loopback, so you can't), or patch in a bearer token. Reporter (`BunnyMoth`) has a hardened fork with a bearer-token implementation. |
| **#1232** | WhatsApp gateway → Rust API has no auth (loopback trust gap) | **HIGH if enabled** | `packages/whatsapp-gateway/index.js:194-205` POSTs `/api/agents/{id}/message` with no `Authorization`. Any stranger who texts your linked number triggers agent tool execution. | Same: don't enable WhatsApp. |
| **#1233** | Untrusted WhatsApp content forwarded unfiltered to the LLM | **HIGH if enabled** | `forwardToOpenFang` (`index.js:130-176`) — no length cap, no per-sender rate limit, no content marking. Strangers can burn your LLM spend, prompt-inject your agent into tool actions, or flood it. | Same. |
| **#1242** | WASM `max_memory_bytes` configured but **never enforced** | MED | Confirmed: `crates/openfang-runtime/src/sandbox.rs:38-39` — *"Maximum WASM linear memory in bytes (**reserved for future enforcement**)"*, `SandboxConfig::default()` is `16 * 1024 * 1024` (`sandbox.rs:54`), but the kernel overwrites it from `ResourceQuota.max_memory_bytes` (default 256 MB, `openfang-types/src/agent.rs:272`) at `kernel.rs:2538` — two different structs, and neither figure is enforced, which is the point of this issue. **No `Store::limiter()` is set anywhere**, so a WASM skill can grow to wasmtime's ~4GB default regardless of the manifest. Fuel metering + epoch interruption cover CPU, not memory. | Only run WASM skills you wrote or audited. Constrain the whole daemon with a systemd `MemoryMax=` / container memory limit. |
| **#1241** | WASM watchdog threads accumulate under load | LOW | `sandbox.rs:188-191` spawns one detached OS thread per execution (the issue text cites `sandbox.rs:203-206`, which is v0.6.4 numbering — see `security-model.md` §6.2); the `_watchdog` `JoinHandle` is dropped, so it sleeps the full `timeout_secs` even after the WASM finished. 100 concurrent invocations × 30s timeout = 100 threads × ~2MB stack. Functionally correct, resource-wasteful. | Ignore for single-user load. Fix exists as unmerged PR **#1278**. |
| #1235 | `rand` unsoundness RUSTSEC-2026-0097 across rand 0.7.3 / 0.8.5 / 0.9.2 | LOW | Only triggers with a custom logger; transitive, cannot be fixed in-tree. `gimli 0.33.1` also flagged yanked. | Accept. Nobody will bump these now. |
| #1170 | `--require-signed` for `openfang skill install` | MED | `openfang-types::manifest_signing` (Ed25519 via `ed25519-dalek` v2) is library-correct and unit-tested. **One caller does wire it**: `POST /api/skills/install` accepts `require_signed` and `allowed_signer_keys` (`crates/openfang-api/src/types.rs:70-72`) and calls `install_with_options` (`routes.rs:3873-3879`). What is genuinely missing: the CLI `openfang skill install <source>` has no such flag (`main.rs:347-351`), there is no `openfang skill sign`, and the ClawHub route is unwired — `ClawHubInstallRequest` carries only `slug` (`types.rs:115-118`) and `clawhub_install` calls `client.install()` with defaults (`routes.rs:4359`). | Install via `POST /api/skills/install` with `require_signed: true` and pinned `allowed_signer_keys`, never via the CLI or the ClawHub route. |
| #1171 | Propagate `TaintLabel` from ingestion sites to tool sinks | MED | `openfang-types::taint` defines `UserInput`/`ExternalNetwork`/`Pii`/`Secret`/`UntrustedAgent` and passes its unit tests, but `tool_runner.rs` only *adds* labels at fixed sites (L49 `ExternalNetwork`, L76 `Secret`) and never propagates inbound labels. Type system correct, runtime unwired. | Assume no taint tracking exists in practice. |
| #1172 | Auto-log HAND.toml SHA-256 to the Merkle audit chain on **reload** | **STALE** | **Already shipped in v0.6.9** — an earlier pass of this file wrongly said reload appends nothing. `HandRegistry` has `audit_callback` + `emit_hand_loaded_audit` (`openfang-hands/src/registry.rs:61-101`), fired from **five** sites: `:164` (bundled), `:226` (workspace), `:258` (`install_from_path`), `:306` (`install_from_content`), `:325` (upsert/reload); the kernel wires the callback at `kernel.rs:1255-1288`. Verified live: the chain holds **153** `HAND.toml load hand=… sha256=…` and **4** `HAND.toml reload hand=… sha256=…` entries. | Nothing to do. |
| #1174 | `POST /api/audit/append` missing | **STALE** | **Already shipped in v0.6.9** — an earlier pass of this file wrongly said it was missing. The route is registered at `server.rs:398-401` with the comment *"issue #1174 — instance-side wrapper integration"*, handler `routes::audit_append` (`routes.rs:3944`). Reads: `GET /api/audit/recent` (`routes.rs:5412`) and `GET /api/audit/verify` (`routes.rs:5448`); `AuditLog::record` is `runtime/src/audit.rs:180`. A POST returns `{"status":"appended","seq":…,"hash":…,"tip":…}` (captured in `security-model.md` §10.3). The issue is open only because triage stopped. | Nothing to do — use the route. |
| #1181 | Per-agent `file_policy` (deny/prompt/read/write tiers) | MED | Today there are only two coarse gates — workspace-root lock and `validate_path` `..`-rejection. **An agent can read anything the daemon UID can read** — `/etc/hosts`, `~/.ssh/config`. No path-aware approval. Detailed design in the issue; `needs-design`, never answered. | Run the daemon as a dedicated low-privilege UID with a minimal home. |
| #1180 | Capability gate + MCP bridge for Claude Code subprocesses + approval push | MED | Two real gaps: CC subprocesses can't reach OpenFang's tool surface; and `shell_exec` runs the approval gate *before* the metachar/`exec_policy` checks, so operators approve commands that are then refused anyway. Also: the approval gate has **no push surface** — nothing tells you an approval is pending. | Watch the Approvals panel manually. |
| #1078 / #754 | Pre-execution / pre-action authorization for Hands (SOF, OAP) | LOW | Third-party-standard proposals; `guardrails` in `HAND.toml` currently only supports human approval gates, not machine-enforceable policy. | — |
| #1113 | Meta-issue: 4 production-hardening subsystem proposals (Sandbox v2, Memory & Context, Planning & Tasks, Model Council) | LOW | Well-written offer of substantial work, explicitly asking "which aligns with your roadmap?" — **never answered**. Emblematic of the maintenance state. | — |

### 4.6 Channels

| # | Title | Sev | Impact | Workaround |
|---|---|---|---|---|
| **#1177** | Matrix adapter has **no E2EE** — encrypted rooms unreachable | **HIGH if you use Matrix** | Verified by the reporter against v0.6.4 and still true: `grep -c -iE 'encrypted\|olm\|megolm' crates/openfang-channels/src/matrix.rs` → **0**. Element creates DMs and most new rooms encrypted by default, so the bot silently drops inbound events and sends undecryptable replies. | Use unencrypted rooms only, or bridge through something that decrypts. Real fix = re-host on `matrix-rust-sdk` with `e2e-encryption` + `sqlite`. |
| **#1184** | MCP bridge is Unix-only — Windows stubbed | **not applicable to v0.6.9** | **The crate the issue describes does not exist in this tree.** `crates/` holds 13 crates and none is `openfang-mcp-bridge`; there is no `bridge_ipc` module and no `OPENFANG_BRIDGE_ENABLED`. MCP lives in `crates/openfang-runtime/src/mcp.rs` + `mcp_server.rs`, which contain **zero `#[cfg(unix)]` gates**; stdio transport is a plain `tokio::process::Command` (`mcp.rs:224`), which is cross-platform. The issue is still open + `needs-design`, but it is describing code that is not in v0.6.9. | Nothing to do. (Irrelevant for our Docker deployment either way.) |
| #1239 | Channel-level streaming (esp. voice TTS) | MED | `run_agent_loop_streaming` exists but only feeds the SSE/WebSocket dashboard; channels get one finished `String` via `ChannelBridge`. Voice therefore waits for the *entire* reply before the first audio byte — 1–3s of avoidable silence on longer replies. | None. |
| #974 | Voice: can't cancel an in-flight agent loop on barge-in | MED | Speaking during the "thinking" phase can't cancel inference; the unheard response still enters history, so the model believes it already answered. | Current mitigation is in-tree: the session injects `[Agent had responded: "…" but you spoke again before hearing it]`. |
| #866 | Page refresh during streaming loses all in-progress state | MED | No inflight-turn buffer, no per-agent broadcast/completion channel, and the session endpoint doesn't expose "is this agent running". Refresh mid-stream → blank chat until the turn commits. Worse on mobile. | Don't refresh mid-stream. (Closed #1179 fixed *reconnection*; catch-up is still missing.) |
| #891 | `agent_send_async` results delivered only to voice | MED | Callback goes through a hardcoded `ASYNC_RESULT_TX` voice channel; from Chat/Email/etc. you get `WARN: Async result received but no active voice session` and the result is **silently dropped** after the agent said "I've delegated the task". | Avoid `agent_send_async` outside voice. |
| #975 | Claude Code driver: one-shot subprocess per message instead of persistent sessions | MED | `ClaudeCodeDriver::complete()` (`runtime/src/drivers/claude_code.rs`) runs `claude -p "<prompt>" --output-format json` **per message** — full CLI startup (~2–3s) every turn, stateless, no session persistence. Painful on interactive channels. | Use an API-key provider for latency-sensitive channels; reserve `claude-code` for batch/scheduled work. Related in-tree: `claude_code.rs:199` strips the `claude-code/` model prefix; closed #1130 added a subprocess timeout config. |
| #1096 | MCP server-initiated notifications ignored | MED | `runtime/src/mcp.rs:94-97,275,320` initialises every connection with a bare `rmcp::model::ClientInfo`, which takes rmcp's **no-op blanket `ClientHandler`** (`rmcp-1.3.0/src/handler/client.rs:263`). Notifications arrive and are typed, then land in no-op callbacks. `resources/subscribe` is never issued, and `ClientCapabilities` is `::default()`. Push-based MCP servers (inbox, chat-bridge, filesystem watch, DB `LISTEN/NOTIFY`) cannot drive reactive behaviour. | Poll with `schedule_create`. Unmerged PR **#1203** implements push. |
| #780 | Telegram: thread_id-based static routing | LOW | All Telegram messages go to one default agent. | One bot per agent (doesn't scale). |
| #586 | Multi-bot → multi-agent 1:1 routing | LOW | Same shape as #780, across platforms. | — |
| #978 | Add Tencent QQ channel | LOW | Not supported. | — |
| #1197 | Feishu/Lark: card output + file send/receive (zh) | LOW | Feature gap. | — |

### 4.7 Install / deploy

| # | Title | Sev | Impact | Workaround |
|---|---|---|---|---|
| **#1254** | GHCR 401/403 — no public Docker image | **HIGH for first-time deploy** | See §3. | Build from source (`docker compose up --build`) or use the public release binaries. |
| #889 | Add OpenFang to Homebrew | LOW | No `brew install openfang`. | Unmerged PR **#1215** adds a tap + CI formula generation. |
| #1085 | "No active connection", chat spins forever (Gemini, v0.4.4, macOS) | **STALE-ish** | Reported against **v0.4.4** — five minor versions behind what we run; much of the connection/WS handling was reworked through v0.6.x (#1179, #1189 WS auth, #569 ws reconnect). Not reproducible as written on v0.6.9. | If seen: check `api_key` and provider auth state; watch the daemon log. |

### 4.8 UX / dashboard

| # | Title | Sev | Impact |
|---|---|---|---|
| #1139 | Move approvals into the chat window; remove/extend the timeout | MED — approvals live in a separate panel you must watch, and the timeout often expires first. Pairs with #1180's "no push surface". |
| #658 | Trigger the permission dialog inline when a privileged tool is called | LOW — same theme. |
| #691 | Popup notification for pending approval requests (zh) | LOW — same theme. |
| #1222 | Improve the chat reply area (step-flow rendering like VS Code / codex / opencode) | LOW — cosmetic, with a mockup. |
| #1012 | Resource/utilisation origin UI — which agent is burning GPU/RAM right now | LOW — closed #1026 added "which agent is inferencing"; per-agent resource attribution is still missing. |
| #1186 | Multilingual UI support | LOW — `enhancement, needs-design`. |
| #1230 | "Can 100 requests to one cloned agent run in parallel?" | Answer: **no** — see #4.9. |

### 4.9 Multi-tenant / concurrency (N/A for single-user, but read if you expose the API)

The whole cluster — **#795, #712, #993, #1211, #1049, #1230** — reduces to two verified facts in
v0.6.9:

1. **One in-flight turn per agent, globally.** `crates/openfang-kernel/src/kernel.rs:1928-1937`:

   ```rust
   // Acquire per-agent lock to serialize concurrent messages for the same agent.
   let lock = self.agent_msg_locks.entry(agent_id)
       .or_insert_with(|| Arc::new(tokio::sync::Mutex::new(()))).clone();
   let _guard = lock.lock().await;
   ```

   (map declared `kernel.rs:181`, initialised `kernel.rs:1251`.) Different agents run in parallel;
   different *sessions of the same agent* do **not**. #795 asks for per-`(agent, session)` locking;
   #1230 asks the same question in plainer words. **Answer: no, 100 concurrent requests to one
   cloned agent serialise.**

2. **No per-caller session isolation on the OpenAI-compatible endpoint.** `ChatCompletionRequest`
   (`crates/openfang-api/src/openai_compat.rs:26-33`) has **five** fields — `model`, `messages`,
   `stream`, `max_tokens`, `temperature` — and critically **no `user`, no `session_id`**; the string
   `session` does not occur anywhere in `openai_compat.rs`. Sessions are keyed on `agent_id`. So every caller of
   `POST /v1/chat/completions` for the same agent **shares one conversation history** (#1049 —
   privacy leak + context pollution). Nobody ever answered #1049.

3. **Credentials are daemon-global.** `POST /api/providers/{name}/key` writes
   `$OPENFANG_HOME/secrets.env`, sets the process env var, and refreshes auth detection; cloned
   agents resolve credentials through `api_key_env` against that shared state, so concurrent
   tenants race and **last write wins** (#1211, #993, #712). There is no request-scoped
   `api_key`/`provider`/`model` override.

   **Only supported multi-tenant architecture today: one OpenFang daemon (and one
   `OPENFANG_HOME`) per tenant/credential group.** Unmerged PR **#943** adds per-session agent
   endpoints; #868's `cloneAgent` API shipped in v0.6.9 but does **not** solve credential
   isolation.

---

## 5. The unmerged patch queue — 45 open PRs, 0 reviews

This is where the value is. If you self-maintain a fork of `acf2587e`, these are the
cherry-pick candidates. All confirmed `mergeable: MERGEABLE`, `reviews: 0` unless noted.

**Direct fixes for open bugs in §4:**

| PR | Date | Fixes | Title |
|---|---|---|---|
| **#1277** | 2026-08-04 | **#1253** | `fix(kernel): scope collect step to preceding fan-out outputs` |
| **#1278** | 2026-08-04 | **#1241** | `fix(runtime): cancel WASM watchdog on early exit` |
| **#1274** | 2026-07-30 | **#1270** | `fix(clawhub): forward owner handle on installs` |
| **#1193** | 2026-05-12 | **#1192** | `Fix: delete workflow file when removing a workflow` |
| **#1248** | 2026-06-08 | #1195/#1212 | `fix(runtime): preserve custom OpenAI-compatible model IDs` |
| **#1276** | 2026-08-02 | — | `fix(cli): cron create by agent name, correct create/list response parsing` |
| **#1228** | 2026-06-04 | — | `fix(runtime): gate phantom-action guard on text-reply-is-delivery` |
| **#1209** | 2026-05-20 | #1204 | Configurable timeouts for long-running local inference + busy-agent queueing |
| **#1203** | 2026-05-15 | #1096 | `feat(mcp): support push notifications` |
| **#1217** | 2026-05-27 | #1070 | Workspace file listing + download endpoint, `/download` channel command |
| **#1216** | 2026-05-27 | #1033 | `codex_app_server` LLM driver (**1 review**, still unmerged) |
| **#1215** | 2026-05-27 | #889 | Homebrew tap + auto-generated formula |
| **#997** | 2026-04-06 | #1212-adjacent | Native Gemini embedding driver |
| **#1273** | 2026-07-28 | #1254-adjacent | CI: provenance + SBOM attestations on the published image |

**Larger unmerged feature stacks** (evaluate before adopting — these are big):
`pbranchu`'s memory series **#1224 → #1225 → #1226 → #1227** (persistent default user, structured
memory storage + opt-in gate, producer/`mini_dream`/dreamer, dashboard UI) plus **#1237/#1238**
(ephemeral hand-query + continuous compaction, implements #896); the a2a series **#1219/#1220/#1221**;
`benhoverter`'s **#1205** (OpenFang tool surface v2 over the MCP bridge), **#1196** (tail-load global
`RULES.md`), **#1229** (Discord outbound attachments with SSRF-guarded multipart), **#1151**
(claude_code image materialisation); **#1191** (directory auth + role mapping); **#1260**
(`LoopRegistry` + REST `/api/loops`); **#1250** (`query_hand_ephemeral`); **#946** (WeCom WebSocket
stream mode); **#943** (per-session agent endpoints); **#1093** (Volcano Engine providers).

**Dependabot backlog** (safe, mechanical): #1198 `axum 0.8.8→0.8.9`, #1199 `rmcp 1.3.0→1.7.0`,
#1200 `tauri-plugin-updater`, #1201 `tauri-build`, #1202 `libc 0.2.185→0.2.186`, #1255
`actions/checkout 6→7`, #1263 `tauri-action 0→1`. Note #1199 (rmcp 1.7.0) is a meaningful MCP
version jump and interacts with #1096/#1203.

---

## 6. Issues that are already fixed in the v0.6.9 you are running

Do not chase these; the tracker is just stale. All verified in `/opt/openfang` at `acf2587e`:

| # | Status in v0.6.9 | Evidence |
|---|---|---|
| **#1154** (Ollama/LM Studio remote hosts) | **Fixed for the chat path** | `model_catalog.rs:345-352` cites the issue by number; `drivers/mod.rs:52-82` normalises `OLLAMA_HOST`/`LMSTUDIO_HOST`/`VLLM_HOST`/`LEMONADE_HOST` (+`_BASE_URL`) into full `/v1` URLs. **Embedding is still broken — that's #1212.** |
| **#1097** (workspace pollution) | **Fixed** | `kernel.rs:292-296` "…regardless of where the user pointed the user-facing workspace. See issue #1097."; backfill at `kernel.rs:2163-2169`. |
| **#1085** ("No active connection") | Not reproducible as filed | Reported on v0.4.4; WS auth/reconnect reworked by #1179/#1189/`ws reconnect` before v0.6.9. |
| **#1172** (HAND.toml hash on reload) | **Fixed** | `openfang-hands/src/registry.rs:61-101` + five `emit_hand_loaded_audit` call sites (`:164,226,258,306,325`), wired at `kernel.rs:1255-1288`. Live chain contains both `HAND.toml load …` and `HAND.toml reload …` entries. |
| **#1174** (`POST /api/audit/append`) | **Fixed** | Route at `server.rs:398-401` (comment cites the issue), handler `routes.rs:3944`. |

Conversely, all 42 issues with `closedAt == 2026-05-12` (#868, #869, #871, #890, #915, #931, #1026,
#1031, #1048, #1051, #1062, #1067, #1083, #1094, #1125, #1134, #1140–#1148, #1152–#1165, #1167,
#1169, #1173, #1178, #1179, #1187–#1189) are **in** v0.6.9. Useful ones to know you already have:
#1051 (configurable STT/TTS/image-gen URLs + `audio_base_url` for local Whisper), #1125 (hardcoded
600s timeout removed), #1094 (`--json` on CLI commands), #1155 (bind to `0.0.0.0`), #1173
(`--add-host=host-gateway` documented), #1157 (vLLM ≥0.19 `reasoning_content` fix), #1189 (WS auth
loopback bypass closed).

---

## Gotchas (verified, not repeated from docs)

1. **`main` is not ahead of `v0.6.9` — there is nothing to upgrade to.** Both are `acf2587e`. The
   only branches ahead are seven `dependabot/*`. If a tool or doc tells you to "pull latest", you
   get the exact bytes you already have.

2. **A default v0.6.9 install burns money while idle.** `efbefa1` auto-spawns every agent in
   `$OPENFANG_HOME/agents/`, and three bundled samples carry live schedules
   (`agents/orchestrator/agent.toml:53` every 120s, `agents/ops/agent.toml:32` every 5m,
   `agents/health-tracker/agent.toml:59` every 1h) ≈ **1032** LLM calls/day (720 + 288 + 24) with nobody
   using it (#1206 — the issue's own "~630/day" figure does not match its own "43 calls/hr").
   Strip those `[schedule]` blocks before first boot.

3. **`[heartbeat] default_timeout_secs` is silently bypassed for any agent with an `[autonomous]`
   block.** `heartbeat.rs:161-166` prefers `autonomous.heartbeat_interval_secs *
   UNRESPONSIVE_MULTIPLIER`; with the defaults (30 × 2) that is the mysterious `timeout_secs=60` in
   #1252. Set `heartbeat_interval_secs` **per agent**, or drop `[autonomous]`. The issue's own
   diagnosis ("the key has no effect") is wrong — the key works, it's just outranked.

4. **Deleting a workflow does not delete its file.** `workflow.rs:235-238` mutates only the
   in-memory map; `kernel.rs:4468-4484` re-imports every `.json` at boot. `rm
   $OPENFANG_HOME/workflows/<id>.json` yourself, or the "deleted" (possibly scheduled) workflow
   resurrects on restart (#1192).

5. **`collect` in a workflow silently includes pre-fan-out output.** `workflow.rs:635-638` joins the
   *global* `all_outputs` buffer, contradicting `docs/workflows.md`. Your synthesis step is getting
   more context than you think (#1253).

6. **Local-LLM privacy has a hole: chat is local, embeddings often are not.** The chat path honours
   `OLLAMA_HOST`/`[provider_urls]`. The embedding path checks `[memory] embedding_provider` first
   (`kernel.rs:1012`), then scans `OPENAI_API_KEY → GROQ_API_KEY → MISTRAL_API_KEY →
   TOGETHER_API_KEY → FIREWORKS_API_KEY → COHERE_API_KEY` in that fixed priority order
   (`kernel.rs:1040-1047`), and only falls back to local `ollama/vllm/lmstudio` when **none** of the
   six is present (`kernel.rs:1074-1076`). It does not *demand* a cloud key — but if one happens to
   be exported, memory-recall silently ships text to that cloud on an otherwise "all-local" setup
   (#1212). Set `[memory] embedding_provider` explicitly to close it.

7. **`embedding.rs:197-210` will mangle a non-`/v1` base URL — for eight specific providers.** The
   append is guarded by `needs_v1 = matches!(provider, "openai"|"groq"|"together"|"fireworks"|
   "mistral"|"ollama"|"vllm"|"lmstudio")`; for those, `http://host:8004/v3` becomes
   `http://host:8004/v3/v1`. Other providers are left alone. And `infer_dimensions()`
   (`embedding.rs:106-119`) falls back to `1536` for any model not in its table, regardless of what
   the server returns (#1251).

8. **Never let an agent `web_fetch` a PDF.** `web_fetch.rs:115-131` reads `content-type` and uses it
   *only* for the HTML check; `application/pdf` falls through to `resp.text()`, putting hundreds of
   thousands of characters of compressed binary into context and poisoning the whole thread. The
   bundled `pdf-reader` skill is prompt-only and cannot intercept it (#1271).

9. **`web_fetch`'s size guard is bypassable.** It only fires when the server sends `Content-Length`
   (`web_fetch.rs:106`, and `tool_runner.rs:1606` for the legacy path). A chunked response buffers
   unbounded into memory before truncation (#1271).

10. **WASM `max_memory_bytes` is decorative.** `sandbox.rs:38-39` literally says "reserved for future
    enforcement" and no `Store::limiter()` is ever installed — a WASM skill can grow to wasmtime's
    ~4GB default no matter what the manifest says (#1242).

11. **Skill signing is enforceable from exactly one surface.** `POST /api/skills/install` takes
    `require_signed` + `allowed_signer_keys` (`types.rs:70-72` → `routes.rs:3873-3879`) and does
    enforce them. But `openfang skill install` has no `--require-signed` flag (`main.rs:347-351`),
    there is no `openfang skill sign`, and `POST /api/clawhub/install` accepts only `slug`
    (`types.rs:115-118`) and calls `client.install()` with defaults (`routes.rs:4359`). CLI and
    ClawHub installs are therefore always TOFU (#1170).

12. **Global skills (`$OPENFANG_HOME/skills/`) are visible but not usable.** `file_read` resolves
    workspace-relative; absolute paths are allowed **only** when they canonicalise inside the
    workspace root (`workspace_sandbox.rs:15-60`), and `$OPENFANG_HOME/skills` never does. The
    agent's fallback `shell_exec` attempts then die on the metacharacter denylist — pipes and redirects are blocked **even
    in Full exec mode** (`tool_runner.rs:247-255`). Install skills per-workspace (#1038).

13. **One agent = one in-flight turn, forever.** `kernel.rs:1928-1937` takes a per-agent mutex before
    every message. Cloning the agent is the only parallelism; sessions of the same agent serialise
    (#795, #1230).

14. **`/v1/chat/completions` has no session isolation.** `ChatCompletionRequest`
    (`openai_compat.rs:26-33`) carries `{model, messages, stream, max_tokens, temperature}` — no
    `user`, no `session_id`, and the word "session" appears nowhere in the file. Every caller of the
    same agent shares one history. Do not expose that endpoint to more than one human (#1049).

15. **`POST /api/providers/{name}/key` is global and destructive** — it rewrites
    `$OPENFANG_HOME/secrets.env` and the process env for the whole daemon. Last write wins across
    every agent and clone (#1211).

16. **GHCR will never work.** The `docker` job in `release.yml` pushes fine but the "set visibility
    to public" step (L247-254) PATCHes the org package API with the default `GITHUB_TOKEN`, which
    lacks `admin:packages`. `docker-compose.yml` line 1 already admits it. Build from source or use
    release binaries (#1254, #961, #12).

17. **`release.yml:243` labels the image `org.opencontainers.image.licenses=MIT`, and that is
    *correct*** — the workspace is dual `Apache-2.0 OR MIT` (`Cargo.toml:23`), so MIT is a valid
    choice under the disjunction. (An earlier pass of this file called it a contradiction; it is not.
    A licence scanner will see MIT-only for the image and dual for the source — worth knowing, not a
    bug.)

18. **The WhatsApp channel is a browser-reachable, unauthenticated remote-control surface.** No auth
    on any gateway endpoint plus `Access-Control-Allow-Origin: *`, and loopback binding is no defence
    against a malicious web page. Leave it disabled (#1234, #1232, #1233).

19. **Matrix + encryption = silence.** `crates/openfang-channels/src/matrix.rs` contains zero
    occurrences of `encrypted`/`olm`/`megolm`. Element's default-encrypted DMs are unreachable
    (#1177).

20. **#1184's `openfang-mcp-bridge` crate does not exist in v0.6.9.** `crates/` has 13 members, none
    named that; `mcp.rs`/`mcp_server.rs` carry no `#[cfg(unix)]` and stdio uses
    `tokio::process::Command` (`mcp.rs:224`). Do not repeat the "Windows has no MCP transport" claim
    against this tree — it is about code that was never in it.

21. **The model catalog is a hardcoded 4,866-line Rust table** (`model_catalog.rs`). Adding a
    *provider* is a source change and a rebuild; adding a *model* can be done at runtime via
    `POST /api/models/custom` (persisted to `$OPENFANG_HOME/custom_models.json`) — but that route
    does **not** register the provider, so a custom provider stays invisible to `/api/providers`.
    The **six** open catalog PRs (#1272, #1267, #1248, #1216, #1210, #1093) will never merge upstream.

22. **A "no maintainer response" issue is the norm, not the exception.** **11** issues carry
    `needs-design`, not 9. Nine of them (#1186 #1184 #1181 #1180 #1177 #1174 #1172 #1171 #1170) were
    labelled in a **20-second burst** on `2026-05-12T12:31:32Z–12:31:51Z`; #1149 got the label at
    12:08:54 the same day, and #1078 on 2026-05-01. Nothing was designed for any of them. Do not read
    "labelled" as "acknowledged and planned".

---

*Data collected 2026-08-09 against `RightNow-AI/openfang` via `gh`, and cross-checked line-by-line
against the `v0.6.9` (`acf2587e`) working tree at `/opt/openfang` and the live container
`openfang-openfang-1` (`openfang 0.6.9`). The daemon API key referenced elsewhere in this skill lives
at `<OPENFANG_HOME>/config.toml` as the top-level `api_key`; it is never reproduced here.*
