# OpenFang Skills & ClawHub — Reference (verified against v0.6.9 source)

Scope: this covers the `openfang-skills` crate (`/opt/openfang/crates/openfang-skills/`),
its integration points in `openfang-kernel`, `openfang-api`, and `openfang-cli`, and the
live instance in container `openfang-openfang-1`. `docs/skill-development.md` is **stale
in several places** — every disagreement below is called out explicitly, with the code
trusted over the doc.

Container facts used throughout: `OPENFANG_HOME` = `/data` inside the container, which is
`/var/lib/docker/volumes/openfang_openfang-data/_data/` on the host. Global skills dir =
`/data/skills/` (currently empty — `ls` returns "No such file or directory", it's created
lazily on first install). `docker exec openfang-openfang-1 openfang skill list` → "No
skills installed" (correct: 0 *user* skills; the 61 bundled ones are compiled in and
don't show there — see Gotchas).

---

## Table of contents

- [1. The manifest format — `skill.toml`](#1-the-manifest-format--skilltoml)
- [2. SKILL.md format — what frontmatter is ACTUALLY parsed](#2-skillmd-format--what-frontmatter-is-actually-parsed)
- [3. The 61 bundled skills (doc says 60 — code says 61, and enforces it)](#3-the-61-bundled-skills-doc-says-60--code-says-61-and-enforces-it)
- [4. Scoping — global vs. workspace vs. agent allowlist](#4-scoping--global-vs-workspace-vs-agent-allowlist)
- [5. Config injection (`config_injection.rs`)](#5-config-injection-config_injectionrs)
- [6. The `skill_*` built-in tools (fix for #1038 — read this before touching skill files)](#6-the-skill_-built-in-tools-fix-for-1038--read-this-before-touching-skill-files)
- [7. Known GitHub issues — verified against v0.6.9 source](#7-known-github-issues--verified-against-v069-source)
- [8. `openfang skill install <source>` — what it ACTUALLY hits (this is the big trap)](#8-openfang-skill-install-source--what-it-actually-hits-this-is-the-big-trap)
- [9. Authoring a custom skill end-to-end (worked example)](#9-authoring-a-custom-skill-end-to-end-worked-example)
- [10. CLI command reference (verified against `main.rs` `SkillCommands`)](#10-cli-command-reference-verified-against-mainrs-skillcommands)
- [Gotchas (all verified against source/live instance, not just docs)](#gotchas-all-verified-against-sourcelive-instance-not-just-docs)

## 1. The manifest format — `skill.toml`

Source of truth: `crates/openfang-skills/src/lib.rs`.

```toml
[skill]
name = "web-summarizer"        # required, unique, used as the install dir name
version = "0.1.0"               # optional, default "0.1.0"
description = "..."             # optional, shown to the LLM
author = "openfang-community"   # optional
license = "MIT"                 # optional
tags = ["web", "summarizer"]    # optional, FangHub/ClawHub discovery

[runtime]
type = "python"                 # python | wasm | node | shell | builtin | promptonly (see below)
entry = "src/main.py"            # relative path to entry point

[[tools.provided]]
name = "summarize_url"
description = "Fetch a URL and return a concise bullet-point summary"
input_schema = { type = "object", properties = { url = { type = "string" } }, required = ["url"] }

[requirements]
tools = ["web_fetch"]            # built-in tools this skill needs the host to expose
capabilities = ["NetConnect(*)"] # capability strings the agent must be granted

[config.github_token]            # optional — see §5 config injection
description = "GitHub personal access token"
env = "GITHUB_TOKEN"
required = true
```

### `SkillRuntime` — the REAL enum (lib.rs:52-69)

The doc table lists only `python | wasm | node | prompt_only | builtin`. The actual Rust
enum has **six** variants:

```rust
pub enum SkillRuntime {
    Python,
    Wasm,
    Node,
    Shell,       // NOT in the doc at all — Bash/sh script over stdin/stdout
    Builtin,
    #[default]
    PromptOnly,  // default when [runtime] is omitted entirely
}
```

- `#[serde(rename_all = "lowercase")]` — the wire/TOML string for `PromptOnly` is
  `"promptonly"` (no underscore), not `"prompt_only"`. `serde_json::from_str::<SkillRuntime>("\"promptonly\"")` is asserted directly in `lib.rs`'s own test suite. SKILL.md
  frontmatter never actually sets this field (see §2), so in practice you only hit this
  spelling when hand-writing a `skill.toml`.
- `Shell` is implemented in full in `loader.rs` (`execute_shell`, spawns `bash -s <script>`
  or falls back to `sh -s`, same `env_clear()` sandboxing as Python/Node) but is completely
  absent from `docs/skill-development.md`.
- `Wasm` is declared but **not implemented**: `loader.rs::execute_skill_tool` returns
  `SkillError::RuntimeNotAvailable("WASM skill runtime not yet implemented")` for it. The
  doc's whole "Building a WASM Skill" section (cargo build --target wasm32-wasi, sandbox
  fuel/memory limits) describes a runtime that does not execute anything yet in v0.6.9.

### Execution protocol (Python / Node / Shell — identical)

Kernel spawns the interpreter as a subprocess with **`env_clear()`** — the skill process
inherits nothing from the host environment except `PATH` and `HOME` (plus `SYSTEMROOT`/
`TEMP` on Windows). This is deliberate: "Skills are third-party code — they must not
inherit API keys, tokens, or credentials from the host environment" (`loader.rs` comment).
Consequence: a skill script cannot read `HYPERFUSION_API_KEY` or any other secret env var
unless it is explicitly resolved through the `config:` mechanism (§5) and passed via the
JSON stdin payload — not via the process environment.

Stdin payload sent to the script:
```json
{"tool": "summarize_url", "input": {"url": "https://example.com"}}
```
Expected stdout: `{"result": "..."}`  or  `{"error": "..."}`. If stdout isn't valid JSON,
the raw trimmed text is wrapped as `{"result": <text>}` — so a script that just
`print()`s a string still works, but never produces `is_error: true` for a malformed
result (only a non-zero exit status does — checked via `output.status.success()`, stderr
becomes `{"error": <stderr>}`).

Python resolution: tries `python3` then `python` via `--version` probe (`find_python()`).
Node: tries `node` (`find_node()`). Shell: tries `bash` then `sh`.
**None of curl, ffmpeg, git, yt-dlp, whisper exist inside `openfang-openfang-1`** — a
Python skill that shells out to any of those will fail; only `python3`, `node`, `npm`,
`pip3` are present in this container.

---

## 2. SKILL.md format — what frontmatter is ACTUALLY parsed

`docs/skill-development.md`'s frontmatter example shows `name` + `description` only, and
that is correct — but the task-level assumption that frontmatter also carries `version`
and `runtime` is **wrong**. The real parser is `SkillMdFrontmatter`
(`openclaw_compat.rs:24-39`):

```rust
pub struct SkillMdFrontmatter {
    pub name: String,
    pub description: String,
    pub metadata: SkillMdMetadata,   // -> metadata.openclaw: { emoji, requires{bins,env}, commands[] }
    pub config: HashMap<String, SkillConfigVar>,   // see §5
}
```

There is **no `version` field and no `runtime`/`type` field** in SKILL.md frontmatter.
Every SKILL.md-sourced skill:
- gets `version = "0.1.0"` hardcoded (`convert_skillmd_str`/`convert_skillmd`),
- is **always** `SkillRuntime::PromptOnly` — even when `metadata.openclaw.commands` lists
  named commands, the runtime is still forced to `PromptOnly` (`openclaw_compat.rs:234-241`,
  comment: *"Has commands but no executable entry point — still prompt-only (the commands
  just indicate which built-in tools to use)"*).

So a SKILL.md file can never cause code execution by itself — it only ever produces a
Markdown block injected into the system prompt, plus (if `metadata.openclaw.commands` is
present) some `SkillToolDef` entries whose "execution" is literally the no-op
`PromptOnly` branch in `loader.rs` (returns a canned "use built-in tools" note — it does
NOT run anything). This is the exact root cause GitHub #1001/#1028 report (see §7).

Minimal valid SKILL.md:
```markdown
---
name: my-skill
description: One-line description shown to the LLM
---
# My Skill

Body text — this becomes `prompt_context`, injected verbatim into the agent's
system prompt when the skill is loaded/allowlisted.
```

Optional `metadata.openclaw` block (OpenClaw compatibility — command names get mapped to
OpenFang tool names via `openfang_types::tool_compat::map_tool_name`, e.g. `Bash` →
`shell_exec`, `Read` → `file_read`; unknown names become `snake_case` custom tool defs):
```yaml
metadata:
  openclaw:
    emoji: "🐙"
    requires:
      bins: [git]
      env: [GITHUB_TOKEN]
    commands:
      - name: create_pr
        description: Create a pull request
```

---

## 3. The 61 bundled skills (doc says 60 — code says 61, and enforces it)

`crates/openfang-skills/src/bundled.rs` embeds every `bundled/<name>/SKILL.md` at compile
time via `include_str!()`. Its own test asserts the count:
```rust
assert_eq!(skills.len(), 61, "Expected 61 bundled skills");
```
`find /opt/openfang/crates/openfang-skills/bundled -name SKILL.md | wc -l` → **61**, confirmed.
The doc's table of "60 bundled skills" is missing **`searxng`** (privacy-respecting
metasearch skill, tier 1) — everything else in the doc's category table matches.

All 61 are `prompt_only`, sourced with `SkillSource::Bundled`, author `"OpenFang"`,
license `"Apache-2.0"`, tags `["bundled", "prompt-only"]`. **None of the 61 declare any
`[[tools.provided]]` and none declare a `config:` block** (verified: `grep -l '^config:'
*/SKILL.md` across the bundled dir returns nothing). They are pure expert-knowledge text
injected into the prompt — they never call `metadata.openclaw.commands` either, so no
bundled skill produces a `SkillToolDef`.

Loading order at agent spawn (`kernel.rs:2150-2165`, comment verbatim): **bundled → global
(`~/.openfang/skills`) → workspace skills**, later layers override earlier ones by name
(`HashMap::insert` semantics — same key replaces value, doesn't duplicate). This is
covered by `registry.rs` tests `test_workspace_skill_overrides_global` (#808) and
`test_snapshot_global_plus_workspace_merge` (#851).

Also `load_bundled()` runs the prompt-injection scanner even on bundled content
("defense in depth") and will silently drop any bundled skill that trips a Critical
warning — there's a dedicated test (`test_bundled_skills_pass_security_scan`) asserting
none of the 61 currently do.

---

## 4. Scoping — global vs. workspace vs. agent allowlist

Three independent axes; do not confuse them:

### (a) Physical layer — where the skill.toml/SKILL.md lives
1. **Bundled** — compiled into the binary, always present unless blocked by the scanner.
2. **Global** — `~/.openfang/skills/<name>/` (= `/data/skills/<name>/` in this container).
   Loaded via `SkillRegistry::load_all()`. Visible to every agent unless the agent's
   `skills =` allowlist excludes it.
3. **Workspace** — `<agent-workspace-dir>/skills/<name>/`. Loaded per-agent, per-spawn, via
   `SkillRegistry::load_workspace_skills()`, called from `kernel.rs` right before tool-list
   and prompt building ("Build workspace-aware skill snapshot BEFORE tool list and prompt
   building"). A workspace skill with the same name as a global/bundled one **replaces**
   it for that agent only — the global registry is untouched (`snapshot()` is cloned first).
   An agent's workspace directory defaults to
   `self.config.effective_workspaces_dir().join(&manifest.name)` unless the manifest pins
   one explicitly (`manifest.workspace`).

   This is the closest thing to "per-agent custom skills": drop a `SKILL.md` or
   `skill.toml` into that agent's own workspace `skills/` subfolder and it's visible only
   to agents sharing that workspace.

### (b) Logical layer — the agent manifest's `skills` allowlist
`AgentManifest.skills: Vec<String>` (`openfang-types/src/agent.rs:458`) — doc comment:
*"Installed skill references (empty = all skills available)"*. Confirmed in
`kernel.rs::available_tools_with_registry` (~line 6166-6238):
```rust
let skill_tools = if skill_allowlist.is_empty() {
    snapshot.all_tool_definitions()
} else {
    snapshot.tool_definitions_for_skills(&skill_allowlist)
};
```
and the same allowlist gates `build_skill_summary_from` (the human-readable "Available
Skills" block appended to the system prompt) and `collect_prompt_context_from`. So:
- Empty `skills = []` → every loaded skill (bundled+global+workspace) is visible to this
  agent, tools and prompt context both.
- Non-empty `skills = ["github", "postgres-expert"]` → only those names (by exact
  `manifest.skill.name` match) contribute tools/prompt text to this agent, even though the
  full registry still contains everything else.

Agent manifest TOML:
```toml
name = "my-assistant"
module = "builtin:chat"
skills = ["web-summarizer", "postgres-expert"]   # empty = unrestricted
```

### (c) `openfang.toml` / `~/.openfang/config.toml` per-skill config
`[skills.<skill-name>]` sections feed §5's config resolution — this is NOT an allowlist,
it only supplies values for a skill's declared `config:` vars.

---

## 5. Config injection (`config_injection.rs`)

A skill's SKILL.md/`skill.toml` can declare a `config:` map of variables it needs at
runtime (tokens, endpoints, default branches). Resolution order, per variable, done in
`resolve_skill_config()`:

1. `~/.openfang/config.toml` → `[skills.<skill-name>]` section (highest priority)
2. `std::env::var(var.env)` if the var declares an `env` name (empty value doesn't count)
3. `var.default`
4. If none resolve **and** `required = true` → hard error
   (`SkillConfigError::MissingRequired`), and the whole skill is refused/skipped at load
   time (`registry.rs::apply_skill_config` — for bundled skills this is a `continue`
   (skip), for user skill.toml it's a hard `Result` error that propagates up from
   `load_skill`).

Resolved values are rendered into a Markdown block and appended to `prompt_context`:
```
[Skill config from ~/.openfang/config.toml:
  default_branch: main
  github_token: ghp_***redacted***
]
```
Secret redaction (`is_secret_name`): any var name ending in `_token`, `_key`, `_secret`,
or exactly/ending `password` (case-insensitive) is shown as `<first 4 chars>***redacted***`
in the **rendered** block — but the underlying resolved `HashMap` still holds the full
value for whatever executes the skill. Non-secret names pass through unredacted.

Config file syntax (`config.rs:1293-1298` doc comment, verified against `SkillsConfig`
field type `HashMap<String, HashMap<String, String>>`):
```toml
# ~/.openfang/config.toml
[skills.github-repo-helper]
github_token = "ghp_..."
default_branch = "develop"
```

---

## 6. The `skill_*` built-in tools (fix for #1038 — read this before touching skill files)

Three built-in tools exist specifically so agents never need `file_read`/`shell_exec` on
skill directories (`openfang-runtime/src/tool_runner.rs:3591-3738`):

- **`skill_list`** — no args. Returns `{count, skills:[{name, version, description,
  runtime, enabled, tools, has_prompt_context}]}` for every skill in the registry
  (respecting the agent's allowlist upstream, but the tool itself just dumps
  `registry.list()`).
- **`skill_describe {name}`** — returns the skill's full `prompt_context` body (the
  SKILL.md markdown) plus its declared tools. Error message on typo: *"Skill 'x' not
  found. Use skill_list to see installed skills."*
- **`skill_execute {skill, tool?, input?}`** — if `tool` is omitted and the skill is
  prompt-only, returns `{"mode": "prompt_context", "body": ..., "note": "...Follow the
  instructions in 'body' using your built-in tools."}` — i.e. for the 61 bundled skills
  (and any other prompt-only skill) this is functionally identical to `skill_describe`.
  If `tool` is given, it dispatches to `openfang_skills::loader::execute_skill_tool` —
  only meaningful for Python/Node/Shell skills that actually declare `[[tools.provided]]`.

`kernel.rs::build_skill_summary_from` appends this exact instruction to every agent's
system prompt when skills are present:
> "Use these skill tools when they match the user's request. To inspect a skill's full
> instructions, call skill_describe with the skill name — do NOT use file_read or
> shell_exec on the skills directory, those paths are outside the agent workspace and
> will fail."

**Why**: global (`~/.openfang/skills/`) and bundled skill "paths" are outside the agent's
workspace sandbox — `file_read`/`shell_exec` calls against them fail. Before this fix
(landed under commit `3617742...`, referenced in the #1038 close comment), agents would
burn tool calls trying `ls ~/.openfang/skills/daily-journal/` and failing. If you are
writing a NEW skill's `SKILL.md` body, you can reference this: instruct the agent to call
`skill_describe` for detail rather than reading files directly.

---

## 7. Known GitHub issues — verified against v0.6.9 source

### #1038 — "Global Skills don't work cleanly with spawned agents" (OPEN, fix landed)
Reported: skills in `~/.openfang/skills/` were technically listed but unusable — agent
tried `file_read`/`shell_exec` on the skill directory and failed (outside workspace
sandbox). **Fix already in this checkout**: the `skill_list`/`skill_describe`/
`skill_execute` tools above, plus the prompt instruction in `build_skill_summary_from`.
Confirmed present at `tool_runner.rs:482-484` (dispatch), `:1321-1351` (tool defs),
`:3591-3738` (handlers); `kernel.rs:6427` has the `// Issue #1038` comment inline.
The GitHub issue is still open (as of this write) even though the fix is merged — treat
the *code* as authoritative, not the issue's `state: OPEN` label.

### #1028 — "The problem with OpenFang's skills" (a skill's declared HTTP callback never fires)
Root cause per the maintainer's own triage comment (quoted from `gh issue view 1028
--comments`): if the skill was installed as SKILL.md-only (no explicit `skill.toml`
`[runtime]` block), it **always** loads as `SkillRuntime::PromptOnly` — confirmed above in
§2 (`convert_skillmd`/`convert_skillmd_str` hardcode `PromptOnly`). A prompt-only skill's
"callback to my system's URL" is just text in the system prompt; nothing in the runtime
ever executes an HTTP request on the skill's behalf — the LLM would have to decide, on its
own, to call `web_fetch`/`shell_exec`. Maintainer's stated position: *"this is
working-as-designed for safety reasons... the path forward is the explicit skill.toml"* —
i.e. ship a `[runtime] type = "python"` + entry script if you need code to actually run.

### #1001 — "Is the skill installed from clawhub all prompt_only type by default?"
Same root cause as #1028, asked more directly. Confirmed: yes, ClawHub/OpenClaw
SKILL.md-format skills are unconditionally converted to `PromptOnly`
(`clawhub.rs::install_with_options`, branch `if is_skillmd ...`, calls
`openclaw_compat::convert_skillmd` which hardcodes the runtime). There is no way to get
executable behavior out of a bare SKILL.md through the installer — you must author (or
hand-add) a `skill.toml` with `[runtime] type = "python"/"node"/"shell"` and a real entry
script alongside it.

### #1270 — "ClawHub installs fail for ambiguous slugs because ownerHandle is not forwarded"
Repro: dashboard → Skills → ClawHub → install a common slug (`weather`) shared by multiple
publishers. Upstream ClawHub API returns `409 Conflict` with a message telling the caller
to retry with `&ownerHandle=<owner>`. `ClawHubInstallRequest` (`openfang-api/src/types.rs:
115-118`) has **only one field, `slug`** — there is no `owner_handle`/`ownerHandle` field
to forward, so this 409 cannot currently be resolved through the API or CLI. The 409 gets
mapped in `clawhub_install()`'s error branch to whatever `SkillError` variant `install()`
raised; a plain `Network` error maps to **HTTP 502**, which the dashboard then displays
misleadingly as "daemon unavailable" even though the daemon is healthy — confirmed by
reading `routes.rs:4169-4234`'s status-code match (`SecurityBlocked`→403, rate-limit→429,
`Network`→502, else 500). Workaround until a fix lands: there isn't one through the
UI/API; you'd need to hit ClawHub's own `/api/v1/download?slug=<slug>&ownerHandle=<owner>`
directly and drop the result into `~/.openfang/skills/<name>/` by hand (matching what
`install_with_options` would have produced: `skill.toml` + `prompt_context.md` written by
`openclaw_compat::write_openfang_manifest`/`write_prompt_context`).

### #1001/#1170 combined finding — `--require-signed` exists in the library, nowhere in the UI
`crates/openfang-skills/src/installer.rs` fully implements Ed25519 manifest-signing
enforcement (`InstallOptions{require_signed, allowed_signer_keys}`,
`enforce_require_signed()`, looks for `signature.json` / `skill.toml.sig.json` /
`SKILL.md.sig.json`, binds the envelope to on-disk bytes with CRLF/BOM normalization,
rejects symlinks). It is unit-tested (8 tests) and wired into both
`ClawHubClient::install_with_options` and `MarketplaceClient::install_with_options`. But:
- **`openfang skill install <source>` (CLI) never calls `install_with_options` with
  anything other than `InstallOptions::default()`** — there is no `--require-signed` or
  `--pubkey` flag in `SkillCommands::Install` (`crates/openfang-cli/src/main.rs:346-351`);
  confirmed by `grep -n "require.signed\|require_signed\|pubkey"
  crates/openfang-cli/src/main.rs` → zero matches.
- **`POST /api/skills/install`** (FangHub route) *does* expose `require_signed` /
  `allowed_signer_keys` in `SkillInstallRequest` and passes them through
  (`routes.rs::install_skill`).
- **`POST /api/clawhub/install`** (the actual ClawHub route used by the dashboard) does
  **not** — `ClawHubInstallRequest` has only `slug` (see #1270 above), and the handler
  calls the unadorned `client.install()` (defaults), never `install_with_options`. So even
  though `require_signed` enforcement is fully implemented and tested for ClawHub in the
  library, there is currently **no caller anywhere** (CLI or API) that can turn it on for
  a ClawHub install. It's only exercised by `installer.rs`'s own unit tests today.
  Maintainer's own comment on #1170 confirms CLI wiring was deliberately deferred:
  *"CLI wiring... is intentionally not included in this PR... Leaving open until CLI
  wiring lands."*

---

## 8. `openfang skill install <source>` — what it ACTUALLY hits (this is the big trap)

The CLI subcommand (`crates/openfang-cli/src/main.rs::cmd_skill_install`, ~line 3564)
branches on the `source` string:

1. **Existing local directory** → copies it into `~/.openfang/skills/<name>/`, converting
   OpenClaw `package.json`+`index.js` format if no `skill.toml` is found.
2. **`https://…` / `http://…` / `git@…`** → `git clone --depth 1` into a temp dir, then
   same local-dir logic. Requires `git` on the host running the CLI (not present inside
   `openfang-openfang-1` — do this from the host, not `docker exec`).
3. **Anything else** (a bare name) → falls to `else` branch: *"Installing {source} from
   FangHub..."* and calls **`openfang_skills::marketplace::MarketplaceClient`**.

**`openfang skill install <name>` NEVER talks to ClawHub.** ClawHub
(`openfang_skills::clawhub::ClawHubClient`, `https://clawhub.ai/api/v1`, "3,000+
community skills") is **only** reachable through the HTTP API routes
`GET /api/clawhub/{search,browse,skill/:slug,skill/:slug/code}` and
`POST /api/clawhub/install` — i.e. the web dashboard. `grep -n "ClawHubClient\|clawhub"
crates/openfang-cli/src/main.rs` returns nothing. There is no CLI subcommand for ClawHub
at all in v0.6.9.

And FangHub itself (what the CLI actually uses) is a **thin, incomplete stub**
(`marketplace.rs`): it resolves `https://api.github.com/repos/openfang-skills/<name>/
releases/latest`, downloads the release tarball, and then — quote the code's own comment —
> "For now, save the download URL in a metadata file. Full tarball extraction would
> require a tar/gz library"

It writes a `marketplace_meta.json` stub (`{name, version, source, installed_at}`) and
**does not extract the tarball at all**. So `openfang skill install <name>` for anything
that isn't a local path or git URL will, at best, produce a skill directory containing
only that metadata stub — no `skill.toml`, no entry script, nothing the registry can
actually load as a working skill (the registry's `load_all()` requires a `skill.toml` or
`SKILL.md` in the directory; a bare `marketplace_meta.json` satisfies neither, so
`load_all()` silently skips that directory). The doc's worked example
(`openfang skill install web-summarizer` producing a working 2-tool skill from "FangHub")
does not reflect what the code does today.

**Practical guidance**: to install a real skill from ClawHub against this instance, call
the API directly (with the bearer token from `config.toml`'s `api_key`):
```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)
curl -s -H "Authorization: Bearer $K" \
  "http://127.0.0.1:4200/api/clawhub/search?q=github&limit=5"
curl -s -X POST -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"slug": "github"}' \
  "http://127.0.0.1:4200/api/clawhub/install"
```
This runs the full pipeline documented in `clawhub_install`'s doc-comment: SHA256 of the
download, format detection (SKILL.md vs `package.json`), prompt-injection scan (blocks
`Critical` findings, purges the extracted dir and returns `403`), manifest security scan,
binary-dependency check (`which <bin>` for each `metadata.openclaw.requires.bins` entry —
just a `Warning`, not a hard block, if missing), staged-then-atomically-renamed write of
`skill.toml`+`prompt_context.md`, then `state.kernel.reload_skills()` to hot-reload without
restarting the daemon.

---

## 9. Authoring a custom skill end-to-end (worked example)

Target: a prompt-only "internal runbook" skill, global-scoped, for this container.

**Step 1** — write the file directly into the volume (no `openfang skill create`
required for prompt-only skills — that scaffold command only generates Python/generic
stubs, see below):
```bash
mkdir -p /var/lib/docker/volumes/openfang_openfang-data/_data/skills/incident-runbook
cat > /var/lib/docker/volumes/openfang_openfang-data/_data/skills/incident-runbook/SKILL.md <<'EOF'
---
name: incident-runbook
description: Internal incident-response steps for a private VPS
---
# Incident Runbook

When the user reports the openfang-openfang-1 container is unhealthy:
1. `docker ps` to confirm state.
2. `docker logs --tail 200 openfang-openfang-1` for the crash reason.
3. Never restart with `--force` without confirming with the user first.
EOF
```
No `openfang skill install` step is needed for a file already placed under
`OPENFANG_HOME/skills/<name>/` — the registry's `load_all()` auto-detects a bare
`SKILL.md` (no `skill.toml` present), converts it via `openclaw_compat::convert_skillmd`,
runs the injection scanner, and — if it passes — **writes `skill.toml` +
`prompt_context.md` back into that same directory** so subsequent loads skip
re-conversion. This happens automatically the next time the daemon loads skills (at boot,
or on `reload_skills()`/`POST /api/skills/reload`).

**Step 2** — reload without restarting the daemon:
```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)
curl -s -X POST -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/skills/reload
```
(`notify_daemon_skill_reload()` in the CLI does exactly this after a local-dir/git
install — worth doing manually here since our skill was hand-placed, not installed via
the CLI's local-dir branch.)

**Step 3** — verify: agents with an empty `skills` allowlist will now see it. Confirm via
the `skill_list` tool in a live agent turn, or:
```bash
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/skills | python3 -m json.tool
```
Note this endpoint (§Gotchas) shows only user-installed skills, not the 61 bundled ones —
"incident-runbook" will now appear here because it's a *global* (user) skill, not bundled.

**For an executable (Python) skill instead**, write `skill.toml` yourself (the `openfang
skill create` scaffold works but only from inside the CLI's own `~/.openfang/skills/`
path resolution, which uses `openfang_home()` — see §Gotchas for its node/wasm entry-path
bug):
```toml
[skill]
name = "port-check"
version = "0.1.0"
description = "Check whether a TCP port is open on localhost"

[runtime]
type = "python"
entry = "main.py"

[[tools.provided]]
name = "check_port"
description = "Check if a TCP port is open on 127.0.0.1"
input_schema = { type = "object", properties = { port = { type = "integer" } }, required = ["port"] }
```
```python
# main.py — read stdin JSON, write stdout JSON, per the protocol in §1.
import json, socket, sys

def main():
    payload = json.loads(sys.stdin.read())
    if payload["tool"] != "check_port":
        print(json.dumps({"error": f"unknown tool {payload['tool']}"})); return
    port = int(payload["input"]["port"])
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    open_ = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    print(json.dumps({"result": f"port {port} is {'open' if open_ else 'closed'}"}))

if __name__ == "__main__":
    main()
```
Place both files at `/var/lib/docker/volumes/openfang_openfang-data/_data/skills/port-check/
{skill.toml,main.py}`, then reload as in Step 2. Remember: this script runs with
`env_clear()` — it cannot read any host env var except `PATH`/`HOME`, and it cannot use
`curl` (absent in-container) — stick to Python stdlib (`socket`, `urllib.request`, `json`).

---

## 10. CLI command reference (verified against `main.rs` `SkillCommands`)

```bash
openfang skill install <source>   # local dir | git URL | bare name → FangHub stub (see §8)
openfang skill list               # user-installed only, NOT bundled (see Gotchas)
openfang skill remove <name>
openfang skill search <query>     # hits FangHub (GitHub code search under org "openfang-skills"), NOT ClawHub
openfang skill create             # interactive scaffold, python/node/wasm — see Gotchas for the entry-path bug
```
All operate on `openfang_home().join("skills")`, i.e. `OPENFANG_HOME/skills` —
`/data/skills` in this container. None of these subcommands touch ClawHub; ClawHub is
API/dashboard-only in v0.6.9 (§8).

The Python SDK skill example in `docs/skill-development.md` ("Using the OpenFang Python
SDK", `from openfang_sdk import SkillHandler`) **does not exist** —
`sdk/python/openfang_sdk.py` defines only an `Agent` class with `read_input()`/`respond()`/
`log()` helpers for writing whole custom *agents* (a different subsystem, `module =
"python:..."` in an agent manifest), not skills. `grep -n "SkillHandler" sdk/python/
openfang_sdk.py` returns nothing. Do not point users at this SDK for skill authoring.

---

## Gotchas (all verified against source/live instance, not just docs)

- **Doc says 60 bundled skills, code has and enforces 61** (`bundled.rs` test
  `assert_eq!(skills.len(), 61)`) — the doc's category table is missing `searxng`.
- **SKILL.md frontmatter has no `version` or `runtime`/`type` field.** Every SKILL.md
  (bundled or installed) is hardcoded to `version = "0.1.0"` and
  `SkillRuntime::PromptOnly` at parse time (`openclaw_compat.rs`), regardless of what
  `metadata.openclaw.commands` declares. To get real code execution you must hand-author
  a `skill.toml` with an explicit `[runtime]` block — this is the exact, maintainer-confirmed
  root cause of GitHub #1001 and #1028.
- **`openfang skill install <name>` (CLI) never contacts ClawHub.** It hits FangHub
  (`marketplace.rs`, GitHub org `openfang-skills`), which is a stub that downloads a
  release tarball and then does **not extract it** — it only writes a
  `marketplace_meta.json` pointer file. ClawHub is reachable only via
  `GET/POST /api/clawhub/*` (dashboard), never from the CLI.
- **`GET /api/skills` and `openfang skill list` both under-report**: both build a fresh
  `SkillRegistry` and call only `load_all()`, never `load_bundled()`. They show 0 skills
  on a stock instance even though 61 bundled skills are active for every agent (loaded via
  a *separate* registry instance inside `kernel.rs` at boot, `load_bundled()` at
  `kernel.rs:875`). Verified live: `docker exec openfang-openfang-1 openfang skill list` →
  "No skills installed"; `curl .../api/skills` → `{"skills":[],"total":0}` on this
  instance, despite 61 bundled skills being live in every agent's prompt/tool list. Use
  the `skill_list` tool from inside an agent turn (or `openfang doctor`) to see the true
  active roster.
- **`--require-signed` is fully implemented and unit-tested but unreachable from any
  user-facing surface for ClawHub installs.** The CLI has no flag for it at all
  (`SkillCommands::Install` takes only `source`); the ClawHub API route's request struct
  (`ClawHubInstallRequest`) has only a `slug` field, no signing options, and its handler
  calls `client.install()` (defaults) rather than `install_with_options`. Only the FangHub
  route (`POST /api/skills/install`, itself semi-broken per the point above) exposes
  `require_signed`/`allowed_signer_keys` end-to-end.
- **`openfang skill create`'s generated `skill.toml` hardcodes `entry = "src/main.py"`
  regardless of the runtime you chose.** If you pick `node` or `wasm` at the prompt, the
  scaffold writes the *content* to `src/index.js` but the manifest's `entry` field is a
  literal string `"src/main.py"` in the format string (`main.rs::cmd_skill_create`) — never
  substituted with the actual `entry_path` variable. The resulting skill is broken out of
  the box for anything but `python`: `execute_node` will look for `<skill_dir>/src/main.py`
  and fail with "Node.js script not found". Fix by hand-editing `entry` after scaffolding.
- **ClawHub ambiguous-slug installs 502 instead of giving an actionable error** (#1270):
  `ClawHubInstallRequest` has no `owner_handle` field to forward ClawHub's own suggested
  fix (`&ownerHandle=<owner>`), and a plain upstream `409` surfaces through
  `SkillError::Network` → mapped to HTTP `502` → the dashboard shows a misleading "daemon
  unavailable", even though `openfang` is healthy.
- **The prompt-injection scanner (`verify.rs::scan_prompt_content`) is a flat, lower-cased
  substring match against ~20 hardcoded English phrases** (`"ignore previous
  instructions"`, `"send to http"`, `"rm -rf"`, etc.) — it is trivially evaded by
  rephrasing, non-English text, splitting a phrase across Markdown formatting, or any
  wording not in the literal list. It is a real gate (blocks `Critical` findings at
  install and at every load, including for bundled skills — "defense in depth") but is not
  a semantic or LLM-based check; treat any pass as "not the exact 20 known bad phrases",
  not as "safe."
- **Skill subprocesses (Python/Node/Shell) run with `env_clear()`.** They inherit only
  `PATH`/`HOME` (`SYSTEMROOT`/`TEMP` added on Windows only). A skill cannot read
  `HYPERFUSION_API_KEY` or any other host secret from its process environment — only
  values resolved through the `config:` mechanism (§5) and passed via the stdin JSON
  payload are visible to it, and only if the skill's own code reads them out of `input`.
- **None of the 61 bundled skills declare any tools or `config:` vars.** They are 100%
  prompt-only text. If a user asks "why doesn't the `github` skill actually call `gh` for
  me automatically" — it doesn't; it's expert *guidance* text telling the agent to use its
  own `shell_exec`/`gh` access, not a tool binding.
- **WASM runtime is declared in the type system but not implemented.** Any skill.toml with
  `type = "wasm"` will load fine (parses, passes the scanner) but calling its tool returns
  `SkillError::RuntimeNotAvailable("WASM skill runtime not yet implemented")` at execution
  time — confirmed in `loader.rs::execute_skill_tool`.
- **`skill_execute` with no `tool` argument on a prompt-only skill is functionally the same
  as `skill_describe`** — it just returns the SKILL.md body wrapped as `mode:
  "prompt_context"`. Don't expect it to "run" a prompt-only skill; there is nothing to run.
