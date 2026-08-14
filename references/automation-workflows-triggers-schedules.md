# OpenFang Automation Reference — Workflows, Triggers, Schedules

Source of truth: **OpenFang v0.6.9**, tag `v0.6.9`, commit `acf2587`, checked out at `/opt/openfang`
(clean tree). Every claim below is either quoted from that source with `file:line`, or verified against
the live container `openfang-openfang-1` (API `http://127.0.0.1:4200`, `OPENFANG_HOME=/data`).
Live-verified items are marked **[verified live]**.

Conventions used here:
- `$OF` = `OPENFANG_HOME`. On this host: `/data` inside the container,
  `/var/lib/docker/volumes/openfang_openfang-data/_data` on the host.
- `<API_KEY>` = the top-level `api_key` in `$OF/config.toml`. Read it with
  `K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)`.
  Match on `^api_key = ` **with the ` = `**: the bare pattern `^api_key` also matches the
  `api_key_env = "..."` line under `[default_model]`, so `grep '^api_key' | cut -d'"' -f2` returns a
  71-char concatenation of both values and every request then fails with a bare `400` and an empty
  body. The correct key here is 51 chars. **[verified live]**

---

## Table of contents

- [0. The three mechanisms, and how they differ](#0-the-three-mechanisms-and-how-they-differ)
- [1.1 Two different JSON formats — do not mix them](#11-two-different-json-formats--do-not-mix-them)
- [1.2 Step field reference (API shape)](#12-step-field-reference-api-shape)
- [1.3 Agent resolution](#13-agent-resolution)
- [1.4 Step modes — real semantics](#14-step-modes--real-semantics)
- [1.5 Variable substitution](#15-variable-substitution)
- [1.6 Error handling and timeouts](#16-error-handling-and-timeouts)
- [1.7 Runs, states, retention](#17-runs-states-retention)
- [1.8 REST API](#18-rest-api)
- [1.9 CLI](#19-cli)
- [1.10 Worked example — fan-out + collect, end to end **[verified live]**](#110-worked-example--fan-out--collect-end-to-end-verified-live)
- [2.1 What actually fires a trigger](#21-what-actually-fires-a-trigger)
- [2.2 Pattern JSON — the exact accepted encodings](#22-pattern-json--the-exact-accepted-encodings)
- [2.3 Match semantics](#23-match-semantics)
- [2.4 `describe_event` — what `{{event}}` becomes](#24-describe_event--what-event-becomes)
- [2.5 Fire counting and auto-disable](#25-fire-counting-and-auto-disable)
- [2.6 CRUD](#26-crud)
- [2.7 Triggers are not persisted](#27-triggers-are-not-persisted)
- [2.8 Trigger dispatch](#28-trigger-dispatch)
- [3.1 (A) CronScheduler — storage and tick loop](#31-a-cronscheduler--storage-and-tick-loop)
- [3.2 CronJob schema](#32-cronjob-schema)
- [3.3 Cron expression support](#33-cron-expression-support)
- [3.4 Agent tools: `schedule_create` / `schedule_list` / `schedule_delete`](#34-agent-tools-schedule_create--schedule_list--schedule_delete)
- [3.5 REST surfaces for cron](#35-rest-surfaces-for-cron)
- [3.6 CLI: `openfang cron` — largely broken in v0.6.9](#36-cli-openfang-cron--largely-broken-in-v069)
- [3.7 (B) Agent-level `[schedule]` — the background self-prompt loops](#37-b-agent-level-schedule--the-background-self-prompt-loops)
- [3.8 (C) Heartbeat monitor](#38-c-heartbeat-monitor)
- [3.9 The wake hook — `/hooks/wake` and `/hooks/agent`](#39-the-wake-hook--hookswake-and-hooksagent)
- [4.1 #1253 — `collect` joins pre-fan-out outputs](#41-1253--collect-joins-pre-fan-out-outputs)
- [4.2 #1192 — deleted workflow reappears after restart](#42-1192--deleted-workflow-reappears-after-restart)
- [4.3 #1252 — `[heartbeat] default_timeout_secs` ignored](#43-1252--heartbeat-default_timeout_secs-ignored)
- [4.4 #1206 — sample agent schedules cause surprise LLM spend after the v0.6.9 auto-spawn](#44-1206--sample-agent-schedules-cause-surprise-llm-spend-after-the-v069-auto-spawn)
- [File / path map](#file--path-map)
- [Log lines worth grepping](#log-lines-worth-grepping)
- [Env vars](#env-vars)
- [Config keys relevant to automation](#config-keys-relevant-to-automation)
- [Agent manifest keys relevant to automation](#agent-manifest-keys-relevant-to-automation)
- [Choosing a mechanism](#choosing-a-mechanism)

## 0. The three mechanisms, and how they differ

| | **Workflows** | **Triggers** | **Schedules** |
|---|---|---|---|
| Question answered | "run agents A→B→C on this input" | "when event X happens, poke agent Y" | "run this at 09:00 every day" |
| Engine | `WorkflowEngine`, `crates/openfang-kernel/src/workflow.rs` | `TriggerEngine`, `crates/openfang-kernel/src/triggers.rs` | `CronScheduler`, `crates/openfang-kernel/src/cron.rs` |
| Storage | in-memory `Arc<RwLock<HashMap>>` + one JSON file per workflow in `$OF/workflows/` | **in-memory `DashMap` only — nothing on disk** | in-memory `DashMap` + `$OF/cron_jobs.json` |
| Survives restart | definitions yes (via `$OF/workflows/*.json`), runs no | **no** | yes |
| Driven by | explicit `POST /api/workflows/{id}/run` (synchronous) or a cron `workflow_run` action | `OpenFangKernel::publish_event()` only | 15-second kernel tick (`kernel.rs:4657-4704`) |
| Unit of work | multi-step pipeline across agents | one message to one agent | one `CronAction` |
| Concurrency | fan-out via `futures::join_all` | one `tokio::spawn` per matching trigger | serial inside one tick |

There is a **fourth**, easily confused surface: the per-agent `[schedule]` block in `agent.toml`
(`ScheduleMode`, `crates/openfang-kernel/src/background.rs`). It is *not* the cron scheduler. It spawns
a naked `tokio::time::sleep` loop that self-prompts the agent. This is the mechanism behind issue
**#1206**, and it does **not** understand real cron expressions. See §3.6.

And a **fifth**: the heartbeat monitor (`crates/openfang-kernel/src/heartbeat.rs`), a 30-second health
loop that can mark agents Crashed and auto-recover them. Issue **#1252**. See §3.8.

---

# 1. WORKFLOWS

Implementation: `crates/openfang-kernel/src/workflow.rs` (1385 lines).
HTTP layer: `crates/openfang-api/src/routes.rs:903-1250`.
Routes registered: `crates/openfang-api/src/server.rs:364-382`.
Kernel glue: `crates/openfang-kernel/src/kernel.rs:4274-4383`.

## 1.1 Two different JSON formats — do not mix them

This trips up everyone. The **API request body** and the **on-disk file** are *different shapes*.

**API shape** (`POST`/`PUT /api/workflows`) — hand-parsed field by field in
`routes.rs:912-973`, never `serde`-deserialised:

```json
{
  "name": "my-pipeline",
  "description": "what it does",
  "steps": [
    {
      "name": "analyze",
      "agent_name": "code-reviewer",      // or "agent_id": "<uuid>"
      "prompt": "Analyze:\n\n{{input}}",  // note: "prompt", not "prompt_template"
      "mode": "sequential",
      "timeout_secs": 180,
      "error_mode": "retry",
      "max_retries": 2,
      "output_var": "analysis",
      "condition": "ERROR",               // only read when mode == "conditional"
      "max_iterations": 4,                // only read when mode == "loop"
      "until": "APPROVED"                 // only read when mode == "loop"
    }
  ]
}
```

**On-disk shape** (`$OF/workflows/<workflow-uuid>.json`) — this is `serde_json` of the real
`Workflow` struct, written at `routes.rs:985-1001`, read back at `kernel.rs:4346-4383`:

```json
{
  "id": "b14740a8-a6b3-4f81-8dae-54ec057241bf",
  "name": "probe-persist",
  "description": "persistence probe",
  "steps": [
    {
      "name": "s1",
      "agent": { "name": "assistant" },        // untagged StepAgent: {"name":...} or {"id":...}
      "prompt_template": "echo {{input}}",     // NOT "prompt"
      "mode": "sequential",                     // or {"conditional":{"condition":"x"}}
      "timeout_secs": 30,
      "error_mode": "fail",                     // or {"retry":{"max_retries":3}}
      "output_var": null
    }
  ],
  "created_at": "2026-08-09T21:52:59.362478827Z"
}
```
**[verified live]** — exact bytes read back from `$OF/workflows/`.

If you hand-author a file in `$OF/workflows/`:
- `id`, `name`, `description`, `steps`, `created_at` are all **required** (no serde defaults on
  `Workflow`, `workflow.rs:67-79`).
- Per step, `name`, `agent`, `prompt_template`, `mode` are **required**; only `timeout_secs`,
  `error_mode`, `output_var` have `#[serde(default)]` (`workflow.rs:83-101`). Omitting `mode` makes the
  whole file fail to load — it is skipped with `"Invalid workflow JSON, skipping"` at `kernel.rs:4378`.
- Tagged-enum forms: `"mode": {"loop": {"max_iterations": 3, "until": "DONE"}}`,
  `"mode": {"conditional": {"condition": "issue"}}`, `"error_mode": {"retry": {"max_retries": 2}}`.
- Registration keys off the `id` **inside** the file, not the filename (`kernel.rs:4370-4373`).

## 1.2 Step field reference (API shape)

| JSON field | Default when omitted | Where parsed |
|---|---|---|
| `name` | `"step"` | `routes.rs:927` |
| `agent_id` / `agent_name` | **required** — 400 `"Step '<n>' needs 'agent_id' or 'agent_name'"` | `routes.rs:928-941` |
| `prompt` | `"{{input}}"` | `routes.rs:967` |
| `mode` | `"sequential"` (any unrecognised string also falls back to sequential — no error) | `routes.rs:943-954` |
| `timeout_secs` | `120` | `routes.rs:969` |
| `error_mode` | `"fail"` (unrecognised → fail) | `routes.rs:956-962` |
| `max_retries` | `3`, only when `error_mode == "retry"` | `routes.rs:959` |
| `output_var` | `null` | `routes.rs:971` |
| `condition` | `""` (matches everything, since `"".contains()` is always true) | `routes.rs:947` |
| `max_iterations` | `5` | `routes.rs:950` |
| `until` | `""` → loop **always terminates after iteration 1** | `routes.rs:951` |

`agent_id` wins if both are present (`routes.rs:928` is checked first).

## 1.3 Agent resolution

`kernel.rs:4294-4306`:
- `{"id": "<uuid>"}` → `registry.get(uuid)`; a non-UUID string yields `None`.
- `{"name": "..."}` → `registry.find_by_name(name)`, **first match wins**; the match is on the live
  registry, so the agent must be spawned, not merely present on disk.

On failure the engine returns `Err("Agent not found for step '<name>'")` via `?` at
`workflow.rs:484-485` / `553-555` / `660-661` / `708-709`.

**This early-return does not mark the run as Failed.** Unlike every other error path in `execute_run`,
the `agent_resolver(...).ok_or_else(...)?` lines skip the `r.state = WorkflowRunState::Failed` block.
**[verified live]** — a run against a bogus `agent_name` returned HTTP 500
`{"error":"Workflow execution failed"}` while `GET /api/workflows/{id}/runs` still reported
`"state": "running", "completed_at": null` minutes later. Because eviction only removes
`Completed`/`Failed` runs (`workflow.rs:291-311`), such zombies are never reclaimed and the
`MAX_RETAINED_RUNS = 200` cap silently stops working.

## 1.4 Step modes — real semantics

The engine keeps three pieces of state across the loop (`workflow.rs:468-471`):
`current_input: String`, `all_outputs: Vec<String>`, `variables: HashMap<String,String>`.

### `sequential` (default) — `workflow.rs:483-532`
Resolves the agent, expands the prompt, runs it under `error_mode`, pushes a `StepResult`, sets
`variables[output_var]`, **pushes the output onto `all_outputs`**, and sets `current_input = output`.

### `fan_out` — `workflow.rs:534-633`
Greedily collects the maximal run of *consecutive* `fan_out` steps starting at the current index
(`workflow.rs:536-545`), builds one future per step and awaits `futures::future::join_all`. All of them
receive the **same** `{{input}}` (the value of `current_input` before the group).

Critical deviations from the docs:
- **`error_mode` is ignored for `fan_out` steps.** The group is built with a raw
  `tokio::time::timeout(...)` at `workflow.rs:561-567`; `execute_step_with_error_mode` is never called.
  `skip` and `retry` have no effect — any single failure or timeout aborts the entire run
  (`workflow.rs:598-621`).
- After the group, `current_input` equals the **last** fan-out step's output (`workflow.rs:596`, assigned
  once per result in index order), not a join. Without a following `collect` step, you silently drop
  every branch but the last.
- Every branch's output is appended to `all_outputs` (`workflow.rs:595`) — see the `collect` bug below.
- `duration_ms` recorded on each branch is the **wall time of the whole group**, not the branch
  (`workflow.rs:570-587`).

### `collect` — `workflow.rs:635-642`
A pure data step. It does **not** resolve an agent, does **not** call the LLM, and ignores `prompt`,
`timeout_secs` and `error_mode`. **[verified live]**: a workflow consisting of a single `collect` step
whose `agent_name` was `"does-not-exist-xyz"` completed successfully with `"output": ""`.

```rust
StepMode::Collect => {
    current_input = all_outputs.join("\n\n---\n\n");
    all_outputs.clear();
    all_outputs.push(current_input.clone());
    if let Some(ref var) = step.output_var { variables.insert(var.clone(), current_input.clone()); }
}
```
It joins the **whole run's** `all_outputs` buffer, not just the preceding fan-out group. That is
issue **#1253** — see §4.1.

### `conditional` — `workflow.rs:644-702`
`if !current_input.to_lowercase().contains(&condition.to_lowercase()) { skip }`. The condition is
tested against `current_input` (previous step's output, or the workflow input for step 1), **not**
against expanded variables. A skipped step produces **no `StepResult`** and leaves `current_input`,
`all_outputs` and `variables` untouched. When it fires, it behaves exactly like `sequential`.
An empty `condition` (the default) always matches.

### `loop` — `workflow.rs:704-781`
The agent is resolved **once**, before the loop. Then `for loop_iter in 0..max_iterations`:
expand → run under `error_mode` → record `StepResult` named `"<name> (iter N)"` (1-based,
`workflow.rs:733`) → `current_input = output` → break if
`output.to_lowercase().contains(&until.to_lowercase())` (`workflow.rs:747`).

- `until: ""` → matches immediately → exactly one iteration.
- `max_iterations: 0` → zero iterations; the step still pushes the *unchanged* `current_input` onto
  `all_outputs` at `workflow.rs:780`.
- `error_mode: "skip"` inside a loop **breaks the loop** (`Ok(None) => break`, `workflow.rs:765`).
- `output_var` is written once, after the loop, with the final `current_input` (`workflow.rs:777-779`).
- Each attempt gets a fresh `timeout_secs`; there is no cap on total loop wall time other than the
  global 3600 s workflow timeout.

## 1.5 Variable substitution

`workflow.rs:343-349`:
```rust
fn expand_variables(template: &str, input: &str, vars: &HashMap<String, String>) -> String {
    let mut result = template.replace("{{input}}", input);
    for (key, value) in vars { result = result.replace(&format!("{{{{{key}}}}}"), value); }
    result
}
```
- `{{input}}` is substituted **first**, then named vars. If a previous step's output itself contains
  the literal text `{{myvar}}`, that text *will* be expanded — variable expansion is not escaped.
- Iteration order over a `HashMap` is nondeterministic; if one variable's value contains another
  variable's placeholder, the result depends on hash order. Avoid `{{...}}` inside values.
- Undefined `{{foo}}` is left verbatim in the prompt (no error).
- Variables live for the whole run and are overwritten by a later step using the same `output_var`.

## 1.6 Error handling and timeouts

`execute_step_with_error_mode`, `workflow.rs:352-428`. Used by `sequential`, `conditional`, `loop`
— **not** by `fan_out`.

| `error_mode` | Behaviour |
|---|---|
| `fail` (default) | First error/timeout aborts. Run → `Failed`, `error` = `"Step '<n>' failed: <e>"` or `"Step '<n>' timed out after <N>s"`, `completed_at` set. `execute_run` returns `Err`. |
| `skip` | Logs `warn!`, returns `Ok(None)`. **No `StepResult` is recorded**, `current_input` unchanged, run continues. |
| `retry` | `for attempt in 0..=max_retries` — so `max_retries: 3` is up to **4** attempts. Each attempt gets a full `timeout_secs`. Exhaustion → `Err("Step '<n>' failed after <N> retries: <last_err>")` and the run is marked `Failed`. There is **no backoff**; retries are immediate. |

Global ceiling: `MAX_WORKFLOW_SECS = 3600` in `kernel.rs:4323`, wrapped around the whole
`execute_run`. On expiry the HTTP call returns 500 but the run row is left in whatever state it was
in — usually `Running`, another zombie.

## 1.7 Runs, states, retention

`WorkflowRun` (`workflow.rs:158-180`) tracks `state` (`pending|running|completed|failed`),
`step_results`, `output`, `error`, `started_at`, `completed_at`.

`create_run` (`workflow.rs:266-314`) evicts oldest `Completed`/`Failed` runs once the map exceeds
`MAX_RETAINED_RUNS = 200` (`workflow.rs:260`). Runs are **memory-only** — every restart loses run history.

`GET /api/workflows/{id}/runs` **ignores the id** and returns every run in the engine
(`routes.rs:1065-1069`, the path param is bound as `Path(_id)`). **[verified live]** — asking for one
workflow's runs returned runs belonging to three different workflows.

## 1.8 REST API

Auth: `GET /api/workflows` (the bare list) is in the public allowlist (`middleware.rs:134`) and needs
no key. **Every other workflow endpoint, including `GET /api/workflows/{id}`, requires
`Authorization: Bearer <API_KEY>`.**

| Method + path | Notes |
|---|---|
| `POST /api/workflows` | 201 `{"workflow_id":"<uuid>"}`; also writes `$OF/workflows/<uuid>.json` |
| `GET /api/workflows` | summaries: `id,name,description,steps(count),created_at` |
| `GET /api/workflows/{id}` | full definition, `steps` serialised in the **on-disk** shape |
| `PUT /api/workflows/{id}` | in-memory only, **never rewrites the file** (see §4.2) |
| `DELETE /api/workflows/{id}` | in-memory only, **never deletes the file** (issue #1192) |
| `POST /api/workflows/{id}/run` | **synchronous** — the HTTP request blocks for the whole run (up to 3600 s). 200 `{"run_id","output","status":"completed"}` / 500 `{"error":"Workflow execution failed"}` (the real error is only in the daemon log: `"Workflow run failed for {id}: {e}"`, `routes.rs:1055`) |
| `GET /api/workflows/{id}/runs` | all runs, unfiltered |

## 1.9 CLI

```
openfang workflow list
openfang workflow create <file.json>       # posts the file verbatim to POST /api/workflows
openfang workflow get <workflow_id>
openfang workflow update <workflow_id> <file.json>
openfang workflow delete <workflow_id>
openfang workflow run <workflow_id> <input>
```
`crates/openfang-cli/src/main.rs:546-578` (definitions), `3205-3383` (implementations).
All require a running daemon (`daemon.json` in `$OF`).

**The CLI cannot authenticate from `config.toml` on this build.** `daemon_client()`
(`main.rs:1193-1206`) is supposed to read `api_key` via `read_api_key()` (`main.rs:1621-1642`), but
**[verified live]** every write command fails with
`Failed to create workflow: Missing Authorization: Bearer <api_key> header` — i.e. no credential
header is sent at all — even with a minimal `config.toml` containing nothing but `api_key = "..."`.
Exporting the env var works:

```bash
OPENFANG_API_KEY=<API_KEY> openfang workflow create ./wf.json     # ✔
openfang workflow create ./wf.json                                # ✘ 401
```
Read-only commands that happen to hit public endpoints (`openfang workflow list`) work either way,
which makes the failure look intermittent. Worse, `openfang trigger list` swallows the 401 entirely and
prints `No triggers registered.` (`main.rs:3399-3419` — the error object simply isn't an array).

## 1.10 Worked example — fan-out + collect, end to end **[verified live]**

```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)

cat > /tmp/wf.json <<'EOF'
{
  "name": "brainstorm",
  "description": "3 parallel branches, then synthesise",
  "steps": [
    {"name":"creative", "agent_name":"assistant","prompt":"Brainstorm 5 creative ideas for: {{input}}",
     "mode":"fan_out","timeout_secs":60,"output_var":"creative"},
    {"name":"technical","agent_name":"assistant","prompt":"Brainstorm 5 technical ideas for: {{input}}",
     "mode":"fan_out","timeout_secs":60,"output_var":"technical"},
    {"name":"gather",   "agent_name":"assistant","prompt":"unused","mode":"collect"},
    {"name":"synth",    "agent_name":"assistant",
     "prompt":"Rank the top 5 ideas:\n\n{{input}}","mode":"sequential","timeout_secs":120}
  ]
}
EOF

ID=$(curl -s -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
     -d @/tmp/wf.json http://127.0.0.1:4200/api/workflows | python3 -c 'import sys,json;print(json.load(sys.stdin)["workflow_id"])')

curl -s --max-time 900 -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
     -d '{"input":"offline-first note taking app"}' \
     http://127.0.0.1:4200/api/workflows/$ID/run | python3 -m json.tool

# teardown — BOTH steps are required (see #1192)
curl -s -X DELETE -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/workflows/$ID
rm -f /var/lib/docker/volumes/openfang_openfang-data/_data/workflows/$ID.json
```

Because there is no sequential step *before* the fan-out group here, `collect` is correct. Insert one
and #1253 bites — see §4.1 for the verified output.

---

# 2. TRIGGERS

Implementation: `crates/openfang-kernel/src/triggers.rs` (742 lines).
HTTP: `routes.rs:1252-1383` and `routes.rs:5946-5975` (the `PUT` toggle).
Routes: `server.rs:338-346`.

## 2.1 What actually fires a trigger

`TriggerEngine::evaluate` is called from exactly **two** places
(`grep -rn "triggers.evaluate" crates/`):

1. `OpenFangKernel::publish_event()` — `kernel.rs:4212-4235`. Evaluates, publishes to the bus, then
   `tokio::spawn`s a `send_message(agent_id, msg)` per match. **This is the only path that actually
   delivers a message to an agent.**
2. `spawn_agent()` — `kernel.rs:1789-1790`:
   ```rust
   // Evaluate triggers synchronously (we can't await in a sync fn, so just evaluate)
   let _triggered = self.triggers.evaluate(&event);
   ```
   The matches are **discarded**. An `agent_spawned` / `lifecycle` trigger therefore has its
   `fire_count` incremented and can auto-disable itself via `max_fires`, while **no agent is ever
   messaged**. This is a silent trap for the documented "React to new agent spawns" recipe.

Everything that publishes straight to `kernel.event_bus.publish(...)` bypasses trigger evaluation
entirely. Notably the **heartbeat monitor** does exactly that (`kernel.rs:4915`, `4955`, `4992`), so the
`docs/workflows.md` recipe `{"content_match":{"substring":"health check failed"}}` **can never fire**.

Paths that *do* reach `publish_event`:
- `POST /hooks/wake` → `routes.rs:11554-11555`
- the `event_publish` tool → `tool_runner.rs:2001` → `kernel.rs:7324-7340`
- cron `system_event` action → `kernel.rs:6591-6596`
- cron `agent_turn` / `workflow_run` completion (`SystemEvent::CronJobExecuted`) → `kernel.rs:6637`, `6707`

## 2.2 Pattern JSON — the exact accepted encodings

`TriggerPattern` (`triggers.rs:38-59`) is an externally-tagged enum with `rename_all = "snake_case"`.
Unit variants must be **bare JSON strings**. The following table is the result of POSTing each literal
to `/api/triggers` on the live daemon:

| Pattern JSON | Result **[verified live]** |
|---|---|
| `"all"` | 201 |
| `"lifecycle"` | 201 |
| `"agent_terminated"` | 201 |
| `"system"` | 201 (schema-valid; not retested) |
| `"memory_update"` | 201 |
| `{"lifecycle":null}` | 201 |
| `{"agent_spawned":{"name_pattern":"*"}}` | 201 |
| `{"system_keyword":{"keyword":"quota"}}` | 201 |
| `{"memory_key_pattern":{"key_pattern":"*"}}` | 201 |
| `{"content_match":{"substring":"deploy"}}` | 201 |
| `{"lifecycle":{}}` | **400**, body is exactly `{"error":"Invalid trigger pattern"}` — the serde detail `invalid type: map, expected unit` is **only** written to the daemon log (`routes.rs:1285`, seen in `docker logs`), never returned to the caller |
| `{"agent_terminated":{}}` | **400** — same, same empty-of-detail body |
| `{"all":{}}` | **400** — same |
| `"bogus"` | **400**, body is again just `{"error":"Invalid trigger pattern"}`. The useful part — `unknown variant 'bogus', expected one of 'lifecycle','agent_spawned','agent_terminated','system','system_keyword','memory_update','memory_key_pattern','all','content_match'` — appears **only** in `docker logs` (`WARN openfang_api::routes: Invalid trigger pattern: …`). Re-verified 2026-08-10. |

Note the rule precisely: **only the unit variants** must be bare strings. Struct variants are
correctly maps and are accepted — `{"content_match":{"substring":"x"}}`,
`{"agent_spawned":{"name_pattern":"*"}}`, `{"system_keyword":{"keyword":"quota"}}` and
`{"memory_key_pattern":{"key_pattern":"*"}}` all return **201**.

**`docs/cli-reference.md:536` and the CLI's own help text (`main.rs:592`) are wrong.** They tell you
to use `'{"lifecycle":{}}'`,
`'{"agent_terminated":{}}'` and `'{"all":{}}'` — all three are rejected. So is the CLI's own on-error
hint block (`main.rs:3426-3430`). `docs/workflows.md:722-731` has it right (`'"lifecycle"'`).

## 2.3 Match semantics

`matches_pattern`, `triggers.rs:323-366`:

| Pattern | Predicate |
|---|---|
| `all` | always `true` — including `EventPayload::Custom` |
| `lifecycle` | `matches!(payload, Lifecycle(_))` |
| `agent_spawned {name_pattern}` | `Lifecycle(Spawned{name})` and (`name.contains(name_pattern)` or `name_pattern == "*"`) |
| `agent_terminated` | `Lifecycle(Terminated)` **or** `Lifecycle(Crashed)` |
| `system` | `matches!(payload, System(_))` |
| `system_keyword {keyword}` | `format!("{:?}", system_event).to_lowercase().contains(keyword.to_lowercase())` — matches against the **Rust `Debug` rendering**, e.g. `QuotaWarning { agent_id: .., resource: "tokens", .. }` |
| `memory_update` | `matches!(payload, MemoryUpdate(_))` |
| `memory_key_pattern {key_pattern}` | `delta.key.contains(key_pattern)` or `key_pattern == "*"` — substring, **not** glob |
| `content_match {substring}` | `describe_event(e).to_lowercase().contains(substring.to_lowercase())` |

`name_pattern` / `key_pattern` are plain `contains` substring tests; `"*"` is special-cased as
"anything". `"cod*"` matches nothing.

## 2.4 `describe_event` — what `{{event}}` becomes

`triggers.rs:369-465`. `{{event}}` in `prompt_template` is replaced with this string
(`triggers.rs:292-294`). Full catalogue:

| Payload | Rendered string |
|---|---|
| `Message` | `Message from {role:?}: {content}` |
| `ToolResult` | `Tool '{tool_id}' succeeded\|failed ({ms}ms): {first 200 chars}` |
| `MemoryUpdate` | `Memory {op:?} on key '{key}' for agent {uuid}` |
| `Lifecycle::Spawned` | `Agent '{name}' (id: {uuid}) was spawned` |
| `Lifecycle::Started/Suspended/Resumed` | `Agent {uuid} started` / `suspended` / `resumed` |
| `Lifecycle::Terminated` | `Agent {uuid} terminated: {reason}` |
| `Lifecycle::Crashed` | `Agent {uuid} crashed: {error}` |
| `Network` | `Network event: {ne:?}` |
| `System::KernelStarted/Stopping` | `Kernel started` / `Kernel stopping` |
| `System::QuotaWarning` | `Quota warning: agent {uuid}, {resource} at {pct:.1}%` |
| `System::QuotaEnforced` | `Quota enforced: agent {uuid}, spent ${x:.4} / ${y:.4}` |
| `System::HealthCheck` | `Health check: {status}` |
| `System::HealthCheckFailed` | `Health check failed: agent {uuid}, unresponsive for {n}s` |
| `System::ModelRouted` | `Model routed: agent {uuid}, complexity={c}, model={m}` |
| `System::UserAction` | `User action: {user} {action} -> {result}` |
| `System::CronJobExecuted` | `Cron job executed: {job_name} ({job_id}) for agent {uuid}` |
| **`Custom(bytes)`** | **`Custom event ({n} bytes)`** — the payload is **not** rendered |

That last row is the single most important fact about triggers. `/hooks/wake`, the `event_publish`
tool, and cron `system_event` actions **all** produce `EventPayload::Custom`. **[verified live]** — a
wake with `{"text":"deployment of service X finished"}` and an `"all"` trigger whose template was
`WAKE EVENT: {{event}} -- reply with the single word ACK` delivered this to the agent:

```
User -> WAKE EVENT: Custom event (106 bytes) -- reply with the single word ACK
Assistant -> ACK
```

Consequences:
- `content_match` **cannot** match wake text, cron event text, or `event_publish` payloads.
  **[verified live]**: `{"content_match":{"substring":"deploy"}}` stayed at `fire_count: 0` while
  `"all"` went to `1` for the same `deploy finished successfully` event.
- The only pattern that reacts to a `Custom` event is `"all"`.
- If you need the payload in the prompt, do **not** use `/hooks/wake` — use `POST /hooks/agent`
  (sends your message text directly to an agent) or a cron `agent_turn` action.

## 2.5 Fire counting and auto-disable

`evaluate`, `triggers.rs:274-308`, iterates with `DashMap::iter_mut()` so the fire count increment is
atomic with the pattern check.

```rust
if trigger.max_fires > 0 && trigger.fire_count >= trigger.max_fires { trigger.enabled = false; continue; }
```
The check runs **at the start of the next evaluation**, so a `max_fires: 1` trigger still reads
`"enabled": true, "fire_count": 1` after firing; it flips to `enabled: false` only when the next event
arrives. **[verified live]**. `max_fires: 0` = unlimited.

Re-enabling via `PUT /api/triggers/{id}` `{"enabled": true}` does **not** reset `fire_count`
(`triggers.rs:246-253`), so a maxed-out trigger will disable itself again on the very next event.
Delete and recreate instead.

## 2.6 CRUD

```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)
AID=<agent-uuid>

# create  (agent must exist in the live registry: kernel.rs:4245-4250, else 404)
curl -s -H "Authorization: Bearer $K" -H 'Content-Type: application/json' -d "{
  \"agent_id\": \"$AID\",
  \"pattern\": {\"content_match\": {\"substring\": \"crashed\"}},
  \"prompt_template\": \"ALERT: {{event}}. Investigate and report.\",
  \"max_fires\": 0
}" http://127.0.0.1:4200/api/triggers
# 201 {"trigger_id":"<uuid>","agent_id":"<uuid>"}

curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/triggers            # all
curl -s -H "Authorization: Bearer $K" "http://127.0.0.1:4200/api/triggers?agent_id=$AID"
curl -s -X PUT    -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
     -d '{"enabled":false}' http://127.0.0.1:4200/api/triggers/<tid>
curl -s -X DELETE -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/triggers/<tid>
```

Defaults on create: `prompt_template` → `"Event: {{event}}"`, `max_fires` → `0` (`routes.rs:1300-1304`).
A missing/invalid `agent_id` → 400; an unknown agent → **404** with
`{"error":"Trigger registration failed (agent not found?)"}`.

CLI (`main.rs:580-606`, `3389-3478`):
```bash
openfang trigger list [--agent-id <uuid>]
openfang trigger create <agent_id> '"lifecycle"' --prompt "Lifecycle: {{event}}" --max-fires 0
openfang trigger create <agent_id> '{"content_match":{"substring":"error"}}'
openfang trigger delete <trigger_id>
```
Remember `OPENFANG_API_KEY=...` (§1.9). Quote the pattern so the shell passes the JSON intact.

## 2.7 Triggers are not persisted

`TriggerEngine` holds two `DashMap`s and nothing else (`triggers.rs:83-88`). There is no `load`/`persist`
anywhere in the file. **Every restart wipes all manually created triggers.** **[verified live]** — after
`docker restart`, `GET /api/triggers` returned only the triggers auto-derived from proactive agent
manifests.

The only triggers that come back are those regenerated at boot from `ScheduleMode::Proactive`
(`kernel.rs:5009-5020` and `kernel.rs:1768-1777`):

```toml
[schedule]
proactive = { conditions = ["event:agent_spawned", "event:agent_terminated"] }
```
`background::parse_condition` (`background.rs:211-243`) accepts exactly:

| condition string | pattern |
|---|---|
| `all` (case-insensitive) | `All` |
| `event:agent_spawned` | `AgentSpawned { name_pattern: "*" }` |
| `event:agent_terminated` | `AgentTerminated` |
| `event:lifecycle` | `Lifecycle` |
| `event:system` | `System` |
| `event:memory_update` | `MemoryUpdate` |
| `memory:<key>` | `MemoryKeyPattern { key_pattern: <key> }` |
| anything else | `None` + `warn!("Unrecognized proactive condition format")` — silently no trigger |

Note there is **no** `event:content_match` form. The generated template is hard-coded
(`kernel.rs:5012-5015`):
`"[PROACTIVE ALERT] Condition '<cond>' matched: {{event}}. Review and take appropriate action. Agent: <name>"`
with `max_fires = 0`.

If you need durable hand-made triggers, re-create them from an external script after every daemon
start, or drive the automation from cron jobs (which *are* persisted) instead.

## 2.8 Trigger dispatch

`kernel.rs:4219-4232`: one `tokio::spawn` per match, each calling `kernel.send_message(aid, &msg)`.
There is **no** concurrency limit and **no** de-duplication — an `"all"` trigger on N agents produces N
concurrent agent turns per event, and an agent whose own turn publishes an event that matches its own
`"all"` trigger will loop. Failures are logged (`"Trigger dispatch failed: {e}"`) and dropped.
Guard with `max_fires` and narrow patterns.

---

# 3. SCHEDULES

Three unrelated things are called "scheduling" in this codebase. Keep them apart:

| # | Thing | Config surface | Persisted at | Code |
|---|---|---|---|---|
| A | **Cron jobs** — the real scheduler | `schedule_create` tool, `/api/cron/jobs`, `/api/schedules`, `openfang cron` | `$OF/cron_jobs.json` | `cron.rs`, `cron_delivery.rs` |
| B | **Agent `[schedule]`** — background self-prompt loops | `agent.toml` `[schedule]` | agent DB row + `$OF/agents/*/agent.toml` | `background.rs` |
| C | **Heartbeat monitor** — liveness + auto-recovery | `config.toml [heartbeat]`, `agent.toml [autonomous]` | n/a | `heartbeat.rs` |

## 3.1 (A) CronScheduler — storage and tick loop

`CronScheduler::new(home_dir, max_total_jobs)` → `persist_path = <home>/cron_jobs.json`
(`cron.rs:90-96`). **[verified live]**: the file is `$OF/cron_jobs.json`, an array of `JobMeta`:

```json
[
  {
    "job": {
      "id": "5274d8b1-da79-4e7c-a37b-7f4439f66aad",
      "agent_id": "17ffd1ca-b548-4132-a305-df3e32fbd9e3",
      "name": "probe-sysevent",
      "enabled": true,
      "schedule": { "kind": "cron", "expr": "0 * * * *", "tz": null },
      "action":   { "kind": "system_event", "text": "deploy finished successfully" },
      "delivery": { "kind": "none" },
      "delivery_targets": [],
      "created_at": "2026-08-09T21:54:55.177034994Z",
      "last_run": null,
      "next_run": "2026-08-09T22:00:00Z"
    },
    "one_shot": false,
    "last_status": null,
    "consecutive_errors": 0
  }
]
```
`JobMeta` (`cron.rs:41-51`) carries `one_shot`, `last_status`, `consecutive_errors` — these live only
in the scheduler, not in the `CronJob` type.

Persistence is atomic (write `.json.tmp` then `rename`, `cron.rs:126-139`) and happens on:
`cron_create` (`kernel.rs:7430`), `cron_cancel` (`kernel.rs:7462`), `DELETE /api/cron/jobs/{id}`,
`PUT /enable`, `POST /api/schedules`, every ~5 minutes (20 × 15 s ticks, `kernel.rs:4688-4695`), and at
shutdown (`kernel.rs:4670`). **`record_success`/`record_failure` do not persist** — `last_run` and
`last_status` from an on-demand run can be lost if the daemon dies before the next 5-minute flush.

**Tick loop** — `kernel.rs:4657-4704`:
- `tokio::time::interval(15s)`, `MissedTickBehavior::Skip`, first tick discarded.
- `due_jobs()` (`cron.rs:321-335`) returns enabled jobs with `next_run <= now` **and pre-advances
  `next_run`** in the same lock hold, so a long-running job cannot double-fire.
- Jobs in one tick run **serially** (`for job in due { kernel.cron_run_job(&job).await }`) — a job that
  takes 10 minutes delays every other job behind it.
- `compute_next_run_after` adds **+1 second** to the base before asking the `cron` crate for `.after()`,
  so a job can never re-fire in the same second (`cron.rs:444-465`).

**Failure handling** — `record_failure` (`cron.rs:393-418`): `consecutive_errors += 1`; at
`MAX_CONSECUTIVE_ERRORS = 5` (`cron.rs:21`) the job is **auto-disabled**
(`"Auto-disabling cron job after repeated failures"`). Re-enable with
`PUT /api/cron/jobs/{id}/enable {"enabled":true}`, which also zeroes the counter and recomputes
`next_run` (`cron.rs:187-199`).

**Limits**: `MAX_JOBS_PER_AGENT = 50` (`crates/openfang-types/src/scheduler.rs:12`);
global `max_cron_jobs` default `500` (`config.rs:1403-1405`, hot-reloadable via
`HotAction::UpdateCronConfig`).

## 3.2 CronJob schema

`crates/openfang-types/src/scheduler.rs:214-240`. All enums are internally tagged with `kind`
(`#[serde(tag = "kind", rename_all = "snake_case")]`).

**`schedule`** (`scheduler.rs:83-101`):
```json
{"kind":"at",   "at":"2026-08-10T09:00:00Z"}                     // one-shot, must be future, ≤ 1 year out
{"kind":"every","every_secs": 3600}                              // 60 ≤ n ≤ 86400
{"kind":"cron", "expr":"0 9 * * 1-5", "tz":"America/New_York"}   // tz optional, null = UTC
```

**`action`** (`scheduler.rs:110-134`):
```json
{"kind":"system_event","text":"..."}                                            // ≤ 4096 chars
{"kind":"agent_turn","message":"...","model_override":null,"timeout_secs":null} // msg ≤ 16384; timeout 10..=600, default 120
{"kind":"workflow_run","workflow_id":"<uuid-or-name>","input":null,"timeout_secs":null} // timeout 10..=3600, default 120
```
`workflow_id` is resolved as a UUID first, then by exact workflow **name**
(`kernel.rs:6672-6684`).
**`model_override` is parsed and stored but never used** — `cron_run_job` destructures
`CronAction::AgentTurn { message, timeout_secs, .. }` (`kernel.rs:6600-6604`) and drops it.

**`delivery`** — the single legacy destination (`scheduler.rs:143-160`):
```json
{"kind":"none"}
{"kind":"channel","channel":"telegram","to":"123456"}
{"kind":"last_channel"}
{"kind":"webhook","url":"https://..."}   // must start http:// or https://, ≤ 2048 chars
```

**`delivery_targets`** — multi-destination fan-out (`scheduler.rs:174-207`), tagged with `type`,
delivered concurrently and best-effort by `CronDeliveryEngine` (`cron_delivery.rs`):
```json
[{"type":"channel",   "channel_type":"telegram","recipient":"123"},
 {"type":"webhook",   "url":"https://hooks.example/x","auth_header":"Bearer abc"},
 {"type":"local_file","path":"/data/out/report.md","append":true},
 {"type":"email",     "to":"a@b.c","subject_template":"Cron: {job}"}]
```
The `auth_header` above is a **schema illustration, not a recommendation** — `GET /api/cron/*` is
public and echoes `delivery_targets` verbatim, so anything you put there is a published secret
(gotcha 31, `security-model.md` §10.6b).

Webhook payload is `{"job":<name>,"output":<text>,"timestamp":<rfc3339>}` with a 30 s timeout;
non-2xx counts as a failure (`cron_delivery.rs:211-233`). `local_file` creates parent dirs and appends
a trailing newline. `{job}` is the only placeholder in `subject_template`.

**Delivery outcome decides job success.** `kernel.rs:6620-6647` and `6690-6715`:
```rust
let delivered_to_channel = cron_deliver_response(...).await.is_ok();
if delivered_to_channel { record_success } else { record_failure("channel delivery failed") }
```
`cron_deliver_response` returns `Ok(())` for `CronDelivery::None` (`kernel.rs:7004`) **and for an
empty response** (`:6999`), so fire-and-forget jobs are fine. The failure-counts-as-job-failure rule
therefore only bites jobs configured with `delivery = channel` or `last_channel`: such a job whose
Telegram/Slack adapter is down is recorded as **failed** even though the LLM turn succeeded, and five
such runs auto-disable it (`MAX_CONSECUTIVE_ERRORS = 5`, `cron.rs:21`, applied `:401-406`). The
source says so outright at `kernel.rs:6637-6639`. `delivery_targets` failures are logged only and never affect the
job's status (`cron_fan_out_targets`, `kernel.rs:7122-7150`).

**Name validation** (`scheduler.rs:256-275`): non-empty, ≤ 128 chars, only alphanumerics, spaces,
`-` and `_`. `"my job!"` is rejected. Both the tool and `/api/schedules` sanitise for you
(`tool_runner.rs:2267-2285`, `routes.rs:9585-9599`) by replacing offending characters with `-`;
`/api/cron/jobs` does **not** and will 400.

## 3.3 Cron expression support

Two independent validators, with different rules:

1. `validate_cron_expr` (`scheduler.rs:427-455`) gates job creation: **exactly 5 whitespace-separated
   fields**, each non-empty, each containing only `0-9 * / - , ?`. **Letters are rejected**, so
   `0 9 * * MON-FRI`, `@daily`, `@hourly` all fail with
   `cron field N contains invalid characters`.
2. `compute_next_run_after` (`cron.rs:444-502`) actually schedules. It converts to the 7-field form the
   `cron` crate wants: 5 fields → `"0 {expr} *"`, 6 fields → `"{expr} *"`, anything else passed through.
   So `sec min hour dom mon dow` (6 fields) **works at runtime** but is rejected by the validator on
   creation — it can only reach the scheduler via a hand-edited `cron_jobs.json`.
   A parse failure falls back to `now + 1 hour` with
   `warn!("Failed to parse cron expression '{}': {}")`.

Timezone: `tz` is an IANA name parsed with `chrono_tz` (`cron.rs:472-491`). An unknown zone logs
`"Invalid timezone '{}' in cron job, falling back to UTC"` and silently uses UTC. `"UTC"` and `""` are
treated as no-tz.

**[verified live]** — `{"kind":"cron","expr":"0 * * * *","tz":null}` created at 21:54:55Z produced
`"next_run":"2026-08-09T22:00:00Z"` and fired at 22:00:05Z (next 15 s tick after the boundary).

## 3.4 Agent tools: `schedule_create` / `schedule_list` / `schedule_delete`

Dispatch: `crates/openfang-runtime/src/tool_runner.rs:318-320`.
Definitions: `tool_runner.rs:824-856`.

### `schedule_create` — `tool_runner.rs:2322-2368`
```json
{"description": "Check for new emails", "schedule": "every 5 minutes", "agent": "ops"}
```
- `description` and `schedule` required; `agent` optional.
- `description` is used for **both** the job `name` (sanitised, `tool_runner.rs:2267-2285`) **and** the
  `agent_turn` message the agent receives. There is no separate prompt field.
- Always builds `{"kind":"cron","expr":<parsed>,"tz":null}`, `{"kind":"agent_turn", ...,
  "timeout_secs":null}`, `{"kind":"none"}` delivery, `one_shot:false`. You cannot get `every`/`at`
  schedules, delivery targets, or a model override through this tool — use `cron_create` for that.
- **`schedule_create` is unusable for any job whose turn takes longer than 2 minutes.**
  `tool_runner.rs:2352` hardcodes `timeout_secs: null` on the `AgentTurn`, and `kernel.rs:6604`
  defaults that to **120 s**; a turn that runs longer is killed mid-flight. It also hardcodes
  `tz: null` = UTC. `openfang cron create` has the same defect (`main.rs:6110-6124`). The only
  mechanism that lets you set a real budget is `POST /api/cron/jobs` with an explicit
  `timeout_secs` (hard ceiling **600**, `scheduler.rs:36`).
- `agent` resolution (`resolve_schedule_target`, `tool_runner.rs:2294-2320`): empty or `"self"` →
  caller; a valid UUID → passthrough (**not** existence-checked here — `cron_create` will 400 later if
  it's bogus); otherwise `find_agents(name)` with exact-name preference, 1 fuzzy match accepted,
  ≥ 2 matches → `Agent name '<a>' is ambiguous (N matches). Pass the agent UUID.`
- Returns a human string, e.g.
  `Schedule created:\n  ID: <uuid>\n  Description: ...\n  Cron: 0 9 * * *\n  Original: daily at 9am\n  Agent: (self)`

**Natural-language grammar** — `parse_schedule_to_cron`, `tool_runner.rs:2147-2226`. Input is
lowercased and trimmed first.

| Input | Cron produced |
|---|---|
| 5 fields of only `0-9 * / , -` | passed through verbatim (note: **`?` is not allowed here**, though `validate_cron_expr` permits it) |
| `every minute`, `every 1 minute` | `* * * * *` |
| `every N minutes` (1–59) | `*/N * * * *` |
| `every hour`, `every 1 hour` | `0 * * * *` |
| `every N hours` (1–23) | `0 */N * * *` |
| `every day`, `every 1 day` | `0 0 * * *` |
| `every week`, `every 1 week` | `0 0 * * 0` |
| `daily at <t>` | `0 <h> * * *` |
| `weekdays at <t>` | `0 <h> * * 1-5` |
| `weekends at <t>` | `0 <h> * * 0,6` |
| `hourly` / `daily` / `weekly` / `monthly` | `0 * * * *` / `0 0 * * *` / `0 0 * * 0` / `0 0 1 * *` |
| anything else | `Err("Could not parse schedule '<x>'. Try: 'every 5 minutes', 'daily at 9am', ...")` |

`<t>` via `parse_time_to_hour` (`tool_runner.rs:2229-2265`): `9am`→9, `12am`→0, `6pm`→18, `12pm`→12,
`14:00`→14, `14`→14. **Minutes are silently discarded** in the `HH:MM` form (`daily at 14:30` →
`0 14 * * *`), and the am/pm form with minutes **errors out** (`daily at 9:30am` →
`Invalid time: 9:30am`, because `"9:30"` fails `parse::<u32>()`).
`every 90 minutes` → `Err("Minutes must be 1-59, got 90")`. `every 30 seconds` → unparseable.

### `schedule_list` — `tool_runner.rs:2370-2409`
Takes no arguments and lists **only the calling agent's** jobs (`cron_list(caller_agent_id)`).
An agent that used `schedule_create` with `agent: "other-agent"` cannot see or manage that job.
Output is a formatted text block; it prints `Created:` but, despite the tool description, **not**
next-run times.

### `schedule_delete` — `tool_runner.rs:2411-2421`
`{"id": "<job-uuid>"}` → `kh.cron_cancel(id)` → `CronScheduler::remove_job`.
**There is no ownership check anywhere on this path** (`kernel.rs:7453-7467`, `cron.rs:178-183`):
any agent with the tool can delete any other agent's cron job if it learns the UUID.

### Lower-level `cron_*` tools
`cron_create` (`tool_runner.rs:2427-2435`) passes the raw JSON straight to `kernel.cron_create` with
the caller as the agent — use this for `every`/`at` schedules, `delivery`, `delivery_targets`,
`one_shot`, `workflow_run`. `cron_list` returns pretty JSON. `cron_cancel` takes `{"job_id": ...}`
(note: different key name from `schedule_delete`'s `{"id": ...}`).

### Tool availability
`ToolProfile::Automation` (`crates/openfang-types/src/agent.rs:326-337`) is
`file_read, file_write, file_list, shell_exec, web_fetch, web_search, agent_send, agent_list,
memory_store, memory_recall` — it contains **no scheduling tools at all**. To give an agent
`schedule_create`/`cron_create` you need `profile = "full"` (or no profile, which yields the full 65-tool
set — **[verified live]**, the default `assistant` gets all 65), or an explicit `tool_allowlist`.

## 3.5 REST surfaces for cron

Two overlapping route families, both backed by the same `CronScheduler`.

### `/api/cron/jobs` — native shape (`routes.rs:11311-11444`, `server.rs:646-663`)
```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)
AID=<agent-uuid>

# create — agent_id MUST be a UUID (kernel.rs:7406-7408 parses it strictly)
curl -s -H "Authorization: Bearer $K" -H 'Content-Type: application/json' -d "{
  \"agent_id\": \"$AID\",
  \"name\": \"morning-brief\",
  \"schedule\": {\"kind\":\"cron\",\"expr\":\"0 6 * * *\",\"tz\":\"Europe/London\"},
  \"action\":   {\"kind\":\"agent_turn\",\"message\":\"Summarise overnight alerts.\",
                 \"model_override\":null,\"timeout_secs\":300},
  \"delivery\": {\"kind\":\"none\"},
  \"delivery_targets\": [{\"type\":\"local_file\",\"path\":\"/data/out/brief.md\",\"append\":true}],
  \"one_shot\": false
}" http://127.0.0.1:4200/api/cron/jobs
# 201 {"result":"{\"job_id\":\"<uuid>\",\"status\":\"created\"}"}   <- note: double-encoded JSON string

curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/cron/jobs            # {"jobs":[...],"total":N}
curl -s -H "Authorization: Bearer $K" "http://127.0.0.1:4200/api/cron/jobs?agent_id=$AID"
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/cron/jobs/<jid>/status  # full JobMeta
curl -s -X PUT    -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
     -d '{"enabled":false}' http://127.0.0.1:4200/api/cron/jobs/<jid>/enable
curl -s -X POST   -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/cron/jobs/<jid>/run
curl -s -X DELETE -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/cron/jobs/<jid>
```
- Enable **and** disable both go through `PUT .../enable` with an `{"enabled": bool}` body. There is no
  `/disable` route. **[verified live]**: `POST .../enable` → **405**, `POST .../disable` → **404**.
- `POST .../run` uses `try_claim_for_run` (`cron.rs:347-362`) which atomically checks existence +
  enabled + reserves `next_run` (only advancing it if already overdue), then executes in a background
  task. Returns `{"status":"triggered","job_id":...}` immediately — poll `/status`.
  404 if unknown, 400 if disabled.
- **`GET /api/cron/*` is in the public allowlist** (`middleware.rs:136`) — job definitions, agent IDs and
  prompt text are readable without any credential. Writes require the key.

### `/api/schedules` — legacy/dashboard shape (`routes.rs:9123-9581`, `server.rs:347-363`)
Kept for dashboard compatibility after #1069 (the old implementation wrote to a shared-memory key that
nothing ever read). It is a thin adapter over the same scheduler.
```bash
curl -s -H "Authorization: Bearer $K" -H 'Content-Type: application/json' -d '{
  "name": "Morning brief", "cron": "0 6 * * *",
  "agent_id": "assistant",
  "message": "Summarise overnight alerts.",
  "enabled": true,
  "delivery_targets": []
}' http://127.0.0.1:4200/api/schedules
```
- Unlike `/api/cron/jobs`, `agent_id` here accepts a UUID **or** an agent name — `create_schedule`
  parses it as an `AgentId` first (`routes.rs:9226`) and falls back to matching `a.name` against the
  live registry (`routes.rs:9238`); neither match is a 404 `Agent not found: <value>`.
  (Do not put that annotation inside the body: JSON has no comments and the `Json<Value>` extractor
  rejects the request with a deserialisation 400 before the handler runs.)
- `cron` must be exactly 5 fields (checked at `routes.rs:9209-9217`); `name` is sanitised.
- Always creates an `agent_turn` action; an empty `message` becomes `"[Scheduled task '<name>']"`.
- `GET /api/schedules` → `{"schedules":[{id,name,cron,agent_id,message,enabled,created_at,last_run,
  next_run,last_status,delivery_targets}],"total":N}`.
- `PUT /api/schedules/{id}` can only toggle `enabled` and replace `delivery_targets`; schedule/action
  edits are accepted and ignored — delete and recreate.
- `GET /api/schedules/{id}/delivery-log` is a **stub**: it always returns `"entries": []`
  (`routes.rs:9543-9580`, comment at 9567: "Delivery history is not yet persisted").

### One-shot migration
`migrate_shared_memory_schedules` (`kernel.rs:5052-5131`) runs once at boot, converts legacy
`__openfang_schedules` shared-memory entries into real cron jobs, clears the key and sets
`__openfang_schedules_migrated_v1`. Entries whose target agent can't be resolved are skipped with a
`warn!`.

## 3.6 CLI: `openfang cron` — largely broken in v0.6.9

Definitions `main.rs:673-707`, implementations `main.rs:6034-6168`. **[verified live]**, all four:

| Command | Actual behaviour |
|---|---|
| `openfang cron list` | Prints **raw JSON**, never the table. `cmd_cron_list` does `body.as_array()` but `/api/cron/jobs` returns an object `{"jobs":[...],"total":N}`, so it always falls to the `else` pretty-print branch (`main.rs:6045`, `6074-6078`). The table code also reads non-existent keys `cron_expr` and `prompt`. `--json` is the only usable form. |
| `openfang cron create <agent> <spec> <prompt>` | The `<agent>` arg is documented as "Agent name or ID" but is sent as `agent_id`, which `cron_create` parses strictly as a UUID → `✘ Failed: Invalid agent ID: invalid character: found 's' at 2` for `assistant`. With a real UUID **the job IS created** but the CLI still prints `✘ Failed: ?`, because it looks for `body["id"]` while the API returns `{"result": "..."}` (`main.rs:6127`). |
| `openfang cron enable <id>` | Sends `POST /api/cron/jobs/{id}/enable` → **405**, no body → the CLI's `body.get("error").is_none()` check passes → prints `✔ Cron job  enabled.` while nothing happened. Also never sends the `{"enabled":...}` body. |
| `openfang cron disable <id>` | `POST .../disable` → **404**; same false success message. |

**Use `curl` against `/api/cron/jobs` for cron management on v0.6.9.** `openfang cron list --json`
is the only CLI cron command that is both honest and useful.

## 3.7 (B) Agent-level `[schedule]` — the background self-prompt loops

`ScheduleMode` (`crates/openfang-types/src/agent.rs:225-245`), executed by
`BackgroundExecutor::start_agent` (`background.rs:48-186`), wired at
`kernel.rs:4446-4474` (boot, staggered 500 ms apart) and `kernel.rs:5002-5030` (per agent).

```toml
# agent.toml — pick exactly one, or omit the section entirely
[schedule]
continuous = { check_interval_secs = 120 }        # self-prompt every N seconds (default 60)
# periodic  = { cron = "every 5m" }               # self-prompt every parse_cron_to_secs(cron) seconds
# proactive = { conditions = ["event:system"] }   # registers triggers, no timer
```
```toml
schedule = "reactive"    # top-level scalar form; the safe default   [verified live]
```
Omitting `[schedule]` also yields `Reactive` (`#[default]`, `agent.rs:230-231`).

Loop bodies (`background.rs:74-179`):
- `Continuous` prompt: `"[AUTONOMOUS TICK] You are running in continuous mode. Check your goals,
  review shared memory for pending tasks, and take any necessary actions. Agent: {name}"`
- `Periodic` prompt: `"[SCHEDULED TICK] You are running on a periodic schedule ({cron}). Perform your
  routine duties. Agent: {name}"`
- Both are plain `tokio::time::sleep(interval)` loops — **the first tick happens `interval` after
  daemon start, not at any wall-clock time**. There is no phase alignment and no catch-up.
- Skip-if-busy via an `AtomicBool` (`background.rs:85-91`), and a global
  `Semaphore(MAX_CONCURRENT_BG_LLM = 5)` across *all* background agents (`background.rs:18`).
- `Reactive` and `Proactive` start no task at all.

### `parse_cron_to_secs` — `periodic` does NOT understand cron
`background.rs:254-284` accepts only `every <N>s|m|h|d`. **Everything else falls back to 300 seconds**
with `warn!("Unparseable cron expression, defaulting to 300s")`.

**[verified live]** — an agent with `[schedule] periodic = { cron = "0 9 * * *" }` logged at boot:
```
WARN openfang_kernel::background: Unparseable cron expression, defaulting to 300s cron=0 9 * * *
INFO openfang_kernel::background: Starting periodic background loop agent=sched-cron5
     cron=0 9 * * * interval_secs=300
```
So "run this once a day at 09:00" written as a normal cron expression becomes **288 LLM turns a day**.
If you want wall-clock scheduling, use a **cron job** (§3.1–3.5), never `[schedule] periodic`.

Grep for the trap on any host:
```bash
docker logs openfang-openfang-1 2>&1 | grep -E "Unparseable cron|Starting (periodic|continuous) background loop"
```

## 3.8 (C) Heartbeat monitor

`crates/openfang-kernel/src/heartbeat.rs` + the loop at `kernel.rs:4832-4999`.

Constants (`heartbeat.rs:19-29`, `133`):
`DEFAULT_CHECK_INTERVAL_SECS = 30` (**not configurable** — `HeartbeatConfig.check_interval_secs` is
never overridden from config), `UNRESPONSIVE_MULTIPLIER = 2`,
`DEFAULT_MAX_RECOVERY_ATTEMPTS = 3`, `DEFAULT_RECOVERY_COOLDOWN_SECS = 60`, `IDLE_GRACE_SECS = 10`.

Per-agent timeout selection — `heartbeat.rs:161-166`, the whole of issue #1252:
```rust
let timeout_secs = entry_ref.manifest.autonomous.as_ref()
    .map(|a| a.heartbeat_interval_secs * UNRESPONSIVE_MULTIPLIER)   // Some(..) => 2 × interval
    .unwrap_or(config.default_timeout_secs) as i64;                 // None     => config value
```

Exemptions, in evaluation order:
1. `never_active`: `(last_active - created_at) <= 10s` and state `Running` → skipped entirely
   (`heartbeat.rs:180-190`, issue #844). A freshly spawned agent is immune until it processes its
   first real message.
2. `should_exempt_idle_reactive_agent` (`heartbeat.rs:139-141`): `ScheduleMode::Reactive` **and** no
   entry in `kernel.running_tasks` → the monitor resets it to `Running` if Crashed and clears the
   recovery tracker (`kernel.rs:4867-4878`). Note `check_agents` still emits the
   `WARN ... Agent is unresponsive` line for these — the exemption happens one layer up, so **that
   warning alone does not mean anything happened**. **[verified live]**: `assistant` logged
   `inactive_secs=1753 timeout_secs=180` repeatedly and stayed `Running`.
3. `quiet_hours` from `[autonomous]`, checked with `is_quiet_hours` (`heartbeat.rs:232-272`).
   The format is **`"HH:MM-HH:MM"` in UTC**, cross-midnight supported. `docs/configuration.md:1597`
   calls it "Cron expression for quiet hours" — that is wrong; a cron string parses to `false` and the
   agent is never quiet.

State machine for a non-exempt agent (`kernel.rs:4889-4993`):
`Running` + `inactive_secs > timeout` → `set_state(Crashed)` + `HealthCheckFailed` event →
next tick sees `Crashed`, checks the 60 s cooldown, `record_attempt`, `set_state(Running)` +
`HealthCheckFailed{unresponsive_secs: 0}` → after `max_recovery_attempts` (3) → `Terminated`.

**In practice it never terminates.** `AgentRegistry::set_state` bumps `last_active`
(`registry.rs:54-62`), so each recovery makes the agent look fresh; 30 s later it is judged healthy and
`recovery_tracker.reset()` fires. **[verified live]**, a perfect 120-second forever-loop:

```
21:58:20 WARN heartbeat: Agent is unresponsive agent=hb-auto inactive_secs=87 timeout_secs=60
21:58:20 WARN kernel:    Unresponsive Running agent marked as Crashed for recovery agent=hb-auto
21:58:50 WARN heartbeat: Agent is crashed — eligible for recovery agent=hb-auto inactive_secs=29
21:58:50 INFO kernel:    Auto-recovering crashed agent (attempt 1/3) agent=hb-auto attempt=1 max=3
21:59:20 INFO kernel:    Agent recovered successfully — resetting recovery tracker agent=hb-auto
22:00:20 WARN heartbeat: Agent is unresponsive agent=hb-auto inactive_secs=89 timeout_secs=60
22:00:20 WARN kernel:    Unresponsive Running agent marked as Crashed for recovery agent=hb-auto
```
The recovery path itself makes **no LLM call** — it only flips state and publishes an event straight to
the bus. The spend reported in #1252 comes from the *interaction* with §3.7 loops and with triggers.

## 3.9 The wake hook — `/hooks/wake` and `/hooks/agent`

Handlers `routes.rs:11511-11658`; routes `server.rs:666-667`; config type
`WebhookTriggerConfig` at `config.rs:442-463`; payloads `crates/openfang-types/src/webhook.rs`.

Disabled by default. Enable in `$OF/config.toml`:
```toml
[webhook_triggers]
enabled = true
token_env = "OPENFANG_WEBHOOK_TOKEN"   # the NAME of an env var, never the token itself
max_payload_bytes = 65536
rate_limit_per_minute = 30
```
and put the secret in `$OF/secrets.env` (loaded into the process env by
`crates/openfang-cli/src/dotenv.rs:22-36`, after `$OF/.env`):
```
OPENFANG_WEBHOOK_TOKEN=<at least 32 characters>
```
`validate_webhook_token` (`routes.rs:11939-11958`) requires `t.len() >= 32`. A shorter token makes
every request 401 with `{"error":"Invalid or missing token"}` and no other diagnostic —
**[verified live]** with a 30-char token.

### Double authentication — the part that will waste your afternoon
`/hooks/*` is **not** in the public allowlist (`middleware.rs:98-140`), so the API-key middleware runs
first and wants `Authorization: Bearer <API_KEY>`. Then the handler wants
`Authorization: Bearer <OPENFANG_WEBHOOK_TOKEN>`. Same header, two different values.

`X-API-Key` does **not** rescue you: `middleware.rs:179-184` uses
`bearer_token.or_else(|| x-api-key)` — the fallback is only consulted when there is **no** Bearer
header at all. **[verified live]**:

| Attempt | Result |
|---|---|
| `Authorization: Bearer <API_KEY>` only | 401 `{"error":"Invalid or missing token"}` (middleware ok, webhook check fails) |
| `Authorization: Bearer <WEBHOOK_TOKEN>` only | 401 `{"error":"Invalid API key"}` (middleware fails first) |
| `X-API-Key: <API_KEY>` + `Authorization: Bearer <WEBHOOK_TOKEN>` | 401 `{"error":"Invalid API key"}` — X-API-Key ignored |
| **`?token=<API_KEY>` + `Authorization: Bearer <WEBHOOK_TOKEN>`** | **200** ✅ |

The only other way is to leave `api_key` empty and call from loopback (`middleware.rs:155-158`), or to
set `api_key` equal to the webhook token.

Working invocations **[verified live]** — but note that neither `[webhook_triggers]` nor
`OPENFANG_WEBHOOK_TOKEN` is configured on this box today, so `$T` comes back empty and both calls
return 404 `{"error":"Webhook triggers not enabled"}`. Do the two steps above first.
```bash
D=/var/lib/docker/volumes/openfang_openfang-data/_data
K=$(grep -m1 '^api_key = ' $D/config.toml | cut -d'"' -f2)
T=$(grep '^OPENFANG_WEBHOOK_TOKEN' $D/secrets.env | cut -d= -f2)

# inject a system event (fires only "all" triggers — see §2.4)
curl -s -X POST -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"text":"deployment of service X finished","mode":"now"}' \
  "http://127.0.0.1:4200/hooks/wake?token=$K"
# {"mode":"now","status":"accepted"}

# run one isolated agent turn and get the answer back synchronously
curl -s -X POST -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"message":"Reply with the single word PONG","agent":"assistant","timeout_secs":60}' \
  "http://127.0.0.1:4200/hooks/agent?token=$K"
# {"agent_id":"...","response":"PONG","status":"completed","usage":{"input_tokens":8460,"output_tokens":61}}
```

`WakePayload` (`webhook.rs:17-23`, validation `62-88`): `text` non-empty, ≤ 4096 chars, **no control
characters except `\n`** — a literal tab makes it 400. `mode` is `"now"` (default) or
`"next_heartbeat"`. **`mode` is decorative**: `webhook_wake` publishes immediately regardless and only
echoes it back (`routes.rs:11549-11566`). **[verified live]** — `next_heartbeat` returned 200 and the
event was published at once.

`AgentHookPayload` (`webhook.rs:27-49`): `message` non-empty ≤ 16384; `agent` name or UUID (omitted →
`registry.list().first()`, i.e. an arbitrary agent); `timeout_secs` validated to 10..=600 but
**never applied** — the handler calls `send_message` with no timeout wrapper
(`routes.rs:11640`). `deliver`, `channel` and `model` are validated and then **silently ignored**.

Neither hook is documented in `docs/api-reference.md`.

---

# 4. Bug dossier

## 4.1 #1253 — `collect` joins pre-fan-out outputs

**Status**: open, reproduces exactly on v0.6.9. **[verified live]**

**Mechanism.** `all_outputs` is a single run-wide buffer. Every `sequential` step
(`workflow.rs:515`), every `conditional` step that fires (`workflow.rs:689`), every fan-out branch
(`workflow.rs:595`) and every completed loop (`workflow.rs:780`) appends to it. `Collect`
(`workflow.rs:635-642`) joins **the whole buffer**:

```rust
StepMode::Collect => {
    current_input = all_outputs.join("\n\n---\n\n");   // <-- no fan-out group boundary
    all_outputs.clear();
    all_outputs.push(current_input.clone());
    ...
}
```
The engine never records where the preceding fan-out group started, so it cannot slice the buffer.

**Live repro** on `openfang-openfang-1`, workflow `sequential → fan_out → fan_out → collect` with three
steps that reply with one word each:

```
POST /api/workflows/<id>/run  {"input":"seed"}  ->  status: completed
OUTPUT:
PRE

---

ALPHA

---

BETA
```
`PRE` is the pre-fan-out sequential output and should not be there. Expected: `ALPHA\n\n---\n\nBETA`.

**Blast radius.** Any `... → fan_out* → collect` pipeline (the shape in `docs/workflows.md` Example 3,
if you prepend a preprocessing step) feeds stale pre-branch context into the synthesis step. It
compounds: `Collect` re-seeds `all_outputs` with the joined blob, so a second `collect` later in the
run emits `join(previous_blob, everything_since)` — multi-phase workflows keep dragging earlier phases
forward.

**Workarounds.**
- Start the workflow *with* the fan-out group — make the fan-out steps the first steps and pass the
  seed through `{{input}}`.
- Or drop `collect` entirely: give each branch an `output_var` and have the synthesis step reference
  them explicitly (`{{creative}}`, `{{technical}}`, ...). This is deterministic and immune to the bug.
- Or split into two workflows and chain them from a cron `workflow_run` job.

## 4.2 #1192 — deleted workflow reappears after restart

**Status**: open, reproduces exactly. **[verified live]**

**Mechanism** (three files):
1. `POST /api/workflows` writes `$OF/workflows/<uuid>.json` — `routes.rs:985-1001`.
2. `DELETE /api/workflows/{id}` calls only `workflows.remove_workflow(id)`, which is
   `self.workflows.write().await.remove(&id).is_some()` — `workflow.rs:236-238`,
   `routes.rs:1239`. **No filesystem call.**
3. At boot, `load_workflows_from_dir` re-registers every `*.json` in the directory —
   `kernel.rs:4346-4383`, invoked from `kernel.rs:4634-4650`.

**Live transcript.**
```
POST   /api/workflows                    -> {"workflow_id":"b14740a8-…"}
ls     $OF/workflows/                    -> b14740a8-….json      (419 bytes)
DELETE /api/workflows/b14740a8-…         -> {"status":"removed", …}
GET    /api/workflows                    -> []
ls     $OF/workflows/                    -> b14740a8-….json      STILL THERE
docker restart openfang-openfang-1
GET    /api/workflows                    -> [{"id":"b14740a8-…","name":"probe-persist","steps":1, …}]
```
The resurrected workflow keeps its **original UUID**, so any dashboard bookmark or cron
`workflow_run` job silently starts working again.

**Sibling bug (same root, not in the issue): `PUT` doesn't persist either.**
`update_workflow` (`routes.rs:1206-1211` → `workflow.rs:245-256`) mutates memory only.
**[verified live]**:
```
POST  probe-update (v1, 1 step)      -> id b4bfc07a-…
PUT   probe-update-RENAMED (v2, 2 steps)
GET   /api/workflows/<id>            -> probe-update-RENAMED | v2 | steps: 2
cat   $OF/workflows/<id>.json        -> probe-update          | v1 | steps: 1
docker restart
GET   /api/workflows/<id>            -> probe-update          | v1 | steps: 1   (edit lost)
```

**Operational rule for v0.6.9: the directory is the source of truth. Always pair API mutations with a
filesystem action.**
```bash
# delete for real
curl -s -X DELETE -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/workflows/$ID
rm -f $OF/workflows/$ID.json

# update for real: edit the on-disk file (on-disk shape! §1.1), then restart, or
# DELETE + rm + POST to get a fresh definition and a fresh id
```
A cheap audit — anything listed here is a workflow that will come back from the dead. `$OF/workflows`
is created lazily on the first `POST /api/workflows` and does not exist on this box today, hence the
guard:
```bash
[ -d $OF/workflows ] || echo 'no workflows dir — nothing to reconcile'
comm -23 \
  <(ls /var/lib/docker/volumes/openfang_openfang-data/_data/workflows/ 2>/dev/null | sed 's/\.json$//' | sort) \
  <(curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/workflows \
     | python3 -c 'import sys,json;[print(w["id"]) for w in json.load(sys.stdin)]' | sort)
```
You can also relocate the directory with `workflows_dir` in `config.toml`
(`config.rs:1281`; default `$OF/workflows`) — undocumented in `docs/configuration.md`.

## 4.3 #1252 — `[heartbeat] default_timeout_secs` ignored

**Status**: open. The reporter's diagnosis ("hardcoded 60s") is *almost* right; the precise cause is
narrower and the fix is different. **[verified live]**

**The config IS read.** `kernel.rs:4839-4842`:
```rust
let config = HeartbeatConfig {
    default_timeout_secs: self.config.heartbeat.default_timeout_secs,
    ..HeartbeatConfig::default()
};
```
**But it is only consulted for agents with no `[autonomous]` section.** `heartbeat.rs:161-166`:
```rust
let timeout_secs = entry_ref.manifest.autonomous.as_ref()
    .map(|a| a.heartbeat_interval_secs * UNRESPONSIVE_MULTIPLIER)
    .unwrap_or(config.default_timeout_secs) as i64;
```
`AutonomousConfig::default().heartbeat_interval_secs == 30` (`crates/openfang-types/src/agent.rs:85-95`)
and `UNRESPONSIVE_MULTIPLIER == 2` (`heartbeat.rs:23`). **30 × 2 = 60.** Because `autonomous` is an
`Option<AutonomousConfig>` with serde defaults, writing *any* `[autonomous]` table — even a single
unrelated key — pins that agent to a 60-second timeout and makes `[heartbeat]` a no-op for it.

The bundled `agents/assistant/agent.toml` does exactly that:
```toml
[autonomous]
max_iterations = 100
```

**Controlled live experiment.** `config.toml` set to `[heartbeat] default_timeout_secs = 86400`;
two identical proactive agents auto-spawned, differing only in the presence of `[autonomous]`:

| agent | `[autonomous]` | outcome after 60–90 s idle |
|---|---|---|
| `hb-auto` | `max_iterations = 5` | `WARN heartbeat: Agent is unresponsive agent=hb-auto inactive_secs=87 timeout_secs=60` → marked Crashed → 120 s crash/recover loop forever (transcript in §3.8) |
| `hb-plain` | *(absent)* | never flagged — the 86400 value was honoured |
| `assistant` | *(absent, Reactive)* | never flagged |

So the config key works; the manifest silently overrides it.

**Fix / workaround (pick one).**
1. **Set the interval explicitly on every agent that has `[autonomous]`.** The effective timeout is
   `2 × heartbeat_interval_secs`, so for a 1-hour grace period:
   ```toml
   [autonomous]
   max_iterations = 100
   heartbeat_interval_secs = 1800      # -> 3600 s unresponsive threshold
   ```
2. **Delete the `[autonomous]` table** from agents that don't need `quiet_hours` / `max_iterations` /
   `max_restarts`, then `[heartbeat] default_timeout_secs` applies:
   ```toml
   [heartbeat]
   default_timeout_secs = 86400
   ```
3. For agents that are genuinely idle-by-design, prefer `schedule = "reactive"` — reactive agents are
   exempt while no turn is running (§3.8 exemption 2) regardless of any timeout.

**Two extra traps.**
- The monitor builds `HeartbeatConfig` once and moves it into the spawned task
  (`kernel.rs:4838-4846`), and `[heartbeat]` still appears **nowhere** in `build_reload_plan`
  (`crates/openfang-kernel/src/config_reload.rs:182-328`) — no `HotAction`, not even a
  `restart_required` entry, even after the applied/deferred split the reload plan now carries for
  everything else (`ReloadPlan.applied_actions` / `.deferred_actions`, same file). Editing
  `[heartbeat]` does nothing until a full process restart, and the hot-reloader will not tell you —
  it has no entry to be honest or dishonest about.
- `docs/configuration.md:1602` describes `heartbeat_interval_secs` as "Seconds between heartbeat
  health checks". It is not — the check interval is the hardcoded 30 s
  `DEFAULT_CHECK_INTERVAL_SECS`; `heartbeat_interval_secs` is half the unresponsive threshold.

**Audit command** (`$OF/agents/` is empty on this box, so it prints nothing — that is a clean result,
not a failure; `-s` and `2>/dev/null` keep it quiet on a host that never ran `openfang init`):
```bash
grep -sl '^\[autonomous\]' $OF/agents/*/agent.toml 2>/dev/null | while read f; do
  grep -q 'heartbeat_interval_secs' "$f" || echo "60s timeout (config ignored): $f"; done
```

## 4.4 #1206 — sample agent schedules cause surprise LLM spend after the v0.6.9 auto-spawn

**Status**: open. This is the expensive one. Chain of four independent facts:

**1. `openfang init` installs 31 agent manifests into `$OF/agents/`** (not 30 —
`crates/openfang-cli/src/bundled_agents.rs` has 31 `include_str!` entries and `/opt/openfang/agents`
contains 31 directories).
`main.rs:1287` → `bundled_agents::install_bundled_agents(&openfang_dir.join("agents"))`
(installer at `bundled_agents.rs:123-135`; it skips files that already exist, `:127-129`). Every template is `include_str!`-embedded at
compile time; the installer skips files that already exist ("preserve user customization"). This runs
in both interactive and `--quick` init, and in the non-TTY auto-quick path (`main.rs:1289-1296`).

**2. Three of those templates carry an active `[schedule]`.** From `/opt/openfang/agents/*/agent.toml`:

| Agent | `[schedule]` | `[resources] max_llm_tokens_per_hour` | Turns/hour |
|---|---|---|---|
| `orchestrator` | `continuous = { check_interval_secs = 120 }` | 500000 | 30 |
| `ops` | `periodic = { cron = "every 5m" }` | 50000 | 12 |
| `health-tracker` | `periodic = { cron = "every 1h" }` | 100000 | 1 |
| `security-auditor` | `proactive = { conditions = ["event:agent_spawned","event:agent_terminated"] }` | 150000 | 0 (event-driven only) |

Total **43 self-prompted agent turns per hour = 1032/day** (720 + 288 + 24), with zero user interaction.
`grep -rn "schedule" /opt/openfang/agents/*/agent.toml` confirms no other template has one.

**3. v0.6.9 auto-spawns everything in `$OF/agents/`.** `kernel.rs:1465-1546` (issue #1140):
for each `$OF/agents/<dir>/agent.toml` whose name is not already in the registry, parse and
`spawn_agent`. Before this change, on-disk manifests were inert templates; now presence on disk means
*live*. Boot log line: `Auto-spawned agent from ~/.openfang/agents agent=<name> id=<uuid>` followed by
`Auto-spawned N agent(s) from ~/.openfang/agents`. **[verified live]** — dropping two directories into
`$OF/agents/` and restarting spawned both immediately.

**4. `start_background_agents` then starts a loop for every non-Reactive agent.**
`kernel.rs:4446-4474` collects all agents whose `manifest.schedule != Reactive` and calls
`start_background_for_agent` 500 ms apart; `background.rs:59-179` spawns the sleep loop.
Log lines: `Starting continuous background loop agent=... interval_secs=120` /
`Starting periodic background loop agent=... cron=every 5m interval_secs=300` /
`Started N background agent loop(s) (staggered)`.

Each tick is a **full agent turn**: system prompt + memory + the whole tool catalogue. On this instance
a trivial one-word turn cost **~8.2–8.5 k input tokens** because 65 tool schemas are injected
(**[verified live]**, `Agent loop completed agent=hb-plain iterations=1 tokens=8230`). At 43 turns/hour
that is ~355 k input tokens/hour of pure idle traffic — which is why `orchestrator` hits its
500 k/hour cap in a couple of hours.

Mitigating factors that are *not* enough on their own: skip-if-busy per agent, the global
`Semaphore(5)`, and the per-agent hourly token quota (`AgentScheduler`, **`openfang-kernel`**`/src/scheduler.rs:78-100` — a different file from the `openfang-types` one cited in §3.2) — the
quota merely makes calls start failing, and its window is an in-memory `Instant` that resets on every
daemon restart (`openfang-kernel/src/scheduler.rs:11-41`).

### Prevention — do this before the first boot after upgrading

**Neutralise the manifests, don't delete them.** `install_bundled_agents` skips files that already
exist, so an edited file survives future `openfang init` runs, whereas a deleted directory is
recreated with the aggressive default.

```bash
OF=/var/lib/docker/volumes/openfang_openfang-data/_data   # = /data inside the container
# On this box $OF/agents/ is empty, so the loop is a no-op — this is for a host that ran
# `openfang init`. Do NOT use ${OPENFANG_HOME:-$HOME/.openfang}: OPENFANG_HOME is unset in the
# host shell, so it resolves to /root/.openfang, which does not exist, and every iteration is
# skipped by the `[ -f "$f" ] || continue` below while the script reports nothing.
for a in orchestrator ops health-tracker; do
  f="$OF/agents/$a/agent.toml"
  [ -f "$f" ] || continue
  cp "$f" "$f.bak"
  # drop the [schedule] table and everything under it up to the next table header
  python3 - "$f" <<'PY'
import re, sys
p = sys.argv[1]; s = open(p).read()
s = re.sub(r'(?ms)^\[schedule\][^\[]*', '', s)
if not re.search(r'(?m)^schedule\s*=', s):
    s = s.rstrip() + '\nschedule = "reactive"\n'
open(p, 'w').write(s)
PY
done
grep -H '^schedule' $OF/agents/*/agent.toml
```
`schedule = "reactive"` as a top-level scalar parses correctly and starts no loop —
**[verified live]** (`sched-reactive` auto-spawned with no background-loop log line).

**Then confirm nothing is ticking:**
```bash
docker logs openfang-openfang-1 2>&1 \
  | grep -E "Auto-spawned|Starting (continuous|periodic) background loop|Started .* background agent loop"
# desired output: only "Auto-spawned ..." lines, no "Starting ... loop" lines
```

**Belt and braces.** Killing an agent at runtime is **not** durable — `DELETE /api/agents/{id}`
removes the registry/DB row, but the next boot re-auto-spawns it from `$OF/agents/`. Only the on-disk
manifest matters. Additional guards, in order of bluntness:
```toml
# config.toml — hard cost ceilings applied to agents that don't set their own
[budget]
max_hourly_usd  = 0.25
max_daily_usd   = 2.0
max_monthly_usd = 20.0
default_max_llm_tokens_per_hour = 50000   # >0 OVERRIDES every agent's own limit
```
(`config.rs:1376-1401`, applied by `apply_budget_defaults`, `kernel.rs:6831-6852`. Note the token key
*overrides* rather than defaults, unlike the USD keys which only fill in zeros.)
```toml
# per agent, if you do want a timer but a cheap one
[schedule]
periodic = { cron = "every 6h" }
[resources]
max_llm_tokens_per_hour = 20000
```
And remember §3.7: **do not** "fix" `every 5m` by writing `0 9 * * *` — that is still every 5 minutes.

**Docker note.** The image copies the templates to `/opt/openfang/agents` inside the container, *not*
to the `/data` volume (`Dockerfile`, `COPY --from=builder /build/agents /opt/openfang/agents`), and
`OPENFANG_HOME=/data`. A container that has never run `openfang init` therefore has an empty
`$OF/agents/` and is **not** affected — **[verified live]**, this instance auto-spawned only the
default `assistant` created by the "no agents found" fallback (`kernel.rs:1548-1560`), which is
`Reactive`. Native installs and anyone who ran `openfang init` are the exposed population.

---

# 5. Gotchas (all verified)

**Workflows**
1. `collect` joins the entire run's output buffer, not the preceding fan-out group (#1253, §4.1).
2. Deleting or updating a workflow via the API does not touch `$OF/workflows/<id>.json`; the old
   definition returns on restart with the same UUID (#1192, §4.2).
3. A step whose agent can't be resolved leaves the run stuck in `state: "running"` forever, and stuck
   runs are never evicted, so `MAX_RETAINED_RUNS = 200` quietly stops bounding memory.
4. `error_mode` (`skip`/`retry`) is **ignored** for `fan_out` steps — any branch failure kills the run.
5. Without a `collect`, `{{input}}` after a fan-out group is only the **last** branch's output.
6. `collect` never resolves an agent: a `collect` step with a nonexistent `agent_name` succeeds.
   A workflow whose only step is `collect` returns `""`.
7. `GET /api/workflows/{id}/runs` ignores `{id}` and returns every run in the engine.
8. `POST /api/workflows/{id}/run` blocks the HTTP connection for the whole run (≤ 3600 s) and hides the
   real error behind a generic `"Workflow execution failed"`; the cause is only in the daemon log.
9. API body uses `prompt` / `agent_name`; the on-disk file uses `prompt_template` / `agent: {name}` and
   requires `mode` — a hand-written file missing `mode` is silently skipped at boot.
10. `until: ""` (the default when you set `mode: "loop"` and forget `until`) matches immediately →
    exactly one iteration. `condition: ""` matches everything.
11. `{{var}}` expansion is applied to *substituted* text and iterates a `HashMap` in nondeterministic
    order; a value containing `{{...}}` can be re-expanded unpredictably.
12. Retries have no backoff — three retries against a rate-limited provider fire back to back.

**Triggers**
13. `EventPayload::Custom` renders as `"Custom event (N bytes)"`. `/hooks/wake`, `event_publish` and
    cron `system_event` all produce it, so `content_match` on the text **never** matches and the agent
    never sees the payload — only `"all"` fires.
14. Triggers are memory-only. Every restart deletes all manually created triggers. Only
    `[schedule] proactive` conditions are regenerated.
15. `{"lifecycle":{}}` / `{"agent_terminated":{}}` / `{"all":{}}` are 400s — and the response body is
    just `{"error":"Invalid trigger pattern"}`; the useful serde detail (`invalid type: map,
    expected unit`) only reaches the daemon log (`routes.rs:1285`). Use bare strings for **unit**
    variants; struct variants like `{"content_match":{"substring":"x"}}` are maps and are correct
    (201). `docs/cli-reference.md:536` and the CLI help (`main.rs:592`) are both wrong.
16. `agent_spawned` and `lifecycle` triggers fired by `spawn_agent` burn `fire_count` and can
    self-disable via `max_fires` **without ever messaging the agent** (`kernel.rs:1789-1790`).
17. The heartbeat's `HealthCheckFailed` events go straight to the event bus, bypassing trigger
    evaluation — the documented "monitor agent health" trigger recipe cannot fire.
18. `max_fires` is enforced lazily: a maxed trigger still shows `enabled: true` until the next event.
    Re-enabling does not reset `fire_count`, so it immediately disables again.
19. Trigger dispatch is unbounded `tokio::spawn` with no dedup — `"all"` + a self-publishing agent is a
    loop, and N agents with `"all"` means N concurrent turns per event.
20. `name_pattern` / `key_pattern` are substring tests, not globs. Only the exact string `"*"` is
    "match anything".

**Schedules / cron**
21. `[schedule] periodic = { cron = "..." }` only understands `every <N>s|m|h|d`. Everything else,
    including valid 5-field cron, becomes **300 seconds** with a single `WARN Unparseable cron
    expression, defaulting to 300s`.
22. Background loops are `sleep(interval)` from process start — no wall-clock alignment, no catch-up.
23. `validate_cron_expr` rejects letters, so `MON-FRI`, `@daily`, `@hourly` cannot be used in cron jobs;
    6-field expressions are rejected at creation but would work at runtime.
24. `daily at 14:30` silently drops the minutes → `0 14 * * *`; `daily at 9:30am` errors.
25. A cron `agent_turn` whose channel delivery fails is recorded as a **job failure** even though the
    LLM turn succeeded; 5 consecutive failures auto-disable the job (`MAX_CONSECUTIVE_ERRORS = 5`).
26. `record_success`/`record_failure` don't persist — `last_run`/`last_status` can be lost if the
    daemon dies before the ~5-minute flush.
27. Enable **and** disable are both `PUT /api/cron/jobs/{id}/enable` with `{"enabled": bool}`.
    `POST .../enable` → 405, `POST .../disable` → 404. `openfang cron enable|disable` uses POST and
    reports `✔ ... enabled.` anyway.
28. `openfang cron create <name> ...` fails because the CLI sends the agent *name* where a UUID is
    required; with a UUID it succeeds but prints `✘ Failed: ?`. `openfang cron list` always dumps raw
    JSON. Use `curl` for cron.
29. `schedule_list` shows only the caller's jobs, so an agent cannot manage jobs it created for another
    agent — but `schedule_delete` / `cron_cancel` have **no ownership check** and will delete any job.
30. `model_override` on a cron `agent_turn` is stored and never used.
31. **`GET /api/cron/*` is unauthenticated (`middleware.rs:136`) and returns the whole `JobMeta`,
    `delivery_targets` included.** Reproduced on this box before the job was deleted (there are now
    zero cron jobs and `cron_jobs.json` is `[]`): a job's webhook target contained
    `{"type":"webhook","url":"http://127.0.0.1:4200/api/agents/…/session/reset",
    "auth_header":"Bearer <API_KEY>"}` — a live credential served to anyone who can reach the port.
    Job prompts, agent IDs and `local_file` paths are equally public. Never put a secret in
    `delivery_targets`.
32. `GET /api/schedules/{id}/delivery-log` is a stub that always returns `"entries": []`.
33. `ToolProfile::Automation` contains **no** scheduling tools.
34. `MAX_JOBS_PER_AGENT = 50`, global `max_cron_jobs = 500`; cron job names accept only
    `[A-Za-z0-9 _-]` and `/api/cron/jobs` (unlike the tool and `/api/schedules`) will not sanitise for you.

**Heartbeat / hooks**
35. Any `[autonomous]` table pins the agent to a 60-second unresponsive threshold and makes
    `[heartbeat] default_timeout_secs` a no-op for it (#1252, §4.3).
36. `[heartbeat]` is absent from `build_reload_plan` — hot-reload neither applies it nor warns; a full
    process restart is required.
37. The crash/recover cycle never reaches `Terminated` because `set_state` refreshes `last_active`; it
    oscillates every ~120 s indefinitely.
38. `WARN heartbeat: Agent is unresponsive` is emitted for reactive agents too, before the exemption is
    applied one layer up. The log line alone does not mean the agent was touched.
39. `quiet_hours` is `"HH:MM-HH:MM"` **UTC**, not a cron expression as `docs/configuration.md` claims.
40. `/hooks/*` needs the API key in `?token=` **and** the webhook token in `Authorization: Bearer` —
    `X-API-Key` is ignored whenever a Bearer header is present.
41. `OPENFANG_WEBHOOK_TOKEN` shorter than 32 characters makes every hook call 401 with no hint.
42. `WakeMode::next_heartbeat` is accepted, echoed, and ignored — the event publishes immediately.
43. `/hooks/agent` validates `timeout_secs` but never applies it, and silently ignores `deliver`,
    `channel` and `model`. With no `agent` it picks `registry.list().first()`.
44. Wake text may not contain tabs or any control character other than `\n`.

**Cross-cutting**
45. The CLI does not send `api_key` from `config.toml` on this build. Every write command 401s;
    `openfang trigger list` swallows the 401 and prints `No triggers registered.` Export
    `OPENFANG_API_KEY=<API_KEY>` for all CLI use against a keyed daemon.
46. `grep '^api_key' config.toml | cut -d'"' -f2` returns **two** lines — `api_key` and
    `api_key_env` both match the prefix — producing a broken header and an empty-bodied `400`.
    Always `grep -m1 '^api_key = '`. The **space before `=` is what disambiguates** — `api_key_env`
    can never match `^api_key = ` at any position, verified against a deliberately reordered
    `config.toml`. `-m1` **alone** is not enough: without the ` = ` it only works because `api_key`
    happens to be line 1 of this file; put `[default_model]` above it and `-m1` returns
    `api_key_env`'s value instead. This is the canonical explanation for the whole package — the
    other files use the same recipe and point here.
47. Only `GET /api/workflows` (bare) and `GET /api/cron/*` are public among the automation endpoints;
    `GET /api/workflows/{id}` and all trigger endpoints require the key.

---

# 6. Quick reference

## File / path map
| Path | What |
|---|---|
| `crates/openfang-kernel/src/workflow.rs` | workflow engine, step modes, error modes |
| `crates/openfang-kernel/src/triggers.rs` | trigger engine, patterns, `describe_event` |
| `crates/openfang-kernel/src/cron.rs` | `CronScheduler`, persistence, `compute_next_run_after` |
| `crates/openfang-kernel/src/cron_delivery.rs` | multi-destination fan-out engine |
| `crates/openfang-kernel/src/background.rs` | agent `[schedule]` loops, `parse_condition`, `parse_cron_to_secs` |
| `crates/openfang-kernel/src/heartbeat.rs` | `check_agents`, `RecoveryTracker`, `is_quiet_hours` |
| `crates/openfang-kernel/src/scheduler.rs` | per-agent hourly token quota |
| `crates/openfang-kernel/src/kernel.rs` | auto-spawn 1465, publish_event 4212, run_workflow 4280, load_workflows 4346, bg agents 4390, cron tick 4657, heartbeat loop 4832, migration 5052, cron_run_job 6573, delivery 6991, cron_create 7375 |
| `crates/openfang-runtime/src/tool_runner.rs` | tool dispatch 318, tool defs 824, NL cron 2147, schedule tools 2322 |
| `crates/openfang-types/src/scheduler.rs` | `CronJob`/`CronSchedule`/`CronAction`/`CronDelivery*`, validation |
| `crates/openfang-types/src/webhook.rs` | `WakePayload`, `AgentHookPayload` |
| `crates/openfang-api/src/routes.rs` | workflows 903, triggers 1252, schedules 9123, cron 11307, hooks 11507 |
| `crates/openfang-api/src/middleware.rs` | public-endpoint allowlist 97-140, auth 170-215 |
| `crates/openfang-cli/src/main.rs` | subcommands 546/673, `daemon_client` 1193, workflows 3205, cron 6034 |
| `$OF/cron_jobs.json` | cron jobs (array of `JobMeta`) |
| `$OF/workflows/<uuid>.json` | one file per workflow definition |
| `$OF/agents/<name>/agent.toml` | auto-spawned agent manifests — the #1206 blast zone |
| `$OF/config.toml` | `api_key`, `[heartbeat]`, `[webhook_triggers]`, `[budget]`, `workflows_dir`, `max_cron_jobs` |
| `$OF/secrets.env` | env vars loaded at startup (`OPENFANG_WEBHOOK_TOKEN`, provider keys) |
| *(none)* | triggers — no persistence file exists |

## Log lines worth grepping
```bash
L() { docker logs --since "${1:-1h}" openfang-openfang-1 2>&1 | sed 's/\x1b\[[0-9;]*m//g'; }

L | grep -E "Auto-spawned|Starting (continuous|periodic) background loop|Unparseable cron"  # #1206
L | grep -E "Agent is unresponsive|marked as Crashed|Auto-recovering|recovered successfully" # #1252
L | grep -E "Auto-loaded workflow|Invalid workflow JSON"                                     # #1192
L | grep -E "Cron: firing scheduled job|Cron job completed|Cron job failed|Auto-disabling cron job"
L | grep -E "Trigger registered|Trigger fired|Trigger dispatch failed|Invalid trigger pattern"
L | grep -E "Workflow registered|Starting workflow execution|Workflow completed|Workflow run failed"
```

## Env vars
| Var | Effect |
|---|---|
| `OPENFANG_HOME` | data dir; `/data` in the image. Used by both daemon and CLI. |
| `OPENFANG_API_KEY` | **required for CLI writes on v0.6.9** (config.toml is not picked up). |
| `OPENFANG_WEBHOOK_TOKEN` | value for `[webhook_triggers] token_env`; must be ≥ 32 chars. |
| `OPENFANG_LISTEN` | overrides `api_listen`. |
| `OPENFANG_ALLOW_NO_AUTH=1` | allows non-loopback requests with no `api_key` (do not use). |

## Config keys relevant to automation
```toml
api_key = "<API_KEY>"
workflows_dir = "/data/workflows"        # optional; default $OF/workflows (undocumented)
max_cron_jobs = 500                      # global cron cap; hot-reloadable

[heartbeat]
default_timeout_secs = 180               # ignored for any agent with [autonomous]; restart required

[webhook_triggers]
enabled = false
token_env = "OPENFANG_WEBHOOK_TOKEN"
max_payload_bytes = 65536
rate_limit_per_minute = 30

[budget]
max_hourly_usd = 0.0
max_daily_usd = 0.0
max_monthly_usd = 0.0
alert_threshold = 0.8
default_max_llm_tokens_per_hour = 0      # >0 OVERRIDES every agent's own limit
```

## Agent manifest keys relevant to automation
```toml
schedule = "reactive"                                  # safe default (or omit entirely)
# [schedule] continuous = { check_interval_secs = 120 }
# [schedule] periodic   = { cron = "every 6h" }        # ONLY "every N s|m|h|d"
# [schedule] proactive  = { conditions = ["event:system", "memory:deploy."] }

[autonomous]                     # presence alone forces a 60 s heartbeat timeout
quiet_hours = "22:00-06:00"      # UTC "HH:MM-HH:MM", NOT cron
max_iterations = 50
max_restarts = 10
heartbeat_interval_secs = 1800   # effective unresponsive threshold = 2x this
heartbeat_channel = "telegram"

[resources]
max_llm_tokens_per_hour = 50000  # rolling 1h, in-memory, resets on restart
```

## Choosing a mechanism
- **"Run agents A→B→C over one input, once, on demand."** → Workflow; call `POST /run`.
- **"Every weekday at 07:00, have the ops agent post a digest to Slack."** → Cron job,
  `{"kind":"cron","expr":"0 7 * * 1-5","tz":"Europe/London"}`, `agent_turn` action,
  `delivery_targets: [{"type":"channel",...}]`. **Not** `[schedule] periodic`.
- **"Every night, run my 5-step pipeline."** → Cron job with a `workflow_run` action referencing the
  workflow by name.
- **"When CI finishes, wake an agent with the build log."** → `POST /hooks/agent` (carries your text).
  **Not** `/hooks/wake` + `content_match`, which loses the payload.
- **"React to internal kernel events (spawn/terminate/memory)."** → Trigger, and re-create it after
  every restart, or express it as `[schedule] proactive` so it is regenerated at boot.
- **"Poll something continuously."** → `[schedule] continuous` — and budget it: 3600/interval turns per
  hour at ~8 k input tokens each.
