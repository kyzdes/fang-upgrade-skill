---
name: fang-upgrade
description: Operating manual for fang-upgrade — the patched OpenFang v0.6.9 fork (github.com/kyzdes/fang-upgrade), and for stock v0.6.9 where the two agree. Use it for agents, hands and HAND.toml, cron jobs and agent schedules, workflows and triggers, providers/models/base_url and cost, channels/MCP/A2A, skills and ClawHub, shell_exec and the 65 builtin tools, the HTTP API with its api_key/Bearer auth, dashboard and TUI, and backups of /data. Covers what the fork changed — per-call usage metering with model disclosure, file_read paging, a 501 that names the working routes, redacted channel credentials — and the v0.6.9 traps that remain: a hand that vanishes after docker restart, HTTP 400 with an empty body, a periodic schedule that silently becomes every 300s, deactivating a hand deleting its cron jobs, agents forgetting after 20 messages, shell_exec rejecting pipes. Load it even if the name is never typed; not for unrelated host services or generic LLM-API questions.
---

# OpenFang — operator manual (this box)

OpenFang is a single-binary Rust "agent OS": one daemon holds a kernel (agent registry, SQLite
memory, cron scheduler, workflow engine, trigger bus, Merkle audit chain), an HTTP API + dashboard,
LLM drivers, a builtin 65-tool surface, curated agent packages called **hands**, prompt/code
**skills**, 42 chat **channels**, and MCP/A2A bridges.

**Upstream is dormant.** `main` == tag `v0.6.9` == `acf2587e`, last commit and last release both
2026-05-12; 73 open issues, 45 open PRs, 0 merges since. There is nothing to upgrade to — you are the
maintainer. Licence, the `librefang` fork, and the cherry-pick queue:
`known-issues-and-project-status.md`.

## Our deployment

| | |
|---|---|
| Source | `/opt/openfang`, branch `main` — a fork of `RightNow-AI/openfang`, ahead of upstream `main` (`acf2587`, tag `v0.6.9`, 2026-05-12). Same repo and branch (`main`) is also checked out at `/root/src/openfang`, but the two are **not guaranteed to be at the same commit** — `/root/src/openfang` is where patches land first and can run ahead of what's actually built into the running image at `/opt/openfang`; check `git -C <path> log -1 --oneline` in both before assuming a line-number citation applies to the live container. **13 crates**, no `openfang-mcp-bridge` |
| Container | `openfang-openfang-1`, image built from source (`docker compose up --build`; GHCR is private forever, #1254). Its `agents/` directory is empty, so #1206's ~1032 idle LLM turns/day does not apply here |
| API | `http://127.0.0.1:4200` **and** `<tailnet-ip>:4200` (Tailscale `<tailnet-host>`) |
| `OPENFANG_HOME` | `/data` in-container = `/var/lib/docker/volumes/openfang_openfang-data/_data` on host. Called `$D` from here down |
| Agent files | `$D/workspaces/<agent-name>/` — cwd for `shell_exec` (`tool_runner.rs:1705-1707`) and where deliverables land. The private **state** dir (`SOUL.md`, `sessions/`, `memory/`, `logs/`) is always name-derived (`kernel.rs:1694-1700`, fix for #1097); the user-facing workspace only *defaults* to it, and an explicit `workspace =` is left alone (`kernel.rs:1701-1708`). Neither agent here sets one, so the two coincide |
| Providers | Two custom ones, both registered in `[provider_urls]` and visible in `/api/providers`. **`hyperfusion`** — `https://api.hyperfusion.io/v1`, key `HYPERFUSION_API_KEY`, 9 models, all live, all tool-capable, real limits published by the router. **`y7router`** — `https://router.y7.hk/v1`, key `Y7ROUTER_API_KEY`, 36 models but only ~10 usable: `alibaba/*` and 13 of 14 `opencode/*` return 429 (pool full), `z/glm-5.2` is 502 access-denied, all four `wave/*` silently ignore the `tools` array (and `wave/fast` claims it called the function anyway), `ark/deepseek-v3-2` and `opencode/deepseek-v4-flash` return an empty body with no `usage`. Both `ds/*` work non-streamed but emit **zero `delta.content` chunks** when streaming — everything lands in `reasoning_content` |
| y7router caveats | The router publishes no limits, so every y7 model except `kimi/k3` carries a placeholder `context_window` of 131072. It also **ignores every OpenAI parameter** — `max_tokens`, `stop`, `seed`, `n`, `logprobs` and `response_format` are accepted and not honoured (asked for 5 output tokens, got 419), so output length and cost cannot be bounded there. Every request also carries ~5 800 tokens of injected hidden prompt before your text. `kimi/k3` is measured: needle-in-haystack retrieved at start, middle and end up to ~300k words; `prompt_tokens` clamps at exactly 1 000 000; past ~10 MB of payload it returns an empty body in ~5 s with no error |
| Model | `openai/gpt-oss-120b`, registered in `$D/custom_models.json` (`context_window 131072`, pricing **0.0/0.0** → every call meters at $0, USD quotas are inert, and `/message` drops `cost_usd` rather than showing zero) |
| API key | `$D/config.toml`, top-level `api_key`, 51 chars. Read it at the point of use and write `<API_KEY>` everywhere else — never into a job definition, a hand setting, or these docs. `GET /api/cron/*` and `/api/hands/*/settings` are public and echo stored values verbatim, and the key is a whole-filesystem read primitive via `POST /mcp` (both under Traps) |
| In container | `python3 node npm pip3` from the image; `yt-dlp` only from `pip3 install` into the **writable layer** — it survives `docker restart`, not `--force-recreate` or a rebuild. **No `curl`, `git`, `ffmpeg`, `ffprobe`, `whisper`, `sqlite3`, `jq`, `ps`, `pkill`, `ss`** (verified). `tar` is present |
| On host | `curl python3 git gh` (repo `RightNow-AI/openfang`) |

Other sessions mutate this instance. Re-read `/api/agents`, `/api/cron/jobs`,
`$D/hand_state.json` and `$D/custom_models.json` rather than trusting any transcript,
including this one.

## Fork vs stock v0.6.9

This box runs `main`, not stock v0.6.9. If this skill is ever pointed at an unmodified
OpenFang, everything in this table reverts to the stock behaviour on its right — check
`git -C /opt/openfang branch --show-current` before trusting the left column.

| Area | On this fork (`main`) | Stock v0.6.9 |
|---|---|---|
| `PUT /api/agents/{id}/update` | **501** `manifest_update_not_implemented`, body names 8 working routes (`routes.rs:6163-6230`) | **200** `{"status":"acknowledged"}`, changes nothing |
| `remove_custom_model` | recomputes the provider's `model_count` (`openfang-runtime/src/model_catalog.rs:473-488`) | leaves `model_count` stale after a removal |
| Runtime prompt sections | facts wrapped in tags, instructive prose stays on `##` headings (`cdd70de`) | plain `##` headings — leaked verbatim into generated documents |
| Fallback `base_url` | resolved per-provider from `[provider_urls]` (`kernel.rs:5692-5700,6930`) | inherited `[default_model]`'s `base_url` regardless of the fallback's own provider |
| Usage accounting | **per LLM call** — `MessageResponse` carries `model_used`, `provider_used`, `fallback`, `calls[]` (`openfang-api/src/types.rs:59-78`); disclosed on SSE, WS and `/v1/chat/completions` too, but **in three different shapes** — SSE emits one `event: call` per call and no summary, openai-compat nests everything under a `"openfang"` key (see `providers-and-models.md` §9.1b before writing a client); `/api/usage/by-model` books by the model that actually served the call | per agent turn, booked to the manifest's configured model even when a fallback served it |
| `/api/metrics` | adds `openfang_llm_calls_total`, `openfang_llm_tokens_total`, `openfang_llm_fallback_calls_total`, all labelled by the model that served the call (`routes.rs:3606-3630`); old `openfang_tokens_total` keeps updating with its pre-fork meaning — value per agent per hour, `provider`/`model` labels are the agent's *configured* model, not necessarily who served it (`routes.rs:3671-3680`) | only `openfang_tokens_total`, same agent-configured labels, no per-model series |
| `openfang_tool_calls_total` | counts tool calls per LLM response (`routes.rs:3682`) | identically zero — never instrumented |
| Hot config reload | `ReloadPlan` splits `applied_actions` from `deferred_actions`; e.g. `ReloadProviderUrls` writes the catalog but is reported **deferred**, because `base_url` resolution still prefers the frozen boot-time `self.config` for any provider already in `[provider_urls]` (`kernel.rs:4253-4277`) | `hot_actions` reported as done regardless of whether anything in-process actually changed |
| SQLite schema | `PRAGMA user_version = 9` (`openfang-memory/src/migration.rs:8`) | `user_version = 8` |
| `file_read` | still `read_to_string`s the whole file first (unchanged — see Traps), but now takes `offset`/`limit` for paging and reports a truncation header (`openfang-runtime/src/tool_runner.rs:577-578,1387-1393`) | no `offset`/`limit`, no truncation header |
| Channel adapters | Telegram no longer inlines the bot token into prompt text on a file fetch; Telegram + 6 more adapters (dingtalk, flock, gotify, messenger, threema, wecom) redact credentials out of `reqwest::Error` before it can be logged or surfaced (`acc85d7`, `crate::redact::redact_reqwest_error`) | a network error from any of those seven could carry the token/key in its `Display` output |

Everything **not** in this table — the 41 public routes, `POST /mcp` as a file-read primitive,
TUI/CLI auth gaps, the vanishing hand, the 20-message cap, `shell_exec` with no pipes — is
unchanged from stock and covered once, under Traps.

## Start here

```bash
export PATH="$HOME/.claude/skills/fang-upgrade/scripts:$PATH"
D=/var/lib/docker/volumes/openfang_openfang-data/_data   # still needed for direct file edits
ofdoctor          # <1 s, read-only: key, auth, provider, model, secret leaks, cron, hands
```

`scripts/` holds five host tools that already encode every procedure below — `ofctl` (API calls),
`ofdoctor`, `ofhand`, `ofcron`, `ofbackup`. Each takes `--help`; `scripts/README.md` says what each
one is for and why it exists. Reach for them instead of retyping the curl dance. The raw form is
kept below only where knowing what the tool does is part of the point. `$D` is assumed set from
here down.

## Two auth facts to get right before anything else

```bash
ofctl GET /api/health              # {"status":"ok","version":"0.6.9"}
ofctl --show-key-source            # 51 chars, top-level api_key in config.toml — never the value
```

1. **`grep '^api_key'` is broken** — it also matches `api_key_env` under `[default_model]`, and
   `cut` then concatenates both into a 71-char string; every request fails HTTP 400 with an empty
   body. Hand-rolled, it has to be `grep -m1 '^api_key = '`: the **space before `=` is what
   disambiguates**, because `api_key_env` can never match `^api_key = ` at any position in the file
   (tested against a deliberately reordered `config.toml`). Dropping the ` = ` and leaning on `-m1`
   alone happens to work here only because `api_key` is line 1 of *this* file; reorder it and `-m1`
   hands you `api_key_env`. `ofctl` sidesteps both: it reads the top-level key with awk that stops
   at the first `[table]`, and passes it to curl through a 0600 `--config` file, so the value never
   reaches `ps` or shell history.
2. **The in-container CLI never sends the key from `config.toml`.** The source does read it
   (`openfang-cli/src/main.rs:1621`), but at the wire the shipped binary sends only `accept`,
   `accept-encoding`, `host` — every writing command fails with
   `✘ Failed: Missing Authorization: Bearer <api_key> header`. Export the env var for anything that
   writes:

```bash
K=$(grep -m1 '^api_key = ' $D/config.toml | cut -d'"' -f2)
docker exec -e OPENFANG_API_KEY="$K" openfang-openfang-1 openfang hand check-deps clip
```

`openfang agent list` / `hand list` only *appear* to work because those GET routes are public.
The **TUI never authenticates at all** (`tui/event.rs:1224` shadows `daemon_client()` with a
header-less client) and silently renders empty screens.

## Everyday operations

### Inspect

```bash
ofctl GET /api/agents               # pretty-printed; -x 0.id pulls a single field out
ofctl GET /api/security             # authenticated
ofcron list                         # id, name, enabled, schedule, next run, failure count
ofhand list                         # disk vs registry vs active instance, with its config
ofctl -s -n GET /api/cron/jobs      # 200 — /api/agents, /api/hands and /api/cron/* are public
docker logs --tail 200 openfang-openfang-1 2>&1 | sed 's/\x1b\[[0-9;]*m//g'
```

Every write below needs an agent **UUID**, and `GET /api/agents` is the route that maps a *name* to
one for any agent (`/api/hands/active` and `/api/sessions` also carry `agent_id`, but only for
agents that already have a hand instance or a session):

```bash
curl -s http://127.0.0.1:4200/api/agents |
  python3 -c 'import sys,json;[print(a["id"],a["state"],a["name"]) for a in json.load(sys.stdin)]'
# bf7564a1-2c88-5fdf-8ba7-24788d4da8c1 Running youtube-insights
# 17ffd1ca-b548-4132-a305-df3e32fbd9e3 Running assistant
```

### Talk to an agent

```bash
ofctl -t 600 POST /api/agents/<AGENT_UUID>/message '{"message":"Reply with exactly: PONG"}'
# -> {
#      "response": "PONG", "input_tokens": 12034, "output_tokens": 3, "iterations": 1,
#      "model_used": "google/gemma-4-31b-it", "provider_used": "hyperfusion",
#      "calls": [
#        {"n":0,"provider":"hyperfusion","model":"google/gemma-4-31b-it",
#         "input_tokens":12034,"output_tokens":3,"tool_calls":0,"cost_usd":0.0}
#      ]
#      // no "fallback" key: every call was served by the model that was asked for
#      // no "cost_usd" at the top level: total cost was 0
#    }
#    Verified live against `/api/agents/17ffd1ca.../message` on this box, 2026-08-14.
#
#    Reply text is in `response`. A one-word turn still burns ~8.5k-12k input tokens because all
#    65 tool schemas ride on every request — size cron timeouts and hand loops against that floor.
#
#    `MessageResponse` has 9 fields (`openfang-api/src/types.rs:59-78`), not 4:
#    `response, input_tokens, output_tokens, iterations` always; `cost_usd, model_used,
#    provider_used, fallback` only `Some`; `calls` only non-empty.
#
#    `model_used`/`provider_used` describe the LAST call of the turn — for a multi-iteration
#    turn that is not necessarily what served the FIRST one. To see who served which call, read
#    `calls[]`: one entry per LLM call (`n` = iteration), each with its own `provider`, `model`,
#    `input_tokens`, `output_tokens`, `tool_calls` and `cost_usd` — the last is always present per
#    call, priced even at $0.0 for a free-tier model, unlike the top-level `cost_usd`.
#
#    `fallback` (`openfang-types/src/usage.rs:51-64`) appears ONLY when some call in the turn was
#    served by a substitute, and has SIX fields, not four: `used` (always true when present),
#    `calls` (how many of this turn's calls were substituted), `of` (total calls in the turn),
#    `requested` (what the first substituted call asked for), `served_by` (de-duplicated list of
#    models that actually served substituted calls), `reason` (why the first substitution
#    happened). Read `calls[].requested`/`calls[].reason` for the per-call detail `fallback` only
#    summarizes.
#
#    `-t 600` is not optional. `ofctl`'s 30 s default is far below a real turn: /message blocks
#    until the whole agent loop finishes, and one that reads a file and writes another runs several
#    LLM round-trips — 2-5 min is normal. On timeout curl gives up but the agent keeps going
#    server-side, so you get no output while work continues invisibly. Symptom of getting this
#    wrong: every call returns in an identical ~30 s with an empty body and nothing on disk.
#
#    Top-level `cost_usd` is ABSENT, not 0. It is set only when the turn's total cost > 0
#    (`kernel.rs:3037-3047`, gated on `[usage_footer]` mode, default `Full`) and the field is
#    `skip_serializing_if = "Option::is_none"`, so a 0.0/0.0 catalog entry makes the key vanish.
#    Never read its absence as "no tokens were spent" — check `calls[].input_tokens` /
#    `output_tokens` instead, those are always there. `calls[].cost_usd` does NOT have this
#    absence behaviour: it is a plain `f64`, always serialized, 0.0 for a free model rather than
#    omitted.
ofctl POST /api/agents/<AGENT_UUID>/session/reset      # wipe history
```

### Point-editing an agent vs. `PUT /api/agents/{id}/update` (do not DELETE + POST)

`PUT /api/agents/{id}/update` is a **501** on this fork — it changes nothing, on purpose
(`routes.rs:6163-6230`). It used to answer `200 {"status":"acknowledged"}` while silently
dropping the manifest; someone who trusted that code read the docs, found no other way to
change one field, and recreated the agent with `DELETE` + `POST` — losing its id and its
session history. The 501 body exists so that never has to happen again; it names the routes
that actually work, straight from the handler (verified live on this box, 2026-08-14):

```bash
ofctl PUT /api/agents/<AGENT_UUID>/update '{"manifest_toml":"..."}'
# -> 501 manifest_update_not_implemented — nothing changed, use one of:
```

| Route | Changes |
|---|---|
| `PATCH /api/agents/{id}` | `name`, `description`, `model` (+ optional `provider`), `system_prompt` |
| `PATCH /api/agents/{id}/config` | `name`, `description`, `system_prompt`, `emoji`, `avatar_url`, `color`, `archetype`, `vibe`, `greeting_style`, `model`, `provider`, `api_key_env`, `base_url`, `fallback_models` |
| `PATCH /api/agents/{id}/identity` | `emoji`, `avatar_url`, `color`, `archetype`, `vibe`, `greeting_style` (same six as a subset of `/config` — either route works for these) |
| `PUT /api/agents/{id}/model` | `model` (body: `{"model":"..."}`) |
| `PUT /api/agents/{id}/mode` | `mode` |
| `PUT /api/agents/{id}/skills` | `skills` |
| `PUT /api/agents/{id}/mcp_servers` | `mcp_servers` |
| `PUT /api/agents/{id}/tools` | `tool_allowlist`, `tool_blocklist` — gates the manifest's `capabilities` rather than replacing them; `capabilities` itself is not writable in place |

Fields that require recreating the agent — no route touches them: `max_iterations`,
`heartbeat_interval_secs`, `schedule`, `module`. For those, and only those,
`DELETE /api/agents/{id}` then `POST /api/agents` is correct — know going in that the new
agent gets a new id and its session history does not carry over.

### Install / update a hand (the only durable path)

```bash
ofhand lint    ./my-hand          # blocks on the traps that are invisible at runtime
ofhand install ./my-hand          # copy into $D/hands/<id>/, restart, prove it loaded
ofhand activate <id> videos_per_run=3
ofhand set <id> videos_per_run=1  # change an ACTIVE hand without losing its cron jobs
```

`install` copies the files, restarts, then greps the boot log for
`Loaded workspace hand hand=<id>` and confirms `GET /api/hands/<id>` — a manifest that failed to
parse cannot pass silently. Add `--dry-run` to see the plan, `--no-restart` to stage the file. By
hand it is `mkdir -p $D/hands/<id> && cp HAND.toml SKILL.md $D/hands/<id>/` then
`docker restart openfang-openfang-1` (or `docker cp <dir>/. openfang-openfang-1:/data/hands/<id>/`
— same thing, the volume is the same bytes; the trailing `/.` matters or the copy nests).

`openfang hand install` and `POST /api/hands/{install,upsert}` register **in memory only** and vanish
on restart; the one function that writes to disk targets `/root/.openfang/hands`, which the kernel
never reads. Use `upsert` for fast iteration, then `ofhand install`.

Hand agent ids are stable. An **unnamed** instance gets `uuid5(NAMESPACE_DNS, hand_id)`
(`kernel.rs:4004`); a **named** one derives from `"hand_instance_<instance_id>"` instead
(`:4002`) — both via `AgentId::from_string` (`openfang-types/src/agent.rs:123-126`). Verified here:
`uuid5(NAMESPACE_DNS,"youtube-insights")` == the live agent `bf7564a1-2c88-5fdf-8ba7-24788d4da8c1`.
So you can compute a hand's agent UUID before it exists, and it is why `ofhand set`
(edit `hand_state.json`, restart) keeps cron jobs pointing at the same agent while
deactivate-and-reactivate deletes them. Three `HAND.toml` details fail *silently* rather than
erroring, and `ofhand lint` fails the build on the first two and warns on the third:

- Write `provider = "default"` and `model = "default"` literally. The field defaults are `anthropic`
  / `claude-sonnet-4-20250514` (`openfang-hands/src/lib.rs:306-311`), so omitting them retargets the
  hand off this box's model — `hands.md` §2.1.
- Pass every setting value as a **JSON string**. Resolution is
  `config.get(key).and_then(|v| v.as_str())` (`openfang-hands/src/lib.rs:217-220`), so a JSON bool or
  number returns `None` and reverts to the default: `{"flag":true}` renders `Disabled` where
  `{"flag":"true"}` renders `Enabled` — `hands.md` §3.2.
- **Omit `max_iterations`** unless you want an hourly autonomous loop. Its mere presence adds
  `ScheduleMode::Continuous{3600}` on top of `AutonomousConfig` (`kernel.rs:3892-3906`).

### Cron (the only reliable scheduler)

```bash
ofcron create --agent assistant --name nightly-brief \
  --cron "0 3 * * *" --tz Europe/Berlin \
  --message "Run the nightly procedure." --timeout 600 \
  --file /data/out/log.md                  # add --dry-run to see the body first

ofcron enable <JID> ; ofcron disable <JID>
ofcron run <JID>    ; ofcron status <JID>
ofcron rm  <JID> --yes
```

`ofcron` resolves the agent name to a UUID, because `cron_create` parses `agent_id` strictly
(`kernel.rs:7406-7408`) and a name returns `Invalid agent ID: invalid character…`. It defaults
`--timeout` to 600 because the API's default is **120 s** and a turn running longer is killed
mid-flight (`kernel.rs:6605`; range 10–600, `openfang-types/src/scheduler.rs:33,36`). That default is
also why the `schedule_create` tool and `openfang cron create` cannot express a real job:
`schedule_create` hardcodes `"timeout_secs": null` (`tool_runner.rs:2347`) and the CLI omits the
field — and `tz` — from its request body entirely (`openfang-cli/src/main.rs:6113-6124`). Both land
on the 120 s default, so `POST /api/cron/jobs` is the only way to set a budget. `ofcron`
also rejects locally what the API 400s on: the name charset, and a cron expression that is not
exactly five fields of `0-9 * / - , ?` (`openfang-types/src/scheduler.rs:427-455`) — `MON-FRI`,
`@daily` and `@hourly` are all rejected upstream, so write `1-5`.

Enable *and* disable both go through `PUT …/enable` with a body — `POST …/enable` → 405,
`…/disable` → 404, and both CLI commands print `✔` while doing nothing. `/run` is async and does not
skip the next scheduled fire. Delivery is fixed at `{"kind":"none"}`, which always counts as
delivered; a failing `channel` delivery is recorded as a *job* failure and five auto-disable the job.

### Workflows

```bash
ID=$(ofctl -x workflow_id POST /api/workflows @wf.json)
ofctl -t 900 POST /api/workflows/$ID/run '{"input":"..."}'   # /run blocks for the whole run
ofctl DELETE /api/workflows/$ID
rm -f $D/workflows/$ID.json      # REQUIRED — the API never touches the file (#1192)
```

Do not copy an API request body into `$D/workflows/*.json` — they are different shapes. The POST
body uses `prompt` and `agent_name`; the on-disk file uses `prompt_template` and
`"agent": {"name": "..."}`, and every step needs `mode`. A step missing `mode` does not error: the
kernel logs `Invalid workflow JSON, skipping` (`kernel.rs:4378`) at the next boot and the workflow is
gone. (`$D/workflows/` is created lazily on the first POST and does not exist today.)

### Providers and models

```bash
ofctl -x total GET /api/models          # 250 = 205 built-in catalog + 45 custom
ofctl -x total GET /api/providers       # 44: 42 built-ins + hyperfusion + y7router

# register a model the hardcoded catalog does not have (fixes context window + pricing)
ofctl POST /api/models/custom '{
  "id":"<VENDOR>/<MODEL>","provider":"<PROVIDER>","context_window":131072,
  "max_output_tokens":32768,"input_cost_per_m":0.1,"output_cost_per_m":0.5,"supports_tools":true}'

# There is no update — POST on an existing (id, provider) pair is a 409 that changes nothing.
# To edit an entry, delete and re-add. Both take effect immediately, no restart:
ofctl DELETE /api/models/custom/kimi/k3      # route is {*id}: put real slashes, do NOT url-encode
ofctl POST   /api/models/custom '{"id":"kimi/k3", ...}'
# Editing $D/custom_models.json by hand also works but needs a container restart, so prefer the API.

# Register the PROVIDER separately or it stays invisible to /api/providers, the dashboard and
# every per-provider route — inference still works, since that only needs base_url:
ofctl PUT /api/providers/<NAME>/url '{"base_url":"https://..."}'
ofctl POST /api/providers/<NAME>/key '{"key":"<KEY>"}'   # writes $D/secrets.env as <NAME>_API_KEY
```

Name a custom provider so that **no model id starts with `<provider>/`** — that prefix gets
stripped (#1195, under Traps). `y7router` is named that way precisely because its models carry
`ark/`, `ds/`, `alibaba/` prefixes.

Provider key writes take `{"key": "..."}`, **not** `{"api_key": ...}` (400 with an explicit body).

### Config changes

Edit `$D/config.toml`, then `docker restart openfang-openfang-1`. `POST /api/config/reload` is not a
substitute: `build_reload_plan` compares only 25 of `KernelConfig`'s 47 fields, so `[exec_policy]`,
`[docker]`, `[budget]`, `[auth]`, `[heartbeat]` and 17 others reload as "no changes" with no warning.

### Backup / restore

```bash
ofbackup create /srv/backups        # snapshot + state.tgz + MANIFEST, all 0600
ofbackup verify /srv/backups/openfang-<stamp>
ofbackup restore /srv/backups/openfang-<stamp>          # prints the plan
ofbackup restore /srv/backups/openfang-<stamp> --yes    # carries it out
```

Everything lives in `$D`. The SQLite substrate is WAL-mode, so never `cp` a live DB — `create` takes
it through SQLite's own backup API inside the container (13 tables, `PRAGMA user_version = 9` —
see Fork vs stock) and
tars `config.toml secrets.env cron_jobs.json custom_models.json hand_state.json daemon.json hands/
workspaces/`. `restore` stops the container, unpacks, and — the step that is easy to forget by hand —
deletes any stale `-wal`/`-shm` next to the restored file, so a half-written transaction cannot
replay over a good snapshot. It refuses a snapshot that fails `integrity_check`. Triggers and
workflow *runs* are memory-only and never come back; cron jobs, hands, agents and sessions do.
Manual snapshot and restore procedure: `architecture.md` § Data locations.

## Traps

| You saw | It is |
|---|---|
| HTTP 400, empty body | `$K` is the 71-char `api_key`+`api_key_env` concatenation — you grepped without the ` = ` |
| `Missing Authorization: Bearer <api_key> header` | the in-container CLI never reads `config.toml`; add `-e OPENFANG_API_KEY="$K"` to `docker exec` |
| a green tick and no change | `openfang cron enable`/`disable` report success unconditionally — verify over HTTP |
| empty TUI screens | the TUI never authenticates (`tui/event.rs:1224`) — use the HTTP API |
| `Audit trail integrity check FAILED` | a missing auth header, not a corrupt chain |

**Auth / exposure** (`security-model.md`)

- `GET /api/cron/*` is public (`middleware.rs:136`) **and returns `delivery_targets` verbatim**, with
  no credential, on `127.0.0.1:4200` *and* `<tailnet-ip>:4200`. This box carried a live example: job
  `fb0efded-…` ("youtube-insights nightly") held `"auth_header":"Bearer <API_KEY>"` in a webhook
  target. That job was deleted on 2026-08-10 and `cron_jobs.json` is now `[]` — but the exposure is
  structural, not historical, and another session can recreate it. Run `ofdoctor` rather than
  trusting this paragraph: it re-checks every job for credential-shaped delivery targets and greps
  the public endpoints for the daemon key itself. Because the key was served unauthenticated on the
  Tailnet while that job existed, **rotating the daemon `api_key` is the conservative call.** There
  is no route that edits a job in place, so remediation is `DELETE /api/cron/jobs/<JID>` and
  recreate. Cheaper alternatives, in order: drop the webhook and let the 20-message history cap do
  the trimming, or run `…/session/reset` from a **host** crontab a few minutes after the fire time so
  the key never enters OpenFang's own state (`youtube-pipeline.md` §5). `ofcron` has no flag for a
  webhook auth header, on purpose.
- 41 path patterns are public (`middleware.rs:98-140`), including `/api/agents`, `/api/config`,
  `/api/sessions`, `/api/approvals` (leaks 200 chars of every gated command), `/api/hands/*/settings`
  (leaks text-setting values), `/api/health/detail`, and `/api/logs/stream` — which SSE-streams the
  whole Merkle audit chain that `/api/audit/recent` protects. (Eleven of the 41 lack an `is_get`
  guard; that is *not* a write bypass — `security-model.md` §10.6c.)
- **`POST /mcp` turns the API key into an arbitrary-file-read primitive.** `mcp_http` calls
  `execute_tool` with `allowed_tools=None`, `workspace_root=None`, `exec_policy=None`
  (`routes.rs:7216-7292`), so `tools/call file_read {"path":"/data/secrets.env"}` returns
  `HYPERFUSION_API_KEY` and `/data/config.toml` returns the key itself — verified live. All 65 tools
  are exposed. Only `shell_exec` is approval-gated, and if approved it skips `safe_bins` entirely.
- Clearing `api_key` on Docker **locks you out**, it does not open the box: the container always sees
  the bridge gateway (172.19.0.1) as the peer, so the loopback exemption never applies — and all
  external clients then share one 500-token/min rate-limit bucket.
- `openfang security verify` prints `✘ Audit trail integrity check FAILED` when the real problem is
  the missing header. `openfang config show` prints `api_key` in plaintext.

**Model / provider** (`providers-and-models.md`)

- `strip_provider_prefix` (`agent_loop.rs:212-222`) builds `"{provider}/"` at runtime and truncates
  any model id starting with it — silently, and the stripped id is **persisted**. This is #1195, real
  and unfixed. We are safe **only because the provider is named `hyperfusion`**; the `base_url` has
  nothing to do with it. Never rename the provider to `openai`.
- An uncatalogued model gets a fabricated `$1/$3` per-M price (`metering.rs:289`) and a 200 000-token
  assumed context window. `available:true` in `/api/models` means nothing for a custom provider.
- `POST /api/providers/{name}/test` false-negatives for 15 of 42 built-ins and 404s for custom ones.

**Tools / execution** (`tools-reference.md`)

- **`shell_exec` is not a shell, in any mode.** `` ` $( ${ ; | > < { } & `` newline and NUL are
  rejected *before* the exec-policy check, even in `full` mode. No pipes, no redirection, no `&&`
  ever. `&` in a URL is rejected; `<` inside `-f "bv[height<=1080]"` is rejected. Escape hatch: write
  a script file and invoke it as flat argv.
- Every tool call is wrapped at **120 s** (`TOOL_TIMEOUT_SECS`, `agent_loop.rs:47`; 600 s for
  `agent_send`/`agent_spawn`, `:54`; override with `OPENFANG_TOOL_TIMEOUT_SECS`, `0` disables — not
  set on this box). A larger `timeout_seconds` on `shell_exec` is silently overridden: the shorter of
  the two wins.
- stdout is truncated at a hardcoded 100 000 bytes; `exec_policy.max_output_bytes` (102 400) and
  `no_output_timeout_secs` are dead config. Subprocesses get `env_clear()` + 8 safe vars — they never
  see provider API keys.
- `file_read` still has **no size limit on the read itself** — `tokio::fs::read_to_string`
  (`tool_runner.rs:1381-1436`) pulls the entire file into the daemon's memory before `offset`/`limit`
  slice it (the fork added the paging — see the delta table above), so size a file before reading
  it. The budget only trims what reaches the model:
  tool results cap at 30 % of the context window (78 643 chars at 131 072), sessions compact at 75 %.
- `tool_policy.rs` is dead code — **nothing is denied to a subagent by depth**. Use `tool_blocklist`.
- `[capabilities] shell/network/memory_*/agent_message` are inert for LLM agents (WASM only). Only
  `capabilities.tools`, `tool_allowlist`/`tool_blocklist`, `profile` and `exec_policy` restrict one.
- Any agent tagged `hand:*` **auto-approves every gated tool**, and a hand declaring `shell_exec`
  is granted `ExecSecurityMode::Full` + 300 s automatically (`kernel.rs:3912-3921`).
- LLM `agent_spawn` skips `validate_capability_inheritance` — a child can hold any capability.

**Scheduling / lifecycle** (`automation-workflows-triggers-schedules.md`)

- `[schedule] periodic = { cron = "..." }` understands only `every <N>s|m|h|d`; a real cron
  expression silently becomes **300 seconds**. Use a cron job for wall-clock work.
- Any `[autonomous]` table pins the agent to a 60 s heartbeat timeout and makes
  `[heartbeat] default_timeout_secs` (default 180) a no-op for it (#1252).
- **Deactivating a hand permanently deletes its cron jobs.** Update by file copy + restart instead.
- Triggers are memory-only — every restart wipes hand-made ones. `EventPayload::Custom` renders as
  `"Custom event (N bytes)"`, so `content_match` never matches wake/`event_publish`/cron text; only
  `"all"` fires. `{"lifecycle":{}}` is a 400 (bare strings for unit variants).
- 5 consecutive cron failures auto-disable a job, and a failed **channel** delivery counts as a
  failure. `delivery:{"kind":"none"}` always succeeds.
- `memory_store` writes to **one global namespace** shared by every agent
  (`00000000-0000-0000-0000-000000000001`). Use a JSON file in the workspace for real state.
- Every agent defaults to **20 messages** of history (`DEFAULT_MAX_HISTORY_MESSAGES`,
  `openfang-types/src/agent.rs:523`). `AgentManifest.max_history_messages` can raise it, but there is
  no `HAND.toml` field and no API route for it — so hand agents are stuck at 20, about 10 tool calls.

**Environment**

- No IPv6 route but AAAA records resolve → intermittent `[Errno 101] Network is unreachable`. Force
  IPv4 in anything you write.
- `hand install-deps` picks `linux_apt` first and `sudo` does not exist (exit 127). Omit `linux_apt`.
- `gpt-oss-120b` via Hyperfusion sometimes emits a tool call as **plain text** and ends the turn.
  Write the valuable artifact first, avoid LLM-maintained counters, make the deliverable a file.

## Bundled, ready to use

Don't rewrite these from scratch — they are known-good and already ran on this box. Everything in
`scripts/` is POSIX `sh` or python3 stdlib, takes `--help`, and honours `OPENFANG_URL` /
`OPENFANG_HOME_HOST` / `OPENFANG_CONTAINER` / `OPENFANG_API_KEY`.

| Path | What it is |
|---|---|
| `scripts/ofctl` | One-line authenticated API calls. Reads the top-level `api_key` with awk that stops at the first `[table]`, and keeps it out of `ps` and shell history. `-x` extracts one field, `-n` sends no credential so you can prove a route is public, `-t` raises the timeout for workflow runs. |
| `scripts/ofdoctor` | Read-only preflight: key, auth enforcement, provider registration, model resolution and pricing, the #1195 prefix collision, **secrets appearing in unauthenticated responses**, cron credential leaks and failure counts, hands on disk that never loaded, container binaries, file modes. `--full` also runs the real `openfang doctor` and explains why its `✘ No LLM provider API keys found!` is a false alarm here. |
| `scripts/ofhand` | `lint` / `install` / `activate` / `set` / `list`. Install = copy + restart + prove it loaded. No `deactivate`, because that deletes the hand's cron jobs. |
| `scripts/ofcron` | Create / list / enable / disable / run / rm, validating locally everything the API 400s on and resolving agent names to UUIDs. |
| `scripts/ofbackup` | WAL-safe snapshot via SQLite's backup API + state tarball + a restore that strips stale `-wal`/`-shm`. |
| `scripts/ofcheck-rs` | `cargo check`/`clippy` for a **fork source worktree**, run inside a container — no Rust toolchain needed on the host. `ofcheck-rs <worktree-path> [crate...]`. Each worktree gets its own build-target Docker volume (`fang-target-<slug>`); a shared one made cargo replay a stale fingerprint from a different worktree, silently returning the wrong worktree's result. First check per worktree is ~4.5 min, then incremental; a build target runs 5-12 GB, so free ≥12G before running it and `docker volume rm fang-target-<slug>` after a patch lands. For patching `/root/src/openfang` itself, not for operating the running instance. |
| `scripts/ofmutate` | Mechanical red-before-green for a fork patch: `ofmutate <worktree> --test <filter> -p <crate>`. Runs the filtered test as committed (must be green **and** non-empty), reverse-applies only the patch's *production* hunks — Rust unit tests sit in the same file under `#[cfg(test)]`, so reverting whole files would delete the test along with the fix and prove nothing — then requires red, then restores the tree. `ДОКАЗАНО (RED-ASSERT)` is proof; `СЛАБОЕ КРАСНОЕ (RED-COMPILE)` only proves the test knows the new API, not that it checks its behaviour; `ТАВТОЛОГИЯ` (exit 1) and the `passed=0` refusal (exit 4, filter matched nothing) mean there is effectively no test. Refuses a dirty worktree (exit 2) and <12 GB free (exit 3 — the tool declining, **not** a patch defect). Shares `ofcheck-rs`'s build volume, so runs stay incremental: measured 203 s cold / 35 s warm on `openfang-runtime`. On its first real use it found a tautology in an existing fork patch (`fix/file-read-truncation`: `test_file_read_full_file_no_truncation_marker` passes with the fix reverted). |
| `scripts/ytwatch.py` | Channel listing + caption fetch (no video download) + `seen.json` dedup. Written as a file precisely because `shell_exec` rejects pipes and redirection. Install: `docker exec openfang-openfang-1 mkdir -p /data/workspaces/<agent>/bin` then `docker cp ~/.claude/skills/fang-upgrade/scripts/ytwatch.py openfang-openfang-1:/data/workspaces/<agent>/bin/` (needs `pip3 install --break-system-packages yt-dlp` in the container). |
| `scripts/rtwatch.py` | RuTube sibling of `ytwatch.py`, same design (flat argv, one JSON object per call, `seen.json` dedup) but not a drop-in: no RSS feed (uses `yt-dlp --flat-playlist`), subtitles are `srt` under `subtitles` not `automatic_captions` (`--write-subs`, not `--write-auto-subs`), and video ids are 32-char hex, not 11-char base64. Same install pattern as `ytwatch.py`, different filename. |
| `assets/youtube-insights-hand/` | A complete working `HAND.toml` + `SKILL.md`. Start any new hand by copying this, not from a blank file. Install: `ofhand install ~/.claude/skills/fang-upgrade/assets/youtube-insights-hand`. `assets/README.md` covers the manual host-volume copy, why `docker cp <dir> …:/data/hands/<id>` silently nests and reloads the old definition, and the fact that the deployed copy still carries the unreachable `timeout_seconds: 240`. |

## Where to read more

Every reference file opens with a table of contents — jump to the section you
need instead of reading the whole file.

| File (`references/`) | Answers |
|---|---|
| `architecture.md` | Boot sequence, agent restore/kill/clone, agent loop, compaction, session repair, SQLite schema v9, `/data` layout, manual backup/restore, hot-reload gaps, shared-memory namespace |
| `providers-and-models.md` | 42 providers / 205 catalog models (live `/api/models` total = 205 + custom entries), driver dispatch, `[default_model]`/`[provider_urls]`/`[provider_api_keys]`, custom providers + custom models, #1195 in full, fallback chains, routing, cost |
| `hands.md` | Complete `HAND.toml` spec, settings/requirements semantics, the 9 bundled hands, lifecycle, hand API + CLI, a full worked custom-hand template |
| `automation-workflows-triggers-schedules.md` | Workflows (step modes, `collect` bug), triggers (patterns, `describe_event`), cron (schema, tick loop, delivery), agent `[schedule]`, heartbeat, `/hooks/*` |
| `tools-reference.md` | All 65 builtin tools with exact JSON schemas, the three timeout layers, truncation, SSRF, web_search providers, exec-policy modes |
| `security-model.md` | What actually enforces anything, capabilities vs tool lists, approvals, `exec_policy`, WASM sandbox, skills isolation, taint, audit chain, auth matrix, Traefik hardening checklist |
| `skills-and-clawhub.md` | `skill.toml` vs `SKILL.md`, 61 bundled skills, global/workspace/allowlist scoping, config injection, `skill_*` tools, why ClawHub is dashboard-only |
| `channels-mcp-api.md` | 42 channels + config shapes, bindings/routing, MCP client+server, A2A, the full public-vs-authenticated endpoint matrix, `/hooks/*` double auth |
| `known-issues-and-project-status.md` | Project-status hard numbers, all 73 open issues triaged by severity, the 45-PR cherry-pick queue, licence and the `librefang` fork, what is already fixed in v0.6.9 |
| `youtube-pipeline.md` | Worked end-to-end build: yt-dlp intake, captions without video, `ytwatch.py`, hand manifest, nightly cron, token budget — the best template for any new pipeline hand |
| `fresh-server-runbook.md` | Deploying the patched fork on a clean server end to end: the two compose defaults that must be closed first, verifying the build actually produced a binary, api_key placement, what to test auth on (POST, not the public GET), installing this skill, and the failure table |
