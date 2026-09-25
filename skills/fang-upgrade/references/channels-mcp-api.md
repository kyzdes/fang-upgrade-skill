# OpenFang v0.6.9 — Channels, MCP/A2A, and HTTP API Reference

Verified against source at `/opt/openfang` (tag `v0.6.9`, commit `acf2587`) and empirically probed
against the live container `openfang-openfang-1` (API on `http://127.0.0.1:4200`) on 2026-08-09.
Where the shipped docs (`docs/channel-adapters.md`, `docs/mcp-a2a.md`, `docs/api-reference.md`)
disagree with code/live behavior, that is called out explicitly — **trust the code/live results**.

---

## Table of contents

- [1. Channels](#1-channels)
- [2. MCP (Model Context Protocol)](#2-mcp-model-context-protocol)
- [3. A2A (Agent-to-Agent Protocol)](#3-a2a-agent-to-agent-protocol)
- [4. HTTP API — endpoint table and the real auth matrix](#4-http-api--endpoint-table-and-the-real-auth-matrix)
- [5. Gotchas (all verified against v0.6.9 code and/or the live instance)](#5-gotchas-all-verified-against-v069-code-andor-the-live-instance)
- [6. Source-file index (for follow-up digging)](#6-source-file-index-for-follow-up-digging)

## 1. Channels

### 1.1 Which of the "40 channels" actually exist — it is 43 modules / 42 listed

`docs/channel-adapters.md` claims 40 channel adapters. The real numbers, counted 2026-08-10:

- **43 adapter modules** under `crates/openfang-channels/src/` (49 `.rs` files minus the six
  non-adapters `bridge.rs`, `formatter.rs`, `lib.rs`, `router.rs`, `types.rs`, `redact.rs` — the
  last one is new in this fork, the shared credential-redaction helper behind FANG-39/43/44, see
  §1.8a). Adapter count is unchanged from stock — the fork added a helper module, not a channel.
- **42 entries returned by `GET /api/channels`** — the hardcoded `CHANNEL_REGISTRY` table at
  `crates/openfang-api/src/routes.rs:1742`, iterated by `list_channels` (`routes.rs:2701-2755`).
  Verified live (`tools/list`-style count, re-checked 2026-08-14: still 42, `mqtt` still absent).
- The one module with no catalog entry is **`mqtt.rs`**: it has a `ChannelType::Mqtt` variant and is
  wired in `channel_bridge.rs:52,816,1733`, but the dashboard catalog never lists it, so it is
  invisible to `/api/channels` and to the channel-config UI. Configure it by hand in `config.toml`.
- `dingtalk_stream` is listed as its own channel alongside `dingtalk`.

`ChannelType` (`crates/openfang-channels/src/types.rs:14-29`) has **12 named variants plus
`Custom(String)`**: ten external platforms — `Telegram`, `WhatsApp`, `Slack`, `Discord`, `Signal`,
`Matrix`, `Email`, `Teams`, `Mattermost`, **`Mqtt`** — and two internal ones, `WebChat` and `CLI`.
Every other adapter (Feishu, LINE, Viber, Mastodon, Bluesky, Reddit, LinkedIn, Twitch, IRC, Guilded,
Revolt, Keybase, Discourse, Gitter, Nextcloud, Threema, Nostr, Mumble, Pumble, Flock, Twist,
DingTalk, dingtalk_stream, Messenger, WeCom, ntfy, Gotify, generic Webhook, XMPP, Google Chat, Webex,
Rocket.Chat, Zulip) is `ChannelType::Custom("<name>".to_string())`. XMPP is explicitly called out in
the doc as a stub.

All adapters share, per `docs/channel-adapters.md:5` and confirmed by the `ChannelAdapter` trait
(`crates/openfang-channels/src/types.rs`):
- graceful shutdown via `watch::channel`
- exponential backoff on connection failure
- `Zeroizing<String>` secret wrapping (wiped from memory on `Drop`)
- automatic message splitting for platform length limits (`split_message()`)
- per-channel model/prompt overrides, DM/group policy, per-user rate limiting
- output formatting (`Markdown`, `TelegramHtml`, `SlackMrkdwn`, `PlainText`)

### 1.2 Config location and shape

All channel config lives in `~/.openfang/config.toml` (container: `/data/config.toml`), one
`[channels.<name>]` subsection per platform. Live instance currently has **no `[channels.*]`
sections configured** (checked `/var/lib/docker/volumes/openfang_openfang-data/_data/config.toml`).

Common fields on every channel config struct:
| Field | Meaning |
|---|---|
| `bot_token_env` / `token_env` / etc. | Name of the env var holding the secret — OpenFang reads the **env var**, not a literal token, at startup. |
| `default_agent` | Agent that receives messages when no `[[bindings]]` rule matches. |
| `allowed_users` | Optional allowlist of platform user IDs; empty = allow all. |
| `overrides` | `ChannelOverrides` block (see below). |

### 1.3 Telegram

```toml
[channels.telegram]
bot_token_env = "TELEGRAM_BOT_TOKEN"
default_agent = "assistant"
# allowed_users = ["123456789"]

[channels.telegram.overrides]
# output_format = "telegram_html"
# group_policy = "mention_only"
```
Upstream's instruction is `export TELEGRAM_BOT_TOKEN=… && openfang start`. That does not apply to
this deployment: there is no `openfang` binary on the host, the daemon is already PID 1 inside the
container (so `openfang start` would try to boot a second one), and a variable exported in a host
shell never reaches the container process. Use the same route as every other secret here:

```bash
D=/var/lib/docker/volumes/openfang_openfang-data/_data
printf 'TELEGRAM_BOT_TOKEN=%s\n' '123456789:ABCdefGHIjklMNOpqrsTUVwxyz' >> $D/secrets.env
chmod 600 $D/secrets.env
# add the [channels.telegram] block above to $D/config.toml, then:
docker restart openfang-openfang-1
```

`openfang channel setup telegram` still exists and is interactive, so it needs a TTY:
`docker exec -it -e OPENFANG_API_KEY="$K" openfang-openfang-1 openfang channel setup telegram`.
Long-polling via `getUpdates`, 30s long-poll timeout, backoff 1s→60s. Sender's numeric Telegram
`user_id` is exposed at `message.metadata["telegram_user_id"]` — use this (not
`sender.display_name`) for RBAC/per-user memory; it's stable, display names aren't.
`sender.platform_id` holds the **chat_id**, not the user_id (replies address the chat).

### 1.4 Discord

```toml
[channels.discord]
bot_token_env = "DISCORD_BOT_TOKEN"
default_agent = "coder"
```
Requires the **Message Content Intent** privileged gateway intent enabled in the Developer
Portal, or the bot receives empty message bodies. Gateway WebSocket v10, `MESSAGE_CREATE` events,
auto reconnect/heartbeat/session-resume.

### 1.5 Slack

```toml
[channels.slack]
bot_token_env = "SLACK_BOT_TOKEN"
app_token_env = "SLACK_APP_TOKEN"
default_agent = "ops"

[channels.slack.overrides]
# output_format = "slack_mrkdwn"
# threading = true
```
Needs both `xapp-...` (Socket Mode, scope `connections:write`) and `xoxb-...` (scopes
`chat:write`, `app_mentions:read`, `im:history`, `im:read`, `im:write`). Socket Mode means no
public webhook URL is needed. `threading = true` replies via `thread_ts`.

### 1.6 Channel Overrides (`[channels.<name>.overrides]`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `model` | `Option<String>` | agent default | per-channel model override |
| `system_prompt` | `Option<String>` | agent default | |
| `dm_policy` | `Respond` \| `AllowedOnly` \| `Ignore` | `Respond` | |
| `group_policy` | `All` \| `MentionOnly` \| `CommandsOnly` \| `Ignore` | `MentionOnly` | |
| `rate_limit_per_user` | `u32` | `0` (unlimited) | sliding window, messages/minute, via `DashMap` in `rate_limiter.rs` |
| `threading` | `bool` | `false` | reply-in-thread where the platform supports it |
| `output_format` | `Markdown`\|`TelegramHtml`\|`SlackMrkdwn`\|`PlainText` | `Markdown` | |
| `usage_footer` | `Option<UsageFooterMode>` | none | append token-usage footer |

Policy enforcement runs in `dispatch_message()` **before** the agent loop — ignored messages
consume zero LLM tokens.

### 1.7 Agent routing / bindings

Routing precedence: **`[[bindings]]` (most specific) → channel `default_agent` → prior
user↔agent binding → `/agent <name>` command → first available agent (fallback)**.

```toml
[[bindings]]
agent = "researcher-medical"
match_rule = { channel = "discord", channel_id = "1234567890" }
```
- `peer_id` = the **user**; `channel_id` = the **room/conversation**. Easy to confuse.
- Specificity scores (summed, ties broken by declaration order): `peer_id`=8, `channel_id`=8,
  `guild_id`=4, `roles`=2, `account_id`=2, `channel`=1.
- Only these adapters populate `channel_id` from `sender.platform_id`: `discord`, `slack`,
  `telegram`, `matrix`, `mattermost`, `teams`, `webex`, `rocketchat`, `nextcloud`, `pumble`,
  `revolt`, `guilded`, `feishu`/`lark`, `keybase`, `google_chat`, `line`, `twist`, `flock`,
  `twitch`. Binding a `channel_id` rule on any other adapter (Reddit, Bluesky, Mastodon, Signal,
  Email, ntfy, Discourse, …) silently never matches unless that adapter happens to write a
  `channel_id` key into metadata — the kernel logs a startup warning in this case. Source of
  truth: `CHANNELS_WITH_PLATFORM_ID_AS_CHANNEL` in `openfang-types::config`.
- `AgentBinding`/`BindingMatchRule` use `#[serde(deny_unknown_fields)]` — a typo like
  `match_rules` or `channnel_id` fails config load loudly instead of silently matching nothing.
  The top-level `KernelConfig` stays permissive on unknown keys (forward-compat).

### 1.8 Known security issues

#### 1.8a Credential leaks — closed in this fork (FANG-39/43/44), not present in stock v0.6.9

Three distinct credential leaks existed on this instance under stock v0.6.9 and are fixed on
`main`. If this instance ever ran the stock build before these commits landed, treat every
credential below as burned and rotate it — the fix stops future leaks, it does nothing about a
token that already went out in a log line or a session file. `ofdoctor` does not scan session
files for this, so do it by hand — look for a bot token pattern (`bot[0-9]+:`) or `access_token=`/
`?token=` query fragments inside stored sessions and logs:

```bash
grep -rlE 'bot[0-9]+:[A-Za-z0-9_-]{30,}' "$D"/sessions/ "$D"/logs/ 2>/dev/null
grep -rlE '(access_token|token)=' "$D"/sessions/ "$D"/logs/ 2>/dev/null
```

Any hit is a live secret sitting in a persisted session or log, independent of whether the fix is
deployed now — a match means rotate, a clean scan is not proof nothing leaked before rotation.

1. **Telegram bot token in the LLM-visible prompt and in the session history on disk.**
   `telegram_get_file_url()` (`crates/openfang-channels/src/telegram.rs:904-923`) builds a
   download URL of the shape `{api_base_url}/file/bot{token}/{file_path}` — the live bot token is
   embedded in the URL, not passed as a header. For documents and voice messages, `bridge.rs`
   formats that URL straight into agent-visible text: `[User sent a file ({filename}): {url}]`
   (`crates/openfang-channels/src/bridge.rs:923`, `:1048`, `:1075`) — no download step in between,
   unlike photos. That text becomes part of the LLM prompt and gets persisted verbatim in the
   session on disk. Fixed by `redact_file_token()` (`telegram.rs:58`), applied at the two
   `ChannelContent::File`/`ChannelContent::Voice` construction sites (`telegram.rs:1057`, `:1070`)
   before the URL ever reaches `ChannelContent` — the token is stripped, not merely masked in
   display.
2. **The same token via `reqwest::Error`.** `reqwest` attaches the full request URL to
   connection-level errors (failed connect, timeout, TLS, redirect). A bare `{e}` on such an error
   — in a log line, or (in the Multipart image-download path) in agent-visible text — printed the
   token in cleartext. Fixed by `redact_reqwest_error()` (`crates/openfang-channels/src/redact.rs`,
   `e.without_url()`), called on every `.send()` in `telegram.rs` and on the image-download error
   path in `bridge.rs:1628-1642`.
3. **Raw provider error body in `fallback.reason`.** A 401 from an LLM provider typically quotes
   the rejected key back in its error body; before the fix that raw body reached
   `fallback.reason`/`calls[].reason` in the `/message`, SSE, WS and `/v1/chat/completions`
   responses (see §4.6) verbatim. Fixed in
   `crates/openfang-runtime/src/drivers/fallback.rs:182-188,233-239`: every substitution's error is
   routed through `llm_errors::classify_error(...).sanitized_message`
   (`crates/openfang-runtime/src/llm_errors.rs:243,406`), which redacts `sk-`/`key-`/`Bearer `/
   `bearer ` prefixed fragments (`redact_secrets`, `llm_errors.rs:495`), strips HTML error pages,
   and caps length — before the string is ever assigned to `first_error`.

None of these three appear in `docs/security.md`, `docs/channel-adapters.md`, or the WhatsApp/
Matrix issues below — they were closed with no upstream GitHub issue number, so the only place
this is recorded is here and in the fork's own commit history.

#### 1.8b Six channel adapters leaked credentials via `reqwest::Error` — closed (FANG-39/44)

Same root cause as leak #2 above, six more adapters: `dingtalk`, `messenger`, `flock`, `threema`,
`wecom`, `gotify`. All embed their credential in the request URL's query string (`?access_token=`,
`?token=`), so an unredacted `reqwest::Error` on a failed request prints it. All six are closed —
every `.send()` in each adapter now runs through
`redact_reqwest_error()`/`crate::redact::redact_reqwest_error` (defined once in
`crates/openfang-channels/src/redact.rs`, imported per-adapter).

The blast radius was not identical across all six, though, and it is worth knowing which:

- **`dingtalk`, `messenger`, `wecom`, `gotify`** — the credential is in the URL for *every* call,
  including the one that actually sends a message (`send()`/`api_send_message()`:
  `dingtalk.rs:271,299-301`; `messenger.rs:98-130,379-420`; `wecom.rs:303-329`;
  `gotify.rs:111-145`). So the send path itself needed redaction, and got it.
- **`flock`, `threema`** — only `validate()` puts the credential in the URL query string
  (`flock.rs:64-78`, `threema.rs:67-80`, both explicitly commented `FANG-44`); the actual
  send/`api_send_message()` call carries the credential in the JSON body or form-encoded POST
  data instead (`flock.rs:99-113`, `threema.rs:96-111`, both commented "no redaction needed on the
  `.send()` below" — verified true: `reqwest` never attaches a request *body* to an error, only the
  URL). So for these two, only the one-time credential-check call was ever at risk, not every
  message sent. `validate()` is still redacted (`flock.rs:78`, `threema.rs:80`) — belt and braces —
  but the send path never needed it.

Practical read: if this instance ran stock v0.6.9 with any of these six channels configured, rotate
that channel's credential regardless of which sub-case it falls into — `validate()` runs at every
adapter startup, so the exposure window existed even for `flock`/`threema`.

#### 1.8c Open upstream issues (verified open on GitHub, `RightNow-AI/openfang`)

**WhatsApp gateway (`packages/whatsapp-gateway/index.js`, a separate Node.js sidecar process,
NOT the in-process `whatsapp.rs` Cloud-API adapter)** — three open issues, all still reproducible
in this checkout:

- **#1232** — the gateway calls `POST /api/agents/{id}/message` on the Rust API
  (`index.js:194-205`) with **no `Authorization` header at all**. Confirmed still true:
  `grep -n "Authorization" packages/whatsapp-gateway/index.js` returns nothing. If the Rust API's
  loopback-trust exemption is in effect (no `api_key` configured, bind is loopback), any WhatsApp
  contact who messages the linked number can silently drive agent tool execution.
- **#1233** — WhatsApp message text is forwarded verbatim to the agent via `forwardToOpenFang`
  (`index.js:130-176`) with no length cap, no per-sender rate limit, no "this is untrusted
  external content" tagging. Prompt-injection and LLM-spend-DoS vector from any stranger who
  texts the linked number.
- **#1234** — every gateway HTTP endpoint (`/login/start`, `/login/status`, `/message/send`,
  `/health`) has zero auth **and** sets `Access-Control-Allow-Origin: *` (confirmed at
  `index.js:270,279`). Binds to `127.0.0.1:3009` (`WHATSAPP_GATEWAY_PORT`), but browsers can reach
  loopback — this is a textbook localhost-CSRF/DNS-rebinding vector: any web page the user's
  browser visits can `fetch('http://127.0.0.1:3009/message/send', ...)` and send WhatsApp
  messages as the linked account.
  - All three are filed by the same reporter (BunnyMoth) as hardening suggestions, not disputed
    bugs; none had a merged fix as of this check. **Do not expose the gateway port or run it on a
    machine with untrusted browser traffic until fixed.**

**Matrix E2EE — #1177** — the bundled Matrix adapter (`crates/openfang-channels/src/matrix.rs`)
is **plaintext-only**: zero references to `encrypted`/`olm`/`megolm`. It cannot participate in
encrypted rooms (which is the *default* for Element-created DMs and most new rooms) — it either
silently drops undecryptable `m.room.encrypted` events, or sends outbound replies unencrypted
into a room that expects megolm ciphertext (visible only to non-encrypted observers / rejected by
encrypted-room clients). No fix landed as of this check; workaround suggested in the issue is a
host-side `matrix-rust-sdk` or `mautrix-python` sidecar for any room that requires E2EE.

---

## 2. MCP (Model Context Protocol)

Protocol version implemented: `2024-11-05`. Source: client `crates/openfang-runtime/src/mcp.rs`,
server `crates/openfang-runtime/src/mcp_server.rs`, CLI entry `crates/openfang-cli/src/mcp.rs`.

### 2.1 OpenFang as MCP client — `[[mcp_servers]]`

```toml
[[mcp_servers]]
name = "github"
timeout_secs = 30
env = ["GITHUB_PERSONAL_ACCESS_TOKEN"]

[mcp_servers.transport]
type = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
```
Transports: `stdio` (subprocess, newline-delimited JSON-RPC) or `sse` (`{ type = "sse", url =
"https://..." }`). Tools are namespaced `mcp_{server}_{tool}` (hyphens→underscores, lowercased),
e.g. server `github` tool `create_issue` → `mcp_github_create_issue`. Discovery happens once at
kernel boot (`connect_mcp_servers()` → `initialize` → `notifications/initialized` → `tools/list`);
results cache into `kernel.mcp_tools`. Dropping an `McpConnection` kills stdio subprocesses via
`Drop` (`child.start_kill()`).

Security guarantees (verified in code, `docs/mcp-a2a.md:826-836`):
- Stdio subprocess environment is fully `env_clear()`'d — only vars listed in `env = [...]` plus
  `PATH` reach the child. Prevents leaking the kernel's full env (including other channel
  tokens/LLM keys) to a third-party MCP server binary.
- Stdio command path is checked to reject `..` path-traversal.
- SSE transport URL is checked against known cloud metadata endpoints
  (`169.254.169.254`, `metadata.google`) to block SSRF.
- 10 MB max message size on the stdio server side; oversized messages are drained and rejected.
- Default per-request timeout 30s (`timeout_secs`).

### 2.2 OpenFang as MCP server

Two independent surfaces, with **different tool sets**:

1. **`openfang mcp`** (stdio, for IDEs) — each OpenFang **agent** becomes one MCP tool named
   `openfang_agent_{name}` (hyphens→underscores), taking a single `message` string param. It
   checks for a running daemon first (`find_daemon()`) and proxies to it over HTTP; if none is
   running it boots an in-process kernel (`McpBackend::InProcess`) as fallback. Only agents are
   exposed this way — not the full tool catalog.
   ```json
   { "mcpServers": { "openfang": { "command": "openfang", "args": ["mcp"] } } }
   ```
   (drop into `.cursor/mcp.json`, VS Code MCP settings, or `claude_desktop_config.json`)

2. **`POST /mcp`** (HTTP, JSON-RPC) — exposes the kernel's **full** tool set: all **65** built-in
   tools (`mcp_http` calls `builtin_tool_definitions()` verbatim, `routes.rs:7221`) + installed skill
   tools + all connected `[[mcp_servers]]` tools, executed through
   `kernel.execute_tool()`. Verified live: `tools/list` returns **65** on this instance (no skills,
   no MCP servers). **This endpoint requires the global `api_key`** if one is configured
   — confirmed empirically: `POST /mcp` with `tools/list` → `401` with no `Authorization` header,
   `200` with a correct `Bearer <api_key>`. (Neither doc file states this explicitly — the MCP
   endpoints table in `docs/mcp-a2a.md` has no Auth column at all.)

Stdio framing: `Content-Length: N\r\n\r\n<json>` , 10 MB message cap. Methods: `initialize`,
`notifications/initialized`, `tools/list`, `tools/call`; unknown method → JSON-RPC `-32601`.

`GET /api/mcp/servers` lists configured + connected servers/tools — **also requires
`api_key`** (empirically `401` unauthenticated, `200` with the key); it is not in the HTTP
middleware's public-path allowlist despite being a read-only listing endpoint.

### 2.3 Known MCP gaps (open GitHub issues)

- **#1184** — "Windows has no working MCP bridge". **The crate this issue describes is not in
  v0.6.9.** `crates/` contains 13 members and none is `openfang-mcp-bridge`; there is no
  `bridge_ipc` module. The MCP implementation that *does* ship —
  `crates/openfang-runtime/src/mcp.rs` and `mcp_server.rs` — has **zero `#[cfg(unix)]` gates**, and
  stdio transport is a plain cross-platform `tokio::process::Command` (`mcp.rs:224`). The issue is
  open + `needs-design` upstream, but do not repeat its claim against this tree.
- **#1096** — OpenFang's MCP client never handles **server-initiated notifications**.
  `crates/openfang-runtime/src/mcp.rs:94-97,275,320` wires every connection with a bare
  `rmcp::model::ClientInfo`, which uses rmcp's no-op `ClientHandler` default — so
  `notifications/resources/updated`, `tools/list_changed`, `prompts/list_changed`, `progress`,
  `cancelled`, `message` are received and typed by rmcp but dropped into no-op callbacks.
  `resources/subscribe` is also never issued, so `resources/updated` can't fire regardless.
  `ClientCapabilities` are `::default()` (no negotiation). Net effect: hosted MCP servers that
  rely on push (inbox/chat-bridge/filesystem-watch/DB-LISTEN MCP servers) cannot drive reactive
  behavior — only `schedule_create` polling or a bespoke webhook bridge work today.

---

## 3. A2A (Agent-to-Agent Protocol)

Source: `crates/openfang-runtime/src/a2a.rs`, routes in `crates/openfang-api/src/routes.rs`,
config in `openfang-types::config` (`A2aConfig`, `ExternalAgent`).

```toml
[a2a]
enabled = true
listen_path = "/a2a"          # currently informational; live routes are hardcoded to /a2a/*

[[a2a.external_agents]]
name = "research-agent"
url = "https://research.example.com"
```
`enabled = false`/absent ⇒ discovery + task store logic inert, but the HTTP routes are always
registered (they just no-op / return empty results).

Agent Card served at `GET /.well-known/agent.json` (first registered agent, or a placeholder if
none). `AgentCapabilities.push_notifications` is hardcoded `false` — not implemented.

### 3.1 A2A endpoints — real auth behavior (doc is wrong here, see §4)

| Method | Path | Docs claim | **Actual (verified)** |
|---|---|---|---|
| GET | `/.well-known/agent.json` | Public | Public (any GET) |
| GET | `/a2a/agents` | Public | Public (any GET) |
| **POST** | `/a2a/tasks/send` | **Public** | **Requires `Authorization: Bearer <api_key>`** — `401` unauthenticated (confirmed via curl) |
| GET | `/a2a/tasks/{id}` | Public | Public (any GET) |
| **POST** | `/a2a/tasks/{id}/cancel` | **Public** | **Requires auth** (same reasoning — POST) |

Root cause: the middleware's public-path rule is `path.starts_with("/a2a/") && is_get` — GET is
free, but any POST under `/a2a/` needs the API key. `docs/mcp-a2a.md`'s A2A endpoint table
(§ "A2A API Endpoints") marks all five rows `Public`, which is simply incorrect for the two POST
rows whenever `api_key` is set.

There is a **second, separate** A2A surface for outbound/external-agent management, all of which
require auth (none of these are in the public allowlist):
`GET /api/a2a/agents` (**this** one IS public — GET-only, distinct from the two above),
`POST /api/a2a/discover`, `POST /api/a2a/send`, `GET /api/a2a/tasks/{id}/status`.

### 3.2 Task lifecycle

States: `Submitted → Working → (InputRequired) → Completed | Cancelled | Failed`.
`A2aTaskStore` is an in-memory `Mutex<HashMap<String, A2aTask>>`, bounded at `max_tasks: 1000`,
FIFO-evicting the oldest completed/failed/cancelled task when full. `A2aClient` (outbound) uses a
30s timeout and sends `User-Agent: OpenFang/0.1 A2A`; failed discovery at boot is logged as a
warning, does not block startup.

---

## 4. HTTP API — endpoint table and the real auth matrix

### 4.1 The docs are wrong about auth scope

`docs/api-reference.md` §Authentication states:

> "When an API key is configured..., all endpoints (except `/api/health` and `/`) require a
> Bearer token" ... "Public Endpoints (No Auth Required): `GET /api/health`, `GET /`"

**This is false.** The real source of truth is the `is_public` predicate in
`crates/openfang-api/src/middleware.rs:145-182`, which whitelists **38** paths/patterns
regardless of `api_key` (counted 2026-08-24; an earlier revision said "dozens" at lines 98-140). Verified empirically against the live instance (which has a 51-char
`api_key` set) with zero `Authorization` header:

| Path | Method(s) public | Live result (no auth) |
|---|---|---|
| `/` , `/logo.png`, `/favicon.ico` | any | 200 |
| `/manifest.json`, `/sw.js` | — **NOT public** despite being static PWA assets next to logo.png/favicon.ico | **401** |
| `/.well-known/agent.json` | GET | 200 |
| `/a2a/*` | GET only | 200 (GET) / 401 (POST) |
| `/api/health`, `/api/health/detail`, `/api/status`, `/api/version` | any | 200 |
| `/api/agents` | GET only (POST spawns → auth) | 200 |
| `/api/profiles` | GET | 200 |
| `/api/config`, `/api/config/schema` | GET | 200 |
| `/api/uploads/{file_id}` | GET | 400 on bogus id (route matched, public) |
| `/api/models`, `/api/models/aliases` | GET | 200 |
| `/api/providers` | GET | 200 |
| `/api/budget`, `/api/budget/agents`, `/api/budget/agents/{id}` | GET | 200 |
| `/api/network/status` | GET | 200 |
| `/api/a2a/agents` | GET | 200 |
| `/api/approvals`, `/api/approvals/{id}` | GET | 200 |
| `/api/channels` | GET | 200 |
| `/api/hands`, `/api/hands/active`, `/api/hands/{id}` | GET | 200 |
| `/api/skills`, `/api/skills/{id}/config` | GET | 200 |
| `/api/sessions` | GET **list only** — `/api/sessions/{id}` is NOT covered (only `DELETE` exists on it → `405` unauth-but-public-path, `401` if a GET-style path were checked against a real route) | 200 (list) |
| `/api/integrations`, `/api/integrations/available`, `/api/integrations/health` | GET | 200 |
| `/api/workflows` | GET | 200 |
| `/api/logs/stream` | any (SSE) | 200 |
| `/api/cron/{...}` (GET subpaths, e.g. `/api/cron/jobs`) | GET | 200 |
| `/api/providers/github-copilot/oauth/*` | any | 200 (405 if method unsupported) |
| `/api/auth/login`, `/api/auth/logout` | any | 200 (login is POST-only → 405 on GET, still "public" i.e. not gated by api_key) |
| `/api/auth/check` | GET | 200 |
| `/api/shutdown` | any, **but loopback-only** regardless of api_key (`path == "/api/shutdown" && is_loopback`) | n/a from host-as-loopback |

Everything else — **including things you might assume are safe reads** — requires the Bearer
token. Empirically confirmed `401` unauthenticated, `200` with `Authorization: Bearer <api_key>`:
`GET /api/settings` (route doesn't even exist at that literal path — see §4.3),
`GET /api/mcp/servers`, `GET /api/metrics` (Prometheus scrape endpoint!), `GET /api/peers`,
`GET /api/tools`, `GET /api/usage*`, `GET /api/security`, `GET /api/commands`,
`GET /api/bindings`, `GET /api/comms/topology`, `GET /api/templates`, `GET /v1/models`,
`POST /v1/chat/completions`, `POST /api/providers/{name}/key`, any `POST`/`PUT`/`DELETE` to
`/api/agents/*`, `/api/cron/jobs`, `/api/skills/install`, `/api/channels/*`.

**Practical takeaway for reverse-proxy exposure**: if you plan to expose OpenFang behind a proxy
and rely on "GET is read-only and safe," note that GET `/api/config` (full config, including
which provider/model is active and env var *names* — though not the secret values themselves) and
GET `/api/channels` are both public. Nothing in the public list leaks a raw secret value (secrets
are stored as env var **names**, not literal values, in config responses — see
`routes.rs:5689`: `"api_key": if config.api_key.is_empty() { "not set" } else { "***" }`).
`GET /api/metrics` and `GET /api/mcp/servers` are *not* public, which is inconsistent with
`/api/status`/`/api/providers`/`/api/agents` also being read-only listing endpoints that *are*
public — there's no principled "read-only ⇒ public" rule, it's an explicit allowlist that must be
checked per-path, not inferred.

### 4.2 Auth mechanics (`middleware::auth`, `middleware.rs:71-243`)

- Accepts `Authorization: Bearer <api_key>` **or** `X-API-Key: <api_key>` header **or**
  `?token=<api_key>` query param (for SSE/EventSource clients that can't set headers) — all
  compared with `subtle::ConstantTimeEq` (timing-attack resistant).
- If `api_key` is empty/unset in config **and** dashboard auth (`auth.enabled`) is off: requests
  from a loopback source IP are allowed through with no credential at all; non-loopback requests
  get `401` **unless** `OPENFANG_ALLOW_NO_AUTH=1` is set (logged loudly at startup — this is the
  fix for issue #1034 B1/B2, where an empty `api_key` previously bypassed auth for *any* origin).
  This loopback determination uses `ConnectInfo<SocketAddr>` — inside a container, "loopback"
  means loopback **as seen by the axum listener**, i.e. what Docker's proxy presents, not
  necessarily the real origin's IP.
- If `api_key` **is** set (as it is on this live instance), loopback status is irrelevant — every
  non-public path needs the correct Bearer/X-API-Key/`?token=` regardless of source IP.
- Dashboard session cookies (`openfang_session`) are accepted as an alternate credential only
  when `auth.enabled = true`, verified via `session_auth::verify_session_token` against
  `session_secret` (which `server.rs` sets to the `api_key` if one exists, else the dashboard
  password hash).
- WebSocket upgrades (`ws.rs::check_ws_auth`) implement **the same rules independently** —
  header, `?token=`, session cookie, loopback-if-no-key. Kept deliberately consistent with the
  HTTP path per issue #1189 (previously WS diverged and let a loopback attacker bypass a
  configured dashboard login).

### 4.3 Gotcha: some "endpoints" in casual descriptions aren't real routes

`/api/settings` is **not a route**. The real per-hand settings path is
`GET/PUT /api/hands/{hand_id}/settings`. Hitting the literal string `/api/settings` returns `401`
unauthenticated (middleware runs before route dispatch, so any non-public path 401s regardless of
whether a route exists) and `404` once authenticated (no matching route). Don't assume a `401`
proves the endpoint exists — check `server.rs` for the real route table.

### 4.4 `/hooks/wake` and `/hooks/agent` — a double-auth trap

These two webhook-trigger endpoints (`routes::webhook_wake`, `routes::webhook_agent`,
`routes.rs:11733-11786` and `:11792-11876`) are gated **twice**, and both checks read the **same** `Authorization`
header:

1. The global `middleware::auth` layer — `/hooks/*` is **not** in the public-path allowlist, so
   this requires `Authorization: Bearer <api_key>` (the global key) or the request 401s before
   the handler even runs.
2. Inside the handler, `validate_webhook_token()` (`routes.rs:12157-12176`) re-reads the *same*
   `Authorization: Bearer <token>` header and compares it against a **different** secret — the
   env var named by `webhook_triggers.token_env` (default `OPENFANG_WEBHOOK_TOKEN`, config struct
   `WebhookTriggerConfig` in `openfang-types::config`, disabled/`None` by default — confirmed
   absent from the live config).

**Correction to an earlier version of this section:** the two checks are *not* mutually exclusive,
because the middleware accepts the API key from a **query parameter** as well as from the header.
`middleware.rs:238-243` honours `?token=<api_key>`, which satisfies check 1 and leaves the
`Authorization: Bearer` header free for `validate_webhook_token` (`routes.rs:12157-12176`). Verified
live:

| Attempt | Result |
|---|---|
| `Authorization: Bearer <API_KEY>` only | 401 `{"error":"Invalid or missing token"}` |
| `Authorization: Bearer <WEBHOOK_TOKEN>` only | 401 `{"error":"Invalid API key"}` |
| `X-API-Key: <API_KEY>` + `Authorization: Bearer <WEBHOOK_TOKEN>` | 401 — `X-API-Key` is only consulted when there is **no** Bearer header (`middleware.rs:222-227`) |
| **`?token=<API_KEY>` + `Authorization: Bearer <WEBHOOK_TOKEN>`** | **200** ✅ |

So a distinct scoped webhook token *is* usable; you just have to pass the master key in the query
string. (Setting the two secrets equal also works, as does an empty `api_key` + loopback caller.)
The token must be **≥ 32 characters** or every call 401s with no other diagnostic.

`OPENFANG_WEBHOOK_TOKEN` is genuinely undocumented: it appears exactly **once** in the whole repo, at
`crates/openfang-types/src/config.rs:458`, and in **zero** `.md` files. None of
`channel-adapters.md`, `mcp-a2a.md` or `api-reference.md` mentions `webhook_triggers` or these two
routes.

### 4.5 Compact endpoint groups (see `server.rs` for the exhaustive ~172-route table)

| Group | Representative paths | Auth |
|---|---|---|
| Agents | `/api/agents`, `/api/agents/{id}/{message,session,mode,tools,skills,mcp_servers,clone,upload,ws,update,...}` | GET `/api/agents` public; everything else (including all mutating verbs) auth required. `PUT /api/agents/{id}/update` is auth-gated like the rest of the group but on this fork returns **501** and changes nothing — it does not silently no-op like stock's 200; see `SKILL.md` §"Point-editing an agent vs. `PUT /api/agents/{id}/update`" for the 8 routes that actually mutate a manifest field |
| Channels | `/api/channels`, `/api/channels/{name}/configure`, `/api/channels/whatsapp/qr/*` | GET list public; configure/test/reload/QR all require auth |
| Skills / Hands / ClawHub | `/api/skills*`, `/api/hands*`, `/api/clawhub/*` | GET listing endpoints mostly public (except `.../config` variants which are also GET-public); install/uninstall/activate require auth |
| Workflows / Triggers / Cron | `/api/workflows*`, `/api/triggers*`, `/api/cron/jobs*`, `/api/schedules*` | GET `/api/workflows` and GET `/api/cron/*` public; create/update/delete/run require auth; `/api/triggers` and `/api/schedules` are **not** in the public list at all (even GET requires auth) |
| MCP | `GET /api/mcp/servers`, `POST /mcp` | **Both require auth** (contrary to the read-only-should-be-public pattern elsewhere) |
| A2A (inbound) | `/.well-known/agent.json`, `/a2a/agents`, `/a2a/tasks/send`, `/a2a/tasks/{id}`, `/a2a/tasks/{id}/cancel` | GET public, POST requires auth (docs say all public — wrong, see §3.1) |
| A2A (outbound mgmt) | `/api/a2a/agents` (GET, public), `/api/a2a/discover`, `/api/a2a/send`, `/api/a2a/tasks/{id}/status` | discover/send/status all require auth |
| OpenAI-compat | `POST /v1/chat/completions`, `GET /v1/models` | Auth required (the `Authorization: Bearer` header doubles as the OpenAI SDK's API key slot, which is convenient) |
| Dashboard auth | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/check` | All three explicitly public (so you can attempt login without already having a session) |
| Metrics / Peers / Comms | `/api/metrics`, `/api/peers`, `/api/comms/*` | Auth required |
| Webhooks | `/hooks/wake`, `/hooks/agent` | Auth required (global key) **and** a second, separate `OPENFANG_WEBHOOK_TOKEN` check — see §4.4 |

### 4.6 Model/provider/fallback disclosure — every response surface (FANG-57)

Accounting is per **LLM call**, not per turn — a turn is `Vec<LlmCall>`, and every surface below
is a projection of that same array, so they cannot disagree with each other
(`crates/openfang-types/src/usage.rs`, `LlmCall`/`FallbackSummary`). Four surfaces disclose it, one
only partially:

**`POST /api/agents/{id}/message`** (non-streaming) — the JSON response gains four fields beyond
the pre-fork `response`/`input_tokens`/`output_tokens`/`iterations`/`cost_usd`
(`crates/openfang-api/src/types.rs:58-78`, wired at `routes.rs:417-432`):
```json
{
  "response": "...", "input_tokens": 7140, "output_tokens": 2, "iterations": 1,
  "model_used": "google/gemma-4-31b-it",
  "provider_used": "hyperfusion",
  "fallback": null,
  "calls": [
    {"n": 0, "provider": "hyperfusion", "model": "google/gemma-4-31b-it",
     "input_tokens": 7140, "output_tokens": 2, "tool_calls": 0, "cost_usd": 0.0}
  ]
}
```
`model_used`/`provider_used` name whoever served the **last** call of the turn
(`openfang_types::usage::last_served`). `fallback` is `null`/omitted
(`#[serde(skip_serializing_if = "Option::is_none")]`) unless some call in the turn was served by a
substitute, in which case it's a `FallbackSummary` — see `providers-and-models.md` for its six
fields (`used`, `calls`, `of`, `requested`, `served_by`, `reason`); `reason` is the sanitized
string from §1.8a point 3, never the raw provider error. `calls` is one entry per LLM call, in
turn order, and is omitted entirely (not `[]`) when empty. Verified live 2026-08-14 against
`agent bf7564a1-…`: response matched this shape exactly.

**`POST /api/agents/{id}/message/stream`** (SSE) — a new event type, `call`, fires once per LLM
call as it completes (`routes.rs:1646-1666`), alongside the pre-existing `chunk`/`tool_use`/
`tool_result`/`done` events (`done`'s shape is untouched, so old clients that only parse `done`
still work):
```
event: call
data: {"n":0,"provider":"hyperfusion","model":"google/gemma-4-31b-it",
       "requested":null,"reason":null,
       "usage":{"input_tokens":7140,"output_tokens":2}}
```
`requested`/`reason` are non-null only when this call substituted for a different requested model.

**`GET /api/agents/{id}/ws`** (WebSocket) — the `{"type":"response", ...}` message gains the same
four fields as the non-streaming HTTP response — `model_used`, `provider_used`, `fallback`,
`calls`  — computed from the accumulated `stream_calls` of the turn, with `cost_usd` filled in
per-call from the model catalog just before sending (`crates/openfang-api/src/ws.rs:959-1000`).

**`POST /v1/chat/completions` — non-streaming only.** The OpenAI-compatible `model` field in the
response stays the *agent name* the client asked for (so clients that compare `response.model`
against their request don't break); the model that actually served the turn goes into a vendor
extension object inserted at the top level, `"openfang"`
(`crates/openfang-api/src/openai_compat.rs:356-373`):
```json
{ "id": "...", "object": "chat.completion", "model": "assistant", "choices": [...],
  "usage": {...},
  "openfang": {"model_used": "...", "provider_used": "...", "fallback": null, "calls": [...]} }
```
**The streaming variant (`"stream": true`) does not carry this.** `stream_response()`
(`openai_compat.rs:394-550`) forwards `TextDelta`/`ToolUseStart`/`ToolInputDelta` into OpenAI-shape
chunks but has no arm for `StreamEvent::CallReported` — it falls into the catch-all `_ => continue`
(`openai_compat.rs:529`) and is dropped. A client polling model/provider/fallback off a streaming
`/v1/chat/completions` response gets nothing; it has to use the SSE `call` event on
`/message/stream` instead, or fall back to non-streaming `/v1/chat/completions` for the vendor
field.

---

## 5. Gotchas (all verified against v0.6.9 code and/or the live instance)

1. **`docs/api-reference.md`'s Authentication section is materially wrong.** It says only
   `GET /api/health` and `GET /` are public; in reality **38 distinct path patterns**
   (counted in `is_public()`, `middleware.rs:145-182`, 2026-08-24; see §4.1) are public
   by design, including `GET /api/config`, `GET /api/agents`, `GET /api/providers`,
   `GET /api/channels`, `GET /api/sessions` (list). Don't use the doc to decide what's safe to
   expose — check `middleware.rs`'s `is_public` predicate or this file's table.
2. **`docs/mcp-a2a.md`'s A2A endpoint table is wrong for the two POST rows.** `POST
   /a2a/tasks/send` and `POST /a2a/tasks/{id}/cancel` are marked "Public" in the doc but return
   `401` without the API key in practice — the middleware's rule is GET-only for `/a2a/*`.
3. **`manifest.json` and `sw.js` are not public** even though they sit next to `logo.png` and
   `favicon.ico` in the router and are needed for PWA installability — expect a broken "Add to
   Home Screen" prompt behind an API key unless you special-case these two paths at your reverse
   proxy.
4. **`GET /api/metrics` (Prometheus) and `GET /api/mcp/servers` both require the API key**, unlike
   most other GET-only listing endpoints. If you're wiring a Prometheus scraper, it needs the
   Bearer token — there's no separate metrics-only credential.
5. **`/hooks/wake` and `/hooks/agent` are double-gated, but they ARE reachable.** The global API key
   and the per-feature `OPENFANG_WEBHOOK_TOKEN` are both read from `Authorization` — pass the API key
   as **`?token=<API_KEY>`** instead and put the webhook token in the Bearer header (verified 200).
   `X-API-Key` does not help: it is only consulted when no Bearer header is present. The token must
   be ≥ 32 chars. `OPENFANG_WEBHOOK_TOKEN` appears exactly once in the repo (`config.rs:458`) and in
   no `.md` file.
6. **`/api/settings` is not a real endpoint** — don't be fooled by a `401` into thinking it
   exists; the real path is `/api/hands/{hand_id}/settings`. A `401` from the auth middleware
   proves nothing about route existence, since middleware runs before routing.
7. **`/api/sessions/{id}` only supports `DELETE`** (and `/api/sessions/{id}/label` only `PUT`) —
   there is no `GET` for a single session; `GET /api/sessions` (no id) is the only public reader
   and it lists all sessions.
8. **The WhatsApp *gateway* (`packages/whatsapp-gateway/`, a Node sidecar) is a different trust
   boundary than the in-process `whatsapp.rs` Cloud API adapter** and, per #1232/#1233/#1234, has
   no auth on its own HTTP surface, sends no auth to the Rust API it forwards into, and sets
   wildcard CORS. Treat it as unsafe to run on any machine with untrusted local browser traffic.
   **Correct env-var names** (an earlier version of this file invented `WA_ACCESS_TOKEN` /
   `WA_PHONE_ID` / `WA_VERIFY_TOKEN`, which appear **nowhere** in the repo): the in-process Cloud API
   adapter uses **`WHATSAPP_ACCESS_TOKEN`** and **`WHATSAPP_VERIFY_TOKEN`**
   (`config.rs:2062-2087`, `routes.rs:1798-1805`) plus a `phone_number_id` **config field**;
   Web/QR (gateway) mode is selected by **`WHATSAPP_WEB_GATEWAY_URL`** (`whatsapp.rs:23`). Those
   Cloud-API credentials have nothing to do with the gateway's security posture — the gateway runs a
   Baileys-style QR-login session of its own.
9. **Matrix E2EE is entirely unimplemented (#1177)** — `matrix.rs` has zero olm/megolm code. Any
   Matrix room your bot account is in that defaults to encryption (which is most Element-created
   rooms) will not work: inbound messages arrive as undecryptable `m.room.encrypted` stubs and
   outbound replies go out in plaintext into a room expecting ciphertext.
10. **#1184's `openfang-mcp-bridge` crate does not exist in v0.6.9** — 13 crates, none named that,
    no `bridge_ipc`. `mcp.rs`/`mcp_server.rs` carry no `#[cfg(unix)]` and stdio uses
    `tokio::process::Command` (`mcp.rs:224`). The "Windows has no MCP transport" claim is about
    code that is not in this tree.
11. **OpenFang's MCP client ignores server push (#1096)** — `notifications/resources/updated`,
    `tools/list_changed`, etc. are received by the `rmcp` layer but land in no-op default
    handlers; `resources/subscribe` is never sent. Any hosted MCP server that depends on push
    (mail, chat-bridge, filesystem-watch, DB LISTEN/NOTIFY) degrades to "never fires" rather than
    "polls slowly" — there is no polling fallback implemented for subscription-style updates
    either; only `schedule_create`-driven periodic tool calls work as a workaround.
12. **`config.toml`'s `api_key` line is easily mis-grepped**: `grep '^api_key'` also matches the
    unrelated `api_key_env = "..."` line used by provider configs (e.g.
    `[default_model] api_key_env = "HYPERFUSION_API_KEY"`). Use the one form the whole package
    uses — `grep -m1 '^api_key = '` — where the space before `=` is what excludes `api_key_env`.
    Full explanation: `automation-workflows-triggers-schedules.md` gotcha 46.
13. **The docker container publishes port 4200 on more than just `127.0.0.1`** on this host (also
    on the Tailscale interface) — `docker port openfang-openfang-1` shows both bindings. Since
    the live instance *does* have a non-empty `api_key` configured, this is safe per the auth
    model in §4.2 (loopback-exemption only applies when `api_key` is empty) — but it's worth
    re-checking `docker port` after any redeploy that might reset `config.toml`, since an empty
    `api_key` + non-loopback bind + no `OPENFANG_ALLOW_NO_AUTH` would fail closed (401 everywhere
    non-loopback) rather than fail open, per the #1034 fix — annoying but not a leak.
14. **The channel count depends on which list you ask.** 43 adapter modules in
    `openfang-channels/src/`, 42 in `GET /api/channels` (`CHANNEL_REGISTRY`, `routes.rs:1742`), 12
    named `ChannelType` variants. `mqtt` is the odd one out: a working adapter with a `ChannelType`
    variant that the dashboard catalog omits, so it never appears in `/api/channels` or the config
    UI. `docs/channel-adapters.md`'s "40" matches none of these.
15. **`POST /mcp` exposes all 65 builtin tools and runs them with no agent context.** `mcp_http`
    (`routes.rs:7216-7311`) calls `execute_tool` with `allowed_tools = None`, `workspace_root = None`
    and `exec_policy = None`. Verified live with a valid key: `tools/list` → 65;
    `tools/call file_read {"path":"/data/secrets.env"}` → returns `HYPERFUSION_API_KEY`;
    `tools/call file_read {"path":"/etc/hostname"}` → returns the file. `shell_exec` *is* still
    approval-gated (blocks 60 s, then auto-denies, pending record shows `agent_id: "unknown"`), but
    if approved it runs with **no** `safe_bins`/`allowed_commands` check. Handing an IDE the API key
    is handing it a filesystem read primitive. Block `/mcp` at the edge if you don't use it.
    See `security-model.md` §3.6b.

---

## 6. Source-file index (for follow-up digging)

| Concern | File(s) |
|---|---|
| Channel adapter trait + all 43 adapter modules | `crates/openfang-channels/src/*.rs` |
| Dashboard channel catalog (the 42 in `GET /api/channels`) | `CHANNEL_REGISTRY`, `crates/openfang-api/src/routes.rs:1742` |
| Channel↔agent wiring, adapter init | `crates/openfang-api/src/channel_bridge.rs` |
| Output formatting | `crates/openfang-channels/src/formatter.rs` |
| Per-user rate limiting | `crates/openfang-channels/src/rate_limiter.rs` |
| MCP client | `crates/openfang-runtime/src/mcp.rs` |
| MCP HTTP/stdio server handler | `crates/openfang-runtime/src/mcp_server.rs`, `crates/openfang-cli/src/mcp.rs` |
| ~~MCP Windows bridge~~ | **does not exist in v0.6.9** — no `openfang-mcp-bridge` crate, no `bridge_ipc` module (see #1184 above) |
| A2A protocol types/logic | `crates/openfang-runtime/src/a2a.rs` |
| All HTTP routes (~172) | `crates/openfang-api/src/server.rs` |
| Route handlers | `crates/openfang-api/src/routes.rs` |
| Auth middleware (the real source of truth for auth) | `crates/openfang-api/src/middleware.rs` |
| WebSocket auth | `crates/openfang-api/src/ws.rs` |
| Config schema (channels, mcp_servers, a2a, webhook_triggers, bindings) | `crates/openfang-types/src/config.rs` |
| WhatsApp gateway (Node sidecar, separate trust boundary) | `packages/whatsapp-gateway/index.js` |
| WhatsApp Cloud API adapter (in-process, different from gateway) | `crates/openfang-channels/src/whatsapp.rs`, `crates/openfang-kernel/src/whatsapp_gateway.rs` |
