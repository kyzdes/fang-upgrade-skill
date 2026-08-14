# OpenFang Runtime Architecture (verified against v0.6.9 source)

Ground truth is the code at `/opt/openfang` tag `v0.6.9`. `docs/architecture.md` and
`docs/configuration.md` in that checkout are aspirational in places — every place they
diverge from code is called out explicitly below with file:line, and several were
confirmed live against the running container (`openfang-openfang-1`, API on
`127.0.0.1:4200`, image also `0.6.9`).

## Table of contents

- [Crate map (dependency flows downward)](#crate-map-dependency-flows-downward)
- [Kernel boot sequence — `OpenFangKernel::boot_with_config()`](#kernel-boot-sequence--openfangkernelboot_with_config)
- [Agent restore on boot — much richer than the docs one-liner](#agent-restore-on-boot--much-richer-than-the-docs-one-liner)
- [Agent lifecycle](#agent-lifecycle)
- [Heartbeat monitor — `crates/openfang-kernel/src/heartbeat.rs`](#heartbeat-monitor--cratesopenfang-kernelsrcheartbeatrs)
- [Agent Loop — `crates/openfang-runtime/src/agent_loop.rs` (5,493 lines)](#agent-loop--cratesopenfang-runtimesrcagent_looprs-5493-lines)
- [Session compaction — thresholds in docs are also wrong](#session-compaction--thresholds-in-docs-are-also-wrong)
- [Session repair — `crates/openfang-runtime/src/session_repair.rs`](#session-repair--cratesopenfang-runtimesrcsession_repairrs)
- [Memory substrate — `crates/openfang-memory`](#memory-substrate--cratesopenfang-memory)
- [Kernel shutdown — agents are preserved, not deleted](#kernel-shutdown--agents-are-preserved-not-deleted)
- [Daemon detection / CLI-vs-in-process (`openfang-cli/src/main.rs`)](#daemon-detection--cli-vs-in-process-openfang-clisrcmainrs)
- [WASM sandbox — dual metering, matches docs](#wasm-sandbox--dual-metering-matches-docs)
- [API auth middleware — a real, verified gap between doc-comment and behavior](#api-auth-middleware--a-real-verified-gap-between-doc-comment-and-behavior)
- [Hot reload — `build_reload_plan` sees half the config](#hot-reload--build_reload_plan-sees-half-the-config)
- [`KernelHandle::memory_store` writes to ONE global namespace](#kernelhandlememory_store-writes-to-one-global-namespace)
- [KernelHandle trait — the inter-agent seam](#kernelhandle-trait--the-inter-agent-seam)
- [Gotchas (all verified against code and/or the live container)](#gotchas-all-verified-against-code-andor-the-live-container)

## Crate map (dependency flows downward)

```
openfang-cli  →  openfang-desktop  →  openfang-api  →  openfang-kernel
                                                              |
                          +-----------------+-----------------+-----------------+
                          |                 |                 |                 |
                  openfang-runtime   openfang-channels  openfang-wire   openfang-skills
                          |
                  openfang-memory  →  openfang-types
```

Not mentioned in `docs/architecture.md` at all but present and wired into the kernel:
- `openfang_hands` — "Hand" registry (curated autonomous agent packages), bundled +
  workspace hands loaded from `<home>/hands/<id>/`, tracked with Merkle audit entries.
  `crates/openfang-kernel/src/kernel.rs:900-920` (bundled), activation at
  `kernel.rs:3751` (`activate_hand`).
- `openfang_extensions` — credential vault (`vault.enc`) + dotenv (`.env`) resolver
  that sits in front of plain env-var API key lookup, and an `IntegrationRegistry`
  that merges installed integrations into the MCP server list.
  `crates/openfang-kernel/src/kernel.rs:651-668` (vault unlock), `:918-955` (extensions).
- A driver-chain / fallback system with **auto-detection of any configured provider**
  when the primary driver fails to init (`kernel.rs:706-724`), wrapped in
  `FallbackDriver::with_models` when more than one driver is available (`kernel.rs:757-767`).
- Additional LLM drivers beyond the "3 native drivers" the docs describe: `bedrock.rs`,
  `claude_code.rs`, `copilot.rs`, `qwen_code.rs`, `vertex.rs` all exist under
  `crates/openfang-runtime/src/drivers/`.

## Kernel boot sequence — `OpenFangKernel::boot_with_config()`

`crates/openfang-kernel/src/kernel.rs:590` (`boot()` at `:551` just loads config and
delegates). Order of operations, condensed from reading the function top to bottom:

1. Install rustls ring crypto provider (idempotent).
2. Env overrides: `OPENFANG_LISTEN` overwrites `api_listen`; `OPENFANG_API_KEY` fills
   `api_key` only if `config.toml`'s `api_key` was empty (config file wins).
3. `config.clamp_bounds()` then `config.validate()` (warnings only, not fatal).
4. `create_dir_all(data_dir)`.
5. Open SQLite (`{data_dir}/openfang.db` unless `[memory].sqlite_path` set),
   `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`, run migrations (see Schema
   section — **8 versions, not 5**).
6. Build credential resolver: vault (`<home>/vault.enc`) → dotenv (`<home>/.env`) → env var.
7. Resolve primary driver via `drivers::create_driver()`. **Primary driver failure is
   non-fatal** — kernel logs a warning and tries `drivers::detect_available_provider()`
   (scans env for any provider key) before falling back further.
8. Build `driver_chain` from primary + all `[[fallback_providers]]` that init
   successfully. If `driver_chain.len() > 1`, wrap in
   `FallbackDriver::with_models(model_chain)`. If **zero** drivers succeeded, use a
   `StubDriver` that returns a helpful error — the kernel and dashboard still boot.
9. `MeteringEngine` shares the memory substrate's SQLite connection via `memory.usage_conn()`.
10. `Supervisor`, `BackgroundExecutor`, `WasmSandbox::new()` (fuel + epoch metering).
11. RBAC `AuthManager::new(&config.users)`.
12. `ModelCatalog::new()` → `detect_auth()` (env-var presence only, never reads secret
    values) → `apply_local_env_overrides()` (OLLAMA_HOST etc., applied **before**
    `provider_urls` so explicit config wins) → `apply_url_overrides()` →
    `load_custom_models(<home>/custom_models.json)` → live Copilot model fetch if
    Copilot OAuth tokens are persisted.
13. `SkillRegistry`: bundled skills via `load_bundled()`, then user skills via
    `load_all()` (user skills override bundled ones with the same name). In
    `mode = "stable"`, registry is frozen (`skill_registry.freeze()`).
14. `HandRegistry`: bundled hands + workspace hands from `<home>/hands/`.
15. `IntegrationRegistry`: bundled + installed integrations, merged into the MCP
    server list (`all_mcp_servers`) — dedup by name so a manual `[[mcp_servers]]`
    entry with the same name wins over an installed integration.
16. `WebToolsContext` (search cascade + SSRF-protected fetch, shared `WebCache`).
17. Embedding driver auto-detect: explicit `[memory].embedding_provider` first; else
    scan `OPENAI_API_KEY → GROQ_API_KEY → MISTRAL_API_KEY → TOGETHER_API_KEY →
    FIREWORKS_API_KEY → COHERE_API_KEY` in that order; else try local
    `ollama → vllm → lmstudio`. If none work, memory recall degrades to text search.
18. Browser manager, media engine, TTS engine, pairing manager (loads paired devices
    from DB if `[pairing].enabled`).
19. Kernel struct assembled. HAND.toml load events are backfilled into the Merkle
    audit chain by hashing bundled hand TOML content (SHA-256) — `kernel.rs:1222-1253`.
20. **Restore persisted agents** from SQLite (see next section) — this is the single
    biggest area where the code is far richer than `docs/architecture.md`'s one-liner
    ("Load all agents from SQLite, re-register in memory, set state to Running").
21. Publish `KernelStarted` event.

When the daemon wraps the kernel in `Arc` (`start_background_agents`, called from
`openfang-cli`'s `start` command / `openfang-api::server::run_daemon`):
- `set_self_handle()` stores a weak `Arc` for trigger dispatch (`kernel.rs:4070`).
- Non-reactive agents' background loops are started **staggered by 500ms each**
  to avoid a rate-limit storm on shared providers (`kernel.rs:4463-4472`).
- `start_heartbeat_monitor()` is launched (`kernel.rs:4477`, function at `:4832`).
- OFP peer node starts only if `network_enabled` **and** `network.shared_secret` is
  non-empty.

## Agent restore on boot — much richer than the docs one-liner

`crates/openfang-kernel/src/kernel.rs:1260-1400`+ (inside `boot_with_config`). For
every agent loaded from SQLite via `memory.load_all_agents()`:

1. Look for `<home>/agents/<name>/agent.toml` on disk. If present and parseable,
   **diff specific fields** against the DB-persisted manifest: `name`, `description`,
   `model.system_prompt`, `model.provider`, `model.model`, `capabilities.tools`,
   `tool_allowlist`, `tool_blocklist`, `skills`, `mcp_servers`, `workspace` (only if
   TOML explicitly sets one), `schedule`, `autonomous`, `resources`, `exec_policy`.
   If any differ, the disk version wins (merged via
   `merge_disk_manifest_preserving_kernel_defaults`) and is re-persisted.
   **Gotcha**: any manifest field *not* in that list is silently ignored on restart —
   editing it in `agent.toml` has no effect until the agent is deleted and recreated
   (the code comment at `kernel.rs:~1310` calls this out explicitly).
2. Capabilities are re-granted, scheduler quota re-registered.
3. State is forced back to `Running` and **`last_active` is reset to `now()`** — this
   is deliberate so the heartbeat monitor doesn't immediately flag a just-restored
   agent as unresponsive because of a stale pre-shutdown timestamp.
4. `exec_policy` resolution: if `agent.toml` on disk does **not** explicitly set
   `[exec_policy]`, the kernel's *current* `config.exec_policy` always overwrites
   whatever was cached in the DB from a previous boot — this fixes a real bug (#1132)
   where editing `config.toml`'s `[exec_policy] mode` had no effect on already-spawned
   agents. If `agent.toml` does set it, the per-agent override is kept.
5. Hand instances are restored similarly from `hand_state.json` (visible on the live
   container's `/data/hand_state.json`).

## Agent lifecycle

### Spawn — `spawn_agent_with_parent()`, `kernel.rs:1601`

1. New `AgentId` (UUID v4) unless `fixed_id` given (used by `activate_hand` and clone).
2. `memory.create_session(agent_id)` — the returned `session_id` is used directly to
   avoid a duplicate-session bug (#651 per comment).
3. `exec_policy` inherited from kernel config if manifest doesn't set one.
4. Model/provider defaulting: if manifest's `provider`/`model` are empty or literally
   `"default"`, overlay the kernel's `default_model` (checking a **hot-reloadable
   override** first, `default_model_override: RwLock<Option<..>>`, before the boot-time
   config value).
5. Model catalog lookup: if `manifest.model.model` matches a catalog entry/alias, the
   canonical provider/model ID replaces what was on the manifest, and
   `api_key_env` is filled from `config.resolve_api_key_env(provider)` if unset.
6. Provider prefix stripped from model name (e.g. `openai/gpt-4o` → `gpt-4o` once
   provider is known).
7. `apply_budget_defaults()` overlays global `[budget]` config onto the agent's
   resource quota.
8. **Private state dir is always `<home>/workspaces/<name>/`** (name-based, not
   user-path-based) so `SOUL.md` and per-agent memory survive recreation and can never
   be redirected into an arbitrary user-supplied path (issue #1097,
   `kernel.rs:~1690`). The user-facing `workspace` defaults to the same path unless
   `agent.toml` sets `workspace = "/custom/path"` explicitly.
9. Capabilities granted (`CapabilityManager`), scheduler registered, `AgentEntry`
   built with `state = Running`, registered, parent's `children` updated if any.
10. **Persisted to SQLite immediately** (`memory.save_agent`).
11. Audit log record: `AuditAction::AgentSpawn`.
12. If `schedule = Proactive { conditions }`, triggers auto-registered for each
    condition via `background::parse_condition`.
13. `Lifecycle::Spawned` event published, triggers evaluated synchronously.

### Clone — `POST /api/agents/{template_id}/clone`, `openfang-api/src/routes.rs:9991`

Not mentioned in `docs/architecture.md` at all. Copies a template agent's manifest
into a new independent agent with `deep_merge_json` overrides applied on top. Explicit
exclusion list `MEMORY_FILES = ["MEMORY.md", "HEARTBEAT.md"]` (`routes.rs:9963`) is
never copied into the clone's workspace — the clone starts with genuinely independent
memory by design (issue #868). `new_name` is validated: ≤256 chars, non-empty, no
`/ \ \0` or control characters (keeps it safe as a workspace directory name), and must
not collide with an existing agent name.

### Kill — `kill_agent()`, `kernel.rs:3717`

Order: remove from `AgentRegistry` → stop background loops
(`BackgroundExecutor::stop_agent`) → unregister scheduler → revoke all capabilities →
unsubscribe event bus → remove triggers → **remove cron jobs** belonging to the agent
and persist the cron scheduler (`cron_removed > 0` → `cron_scheduler.persist()`, not
mentioned in docs — issue #504) → remove from SQLite (`memory.remove_agent`) → audit
log `AuditAction::AgentKill`.

### Message dispatch by `manifest.module`

`kernel.rs:1919-1922` (and duplicated at `:2005-2006` for the streaming path):
```rust
if entry.manifest.module.starts_with("wasm:") { /* WasmSandbox exec */ }
else if entry.manifest.module.starts_with("python:") { /* subprocess */ }
else { /* builtin:chat or unrecognized -> LLM agent loop */ }
```
Module path prefix is stripped with `strip_prefix("wasm:")` /
`strip_prefix("python:")` at `kernel.rs:2496` / `:2578`.

## Heartbeat monitor — `crates/openfang-kernel/src/heartbeat.rs`

Started as a `tokio::spawn` loop in `start_heartbeat_monitor()`
(`kernel.rs:4832`), ticking every `check_interval_secs` (default **30s**,
`HeartbeatConfig::default()`, `heartbeat.rs:60-70`). Per tick:

- `check_agents()` is a **pure function** (no side effects) that scans
  `Running`/`Crashed` agents and computes `inactive_secs` vs. a per-agent timeout:
  `entry.manifest.autonomous.heartbeat_interval_secs * UNRESPONSIVE_MULTIPLIER(=2)`,
  falling back to `config.heartbeat.default_timeout_secs` (default **180s**, not
  30s — chosen because "browser tasks and complex LLM calls can take 1-3 minutes",
  `heartbeat.rs:64-65`).
- **Idle-since-spawn exemption**: if `last_active - created_at <= IDLE_GRACE_SECS(=10)`,
  the agent is skipped entirely — prevents disabled/rarely-scheduled agents from
  entering an infinite crash/recover loop (issue #844).
- **Reactive agents are exempt while idle** (`should_exempt_idle_reactive_agent` —
  `ScheduleMode::Reactive` and no task currently running in
  `kernel.running_tasks`). If a reactive agent was marked `Crashed` while idle, it's
  reset straight back to `Running` with no recovery-attempt penalty.
- Agents in per-agent `quiet_hours` (`"HH:MM-HH:MM"` UTC, `is_quiet_hours()`,
  cross-midnight aware) are skipped for that tick.
- **Crash/recovery state machine** (this entire mechanism is undocumented in
  `docs/architecture.md`, which only says "publishes HealthCheckFailed events"):
  - Unresponsive `Running` agent → marked `Crashed`, `HealthCheckFailed` event published.
  - `Crashed` agent, under `max_recovery_attempts` (default **3**) and past
    `recovery_cooldown_secs` (default **60s**) since the last attempt → reset to
    `Running` (a `HealthCheckFailed` event with `unresponsive_secs: 0` signals "recovery
    attempt" rather than a failure).
  - `Crashed` agent that has exhausted `max_recovery_attempts` → marked
    **`Terminated`** (not restarted automatically — "Manual restart required" log
    line, `kernel.rs:~4900`).
  - A `Running` agent that stops being unresponsive after prior failures has its
    `RecoveryTracker` entry reset.

## Agent Loop — `crates/openfang-runtime/src/agent_loop.rs` (5,493 lines)

Two near-identical top-level entry points: `run_agent_loop()` (`:293`) for
request/response, and `run_agent_loop_streaming()` (`:1520`) for SSE/WS streaming —
same logic, duplicated rather than shared (grep shows matching constants and control
flow at both `:483-1145` and `:1702-2364`).

Per-call sequence:

1. **Memory recall**: vector similarity via the embedding driver if available
   (`memory.recall_with_embedding_async`), else falls back to text search
   (`memory.recall`) — top 5 memories filtered to `session.agent_id`.
2. `HookEvent::BeforePromptBuild` fired if a `HookRegistry` is wired.
3. System prompt = `manifest.model.system_prompt` + a memory section appended by
   `prompt_builder::build_memory_section()`. **Canonical (cross-channel) context is
   NOT appended to the system prompt** — contrary to
   `docs/architecture.md`'s claim ("Load canonical context summary ... into system
   prompt"). Instead it's read from `manifest.metadata["canonical_context_msg"]`
   (set upstream by the kernel via `build_canonical_context_message`,
   `kernel.rs:2284-2291` / `:2865-2872`) and **inserted as the first `user` message**
   in the request, specifically to keep the system prompt byte-stable across turns
   for provider prompt caching (`agent_loop.rs:~430-440`).
4. User message appended to `session.messages`; `llm_messages` built **before**
   stripping images (so the current turn's image bytes reach the LLM); then image
   `ContentBlock`s are stripped from what gets persisted back into `session.messages`
   (replaced with `[Image processed]` placeholder text) to prevent base64 bloat in
   SQLite.
5. `session_repair::validate_and_repair()` runs on the LLM-bound message list.
6. History cap: `manifest.effective_max_history_messages()` (`AgentManifest` field
   `max_history_messages`, default via `DEFAULT_MAX_HISTORY_MESSAGES` in
   `openfang-types`) — if exceeded, oldest messages are drained, then
   `validate_and_repair()` + `ensure_starts_with_user()` run again because a naive
   drain can split a `ToolUse`/`ToolResult` pair or leave an assistant turn first
   (which strict providers like Gemini reject).
7. `max_iterations`: `manifest.autonomous.max_iterations` if set, else
   `MAX_ITERATIONS = 50` (`agent_loop.rs:35`) — matches the manifest default
   (`openfang-types/src/agent.rs:89`, `max_iterations: 50`).
8. `LoopGuard` initialized with `LoopGuardConfig::default()`, **except**
   `global_circuit_breaker` is bumped to `max_iterations * 3` whenever
   `max_iterations > 30` (so highly autonomous agents get proportionally more
   circuit-breaker headroom than the default 30).
9. Main loop `for iteration in 0..max_iterations`: context-overflow recovery pipeline
   (`context_overflow::recover_from_overflow`) runs every iteration before the LLM
   call; re-validates message pairing after any drain.

### Loop-guard thresholds (`crates/openfang-runtime/src/loop_guard.rs:52-65`) — docs match code here

```
warn_threshold = 3, block_threshold = 5, global_circuit_breaker = 30,
poll_multiplier = 3 (relaxed thresholds for POLL_TOOLS = ["shell_exec"]),
outcome_warn_threshold = 2, outcome_block_threshold = 3,
ping_pong_min_repeats = 3, max_warnings_per_call = 3
```
Also does outcome-hash tracking (identical call+result pairs escalate faster) and
A-B-A-B / A-B-C-A-B-C ping-pong detection across a 30-call rolling history
(`HISTORY_SIZE = 30`) — none of this granularity is in `docs/architecture.md`.

### Tool timeouts — not a flat 60s as docs claim

`docs/architecture.md:260` says "universal 60-second timeout". Code
(`agent_loop.rs:44-72`) actually uses:
- Regular tools: **120s** default (`TOOL_TIMEOUT_SECS`), overridable via
  `OPENFANG_TOOL_TIMEOUT_SECS` env var; `0` disables the timeout entirely.
- `agent_send` / `agent_spawn` (inter-agent calls, which can trigger a full nested
  agent loop): **600s** default (`AGENT_TOOL_TIMEOUT_SECS`), overridable via
  `OPENFANG_AGENT_TOOL_TIMEOUT_SECS`, same `0` = unbounded escape hatch.

### Max continuations — docs say 3, code says 5

`docs/architecture.md:264`: "`MAX_CONTINUATIONS = 3`". Code
(`agent_loop.rs:85-86`): `const MAX_CONTINUATIONS: u32 = 5;` with the comment
"Raised from 3 to 5 to allow longer-form generation" — the docs simply weren't
updated after that change.

### Inter-agent call depth — docs correct

`crates/openfang-runtime/src/tool_runner.rs:19`: `const MAX_AGENT_CALL_DEPTH: u32 = 5;`
enforced via `tokio::task_local!` (`tool_runner.rs:87`), checked at `:1800`.

### Tool result truncation — docs describe a removed mechanism

`docs/architecture.md:254-257` describes a flat "50,000 character hard cap" via a
function called `truncate_tool_result()`. That function no longer exists. It was
replaced by a **dynamic two-layer budget** in
`crates/openfang-runtime/src/context_budget.rs` (module doc comment: "Replaces the
hardcoded MAX_TOOL_RESULT_CHARS with a two-layer system"):
- Layer 1, `truncate_tool_result_dynamic()`: per-result cap = 30% of the model's
  context window, converted to chars at 2 chars/token for tool content
  (`ContextBudget::per_result_cap`). Breaks at the last newline within 200 chars of
  the cap for a clean cut, and appends a `[TRUNCATED: ... budget: 30% of {N}K context
  window]` marker.
- A single-result absolute ceiling of 50% of the context window
  (`single_result_max`), and a total-headroom ceiling of 75%
  (`total_tool_headroom_chars`) used by a second pass that compacts the *oldest* tool
  results first when the running total across the whole session exceeds it.
- `ContextBudget::default()` assumes a 200,000-token window if the model's actual
  context window isn't known.

## Session compaction — thresholds in docs are also wrong

`docs/architecture.md:276`: "default 80% of context window, keeping ... 20" messages.
Actual `CompactionConfig::default()` (`crates/openfang-runtime/src/compactor.rs:50-65`):
```
threshold = 30 messages, keep_recent = 10, token_threshold_ratio = 0.7 (70%),
max_summary_tokens = 1024, context_window_tokens = 200_000 (fallback),
base_chunk_ratio = 0.4, min_chunk_ratio = 0.15, max_retries = 3
```
`CompactionConfig::default()` is constructed fresh at every call site in
`kernel.rs` (lines 2078, 2453, 2664, 3578) — **there is no config.toml knob for
compaction thresholds**; they're compile-time constants.

Three independent auto-compaction triggers are OR'd together per turn
(`kernel.rs:2072-2109`), not just the single message/token trigger the docs describe:
1. `needs_compaction()` — message count > 30.
2. `needs_compaction_by_tokens()` — estimated tokens > 70% of the model's actual
   context window (looked up from the model catalog, not the 200K fallback, when
   available).
3. **Quota-headroom trigger** (entirely undocumented): if the agent's scheduler
   token headroom is known and the session's estimated tokens exceed 80% of that
   remaining hourly quota (and the session has >4 messages), compaction fires even
   if neither of the above did — "session would consume >80% of remaining quota"
   (`kernel.rs:2093-2100`).

Compaction itself (`compact_session()`, `compactor.rs:665`) tries, in order: single-pass
LLM summarization → adaptive chunked summarization (chunk ratio computed from message
length distribution, `min_chunk_ratio=0.15` floor) → a minimal non-LLM fallback string
("[Session compacted: N messages removed ...]") if the LLM is unavailable. The split
point is adjusted (`adjust_split_for_tool_pairs`) so a `ToolUse`/`ToolResult` pair is
never separated across the summarized/kept boundary. Result is stored via
`memory.store_llm_summary()` into the **canonical session**, and the regular session
is updated with `validate_and_repair_with_stats()`-repaired kept messages. The `/compact`
chat command maps to `compact_agent_session()` (`kernel.rs:3560`).

## Session repair — `crates/openfang-runtime/src/session_repair.rs`

`validate_and_repair()` (thin wrapper around `validate_and_repair_with_stats()`) does,
in this documented order (important: dedup before synthetic-insertion, per the
in-code comment, because some providers like Moonshot reuse `tool_use_id` across
turns after compaction):
1. Drop orphaned `ToolResult` blocks with no matching `ToolUse` id.
2. Drop messages that end up empty after filtering.
3. Reorder misplaced `ToolResult`s to immediately follow their `ToolUse`.
4. Deduplicate `ToolResult`s sharing a `tool_use_id`.
5. Insert synthetic error `ToolResult`s for any `ToolUse` left unmatched.
6. Merge consecutive same-role messages (required by providers like Anthropic that
   demand strict role alternation).

`RepairStats` tracks each category's count — surfaced in the `/compact` command's
result message and in `compact_agent_session`'s audit summary.

## Memory substrate — `crates/openfang-memory`

`MemorySubstrate::open()` (`substrate.rs:44`) opens the SQLite file, sets
`journal_mode=WAL` + `busy_timeout=5000`, runs `run_migrations()`, and composes:
`StructuredStore` (KV), `SemanticStore` (embeddings, optionally routed to an HTTP
memory-api gateway when `[memory] backend = "http"` and both `http_url` +
`http_token_env` are set — feature-gated behind `http-memory`), `KnowledgeStore`
(entity/relation graph), `SessionStore` (conversation history + canonical sessions),
`UsageStore`, `ConsolidationEngine`. All backed by one `Arc<Mutex<Connection>>`
shared across stores, bridged to async via `tokio::task::spawn_blocking`.

### SQLite schema — 8 versions, not 5

`docs/architecture.md:55` claims "Five schema versions: V1 core, V2 collab, V3
embeddings, V4 usage, V5 canonical_sessions." The live `SCHEMA_VERSION` constant
(`crates/openfang-memory/src/migration.rs:8`) is **8**, gated by `PRAGMA
user_version` (`get_schema_version`/`set_schema_version`, `migration.rs:51-72`).
Column additions use a hand-rolled `column_exists()` check because SQLite has no
`ADD COLUMN IF NOT EXISTS`.

| v | What it adds | Tables/columns |
|---|---|---|
| 1 | Core schema | `agents`, `sessions`, `events` (+idx on timestamp, source_agent), `kv_store`, `task_queue` (+idx status,priority), `memories` (+idx agent, scope), `entities`, `relations` (+idx source, target, type), `migrations` |
| 2 | Task collaboration | `task_queue` gains `title`, `description`, `assigned_to`, `created_by`, `result` |
| 3 | Vector search | `memories.embedding BLOB` |
| 4 | Usage/metering | `usage_events` table (+idx agent+time, timestamp) |
| 5 | Cross-channel memory | `canonical_sessions` (agent_id PK, messages, compaction_cursor, compacted_summary, updated_at) |
| 6 | Session labels | `sessions.label TEXT` |
| 7 | Device pairing | `paired_devices` table |
| 8 | Audit trail | `audit_entries` table (seq PK, timestamp, agent_id, action, detail, outcome, prev_hash, hash; +idx agent, timestamp, action) |

On the live container (`/var/lib/docker/volumes/openfang_openfang-data/_data/data/openfang.db`,
confirmed present, WAL mode — `.db-shm`/`.db-wal` siblings exist), the container has
no `sqlite3` binary to query directly; use `python3`'s `sqlite3` module inside the
container or copy the file out to the host.

### Data locations inside the official Docker image (`OPENFANG_HOME=/data`)

```
/data/config.toml           top-level config (api_key lives here in plaintext TOML, mode 0600)
/data/config.toml.bak       auto-backup written by some config edits (set_provider_key does; PUT /api/providers/{n}/url does NOT)
/data/secrets.env           provider API keys (e.g. HYPERFUSION_API_KEY), loaded into the process env at boot
/data/.env                  dotenv, read by the CredentialResolver *before* secrets.env/env (absent here)
/data/vault.enc             openfang-extensions AES-256-GCM credential vault (absent here)
/data/daemon.json           {listen_addr, pid, started_at, version, platform} written by `openfang start`, read by CLI/find_daemon
/data/cron_jobs.json        persisted cron scheduler state (array of JobMeta) — the ONLY durable scheduler
/data/hand_state.json       active Hand instances: [{hand_id, config, agent_id}] — replayed verbatim at boot
/data/custom_models.json    runtime model-catalog extensions (POST /api/models/custom)
/data/hands/<id>/HAND.toml  workspace hands (+ optional SKILL.md) — the only durable custom-hand path
/data/workflows/<uuid>.json one file per workflow definition; the API never deletes these (#1192)
/data/skills/<name>/        global user skills (created lazily on first install; absent here)
/data/agents/<name>/agent.toml   per-agent manifest on disk (see restore-merge logic above; EMPTY on this box)
/data/data/openfang.db(+-wal,-shm)   the SQLite substrate described above
/data/workspaces/<name>/    per-agent private state_dir + user-facing workspace
```

Present on this box right now: `agents/` (empty), `config.toml`, `config.toml.bak`,
`cron_jobs.json`, `custom_models.json`, `daemon.json`, `data/`, `hand_state.json`, `hands/`,
`secrets.env`, `workspaces/`. No `workflows/`, no `skills/`, no `.env`, no `vault.enc` — those are
created lazily.

**Backing it up:** the DB is WAL-mode, so `cp` on a live file can capture a torn state. Use SQLite's
backup API through the container's `sqlite3` *module* (verified — produces a 13-table snapshot with
`PRAGMA user_version = 8`):

```bash
D=/var/lib/docker/volumes/openfang_openfang-data/_data     # $D is not set for you here
docker exec openfang-openfang-1 python3 -c "
import sqlite3
s=sqlite3.connect('file:/data/data/openfang.db?mode=ro',uri=True); d=sqlite3.connect('/tmp/of.db')
s.backup(d); d.close(); s.close()"
docker cp openfang-openfang-1:/tmp/of.db ./openfang.db
docker exec openfang-openfang-1 rm -f /tmp/of.db    # the copy in the container is a second
                                                    # unprotected copy of every session and key
tar czf openfang-$(date +%F).tgz -C $D config.toml secrets.env cron_jobs.json custom_models.json \
        hand_state.json daemon.json hands workspaces      # everything else that matters
chmod 600 openfang.db openfang-$(date +%F).tgz            # both contain secrets in clear
```

**Restoring:** stop the container, drop the files back into `$D` (`config.toml`/`secrets.env` at
0600), put the DB at `$D/data/openfang.db` with **no** stale `-wal`/`-shm` siblings — a leftover WAL
replays a half-written transaction over the good snapshot — then start. Triggers and workflow *runs*
are memory-only and never come back; cron jobs, hands, agents and sessions do.

`scripts/ofbackup` performs exactly this sequence (`create` / `verify` / `restore`), refuses a
snapshot that fails `integrity_check`, and removes the stale `-wal`/`-shm` for you.

## Kernel shutdown — agents are preserved, not deleted

`kernel.rs:5231` `shutdown()`: best-effort SIGTERM to the WhatsApp gateway child
process if running, `supervisor.shutdown()`, then for every registered agent:
`registry.set_state(id, Suspended)` and **re-save to SQLite with the Suspended
state** so the next boot's restore path (see above) resumes them cleanly. Nothing
is deleted. `openfang stop` (`main.rs:1637`) POSTs `/api/shutdown`, polls
`find_daemon()` for up to 5s, and force-kills by PID from `daemon.json` if the
daemon doesn't exit gracefully in that window.

## Daemon detection / CLI-vs-in-process (`openfang-cli/src/main.rs`)

`find_daemon()` (`:1166`) reads `daemon.json` from the resolved OpenFang home,
normalizes `0.0.0.0` → `127.0.0.1` in the listen address (macOS DNS/connectivity
quirk), and GETs `/api/health` with a 1s connect / 2s total timeout. If that
succeeds the CLI talks HTTP to the running daemon; otherwise it boots an in-process
kernel for that single command. `daemon_client()` (`:1193`) auto-attaches
`Authorization: Bearer <key>` from `read_api_key()` (`:1621`), which checks
`config.toml`'s `api_key` first, then `OPENFANG_API_KEY` env var — **in source**. On this deployment
the file branch never produces a header: every mutating `openfang` command fails with
`Missing Authorization: Bearer <api_key> header` while `OPENFANG_API_KEY=<key>` works, proved at the
wire with a header-logging listener (only `accept`, `accept-encoding`, `host` are sent). Always
export `OPENFANG_API_KEY`; see `hands.md` §9.11 and §12. `openfang doctor
--repair` removes a stale `daemon.json` if no daemon actually answers health checks
(`main.rs:2345-2359`).

## WASM sandbox — dual metering, matches docs

`crates/openfang-runtime/src/sandbox.rs:109` `WasmSandbox::new()` enables both
`config.consume_fuel(true)` and `config.epoch_interruption(true)` on the Wasmtime
engine. `SandboxConfig::default()`: `fuel_limit = 1_000_000`, `max_memory_bytes = 16
MiB` (`sandbox.rs:54`), `timeout_secs: None` (defaults to 30s when unset per the doc
comment). The kernel then overwrites that memory figure from
`ResourceQuota.max_memory_bytes` (default **256 MB**, `openfang-types/src/agent.rs:272`) at
`kernel.rs:2512`, so 16 MiB and 256 MB are two different structs, not a disagreement — and
neither value is actually enforced, which is the point of #1242. A
background thread calls `engine.increment_epoch()` to drive the wall-clock
interrupt independent of fuel consumption, so a module that avoids expensive
instructions but spins in a tight loop still gets killed on time rather than
instruction count.

## API auth middleware — a real, verified gap between doc-comment and behavior

`crates/openfang-api/src/middleware.rs:71` `auth()` middleware. Loopback detection
via `ConnectInfo<SocketAddr>` defaults to **not loopback** if the extension is
missing (fail-closed intent). `/api/shutdown` skips auth only when the caller is
loopback.

**Verified gotcha**: `docs/architecture.md:559` states "Detailed health
(`/api/health/detail`) requires authentication and shows database stats, agent
counts, and subsystem status." The route handler's own doc-comment
(`crates/openfang-api/src/routes.rs:3494`, `"/// GET /api/health/detail — Full
health diagnostics (requires auth)."`) makes the same claim. **Both are wrong.**
`middleware.rs:103-104` explicitly lists `path == "/api/health/detail"` alongside
`/api/health` in the `is_public` boolean — no auth is enforced on it. Confirmed live
against the running container, which **has** a non-empty `api_key` configured:

```
$ curl -s http://127.0.0.1:4200/api/health/detail
{"agent_count":<n>,"config_warnings":[],"database":"connected","panic_count":0,
 "restart_count":0,"status":"ok","uptime_seconds":<n>,"version":"0.6.9"}
```
(`agent_count` and `uptime_seconds` are volatile — 2 agents at the time of writing. The point is the
200, not the numbers.)
No `Authorization` header was sent. This leaks agent count, uptime, restart/panic
counters, and any config validation warnings to anyone who can reach the port —
worth knowing before exposing `api_listen` beyond loopback even with an `api_key` set.

Also worth noting from the same `is_public` list (`middleware.rs:98-140`): a long
list of **GET-only** dashboard read endpoints are public by design so the SPA can
render before a key is entered — `/api/agents`, `/api/models`, `/api/providers`,
`/api/budget*`, `/api/channels`, `/api/hands*`, `/api/skills`, `/api/sessions`,
`/api/workflows`, `/api/cron/*`, `/api/config` (GET only — POST/PUT/DELETE always
require auth per the surrounding comment). This is intentional per the code
comments, unlike the health/detail case which contradicts its own docstring.

## Hot reload — `build_reload_plan` sees half the config

`crates/openfang-kernel/src/config_reload.rs:123-267`. `KernelConfig` has **47** `pub` fields
(`openfang-types/src/config.rs`); `build_reload_plan` touches only **25** of them. The other **22 are
never compared**, so editing them produces neither a `HotAction` nor a `restart_required` entry — the
hot-reloader cheerfully reports "no changes":

```
users  workspaces_dir  media  links  reload  include  exec_policy  bindings  broadcast
auto_reply  canvas  tts  docker  pairing  auth_profiles  thinking  budget  oauth
auth  workflows_dir  heartbeat  skills
```

Practical consequences: `POST /api/config/reload` after editing `[exec_policy]`, `[docker]`,
`[budget]`, `[auth]` or `[heartbeat]` does **nothing and says nothing**. Restart the daemon.

## `KernelHandle::memory_store` writes to ONE global namespace

`kernel.rs:7246-7251` stores under a single fixed agent id
`00000000-0000-0000-0000-000000000001` (`shared_memory_agent_id`, `kernel.rs:6958`) — **every agent
on the box shares that namespace**, so the `memory_store` / `memory_recall` tools are a global KV
store, not per-agent state. Two agents using the key `counter` clobber each other. For anything that
must be reliable, write a JSON file in the agent workspace and mutate it from a script instead.

## KernelHandle trait — the inter-agent seam

`crates/openfang-runtime/src/kernel_handle.rs:27`. Defined in `openfang-runtime` (so
tools like `agent_send`/`agent_spawn` don't need a circular dependency on
`openfang-kernel`) and implemented by `OpenFangKernel`. Key methods: `spawn_agent`,
`send_to_agent`, `list_agents`, `kill_agent`, `activate_agent` (default impl returns
an error — only overridden where wake-from-Suspended/Crashed is supported),
`memory_store`/`memory_recall` (shared namespace), `find_agents`, `task_post`/etc.
for the task board.

## Gotchas (all verified against code and/or the live container)

- `/api/health/detail` is **publicly readable with no auth** in v0.6.9 despite both
  `docs/architecture.md` and the route's own doc-comment claiming otherwise —
  verified live against the running container even with `api_key` configured
  (`middleware.rs:103-104` vs. `routes.rs:3494`).
- `read_api_key()` (`openfang-cli/src/main.rs:1621`) checks `config.toml` before `OPENFANG_API_KEY`
  **in source only** — the shipped binary sends no header from the file branch. Every mutating
  `openfang` command fails with `Missing Authorization: Bearer <api_key> header` unless you export
  `OPENFANG_API_KEY`. Do not "fix" a CLI 401 by editing `config.toml`; it is already correct there.
- `MAX_CONTINUATIONS` is **5**, not 3 as `docs/architecture.md:264` states
  (`agent_loop.rs:85-86`, explicit "Raised from 3 to 5" comment).
- Tool call timeout is **120s** for normal tools / **600s** for `agent_send` and
  `agent_spawn`, not a flat 60s as the docs claim; both are env-overridable
  (`OPENFANG_TOOL_TIMEOUT_SECS`, `OPENFANG_AGENT_TOOL_TIMEOUT_SECS`) and `0` disables
  the timeout entirely (`agent_loop.rs:44-72`).
- There is no fixed 50,000-char tool-result cap — `truncate_tool_result()` doesn't
  exist anymore; it's a dynamic budget (30%/50%/75% of the model's actual context
  window) in `context_budget.rs`, and the module doc-comment says so explicitly.
- Compaction triggers at **70%** token-threshold and keeps **10** recent messages by
  default, not the docs' claimed 80%/20 — and there's a third, undocumented
  quota-headroom trigger (80% of remaining hourly token quota) that can fire
  compaction independent of message count or context-window percentage.
- SQLite schema is at version **8** (adds `sessions.label`, `paired_devices`,
  `audit_entries`), not the 5 versions docs describe.
- Canonical cross-channel context is injected as a **user message**, not appended
  into the system prompt — deliberately, to keep the system prompt stable for
  provider-side prompt caching (`agent_loop.rs` around the `canonical_context_msg`
  handling).
- Restoring an agent on daemon restart only re-syncs a specific allow-list of
  manifest fields from `agent.toml` on disk against the DB copy (`kernel.rs:1303-1332`); edits to any
  other field are silently ignored until the agent is deleted and recreated
  (`kernel.rs` comment: "Missing a field here means changes to it are silently
  ignored until the agent is deleted and recreated"). **Silently ignored on restart:**
  `model.max_tokens`, `model.temperature`, `model.base_url`, `model.api_key_env`, `fallback_models`,
  `capabilities.shell` / `.network` / `.memory_read` / `.memory_write` / `.agent_message`, `tags`,
  `profile`, `module`, `state_dir`, `hooks`, `metadata`, `max_history_messages`.
- The hot-reloader is blind to 22 of `KernelConfig`'s 47 fields (25 are compared) — see the
  `build_reload_plan` section above.
- Agent private state (`SOUL.md`, per-agent memory) always lives under
  `<home>/workspaces/<name>/` by *name*, regardless of what `workspace =` is set to
  in `agent.toml` — the user-facing workspace path is a separate, optional overlay
  (issue #1097).
- Cloning an agent (`POST /api/agents/{id}/clone`) explicitly refuses to copy
  `MEMORY.md` and `HEARTBEAT.md` from the template's workspace so the clone starts
  with independent memory (issue #868) — don't expect a clone to inherit the
  template's accumulated state.
- A crashed agent gets at most `max_recovery_attempts` (default 3) auto-restarts,
  60s apart by default, before the heartbeat monitor marks it `Terminated`
  permanently — it will not come back without a manual respawn/restart.
- Reactive-schedule agents are exempt from heartbeat "unresponsive" checks while no
  turn is actively running — silence between messages is their expected steady
  state, so don't expect a `HealthCheckFailed` for a quiet chatbot agent.
- `exec_policy` is force-reinherited from the kernel's *current* `config.toml` on
  every restart unless the agent's own `agent.toml` explicitly sets `[exec_policy]`
  — editing the global exec policy retroactively changes already-spawned agents
  (this was a deliberate bug fix, #1132, not an oversight).
