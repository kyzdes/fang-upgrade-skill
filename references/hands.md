# OpenFang Hands — Operator & Author Reference

**Scope:** OpenFang **v0.6.9** (`/opt/openfang`, tag `v0.6.9`, commit `acf2587`). Every claim is
grounded in that source tree or verified live against container `openfang-openfang-1`
(API `http://127.0.0.1:4200`, `OPENFANG_HOME=/data`).

Crate: `crates/openfang-hands/` — `src/lib.rs` (909 L, types + settings resolution),
`src/registry.rs` (1365 L, registry + requirement probing), `src/bundled.rs` (457 L, `include_str!`
embedding), `bundled/<id>/{HAND.toml,SKILL.md}` (9 hands).

**`README.md` says "The 7 Bundled Hands" (`README.md:78`). There are 9.** It also shows
`openfang hand status <id>` (`README.md:95`) — that subcommand does not exist. Trust this file.

---

## Table of contents

- [0. TL;DR](#0-tldr)
- [1. What a Hand is](#1-what-a-hand-is)
- [2. Complete `HAND.toml` specification](#2-complete-handtoml-specification)
- [3. Settings](#3-settings)
- [4. Requirements](#4-requirements)
- [5. The 9 bundled hands](#5-the-9-bundled-hands)
- [6. Lifecycle](#6-lifecycle)
- [7. API surface](#7-api-surface)
- [8. CLI and agent-facing tools](#8-cli-and-agent-facing-tools)
- [9. Gotchas (all verified on this box)](#9-gotchas-all-verified-on-this-box)
- [10. Complete worked template for a custom hand](#10-complete-worked-template-for-a-custom-hand)
- [11. Where the docs lie](#11-where-the-docs-lie)
- [12. Open questions](#12-open-questions)

## 0. TL;DR

| | |
|---|---|
| Bundled hands | **9**: `browser clip collector infisical-sync lead predictor researcher trader twitter` |
| Bundled hand source | compiled into the binary via `include_str!` (`bundled.rs:6-54`) — editing `/opt/openfang/crates/.../bundled/*` does nothing until you rebuild |
| Custom hand on-disk path | **`$OPENFANG_HOME/hands/<dir>/HAND.toml`** (+ optional `SKILL.md`) → `/data/hands/…` here. Loaded at daemon boot only (`kernel.rs:906-918`) |
| `openfang hand install <path>` | registers in memory only — **lost on restart** (see [§9.2](#92-gotcha-hand-install-does-not-persist)) |
| Active-instance state | `$OPENFANG_HOME/hand_state.json` → `/data/hand_state.json` |
| Requirements | **advisory** — never block activation ([§4.4](#44-nothing-enforces-requirements)) |
| Settings → agent | appended to the system prompt as a `## User Configuration` markdown block; env-var *names* (never values) forwarded to `shell_exec` |
| Instances per hand | 1 unnamed, or N uniquely-named (`--name`); names are **not persisted** across restart |
| Uninstall a custom hand | no API/CLI exists — restart the daemon (in-memory) or delete the dir (on-disk) |

Live inventory on this box (`GET /api/hands` + `GET /api/hands/{id}`, re-verified 2026-08-10) —
**10 definitions: the 9 bundled ones plus the workspace hand `youtube-insights`** loaded from
`/data/hands/` (see `youtube-pipeline.md`):

```
browser          productivity   req_met=true   settings=5   reqs python3✓ chromium✗(optional)
clip             content        req_met=false  settings=9   reqs ffmpeg✗ ffprobe✗ yt-dlp✓
collector        data           req_met=true   settings=8   (no requires)
infisical-sync   security       req_met=false  settings=4   reqs INFISICAL_URL✗ _CLIENT_ID✗ _CLIENT_SECRET✗
lead             data           req_met=true   settings=9   (no requires)
predictor        data           req_met=true   settings=8   (no requires)
researcher       productivity   req_met=true   settings=8   (no requires)
trader           data           req_met=true   settings=12  (no requires)
twitter          communication  req_met=false  settings=11  reqs TWITTER_BEARER_TOKEN✗
youtube-insights content        req_met=true   settings=4   reqs python3✓ yt-dlp✓   <- workspace hand, ACTIVE
```

`clip`'s `yt-dlp` requirement now probes ✓ because `pip3 install yt-dlp` was run in the container;
`ffmpeg`/`ffprobe` are still absent, so `clip` stays `requirements_met=false`. Other sessions mutate
this instance — re-run the two GETs rather than trusting this block.

---

## 1. What a Hand is

A Hand = a `HAND.toml` manifest + an optional `SKILL.md` knowledge blob. Activating it makes the
kernel synthesise an `AgentManifest` and spawn a **normal agent** with a deterministic UUID, a
curated tool list, an injected prompt, and two tags (`hand:<id>`, `hand_instance:<uuid>`).
There is no separate Hand runtime — everything after activation is the ordinary agent loop.

Composition of the spawned agent's system prompt (`kernel.rs:3867-3900`), in order:

```
<agent.system_prompt from HAND.toml>

---

## User Configuration

- <Setting label>: <resolved value>
...

---

## Reference Knowledge

<entire SKILL.md file, verbatim, including its YAML frontmatter>
```

Verified live — `GET /api/agents/<id>.system_prompt` on a probe hand returned exactly:
`"You are a probe.\n\n---\n\n## User Configuration\n\n- Mode: Beta (b)\n- Secret Field: hunter2-plaintext\n- Flag: Disabled\n\n---\n\n## Reference Knowledge\n\n-- SKILL --\n"`.

The `## User Configuration` block is omitted entirely when no setting produces a line
(`lib.rs:256-260`); the `## Reference Knowledge` block is omitted when `SKILL.md` is absent/empty
(`bundled.rs:64-66`, `kernel.rs:3895`).

---

## 2. Complete `HAND.toml` specification

Parser: `parse_hand_toml()` (`lib.rs:325-331`). **Two accepted layouts** — flat (all bundled hands
use this) or wrapped in a `[hand]` table. The parser tries flat first, then wrapped.

Serde is *not* `deny_unknown_fields`: unknown top-level keys (e.g. `version`, `author`) are silently
ignored. The registry's own test writes `version`/`author` keys (`registry.rs:1069-1070`) that go
nowhere.

### 2.1 Top level (`HandDefinition`, `lib.rs:359-395`)

| Key | Type | Req? | Default | Meaning |
|---|---|:--:|---|---|
| `id` | string | **yes** | — | Registry key. Used for the URL path, the CLI arg, the `hand:<id>` tag, and (for unnamed instances) to derive the agent UUID. Must be unique; a duplicate is rejected by `install`, accepted by `upsert`. |
| `name` | string | **yes** | — | Display name. `list_definitions()` sorts by this (`registry.rs:335`). |
| `description` | string | **yes** | — | Marketplace blurb. |
| `category` | enum | **yes** | — | `content` `security` `productivity` `development` `communication` `data` `finance` `other`. Lower-case. **Unknown values silently become `Other`** (`#[serde(other)]`, `lib.rs:51-52`) — verified: `category = "nonsense-cat"` installed fine and reported `"Other"`. |
| `icon` | string | no | `""` | Emoji, shown in the dashboard. `\U0001F3AC` escapes work. |
| `tools` | string[] | no | `[]` | Tool allowlist → `manifest.capabilities.tools`. Non-empty also forces `profile = ToolProfile::Custom` so profile expansion cannot override the list (`kernel.rs:3855-3859`). |
| `skills` | string[] | no | `[]` | Skill allowlist for the agent (empty = all). **No bundled hand uses this.** |
| `mcp_servers` | string[] | no | `[]` | MCP server allowlist (empty = all). **No bundled hand uses this.** |
| `requires` | `[[requires]]` | no | `[]` | See [§4](#4-requirements). |
| `settings` | `[[settings]]` | no | `[]` | See [§3](#3-settings). |
| `agent` | table | **yes** | — | See [§2.2](#22-agent-handagentconfig-librs275-317). |
| `dashboard` | table | no | `{metrics=[]}` | See [§2.4](#24-dashboard--dashboardmetrics-librs138-151-269-272). |

`skill_content` is `#[serde(skip)]` — it is populated at load time from the sibling `SKILL.md`,
never from TOML.

### 2.2 `[agent]` (`HandAgentConfig`, `lib.rs:275-317`)

| Key | Type | Req? | Default | Notes |
|---|---|:--:|---|---|
| `name` | string | **yes** | — | Agent name. For an *unnamed* instance this is the spawned agent's name; a pre-existing agent with the same name is **killed and replaced** on activation (`kernel.rs:3903-3919`). |
| `description` | string | **yes** | — | |
| `system_prompt` | string | **yes** | — | Base prompt. Use `"""…"""`. |
| `module` | string | no | `"builtin:chat"` | |
| `provider` | string | no | **`"anthropic"`** | The literal string `"default"` → inherit `config.default_model.provider` (`kernel.rs:3783-3787`). |
| `model` | string | no | **`"claude-sonnet-4-20250514"`** | `"default"` → inherit `config.default_model.model`. |
| `api_key_env` | string | no | none | Overrides the provider's key env var. |
| `base_url` | string | no | none | |
| `max_tokens` | u32 | no | `4096` | |
| `temperature` | f32 | no | `0.7` | |
| `max_iterations` | u32 | no | none | **The autonomy switch.** Present → `AutonomousConfig{max_iterations}` (`kernel.rs:3892-3898`) **and** `ScheduleMode::Continuous{check_interval_secs: 3600}` (`kernel.rs:3902-3906`). Absent → `ScheduleMode::default()` (Reactive; only runs on incoming messages). |
| `heartbeat_interval_secs` | u64 | no | `30` | Only meaningful with `max_iterations`. Kernel default 30 s is too aggressive for long LLM calls; only `researcher` overrides it (120). |

> **Verified trap:** omit `provider`/`model` and your hand silently targets Anthropic
> `claude-sonnet-4-20250514`, not the box's configured LLM. Live probe: a custom hand without them
> reported `provider: "anthropic", model: "claude-sonnet-4-20250514"`; the same hand with
> `provider = "default"` / `model = "default"` reported `hyperfusion` / `openai/gpt-oss-120b`.
> **Always write `provider = "default"` and `model = "default"` unless you mean otherwise.**

The kernel also derives, from the hand definition:

* **`exec_policy`** — if `tools` contains `shell_exec`, the agent gets
  `ExecSecurityMode::Full`, `timeout_secs = 300`, `no_output_timeout_secs = 120`
  (`kernel.rs:3912-3921`, the `mode` line is `:3915`). Hands are treated as curated packages: **declaring `shell_exec` routes
  commands through `sh -c` and skips the `safe_bins`/`allowed_commands` allowlist** that ordinary
  agents get (`tool_runner.rs:1650-1700`), plus the taint heuristics (`tool_runner.rs:277-288`).
  It does **not** buy you shell operators — `contains_shell_metacharacters` runs at
  `tool_runner.rs:249`, *before* the exec-policy branch, with the in-code comment "Always check for
  shell metacharacters, even in Full mode". Full mode gives you `$VAR` expansion and globbing, never
  `|`, `>`, `<`, `;`, `&`, `{}`, `` ` ``, `$(`, `${`.
* **`tags`** — `["hand:<id>", "hand_instance:<uuid>"]`.
* **`metadata.hand_allowed_env`** — see [§3.3](#33-how-env_var-and-provider_env-actually-work).

### 2.3 `[[requires]]` and `[requires.install]`

See [§4](#4-requirements) for the full table and probe semantics.

### 2.4 `[dashboard]` / `[[dashboard.metrics]]` (`lib.rs:138-151`, `269-272`)

```toml
[dashboard]
[[dashboard.metrics]]
label      = "Jobs Completed"          # required — also the JSON key in the stats response
memory_key = "clip_hand_jobs_completed" # required — structured-memory key the agent writes
format     = "number"                   # optional, default "number"
```

`format` is a **free-form string**, not an enum. Values in use across bundled hands: `number`,
`duration`, `text`, `percentage`. Nothing formats on it — `GET /api/hands/instances/{id}/stats`
returns `{"<label>": {"value": …, "format": "<format>"}}` and the built-in dashboard only prints it
as a hint (`static/index_body.html:3016`).

Metric resolution order (`routes.rs:5028-5046`): **shared** structured memory
(`shared_memory_agent_id()`, where the `memory_store` tool writes) first, then the agent's own
namespace, else `null`. So a metric only ever populates if the hand's prompt actually instructs the
model to call `memory_store` with that exact key — all bundled hands do this in a "Report" phase.

### 2.5 Minimal valid manifest

```toml
id = "minimal"
name = "Minimal"
description = "d"
category = "other"

[agent]
name = "minimal-hand"
description = "d"
system_prompt = "You are minimal."
```

---

## 3. Settings

### 3.1 Schema (`HandSetting`, `lib.rs:178-193`)

```toml
[[settings]]
key          = "stt_provider"    # required — the config map key
label        = "Speech-to-Text"  # required — the label shown in the prompt block and UI
description  = "…"               # optional, default ""
setting_type = "select"          # required — exactly one of: select | text | toggle (lower-case)
default      = "auto"            # optional, default ""
env_var      = "ELEVENLABS_API_KEY"  # optional; text-type only (see §3.3)

[[settings.options]]             # select-type only
value        = "groq_whisper"    # required
label        = "Groq Whisper"    # required
provider_env = "GROQ_API_KEY"    # optional — "Ready/Missing" badge + env grant when selected
binary       = "whisper"         # optional — "Ready/Missing" badge (binary on PATH)
```

`setting_type` has **no** `serde(other)` fallback — an unknown value is a hard parse error
(and surfaces as the misleading `missing field 'hand'`, see [§9.1](#91-gotcha-every-toml-error-says-missing-field-hand)).

### 3.2 Resolution → prompt block (`resolve_settings`, `lib.rs:209-266`)

For each setting, in declaration order, the chosen value is
`config.get(key).and_then(as_str)` **falling back to `setting.default`**, then:

| type | prompt line emitted | env grant |
|---|---|---|
| `select` | `- {label}: {matched option label} ({value})`; if no option matches, `- {label}: {value} ({value})` | the matched option's `provider_env`, if any |
| `toggle` | `- {label}: Enabled` when the value is exactly `"true"` or `"1"`, else `- {label}: Disabled` | none |
| `text` | `- {label}: {value}` — **skipped entirely when the value is empty** | the setting's `env_var`, if any and the value is non-empty |

Non-empty lines are joined and prefixed with `## User Configuration\n\n`.

> **Verified trap — values must be JSON *strings*.** `config.get(key).and_then(|v| v.as_str())`
> returns `None` for a JSON boolean/number, so the setting silently reverts to its default.
> Live probe: activating with `{"flag": true}` on a toggle whose default is `"false"` rendered
> `- Flag: Disabled`. Send `{"flag": "true"}`. The dashboard wizard does this correctly
> (`index_body.html:3271`); hand-rolled API calls usually do not.

> **Verified trap — text settings leak into the prompt.** `hunter2-plaintext` typed into a text
> setting appeared verbatim in the agent's system prompt. `clip.elevenlabs_api_key`,
> `clip.telegram_bot_token`, `clip.whatsapp_token`, `trader.alpaca_api_key`,
> `trader.alpaca_secret_key`, `twitter.twitter_bearer_token` are all `setting_type = "text"`.
> Anything you type there is sent to the LLM provider on every turn. Put secrets in the daemon's
> environment instead and reference them by name.

### 3.3 How `env_var` and `provider_env` actually work

They forward **names, never values**.

1. `resolve_settings` collects the names into `ResolvedSettings.env_vars`.
2. `kernel.rs:3874-3892` appends the `check_value` of every `api_key`/`env_var` requirement to that
   list, dedupes, and stores it as `manifest.metadata.hand_allowed_env`.
3. `agent_loop.rs:319-323` reads it back and passes it to `tool_runner::execute_tool`.
4. Only `shell_exec` consumes it (`tool_runner.rs:290`). `subprocess_sandbox::sandbox_command`
   (`subprocess_sandbox.rs:46-78`) does `env_clear()`, re-adds `SAFE_ENV_VARS`, then re-adds each
   allowed name **by reading it from the daemon's own process environment**
   (`std::env::var(var)`).

Consequences:

* A `text` setting with `env_var = "FOO"` does **not** set `FOO`. It only whitelists `FOO` for
  pass-through *if the daemon process already has it*. To actually supply the value, put it in
  `/data/secrets.env` (or the container env) and restart.
* Names merge with `exec_policy.shell_env_passthrough` via `merge_env_passthrough`
  (`subprocess_sandbox.rs:83-94`); `"*"` in either list means "forward everything".
* A hand without `shell_exec` gets **no** env forwarding at all — the list is computed and ignored.

### 3.4 Option availability badges (`check_option_available`, `registry.rs:767-791`)

`GET /api/hands/{id}` and `/settings` return `available: bool` per option:

* no `provider_env` and no `binary` → always `true` (this is why `auto` / `none` always show Ready);
* `provider_env` set → env var must be non-empty. **Special case:** `GEMINI_API_KEY` also accepts
  `GOOGLE_API_KEY` (`registry.rs:775-780`);
* `binary` set → must be found by `which_binary` on `PATH`;
* both set → both must pass.

Note `SettingStatus` (`registry.rs:45-53`) does **not** include `env_var` — the API never tells a
client which text setting maps to which env var.

---

## 4. Requirements

### 4.1 Schema (`HandRequirement`, `lib.rs:112-135`)

```toml
[[requires]]
key              = "ffmpeg"                    # required — identifier used in API output
label            = "FFmpeg must be installed"  # required — human string
requirement_type = "binary"                    # required — binary | env_var | api_key (snake_case)
check_value      = "ffmpeg"                    # required — what is actually probed
description      = "…"                         # optional
optional         = false                       # optional, default false

[requires.install]                             # optional — attaches to the preceding [[requires]]
macos          = "brew install ffmpeg"
windows        = "winget install Gyan.FFmpeg"
linux_apt      = "sudo apt install ffmpeg"
linux_dnf      = "sudo dnf install ffmpeg-free"
linux_pacman   = "sudo pacman -S ffmpeg"
pip            = "pip install yt-dlp"
signup_url     = "https://…"                   # api_key style
docs_url       = "https://…"
env_example    = "API_TOKEN=your_token_here"
manual_url     = "https://…"
estimated_time = "2-5 min"
steps          = ["step 1", "step 2"]          # ordered manual instructions
```

All `[requires.install]` fields are optional (`HandInstallInfo`, `lib.rs:83-109`); `steps` defaults
to `[]`, everything else to `None`.

### 4.2 `requirement_type` probe semantics (`check_requirement`, `registry.rs:584-612`)

| value | probe |
|---|---|
| `binary` | `which_binary(check_value)`: split `PATH` on `:` (`;` on Windows), test `dir/{name}{ext}` `is_file()` for ext ∈ `[""]` (`["", ".exe", ".cmd", ".bat"]` on Windows). **No exec-bit check.** |
| `binary` + `check_value` is `python3` or `python` | short-circuits to `check_python3_available()`: actually runs `python3 --version`, then `python --version`, and requires exit 0 **and** `"Python 3"` in stdout *or* stderr (`registry.rs:618-650`). Defeats the Windows Store shim and Python 2. |
| `binary` + `check_value == "chromium"` (only if not already on PATH) | `check_chromium_available()` (`registry.rs:659-739`): (1) `CHROME_PATH`/`CHROMIUM_PATH` pointing at an existing file; (2) PATH names `chromium`, `chromium-browser`, `google-chrome`, `google-chrome-stable`, `chrome`; (3) well-known install paths (Program Files / `/Applications` / `/usr/bin/*` / `/snap/bin/chromium`, incl. `msedge.exe`); (4) any `~/.cache/ms-playwright/chromium-*` directory. |
| `env_var` | `std::env::var(check_value)` set **and non-empty**, read from the **daemon** process. |
| `api_key` | identical to `env_var` — the two differ only in UI phrasing and in which `[requires.install]` fields make sense. |

`check_value` special-casing is **hard-coded to those exact strings**. `python3.11` or
`chromium-browser` as a `check_value` get plain PATH lookup only.

Because env vars are read from the daemon's process, adding a key to `/data/secrets.env` requires a
**daemon restart** before the requirement flips to satisfied.

### 4.3 Readiness model (`readiness`, `registry.rs:536-560`)

```
requirements_met = all(satisfied OR optional)      # optional reqs never gate
active           = any instance of this hand is Active
degraded         = active AND any requirement (incl. optional) unsatisfied
```

Exposed on `GET /api/hands`, `GET /api/hands/{id}`, `POST /api/hands/{id}/check-deps`.
`browser` is the canonical degraded case here: `python3` ✓ (non-optional), `chromium` ✗ (optional)
→ `requirements_met = true`, and `degraded = true` once activated.

### 4.4 Nothing enforces requirements

`kernel::activate_hand` (`kernel.rs:3754`) never calls `check_requirements`. Its own doc-comment —
*"Activate a hand: check requirements, create instance, spawn agent"* (`kernel.rs:3753`) — is wrong.

**Verified live:** a probe hand whose only requirement was a non-existent binary reported
`requirements_met: false` and still activated with HTTP 200 and a spawned agent. Same is true for
`clip` on this box (no ffmpeg/ffprobe/yt-dlp). Requirements are a UI affordance; the agent simply
fails at runtime when it shells out.

### 4.5 Auto-install (`POST /api/hands/{id}/install-deps`)

`routes.rs:4485-4620`. For each unsatisfied requirement it picks a command by server platform
(`linux` → `linux_apt` → `linux_dnf` → `linux_pacman` → `pip`; `macos` → `macos` → `pip`;
`windows` → `windows` → `pip`), then runs it with `sh -c` / `cmd /C`, 300 s timeout, **as the daemon
user with the daemon's full environment** (no sandbox). On Windows it appends
`--accept-source-agreements --accept-package-agreements` to `winget` commands, and treats
"already installed" / "No applicable update" / "No available upgrade" in the output as success.

Per-requirement statuses returned: `already_installed`, `skipped` (no `[requires.install]`),
`no_command`, `installed`, `error`, `timeout`.

> **Security note:** the `[requires.install]` strings of *any installed hand* are arbitrary shell
> commands that this endpoint will execute as root inside the container. Treat third-party
> `HAND.toml` files as executable code.

---

## 5. The 9 bundled hands

Common to all: `module = "builtin:chat"`, `provider = "default"`, `model = "default"`.
"Einstein set" (enforced by tests in `bundled.rs:248-304`, `426-456`) = `lead`, `collector`,
`predictor`, `researcher`, `twitter`, `trader` — each must carry `schedule_create/list/delete`,
`memory_store/recall`, and `knowledge_add_entity/add_relation/query`.

### 5.1 `clip` — Clip Hand 🎬 (`content`)
Long-form video → vertical short clips with burned-in captions, thumbnails, optional TTS voice-over,
optional Telegram/WhatsApp publishing. 8-phase pipeline driven entirely by `shell_exec`.

* **Tools:** `shell_exec file_read file_write file_list web_fetch memory_store memory_recall`
* **Requires (all non-optional, all `binary`):** `ffmpeg`, `ffprobe`, `yt-dlp`
* **Settings (9):** `stt_provider` (select: `auto` | `whisper_local`→binary `whisper` |
  `groq_whisper`→`GROQ_API_KEY` | `openai_whisper`→`OPENAI_API_KEY` | `deepgram`→`DEEPGRAM_API_KEY`),
  `tts_provider` (select: `none` | `edge_tts`→binary `edge-tts` | `openai_tts` | `elevenlabs`),
  `elevenlabs_api_key` (text, `env_var = ELEVENLABS_API_KEY`), `publish_target`
  (`local_only|telegram|whatsapp|both`), `telegram_bot_token`, `telegram_chat_id`,
  `whatsapp_token`, `whatsapp_phone_id`, `whatsapp_recipient` (all text, **no `env_var`**)
* **Agent:** `clip-hand`, max_tokens 8192, temp 0.4, max_iterations 40
* **Metrics:** `clip_hand_jobs_completed`, `_clips_generated`, `_total_duration_secs` (duration),
  `_clips_published_telegram`, `_clips_published_whatsapp`
* **On this box:** unusable as-is — `yt-dlp` is now present (pip-installed, see `youtube-pipeline.md`
  §2.2) but the container still has no `ffmpeg`, `ffprobe` or `curl`, and the prompt's
  Groq/OpenAI/Deepgram/Telegram/WhatsApp paths all shell out to `curl`. Its `ffmpeg` pipeline is also
  full of `|`/`>` one-liners, which `shell_exec` rejects in every mode (§9.9).

### 5.2 `lead` — Lead Hand 📊 (`data`)
Scheduled lead generation: discover → enrich → score → dedupe → deliver CSV/JSON/Markdown.

* **Tools:** `shell_exec file_read file_write file_list web_fetch web_search memory_store memory_recall schedule_create schedule_list schedule_delete knowledge_add_entity knowledge_add_relation knowledge_query`
* **Requires:** none
* **Settings (9):** `target_industry`(text) `target_role`(text) `company_size`
  (`any|startup|smb|enterprise`) `lead_source`(`web_search|linkedin_public|crunchbase|custom`)
  `output_format`(`csv|json|markdown_table`) `leads_per_report`(`10|25|50|100`)
  `delivery_schedule`(`daily_7am|daily_9am|weekdays_8am|weekly_monday`) `geo_focus`(text)
  `enrichment_depth`(`basic|standard|deep`)
* **Agent:** `lead-hand`, 16384, temp 0.3, max_iterations 50
* **Metrics:** `lead_hand_leads_found`, `_reports_generated`, `_last_report_date`(text), `_unique_companies`

### 5.3 `collector` — Collector Hand 🔍 (`data`)
Continuous OSINT on one target: change detection, sentiment, knowledge graph, event alerts.

* **Tools:** lead's set **plus `event_publish`**
* **Requires:** none
* **Settings (8):** `target_subject`(text) `collection_depth`(`surface|deep|exhaustive`)
  `update_frequency`(`hourly|every_6h|daily|weekly`) `focus_area`
  (`market|business|competitor|person|technology|general`) `alert_on_changes`(toggle, `true`)
  `report_format`(`markdown|json|html`) `max_sources_per_cycle`(`10|30|50|100`)
  `track_sentiment`(toggle, `false`)
* **Agent:** `collector-hand`, 16384, temp 0.3, max_iterations 60
* **Metrics:** `collector_hand_data_points`, `_entities_tracked`, `_reports_generated`, `_last_update`(text)

### 5.4 `predictor` — Predictor Hand 🔮 (`data`)
Superforecasting: signal collection, reasoning chains, calibrated confidence, Brier-style accuracy tracking.

* **Tools:** same as `lead` (no `event_publish`)
* **Requires:** none
* **Settings (8):** `prediction_domain`(`tech|finance|geopolitics|climate|general`)
  `time_horizon`(`1_week|1_month|3_months|1_year`) `data_sources`(`news|social|financial|academic|all`)
  `report_frequency`(`daily|weekly|biweekly|monthly`) `predictions_per_report`(`3|5|10|20`)
  `track_accuracy`(toggle, `true`) `confidence_threshold`(`low|medium|high`)
  `contrarian_mode`(toggle, `false`)
* **Agent:** `predictor-hand`, 16384, temp 0.5, max_iterations 60
* **Metrics:** `predictor_hand_predictions_made`, `_accuracy_pct`(percentage), `_reports_generated`, `_active_predictions`

### 5.5 `researcher` — Researcher Hand 🧪 (`productivity`)
Deep research with CRAAP source evaluation, cross-referencing, cited reports.

* **Tools:** collector's set (incl. `event_publish`)
* **Requires:** none
* **Settings (8):** `research_depth`(`quick|thorough|exhaustive`)
  `output_style`(`brief|detailed|academic|executive`) `source_verification`(toggle, `true`)
  `max_sources`(`10|30|50|unlimited`) `auto_follow_up`(toggle, `true`)
  `save_research_log`(toggle, `false`) `citation_style`(`inline_url|footnotes|academic_apa|numbered`)
  `language`(`english|spanish|french|german|chinese|japanese|auto`)
* **Agent:** `researcher-hand`, 16384, temp 0.3, max_iterations 25, **`heartbeat_interval_secs = 120`**
  (the only hand that overrides it)
* **Metrics:** `researcher_hand_queries_solved`, `_sources_cited`, `_reports_generated`, `_active_investigations`

### 5.6 `twitter` — Twitter Hand 𝕏 (`communication`)
Content creation, scheduling, posting, engagement, performance tracking via Twitter API v2.

* **Tools:** collector's set (incl. `event_publish`)
* **Requires:** `TWITTER_BEARER_TOKEN` (`api_key`, non-optional) with a full 6-step
  `[requires.install]` guide (`signup_url`, `docs_url`, `env_example`, `estimated_time`)
* **Settings (11):** `twitter_bearer_token`(text — **no `env_var`; see gotcha below**)
  `twitter_style`(6 options) `post_frequency`(`1_daily|3_daily|5_daily|hourly`)
  `auto_reply`(toggle,false) `auto_like`(toggle,false) `content_topics`(text) `brand_voice`(text)
  `thread_mode`(toggle,true) `content_queue_size`(`5|10|20|50`)
  `engagement_hours`(`business_hours|waking_hours|all_day`) `approval_mode`(toggle,**true**)
* **Agent:** `twitter-hand`, 16384, temp 0.7, max_iterations 50
* **Metrics:** `twitter_hand_tweets_posted`, `_replies_sent`, `_queue_size`, `_engagement_rate`(percentage)
* **Gotcha:** the `twitter_bearer_token` text setting has **no `env_var`**, so typing the token there
  only pastes it into the system prompt — it never becomes `$TWITTER_BEARER_TOKEN` for `shell_exec`.
  The requirement's `check_value` *does* whitelist that name, so the real fix is to set
  `TWITTER_BEARER_TOKEN` in the daemon environment and leave the text field empty.

### 5.7 `browser` — Browser Hand 🌐 (`productivity`)
Playwright-driven web automation with a mandatory purchase-approval gate.

* **Tools:** `browser_navigate browser_click browser_type browser_screenshot browser_read_page browser_close web_search web_fetch memory_store memory_recall knowledge_add_entity knowledge_add_relation knowledge_query schedule_create schedule_list schedule_delete file_write file_read` — **no `shell_exec`**, so this hand gets no Full exec policy and no env pass-through.
* **Requires:** `python3` (`binary`, non-optional, exec-probed) and `chromium`
  (`binary`, **`optional = true`**, multi-strategy probe)
* **Settings (5):** `headless`(toggle, `true`) `approval_mode`(toggle, `true`)
  `max_pages_per_task`(`10|20|50`) `default_wait`(`auto|1|3`) `screenshot_on_action`(toggle,`false`)
* **Agent:** `browser-hand`, 16384, temp 0.3, max_iterations 60
* **Metrics:** `browser_hand_pages_visited`, `_tasks_completed`, `_screenshots_taken`
* **Extra endpoint:** `GET /api/hands/instances/{id}/browser` returns the live page URL/title/
  content (truncated to 2000 chars) plus a base64 screenshot, or `{"active": false}` when no
  browser session exists (`routes.rs:5066-5140`).

### 5.8 `trader` — Trading Hand 📈 (`data`)
Multi-signal market analysis, adversarial bull/bear reasoning, risk management, portfolio analytics.
Largest prompt of the set (~19.9 kB) plus a ~937-line `SKILL.md`.

* **Tools:** collector's set (incl. `event_publish`)
* **Requires:** none (deliberately — paper/analysis modes need nothing)
* **Settings (12):** `trading_mode`(`analysis|paper|live`, default `paper`)
  `market_focus`(`us_stocks|crypto|multi_asset`) `strategy_style`(`scalping|day|swing|position`)
  `risk_per_trade`(`1|2|3|5`) `max_daily_loss`(`2|5|10`) `analysis_depth`(`quick|standard|deep`)
  `scan_schedule`(`15m|1h|4h|daily`) `watchlist`(text, default `SPY,QQQ,AAPL,MSFT,NVDA,BTC,ETH`)
  `initial_capital`(text, default `10000`) `alpaca_api_key`(text, `env_var = ALPACA_API_KEY`)
  `alpaca_secret_key`(text, `env_var = ALPACA_SECRET_KEY`) `approval_mode`(toggle, `true`)
* **Agent:** `trader-hand`, 16384, temp 0.3, max_iterations 80
* **Metrics (10):** `trader_hand_portfolio_value`(text) `_total_pnl`(text) `_win_rate`(percentage)
  `_sharpe_ratio` `_max_drawdown`(percentage) `_trades_count` `_active_positions`
  `_signals_generated` `_accuracy_pct`(percentage) `_last_scan`(text)

### 5.9 `infisical-sync` — Infisical Sync Hand 🔐 (`security`)
Two-way secret sync between a self-hosted Infisical instance and the local credential vault.

* **Tools:** `schedule_create schedule_list schedule_delete memory_store memory_recall knowledge_add_entity knowledge_add_relation knowledge_query event_publish shell_exec file_read file_write vault_set vault_get vault_list vault_delete`
* **Requires (all `env_var`, all non-optional):** `INFISICAL_URL`, `INFISICAL_CLIENT_ID`,
  `INFISICAL_CLIENT_SECRET` — all three are auto-added to `hand_allowed_env`, so `shell_exec` sees
  them if the daemon has them.
* **Settings (4):** `sync_interval_minutes`(`5|15|30|60`) `environment`(`prod|staging|dev`)
  `push_on_vault_write`(toggle, `true`) `delete_orphans`(toggle, `false`)
* **Agent:** `infisical-sync-hand`, 8192, **temp 0.1**, max_iterations 40
* **Metrics (6):** `infisical_sync_secrets_count`, `_last_sync`(text), `_last_error`(text),
  `_projects_count`, `_push_count`, `_pull_count`

---

## 6. Lifecycle

### 6.1 Load (daemon boot, `kernel.rs:897-918`)

1. `HandRegistry::new()` → `load_bundled()` — parses the 9 `include_str!`-embedded manifests
   (`bundled.rs:6-54`). Logs `Loaded bundled hand hand=… sha256=…` per hand.
2. `load_workspace_hands(config.home_dir.join("hands"))` (`registry.rs:191-237`) — scans
   **`$OPENFANG_HOME/hands/*/HAND.toml`** (+ optional sibling `SKILL.md`). Non-directories and
   directories without `HAND.toml` are skipped silently; bad manifests are logged
   (`Invalid HAND.toml, skipping`) and skipped. **A missing dir is `Ok(0)`, not an error.**
   Verified live: `/data/hands/zz-disk/HAND.toml` produced
   `Loaded workspace hand hand=zz-disk path=/data/hands/zz-disk sha256=…`.
   The **directory name is irrelevant** — the registry key is the `id` inside the TOML, and a
   workspace hand with the same `id` as a bundled hand overwrites it (plain `DashMap::insert`).
3. Every successful load fires the audit callback with `(hand_id, sha256_hex(HAND.toml))`
   (`registry.rs:92-102`, issue #1172).
4. Later, `start_background_agents()` restores instances from `hand_state.json`
   (`kernel.rs:4391-4444`).

### 6.2 Activate (`kernel.rs:3754-4015`)

`POST /api/hands/{id}/activate` `{"config": {...}, "instance_name": "…"}` (both optional) →

1. `registry.activate()` — rejects only if an instance with the **same `(hand_id, instance_name)`**
   is already `Active` (`registry.rs:362-375`). Two `None` names collide; two distinct names do not.
2. Build the `AgentManifest` (provider/model `"default"` resolution, tools, tags, autonomy,
   exec policy).
3. Append the `## User Configuration` block, compute `hand_allowed_env`, append `## Reference
   Knowledge`.
4. If an agent with the target name already exists: snapshot its triggers and cron jobs, `kill_agent`
   it, respawn, then restore triggers (#519) and cron jobs.
5. Spawn with a **deterministic** agent UUID: `AgentId::from_string(hand_id)` for unnamed instances,
   `AgentId::from_string("hand_instance_<instance_uuid>")` for named ones — the two calls are at `kernel.rs:4004` (unnamed) and `:4002` (named).
   Unnamed `clip` therefore always gets the same agent UUID across restarts.
6. `set_agent(instance_id, agent_id)` and `persist_hand_state()`.
7. The API layer additionally starts the background loop when the schedule is non-Reactive
   (`routes.rs:4808-4830`) — a raw `kernel.activate_hand()` call does not.

Agent name: `instance_name` when given, else `agent.name` from the TOML
(`kernel.rs:3798-3800`) — so `--name probe-a` produces an agent literally named `probe-a`.

### 6.3 Pause / resume / deactivate

* `pause` / `resume` (`registry.rs:395-414`) flip `HandInstance.status` **only**. The agent keeps
  running; nothing checks `Paused` in the agent loop. Neither call persists state, so a paused hand
  comes back **Active** after a restart — and a hand paused at shutdown is dropped from
  `hand_state.json` entirely (`persist_state` filters `status == Active`, `registry.rs:109`).
* `deactivate` (`kernel.rs:4017-4043`) removes the instance from the map and kills the linked agent.
  If `agent_id` was never set it falls back to killing every agent tagged `hand:<id>`.
  Then `persist_hand_state()`.

  > **Deactivating a hand PERMANENTLY DELETES all of its cron jobs.** `deactivate_hand` →
  > `kill_agent` → `remove_agent_jobs` + persist (`kernel.rs:3728-3735`). Watched live:
  > `/data/cron_jobs.json` became `[]`. The snapshot/restore logic for cron jobs and triggers exists
  > **only in the activation path** (`kernel.rs:3915-3931`), so restarts and re-activations are safe
  > — an explicit `deactivate` is not. **To update a hand, copy the new `HAND.toml` into
  > `/data/hands/<id>/` and `docker restart` — never deactivate/reactivate.**

### 6.4 State on disk

`$OPENFANG_HOME/hand_state.json` (`kernel.rs:4046-4052`), rewritten on every activate/deactivate,
containing **only** `hand_id`, `config`, `agent_id` for `Active` instances:

```json
[{"agent_id":"3e0981be-…","config":{"mode":"b"},"hand_id":"zz-probe"}]
```

`instance_name` and `status` are **not** stored. On boot each entry is replayed as
`activate_hand(hand_id, config, None)`; the old `agent_id` is used only to reassign cron jobs
(#402) and triggers (#519) to the newly spawned agent.

There is **no** per-hand definition persistence for API-installed hands — see [§9.2](#92-gotcha-hand-install-does-not-persist).

---

## 7. API surface

Routes registered in `crates/openfang-api/src/server.rs:435-484`.

| Method | Path | Auth | Notes |
|---|---|:--:|---|
| GET | `/api/hands` | **public** | marketplace list + readiness + `requirements[]` |
| GET | `/api/hands/active` | **public** | instances |
| GET | `/api/hands/{id}` | **public** | full definition, per-requirement `install` blocks, `server_platform`, resolved agent provider/model, dashboard schema, settings with availability |
| GET | `/api/hands/{id}/settings` | **public** | schema + **`current_values` of the active instance** |
| PUT | `/api/hands/{id}/settings` | key | body = flat `{"key":"value"}` map (replaces the whole config) |
| POST | `/api/hands/install` | key | `{"toml_content": "...", "skill_content": "..."}`; 400 if the id already exists |
| POST | `/api/hands/upsert` | key | same body; overwrites an existing id |
| POST | `/api/hands/{id}/activate` | key | `{"config": {...}, "instance_name": "..."}`, both optional |
| POST | `/api/hands/{id}/check-deps` | key | re-probe requirements |
| POST | `/api/hands/{id}/install-deps` | key | run platform install commands ([§4.5](#45-auto-install-post-apihandsidinstall-deps)) |
| POST | `/api/hands/instances/{uuid}/pause` | key | |
| POST | `/api/hands/instances/{uuid}/resume` | key | |
| DELETE | `/api/hands/instances/{uuid}` | key | deactivate + kill agent |
| GET | `/api/hands/instances/{uuid}/stats` | **public** | dashboard metric values |
| GET | `/api/hands/instances/{uuid}/browser` | **public** | live page + screenshot |

**There is no delete/uninstall route for a hand *definition*.**

Auth: the three hands rules are `middleware.rs:125-127` (`:124` is `/api/channels`) — `/api/hands`,
`/api/hands/active`, and at **`:127`** the wildcard **`path.starts_with("/api/hands/") && is_get`**.
That last rule makes every hands GET unauthenticated,
including `/settings` — which returns `current_values`, i.e. anything typed into a text setting
(Alpaca keys, Telegram bot tokens, …). On a non-loopback bind that is a credential disclosure.

Working example (host):

```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/hands | jq '.hands[].id'
curl -s -X POST -H "Authorization: Bearer $K" -H 'content-type: application/json' \
     -d '{"config":{"research_depth":"exhaustive"},"instance_name":"deep"}' \
     http://127.0.0.1:4200/api/hands/researcher/activate
curl -s -X DELETE -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/hands/instances/<uuid>
```

> Note the ` = ` in `grep -m1 '^api_key = '` — the space before `=` is what excludes `api_key_env`
> under `[default_model]`. Without it the two-line capture yields a malformed header (HTTP 400,
> empty body). Full explanation: `automation-workflows-triggers-schedules.md` gotcha 46;
> `scripts/ofctl` removes the foot-gun entirely.

---

## 8. CLI and agent-facing tools

### 8.1 `openfang hand …` (`crates/openfang-cli/src/main.rs:394-461`, dispatch `:1023-1041`)

| Command | HTTP | Notes |
|---|---|---|
| `hand list` | GET `/api/hands` | table |
| `hand active` | GET `/api/hands/active` | table |
| `hand info <id>` | GET `/api/hands/{id}` | prints raw pretty JSON (`main.rs:4530`) |
| `hand install <path>` | POST `/api/hands/install` | reads `<path>/HAND.toml` + `<path>/SKILL.md` and posts the **content**; the path itself is never persisted |
| `hand activate <id> [-n\|--name NAME]` | POST activate | |
| `hand deactivate <id>` | GET active → DELETE instance | resolves the **first** instance whose `hand_id` matches — with multiple named instances the choice is arbitrary (`DashMap` iteration order) |
| `hand check-deps <id>` | POST check-deps | raw JSON |
| `hand install-deps <id>` | POST install-deps | |
| `hand pause <instance-uuid>` / `hand resume <instance-uuid>` | POST | takes the **instance UUID**, not the hand id |
| `hand config <id> [--get K] [--set K=V]… [--unset K]… [--list]` | GET/PUT settings | read-modify-write of the whole config map; all values written as JSON **strings** (`main.rs:4708`), which is exactly what `resolve_settings` needs |

There is **no** `hand status` subcommand despite `README.md:95`.

### 8.2 Hand tools available to agents (`tool_runner.rs:370-373`, definitions `:1128-1168`)

| Tool | Input |
|---|---|
| `hand_list` | `{}` |
| `hand_activate` | `{"hand_id": "...", "config": {...}}` — activates **unnamed** only (`kernel.rs:7524-7541` passes `None`) |
| `hand_status` | `{"hand_id": "..."}` |
| `hand_deactivate` | `{"instance_id": "<uuid>"}` |

`KernelHandle::hand_install` exists (`kernel.rs:7506`) but **no tool is wired to it** — agents can
activate hands, not author them.

---

## 9. Gotchas (all verified on this box)

### 9.1 Gotcha: every TOML error says `missing field 'hand'`
`parse_hand_toml` tries the flat layout, discards the real error, then tries the `[hand]`-wrapped
layout and returns *that* error (`lib.rs:325-331`). Any mistake in a flat manifest — missing
`category`, `setting_type = "dropdown"`, a typo'd `[agent]` key — surfaces as:

```
TOML parse error: TOML parse error at line 1, column 1
  |
1 | id="e1"
  | ^
missing field `hand`
```

Verified with two deliberately broken manifests. Debug by wrapping your manifest in `[hand]` /
`[hand.agent]` temporarily, or by running `python3 -c "import tomllib;tomllib.load(open('HAND.toml','rb'))"`
first (that only catches syntax, not schema).

### 9.2 Gotcha: `hand install` does not persist
`HandRegistry::install_from_path` (`registry.rs:240-289`) is the function that copies a hand into
`~/.openfang/hands/<id>/` — and it is **dead code**: `grep -rn install_from_path --include=*.rs`
returns only its definition and a doc-comment. Both the CLI and the API go through
`install_from_content` (`registry.rs:292-310`), which only inserts into the in-memory `DashMap`.

Verified: installed `zz-probe` via `POST /api/hands/install`, activated it, restarted the container →

```
Loaded 9 bundled hand(s)
Restoring 1 persisted hand(s)
WARN Failed to restore hand hand=zz-probe error=Agent not found: Hand not found: zz-probe
```

…and the definition was gone from `GET /api/hands`, while the spawned agent survived in the agent
registry as an orphan. Issue #984 is marked closed but only the *read* half shipped.

Two further mismatches in that dead path: it writes to `dirs::home_dir()/.openfang/hands`
(`/root/.openfang/hands` here) while the loader reads `config.home_dir/hands` (`/data/hands`), so
even if it ran, the file would land where nothing looks.

**Workaround (the supported way to install a custom hand):**

```bash
mkdir -p /var/lib/docker/volumes/openfang_openfang-data/_data/hands/my-hand
cp HAND.toml SKILL.md /var/lib/docker/volumes/openfang_openfang-data/_data/hands/my-hand/
docker restart openfang-openfang-1
```

Use `POST /api/hands/upsert` for a hot reload of the same content within the running daemon (it
overwrites an existing id; active instances keep the *old* definition until deactivate+reactivate).

### 9.3 Gotcha: requirements never block anything
See [§4.4](#44-nothing-enforces-requirements). `clip` activates happily on a box with no `ffmpeg`.

### 9.4 Gotcha: non-string config values silently revert to the default
`resolve_settings` uses `v.as_str()`. `{"flag": true}` → the toggle renders `Disabled`. Always send
`"true"`/`"false"`, `"20"`, etc.

### 9.5 Gotcha: text settings are prompt text, not environment
Typing a secret into a text setting puts it verbatim in the system prompt (verified) and does **not**
export it. `env_var` only whitelists a name for pass-through from the *daemon's* environment.
The dashboard renders text settings as a plain `<input type="text">` — no masking
(`index_body.html:3277-3279`).

### 9.6 Gotcha: named instances collapse into one on restart
`persist_state` drops `instance_name` (`registry.rs:105-123`) and the restore loop passes `None`
(`kernel.rs:4396`). Verified: two active named instances (`probe-a`, `probe-b`) of the same hand →
after restart, one unnamed instance plus

```
WARN Failed to restore hand hand=zz-disk error=Internal error: Hand already active: zz-disk
```

The `probe-a` / `probe-b` agents themselves survive in the agent registry, now orphaned (no hand
instance points at them). Clean them with `DELETE /api/agents/<id>`.

### 9.7 Gotcha: settings changes are not persisted and do not reach a running agent
`PUT /api/hands/{id}/settings` → `registry.update_config` only (`routes.rs:4948-4987`). It never
calls `persist_hand_state`, and it never rebuilds the agent's system prompt. Verified: after a PUT,
`hand_state.json` still showed `"config": {}`.

**Do not "just deactivate and re-activate"** — that is the one path that permanently deletes the
hand's cron jobs ([§6.3](#63-pause--resume--deactivate)). Use one of these instead:

* **No cron jobs attached** → deactivate, then re-activate with the config in the `/activate` body.
* **Cron jobs attached (the usual case)** → edit `config` inside `$OPENFANG_HOME/hand_state.json` and
  `docker restart`. The boot path replays `activate_hand(&hand_id, config, None)` verbatim from that
  file (`kernel.rs:4391-4397`), and because an unnamed instance's agent id is
  `uuid5(NAMESPACE_DNS, hand_id)` the agent UUID does not change, so cron jobs and triggers still
  point at it (the reassignment branch at `kernel.rs:4406-4438` is a no-op). Confirm with
  `docker logs … | grep 'Hand restored'`.

It also targets `list_instances().find(hand_id == …)` — the **first** match in `DashMap` order, so
with multiple named instances you cannot control which one you edit.

### 9.8 Gotcha: `provider`/`model` default to Anthropic, not to your configured LLM
Omitting them yields `anthropic` / `claude-sonnet-4-20250514` (`lib.rs:306-311`), verified live.
Write `provider = "default"` and `model = "default"`.

### 9.9 Gotcha: `shell_exec` in a hand means an un-allowlisted shell — but still not a real shell
`kernel.rs:3912-3921` grants `ExecSecurityMode::Full` whenever the tool list contains `shell_exec`,
which routes commands through `sh -c` (instead of `shlex` argv-splitting) and skips both
`validate_command_allowlist` and the taint heuristics. **The metacharacter denylist still applies** —
`contains_shell_metacharacters` runs first, at `tool_runner.rs:249`, in every mode. So a hand can run
any binary, but never with `|`, `>`, `<`, `;`, `&`, `{}`, `` ` ``, `$(`, `${`, a newline or a NUL.
**Eight of the nine** bundled hands declare `shell_exec` — all but `browser`.

### 9.10 Gotcha: every hands GET is unauthenticated
Including `/api/hands/{id}/settings`, which echoes `current_values` (potential secrets). See
[§7](#7-api-surface).

### 9.11 Gotcha (this deployment): the in-container CLI sends no API key
`read_api_key()` (`main.rs:1621-1642`) is supposed to read `api_key` from
`$OPENFANG_HOME/config.toml` first, env `OPENFANG_API_KEY` second. On this box the config-file path
does not work: every mutating `openfang hand …` fails with
`Missing Authorization: Bearer <api_key> header`, while `openfang config get api_key` prints the key
and GETs succeed (they are public). Reproduced with a copied config in a fresh `OPENFANG_HOME`, and
proved to be the env path that works: with `OPENFANG_API_KEY=<wrong>` the daemon answers
`Invalid API key`, i.e. the file value was never used.

**Workaround:**
```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)
docker exec -e OPENFANG_API_KEY="$K" openfang-openfang-1 openfang hand activate researcher -n deep
```

### 9.12 Smaller traps
* Editing `/opt/openfang/crates/openfang-hands/bundled/*/HAND.toml` changes nothing at runtime —
  those files are `include_str!`-embedded at compile time. Override by dropping a same-`id` hand in
  `/data/hands/`.
* `SKILL.md` YAML frontmatter is **not** parsed for hands. The whole file, `---` fences included, is
  pasted into the prompt.
* `[requires.install]` must immediately follow its `[[requires]]` entry — it binds to the last array
  element. Three consecutive `[[requires]]`+`[requires.install]` pairs (as in `clip`) are correct
  TOML; a stray table between them silently reattaches.
* A hand without `max_iterations` is **Reactive** — it spawns and then sits idle forever unless you
  message it. Autonomy requires `max_iterations`.
* `Continuous` hands poll every **3600 s** by default (`kernel.rs:3838`, deliberate, issue #848), so
  "starts working immediately" means "one pass now, next pass in an hour" unless the hand schedules
  its own cron via `schedule_create`.
* `hand pause`/`resume` take an **instance UUID**; `hand deactivate` takes a **hand id**.
* Deactivating removes the instance but the **agent's persisted record may linger** if it was
  spawned under a different name (e.g. after a named→unnamed restart collapse). Check
  `GET /api/agents` for stragglers.
* `list_instances()` is unsorted (`DashMap`); never rely on ordering.
* **A hand agent is hard-capped at 20 messages of conversation history.**
  `DEFAULT_MAX_HISTORY_MESSAGES = 20` (`openfang-types/src/agent.rs:523`), applied at
  `agent_loop.rs:462-470`. Observed live: `Trimming old messages … total_messages=27 trimming=7
  max_history=20`. `AgentManifest::max_history_messages` exists, but there is **no `HAND.toml` field
  and no API route** to set it — two tool calls burn two messages, so a run has roughly 10 tool calls
  of visible memory. Design long pipelines around a state file, not around the conversation.
* **The workspace is seeded with `BOOTSTRAP.md`** telling the agent to greet the user and ask their
  name on the first conversation — the very first turn will do zero work. Overwrite it after
  activation, and again after every re-activation (the scaffold is re-seeded).
* **`hand install-deps` picks `linux_apt > linux_dnf > linux_pacman > pip` on Linux**
  (`routes.rs:4532-4541`), and `sudo` does not exist in the container, so any `linux_apt = "sudo apt
  install …"` returns exit 127. Omit `linux_apt` from your own `[requires.install]` so `pip` wins.

---

## 10. Complete worked template for a custom hand

Directory layout (the directory name is cosmetic; `id` is what matters):

```
/data/hands/watchdog/
├── HAND.toml
└── SKILL.md          # optional
```

### 10.1 `HAND.toml`

```toml
# ── Identity ────────────────────────────────────────────────────────────────
id          = "watchdog"                 # registry key, CLI arg, URL segment
name        = "Watchdog Hand"
description = "Polls a set of HTTP endpoints and reports regressions"
category    = "productivity"             # content|security|productivity|development|
                                         # communication|data|finance|other  (unknown → other)
icon        = "🐕"

# Tool allowlist. Non-empty ⇒ ToolProfile::Custom (no profile expansion).
# Declaring shell_exec ALSO grants ExecSecurityMode::Full + 300s timeout.
tools = [
  "shell_exec", "file_read", "file_write",
  "web_fetch", "web_search",
  "memory_store", "memory_recall",
  "schedule_create", "schedule_list", "schedule_delete",
  "event_publish",
]

# Optional allowlists — empty/omitted means "all".
skills      = []
mcp_servers = []

# ── Requirements (ADVISORY ONLY — they never block activation) ──────────────
# Requires python3, NOT curl: curl is absent from this container and cannot be apt-installed
# (§4.5, §9.12 — `install-deps` prefers linux_apt and `sudo` exits 127). The probe is therefore a
# script file, which is also the only way to get a `{`/`}` format string past the metacharacter
# denylist (§9.9). No [requires.install] block: python3 is already in the image, and declaring
# linux_apt is the exact mistake §4.5 tells you to avoid.
[[requires]]
key              = "python3"
label            = "python3 must be installed"
requirement_type = "binary"              # binary | env_var | api_key
check_value      = "python3"             # PATH lookup (python3/python and chromium are special-cased)
description      = "Runs bin/probe.py, which measures status and latency per target."
optional         = false                 # true ⇒ excluded from requirements_met, still marks degraded

[[requires]]
key              = "WATCHDOG_WEBHOOK"
label            = "Alert webhook URL"
requirement_type = "env_var"             # read from the DAEMON's env; restart after changing
check_value      = "WATCHDOG_WEBHOOK"    # auto-added to hand_allowed_env → visible to shell_exec
description      = "Slack/Discord webhook that alerts are POSTed to."
optional         = true

[requires.install]
docs_url    = "https://api.slack.com/messaging/webhooks"
env_example = "WATCHDOG_WEBHOOK=https://hooks.slack.com/services/XXX"
steps = [
  "Create an incoming webhook in your Slack workspace",
  "Add WATCHDOG_WEBHOOK=... to /data/secrets.env",
  "Restart the daemon so the env var is visible",
]

# ── Settings (shown in the activation wizard; injected as ## User Configuration) ──
[[settings]]
key          = "targets"
label        = "Target URLs"
description  = "Comma-separated list of endpoints to poll."
setting_type = "text"                    # select | text | toggle
default      = "https://example.com/health"
# env_var = "..."   # would whitelist a NAME for shell_exec — it does NOT set the value

[[settings]]
key          = "interval"
label        = "Poll Interval"
setting_type = "select"
default      = "15m"

[[settings.options]]
value = "5m"
label = "Every 5 minutes"

[[settings.options]]
value = "15m"
label = "Every 15 minutes"

[[settings.options]]
value = "1h"
label = "Hourly"

[[settings]]
key          = "llm_summary"
label        = "LLM Summaries"
setting_type = "select"
default      = "off"

[[settings.options]]
value = "off"
label = "Disabled"

[[settings.options]]
value = "groq"
label = "Groq"
provider_env = "GROQ_API_KEY"            # "Ready" badge + grants the NAME to shell_exec when chosen

[[settings.options]]
value = "local"
label = "Local llama.cpp"
binary = "llama-cli"                     # "Ready" badge if the binary is on PATH

[[settings]]
key          = "alert_on_slow"
label        = "Alert on Slow Responses"
setting_type = "toggle"
default      = "true"                    # STRING "true"/"1" ⇒ Enabled; anything else ⇒ Disabled

# ── Agent ───────────────────────────────────────────────────────────────────
[agent]
name        = "watchdog-hand"            # agent name for unnamed instances (kills a same-named agent)
description = "Endpoint watchdog"
module      = "builtin:chat"
provider    = "default"                  # MUST be "default" to inherit the configured LLM
model       = "default"                  # MUST be "default" (else claude-sonnet-4-20250514)
max_tokens  = 8192
temperature = 0.2
max_iterations = 30                      # presence ⇒ autonomous + ScheduleMode::Continuous(3600s)
heartbeat_interval_secs = 120            # override the 30s kernel default for long LLM calls
system_prompt = """You are Watchdog Hand.

Check the **User Configuration** block below for your target list, poll interval and alerting
preferences; those values are authoritative and override anything in this prompt.

Each cycle:
1. Probe every target with `python3 bin/probe.py <url>` and `timeout_seconds: 60`. The helper
   prints one line of JSON per target: {status, time_total, size}. There is no shell here:
   `|`, `>`, `<`, `;`, `&`, backtick, `$(`, `${`, `{`, `}`, newline and NUL are all rejected before
   execution in every mode, which is why the probe is a script and not a curl one-liner.
2. Compare against the previous cycle recalled via `memory_recall`.
3. On a regression (non-2xx, or >2x slower when 'Alert on Slow Responses' is Enabled),
   send the alert with `python3 bin/alert.py "<message>"` — it reads WATCHDOG_WEBHOOK from the
   environment, so the URL never appears in an argv or a log line — and `event_publish` a
   `watchdog.regression` event.
4. Persist counters with `memory_store` using EXACTLY these keys:
   `watchdog_checks_total`, `watchdog_failures_total`, `watchdog_last_run`.
5. Use `schedule_create` once to install a recurring job matching the configured interval.

Never invent command output. Report real exit codes and real errors.
"""

# ── Dashboard ───────────────────────────────────────────────────────────────
[dashboard]
[[dashboard.metrics]]
label      = "Checks Run"
memory_key = "watchdog_checks_total"     # must match the memory_store key in the prompt
format     = "number"                    # free-form label: number|duration|text|percentage|…

[[dashboard.metrics]]
label      = "Failures"
memory_key = "watchdog_failures_total"
format     = "number"

[[dashboard.metrics]]
label      = "Last Run"
memory_key = "watchdog_last_run"
format     = "text"
```

### 10.2 `SKILL.md` (optional)

Injected verbatim under `## Reference Knowledge`. Keep it factual reference material — API shapes,
command recipes, heuristics — not instructions that duplicate the system prompt. Frontmatter is
decorative here.

```markdown
---
name: watchdog-skill
version: "1.0.0"
runtime: prompt_only
---

# Watchdog Reference

## probe recipe (`bin/probe.py`)
There is no `curl` in this container, and a `curl -w '%{http_code}…'` one-liner would be rejected
before execution anyway — `{` and `}` trip `contains_shell_metacharacters` in every mode (§9.9).
Ship the probe as a file and call it with flat argv:

```python
# bin/probe.py — python3 stdlib only
import json, sys, time, urllib.request
for url in sys.argv[1:]:
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            body = r.read()
            out = {"url": url, "status": r.status, "size": len(body)}
    except urllib.error.HTTPError as e:
        out = {"url": url, "status": e.code, "size": 0}
    except Exception as e:
        out = {"url": url, "status": 0, "error": str(e)}
    out["time_total"] = round(time.monotonic() - t0, 3)
    print(json.dumps(out))
```

Invoke as `python3 bin/probe.py https://a/health https://b/health` — one flat argv, no shell.

## Regression heuristics
- Any 5xx → immediate alert.
- 4xx → alert only if it persists for two consecutive cycles.
- Latency: alert when `time_total > 2 × trailing_median(last 10)`.
```

### 10.3 Install it so the running instance picks it up

The bundled script does lint + copy + restart + proof-of-load in one step:

```bash
ofhand install ./watchdog        # add --dry-run to see the plan, --no-restart to stage only
ofhand activate watchdog targets="https://a/health,https://b/health" interval=5m \
    llm_summary=off alert_on_slow=true
```

By hand, note that `HD` is the *hand* directory — do not reuse the `$D` you bound to `OPENFANG_HOME`
in another block, or the two files land next to `config.toml` where the registry never looks, with no
error at the next restart:

```bash
HD=/var/lib/docker/volumes/openfang_openfang-data/_data/hands/watchdog   # == /data/hands/watchdog
mkdir -p "$HD"
cp HAND.toml SKILL.md "$HD"/
docker restart openfang-openfang-1
sleep 12
docker logs --tail 200 openfang-openfang-1 2>&1 | grep -i "workspace hand"
#   INFO openfang_hands::registry: Loaded workspace hand hand=watchdog path=/data/hands/watchdog sha256=…
#   INFO openfang_kernel::kernel: Loaded 1 workspace hand(s) from /data/hands
```

Then:

```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)
curl -s http://127.0.0.1:4200/api/hands/watchdog | python3 -m json.tool          # GET is public
curl -s -X POST -H "Authorization: Bearer $K" -H 'content-type: application/json' \
  -d '{"config":{"targets":"https://a/health,https://b/health","interval":"5m","llm_summary":"off","alert_on_slow":"true"}}' \
  http://127.0.0.1:4200/api/hands/watchdog/activate
```

**Hot-reload without a restart** (in-memory only; re-add to `/data/hands/` to make it survive):

```bash
python3 -c "import json;print(json.dumps({'toml_content':open('HAND.toml').read(),'skill_content':open('SKILL.md').read()}))" > /tmp/p.json
curl -s -X POST -H "Authorization: Bearer $K" -H 'content-type: application/json' \
     --data @/tmp/p.json http://127.0.0.1:4200/api/hands/upsert
# then deactivate + reactivate any running instance to pick up the new definition
```

**Removal:** delete `/data/hands/watchdog` and restart (there is no uninstall endpoint). For an
`upsert`-only definition, a plain restart is enough.

### 10.4 Authoring checklist

1. `provider = "default"`, `model = "default"`.
2. `max_iterations` present iff you want the hand to run on its own.
3. Every `[[dashboard.metrics]].memory_key` is named literally in the system prompt next to a
   `memory_store` instruction.
4. Secrets go in the daemon environment + an `env_var`/`api_key` `[[requires]]` entry — never in a
   `text` setting.
5. Config values sent to the API are JSON **strings**.
6. `shell_exec` in `tools` = full shell. Omit it if the hand does not need one.
7. Validate before shipping: `python3 -c "import tomllib;tomllib.load(open('HAND.toml','rb'))"`,
   then `POST /api/hands/upsert` and read the error (remember [§9.1](#91-gotcha-every-toml-error-says-missing-field-hand)).

---

## 11. Where the docs lie

| Claim | Reality |
|---|---|
| `README.md:78` "The 7 Bundled Hands" | 9 (`bundled.rs:6-54`, asserted in `bundled.rs:82-84`) |
| `README.md:95` `openfang hand status researcher` | no such subcommand (`main.rs:394-461`) |
| `README.md:107` "Publish to FangHub" | no such mechanism in the tree |
| `README.md:192,243` "7 autonomous Hands" | 9 |
| `kernel.rs:3753` doc-comment "check requirements" | `activate_hand` never checks them |
| `registry.rs:262-267` "On next restart, `load_workspace_hands` will pick it up from disk" | true only for the dead `install_from_path`; the shipped install path never writes to disk |
| Issue #984 (closed) "custom hands lost on daemon restart" | still reproducible via CLI/API install |

---

## 12. Open questions

* Root cause of [§9.11](#911-gotcha-this-deployment-the-in-container-cli-sends-no-api-key): the
  v0.6.9 source reads `api_key` from `$OPENFANG_HOME/config.toml` in `read_api_key()`
  (`main.rs:1621-1642`, via `cli_openfang_home()` at `main.rs:870`, which honours `OPENFANG_HOME`),
  but the deployed `/usr/local/bin/openfang` demonstrably does not. Re-verified 2026-08-10 at the
  wire, with a header-logging listener on `127.0.0.1:4299` inside the container and a scratch
  `OPENFANG_HOME` holding a matching `daemon.json`:
  - full copy of `/data/config.toml` → `POST /api/hands/clip/check-deps` carried only
    `accept`, `accept-encoding`, `host`. **No `authorization`.**
  - a one-line `config.toml` containing nothing but `api_key = "testkey12345"` → same, no header.
  - a copy at `$HOME/.openfang/config.toml` as well → still no header (so it is not a
    home-resolution mismatch).
  - `-e OPENFANG_API_KEY=<key>` → `authorization: <present, 58 bytes>` and the call succeeds.

  Ruled out: binary/source drift (`git status` on `/opt/openfang` is clean apart from
  `docker-compose.override.yml`; the binary self-reports `openfang 0.6.9`), file permissions
  (`docker exec` runs as uid 0 and `openfang config get api_key` prints the value), and a second
  shadowing `read_api_key` (only one definition exists in the crate). The file branch simply never
  produces a header. **Operationally it does not matter — always export `OPENFANG_API_KEY` — but do
  not "fix" a CLI 401 by editing `config.toml`.**
* No mechanism exists to remove a hand *definition* at runtime — is a `DELETE /api/hands/{id}`
  intended, or are workspace hands the only supported lifecycle?
* `HandStatus::Paused` is stored but nothing in the agent loop honours it; whether pause is meant to
  gate scheduling is unclear from the code.
