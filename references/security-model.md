# OpenFang Security Model — v0.6.9 (source of truth: tag `v0.6.9` / commit `acf2587`, mirrored on
this repo's `main` branch)

Everything below was read out of the v0.6.9 tree and, where marked **[verified live]**, reproduced
against the running container `openfang-openfang-1` (API `http://127.0.0.1:4200`, `OPENFANG_HOME=/data`)
— that container now runs the fork (`main`), not a stock build, so a **[verified live]** tag here
means the stock behaviour described was also confirmed on the fork, not that the fork's own
changes are covered; those are in `SKILL.md`'s "Fork vs stock v0.6.9" table and §1.8 below.
**`/opt/openfang` is not a tag `v0.6.9` checkout** — it runs `main`, ahead of `acf2587`; don't cite
it as stock. Where `docs/security.md` disagrees with the code, the code wins and the disagreement
is called out.

---

## Table of contents

- [0. TL;DR — what actually enforces anything](#0-tldr--what-actually-enforces-anything)
- [1. Config surface (exact names)](#1-config-surface-exact-names)
- [2. The capability type system](#2-the-capability-type-system)
- [3. Enforcement paths](#3-enforcement-paths)
- [4. Approval flow](#4-approval-flow)
- [5. `exec_policy` — the real shell control](#5-exec_policy--the-real-shell-control)
- [6. WASM sandbox](#6-wasm-sandbox)
- [7. Skills & extensions: the actual isolation story](#7-skills--extensions-the-actual-isolation-story)
- [8. Prompt-injection scanner](#8-prompt-injection-scanner)
- [9. Taint labels (issue #1171)](#9-taint-labels-issue-1171)
- [10. Audit trail, Merkle chain, and auth](#10-audit-trail-merkle-chain-and-auth)
- [11. SSRF (relevant to any public deployment)](#11-ssrf-relevant-to-any-public-deployment)
- [12. Gotchas (all verified on this box unless noted)](#12-gotchas-all-verified-on-this-box-unless-noted)
- [13. Hardening checklist — exposing this instance behind Traefik](#13-hardening-checklist--exposing-this-instance-behind-traefik)

## 0. TL;DR — what actually enforces anything

`docs/security.md` §1 advertises "16 independent systems". In v0.6.9 the ones that are load-bearing
for an **LLM agent** (the normal case) are much fewer. Map of reality:

| Layer | Enforced for LLM agents? | Enforced for WASM agents? | Where |
|---|---|---|---|
| `Capability` enum / `CapabilityManager` | **No** (only feeds the tool-list filter) | **Yes**, deny-by-default per host call | `openfang-kernel/src/capabilities.rs`, `openfang-runtime/src/host_functions.rs` |
| `capabilities.tools` allowlist | Yes — becomes `allowed_tools` at dispatch | n/a | `kernel.rs:6303` → `agent_loop.rs:1009` → `tool_runner.rs:133` |
| `tool_allowlist` / `tool_blocklist` | Yes (post-filter on the tool list) | n/a | `kernel.rs:6283-6310` |
| `profile` (ToolProfile) | Yes (only when `capabilities.tools` is empty) | n/a | `kernel.rs:6356-6384` |
| Approval gate | Yes, `shell_exec` only by default | **No** (WASM host calls never hit it) | `tool_runner.rs:146-197` |
| `exec_policy` (metachar + allowlist) | Yes, `shell_exec` / `process_start` only | No | `subprocess_sandbox.rs:126,329` |
| Taint sinks | Yes, two heuristic call sites only | No | `tool_runner.rs:37,63` |
| WASM fuel + epoch | n/a | Yes | `sandbox.rs:178-191` |
| WASM `max_memory_bytes` | n/a | **No — never wired** (#1242) | `sandbox.rs:38` |
| Capability inheritance (anti-escalation) | **No — LLM `agent_spawn` bypasses it** | Yes | `kernel.rs:8027`, `tool_runner.rs:1863` |
| `openfang-runtime/src/tool_policy.rs` (478 lines) | **Dead code — zero callers** | — | see §3.7 |
| Merkle audit chain | Yes (writes), read API partially public | Yes | `openfang-runtime/src/audit.rs` |
| Bearer / dashboard auth | Yes, with a large public-GET surface + one real bypass | — | `openfang-api/src/middleware.rs` |

**Two findings you must internalise before exposing this box:**

1. An LLM agent holding the `agent_spawn` tool can spawn a child with **any** capabilities —
   `spawn_agent_checked()` (which runs `validate_capability_inheritance`) is only reachable from the
   WASM host ABI (§3.5).
2. With `api_key = ""` and `[auth] enabled = true`, an **empty `X-API-Key:` header or an empty
   `?token=` query param authenticates every protected endpoint, including writes** (§10.6,
   [verified live], including an unauthenticated `POST /api/audit/append` that landed in the chain).

---

## 1. Config surface (exact names)

`/data/config.toml` (container) / `~/.openfang/config.toml`. Sections that matter here:

```toml
api_key = "of-..."                 # root level, NOT [api]. Bearer token for the whole API.
api_listen = "0.0.0.0:4200"        # or OPENFANG_LISTEN env (env wins in this deployment)

[auth]                              # dashboard username/password login. Default: disabled.
enabled = true
username = "admin"
password_hash = "$argon2id$v=19$m=19456,t=2,p=1$..."   # openfang auth hash-password
session_ttl_hours = 168

[approval]                          # alias: [approval_policy]
require_approval = ["shell_exec"]  # list of tool names, OR bool: false → [], true → ["shell_exec"]
timeout_secs = 60                  # clamped 10..=300 by validate(); auto-DENY on expiry
auto_approve_autonomous = false    # DEAD FIELD — never read outside tests
auto_approve = false               # only honoured by `openfang start --yolo`, NOT by config load

[exec_policy]                       # also settable per-agent in agent.toml
mode = "allowlist"                 # deny | allowlist (default) | full  (aliases: none/disabled, restricted, allow/all/unrestricted)
safe_bins = ["sleep","true","false","cat","sort","uniq","cut","tr","head","tail","wc","date","echo","printf","basename","dirname","pwd","env"]
allowed_commands = []
timeout_secs = 30
max_output_bytes = 102400
no_output_timeout_secs = 30
shell_env_passthrough = []         # aliases: env_passthrough, env_allowlist ; "*" forwards EVERYTHING

[web.fetch]
ssrf_allowed_hosts = []            # exact host, "*.domain", or CIDR. Metadata IPs never allowed.

[docker]                            # gates the docker_exec TOOL, does not sandbox shell_exec
enabled = false
image = "python:3.12-slim"
network = "none"
memory_limit = "512m"
cpu_limit = 1.0
read_only_root = true
pids_limit = 100
```

Env vars: `OPENFANG_API_KEY`, `OPENFANG_ALLOW_NO_AUTH=1|true|yes|on`, `OPENFANG_HOME`,
`OPENFANG_LISTEN`, `OPENFANG_VAULT_KEY` (extension credential vault).

`[approval]` and a few others are hot-reloadable via `POST /api/config/reload`
(`kernel.rs:4145-4147` calls `approval_manager.update_policy`). `[auth]`, `api_key` and
`api_listen` are **not** — restart required.

**Live instance state:** `/data/config.toml` holds only `api_key` + `[default_model]`. So the
running daemon uses: approval = `["shell_exec"]`, timeout 60 s; `[auth]` disabled;
exec_policy = allowlist with the default `safe_bins` and an empty `allowed_commands`.
Read the key with (note the `=`, see Gotchas):
`K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)`
then `curl -H "Authorization: Bearer $K" ...`. Referred to below as `<API_KEY>`.

---

## 2. The capability type system

`crates/openfang-types/src/capability.rs`

```rust
pub enum Capability {
    FileRead(String), FileWrite(String),            // glob on path
    NetConnect(String), NetListen(u16),             // "host:port" glob
    ToolInvoke(String), ToolAll,
    LlmQuery(String), LlmMaxTokens(u64),
    AgentSpawn, AgentMessage(String), AgentKill(String),
    MemoryRead(String), MemoryWrite(String),
    ShellExec(String), EnvRead(String),
    OfpDiscover, OfpConnect(String), OfpAdvertise,
    EconSpend(f64), EconEarn, EconTransfer(String),
}
```

`capability_matches(granted, required)` (`capability.rs:106`) — same-variant only, plus the one
special case `ToolAll ⊇ ToolInvoke(_)`. Numeric variants compare `granted >= required`
(`LlmMaxTokens`, `EconSpend`); `NetListen` is exact equality.

`glob_matches()` (`capability.rs:190`) is a **hand-rolled 4-case matcher**, not a real glob:
`"*"`, exact, `*suffix` (prefix-star), `prefix*` (suffix-star), and one middle `prefix*suffix`.
There is **no** path-segment awareness: `FileRead("/data/*")` matches `/data/../../etc/shadow`
as a string (the `..` is caught later, in `safe_resolve_path`, not by the capability).
Multiple stars beyond the first are not handled — `"a*b*c"` is treated as prefix `a`, suffix `b*c`.

Variants that are **never constructed anywhere in the workspace**: `NetListen`, `LlmQuery`,
`LlmMaxTokens`, `AgentKill`, `OfpAdvertise`, `EconSpend`, `EconEarn`, `EconTransfer`.
`manifest_to_capabilities()` (`kernel.rs:6948`) can only emit `NetConnect`, `ToolInvoke`,
`MemoryRead`, `MemoryWrite`, `AgentSpawn`, `AgentMessage`, `ShellExec`, `OfpDiscover`,
`OfpConnect`. `ToolAll`, `FileRead`, `FileWrite`, `EnvRead` are **unreachable from any manifest**
— you cannot grant them in TOML.

### 2.1 Agent manifest `[capabilities]`

`crates/openfang-types/src/agent.rs:579` — `ManifestCapabilities`:

```toml
[capabilities]
tools         = ["file_read", "web_fetch"]   # → ToolInvoke(x)  — the ONLY field an LLM agent respects
network       = ["api.example.com:443"]      # → NetConnect(x)  — WASM only
shell         = ["ls", "git"]                # → ShellExec(x)   — WASM only
memory_read   = ["*"]                        # → MemoryRead(x)  — WASM only
memory_write  = ["scratch/*"]                # → MemoryWrite(x) — WASM only
agent_spawn   = false                        # → AgentSpawn     — WASM only (LLM uses the tool)
agent_message = ["*"]                        # → AgentMessage(x)— WASM only
ofp_discover  = false
ofp_connect   = []
```

All list fields go through `serde_compat::vec_lenient`, so a bare string is accepted where a list
is expected. `profile` expansion (`manifest_to_capabilities`, `kernel.rs:6950-6985`): if a
`profile` is set **and** `capabilities.tools` is empty, the profile's implied capabilities become
the base and each non-empty manifest field overrides its counterpart. If `tools` is non-empty the
profile contributes nothing to capabilities.

### 2.2 What the kernel does with the grants

`CapabilityManager` (`openfang-kernel/src/capabilities.rs`) is a `DashMap<AgentId, Vec<Capability>>`
with `grant` / `check` / `list` / `revoke_all`. Grants happen at spawn (`kernel.rs:1747-1749`),
on config re-sync (`kernel.rs:1400-1402`) and are dropped on kill (`kernel.rs:3792`).

`CapabilityManager::check()` has **exactly zero callers** outside its own unit tests. `list()` has
**exactly one caller** in the whole workspace — the `has_tool_all` computation in
`available_tools_with_registry` (`kernel.rs:6359`) — where the match arms are:

```rust
_ if has_tool_all => all_builtins,
_ => all_builtins,          // kernel.rs:6381-6382 — identical, so ToolAll is a no-op
```

**Correction to an earlier pass of this file, which said `list()` had two callers** (this one plus
the WASM sandbox config): grepping `\.capabilities\.list\(` across the workspace now finds exactly
one hit. `execute_wasm_agent` (`kernel.rs:2516-2542`) builds the WASM `SandboxConfig.capabilities`
by calling `manifest_to_capabilities(&entry.manifest)` directly (`kernel.rs:2535`) — it re-derives
capabilities from the manifest fresh rather than reading back what `CapabilityManager::grant`
stored for that agent at spawn. Both paths run the same `manifest_to_capabilities()`, so they agree
in practice; the point is that `CapabilityManager::list()` itself is not on the WASM path at all.

---

## 3. Enforcement paths

### 3.1 WASM agents (real capability enforcement)

`crates/openfang-runtime/src/host_functions.rs`. `dispatch()` routes the guest's `host_call`
JSON `{"method","params"}` to one of: `time_now` (no check), `fs_read`, `fs_write`, `fs_list`,
`net_fetch`, `shell_exec`, `env_read`, `kv_get`, `kv_set`, `agent_send`, `agent_spawn`.
Each handler calls `check_capability(&state.capabilities, &required)` first — deny by default
(`host_functions.rs:57-67`). Notes:

- Path traversal is checked **after** the capability gate: `safe_resolve_path` rejects any
  `Component::ParentDir` then `canonicalize()`s (`:75-87`); writes use `safe_resolve_parent`
  which canonicalizes the parent and re-checks the filename (`:90-117`).
- The capability check uses the **raw, un-canonicalized** path, so `FileRead("/data/*")` +
  `path="/data/../etc/passwd"` is *granted* by the capability and then rejected by the traversal
  check. Defence-in-depth works, but never rely on the glob alone.
- `host_shell_exec` uses `Command::new(command).args(args)` — no shell, so no metacharacter
  problem, but also **no `exec_policy`, no approval gate, no taint check**. A WASM agent with
  `capabilities.shell = ["*"]` executes arbitrary binaries with the daemon's full environment.
- `host_net_fetch` runs `web_fetch::check_ssrf(url, &state.ssrf_allowed_hosts)` **before** the
  `NetConnect` capability check.
- `host_agent_spawn` is the *only* caller of `kernel.spawn_agent_checked(...)`.

### 3.2 LLM agents — the tool list is the capability

`OpenFangKernel::available_tools_with_registry` (`kernel.rs:6314-6487`) builds the list handed to
the model, in order:

1. builtins (minus `browser_*` when `[browser] enabled = false`);
2. filtered by `capabilities.tools` if non-empty and not containing `"*"`, else by `profile`
   (unless profile is `Full`/`Custom`), else everything;
3. `+` skill tools (filtered by `manifest.skills` allowlist, then by declared tools);
4. `+` MCP tools (filtered by `manifest.mcp_servers`, then by declared tools);
5. `manifest.tool_allowlist` retain / `manifest.tool_blocklist` remove (case-insensitive);
6. `shell_exec` removed if the effective `exec_policy.mode == deny`.

`agent_loop.rs:914,1009` then flattens that to `allowed_tool_names` and passes it to
`tool_runner::execute_tool(..., allowed_tools, ...)`, which rejects anything not in the list
(`tool_runner.rs:132-143`, "Permission denied: agent does not have capability to use tool 'x'").
Tool names are first normalised through `normalize_tool_name()` (compat aliases such as
`fs-write` → `file_write`) — the allowlist compare happens **after** normalisation, so aliasing
cannot smuggle a tool past it.

**Consequence:** `[capabilities] shell = ["ls"]` on an LLM agent does nothing. Shell restriction
for LLM agents is `exec_policy` only.

### 3.3 Full builtin tool inventory (v0.6.9)

```
file_read file_write file_list create_directory apply_patch web_fetch web_search shell_exec
agent_send agent_spawn agent_list agent_kill agent_activate memory_store memory_recall agent_find
task_post task_claim task_complete task_list event_publish schedule_create schedule_list
schedule_delete knowledge_add_entity knowledge_add_relation knowledge_query image_analyze
location_get browser_navigate browser_click browser_type browser_screenshot browser_read_page
browser_close browser_scroll browser_wait browser_run_js browser_back media_describe
media_transcribe image_generate cron_create cron_list cron_cancel channel_send hand_list
hand_activate hand_status hand_deactivate a2a_discover a2a_send text_to_speech speech_to_text
docker_exec process_start process_poll process_write process_kill process_list system_time
canvas_present skill_list skill_describe skill_execute
```

Of these, only `shell_exec` requires approval by default. `file_write`, `apply_patch`,
`browser_run_js`, `skill_execute`, `channel_send`, `cron_create`, `agent_spawn` and `docker_exec`
are ungated unless you add them to `[approval] require_approval`.

### 3.4 `docker_exec` is not a sandbox for `shell_exec`

`tool_docker_exec` (`tool_runner.rs:3325-3369`) is a *separate tool*. It checks only
`docker_config.enabled`, then creates a container (workspace bind-mounted) and runs the command.

Be precise about what is and is not missing (an earlier pass of this file overstated it):

- **It DOES get a metacharacter check, one layer down.** `exec_in_sandbox` calls `validate_command`
  (`crates/openfang-runtime/src/docker_sandbox.rs:181`, defined `:65-75`), which delegates to the
  same `contains_shell_metacharacters` used by `shell_exec`.
- **Genuinely missing:** `is_shell_tool()` is `shell_exec | process_start` only
  (`tool_runner.rs:27-29`), so `docker_exec` gets **no `exec_policy` validation** (no allowlist, no
  `safe_bins`); and the default `require_approval = ["shell_exec"]` means **no approval prompt**.
- **Blast radius is bounded by the sandbox**, not by the host: the command runs in an ephemeral
  container whose defaults (`config.rs:635-657`) are `network = "none"`, `read_only_root = true`,
  `memory_limit = "512m"`, `pids_limit = 100`. This is not host-level arbitrary execution.

Still: if you set `[docker] enabled = true`, every agent gets an un-approved, un-allowlisted command
tool. Blocklist it or add it to `require_approval`.

### 3.5 Privilege escalation: the inheritance check that isn't wired for LLMs

- `validate_capability_inheritance(parent, child)` (`capability.rs:171`) requires every child
  capability to be covered by a parent grant.
- `spawn_agent_checked()` (`kernel.rs:8027-8050`) parses the child manifest, derives its caps and
  runs that check. Its **only** caller is `host_agent_spawn` (`host_functions.rs:415`).
- The LLM-facing tool `agent_spawn` calls `tool_agent_spawn` → `kh.spawn_agent(manifest_toml,
  parent_id)` (`tool_runner.rs:1863`) — the *unchecked* path. `spawn_agent` grants whatever
  `manifest_to_capabilities()` derives (`kernel.rs:1747-1749`) with no parent comparison.
- `/api/security` nevertheless reports `"privilege_escalation_prevention": true` [verified live].

Mitigation: keep `agent_spawn` out of `capabilities.tools` / put it in `tool_blocklist` for any
agent exposed to untrusted input, or add `agent_spawn` to `[approval] require_approval`.

### 3.6 Sub-agent depth

`tool_runner.rs:19` `MAX_AGENT_CALL_DEPTH = 5`, tracked in the task-local `AGENT_CALL_DEPTH`.
This is the only depth limiter that runs.

### 3.6b `POST /mcp` executes tools with **no** agent context — no tool list, no exec_policy, no workspace

`mcp_http` (`routes.rs:7216-7311`) is the one place `tool_runner::execute_tool` is called outside an
agent loop, and it passes almost every security parameter as `None`:

```rust
execute_tool("mcp-http", tool_name, &arguments,
    Some(&kernel_handle),
    None,               // allowed_tools   -> no capability filter at all
    None,               // caller_agent_id -> approvals show agent "unknown"
    ..., 
    None,               // workspace_root  -> no workspace sandbox
    ..., 
    None,               // exec_policy     -> allowlist/safe_bins never validated
    ...)
```

Consequences, all reproduced live against this instance with a valid `Authorization: Bearer <API_KEY>`:

| Call | Result |
|---|---|
| `tools/list` | **65** tools — the full builtin surface, `shell_exec`/`agent_spawn`/`docker_exec` included |
| `tools/call file_read {"path":"/etc/hostname"}` | `isError: false`, returns the file. `workspace_root = None` ⇒ `resolve_file_path` only rejects `..` components, so **any absolute path the daemon UID can read** is fair game |
| `tools/call file_read {"path":"/data/secrets.env"}` | **returns `HYPERFUSION_API_KEY`**. The same call on `/data/config.toml` returns the daemon `api_key`. There is no redaction on this path |
| `tools/call shell_exec {"command":"id"}` | approval-gated (default `require_approval = ["shell_exec"]`), blocks 60 s, then `Execution denied`. The pending record shows `"agent_id":"unknown"` |

So the API key is not merely an API credential: **anything holding it can read every file the daemon
can read, in one unauthenticated-to-the-agent-layer call.** And because `exec_policy` is `None`, the
`if let Some(policy) = exec_policy` allowlist guard in the `"shell_exec"` arm
(`tool_runner.rs:262`) never runs — an operator who approves one of these prompts gets **unfiltered**
binary execution, not `safe_bins`. (`contains_shell_metacharacters` still applies.)

Mitigation: `POST /mcp` is not in the public allowlist, so it needs the key — keep the key secret,
block `/mcp` at the edge if you do not use it, and never hand the API key to an IDE/MCP client you
would not give a shell to.

### 3.7 Dead policy engine

`crates/openfang-runtime/src/tool_policy.rs` (478 lines: `ToolPolicy`, `resolve_tool_access`
deny-wins resolution, group expansion `@name`, `subagent_max_depth`, `filter_tools_by_depth`,
`SUBAGENT_DENY_ALWAYS`, `SUBAGENT_DENY_LEAF`) is declared in `lib.rs:51` and **called from
nowhere**. There is no `[tool_policy]` config section. Do not plan around it.

### 3.8 Also configured-but-unenforced

`ResourceQuota` (`agent.rs:250`): **only** `max_tool_calls_per_minute` (default 60) and
`max_network_bytes_per_hour` (default 100 MB) have zero non-type/non-test readers.
`max_memory_bytes` — see #1242.
`max_cost_per_{hour,day,month}_usd` **are** enforced (`openfang-kernel/src/metering.rs:27`).

**`max_llm_tokens_per_hour` IS enforced** (an earlier pass of this file said otherwise): agents
register with the scheduler at spawn (`kernel.rs:1751-1753`), and `scheduler.check_quota()` runs
**before every turn** (`kernel.rs:1941` and `:2032`), erroring out via
`crates/openfang-kernel/src/scheduler.rs:91-96`. Caveat worth knowing: the rolling token window is an
in-memory `DashMap` keyed on `Instant`, so **it resets on every daemon restart** — the quota is real
but not durable.

---

## 4. Approval flow

### 4.1 Mechanics

`crates/openfang-kernel/src/approval.rs` + `crates/openfang-types/src/approval.rs`.

- `ApprovalManager { pending: DashMap<Uuid, PendingRequest>, recent: VecDeque<ApprovalRecord>, policy: RwLock<ApprovalPolicy> }`.
- `MAX_PENDING_PER_AGENT = 5` (`approval.rs:13`), `MAX_RECENT_APPROVALS = 100` (`:15`).
- `request_approval()` inserts a oneshot sender and awaits with `tokio::time::timeout(policy.timeout_secs)`.
  Timeout ⇒ `ApprovalDecision::TimedOut` ⇒ treated as **deny** by the caller.
- 6th concurrent pending request for the same agent returns `Denied` immediately (`:60-63`).
- `resolve(id, decision, decided_by)` is what the API calls; unknown id ⇒ `Err`.
- Risk classification is a hard-coded match (`approval.rs:161-168`):
  `shell_exec → Critical`, `file_write | file_delete → High`, `web_fetch | browser_navigate → Medium`, everything else `Low`.
  It is **display metadata only** — nothing gates on `RiskLevel`.
- `ApprovalRequest::validate()` (tool name ≤64 alphanum/underscore, description ≤1024,
  action_summary ≤512, timeout 10..=300) exists but is **not called** on the kernel-generated
  request path (`kernel.rs:7767-7800` builds the struct and submits it directly).

### 4.2 The gate in the tool path

`tool_runner.rs:146-197`, executed after the `allowed_tools` check and **before** the per-tool match:

```rust
let exec_policy_bypasses_approval = is_shell_tool(tool_name)          // shell_exec | process_start
    && exec_policy.is_some_and(|p| p.mode == Full
        || (p.mode == Allowlist && p.allowed_commands.iter().any(|c| c == "*")));
if let Some(kh) = kernel {
    if !exec_policy_bypasses_approval && kh.requires_approval(tool_name) { ... }
}
```

On deny/timeout the tool returns
`"Execution denied: '<tool>' requires human approval and was denied or timed out."`, and
`agent_loop.rs:1008-1026 / 2232-2250` injects a system nudge telling the user to
"set auto_approve = true in [approval] section of config.toml" — **that advice does not work**
(see §4.4).

### 4.3 Bypasses (all verified in code)

| Bypass | Where |
|---|---|
| Agent tagged `hand:*` → **every** approval auto-granted, silently | `kernel.rs:7776-7784` |
| `exec_policy.mode = "full"` (or `allowlist` + `allowed_commands = ["*"]`) → `shell_exec`/`process_start` skip the gate (#772) | `tool_runner.rs:152-158` |
| WASM host calls never consult the approval manager at all | `host_functions.rs` |
| `docker_exec`, `browser_run_js`, `file_write`, `apply_patch` … are not in the default require list | `approval.rs:188` |
| `openfang start --yolo` clears `require_approval` at boot | `openfang-cli/src/main.rs:1565-1568` |

### 4.4 `auto_approve` is a trap

`ApprovalPolicy::apply_shorthands()` (`types/approval.rs:236`) clears `require_approval` when
`auto_approve == true`, but its **only non-test caller** is `cmd_start` under `--yolo`
(`main.rs:1567`). `openfang_kernel::config::load_config()` never calls it. So
`[approval] auto_approve = true` in `config.toml` is a **no-op**.
To actually disable approvals from config use the boolean form of the real field:

```toml
[approval]
require_approval = false      # → [] via the custom deserializer (types/approval.rs:199-232)
# or: require_approval = []
```

`auto_approve_autonomous` is referenced only in tests — dead.

### 4.5 API / CLI

```
GET  /api/approvals                    # pending + last 50 resolved   ← PUBLIC, no auth [verified live]
POST /api/approvals                    # inject a manual request (auth)
POST /api/approvals/{id}/approve       # auth; decided_by = "api"
POST /api/approvals/{id}/reject        # auth
```
CLI (`openfang approvals list|approve <id>|reject <id>`) wraps those.

The pending list includes `action_summary` = `"<tool>: <first 200 chars of the JSON input>"`
(`tool_runner.rs:170-175`, truncated again to 512 chars at `kernel.rs:7601`) — i.e. an
unauthenticated caller can read the commands your agents are trying to run.

### 4.6 Upstream issues, checked against v0.6.9

- **#1139** *"Move approvals into chat & remove/extend timeout"* — **open, not implemented.**
  Approvals still live only in the dashboard `approvals.js` panel (`webchat.rs:182`) and the
  `/api/approvals` REST surface. The timeout is still `ApprovalPolicy::timeout_secs`, hard-clamped
  to **10..=300 s** by `validate()` (`types/approval.rs:26-29`) — you cannot configure "no timeout";
  the maximum wait is 5 minutes, after which the tool call is auto-denied. There is no push
  notification of any kind when a request appears.
- **#1078** *"Pre-execution authorization layer (Sigil / signed intent attestation)"* —
  **open, design-only.** Accurate diagnosis of the architecture: the gate at `tool_runner.rs:146`
  runs *inside* the agent's execution path, after the model has already chosen the call, and its
  decision is a plain `bool` returned over an in-process channel. Nothing is signed. The Ed25519
  machinery that does exist (`types/manifest_signing.rs`) covers skill/agent manifests at install
  time only (§7.3).
- **#1180** *"Capability gate, MCP bridge, approval push surface"* — **open; none of A/B/C landed.**
  - (A) no `openfang-mcp-bridge` crate, no `OPENFANG_BRIDGE_ENABLED`, no `bridge.sock`.
  - (B) the shell pre-gate was **not** lifted: `contains_shell_metacharacters` and
    `validate_command_allowlist` still run inside the `"shell_exec"` match arm
    (`tool_runner.rs:247-276`), i.e. **after** the approval gate at line 167. The described waste
    is real and reproducible: an operator gets prompted for
    `rm -rf / ; echo lol`, approves it, and the tool then refuses it with
    `"shell_exec blocked: command contains semicolon command chaining"`.
  - (C) `ApprovalManager` has no broadcast channel and `channel_bridge` has no approval surfacer;
    `list_pending()` polling is the only way to notice a request.
  Note #1180's premise that `process_start` has its own validators is correct — `tool_process_start`
  (`tool_runner.rs:3339-3400`) checks metacharacters in the command *and* every arg, plus
  `validate_command_allowlist` (regression tests for #919 at `tool_runner.rs:4641-4746`).

---

## 5. `exec_policy` — the real shell control

`crates/openfang-runtime/src/subprocess_sandbox.rs`

`contains_shell_metacharacters()` (`:126`) rejects, unconditionally and in **every** mode
including `full`: `` ` ``, `$(`, `${`, `;`, `|`, `>`, `<`, `{`, `}`, `\n`, `\r`, `\0`, `&`.
Note the absence of `'`, `"`, `*`, `?`, `~`, `!`, `#`.

`validate_command_allowlist()` (`:329`):
- `deny` → always error.
- `full` → always ok (logs a warning).
- `allowlist` → metachar check first (skipped when the outer command is a known shell wrapper:
  `powershell|pwsh|cmd|bash|sh|zsh`), then every segment split on `&&`/`||`/`|`/`;` is reduced to
  its basename and must be in `safe_bins` or `allowed_commands`; then, for shell wrappers, the
  inline script's commands are validated too (#794 fix at `:381-397`).

Environment isolation for `shell_exec` subprocesses: `env_clear()` + `SAFE_ENV_VARS`, extended by
`exec_policy.shell_env_passthrough` (aliases `env_passthrough`, `env_allowlist`). Setting it to
`["*"]` forwards the whole daemon environment — including every provider API key.

---

## 6. WASM sandbox

`crates/openfang-runtime/src/sandbox.rs`. **Scope correction:** this sandbox runs **WASM
*agents* only** — `manifest.module = "wasm:<file>"`, dispatched by
`OpenFangKernel::execute_wasm_agent` (`kernel.rs:2490-2537`). It is **not** used for skills and
**not** used for extensions:

- `SkillRuntime::Wasm` returns `SkillError::RuntimeNotAvailable("WASM skill runtime not yet
  implemented")` (`openfang-skills/src/loader.rs:34-36`). Skills execute as plain Python/Node/Shell
  subprocesses (§7).
- Extensions (`openfang-extensions`) are MCP-server templates + an AES-256-GCM credential vault;
  no WASM anywhere.

Guest ABI: exports `memory`, `alloc(i32)->i32`, `execute(i32,i32)->i64` (packed `ptr<<32|len`);
imports `openfang.host_call(i32,i32)->i64` and `openfang.host_log(i32,i32,i32)`. No WASI is linked.

Dual metering (`sandbox.rs:177-191`):
- **Fuel** — `Config::consume_fuel(true)`, `store.set_fuel(config.fuel_limit)`;
  `fuel_limit = manifest.resources.max_cpu_time_ms * 100_000` (`kernel.rs:2511`), default
  30 000 ms → 3 × 10⁹ fuel. Exhaustion ⇒ `Trap::OutOfFuel` ⇒ `SandboxError::FuelExhausted`.
- **Epoch** — `store.set_epoch_deadline(1)` plus a watchdog thread that sleeps `timeout_secs`
  then calls `engine.increment_epoch()`. Precisely: the 30 s is a **default**, not a literal —
  `sandbox.rs:187` reads `config.timeout_secs.unwrap_or(30)`. It is nevertheless fixed in practice
  because `kernel.rs:2514` always passes `Some(30)` and nothing feeds
  `SandboxConfig.timeout_secs: Option<u64>` from config.

### 6.1 Hole #1 — `max_memory_bytes` is decorative (issue #1242, confirmed present)

`sandbox.rs:38`:
```rust
/// Maximum WASM linear memory in bytes (reserved for future enforcement)
pub max_memory_bytes: usize,
```
The value flows manifest → `ResourceQuota.max_memory_bytes` (default **256 MB**,
`agent.rs:272`) → `SandboxConfig` (`kernel.rs:2512`) → and stops. There is **no
`store.limiter(...)`** call and no `ResourceLimiter` impl anywhere in the workspace (grep
`limiter` = 0 hits in `openfang-runtime`). A WASM guest can `memory.grow` up to wasmtime's
default ceiling (~4 GiB for wasm32) regardless of the manifest. Fuel/epoch bound CPU, not RSS.
The issue's suggested fix (a `ResourceLimiter` with `memory_growing`) is still the right one.

### 6.2 Hole #2 — watchdog threads leak per execution (issue #1241, confirmed present)

`sandbox.rs:188-191`:
```rust
let _watchdog = std::thread::spawn(move || {
    std::thread::sleep(std::time::Duration::from_secs(timeout));
    engine_clone.increment_epoch();
});
```
The `JoinHandle` is dropped immediately, detaching the thread; there is no cancellation flag, so
the thread always sleeps the **full 30 s** even when the guest finished in 1 ms, then bumps the
epoch of an `Engine` whose `Store` is long gone. Concurrency × 30 s of ~2 MB stacks each is the
steady-state cost. (Issue text cites `sandbox.rs:203-206`, which was v0.6.4 line numbering.)
Functionally harmless — a stale `increment_epoch()` on a shared `Engine` can only nudge the epoch
counter forward, and every new `Store` sets its own relative deadline — but it is a real
thread-count amplifier under load.

### 6.3 Other sandbox notes

- `host_call` allocates the response inside guest memory via the guest's own `alloc`, then writes
  with a bounds check (`sandbox.rs:346-349`). A malicious `alloc` returning a huge pointer gets a
  `bail!`, not a host overwrite.
- Result unpacking checks `result_ptr + result_len > mem_data.len()` (`:262`) — `usize` addition,
  so a hostile `packed` value with `ptr` near `usize::MAX` could in principle wrap on 32-bit hosts;
  on 64-bit (the deployment) `ptr` and `len` are each ≤ 2³², so no wrap.
- `GuestState.capabilities` is a snapshot taken at execution start — revoking a capability
  mid-run does not affect an in-flight WASM call.

---

## 7. Skills & extensions: the actual isolation story

### 7.1 Execution

`openfang-skills/src/loader.rs` — Python (`execute_python:54`), Node (`:160`), Shell (`:306`)
run as **ordinary subprocesses of the daemon**, `current_dir(skill_dir)`, JSON payload on stdin,
JSON on stdout. Isolation is exactly `cmd.env_clear()` plus `PATH`, `HOME`
(`+SYSTEMROOT`,`TEMP` on Windows) and `PYTHONIOENCODING=utf-8`. That is the whole sandbox:

- no timeout on `child.wait_with_output()` in the loader (the caller's per-tool timeout in
  `agent_loop.rs` is the only bound),
- no memory/CPU/pid limits, no namespace/seccomp, no filesystem restriction,
- full network access,
- `skill_execute` is a normal builtin tool and is **not** in the default approval list.

Treat any installed skill as code running with daemon privileges.

### 7.2 Skill config injection

`openfang-skills/src/config_injection.rs`. Declared `config:` vars resolve from
`[skills.<name>]` in config.toml → `var.env` → `var.default`. The rendered block is appended to
the skill's Markdown and injected into the **LLM system prompt**; only names matching
`*_token`, `*_key`, `*_secret`, `password` are redacted in that rendering — anything else
(e.g. `webhook_url`, `session_id`) goes to the model in cleartext. The unredacted map is what the
runtime hands the skill.

### 7.3 Install-time supply-chain gates

- `InstallOptions { require_signed, allowed_signer_keys }` (`openfang-skills/src/installer.rs:30`).
  When `require_signed`, an Ed25519 `SignedManifest` envelope must exist as `signature.json`,
  `skill.toml.sig.json` or `SKILL.md.sig.json`, verify against the manifest bytes (BOM/CRLF
  normalised) and, if `allowed_signer_keys` is non-empty, match a pinned hex pubkey; otherwise the
  skill dir is deleted and `SkillError::SecurityBlocked` is returned. Symlinked signature/manifest
  files are refused (`safe_regular_file_in:87`).
- **`require_signed` defaults to `false`, and the CLI has no `--require-signed` flag** — the only
  way to set it is the JSON body of `POST /api/skills/install`
  (`routes.rs:3706-3709`: `{"name": "...", "require_signed": true, "allowed_signer_keys": ["<64 hex>"]}`).
  `openfang skill install` is therefore always TOFU.
- `SignedManifest::sign/verify` (`types/manifest_signing.rs`) signs the **hex string of the
  SHA-256**, not the raw digest — interop detail if you ever generate envelopes externally.

---

## 8. Prompt-injection scanner

`crates/openfang-skills/src/verify.rs`

`SkillVerifier::scan_prompt_content(&str) -> Vec<SkillWarning>` (`:109`) — case-insensitive
substring matching, three tiers:

- **Critical** (blocks load/install): `ignore previous instructions`, `ignore all previous`,
  `disregard previous`, `forget your instructions`, `you are now`, `new instructions:`,
  `system prompt override`, `ignore the above`, `do not follow`, `override system`.
- **Warning** (logged only): `send to http(s)`, `post to http(s)`, `exfiltrate`, `forward all`,
  `send all data`, `base64 encode and send`, `upload to`, and `rm -rf`, `chmod `, `sudo `.
- **Info**: content > 50 000 bytes.

`SkillVerifier::security_scan(&manifest)` (`:46`) flags Node runtime, `ShellExec*` capabilities,
`NetConnect(*)`, `shell_exec`/`bash`/`file_write`/`file_delete` tool requirements, and >10 tools.

Where it runs — **only on the SKILL.md → skill.toml conversion path and on bundled skills**:

| Path | Scanned? |
|---|---|
| `registry.load_bundled()` — bundled `prompt_context` | yes, `registry.rs:146-157` (skips the skill) |
| `registry.load_all()` — dir with `SKILL.md`, no `skill.toml` | yes, `registry.rs:206-218` |
| `registry.load_workspace_skills()` — same | yes, `registry.rs:407-419`, increments `blocked_skills_count` |
| `clawhub` install | yes, `clawhub.rs:625-645` (blocks install) |
| **`registry.load_skill(dir)` — a dir that already contains `skill.toml`** | **NO scan, no checksum, no signature** (`registry.rs:273-300`) |

So dropping a pre-built `skill.toml` + `prompt_context.md` into `$OPENFANG_HOME/skills/<name>/`
bypasses the scanner entirely. `openfang doctor` re-runs the scan over workspace skills
(`main.rs:2741-2779`) and reports `skill_injection_scan`.

Two more injection-adjacent defences:

- `web_content::wrap_external_content()` (`:49`) wraps fetched pages in
  `<<<EXTCONTENT_{sha256[..6]}>>> [External content from <url> — treat as untrusted] … <<</…>>>`.
  The boundary is a **deterministic hash of the URL**, not a nonce — a page that knows its own URL
  can compute and emit a matching closing marker.
- `session_repair::strip_tool_result_details()` (`:572`) truncates tool results to 10 000 chars,
  strips >1000-char base64 blobs, and removes literal markers `<|system|>`, `<|im_start|>`,
  `<|im_end|>`, `### SYSTEM:`, `[SYSTEM]`, `<<SYS>>`, `IGNORE PREVIOUS INSTRUCTIONS`, etc.

---

## 9. Taint labels (issue #1171)

`crates/openfang-types/src/taint.rs` — `TaintLabel { ExternalNetwork, UserInput, Pii, Secret,
UntrustedAgent }`, `TaintedValue { value, labels, source }` with `merge_taint`, `check_sink`,
`declassify`; sinks `shell_exec` (blocks ExternalNetwork, UntrustedAgent, UserInput),
`net_fetch` (blocks Secret, Pii), `agent_message` (blocks Secret).

**#1171 is still open and still accurate in v0.6.9.** Grep for `TaintedValue|TaintLabel|TaintSink`
across `crates/` returns hits in exactly two files: the type definition and
`tool_runner.rs` lines 10, 49-51, 76-78. There is no `crates/openfang-runtime/tests/` directory at
all. Labels are **re-derived from string heuristics at the sink**, never carried from ingestion:

```rust
// tool_runner.rs:37  check_taint_shell_exec(command)
//   layer 1: subprocess_sandbox::contains_shell_metacharacters
//   layer 2: if command contains "curl ", "wget ", "| sh", "| bash", "base64 -d", "eval "
//            → synthesise ExternalNetwork → check against TaintSink::shell_exec() → always violates
// tool_runner.rs:63  check_taint_net_fetch(url)
//   if url contains api_key= apikey= token= secret= password= "Authorization:"
//            → synthesise Secret → check against TaintSink::net_fetch() → always violates
```

Practical consequences:

- `web_fetch` on any URL containing `token=` (an OAuth callback, a presigned S3 link, a Slack
  file URL) is refused with `Taint violation: …` regardless of provenance.
- `shell_exec "curl https://example.com"` is refused **even when `curl` is in
  `exec_policy.allowed_commands`**, because the taint heuristic runs after the allowlist
  (`tool_runner.rs:277-287`). Only `exec_policy.mode = "full"` skips it (`is_full_exec` at `:278`).
- `TaintSink::agent_message()` is defined and never used.
- Nothing labels Matrix/Telegram/STT/web input as `UserInput`, so `TaintSink::shell_exec`'s
  `UserInput` block never fires in practice.

---

## 10. Audit trail, Merkle chain, and auth

### 10.1 The chain

`crates/openfang-runtime/src/audit.rs`.
`AuditEntry { seq, timestamp(RFC3339), agent_id, action, detail, outcome, prev_hash, hash }`;
`hash = SHA256(seq ‖ timestamp ‖ agent_id ‖ action(Debug string) ‖ detail ‖ outcome ‖ prev_hash)`
(`compute_entry_hash:61-79`), genesis `prev_hash = "0"*64`. `AuditAction` variants: `ToolInvoke,
CapabilityCheck, AgentSpawn, AgentKill, AgentMessage, MemoryAccess, FileAccess, NetworkAccess,
ShellExec, AuthAttempt, WireConnect, ConfigChange`.

`AuditLog::with_db(conn)` loads the `audit_entries` table (schema V8) at boot, verifies the chain
and logs `Audit trail integrity check FAILED` (error level) or `chain integrity OK`.
`verify_integrity()` recomputes every hash and reports the first `chain break at seq N` /
`hash mismatch at seq N`. `record()` holds both mutexes, INSERTs, pushes, advances the tip.

Reality check on what is actually recorded — **corrected 2026-08-10; an earlier pass of this file
said `AgentSpawn` is never emitted, which is wrong.** The complete set of non-test `record()` call
sites is:

| Site | Action |
|---|---|
| `kernel.rs:1272`, `:1284` | `ConfigChange` — HAND.toml load / reload SHA-256 |
| `kernel.rs:1790` (`spawn_agent_with_parent`) | **`AgentSpawn`** |
| `kernel.rs:1981`, `:1995` (`send_message`, ok and err) | **`AgentMessage`** |
| `kernel.rs:3808` (`kill_agent`) | **`AgentKill`** |
| `routes.rs:123` | `AuthAttempt` — **not** dashboard login: fires on a failed Ed25519 signature check on a `signed_manifest` passed to `POST /api/agents`, regardless of whether `[auth]` is enabled at all |
| `routes.rs:12782`, `:12811` | `AuthAttempt` — dashboard login, fail and success (`auth_login`, only reachable when `[auth].enabled`, which 404s the route otherwise) |
| `routes.rs:897`, `:11175`, `:11449` | `ConfigChange` — config/dashboard writes (`shutdown`, `config_reload`, dashboard config-set) |
| `routes.rs:3974-3995` | whatever `POST /api/audit/append` was told, defaulting to `ToolInvoke` |

**Correction to an earlier pass of this file:** it listed `routes.rs:123` as "dashboard login" and
claimed **four** `ConfigChange` sites in `routes.rs` (`:891`, `:10976`, `:11231`, `:12564`). Neither
holds up against the current tree: `:123` is a manifest-signature failure inside agent spawn, not
login (the real login `AuthAttempt` sites are `:12782`/`:12811`, found by grepping every
`audit_log.record(` call in `routes.rs` — six total, three `AuthAttempt`, three `ConfigChange`),
and there are only **three** `ConfigChange` sites in `routes.rs`, not four — grep
`audit_log\.record\(` in `routes.rs` to reproduce. Whether the fourth site was refactored away or
the original count was simply wrong could not be determined from this tree alone.

**Genuinely never emitted by the runtime:** `ToolInvoke`, `ShellExec`, `CapabilityCheck`,
`FileAccess`, `NetworkAccess`, `MemoryAccess`, `WireConnect` — they exist only as `AuditAction`
variants reachable through `/api/audit/append`. So you get an **agent-lifecycle** trail, not a
**tool-call** trail.

[verified live 2026-08-10: 201 entries — `ConfigChange` 157, `AgentMessage` 18, `AgentSpawn` 14,
`AgentKill` 12, `AuthAttempt` 0 (dashboard auth is disabled on this box).]

### 10.2 #1172 — HAND.toml reload hashing: **implemented in v0.6.9** (issue still open upstream)

`HandRegistry` gained `audit_callback` + `emit_hand_loaded_audit()`
(`openfang-hands/src/registry.rs:61-101`), invoked from all five load/upsert/reload sites
(`:164,226,258,306,325`). The kernel backfills bundled hands at boot and installs the callback
(`kernel.rs:1255-1288`), emitting
`HAND.toml load hand=<id> sha256=<hex>` and `HAND.toml reload hand=<id> sha256=<hex>` as
`ConfigChange`/`ok`. [verified live — both forms present in the chain]

### 10.3 #1174 — `POST /api/audit/append`: **implemented in v0.6.9** (issue still open upstream)

Route registered at `server.rs:398-401`, handler `routes::audit_append` (`routes.rs:3944-4041`).

```bash
curl -s -X POST http://127.0.0.1:4200/api/audit/append \
  -H "Authorization: Bearer <API_KEY>" -H 'content-type: application/json' \
  -d '{"event_type":"config_change","agent_id":"wrapper","detail":"HAND.toml reload sha256=abc","outcome":"ok"}'
# → {"status":"appended","seq":<u64>,"hash":"<sha256>","tip":"<sha256>"}
```

Fields: `event_type` (case-insensitive alias set, unknown ⇒ falls back to `ToolInvoke` with a
warning), `agent_id` (empty ⇒ `"external-wrapper"`), `detail`, optional `outcome` (default `"ok"`),
optional `signing_context` (appended as `signer=<ctx>`), optional `payload` (JSON, serialised and
capped at 8 KiB, appended as `payload=<json>`). Every string field is capped at 16 KiB ⇒ 413.
The response shape differs slightly from the issue's proposal (adds `status` and `tip`).

Read side: `GET /api/audit/recent?n=N` and `GET /api/audit/verify`
(`{"entries":N,"tip_hash":"…","valid":true}`), both auth-required.

> **The query parameter is `n`, not `limit`** (`routes.rs:5416-5420`: `params.get("n")`, default
> **50**, `.min(1000)`). `?limit=500` is silently ignored and you get 50 rows — verified live:
> `?limit=500` → 50 entries, `?n=500` → 201 entries. Any pagination you build on `limit` will
> silently truncate your export.

### 10.4 …but the chain is readable without auth

`GET /api/logs/stream` is in the public list (`middleware.rs:180`) and its handler
(`routes.rs:5488-5571`) polls `audit_log.recent(200)` every second and SSEs
`{seq,timestamp,agent_id,action,detail,outcome,hash}` — the same rows `/api/audit/recent` protects.
[verified live: `curl -N http://127.0.0.1:4200/api/logs/stream` with no credentials streams the chain.]
Supports `?level=` and `?filter=` query filters.

### 10.5 Auth model

Two independent mechanisms, both handled by one middleware (`openfang-api/src/middleware.rs:71`):

1. **Top-level `api_key` → Bearer.** Accepted as `Authorization: Bearer <key>`, `X-API-Key: <key>`,
   or `?token=<urlencoded key>` (for EventSource/WebSocket clients). Compared with
   `subtle::ConstantTimeEq` after a length check.
2. **`[auth]` dashboard login.** `POST /api/auth/login {username,password}` →
   constant-time username compare + `argon2::Argon2::default().verify_password` against
   `password_hash`; on success issues `openfang_session=<token>; Path=/; HttpOnly; SameSite=Strict; Max-Age=<ttl>`
   and also returns the token in the JSON body. Token format
   `base64("<username>:<expiry_unix>:<hmac_sha256_hex>")` (`session_auth.rs:10-18`), verified with a
   constant-time compare and an expiry check. **Session secret = `api_key` if non-empty, else
   `password_hash`** (`server.rs:147-155`, mirrored in `routes.rs:12856-12862` and `ws.rs:328-335`).
   Both login outcomes are written to the audit chain as `AuthAttempt`.

`openfang auth hash-password` (`main.rs:6294-6314`) prompts twice, calls
`openfang_api::session_auth::hash_password` → `Argon2::default()` (Argon2id, v19, m=19456, t=2, p=1)
with a random `SaltString`, and prints a ready-to-paste `[auth]` block. `verify_password` parses
the PHC string; a legacy SHA-256 hex hash is rejected outright, and `server.rs:111-118` logs a
startup warning if `enabled && !password_hash.starts_with("$argon2")`.

Fail-closed rule (`middleware.rs:197-212`, issue #1034): if `api_key` is empty **and**
`[auth].enabled == false`, only loopback peers (`ConnectInfo` IP `is_loopback()`, default-deny when
`ConnectInfo` is absent) get through; everyone else gets 401 unless `OPENFANG_ALLOW_NO_AUTH=1`.
`/api/shutdown` skips token auth entirely when the peer is loopback (`:88-90`).

**The public (no-auth) route list** (`middleware.rs:145-182`) — non-GET always requires auth:

```
/  /logo.png  /favicon.ico  /api/health  /api/health/detail  /api/status  /api/version
GET /.well-known/agent.json   GET /a2a/*
GET /api/agents  /api/profiles  /api/config  /api/config/schema  /api/uploads/*
GET /api/models  /api/models/aliases  /api/providers  /api/budget  /api/budget/agents(/*)
GET /api/network/status  /api/a2a/agents  /api/approvals  /api/approvals/*
GET /api/channels  /api/hands  /api/hands/active  /api/hands/*
GET /api/skills  /api/skills/*/config  /api/sessions  /api/integrations(+/available,/health)
GET /api/workflows  /api/cron/*
    /api/logs/stream            (any method)
    /api/providers/github-copilot/oauth/*
    /api/auth/login  /api/auth/logout  GET /api/auth/check
```

`docs/configuration.md:309` claims these "do not expose agent data or accept commands" and
`docs/security.md` §17.2 claims `/api/health/detail` "requires authentication". Both are false.
[verified live, unauthenticated] `/api/status` returns the full agent roster with model/provider,
`home_dir`, `api_listen`; `/api/agents` returns per-agent state; `/api/sessions` returns session
IDs and message counts; `/api/health/detail` returns agent count, uptime, panic/restart counters;
`/api/config` returns `home_dir`, `data_dir`, provider/model and `api_key: "***"` (redacted).

### 10.6 **Auth bypass: empty credential vs empty `api_key`** [verified live on v0.6.9]

When `api_key` is empty **and** `[auth].enabled = true`, the early fail-closed branch at
`middleware.rs:198` is skipped (because `auth_enabled` is true), execution falls through to
`api_key = ""`, and the comparison becomes:

```rust
let header_auth = api_token.map(|token| {
    if token.len() != api_key.len() { return false; }   // 0 == 0
    token.as_bytes().ct_eq(api_key.as_bytes()).into()   // ct_eq(b"", b"") == true
});
if header_auth == Some(true) || query_auth == Some(true) { return next.run(request).await; }
```

An **empty credential therefore authenticates**. Reproduced against a scratch daemon
(`api_key = ""`, `[auth] enabled = true`, port 4299):

```
GET /api/audit/recent                          → 401
GET /api/audit/recent  X-API-Key: (empty)      → 200  + full audit chain
GET /api/audit/recent?token=                   → 200  + full audit chain
POST /api/audit/append?token=  {json}          → 200  {"status":"appended","seq":10,...}   ← unauthenticated WRITE
GET /api/audit/recent  Authorization: "Bearer nope"  → 401 "Invalid API key"
```

`Authorization: Bearer ` (empty token) happens to fail because hyper trims the trailing space, so
`strip_prefix("Bearer ")` misses; `X-API-Key:` and `?token=` have no such protection.
The WebSocket path is **not** affected — `check_ws_auth` (`ws.rs:223-255`) returns early for
`api_key.is_empty()` and demands a valid session cookie (that early return was added for #1189;
the HTTP middleware never got the same treatment).

**Therefore: never run `[auth]` as your only authentication. Always set `api_key` as well.**

### 10.6b Cron `delivery_targets` is an unencrypted secret store behind a public read endpoint

**SEVERE (mechanism), previously live on this box.** `middleware.rs:181` whitelists `GET /api/cron/*`, and the cron
handler returns the **entire** `JobMeta` record — including `delivery_targets`. A webhook target
looks like this:

```json
{"type":"webhook",
 "url":"http://127.0.0.1:4200/api/agents/<id>/session/reset",
 "auth_header":"Bearer <API_KEY>"}
```

The job that carried this has since been deleted; `$OPENFANG_HOME/cron_jobs.json` is `[]` and
`GET /api/cron/jobs` returns `{"jobs":[],"total":0}` as of 2026-08-10. The exposure is structural,
not historical — any future job with an `auth_header` reproduces it, and the mechanism below was
re-verified after the deletion. Because the key was served unauthenticated on the Tailnet for as long
as that job existed, rotating the daemon `api_key` is the conservative call.

That `auth_header` is stored verbatim in `$OPENFANG_HOME/cron_jobs.json` and served **with no
credential at all** to anyone who can reach the port. On this host that is loopback *and*
`<tailnet-ip>:4200`. The same unauthenticated read exposes every job's prompt text, `agent_id`, and
any `local_file` path. Treat `GET /api/cron/jobs` as a public dump: never put a bearer token,
webhook secret, or anything sensitive into `delivery_targets`, and block `/api/cron/*` at the edge.

### 10.6c `/api/logs/stream` has no method guard — but that is not an auth bypass

`path == "/api/logs/stream"` at `middleware.rs:180` carries no `is_get`, so **any** method on that
path skips the auth middleware. It is **not the only such entry** — **nine of the 38** clauses of the
no-passkey allowlist have no `is_get` (`middleware.rs:145-182`, counted 2026-08-24): `/`,
`/logo.png`, `/favicon.ico`, `/api/health`, `/api/health/detail`, `/api/status`, `/api/version`,
`/api/logs/stream`, `/api/providers/github-copilot/oauth/*`. An earlier revision said "eleven of the
41" and also listed `/api/auth/login` and `/api/auth/logout`, which are not on the list at all.

And it does not buy an attacker a write: every one of those paths is registered on a single method
(`/api/logs/stream` is `axum::routing::get`, `server.rs:501`), so the router answers a non-GET with
**405** *after* the middleware waved it through. Verified live:
`POST /api/logs/stream` → `405`; `GET /api/logs/stream` with no credential → **200, streaming the
audit chain**. The real problem is the unauthenticated GET, not the method gap.

### 10.6d On Docker, an empty `api_key` locks you OUT — it does not open the daemon up

`is_loopback` is derived from `ConnectInfo` (`middleware.rs:79-83`), and inside the container the
peer is always the bridge gateway. Verified by decoding `/proc/net/tcp` inside the container during a
host-side `curl http://127.0.0.1:4200`: the peer was **172.19.0.1**, not 127.0.0.1. So the
`api_key.is_empty() && !auth_enabled` branch (`middleware.rs:197-212`) takes the *non*-loopback path
and 401s everything non-public. Two consequences:

- Clearing `api_key` in a Docker deployment breaks the API rather than opening it.
- Every external client shares **one** GCRA bucket keyed on `172.19.0.1` (500 tokens/minute, §10.7).

### 10.7 Rate limiting, CORS, headers

- GCRA (`rate_limiter.rs`): **500 tokens/minute keyed on the `ConnectInfo` peer IP.** Costs:
  health 1, status/version/tools 1, agents/skills/peers/config 2, usage 3, `GET /api/audit*` 5,
  marketplace 10, `POST /api/agents` 50, `*/message` 30, `*/run` 100, `POST /api/skills/install` 50,
  `POST /api/migrate` 100, default 5. `/api/auth/login` falls in the default bucket ⇒ **100 login
  attempts per minute per source IP.** There is no `X-Forwarded-For` handling anywhere, so behind a
  reverse proxy the whole world shares one bucket (both a DoS amplifier and a brute-force enabler).
- Layer order (outermost → innermost, `server.rs:782-794`): CORS → Trace → Compression →
  request_logging → security_headers → **rate limit → auth** → handler. Rate limiting runs before
  auth, so 401s are still metered.
- CORS: `CorsLayer::permissive()` is *not* used; with auth enabled the allowlist is
  `http://<listen_addr>` plus `localhost`/`127.0.0.1` on 4200, 8080 and the actual port
  (`server.rs:80-108`). Your public hostname will **not** be in it unless it equals `listen_addr`.
- `security_headers` (`middleware.rs:246-275`) always sets `x-content-type-options: nosniff`,
  `x-frame-options: DENY`, `x-xss-protection`, `referrer-policy: strict-origin-when-cross-origin`,
  `cache-control: no-store…`, `strict-transport-security: max-age=63072000; includeSubDomains`,
  and `content-security-policy: default-src 'none'; frame-ancestors 'none'` unless the handler
  already set one (the dashboard sets its own nonce CSP).
- WebSocket: `MAX_WS_PER_IP = 5`, 64 KiB max message, 10 msg/min, 1800 s idle (`/api/security`).

### 10.8 RBAC (`openfang-kernel/src/auth.rs`) — channels only

`UserRole { Viewer=0, User=1, Admin=2, Owner=3 }`, `Action::required_role()`:
ChatWithAgent/ViewConfig → User; ViewUsage/SpawnAgent/KillAgent/InstallSkill → Admin;
ModifyConfig/ManageUsers → Owner. Users come from `[[users]]` with `channel_bindings`
(`"telegram" = "123456"`). `AuthManager::identify(channel_type, platform_id)` is the only lookup —
**there is no path from an HTTP request to a `UserId`**, so this RBAC never applies to the REST API
or the dashboard. `UserConfig.api_key_hash` exists in the struct and is unused.

---

## 11. SSRF (relevant to any public deployment)

`web_fetch::check_ssrf(url, allowed_hosts)` (`web_fetch.rs:195-255`), also used by the WASM
`net_fetch` host function: scheme must be `http(s)`; unconditional hostname blocklist
(`localhost`, `ip6-localhost`, `metadata.google.internal`, `metadata.aws.internal`,
`instance-data`, `169.254.169.254`, `100.100.100.200`, `192.0.0.192`, `0.0.0.0`, `::1`, `[::1]`);
then `allowed_hosts` (exact / `*.suffix` / CIDR) short-circuits; then DNS resolution with metadata
IPs always rejected and loopback/unspecified/private rejected unless the resolved IP matches an
allowlisted CIDR.

Caveat: the check resolves DNS itself and then hands the **URL** to `reqwest`, which resolves
again — a classic TOCTOU/DNS-rebinding window. Also `mcp.rs:329` has a *second, independent*
`check_ssrf` implementation for MCP URLs; they can drift.

---

## 12. Gotchas (all verified on this box unless noted)

1. **`grep '^api_key' config.toml` also matches `api_key_env`.** The naive recipe yields a
   two-line `$K`, curl emits a bogus second header line, and the server answers `400` with an
   empty body. Use `grep -m1 '^api_key = ' … | cut -d'"' -f2` — the space before `=` is what
   excludes `api_key_env` (full explanation: `automation-workflows-triggers-schedules.md` gotcha 46).
2. **`openfang security audit|verify` inside the container fails with
   `Missing Authorization: Bearer <api_key> header`.** `read_api_key()`
   (`openfang-cli/src/main.rs:1621-1642`) is *documented and coded* to read
   `$OPENFANG_HOME/config.toml` first, but on this build it demonstrably does not — a config file
   containing only `api_key = "…"` in the CLI's resolved home produces no auth header, while
   `OPENFANG_API_KEY=<key>` works immediately (a deliberately wrong env value returns
   `Invalid API key`, proving the header path). Workaround:
   `docker exec -e OPENFANG_API_KEY="$K" openfang-openfang-1 openfang security verify`.
   Code and observed binary behaviour disagree here; I could not reconcile them.
   **Wire-level proof** (so nobody re-derives it from 401s): a throwaway `OPENFANG_HOME` containing a
   copy of `config.toml` plus a `daemon.json` pointing at `127.0.0.1:4299`, with a header-logging
   listener on 4299, captured `openfang security audit` sending only `accept`, `accept-encoding` and
   `host` — **no `authorization` header** — both with the full config copy and with a minimal
   one-line `api_key = "..."` file. From the same home, `openfang config get api_key` printed the
   value and `openfang config show` printed the file, so path resolution and TOML parsing both work.
   The shipped binary behaves as if only the `OPENFANG_API_KEY` branch of `read_api_key()`
   (`main.rs:1621-1641`) exists, contradicting `main.rs:1193-1206`.
2b. **`openfang security verify` reports a FALSE integrity failure when the real problem is auth.**
   It prints `✘ Audit trail integrity check FAILED.` with a `hint: Missing Authorization: Bearer
   <api_key> header` underneath. An operator reading only the headline concludes the Merkle chain was
   tampered with. Always export `OPENFANG_API_KEY` before believing that message.
2c. **`openfang config show` prints `api_key` in plaintext with no redaction**, while
   `GET /api/config` returns `"api_key":"***"`. Two different disclosure postures for one value —
   never paste `config show` output into a ticket.
2d. **The TUI never authenticates at all.** `crates/openfang-cli/src/tui/event.rs:1224` defines its
   own shadowing `daemon_client()` that builds a bare 5-second-timeout `reqwest` client with **no
   `Authorization` header**. Every TUI screen fetch (sessions, agents, …) is unauthenticated and
   silently renders empty against a keyed daemon.
3. **`[approval] auto_approve = true` does nothing** (§4.4). Use `require_approval = false`.
   The runtime's own error message tells users to do the thing that does not work.
4. **Any agent tagged `hand:*` auto-approves every gated tool** (`kernel.rs:7776-7784`), silently,
   with only an `info!` line.
5. **Approval fires before validation** (#1180 B): operators are asked to approve commands that
   the metacharacter/allowlist checks will reject anyway.
6. **Approval timeout is capped at 300 s and auto-denies**; there is no "wait forever" option.
7. **`GET /api/approvals` is unauthenticated** and leaks the first 200 chars of every gated tool
   call's input.
8. **`GET /api/logs/stream` is unauthenticated** and streams the Merkle audit chain that
   `/api/audit/recent` protects (`middleware.rs:180`) — verified live, 200 with no credential.
   It is one of **eleven** `is_public()` entries with no `is_get` guard, but the route is
   GET-only, so a non-GET gets 405 from the router, not a write bypass (§10.6c).
8b. **`GET /api/cron/*` is unauthenticated and dumps `delivery_targets` verbatim**, including any
   `auth_header` bearer token you put in a webhook target (§10.6b — reproduced on this box before
   the job was deleted; `cron_jobs.json` is `[]` today).
8c. **Clearing `api_key` on a Docker deployment fails closed, not open** (§10.6d): the container
   always sees the bridge gateway (172.19.0.1) as the peer, so the loopback exemption never applies.
9. **`api_key = ""` + `[auth] enabled = true` ⇒ empty `X-API-Key`/`?token=` authenticates
   everything, including writes** (§10.6).
10. **LLM `agent_spawn` skips `validate_capability_inheritance`** (§3.5) while `/api/security`
    reports `privilege_escalation_prevention: true`.
10b. **`POST /mcp` = arbitrary file read as the daemon UID** (§3.6b). It calls `execute_tool` with
    `allowed_tools = None`, `workspace_root = None`, `exec_policy = None`, so `file_read` on
    `/data/secrets.env` returns `HYPERFUSION_API_KEY` and `file_read` on `/data/config.toml` returns
    the `api_key` itself — verified live. The API key is a filesystem read primitive; treat it that
    way when deciding who gets it.
11. **`[capabilities] shell/network/memory_*/agent_message` are inert for LLM agents** — they only
    bind in the WASM host ABI. Only `capabilities.tools` (plus `tool_allowlist`/`tool_blocklist`/
    `profile`/`exec_policy`) restricts an LLM agent.
12. **`Capability::ToolAll`, `FileRead`, `FileWrite`, `EnvRead` cannot be granted from a manifest**;
    the `has_tool_all` branch in `available_tools_with_registry` is a no-op (both arms identical).
13. **`tool_policy.rs` is dead code** (§3.7) — no `[tool_policy]` section exists.
14. **`max_memory_bytes` (#1242), `max_tool_calls_per_minute` and `max_network_bytes_per_hour` are
    configured but unenforced.** USD cost quotas bite, **and so does `max_llm_tokens_per_hour`** —
    `scheduler.check_quota()` runs before every turn (`kernel.rs:1941`, `:2032`;
    `openfang-kernel/src/scheduler.rs:91-96`), but its window is in-memory and resets on daemon restart.
15. **WASM watchdog threads live for the full 30 s after every execution** (#1241); WASM timeout is
    hard-coded to 30 s (`kernel.rs:2514`), not configurable.
16. **The WASM sandbox does not sandbox skills.** `SkillRuntime::Wasm` is unimplemented; skills are
    unrestricted Python/Node/Shell subprocesses with only `env_clear()` (§7.1).
17. **The prompt-injection scanner never sees a skill that ships a ready `skill.toml`** (§8).
18. **`shell_exec "curl …"` is blocked by the taint heuristic even when `curl` is allowlisted**;
    only `exec_policy.mode = "full"` bypasses it. Likewise `web_fetch` on any URL containing
    `token=`/`api_key=`.
19. **`docker_exec` has no approval gate and no `exec_policy` allowlist** — but it *does* get the
    metacharacter check via `docker_sandbox::validate_command` (`docker_sandbox.rs:181`, `:65-75`),
    and it runs in an ephemeral container defaulting to `network="none"`, `read_only_root=true`,
    `512m`, `pids_limit=100` (`config.rs:635-657`). Enabling `[docker]` hands every agent an
    unapproved command tool, not host-level arbitrary execution (§3.4).
20. **`exec_policy.shell_env_passthrough = ["*"]` forwards every daemon env var** — including
    `HYPERFUSION_API_KEY` and every channel token — into child processes.
21. **Rate limiting keys on the TCP peer IP with no `X-Forwarded-For` support**: behind a proxy all
    clients share one 500 tok/min bucket, and `/api/auth/login` allows ~100 attempts/min from it.
22. **CORS never includes your public hostname** unless it matches `api_listen`; browser dashboards
    served from another origin will fail preflight even with a valid key.
23. **`docs/security.md` §17.2 ("`/api/health/detail` requires authentication") and
    `docs/configuration.md`'s "public routes do not expose agent data" are both wrong.**
    §17.3's snippet also shows a `403`; the code returns `401`.
24. **`ApprovalRequest::validate()` / `ApprovalPolicy::validate()` are not called on the kernel
    request path** — the 10..=300 s clamp is only enforced if something calls `validate()`.
25. **Config deserialization falls back to `KernelConfig::default()` on any non-binding parse
    error** (`openfang-kernel/src/config.rs`, TODO GAP-012-Tier-2) — a typo in `[auth]` can
    silently disable auth *and* wipe `api_key`. Always confirm after editing config. **`/api/security`
    is not public** — without the header it returns `401 {"error":…}`, which `jq .configurable.auth`
    prints as a cheerful `null`:
    ```bash
    curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/security | jq .configurable.auth
    # healthy -> {"api_key_set":true,"mode":"bearer_token"}
    ```
26. **Three credential leaks that existed under stock v0.6.9 are closed in this fork (FANG-39/43/
    44) — no CVE, no upstream issue number.** Telegram's bot token reached the LLM prompt and the
    on-disk session (embedded in the file/voice download URL, formatted straight into
    `[User sent a file (...): <url>]` text) and reached logs via unredacted `reqwest::Error`; six
    more adapters (`dingtalk`, `messenger`, `flock`, `threema`, `wecom`, `gotify`) leaked their own
    credential the same way via `reqwest::Error`; and a raw provider error body (which can quote a
    rejected key) reached `fallback.reason`/`calls[].reason` in API responses unredacted. Full
    detail and file:line citations: `channels-mcp-api.md` §1.8a/§1.8b. **Action, not just
    awareness:** if this instance ever ran the stock build, every channel bot token and every
    provider API key that was ever rejected once is compromised — rotate them. The fix stops future
    leaks; it does not un-leak anything already written to a log or a session file.
27. **Model/provider/fallback disclosure landed on four response surfaces, one of them only
    partially** (FANG-57) — `/api/agents/{id}/message`, the `/message/stream` SSE `call` event, the
    agent WebSocket, and non-streaming `/v1/chat/completions` (as a `"openfang"` vendor-extension
    object). **Streaming `/v1/chat/completions` does not carry it** — `StreamEvent::CallReported`
    has no arm in that forwarder and is silently dropped. Full shapes and citations:
    `channels-mcp-api.md` §4.6.

---

## 13. Hardening checklist — exposing this instance behind Traefik

Current topology: `openfang-openfang-1` publishes `127.0.0.1:4200` and `<tailnet-ip>:4200`
(Tailscale); `dokploy-traefik` owns :80/:443. Because Traefik reaches the container over the Docker
bridge, OpenFang sees the proxy's container IP — **not** loopback — so the fail-closed path still
applies. Do not "helpfully" wire Traefik through `network_mode: host` + `127.0.0.1:4200`: that
would make every proxied request look like loopback and, with an empty `api_key`, open the box.

**Before you publish**

1. `api_key` **must** be non-empty. Generate 32+ random bytes; never rely on `[auth]` alone (§10.6).
   Keep it in `/data/config.toml` (0600) or `OPENFANG_API_KEY`. Verify (the header is **required** —
   `/api/security` is not in the public allowlist):
   `curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/security | jq '.configurable.auth'`
   → `{"api_key_set":true,"mode":"bearer_token"}`.
2. Enable `[auth]` **in addition** for the browser dashboard:
   `docker exec -it openfang-openfang-1 openfang auth hash-password`, paste `enabled/username/
   password_hash` into `/data/config.toml`, restart the container ( `[auth]` is not hot-reloadable ).
   Confirm no startup warning about non-Argon2id format.
3. Never set `OPENFANG_ALLOW_NO_AUTH`. Grep the compose/env to be sure.
4. Keep `api_listen`/`OPENFANG_LISTEN` bound so the only routes in are Traefik and Tailscale;
   drop the `<tailnet-ip>:4200` publish if Tailscale users can go through Traefik.

**Traefik middleware you have to add, because OpenFang won't do it for you**

5. **Strip the public-GET surface.** Traefik must 403 these to anonymous clients — OpenFang serves
   them with no credential: `/api/logs/stream` (audit chain), `/api/approvals*`, `/api/status`,
   `/api/health/detail`, `/api/agents`, `/api/sessions`, `/api/config`, `/api/skills*`,
   `/api/cron/*`, `/api/hands*`, `/api/budget*`, `/api/models`, `/api/providers`,
   `/api/integrations*`, `/api/workflows`, `/api/uploads/*`, `/api/a2a/agents`, `/a2a/*`,
   `/.well-known/agent.json`. Keep `/api/health` (cost-1 liveness) open for the LB.
   Simplest robust shape: allow only `/`, `/logo.png`, `/favicon.ico`, `/manifest.json`, `/sw.js`,
   `/api/health`, `/api/auth/login|logout|check`, and put everything else behind a Traefik
   forward-auth/basic-auth middleware.
6. **Block `?token=` on the edge** (and `X-API-Key` if you terminate auth at Traefik). A query-string
   credential lands in Traefik access logs and browser history, and it is the vector for §10.6.
6b. **Block `POST /mcp` and `POST /v1/chat/completions` unless you use them.** `/mcp` runs all 65
   builtin tools with no tool list, no workspace sandbox and no `exec_policy` (§3.6b) — it turns the
   API key into an arbitrary-file-read primitive. `/v1/chat/completions` has no session isolation.
7. **Add your own rate limit** at Traefik, per real client IP, in front of `/api/auth/login`,
   `/api/agents/*/message`, `POST /api/agents`. OpenFang's GCRA will see one IP (the proxy) and is
   effectively a global 500 tok/min cap, not per-client.
8. **Terminate TLS and set HSTS at Traefik.** OpenFang already emits an HSTS header on plain HTTP,
   which is meaningless without TLS.
9. Add a CSP/frame policy at the edge if you embed the dashboard; OpenFang's default is
   `default-src 'none'; frame-ancestors 'none'` for API responses and a nonce CSP for `/`.
10. Set `X-Forwarded-*`, but understand OpenFang ignores them — all attribution in its logs and
    rate limiter will be the proxy IP. Keep Traefik access logs for real client attribution.

**Lock down the agent blast radius**

11. `[approval] require_approval` — the default is *only* `shell_exec`. For an internet-exposed box
    extend it, e.g.:
    ```toml
    [approval]
    require_approval = ["shell_exec","process_start","docker_exec","file_write","apply_patch",
                        "browser_run_js","agent_spawn","cron_create","schedule_create",
                        "channel_send","skill_execute"]
    timeout_secs = 300
    ```
    Then **watch for pending requests** — there is no notification (#1180 C) and a request
    auto-denies after ≤5 minutes. Poll `GET /api/approvals` (authenticated at the edge) or
    `openfang approvals list`.
12. Keep `exec_policy.mode = "allowlist"` (default) with an explicit, minimal `allowed_commands`.
    Never `mode = "full"` and never `allowed_commands = ["*"]` — both silently disable the approval
    gate for `shell_exec`/`process_start` (`tool_runner.rs:152`). `mode = "deny"` also removes
    `shell_exec` from the tool list entirely (`kernel.rs:6313-6320`).
13. Leave `shell_env_passthrough` empty.
14. Per agent, set `capabilities.tools` explicitly (never `["*"]`, never empty) and add
    `tool_blocklist = ["agent_spawn","docker_exec","browser_run_js","process_start"]` for anything
    that touches untrusted input. Remember `agent_spawn` is your escalation path (§3.5).
15. Audit every agent tagged `hand:*` — they auto-approve everything. `openfang hand list` /
    `GET /api/hands`.
16. `[docker] enabled = false` unless you need `docker_exec`; if you enable it, blocklist or
    approval-gate the tool, keep `network = "none"`, `read_only_root = true`, `pids_limit = 100`.
17. Install skills only from sources you control, and use the signed path — the CLI cannot do it:
    ```bash
    curl -s -X POST http://127.0.0.1:4200/api/skills/install \
      -H "Authorization: Bearer <API_KEY>" -H 'content-type: application/json' \
      -d '{"name":"<skill>","require_signed":true,"allowed_signer_keys":["<64-hex-pubkey>"]}'
    ```
    Never hand-place a `skill.toml` into `$OPENFANG_HOME/skills/` — that path skips the injection
    scanner (§8). Re-run `openfang doctor` after any skill change.
18. Assume skills = daemon-privileged code (§7.1). If that is unacceptable, run the whole daemon in
    a container with `--read-only`, a tmpfs `/tmp`, `--cap-drop ALL`, `--pids-limit`, `--memory`,
    and no Docker socket mount (mounting `/var/run/docker.sock` for `docker_exec` is a full host
    escape — do not).
19. If you run WASM agents, remember `max_memory_bytes` is not enforced (#1242): cap the *container's*
    memory instead, and keep concurrency low so the 30 s watchdog threads (#1241) don't pile up.
20. `[web.fetch] ssrf_allowed_hosts` — leave empty unless you truly need internal targets; every
    entry is an SSRF hole into your private network (metadata IPs stay blocked either way).

**Operate**

21. Verify the chain regularly and alert on failure:
    `docker exec -e OPENFANG_API_KEY="$K" openfang-openfang-1 openfang security verify`
    (or `curl -H "Authorization: Bearer <API_KEY>" .../api/audit/verify` → `{"valid":true}`).
    Remember the chain is in-memory + SQLite `audit_entries` in the same DB the daemon can rewrite —
    it is tamper-*evident*, not tamper-proof. Ship entries off-box with
    `GET /api/audit/recent?n=1000` — **`?limit=` is ignored and silently gives you 50** (§10.3).
22. Do not expect **tool-call** auditing (§10.1). You do get `AgentSpawn` / `AgentKill` /
    `AgentMessage` / `ConfigChange`; `ToolInvoke`/`ShellExec`/`FileAccess`/`NetworkAccess` are never
    emitted by the runtime. If you need them, feed them in yourself via `POST /api/audit/append`.
23. Keep `/data` at 0700 and `config.toml`/`secrets.env` at 0600 (the CLI does this on create via
    `restrict_file_permissions`, but dashboard/manual edits can loosen it).
24. After every config edit, confirm the daemon actually parsed it (§12.25) — a bad `[auth]` block
    silently reverts the whole config, `api_key` included, to defaults.
