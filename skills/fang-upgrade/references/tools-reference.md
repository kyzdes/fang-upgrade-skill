# OpenFang Built-in Tools — Complete Reference (v0.6.9 / acf2587)

Source of truth: `crates/openfang-runtime/src/tool_runner.rs` (dispatch + all 65 `ToolDefinition`
schemas + implementations), `agent_loop.rs` (timeouts, iteration limits), `host_functions.rs`
(the *separate* WASM-sandbox tool surface), `tool_policy.rs` (glob allow/deny policy),
`subprocess_sandbox.rs` (shell safety), `web_fetch.rs` / `web_search.rs`, `context_budget.rs`
(truncation), and `crates/openfang-types/src/{config,agent,capability}.rs`.

This file originated as a survey of the bare `v0.6.9` tag (`git describe --tags` → `v0.6.9`, commit
`acf2587`) at `/opt/openfang`. Prod has run our fork (`/root/src/openfang`, branch `main`, three
sprints ahead) since 2026-08-09, and `tool_runner.rs`, `agent_loop.rs`, `routes.rs`, `kernel.rs` and
`context_budget.rs` all carry fork edits that shifted line numbers below their edit points. Every
citation into those five files has been re-verified against the fork's current tree, not the bare
tag; citations into every other file (`host_functions.rs`, `tool_policy.rs`, `subprocess_sandbox.rs`,
`web_fetch.rs`, `web_search.rs`, `config.rs`, `capability.rs`, `docker_sandbox.rs`,
`process_manager.rs`, etc.) are unchanged from the original `acf2587` survey — the fork does not
touch those files, so those citations still point at `/opt/openfang` accurately. The one behavioral
change in this whole file is `file_read` gaining `offset`/`limit` (§3 Filesystem, FANG-58); every
other one of the 65 tool schemas is byte-identical to stock. Where docs/GitHub issues disagree with
the code, the code wins and the disagreement is called out explicitly.

**There are two independent tool surfaces in this codebase — do not conflate them:**

1. **The LLM-facing tool-calling surface** — `tool_runner::execute_tool()` / `builtin_tool_definitions()`.
   This is what an agent's LLM calls via the normal chat/tool-use loop. 65 tools, listed below.
2. **The WASM guest-sandbox host-call surface** — `host_functions::dispatch()` in `host_functions.rs`.
   Used only by WASM-compiled extensions/skills running inside `sandbox.rs`'s `GuestState`. It exposes
   9 methods (`fs_read`, `fs_write`, `fs_list`, `net_fetch`, `shell_exec`, `env_read`, `kv_get`, `kv_set`,
   `agent_send`, `agent_spawn`) each gated by a `Capability` enum value (`FileRead`, `FileWrite`,
   `NetConnect`, `ShellExec`, `EnvRead`, `MemoryRead`, `MemoryWrite`, `AgentMessage`, `AgentSpawn`).
   **The agent.toml `[capabilities] shell = [...]` glob list feeds ONLY this WASM surface** (via
   `Capability::ShellExec` pattern matching, `crates/openfang-types/src/capability.rs:137`) — it has
   **no effect whatsoever** on the main LLM `shell_exec` tool (see Gotcha #1).

---

## Table of contents

- [1. Timeout system for `shell_exec` (and all tools) — the full story](#1-timeout-system-for-shell_exec-and-all-tools--the-full-story)
- [2. Output truncation](#2-output-truncation)
- [3. Complete tool catalog (65 tools, `builtin_tool_definitions()`, tool_runner.rs:567-1355)](#3-complete-tool-catalog-65-tools-builtin_tool_definitions-tool_runnerrs567-1355)
- [4. `web_fetch` / SSRF details (`web_fetch.rs`)](#4-web_fetch--ssrf-details-web_fetchrs)
- [5. `web_search` providers (`web_search.rs`, `config.rs:195-327`)](#5-web_search-providers-web_searchrs-configrs195-327)
- [6. Shell security model for `shell_exec` / `process_start` (`subprocess_sandbox.rs`, `config.rs`)](#6-shell-security-model-for-shell_exec--process_start-subprocess_sandboxrs-configrs)
- [7. `tool_policy.rs` — **DEAD CODE. Nothing in this section runs.**](#7-tool_policyrs--dead-code-nothing-in-this-section-runs)
- [8. Gotchas (all verified against the live source and/or live instance — not guesses)](#8-gotchas-all-verified-against-the-live-source-andor-live-instance--not-guesses)

## 1. Timeout system for `shell_exec` (and all tools) — the full story

There are **three layers** of timeout, two of which are commonly confused with each other in
support threads (see GitHub #1204 below).

### Layer A — the outer per-tool-call wrapper (agent_loop.rs)

```rust
// agent_loop.rs:50
const TOOL_TIMEOUT_SECS: u64 = 120;          // default for ordinary tools
// agent_loop.rs:57
const AGENT_TOOL_TIMEOUT_SECS: u64 = 600;    // default for agent_send / agent_spawn only
```

`tool_timeout_for(tool_name)` (agent_loop.rs:72-84):

```rust
fn tool_timeout_for(tool_name: &str) -> Option<Duration> {
    let secs = match tool_name {
        "agent_send" | "agent_spawn" =>
            env_timeout_secs("OPENFANG_AGENT_TOOL_TIMEOUT_SECS").unwrap_or(AGENT_TOOL_TIMEOUT_SECS),
        _ =>
            env_timeout_secs("OPENFANG_TOOL_TIMEOUT_SECS").unwrap_or(TOOL_TIMEOUT_SECS),
    };
    if secs == 0 { None } else { Some(Duration::from_secs(secs)) }
}
```

- `OPENFANG_TOOL_TIMEOUT_SECS` — overrides the 120s default for every tool except agent_send/agent_spawn.
- `OPENFANG_AGENT_TOOL_TIMEOUT_SECS` — overrides the 600s default for agent_send/agent_spawn (added
  for issue #1125 — slow local vLLM rigs running "Hands").
- Setting either to `0` **disables that timeout entirely** — the future runs unbounded
  (`tool_timeout_for` returns `None`, and the match block at agent_loop.rs:1027-1046 does
  `None => exec_fut.await` (agent_loop.rs:1045) with no `tokio::time::timeout` wrapper at all).
- Env var parsing (`env_timeout_secs`, agent_loop.rs:61-63) is lenient: unset or unparseable →
  falls back to the compiled default (not an error, not 0).
- This wrapper is applied identically at two call sites (agent_loop.rs:1003 and :2271 — there
  are two structurally similar tool-execution loops in the file, one per response-handling path).
- On outer-timeout firing, the tool result becomes an `is_error: true` `ToolResult` with content
  `"Tool '<name>' timed out after <n>s."` (agent_loop.rs:1034-1041) — the agent sees this as a normal
  tool error and can retry/adjust, it is not a crash.

### Layer B — the inner per-call timeout inside `tool_shell_exec` (tool_runner.rs:1678-1820)

```rust
// tool_runner.rs:1688-1689
let policy_timeout = exec_policy.map(|p| p.timeout_secs).unwrap_or(30);
let timeout_secs = input["timeout_seconds"].as_u64().unwrap_or(policy_timeout);
...
// tool_runner.rs:1779-1780
let result = tokio::time::timeout(Duration::from_secs(timeout_secs), cmd.output()).await;
```

- The LLM-supplied `timeout_seconds` parameter (schema at tool_runner.rs:658-670, default advertised
  as "30" in the description) directly sets this inner timeout, **uncapped** — the LLM can pass
  `timeout_seconds: 999999` and this inner `tokio::time::timeout` will happily wait that long.
- If the parameter is omitted, falls back to `exec_policy.timeout_secs` (default `30`,
  `config.rs:965,1009`), which is itself overridable via `exec_policy.timeout_secs` in config.toml
  or agent.toml `[exec_policy]`.
- **This inner timeout is completely independent of Layer A.** Nothing links `input.timeout_seconds`
  to `OPENFANG_TOOL_TIMEOUT_SECS`.

### The interaction (this is the part GitHub #1204 gets wrong)

Because Layer A wraps the *entire* `execute_tool()` future including whatever Layer B is doing
inside it, **whichever timeout is shorter wins**. With stock defaults (Layer A = 120s outer,
Layer B = 30s inner-default-or-LLM-specified), if the LLM asks for `timeout_seconds: 300`, the
process is still killed at 120s by Layer A — the LLM's own requested timeout never gets a chance to
fire. Conversely, lowering `OPENFANG_TOOL_TIMEOUT_SECS` below the LLM's requested value makes Layer A
the effective ceiling. There is no code anywhere that clamps `input.timeout_seconds` to a maximum —
the "max 30 seconds per individual call" language some users have been told is **not in the code**.

**GitHub issue #1204** (title: *"The execution environment that powers shell_exec is deliberately
capped at 120 seconds (and a maximum of 30 seconds per individual call you specify)"*) is quoting an
**assistant hallucination**, not a real platform limit. It is FALSE as stated:
- Both the 120s and the 600s defaults are trivially overridden via `OPENFANG_TOOL_TIMEOUT_SECS` /
  `OPENFANG_AGENT_TOOL_TIMEOUT_SECS` — no rebuild needed, just set the env var on the daemon process.
- Both can be **disabled entirely** by setting the var to `0` (this exact feature was added, and is
  tested, per issue #1125, which is CLOSED and shipped).
- There is no per-call 30s ceiling in the code; 30 is only the *inner default* used when the LLM
  omits `timeout_seconds` and no `exec_policy.timeout_secs` override exists.
- What *is* true: with **all defaults left alone**, a shell command effectively cannot exceed ~120s
  in practice, because Layer A's default will kill it first regardless of what the LLM requests in
  `timeout_seconds`. That's a real, confirmable behavior — just not "deliberate and uncircumventable."

**How to actually raise the ceiling** (set on the OpenFang daemon's environment, not in agent.toml):
```bash
OPENFANG_TOOL_TIMEOUT_SECS=0            # disable the outer wrapper for all non-agent tools
OPENFANG_AGENT_TOOL_TIMEOUT_SECS=0      # disable it for agent_send / agent_spawn
# or, to raise rather than disable:
OPENFANG_TOOL_TIMEOUT_SECS=900
```
Then also raise `exec_policy.timeout_secs` (config.toml `[exec_policy]` or per-agent
`[exec_policy]` table) or pass a larger `timeout_seconds` in the tool call itself, since Layer B
still applies underneath Layer A.

### Layer C — process lifecycle on timeout (orphan risk)

Neither `tool_shell_exec`'s inner `tokio::time::timeout` nor agent_loop's outer wrapper actually
**kills** the child process when the timeout fires — they just stop polling the future.
`tokio::process::Command` in `tool_shell_exec` (tool_runner.rs:1703-1750) is never configured with
`.kill_on_drop(true)`, and dropping a `tokio::process::Child` does **not** send it a signal by
default; the process is silently orphaned (reparented, pipes closed, keeps running). Verified: a
repo-wide grep for `kill_on_drop` in `openfang-runtime/src` finds exactly one call site
(`media_understanding.rs:351`, used for ffmpeg subprocesses) — `tool_shell_exec` and
`process_manager.rs` do not use it.

There *is* a correctly-implemented idle/absolute dual-timeout-with-process-tree-kill function,
`wait_or_kill_with_idle()` (`subprocess_sandbox.rs:594-720`, calls `kill_child_tree`) — but it has
**zero callers** anywhere in the runtime crate. It is dead code. `tool_shell_exec` does not use it.
Practical effect: a `shell_exec` call that spawns something long-running (e.g. `python3 -m http.server`)
and then times out at either layer leaves that server process running on the host/container,
invisible to the agent, until the container itself is restarted.

---

## 2. Output truncation

### Layer 1 — per-tool-result dynamic cap (`context_budget.rs`)

Replaces an older hardcoded `MAX_TOOL_RESULT_CHARS` constant. `ContextBudget` (default context
window 200,000 tokens, `context_budget.rs:53-56`):
- `per_result_cap()` = 30% of context window × 2.0 chars/token (tool content is denser than prose).
- `single_result_max()` = 50% of window × 2.0 chars/token — absolute ceiling for one result.
- `total_tool_headroom_chars()` = 75% of window × 2.0 chars/token — Layer 2 trigger threshold.

`truncate_tool_result_dynamic()` (context_budget.rs:62-97) is applied to **every** tool result
after execution (agent_loop.rs:1064 for the non-streaming loop, :2332 for the streaming one — both
right after their respective timeout-wrapped call). It breaks at the last newline within 200 chars
of the cap (char-boundary-safe — walks back to avoid splitting multi-byte UTF-8), and appends:
```
[TRUNCATED: result was N bytes, showing first M (budget: 30% of 200K context window)]
```

**`file_read` interaction (FANG-58).** If the truncated content starts with `file_read`'s own
`[file_read: returned bytes …]` header (see §3 Filesystem), this layer calls
`rewrite_paging_header()` (context_budget.rs:110-151) on the kept slice before appending the
`[TRUNCATED]` marker. `file_read` writes its header based on what *it* delivered; if this 30%-of-window
cap then cuts that further, the original header's `end`/`offset=` numbers would describe bytes the
model never actually received. `rewrite_paging_header` recomputes `delivered_end` from the real
kept length and rewrites the header's byte range and `offset=` continuation value to match — so a
`limit` bigger than the per-result budget still gets you a truthful header, just not more bytes
than the budget allows.

### Layer 2 — total-headroom context guard (`apply_context_guard`, context_budget.rs:157+)

Scans *all* tool_result blocks already in message history before each LLM call; if their combined
size exceeds the 75%-headroom threshold, compacts the oldest results first (down to `single_max`).

### Tool-specific hardcoded caps (independent of the above, applied before Layer 1 ever sees the string)

| Tool | Cap | Location |
|---|---|---|
| `shell_exec` stdout/stderr | 100,000 bytes each | `tool_runner.rs:1789` (`let max_output = 100_000;`) — hardcoded literal, **not** read from `exec_policy.max_output_bytes` (whose default is 102,400 — the two do not match; see Gotcha #4) |
| `web_fetch` | `WebFetchConfig.max_chars` = 50,000 chars default | `config.rs:354`, applied `web_fetch.rs:144-152` |
| `image_analyze` base64 preview | full image if ≤512KB, else first 64KB only | `tool_runner.rs:2830-2843` |
| `canvas_present` HTML | `CANVAS_MAX_BYTES` task-local, default 512KB | `tool_runner.rs:3589`, set from `KernelConfig` at loop start |
| `process_poll` buffered lines | 1000 lines per stream, oldest dropped | `process_manager.rs:111,126` |
| `file_read` | 30% of context window (Layer 1, above), plus its own `offset`/`limit` window if the model passes one | see §3 Filesystem and the interaction note just above |

### The "64KB limit" — GitHub issue #1256

Issue #1256 title: *"How do I upload a file that exceeds the 64KB limit?"* This is **not** about
any of the LLM tool schemas above. There is genuinely a `65536`-byte (64KB) constant in the
codebase — `WebhookTriggerConfig.max_payload_bytes` (`config.rs:448-459`), documented as governing
the `/hooks/wake` and `/hooks/agent` webhook-trigger HTTP endpoints. **But it is dead config**:
`grep -rn max_payload_bytes crates/openfang-api/src` returns nothing — `routes.rs`'s
`webhook_wake`/`webhook_agent` handlers (routes.rs:11733-11876) use a plain `axum::Json<...>`
extractor with no reference to this field, and `server.rs` applies no `DefaultBodyLimit` layer to
these routes or globally. So `max_payload_bytes` in config.toml currently does nothing — the real
ceiling on those two routes is whatever axum's built-in default body limit is (2MB, unconfigured).
Separately, the general file-upload HTTP endpoint has its own real, enforced cap:
`MAX_UPLOAD_SIZE = 10 * 1024 * 1024` (10MB, `routes.rs:10731`, checked at `:10829`). Neither of
these is a *tool* limit — `file_write` has no explicit size cap of its own, and `file_read`'s own
cap is the `offset`/`limit` window described in §3, layered under the same 30%-of-window budget as
everything else (see Gotcha #6).

---

## 3. Complete tool catalog (65 tools, `builtin_tool_definitions()`, tool_runner.rs:567-1355)

For each: exact JSON Schema (`input_schema`) as shipped, and behavior notes. All are called via
the model's native tool-calling with these exact `name` values (aliases like `fs-write` get
normalized to canonical names first via `openfang_types::tool_compat::normalize_tool_name`,
tool_runner.rs:130). Of the 65 schemas, `file_read` is the only one the fork changed (FANG-58,
`tool_runner.rs:571-582`); the other 64 are byte-identical to stock v0.6.9 — verified via
`git diff acf2587e...main -- crates/openfang-runtime/src/tool_runner.rs`, which touches only the
`file_read` definition/impl and its tests.

### Filesystem
- **`file_read`** `{path: string (required), offset?: integer ≥0, limit?: integer ≥1}`
  (`tool_runner.rs:571-582`) → still reads the **whole file** as UTF-8 via
  `tokio::fs::read_to_string` first (`tool_runner.rs:1387`, unchanged from stock — a 500MB file is
  read entirely into memory regardless of `offset`/`limit`); paths resolved through
  `workspace_sandbox::resolve_sandbox_path` when a workspace root is set (blocks traversal),
  otherwise only rejects `..` components. *After* the read, `tool_file_read`
  (`tool_runner.rs:1381-1436`) slices `[offset, offset+limit)` (byte positions, UTF-8-boundary-clamped,
  `offset` past EOF is an error) and, if the slice is not the whole file, prepends a header:
  `[file_read: returned bytes {start}-{end} of {total} total in this file (N bytes); {M} bytes
  remain. Call file_read again with offset={end} to continue reading.]` — or `"; this is the end of
  the file.]"` when nothing remains. Omitting both params still returns the whole file with no
  header, byte-for-byte as before.
  **This function's own cap is not the last word.** The result still passes through the
  context-budget truncator (§2 Layer 1, 30% of the model's context window) same as every tool
  result — so a `limit` larger than that budget does not get you more bytes than the budget allows;
  it gets you a truncated result whose header is *rewritten* by `rewrite_paging_header`
  (`context_budget.rs:110-151`) to state what was actually delivered, not what `file_read` originally
  promised. See §2 for the two-layer mechanics.
  **Reading a large file to completion**: don't pass a `limit` bigger than the budget and expect
  it honored — instead loop, using the header's own numbers to drive the next call: start with
  `file_read {path}` (or `{path, offset:0}`), read the reported `end` back out of the header
  (`... returned bytes {start}-{end} of {total} ...`), and call again with `offset={end}` until the
  header says `"; this is the end of the file.]"` or `remaining` is 0. Each call still `read_to_string`s
  the whole file server-side regardless of where you resume — this loop bounds what reaches the
  model, not what the daemon reads off disk per call.
- **`file_write`** `{path, content: string, both required}` → creates parent dirs, overwrites,
  returns `"Successfully wrote N bytes to <path>"`.
- **`file_list`** `{path: string, required}` → directory listing (`std::fs::read_dir`, names only).
- **`create_directory`** `{path: string, required}` → idempotent `create_dir_all`, resolves nearest
  existing ancestor for path canonicalization (`resolve_directory_path_for_create`, tool_runner.rs:1466).
- **`apply_patch`** `{patch: string, required}` → custom diff format:
  `*** Begin Patch` / `*** Add File:` / `*** Update File:` / `*** Delete File:` / `@@` hunks with
  ` `/`-`/`+` prefixed lines (see `apply_patch.rs` for the parser).

### Web
- **`web_fetch`** `{url (required), method?: GET|POST|PUT|PATCH|DELETE, headers?: object, body?: string}`
  → SSRF-checked (`check_ssrf`, see §4), cached for GET (TTL = `WebConfig.cache_ttl_minutes`, default
  15 min), HTML→Markdown for GET responses via `html_to_markdown`, truncated at `max_chars` (50,000
  default), wrapped with `wrap_external_content()` markers, result prefixed `HTTP <status>\n\n...`.
  Non-GET responses are returned raw (not markdown-converted) — intentional, to not mangle JSON/XML.
  **PDF bug (issue #1271, confirmed in code, still open)**: `resp.text()` (web_fetch.rs:122-125) is
  called unconditionally — there is no Content-Type branch for `application/pdf` or any binary type.
  A PDF response is decoded as a lossy UTF-8 string of its raw (often FlateDecode-compressed) bytes
  and passed straight into the agent's context, both wasting/overflowing the context window and (on
  local models) poisoning the running conversation with garbled tokens that get re-sent every turn.
  A second, related gap: the size guard at web_fetch.rs:106-113 only fires when the server sends a
  `Content-Length` header — chunked responses without one buffer unbounded in `resp.text()` before
  the `max_chars` truncation at step 5 ever runs (memory-exhaustion vector on chunked responses).
- **`web_search`** `{query (required), max_results?: integer, default 5, max 20}` → provider chain,
  see §5.

### Shell
- **`shell_exec`** `{command (required), timeout_seconds?: integer, default 30}` → see §1 for
  timeouts and §6 for exec-policy/security. Result format:
  `"Exit code: N\n\nSTDOUT:\n...\nSTDERR:\n..."`; if exit 0 and stdout empty, stdout is replaced
  with `"Command executed successfully"`.

### Inter-agent
- **`agent_send`** `{agent_id (UUID or name), message}` both required → routes through
  `KernelHandle::send_to_agent`, which runs a **full agent loop on the target** synchronously —
  this is why it gets the 600s default timeout instead of 120s. Depth-limited: `MAX_AGENT_CALL_DEPTH
  = 5` (tool_runner.rs:19), tracked via a `tokio::task_local!` (`AGENT_CALL_DEPTH`); depth 5 →
  `"Inter-agent call depth exceeded (max 5). A->B->C chain is too deep. Use the task queue instead."`
- **`agent_spawn`** `{manifest_toml: string, required}` → spawns from an inline TOML manifest
  string; result `"Agent spawned successfully.\n  ID: <id>\n  Name: <name>"`. Depth-restricted
  ~~separately by `tool_policy.rs`'s `SUBAGENT_DENY_LEAF` at leaf depth~~ — **that restriction does
  not run**; `tool_policy.rs` is dead code (see §7). Depth is bounded only by
  `MAX_AGENT_CALL_DEPTH = 5`.
- **`agent_list`** `{}` (no params) → `"Running agents (N):\n  - name (id: ..., state: ..., model: provider:model)\n..."`.
- **`agent_kill`** `{agent_id: string, required}`.
- **`agent_activate`** `{agent_id: string, required}` — wakes Suspended/Crashed/Created agents;
  terminated agents cannot be revived.

### Shared memory
- **`memory_store`** `{key: string, value: any-JSON}` both required → arbitrary JSON value (not
  just strings — schema description says "JSON-encode... or pass a plain string" but the impl
  (tool_runner.rs:1924-1933) accepts the raw `serde_json::Value` unchanged). Returns
  `"Stored value under key '<key>'."`
- **`memory_recall`** `{key: string, required}` → pretty-printed JSON of the stored value, or
  `"No value found for key '<key>'."` if absent. **One global namespace for the whole box** —
  `KernelHandle::memory_store/recall` hardcode the agent id
  `00000000-0000-0000-0000-000000000001` (`shared_memory_agent_id`, `kernel.rs:7149`, used at
  `kernel.rs:7438`/`:7445`). Two agents writing `counter` clobber each other. The `self.*` / `shared.*`
  prefixes you see in `[capabilities] memory_write = [...]` are **not enforced for LLM agents** —
  that field only binds in the WASM host ABI (Gotcha #1) — so they are a naming convention you must
  uphold yourself. For state that must be reliable, use a JSON file in the agent workspace.

### Collaboration
- **`agent_find`** `{query: string, required}` → fuzzy match over agent name/tags/tools/description.
- **`task_post`** `{title, description (both required), assigned_to?: string}`.
- **`task_claim`** `{}` — claims next queued task assigned to self or unassigned.
- **`task_complete`** `{task_id, result}` both required.
- **`task_list`** `{status?: "pending"|"in_progress"|"completed"}`.
- **`event_publish`** `{event_type: string (required), payload?: object, default {}}` → broadcasts
  to the kernel event bus; can trigger proactive agents subscribed to that event type.

### Scheduling (soft, in-process) vs Cron (see also §Cron below — two separate subsystems)
- **`schedule_create`** `{description, schedule (both required), agent?: string}` — natural language
  ("every 5 minutes", "daily at 9am") or cron syntax; `agent` defaults to self.
- **`schedule_list`** `{}`.
- **`schedule_delete`** `{id: string, required}`.

### Knowledge graph
- **`knowledge_add_entity`** `{name, entity_type (both required), properties?: object}` —
  `entity_type` parsed case-insensitively into a fixed enum (`person`/`organization`|`org`/
  `project`/`concept`/`event`/`location`/`document`/`tool`/custom-fallback,
  `parse_entity_type`, tool_runner.rs:2057). Returns the store-assigned entity ID.
- **`knowledge_add_relation`** `{source, relation, target (required), confidence?: 0.0-1.0 default
  1.0, properties?: object}` — `relation` similarly parsed into a fixed set (works_at,
  knows_about, related_to, depends_on, owned_by, created_by, located_in, part_of, uses,
  produces, or custom).
- **`knowledge_query`** `{source?, relation?, target?: string, max_depth?: integer default 1}` —
  returns matching triples as `"{source} ({type}) --[{relation} ({confidence}%)]--> {target} ({type})"`
  lines, or `"No matching knowledge graph entries found."`

### Image / media
- **`image_analyze`** `{path (required), prompt?: string}` → reads file directly off disk (no
  workspace-sandbox resolution — raw path passed to `tokio::fs::read`, so absolute paths outside the
  workspace ARE readable by this tool, unlike `file_read`), sniffs format from magic bytes, extracts
  dimensions for common formats, base64-encodes (full image if ≤512KB, else 64KB preview with a
  `"... [truncated, N total bytes]"` suffix).
- **`media_describe`** `{path (required), prompt?: string}` → vision-LLM description; auto-picks
  Anthropic/OpenAI/Gemini based on configured keys.
- **`media_transcribe`** `{path (required), language?: ISO-639-1}` → auto-picks Groq Whisper or
  OpenAI Whisper; formats: mp3, wav, ogg, flac, m4a, webm.
- **`image_generate`** `{prompt (required, max 4000 chars), model?: dall-e-3|dall-e-2|gpt-image-1,
  size?, quality?: hd|standard, count?: 1-4 (dall-e-3 forces 1)}` → requires `OPENAI_API_KEY`; saves
  to workspace `output/`.
- **`text_to_speech`** `{text (required, max 4096 chars), voice?: alloy|echo|fable|onyx|nova|shimmer,
  format?: mp3|opus|aac|flac}` → auto-picks OpenAI or ElevenLabs; saves to workspace `output/`.
- **`speech_to_text`** `{path (required), language?}` → same backends as `media_transcribe`
  (these are two separate tool names doing the same thing — likely a naming-migration artifact).

### Cron (persistent scheduler, separate from `schedule_*`)
- **`cron_create`** `{name (required, max 128 chars, alnum+space/hyphen/underscore),
  schedule (required object): {"kind":"at","at":"<RFC3339>"} | {"kind":"every","every_secs":N} |
  {"kind":"cron","expr":"<5-field cron>"}, action (required object):
  {"kind":"system_event","text":"..."} | {"kind":"agent_turn","message":"...","timeout_secs":300},
  delivery?: {"kind":"none"} | {"kind":"channel","channel":"telegram"} | {"kind":"last_channel"},
  one_shot?: boolean}` → **Max 50 jobs per agent** — `MAX_JOBS_PER_AGENT`
  (`crates/openfang-types/src/scheduler.rs:12`, enforced `:250`), not in `tool_runner.rs` itself; the
  global cap is `max_cron_jobs = 500` (`config.rs:1403-1405`).
- **`cron_list`** `{}`, **`cron_cancel`** `{job_id: string, required}`.
- *Intended* to be denied to subagents by `tool_policy.rs::SUBAGENT_DENY_ALWAYS` — **but that code
  never runs** (§7). Subagents can create cron jobs.

### Channel / outbound
- **`channel_send`** `{channel, recipient (both required), subject?, message?, image_url?, file_url?,
  file_path?, filename?, thread_id?}` — one of message/image_url/file_url/file_path must carry
  content; `file_path` reads from local disk.

### Hands (curated capability packages)
- **`hand_list`** `{}`, **`hand_activate`** `{hand_id (required), config?: object}` (spawns a
  specialized sub-agent), **`hand_status`** `{hand_id: string, required}`, **`hand_deactivate`**
  `{instance_id: string (UUID), required}`. (`SUBAGENT_DENY_ALWAYS` nominally covers
  `hand_activate`/`hand_deactivate`, but that list is never consulted — §7.)

### A2A (cross-instance agent-to-agent)
- **`a2a_discover`** `{url: string, required}` → fetches the remote agent card.
- **`a2a_send`** `{message (required), agent_url?, agent_name?, session_id?}` — one of
  agent_url/agent_name needed to address the target.

### Docker sandbox
- **`docker_exec`** `{command: string, required}` → requires `docker.enabled=true` in config AND
  a live Docker daemon (`is_docker_available()`); creates a fresh sandbox container per call
  (`docker_sandbox::create_sandbox`), timeout from `DockerSandboxConfig.timeout_secs` (default 60,
  `config.rs:645`), idle_timeout_secs default 300. Requires a workspace directory (errors otherwise).
  It **does** get the shell-metacharacter check — `exec_in_sandbox` → `validate_command`
  (`docker_sandbox.rs:181`, `:65-75`) → the same `contains_shell_metacharacters`. What it does **not**
  get: `exec_policy` validation (`is_shell_tool()` is `shell_exec | process_start` only,
  `tool_runner.rs:27-29`) and any approval prompt (default `require_approval = ["shell_exec"]`).
  Container defaults bound the damage: `network="none"`, `read_only_root=true`, `512m`,
  `pids_limit=100` (`config.rs:635-657`).

### Persistent processes (long-running REPLs/servers, distinct from `shell_exec`)
- **`process_start`** `{command (required), args?: string[]}` → **hardcoded to 5 processes max per
  agent** (`ProcessManager::new(5)` at `kernel.rs:1243`, enforced at `process_manager.rs:80-85` with
  error `"Agent '<id>' already has N processes (max: 5)"`). Also runs through
  `contains_shell_metacharacters` on both `command` and every arg individually, and through
  `validate_command_allowlist` against `exec_policy` exactly like `shell_exec`. (Nominally in
  `SUBAGENT_DENY_ALWAYS`, but that list is dead code — subagents *can* start processes, §7.)
  Stdout/stderr each buffered up to 1000 lines (oldest dropped, process_manager.rs:111,126).
- **`process_poll`** `{process_id: string, required}` → non-blocking drain, `{stdout, stderr}` JSON.
- **`process_write`** `{process_id, data (both required)}` → newline auto-appended if missing.
- **`process_kill`** `{process_id: string, required}`.
- **`process_list`** `{}` → all processes for the calling agent (id, command, uptime, alive).

### Misc
- **`location_get`** `{}` — IP-based geolocation (city/country/coords/timezone).
- **`system_time`** `{}` — ISO 8601 + Unix epoch + timezone.
- **`canvas_present`** `{html (required), title?: string}` → HTML is sanitized (no `<script>`, no
  event-handler attributes, no `javascript:` URLs — see `sanitize_canvas_html`), capped at
  `CANVAS_MAX_BYTES` (default 512KB), written to workspace `output/canvas_<timestamp>_<8charid>.html`,
  wrapped in a minimal `<!DOCTYPE html>` document.

### Browser automation (all require `browser_ctx`; error "Browser tools not available. Ensure
Chrome/Chromium is installed." otherwise — persistent session keyed per agent_id)
- **`browser_navigate`** `{url: string, required}` (SSRF-checked like web_fetch)
- **`browser_click`** `{selector: string, required}` (CSS selector or visible text)
- **`browser_type`** `{selector, text (both required)}`
- **`browser_screenshot`** `{}` → base64 PNG
- **`browser_read_page`** `{}` → markdown
- **`browser_close`** `{}`
- **`browser_scroll`** `{direction?: up|down|left|right default down, amount?: integer default 600}`
- **`browser_wait`** `{selector (required), timeout_ms?: default 5000, max 30000}`
- **`browser_run_js`** `{expression: string, required}`
- **`browser_back`** `{}`

### Skill introspection (issue #1038 — lets an agent discover skills without filesystem access,
since global skills live outside the workspace sandbox and `file_read` can't reach them)
- **`skill_list`** `{}` → name/version/description/runtime/provided-tools per installed skill.
- **`skill_describe`** `{name: string, required}` → the SKILL.md body.
- **`skill_execute`** `{skill (required), tool?: string, input?: object}` → for code-runtime
  skills (Python/Node/Shell), invokes the underlying script; for prompt-only skills, returns the
  SKILL.md body for the agent to follow using its other tools.

---

## 4. `web_fetch` / SSRF details (`web_fetch.rs`)

`check_ssrf(url, ssrf_allowed_hosts)` runs **before any network I/O**:
1. Scheme must be `http://` or `https://` — `file://`, `ftp://`, `gopher://` all rejected.
2. Unconditional hostname blocklist (never bypassable by allowlist): `localhost`,
   `ip6-localhost`, `metadata.google.internal`, `metadata.aws.internal`, `instance-data`,
   `169.254.169.254`, `100.100.100.200` (Alibaba IMDS), `192.0.0.192` (Azure IMDS alt), `0.0.0.0`,
   `::1`, `[::1]`.
3. If hostname matches `ssrf_allowed_hosts` (exact, `*.domain` wildcard, or CIDR for IPs) → allow,
   skip DNS resolution.
4. Otherwise resolve DNS and check every returned IP: cloud-metadata IPs are hard-blocked even
   through the allowlist; loopback/unspecified/private-range IPs are blocked unless CIDR-allowlisted.
5. Private ranges checked: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16` (IPv4);
   `fc00::/7` and `fe80::/10` (IPv6 ULA/link-local).

`WebFetchConfig` defaults (`config.rs:351-361`): `max_chars=50_000`, `max_response_bytes=10MB`,
`timeout_secs=30`, `readability=true` (HTML→Markdown on), `ssrf_allowed_hosts=[]`.
`host_functions.rs`'s WASM `net_fetch` delegates to this exact `check_ssrf` (host_functions.rs:230)
— same protection for both tool surfaces, not duplicated/divergent logic.

---

## 5. `web_search` providers (`web_search.rs`, `config.rs:195-327`)

`SearchProvider` enum: `Brave | Tavily | Perplexity | DuckDuckGo | Searxng | Auto` (default `Auto`).

`Auto` priority order (`search_auto`, web_search.rs:71-113): **Tavily → Brave → Perplexity →
Searxng → DuckDuckGo**, picking the first provider whose API key env var resolves (or, for
Searxng, whose `url` is non-empty). DuckDuckGo is the only one that never needs a key — it's the
guaranteed-available fallback.

| Provider | Needs API key? | Env var (config key) | Notes |
|---|---|---|---|
| Tavily | Yes | `TAVILY_API_KEY` (`tavily.api_key_env`) | `search_depth`="basic"/"advanced", `include_answer` default true |
| Brave | Yes | `BRAVE_API_KEY` (`brave.api_key_env`) | supports `country`, `search_lang`, `freshness` filters |
| Perplexity | Yes | `PERPLEXITY_API_KEY` (`perplexity.api_key_env`) | model default `"sonar"` |
| Searxng | No (self-hosted) | n/a — set `[web.searxng] url = "https://..."` | no key, but you must run/host an instance |
| DuckDuckGo | No | n/a | scrapes DDG HTML results |

`resolve_api_key()` (web_search.rs:596) wraps values in `Zeroizing<String>` (auto-wiped on drop) —
consistent with the rule that API keys never get written to disk/logs in plaintext by this runtime.

---

## 6. Shell security model for `shell_exec` / `process_start` (`subprocess_sandbox.rs`, `config.rs`)

### `ExecSecurityMode` (3 modes, default `Allowlist`)
- **`Deny`** (aliases: `none`, `disabled`) — blocks all shell execution outright.
- **`Allowlist`** (default) — direct `argv`-split execution (via `shlex::split`, **no shell
  interpreter invoked at all** — eliminates an entire class of injection via encoding tricks, `$IFS`,
  glob expansion) of only commands whose base name is in `safe_bins` or `allowed_commands`.
- **`Full`** — `sh -c` (or `cmd /C` / Git-Bash `sh.exe` on Windows) interpretation of anything;
  explicit opt-in, documented as "unsafe, dev only".

### Default `ExecPolicy` (`config.rs:997-1015`)
```
mode = Allowlist
safe_bins = [sleep, true, false, cat, sort, uniq, cut, tr, head, tail, wc,
             date, echo, printf, basename, dirname, pwd, env]
allowed_commands = []          # empty by default!
timeout_secs = 30
max_output_bytes = 102400      # 100KB — DECLARED BUT UNUSED, see Gotcha #4
no_output_timeout_secs = 30    # DECLARED BUT UNUSED, see Gotcha #4
shell_env_passthrough = []
```
With **no `[exec_policy]` anywhere** (neither in config.toml nor in the agent's own agent.toml —
this is the state of the actual live instance checked during this research: `grep exec
config.toml` returns nothing), the effective policy at runtime is exactly this default: `Allowlist`
mode with only the 18 `safe_bins` runnable and an **empty** `allowed_commands`. Any other binary —
`python3`, `git`, `npm`, `cargo`, `curl`, etc. — is rejected by `validate_command_allowlist`
(`subprocess_sandbox.rs:329+`) with: `"Command '<base>' is not in the exec allowlist. Add it to
exec_policy.allowed_commands or exec_policy.safe_bins."`

### Resolution order for which `ExecPolicy` an agent actually gets (kernel.rs:1650-1655, :1417-1436)
1. If the agent's own `agent.toml` has an `[exec_policy]` table → use it verbatim.
2. Otherwise → inherit the kernel's global `config.exec_policy` (itself the default above unless
   `config.toml` has a top-level `[exec_policy]` table).
3. Re-resolved **on every kernel restart** specifically so that live edits to `config.toml`'s
   `[exec_policy]` take effect for agents that don't explicitly override it (fixes #1132).

Because of step 2, `manifest.exec_policy` passed into `tool_runner::execute_tool()` is essentially
**never actually `None`** for a properly-loaded agent — the `if let Some(policy) = exec_policy { ... }`
guards around allowlist validation in `tool_runner.rs` (:262, :3380) are effectively always taken;
don't assume "no exec_policy configured" means "no restriction" — it means "default restriction."

### Metacharacter blocking (always applied, **even in Full mode** — `contains_shell_metacharacters`,
subprocess_sandbox.rs:126-179): backtick, `$(`, `${`, `;`, `|`, `>`, `<`, `{`, `}`, embedded
newline/CR, null byte, `&`. This runs unconditionally before allowlist checks and cannot be
bypassed by exec_policy mode.

### Taint heuristics (`check_taint_shell_exec`, tool_runner.rs:37-58) — skipped only in `Full` mode
Flags commands containing `curl `, `wget `, `| sh`, `| bash`, `base64 -d`, `eval ` when combined
with the `TaintLabel::ExternalNetwork` sink check. (Note: pipes are already unconditionally blocked
by the metacharacter check above in non-Full modes, so the `| sh`/`| bash` heuristic here only
matters in `Full` mode where pipes are otherwise allowed — but taint checking is explicitly
*skipped* in Full mode per tool_runner.rs:278-288, so this heuristic path is effectively dead for
the one mode it would matter in. Worth flagging if reviewing this code.)

### Env isolation (`sandbox_command`, subprocess_sandbox.rs:46-78)
Every shell_exec/process_start child gets `env_clear()`'d, then only `SAFE_ENV_VARS` (`PATH`,
`HOME`, `TMPDIR`, `TMP`, `TEMP`, `LANG`, `LC_ALL`, `TERM`, plus Windows-specific ones) plus
whatever's in `exec_policy.shell_env_passthrough` (merged with any Hand-granted env list) is
re-added. `shell_env_passthrough = ["*"]` forwards the **entire** parent environment including
API keys — documented as dangerous, use only deliberately. Aliases `env_passthrough` and
`env_allowlist` are accepted for backward compat (issue #1169).

---

## 7. `tool_policy.rs` — **DEAD CODE. Nothing in this section runs.**

> **Correction (verified by grep across the whole workspace).** Everything described below exists as
> compiled Rust and is unit-tested, but **has zero callers**. `resolve_tool_access`
> (`tool_policy.rs:76`), `filter_tools_by_depth` (`:254`), `SUBAGENT_DENY_ALWAYS` (`:236-244`) and
> `SUBAGENT_DENY_LEAF` (`:247`) are referenced by nothing outside the module's own tests; the only
> mention of the module anywhere else in the tree is `pub mod tool_policy;` at
> `crates/openfang-runtime/src/lib.rs:51`. There is no `[tool_policy]` config section.
>
> **Practical consequence: nothing is denied to a subagent by depth.** A subagent *can* call
> `cron_create`, `schedule_create`, `hand_activate`, `process_start`, `agent_spawn` — the only depth
> limiter that actually runs is `MAX_AGENT_CALL_DEPTH = 5` (`tool_runner.rs:19`, enforced at `:1800`).
> If you need those tools kept away from delegated agents, use `tool_blocklist` on the child
> manifest or leave them out of `capabilities.tools`; the policy engine will not do it for you.
>
> The rest of this section is retained as a description of the *intended* design (and of what a
> cherry-picked upstream patch would activate), **not of runtime behaviour**.

`ToolPolicy` adds glob-pattern rules with groups, deny-wins:

```rust
pub struct ToolPolicy {
    agent_rules: Vec<ToolPolicyRule>,   // {pattern, effect: Allow|Deny} — highest priority
    global_rules: Vec<ToolPolicyRule>,  // checked after agent rules
    groups: Vec<ToolGroup>,             // named pattern collections, ref'd as "@group_name"
    subagent_max_depth: u32,            // default 10
    subagent_max_concurrent: u32,       // default 5
}
```

Resolution order (`resolve_tool_access`, tool_policy.rs:76-145):
1. Depth check first for subagent tools (`agent_spawn`/`agent_call`/`spawn_agent`) — if
   `depth > subagent_max_depth` → `DepthExceeded`.
2. Agent-level `Deny` rules checked first — any match denies immediately (deny-wins, even over an
   agent-level Allow for the same pattern).
3. Global-level `Deny` rules checked next.
4. If **any** `Allow` rule exists anywhere (agent or global), the tool must match at least one or
   it's implicitly denied — i.e. once you write a single allow rule, everything else is deny-by-default.
5. With **zero** rules configured at all → allow everything (this is the common case; most shipped
   agent.toml files don't use `[tool_policy]` at all, relying solely on `[capabilities] tools = [...]`).

`glob_match()` supports only `*` wildcards (no `?`, no character classes) — `"shell_*"`,
`"mcp_*_list"`, bare `"*"` for everything.

**Declared-but-inert subagent restrictions** (`tool_policy.rs:236-247`):
```rust
SUBAGENT_DENY_ALWAYS = [cron_create, cron_cancel, schedule_create, schedule_delete,
                         hand_activate, hand_deactivate, process_start]   // intended: any depth > 0
SUBAGENT_DENY_LEAF   = [agent_spawn, agent_kill]                          // intended: depth >= max_depth-1
```
**These lists are never consulted.** The function that would apply them, `filter_tools_by_depth`
(`:254`), has no callers. A subagent at depth > 0 can call every one of those tools if its tool list
includes them.

---

## 8. Gotchas (all verified against the live source and/or live instance — not guesses)

1. **`[capabilities] shell = [...]` in agent.toml does nothing for the LLM's `shell_exec` tool.**
   `manifest_to_capabilities()` (kernel.rs:6948-7016) turns the `shell` glob list into
   `Capability::ShellExec(pattern)` values, which are only ever checked by the **WASM sandbox**
   host-call path (`host_functions::host_shell_exec`, via `capability_matches` →
   `capability.rs:137`). The main LLM-tool-calling `shell_exec` (`tool_runner.rs:244-296`) checks
   only `allowed_tools` (the flat `[capabilities] tools = [...]` list, i.e. is `"shell_exec"` in the
   list at all) plus `exec_policy.allowed_commands`/`safe_bins` — it never looks at
   `[capabilities] shell`. The shipped `agents/assistant/agent.toml` declares
   `shell = ["python *", "cargo *", "git *", "npm *"]` (line 78) which, given the default global
   `ExecPolicy` (Allowlist, empty `allowed_commands`), means the Assistant agent's `shell_exec` tool
   in practice can run only the 18 `safe_bins` — not python/cargo/git/npm — despite that glob list
   suggesting otherwise. To actually allow those binaries, add them to `exec_policy.allowed_commands`
   in config.toml or the agent's own `[exec_policy]` table, or set `mode = "full"`.

2. **Issue #1204's "hard 120s/30s cap" is an LLM hallucination, not a real limit** — see §1. Both
   numbers are env-var-overridable and env-var-disableable. File this away if you see users citing it.

3. **Timeout-killed shell/process children are orphaned, not killed** (§1, Layer C). No
   `kill_on_drop`, and the properly-built `wait_or_kill_with_idle`/`kill_child_tree` pair in
   `subprocess_sandbox.rs` has no callers anywhere. A long-running background process started via
   `shell_exec` (not `process_start`, which is the correct tool for that) and then timed out keeps
   running on the host/container after the tool call "fails."

4. **`exec_policy.max_output_bytes` and `exec_policy.no_output_timeout_secs` are dead config
   fields.** They're declared in `ExecPolicy` (config.rs:966-971) and `max_output_bytes` is asserted
   in a unit test (`subprocess_sandbox.rs:957`), but it is never read outside test code;
   `no_output_timeout_secs` is only ever **written** (`kernel.rs:3917`) and never read at all.
   The actual output cap used by `shell_exec` is a separate hardcoded literal,
   `let max_output = 100_000;` at `tool_runner.rs:1789`. Note the two numbers do **not** coincide:
   the config default is `100 * 1024 = 102,400`, the enforced literal is `100,000`. Changing
   `exec_policy.max_output_bytes` in config.toml has zero effect. Likewise there is no idle/no-output timeout logic wired into `tool_shell_exec` at
   all (only the flat absolute timeout described in §1); the fully-implemented
   `wait_or_kill_with_idle` idle-timeout function that *would* honor `no_output_timeout_secs` is dead
   code (see Gotcha #3).

5. **`max_concurrent_tools` in `[resources]` is not a real field — it's silently dropped.**
   `agents/assistant/agent.toml` ships with `[resources] max_concurrent_tools = 10` (line 70), and
   the TUI's agent-creation wizard (`crates/openfang-cli/src/tui/screens/agents.rs:832`) writes the
   same key into new agent.toml files it generates. But `ResourceQuota`
   (`crates/openfang-types/src/agent.rs:250-267`) has no such field, and it derives plain
   `#[serde(default)]` (not `deny_unknown_fields`) — so this key parses fine and is quietly discarded.
   There is no concurrency limit on tool calls in the current runtime. Likewise
   `max_tool_calls_per_minute` **is** a real `ResourceQuota` field with a default (60/min,
   agent.rs:256,274) but a repo-wide grep shows it is never read anywhere outside its own
   declaration — also currently unenforced. Don't assume any `[resources]` limit is active without
   checking whether the runtime actually reads it.

6. **`file_read`'s `offset`/`limit` page the *response*, not the read; `file_write` has no built-in
   size limit; `image_analyze` bypasses the workspace sandbox entirely.** `tool_file_read` still
   does `tokio::fs::read_to_string` on the whole file with no cap (`tool_runner.rs:1387`,
   `offset`/`limit` are applied afterward as a slice on the in-memory `String` — `tool_runner.rs:1381-1436`)
   — a multi-hundred-MB file is read fully into memory regardless of what `offset`/`limit` the model
   passed, before either `file_read`'s own slicing or Layer-1 context truncation ever sees the
   string. `offset`/`limit` bound what comes back to the model; they do not bound what gets read off
   disk. Separately, `tool_image_analyze` (tool_runner.rs:2813+) calls `tokio::fs::read(path)`
   directly on the raw input path — unlike `file_read`, it does **not** go through
   `workspace_sandbox::resolve_sandbox_path`, so it can read arbitrary absolute paths outside the
   agent's workspace if the LLM supplies one (e.g. `/etc/passwd`, though it'll fail image-format
   sniffing on non-image content, it *will* read and base64-encode the bytes first).

7. **The webhook-trigger 64KB payload cap (config's `max_payload_bytes`) is unenforced dead
   config** (see §2's "64KB limit" writeup) — don't tell an operator to raise it expecting a
   behavior change; there is nothing in `routes.rs` that reads it.

8. **`web_fetch` will feed raw PDF bytes into the model's context** (issue #1271, confirmed at
   `web_fetch.rs:122-125` — no Content-Type routing exists). If you need PDF content, don't rely on
   `web_fetch`; download via `shell_exec`+an allowlisted downloader or `docker_exec`, or wait for a
   fix — the reporter claims to have a working patch (pure-Rust `pdf-extract`, no new system deps)
   but it is not merged as of this checkout.
   Also: chunked HTTP responses with no `Content-Length` header bypass web_fetch's size guard
   entirely and buffer unbounded in memory before any truncation applies.

9. **`agent_send` gets a 600s timeout by default, `agent_spawn` also gets 600s** (both match on
   `"agent_send" | "agent_spawn"` in `tool_timeout_for`) — but the tool that actually starts a brand
   new agent process/context (`agent_spawn`) doing a first turn can be just as slow as a
   `agent_send` to an existing one; this is intentional, not an oversight, per issue #1125's history.

10. **`shell_exec`'s Allowlist-mode execution never invokes a shell at all** (`shlex::split` +
    direct `Command::new(argv[0]).args(argv[1..])`) — meaning shell features the LLM might expect
    (globbing, `~` expansion, env var substitution inside the command string, pipes) simply don't
    work in the default mode, and will either error (unmatched quotes) or be caught by the
    metacharacter blocklist. This is different from `Full` mode, which does go through `sh -c`.

11. **There is NO depth-based tool restriction at runtime.** `tool_policy.rs` — including
    `SUBAGENT_DENY_ALWAYS`, `SUBAGENT_DENY_LEAF`, `filter_tools_by_depth` and `resolve_tool_access`
    — is dead code with zero callers anywhere in the workspace; its only external reference is
    `pub mod tool_policy;` (`openfang-runtime/src/lib.rs:51`). A subagent **can** call
    `cron_create`, `schedule_create`, `hand_activate`, `process_start`, `agent_spawn`. The only
    limiter that runs is `MAX_AGENT_CALL_DEPTH = 5` (`tool_runner.rs:19`, checked at `:1800`).
    Enforce the rest yourself with `tool_blocklist` / `capabilities.tools` on the child manifest.

12. **`GET /api/agents/{id}/tools` does NOT show an agent's tools.** It returns only
    `{"tool_allowlist": [...], "tool_blocklist": [...]}` — both usually `[]` — which reads as "this
    agent has no tools" when the opposite is true. Verified live on two agents. To see what an agent
    actually declares, use **`GET /api/agents/{id}`** (auth required — `/api/agents` is public but
    `/api/agents/{id}` is **401**) and read `capabilities.tools` + `profile`; e.g. the
    `youtube-insights` hand agent returns
    `{"capabilities":{"tools":["shell_exec","file_read","file_write","file_list"]},"profile":"custom"}`.
    `GET /api/tools` (auth required) lists the 65 builtins globally, not per agent. **No endpoint
    exposes the *effective* post-filter tool list** — the only ground truth is
    `available_tools_with_registry` (`kernel.rs:6314`) and the daemon log.
13. **`memory_recall`'s "not found" and `knowledge_query`'s "no matches" are `Ok(...)` results, not
    errors** — `is_error: false` in both cases, just informative text. Don't have downstream logic
    branch on `is_error` to detect an empty memory lookup; check the returned string content.
