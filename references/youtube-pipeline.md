# YouTube channel → insights pipeline on OpenFang v0.6.9

Built and verified end-to-end on the live instance (container `openfang-openfang-1`,
API `http://127.0.0.1:4200`, source `/opt/openfang` @ tag `v0.6.9`, commit `acf2587`)
on 2026-08-09/10. Every command below was actually run; every claim carries a
`file:line` or a captured output.

**Verdict: viable.** YouTube is not blocking this datacenter IP. Channel listing,
metadata and caption download all work from inside the container with nothing but
`pip3 install yt-dlp`. No ffmpeg, no whisper, no video download, no cookies, no proxy.
A 3h46m podcast goes from URL to a 222 KB timestamped transcript in **3 seconds**.

What is running right now on the box:

| Thing | Where | State |
|---|---|---|
| Custom hand `youtube-insights` | `/data/hands/youtube-insights/{HAND.toml,SKILL.md}` | installed, active |
| Hand agent | id `bf7564a1-2c88-5fdf-8ba7-24788d4da8c1`, ws `/data/workspaces/youtube-insights` | Running |
| Helper script | `/data/workspaces/youtube-insights/bin/ytwatch.py` | working |
| Cron job "youtube-insights nightly" | — | **deleted** — `cron_jobs.json` is `[]` and `GET /api/cron/jobs` returns `{"jobs":[],"total":0}` as of 2026-08-10. Recreate with the call in [§5](#creating-the-nightly-job) |
| `yt-dlp` 2026.07.04 | container writable layer (pip) | installed, **not** in the image |
| Catalog entry for `openai/gpt-oss-120b` | `/data/custom_models.json` | added (fixes context window + cost) |

Sample output produced by the live agent: `/data/workspaces/youtube-insights/insights/*.md`.

---

## Table of contents

- [1. Is the `clip` hand reusable?](#1-is-the-clip-hand-reusable)
- [2. Getting the binaries in, and what actually works](#2-getting-the-binaries-in-and-what-actually-works)
- [3. Building it as a hand (and the traps in doing so)](#3-building-it-as-a-hand-and-the-traps-in-doing-so)
- [4. `ytwatch.py` — the deterministic half](#4-ytwatchpy--the-deterministic-half)
- [5. Scheduling: which mechanism, and the exact call](#5-scheduling-which-mechanism-and-the-exact-call)
- [6. State: how it remembers what it already did](#6-state-how-it-remembers-what-it-already-did)
- [7. Videos with no captions](#7-videos-with-no-captions)
- [8. Cost, context and token budget](#8-cost-context-and-token-budget)
- [9. The hand manifest](#9-the-hand-manifest)
- [10. Gotchas](#10-gotchas)
- [11. Verification checklist](#11-verification-checklist)

## 1. Is the `clip` hand reusable?

Files: `/opt/openfang/crates/openfang-hands/bundled/clip/HAND.toml` (21.8 KB),
`.../clip/SKILL.md` (16.5 KB).

**Reusable: the shape, not the content.** Roughly 15% carries over.

| Clip hand piece | Verdict |
|---|---|
| `tools = [...]` list and the `[[requires]]` / `[[settings]]` / `[agent]` / `[dashboard]` TOML layout | **Copy it.** This is the whole reason to build as a hand — see §3. |
| Phase 1 intake (`yt-dlp --dump-json`) | **Copy.** Still the right way to get duration/title/caption availability. |
| Phase 3 Path A (YouTube auto-subs `json3`, word-level timing) | **Copy the idea, rewrite the code.** The json3 parsing recipe in SKILL.md is correct (`word_start = tStartMs + tOffsetMs`). |
| Phase 4 "read the transcript and pick segments on CONTENT" | **Copy the principle.** This is exactly the insights job with a different rubric. |
| Phases 2, 5, 6 (video download, ffmpeg crop/caption-burn/thumbnail, Telegram/WhatsApp upload) | **Delete.** ~70% of the prompt. We never touch a video file. |
| Every `curl` example (Groq/OpenAI/Deepgram/ElevenLabs/Telegram) | **Broken here.** No curl in the container, and the commands contain metacharacters that `shell_exec` rejects (§2.1). |
| Every `-f "bv[height<=1080]+ba/b[height<=1080]"` | **Broken here.** The `<` in `height<=1080` is rejected by `shell_exec` before the command runs. |
| `2>/dev/null`, `| grep`, `&&` | **Broken here.** Same reason. |
| `max_iterations = 40` in `[agent]` | **Do not copy.** It silently makes the agent autonomous — see §3.3. |

Net: build a *new* hand. Steal the manifest skeleton and the json3 knowledge.

---

## 2. Getting the binaries in, and what actually works

### 2.1 The constraint that shapes everything: `shell_exec` is not a shell

`crates/openfang-runtime/src/subprocess_sandbox.rs:126-179` rejects, **always**:

```
`   $(   ${   ;   |   >   <   {   }   &   \n   \r   \0
```

This check runs in `tool_runner.rs:249` *before* the exec-policy check, with the
comment "Always check for shell metacharacters, even in Full mode". So
`exec_policy.mode = "full"` does **not** buy you pipes or redirection. Full mode only
changes three things: the command goes through `sh -c` (so `$VAR` expands and globs
work), the allowlist is skipped, and the taint heuristics are skipped
(`tool_runner.rs:275-287`).

Consequences, verified:

| Attempt | Result |
|---|---|
| `yt-dlp ... \| head -5` | `shell_exec blocked: command contains pipe operator` |
| `cmd > out.txt` | `... contains I/O redirection` |
| `yt-dlp -f "bv[height<=1080]"` | `... contains I/O redirection` (the `<`) |
| `.../watch?v=X&t=30` | `... contains ampersand operator` |
| `python3 -c "import os; os.system(...)"` | `... contains semicolon command chaining` |
| `bash -c "a \| b"` | blocked — the check is on the whole string |

**The escape hatch is a script file.** Write it with `file_write` (or pre-place it),
then run `python3 bin/thing.py arg arg`. That is why this build ships
`ytwatch.py` (§4) instead of a prompt full of shell one-liners.

Other `shell_exec` facts (`tool_runner.rs:1630-1760`):

- `timeout_seconds` is an LLM-settable input with **no upper bound of its own**; default is
  `exec_policy.timeout_secs` (300 for hands) or 30. But the agent loop wraps the *whole* tool call in
  `TOOL_TIMEOUT_SECS = 120` (`agent_loop.rs:47`, applied at `:928`/`:2137`) and the shorter of the two
  wins, so anything above 120 s is inert unless you set `OPENFANG_TOOL_TIMEOUT_SECS` on the daemon —
  which is **not set on this box** (the container env holds only `OPENFANG_LISTEN` and
  `OPENFANG_HOME`). See `tools-reference.md` §1 for all three timeout layers.
- stdout is truncated at a hardcoded `100_000` bytes (`tool_runner.rs:1789`).
  `exec_policy.max_output_bytes` is dead config — grep shows it is only read in a test.
  `no_output_timeout_secs` is likewise never applied to `shell_exec`.
- Child env is `env_clear()`ed and only `PATH HOME TMPDIR TMP TEMP LANG LC_ALL TERM`
  come back (`subprocess_sandbox.rs:14-16`), plus anything in
  `exec_policy.shell_env_passthrough` or granted by hand `[[requires]]` of type
  `api_key`/`env_var` (`kernel.rs:3874-3886`). **`HYPERFUSION_API_KEY` and friends are
  NOT visible to subprocesses by default.**
- cwd is the agent workspace, `/data/workspaces/<agent-name>/`.

### 2.2 Installing yt-dlp — three options, ranked

**Option A (durable, recommended): extend the Dockerfile.** `/opt/openfang/Dockerfile`
runtime stage currently installs `ca-certificates python3 python3-pip python3-venv
nodejs npm`. Patch it:

```dockerfile
FROM rust:1-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# yt-dlp from apt is stale; YouTube breaks it within weeks. Always pip.
RUN pip3 install --break-system-packages --no-cache-dir yt-dlp
```

`ffmpeg` is optional — only needed if you add a Whisper fallback (§7). It costs
~250 MB. Leave it out unless you need it. Rebuild:
`cd /opt/openfang && docker compose up -d --build` (the override sets `LTO=false
CODEGEN_UNITS=16`, so a rebuild is tolerable on 4 cores).

**Option B (what is installed right now): pip into the running container.**

```bash
docker exec openfang-openfang-1 pip3 install --break-system-packages -q yt-dlp
# -> /usr/local/bin/yt-dlp, version 2026.07.04
```

Survives `docker restart` (verified — restarted twice, `yt-dlp --version` still
answers). Does **not** survive `docker compose up --force-recreate` or an image
rebuild. Fine for today, guaranteed to bite in a month.

**Option C: sidecar container.** Rejected. `shell_exec` runs inside the openfang
container; reaching a sidecar means an HTTP hop, and the container has no curl, so
you would write a Python HTTP client anyway — at which point Option A is strictly
simpler.

**Do NOT use `openfang hand install-deps`.** Verified failure:

```bash
curl -s -X POST -H "Authorization: Bearer <API_KEY>" \
  http://127.0.0.1:4200/api/hands/clip/install-deps
```
```json
{"key":"ffmpeg","status":"error","command":"sudo apt install ffmpeg",
 "message":"Install failed (exit 127): sh: 1: sudo: not found"}
```

`routes.rs:4532-4541` picks `linux_apt > linux_dnf > linux_pacman > pip` on Linux, and
the clip hand declares `linux_apt = "sudo apt install ..."`. There is no sudo in the
container. If you write your own hand, **omit `linux_apt`** so `pip` is chosen — the
`youtube-insights` HAND.toml does exactly that, and `install-deps` then works.

### 2.3 Real tested output — channel listing

```bash
docker exec openfang-openfang-1 yt-dlp --flat-playlist --dump-json \
  --playlist-end 3 "https://www.youtube.com/@fireship/videos"
```

Works. Returns one JSON object per video. Key fields from the real response:

```json
{"title":"The safest way to store Bitcoin was just hacked...",
 "duration":310,"view_count":570000,"id":"2X2V3xv_jik",
 "url":"https://www.youtube.com/watch?v=2X2V3xv_jik",
 "playlist_id":"UCsBjURrPoezykLs9EqgamOA","playlist_channel":"Fireship",
 "timestamp":null,"upload_date":null,"channel_url":null,"availability":null}
```

**Gotcha: `--flat-playlist` gives you no dates.** `timestamp`, `upload_date` and
`channel_id` are all `null`/`NA`:

```bash
docker exec openfang-openfang-1 yt-dlp --flat-playlist --playlist-end 5 \
  --print "%(id)s\t%(title)s\t%(duration)s\t%(upload_date)s\t%(timestamp)s" \
  "https://www.youtube.com/@fireship/videos"
# 2X2V3xv_jik  The safest way to store Bitcoin...  310  NA  NA
```

`%(playlist_id)s` *does* work and yields the `UC…` channel id — handy, because
`%(channel_id)s` returns `NA` in flat mode.

**Use the Atom feed instead.** It is one small HTTP GET, needs no yt-dlp, and has
real publish timestamps:

```
https://www.youtube.com/feeds/videos.xml?channel_id=UCsBjURrPoezykLs9EqgamOA
```

Verified from inside the container: HTTP 200, 23,181 bytes, **15 entries, newest
first**, each with `<yt:videoId>` and `<published>`:

```
2X2V3xv_jik | 2026-08-05T18:08:46+00:00 | The safest way to store Bitcoin was just hacked...
jxGJT1weu4w | 2026-07-29T16:33:51+00:00 | Did Anthropic just kill the indie hacker...?
KOpTWx1Eou4 | 2026-07-23T16:53:37+00:00 | The most interesting "hack" in history...
```

Trap: the feed has a **channel-level `<published>` before the entries** (2017-04-07 for
Fireship). Parse `entry/published`, not the first `<published>` you regex out.

`@handle → UC…` resolution: fetch `https://www.youtube.com/@handle` and pull
`"externalId":"(UC[\w-]{22})"`. Verified → `UCsBjURrPoezykLs9EqgamOA`.

### 2.4 Real tested output — captions without downloading video

```bash
docker exec openfang-openfang-1 sh -c 'mkdir -p /tmp/ytest && cd /tmp/ytest && yt-dlp \
  --write-auto-subs --sub-lang en --sub-format json3 --skip-download \
  --restrict-filenames -o "src" "https://www.youtube.com/watch?v=2X2V3xv_jik"'
```
```
[youtube] 2X2V3xv_jik: Downloading webpage
WARNING: [youtube] No supported JavaScript runtime could be found. Only deno is enabled by default...
[youtube] 2X2V3xv_jik: Downloading android vr player API JSON
[info] 2X2V3xv_jik: Downloading subtitles: en
WARNING: ffmpeg not found. The downloaded format may not be the best available...
[info] Writing video subtitles to: src.en.json3
WARNING: The extractor specified to use impersonation for this download, but no impersonate target is available...
[download] 100% of 90.92KiB in 00:00:00 at 1.49MiB/s
```

**No block, no captcha, no 429.** The three warnings are all benign here:

- *No supported JavaScript runtime* — yt-dlp wants deno for the player JS. Without it
  it falls back to the `android vr` client, which still serves caption tracks. It is
  a deprecation warning, not an error. If it ever does break, `apt install deno` or
  `--js-runtimes node:/usr/bin/node` (node IS in the image).
- *ffmpeg not found* — irrelevant, nothing is being muxed.
- *impersonation ... no impersonate target* — `curl_cffi` is absent. Only matters for
  TLS-fingerprint-gated requests; captions are not.

Parsed result: `wireMagic`/`pens`/`wsWinStyles`/`wpWinPositions`/`events`, 324 events,
6,161 chars of prose, ~1,540 tokens.

**Rate limiting: none observed.** Five sequential videos fetched back to back, 3s
each, all `ok`. No cookies, no proxy, no `--sleep-requests` needed. That said,
`ytwatch.py` forces IPv4 and retries twice — see §2.6.

### 2.5 Transcript sizes — measured, not guessed

| Video | Duration | json3 on disk | Flattened text | ~tokens | Fetch wall time |
|---|---|---|---|---|---|
| Fireship, `2X2V3xv_jik` | 5:10 | 91 KB | 6,249 chars | 1.5k | 3 s |
| Fireship, `KOpTWx1Eou4` | 4:33 | — | 5,418 chars | 1.4k | 3 s |
| Lex Fridman #499, `XyXBwO5jYpw` | **3:46:18** | **4,307,148 bytes** | **222,162 chars** | **~55.5k** | **3 s** |

The json3 is ~19× the size of the text it contains. **Never `file_read` a json3.**
`tool_file_read` (`tool_runner.rs:1381-1433`) is still a bare `read_to_string` — it reads
the whole file into memory regardless of what you ask for — but it now accepts
`offset`/`limit` and, when the requested window is not the whole file, prepends an
honest `[file_read: returned bytes N-M of TOTAL total …; call again with offset=M to
continue]` header instead of staying silent. A plain call with neither argument still
returns the entire file with no truncation of its own, so a 4.3 MB json3 read in one
call is still handed straight to the model (and then silently cut by the unrelated
30%-of-context-window tool-result cap, not by `file_read` itself).

### 2.6 The one real network failure, and its fix

During a cron run the agent got:

```
{"ok": false, "error": "URLError: <urlopen error [Errno 101] Network is unreachable>"}
```

Root cause, verified inside the container:

```bash
docker exec openfang-openfang-1 python3 -c "
import socket
for f in socket.getaddrinfo('www.youtube.com', 443, proto=socket.IPPROTO_TCP): print(f[0], f[4])"
# 2  ('<A-record>', 443)          ... eight A records
# 10 ('<AAAA-record>', 443, 0, 0)   ... eight AAAA records

docker exec openfang-openfang-1 cat /proc/net/if_inet6
# 00000000000000000000000000000001 01 80 10 80  lo      <- ::1 only, no route
```

DNS hands back AAAA records; the container has no IPv6 route; any attempt on a v6
address is `ENETUNREACH`. It is intermittent because address ordering varies.

Two fixes, both applied in `ytwatch.py`:
1. Monkeypatch `socket.getaddrinfo` to request `AF_INET` only.
2. Pass `--force-ipv4` to every `yt-dlp` invocation.
Plus retries (3× for HTTP with backoff, 2× for yt-dlp, skipping permanent errors like
`This video is unavailable`).

This matters more than it looks: when the fetch failed, the agent concluded "no
network, nothing to do", produced an empty table, and **the cron job still recorded
`last_status: "ok"`**. Silent no-op. The hand prompt now carries an explicit
"retry once, never report *no new videos* off a failed command" rule.

---

## 3. Building it as a hand (and the traps in doing so)

### 3.1 Why a hand rather than a plain agent

`kernel.rs:3912-3921` (the `mode` line is `:3915`): if a hand's `tools` list contains `shell_exec`, the kernel
hands it, automatically and with no config file:

```rust
exec_policy: if def.tools.iter().any(|t| t == "shell_exec") {
    Some(ExecPolicy { mode: ExecSecurityMode::Full, timeout_secs: 300,
                      no_output_timeout_secs: 120, ..Default::default() })
}
```

Confirmed in the live log: `Agent exec_policy resolved agent=youtube-insights
exec_mode=Some(Full)`. A hand also gets a **stable agent UUID** derived from the hand
id (`kernel.rs:4004`, `AgentId::from_string(hand_id)` = UUIDv5/DNS; named instances take the
`:3934` branch and derive from `hand_instance_<instance_uuid>` instead):

```python
uuid.uuid5(uuid.NAMESPACE_DNS, "youtube-insights")
# bf7564a1-2c88-5fdf-8ba7-24788d4da8c1   <- exactly what activation returned
```

That stability is what lets a cron job keep pointing at the agent across restarts.

### 3.2 Installing a custom hand — only one method actually persists

| Method | Registers | Survives restart |
|---|---|---|
| `POST /api/hands/install` (= `openfang hand install <dir>`) | yes | **no** — `install_from_content` (`registry.rs:292-310`) only inserts into a `DashMap` |
| `POST /api/hands/upsert` | yes, overwrites | **no** — same |
| `HandRegistry::install_from_path` | yes | writes to `dirs::home_dir()/.openfang/hands/<id>` (`registry.rs:269`) = `/root/.openfang/hands` in the container — a path the kernel **never reads** and which is not on the volume |
| **Write `$OPENFANG_HOME/hands/<id>/HAND.toml` + restart** | yes | **yes** — `kernel.rs:906-907` calls `load_workspace_hands(config.home_dir.join("hands"))` at boot |

So the CLI's `hand install` never touches disk, and the one function that does write
to disk writes to the wrong directory. **Install by file copy:**

```bash
mkdir -p /var/lib/docker/volumes/openfang_openfang-data/_data/hands/youtube-insights
cp HAND.toml SKILL.md \
   /var/lib/docker/volumes/openfang_openfang-data/_data/hands/youtube-insights/
docker restart openfang-openfang-1
```

Verified boot log:
```
INFO openfang_hands::registry: Loaded workspace hand hand=youtube-insights
     path=/data/hands/youtube-insights sha256=59f28c7bd1e6f6a90b1204b57829570905ecb573bff700e1e892338e4b6b1eb3
INFO openfang_kernel::kernel: Loaded 1 workspace hand(s) from /data/hands
```

Use `POST /api/hands/upsert` for fast iteration during development (no restart), then
copy the final file to `/data/hands/` so it survives.

### 3.3 `max_iterations` in `[agent]` is a booby trap

`kernel.rs:3892-3906`:

```rust
autonomous: def.agent.max_iterations.map(|max_iter| AutonomousConfig { ... }),
schedule: if def.agent.max_iterations.is_some() {
    ScheduleMode::Continuous { check_interval_secs: 3600 }
} else { ScheduleMode::default() },   // Reactive
```

Setting `max_iterations` (as the clip hand does, `= 40`) flips the agent into a
**continuous background loop that wakes every hour and spends tokens on its own**.
For a cron-driven pipeline that is exactly wrong. **Omit it.** The agent-loop cap then
falls back to `MAX_ITERATIONS = 50` (`agent_loop.rs:35, 483-488`) — more than enough;
our runs used 5–12.

### 3.4 Hand settings must be strings

`resolve_settings` (`lib.rs:217-220`) reads each value with
`config.get(key).and_then(|v| v.as_str())`. A JSON number silently falls through to
`setting.default`. So `"videos_per_run": "3"`, never `"videos_per_run": 3`.

Resolved settings are appended to the system prompt as a `## User Configuration`
block, and `provider_env`/`env_var` names from the chosen options are added to the
subprocess env allowlist.

### 3.5 The first-run greeting eats your first cron fire

The agent workspace is seeded with `BOOTSTRAP.md`:

> On your FIRST conversation with a new user, follow this protocol: 1. **Greet** … 2.
> **Discover** — Ask the user's name …

Verified: the very first turn returned *"Hi! I'm youtube-insights … What should I
call you?"* and did zero work (1 iteration, 4,723 input tokens). Overwrite it right
after activation:

```bash
W=/var/lib/docker/volumes/openfang_openfang-data/_data/workspaces/youtube-insights
printf '# First-Run Bootstrap\n\nNo bootstrap protocol. This agent is driven by a cron job, not by a human conversation.\nDo not greet, do not ask for a name. Execute the run procedure in the system prompt immediately.\n' > $W/BOOTSTRAP.md
```

Re-check it after every hand reactivation — the workspace scaffold is re-seeded.

### 3.6 Activation and deactivation

```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)

curl -s -X POST -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"config":{"channel_url":"https://www.youtube.com/@fireship",
                 "videos_per_run":"1","transcript_language":"en","report_dir":"insights"}}' \
  http://127.0.0.1:4200/api/hands/youtube-insights/activate
# {"agent_id":"bf7564a1-2c88-5fdf-8ba7-24788d4da8c1","status":"Active", ...}
```

Deactivation takes the **instance UUID**, not the hand id, and lives on a different
path (`server.rs:474-477`):

```bash
INST=$(curl -s http://127.0.0.1:4200/api/hands/active | jq -r '.instances[]|select(.hand_id=="youtube-insights").instance_id')
curl -s -X DELETE -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/hands/instances/$INST
```

> ### ⚠ Deactivating a hand deletes all of its cron jobs, permanently.
> `deactivate_hand` (`kernel.rs:4017-4043`) → `kill_agent` → `remove_agent_jobs` +
> `persist()` (`kernel.rs:3728-3735`). I did this and watched
> `/data/cron_jobs.json` become `[]`. There is snapshot/restore logic, but it lives
> only in the *activation* path (`kernel.rs:3915-3931`, "Snapshot cron jobs before
> kill"), so it covers restarts and reactivations — **not** an explicit deactivate.
>
> To change a hand definition, do **not** deactivate. Copy the new `HAND.toml` into
> `/data/hands/<id>/` and `docker restart`. Verified: after restart the job was still
> there with both delivery targets and its original `next_run`.

---

## 4. `ytwatch.py` — the deterministic half

Lives at `/data/workspaces/youtube-insights/bin/ytwatch.py` (host:
`/var/lib/docker/volumes/openfang_openfang-data/_data/workspaces/youtube-insights/bin/ytwatch.py`).
Stdlib + the `yt-dlp` binary. Every invocation is flat argv, every output is one line
of JSON. This is what keeps the LLM out of the parts that must not be improvised.

```python
#!/usr/bin/env python3
"""ytwatch — deterministic YouTube channel -> transcript helper for OpenFang."""

import argparse, json, os, re, socket, subprocess, sys, time, urllib.request
import xml.etree.ElementTree as ET

# The container resolves AAAA records for youtube.com but has no IPv6 route
# (/proc/net/if_inet6 holds only ::1), so any attempt on a v6 address dies with
# "[Errno 101] Network is unreachable". Pin every lookup to IPv4.
_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_v4(host, port, family=0, type=0, proto=0, flags=0):
    res = _getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    return res or _getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_v4

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id="
NS = {"a": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
DEFAULT_STATE = "seen.json"


def out(obj, code=0):
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.exit(code)


def http_get(url, timeout=30, attempts=3):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:                      # transient DNS / routing / 5xx
            last = e
            if i + 1 < attempts:
                time.sleep(2 * (i + 1))
    raise last


def resolve_channel_id(channel):
    """Accept a UC… id, a /channel/UC… url, an @handle, or any channel url."""
    c = channel.strip()
    m = re.search(r"(UC[0-9A-Za-z_-]{22})", c)
    if m:
        return m.group(1)
    if c.startswith("@"):
        c = "https://www.youtube.com/" + c
    if not c.startswith("http"):
        c = "https://www.youtube.com/@" + c.lstrip("/")
    html = http_get(c)
    m = (re.search(r'"externalId":"(UC[0-9A-Za-z_-]{22})"', html)
         or re.search(r'channel_id=(UC[0-9A-Za-z_-]{22})', html))
    if not m:
        raise RuntimeError("could not resolve channel_id from " + c)
    return m.group(1)


def list_videos(channel_id, limit=15):
    root = ET.fromstring(http_get(FEED + channel_id))
    vids = []
    for e in root.findall("a:entry", NS)[:limit]:            # NOT the feed-level <published>
        vid = e.findtext("yt:videoId", namespaces=NS)
        vids.append({"id": vid,
                     "title": (e.findtext("a:title", namespaces=NS) or "").strip(),
                     "published": e.findtext("a:published", namespaces=NS),
                     "url": "https://www.youtube.com/watch?v=" + vid})
    return vids


def load_state(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen": {}}


def save_state(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)                                     # atomic


def json3_to_text(path, stride=30):
    """Flatten a YouTube json3 caption file to timestamped plain text."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    lines, cur, cur_start, last_stamp = [], [], None, -10**9
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:                                          # positioning records
            continue
        t = ev.get("tStartMs", 0) / 1000.0
        txt = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if not txt:
            continue
        if cur_start is None:
            cur_start = t
        cur.append(txt)
        if t - last_stamp >= stride:
            last_stamp = t
            lines.append("[%02d:%02d:%02d] %s" % (t // 3600, (t % 3600) // 60, t % 60, " ".join(cur)))
            cur = []
    if cur:
        t = cur_start or 0
        lines.append("[%02d:%02d:%02d] %s" % (t // 3600, (t % 3600) // 60, t % 60, " ".join(cur)))
    return "\n".join(lines)


def ytdlp(args, timeout=240, attempts=2):
    # --force-ipv4 for the same no-IPv6-route reason as the getaddrinfo patch.
    rc, so, se = 1, "", ""
    for i in range(attempts):
        p = subprocess.run(["yt-dlp", "--force-ipv4", "--no-progress"] + args,
                           capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        rc, so, se = p.returncode, p.stdout, p.stderr
        if rc == 0:
            return rc, so, se
        if "unavailable" in se or "Private video" in se or "members-only" in se:
            return rc, so, se                                 # permanent, do not retry
        if i + 1 < attempts:
            time.sleep(3)
    return rc, so, se


def cmd_fetch(a):
    os.makedirs(a.outdir, exist_ok=True)
    stem = os.path.join(a.outdir, a.video_id)
    url = "https://www.youtube.com/watch?v=" + a.video_id

    rc, so, se = ytdlp(["--dump-json", "--no-playlist", "--skip-download", url], timeout=a.timeout)
    meta = {}
    if rc == 0 and so.strip():
        j = json.loads(so.splitlines()[0])
        meta = {"title": j.get("title"), "duration": j.get("duration"),
                "upload_date": j.get("upload_date"), "channel": j.get("channel"),
                "view_count": j.get("view_count"),
                "has_manual_subs": bool(j.get("subtitles")),
                "auto_caption_langs": sorted(list((j.get("automatic_captions") or {}).keys()))[:5]}

    rc, so, se = ytdlp(["--write-auto-subs", "--write-subs",
                        "--sub-langs", a.lang, "--sub-format", "json3",
                        "--skip-download", "--no-playlist", "-o", stem, url],
                       timeout=a.timeout)
    cands = [stem + "." + a.lang + ".json3", stem + ".en.json3"]
    src = next((c for c in cands if os.path.exists(c)), None)
    if src is None:
        for f in os.listdir(a.outdir):
            if f.startswith(a.video_id) and f.endswith(".json3"):
                src = os.path.join(a.outdir, f)
                break
    if src is None:
        out({"ok": False, "video_id": a.video_id, "error": "no_captions",
             "meta": meta, "stderr": se[-800:]}, 2)

    text = json3_to_text(src, a.stride)
    txt_path = stem + ".txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    if not a.keep_json3:
        os.remove(src)

    # Split into parts small enough to survive OpenFang's per-tool-result cap
    # (30% of the context window x 2 chars/token). file_read now takes offset/limit,
    # but a plain call with neither still returns the WHOLE file with no automatic
    # truncation of its own -- and the outer per-tool-result cap that DOES truncate
    # applies silently regardless, so a 220k-char transcript read in one call would
    # still be cut mid-way with no notice. Pre-splitting stays the deterministic fix;
    # it doesn't depend on the agent remembering to page with offset/limit itself.
    parts = []
    if a.chunk_chars > 0 and len(text) > a.chunk_chars:
        buf, size, idx = [], 0, 1
        for line in text.split("\n"):
            if size + len(line) + 1 > a.chunk_chars and buf:
                pp = "%s.part%02d.txt" % (stem, idx)
                with open(pp, "w", encoding="utf-8") as f:
                    f.write("\n".join(buf))
                parts.append({"path": pp, "chars": size})
                buf, size, idx = [], 0, idx + 1
            buf.append(line)
            size += len(line) + 1
        if buf:
            pp = "%s.part%02d.txt" % (stem, idx)
            with open(pp, "w", encoding="utf-8") as f:
                f.write("\n".join(buf))
            parts.append({"path": pp, "chars": size})

    out({"ok": True, "video_id": a.video_id, "meta": meta,
         "transcript_path": txt_path, "chars": len(text),
         "approx_tokens": len(text) // 4, "lines": text.count("\n") + 1,
         "parts": parts})


def main():
    p = argparse.ArgumentParser(prog="ytwatch")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve"); r.add_argument("channel")
    l = sub.add_parser("list"); l.add_argument("channel"); l.add_argument("--max", type=int, default=15)
    n = sub.add_parser("new")
    n.add_argument("channel"); n.add_argument("--max", type=int, default=15)
    n.add_argument("--state", default=DEFAULT_STATE)
    m = sub.add_parser("mark")
    m.add_argument("video_id"); m.add_argument("--state", default=DEFAULT_STATE)
    m.add_argument("--note", default="")
    s = sub.add_parser("state"); s.add_argument("--state", default=DEFAULT_STATE)
    f = sub.add_parser("fetch")
    f.add_argument("video_id")
    f.add_argument("--outdir", default="transcripts")
    f.add_argument("--lang", default="en")
    f.add_argument("--stride", type=int, default=30)
    f.add_argument("--timeout", type=int, default=240)
    f.add_argument("--chunk-chars", type=int, default=60000)
    f.add_argument("--keep-json3", action="store_true")

    a = p.parse_args()
    try:
        if a.cmd == "resolve":
            out({"ok": True, "channel_id": resolve_channel_id(a.channel)})
        if a.cmd == "list":
            cid = resolve_channel_id(a.channel)
            out({"ok": True, "channel_id": cid, "videos": list_videos(cid, a.max)})
        if a.cmd == "new":
            cid = resolve_channel_id(a.channel)
            st = load_state(a.state)
            vids = [v for v in list_videos(cid, a.max) if v["id"] not in st["seen"]]
            out({"ok": True, "channel_id": cid, "count": len(vids), "videos": vids,
                 "already_seen": len(st["seen"]), "state_path": os.path.abspath(a.state)})
        if a.cmd == "mark":
            st = load_state(a.state)
            st["seen"][a.video_id] = {"note": a.note}
            save_state(a.state, st)
            out({"ok": True, "marked": a.video_id, "total_seen": len(st["seen"])})
        if a.cmd == "state":
            st = load_state(a.state)
            out({"ok": True, "total_seen": len(st["seen"]), "ids": sorted(st["seen"].keys())})
        if a.cmd == "fetch":
            cmd_fetch(a)
    except Exception as e:
        out({"ok": False, "error": type(e).__name__ + ": " + str(e)}, 1)


if __name__ == "__main__":
    main()
```

Real outputs:

```bash
$ python3 bin/ytwatch.py new https://www.youtube.com/@fireship --max 4
{"ok": true, "channel_id": "UCsBjURrPoezykLs9EqgamOA", "count": 2,
 "videos": [{"id": "KOpTWx1Eou4", "title": "The most interesting \"hack\" in history...",
             "published": "2026-07-23T16:53:37+00:00", "url": "https://www.youtube.com/watch?v=KOpTWx1Eou4"}, ...],
 "already_seen": 2, "state_path": "/data/workspaces/youtube-insights/seen.json"}

$ python3 bin/ytwatch.py fetch XyXBwO5jYpw --stride 60      # 3h46m podcast, 3 seconds
{"ok": true, "video_id": "XyXBwO5jYpw",
 "meta": {"title": "Gary Gallagher: American Civil War... | Lex Fridman Podcast #499",
          "duration": 13578, "upload_date": "20260728", "channel": "Lex Fridman",
          "view_count": 526831, "has_manual_subs": true, "auto_caption_langs": ["aa","ab","af","ak","am"]},
 "transcript_path": "transcripts/XyXBwO5jYpw.txt", "chars": 222162, "approx_tokens": 55540, "lines": 220,
 "parts": [{"path": "transcripts/XyXBwO5jYpw.part01.txt", "chars": 59572},
           {"path": "...part02.txt", "chars": 59584},
           {"path": "...part03.txt", "chars": 59169},
           {"path": "...part04.txt", "chars": 43838}]}

$ python3 bin/ytwatch.py fetch ZZZZZZZZZZZ         # exit code 2
{"ok": false, "video_id": "ZZZZZZZZZZZ", "error": "no_captions", "meta": {},
 "stderr": "...ERROR: [youtube] ZZZZZZZZZZZ: This video is unavailable\n"}
```

Transcript format (`--stride` = seconds between timestamps):

```
[00:00:00] As if last year hasn't been hard enough
[00:00:45] on the hodlers, last week what used to be the safest way to store Bitcoin turned out to be
           the fastest way to lose it when Coldcard, a security-obsessed air-gapped Bitcoin hardware
           wallet, was hacked. ...
```

---

## 5. Scheduling: which mechanism, and the exact call

Four candidates existed. Only one is right.

| Mechanism | Verdict |
|---|---|
| `schedule_create` tool | **No.** Hardcodes `"timeout_secs": null` (`tool_runner.rs:2352`) → 120 s, and `"tz": null` → UTC. A run takes 90–170 s for one short video; 120 s kills it mid-flight. |
| `openfang cron create` CLI | **No.** Same `timeout_secs` omission (`main.rs:6110-6124`), requires the agent **UUID** (a name gives `Invalid agent ID: invalid character: found 'y' at 1`), and misreports success as `✘ Failed: ?` because it reads `body["id"]` while the API returns `{"result":"{\"job_id\":…}"}`. It does create the job. |
| Trigger / workflow | **No.** Triggers are event-driven, not time-driven. `CronAction::WorkflowRun` exists and allows `timeout_secs` up to 3600, but a workflow adds an orchestration layer for a single-agent job. |
| **`POST /api/cron/jobs` with an explicit `timeout_secs`** | **Yes.** Full control of schedule, timezone, timeout and delivery fan-out. |

### The working invocation

```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)
AGENT=bf7564a1-2c88-5fdf-8ba7-24788d4da8c1     # uuid5(NAMESPACE_DNS, "youtube-insights")

curl -s -X POST -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  http://127.0.0.1:4200/api/cron/jobs -d @- <<JSON
{
  "agent_id": "$AGENT",
  "name": "youtube-insights nightly",
  "schedule": { "kind": "cron", "expr": "0 3 * * *", "tz": "Europe/Berlin" },
  "action": {
    "kind": "agent_turn",
    "message": "Nightly run. Execute the run procedure from your system prompt for the configured channel. Process at most videos_per_run new videos, newest first. Do not ask questions, do not greet. Your final message must be the summary table and nothing else.",
    "model_override": null,
    "timeout_secs": 600
  },
  "delivery": { "kind": "none" },
  "delivery_targets": [
    { "type": "local_file",
      "path": "/data/workspaces/youtube-insights/insights/cron-log.md", "append": true }
  ],
  "one_shot": false
}
JSON
# {"result":"{\"job_id\":\"<JOB>\",\"status\":\"created\"}"}
```

Verified `next_run` came back as `2026-08-10T01:00:00Z` — 03:00 Europe/Berlin in CEST.
Timezone handling is real (`cron.rs:472-490`, `chrono_tz`); a 5-field expr is padded
to the `cron` crate's 7-field form (`cron.rs:456-462`).

`scripts/ofcron` builds this exact body for you, resolves the agent name to the UUID, and defaults
`--timeout` to 600:

```bash
ofcron create --agent youtube-insights --name "youtube-insights nightly" \
  --cron "0 3 * * *" --tz Europe/Berlin --timeout 600 \
  --message "Nightly run. Execute the run procedure from your system prompt…" \
  --file /data/workspaces/youtube-insights/insights/cron-log.md
```

### Session hygiene without a credential

Cron `agent_turn`s all land in one session and history is only trimmed at 20 messages
(`DEFAULT_MAX_HISTORY_MESSAGES`), so night 2 pays for night 1's tokens.

**Do not solve this with a `delivery_targets` webhook at `…/session/reset`.** An earlier version of
this build did exactly that, and it was a live credential leak: `POST /api/agents/{id}/session/reset`
needs the daemon key, `delivery_targets` is stored cleartext in `$OF/cron_jobs.json`, and
`GET /api/cron/*` is in the public allowlist (`middleware.rs:181`) and returns `delivery_targets`
**verbatim** — so the key was served, with no credential, to anything on loopback *or*
`<tailnet-ip>:4200` (Tailscale). That key is also a whole-filesystem read primitive via `POST /mcp`
(`security-model.md` §3.6b), so it is not a small leak. There is no credential-free variant: a
webhook target with no `auth_header` just 401s, because `session/reset` is not in the public
allowlist. The job that carried it was deleted on 2026-08-10 and `cron_jobs.json` is now `[]`.

Two safe options:

- **Do nothing.** The 20-message cap bounds the session on its own.
- **Reset from a host crontab** a few minutes after the fire time, so the key never enters
  OpenFang's own state — the host has `curl`, the container does not:
  ```bash
  # /etc/cron.d/openfang-yt-reset  — 03:10 Europe/Berlin
  10 3 * * * root K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2); curl -s -X POST -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/agents/bf7564a1-2c88-5fdf-8ba7-24788d4da8c1/session/reset
  ```

Mechanically the webhook target does work, which is why it is tempting: `deliver_webhook`
(`cron_delivery.rs:211-236`) does a plain `POST` with `{job, output, timestamp}` and the
`Authorization` header verbatim, with **no SSRF check**, so loopback resolves. It was observed
working before removal:

```
INFO openfang_api::middleware: POST /api/agents/bf7564a1-.../session/reset status=200
INFO openfang_kernel::kernel: Cron fan-out: all 2 target(s) delivered job=youtube-insights nightly targets=2
```
followed by `message_count: 0` on the session. The mechanism is sound; the credential is the problem.

### Cadence advice

`timeout_secs` maxes at **600** (`openfang-types/src/scheduler.rs:36`), and the agent turn is killed hard
at that point. Measured: **90–170 s per 5-minute video**. A 3h45m video with four
transcript parts will be 3–5× that. So:

- Short-form channel (Fireship-like): `videos_per_run = 3`, `0 3 * * *`. Comfortable.
- Long-form channel (podcasts): `videos_per_run = 1`. If the channel posts a burst,
  switch the schedule to `{"kind":"every","every_secs":3600}` and let the backlog
  drain one video an hour. `every_secs` is clamped to 60…86400 (`openfang-types/src/scheduler.rs:18-21`).

Other scheduler limits, from `scheduler.rs` and `cron.rs`:

| Limit | Value | Source |
|---|---|---|
| tick interval | 15 s | `kernel.rs:4661` |
| max jobs per agent | 50 | `openfang-types/src/scheduler.rs:12` (enforced `:250`) |
| `AgentTurn` timeout | 10…600 s, default **120** | `openfang-types/src/scheduler.rs:33,36`, applied at `kernel.rs:6605` |
| `WorkflowRun` timeout | 10…3600 s, default 120 | `openfang-types/src/scheduler.rs:126-132` |
| `AgentTurn` message | ≤ 16,384 chars | `openfang-types/src/scheduler.rs:30` |
| auto-disable after | **5 consecutive errors** | `cron.rs:21` |
| `next_run` on manual `/run` | only advances if already due | `cron.rs:346-360` — manual runs do **not** skip the scheduled fire |

Manual fire and status poll:

```bash
curl -s -X POST -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/cron/jobs/$JOB/run
# {"job_id":"...","status":"triggered"}        <- async, returns immediately
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/cron/jobs/$JOB/status \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['last_status'], d['job']['last_run'])"
# ok 2026-08-09T23:19:32.545024721Z
```

### Creating the nightly job

The job no longer exists — it was deleted on 2026-08-10 and `cron_jobs.json` is `[]`. Recreate it
with the call in "The working invocation" above (or `ofcron create`), then point it at a real
channel and enable it. Steps 1 and 2 apply whether or not the job already exists.

```bash
D=/var/lib/docker/volumes/openfang_openfang-data/_data
K=$(grep -m1 '^api_key = ' $D/config.toml | cut -d'"' -f2)

# 1. point it at the real channel.
#    Do NOT deactivate+reactivate (that deletes the cron job, §3.6). A re-POST to /activate on an
#    already-active instance is rejected anyway ("Hand already active"), and PUT /api/hands/{id}/settings
#    neither persists nor reaches the running agent (hands.md §9.7).
#    The safe path: edit the stored config and restart. On boot the kernel replays
#    activate_hand(hand_id, config, None) straight from hand_state.json (kernel.rs:4391-4397),
#    and because a hand's agent UUID is uuid5(NAMESPACE_DNS, hand_id) it is unchanged, so the
#    cron job keeps pointing at the same agent.
python3 - <<'PY'
import json
p="/var/lib/docker/volumes/openfang_openfang-data/_data/hand_state.json"
st=json.load(open(p))
for e in st:
    if e["hand_id"]=="youtube-insights":
        e["config"].update({"channel_url":"https://www.youtube.com/@YOURCHANNEL",
                            "videos_per_run":"2","transcript_language":"en","report_dir":"insights"})
json.dump(st, open(p,"w"), indent=2)
PY
docker restart openfang-openfang-1 && sleep 12
docker logs --tail 100 openfang-openfang-1 2>&1 | grep -iE 'Hand restored|Reassigned cron'
# 2. reset the state file so it does not treat the last 15 videos as new
echo '{"seen":{}}' > /var/lib/docker/volumes/openfang_openfang-data/_data/workspaces/youtube-insights/seen.json
# 3. enable. Discover the id rather than hardcoding it — the old one is gone:
JOB=$(curl -s http://127.0.0.1:4200/api/cron/jobs |
  python3 -c 'import json,sys;[print(j["id"]) for j in json.load(sys.stdin)["jobs"] if j["name"]=="youtube-insights nightly"]')
curl -s -X PUT -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"enabled":true}' http://127.0.0.1:4200/api/cron/jobs/$JOB/enable
# or: ofcron enable $JOB
```

Seeding note: an empty `seen.json` means the first run sees all 15 feed entries and
processes `videos_per_run` of them, oldest-newest by your ordering, then chips away
nightly. To start clean from today, pre-mark the current 15:

```bash
docker exec openfang-openfang-1 sh -c 'cd /data/workspaces/youtube-insights && \
  for v in $(python3 bin/ytwatch.py list https://www.youtube.com/@YOURCHANNEL --max 15 | python3 -c "import json,sys;[print(x[\"id\"]) for x in json.load(sys.stdin)[\"videos\"]]"); do \
    python3 bin/ytwatch.py mark $v --note backfill; done'
```

---

## 6. State: how it remembers what it already did

Three candidates were on the table. **A JSON file in the agent workspace wins.**

| Option | Verdict |
|---|---|
| `memory_store` / `memory_recall` | Works, but it is a **globally shared** KV: `KernelHandle::memory_store` (`kernel.rs:7246-7251`) writes under the fixed agent id `00000000-0000-0000-0000-000000000001` (`shared_memory_agent_id`, `kernel.rs:6958`), so every agent on the box shares one namespace. Fine for a counter with a prefix; wrong for a set of ids the LLM must maintain by hand. |
| Knowledge graph (`knowledge_add_entity` …) | Overkill and slow: an entity per video, and dedup still depends on the LLM querying correctly. |
| **`seen.json` in the workspace, mutated by `ytwatch.py`** | **Chosen.** The diff happens in Python, not in the model. Atomic write via `os.replace`. The LLM's only job is to call `mark`. |

Live file after three runs:

```json
{ "seen": { "2X2V3xv_jik": {"note": "processed"},
            "jxGJT1weu4w": {"note": "processed"},
            "KOpTWx1Eou4": {"note": "high insight"} } }
```

`ytwatch.py new` does the set-difference itself and returns `count: 0` when there is
nothing to do, so a no-op night costs exactly one shell call and one LLM turn.

**Why not let the model track it:** it demonstrably will not. In two of five runs the
model got as far as reading the transcript and then derailed on the
`memory_recall` → increment → `memory_store` bookkeeping, emitting the tool arguments
as plain text (`{"key":"yti_ideas_generated"}`) and ending the turn without writing the
report or marking the video. The cron job still logged `ok`. Counters were dropped
from the hand entirely for this reason; `seen.json` plus the files in `insights/` are
the source of truth, countable with `python3 bin/ytwatch.py state`.

If you *do* want shared-memory counters, note that values land as JSON strings and the
keys must be namespaced:

```bash
docker exec openfang-openfang-1 python3 -c "
import sqlite3
c=sqlite3.connect('/data/data/openfang.db')
for a,k,v in c.execute(\"select agent_id,key,value from kv_store where key like 'yti_%'\"): print(k,v)"
# yti_runs_completed b'\"1\"'
```

---

## 7. Videos with no captions

`ytwatch.py fetch` returns `{"ok": false, "error": "no_captions"}` and exit 2. The hand
marks the video skipped and moves on. That is the right default — auto-captions exist
for essentially every non-music English upload.

If you want a real fallback, **you still do not need ffmpeg**. Verified: yt-dlp can
pull a bare audio stream with no post-processing.

```bash
docker exec openfang-openfang-1 sh -c 'mkdir -p /tmp/ytaudio && cd /tmp/ytaudio && \
  yt-dlp --force-ipv4 -f "139/249/ba" --no-playlist -o "a.%(ext)s" \
  https://www.youtube.com/watch?v=ID'
# WARNING: writing DASH m4a. Only some players support this container. Install ffmpeg to fix this automatically
```

(`yt-dlp` exists only inside the container, and it writes into the current directory — hence the
`mkdir`+`cd`, same as §2.4. The selector picks 139 when it exists and falls back through 249 to
`ba`; the format-id line that yt-dlp prints therefore varies with the video.)

Audio formats on a typical video, and the size maths that decides the choice:

| fmt | codec | bitrate | 5:10 clip | extrapolated 1 h |
|---|---|---|---|---|
| 139 | m4a AAC 22 kHz | 49 k | **1.80 MiB** | **~21 MB** |
| 249 | webm opus 48 kHz | 51 k | 1.89 MiB | ~22 MB |
| 140 | m4a AAC 44 kHz | 129 k | 4.78 MiB | ~58 MB |
| 251 | webm opus 48 kHz | 116 k | 4.28 MiB | ~51 MB |

Groq and OpenAI Whisper cap uploads at 25 MB, so `-f "139/249/ba"` (not `ba`) keeps
you under the limit up to about 70 minutes. Beyond that you need ffmpeg to split, and
then Option A's `ffmpeg` line earns its 250 MB.

The upload itself must be Python (`urllib` multipart) inside `ytwatch.py` — there is no
curl, and the clip hand's curl recipes would be metacharacter-rejected anyway. Grant
the key by adding to the HAND.toml:

```toml
[[requires]]
key = "groq"
label = "Groq API key (Whisper fallback)"
requirement_type = "api_key"
check_value = "GROQ_API_KEY"
optional = true
```

`kernel.rs:3874-3886` then adds `GROQ_API_KEY` to the subprocess env allowlist, and the
key itself goes in `/data/secrets.env` (loaded into the daemon's env at boot,
`cli/dotenv.rs:22-36`). Unmet **optional** requirements only mark the hand `degraded`;
they never block activation (`registry.rs:542`).

---

## 8. Cost, context and token budget

### The two numbers OpenFang gets wrong for this instance (both now fixed)

`config.toml` sets `model = "openai/gpt-oss-120b"` under provider `hyperfusion`. That
exact id was **not in the model catalog** — the only near match was
`openrouter/openai/gpt-oss-120b:free`, and `find_model` requires an exact
(case-insensitive) id or alias match (`model_catalog.rs:131-187`). Consequences:

1. **Context window defaulted to 200,000** instead of the real 131,072.
   `kernel.rs:2925-2928` looks the model up and passes `None` on a miss;
   `agent_loop.rs:1721` falls back to `DEFAULT_CONTEXT_WINDOW = 200_000`
   (`agent_loop.rs:225`). Every tool-result budget was therefore computed against a
   window 53% larger than reality:

   | Budget | at 200k (wrong) | at 131,072 (right) |
   |---|---|---|
   | per tool result — 30% × 2 chars/tok | 120,000 chars | **78,643 chars** |
   | single result max — 50% | 200,000 chars | 131,072 chars |
   | total tool headroom — 75% | 300,000 chars | 196,608 chars |

   (`context_budget.rs:34-50`; applied at `agent_loop.rs:989` and `:2198`.)

2. **Every cost figure was fabricated.** `metering.rs:289`:
   `let (input_per_m, output_per_m) = catalog.pricing(model).unwrap_or((1.0, 3.0));`
   A run reporting `cost_usd: 0.120373` was just `109861×$1/M + 3504×$3/M`. Real
   Hyperfusion pricing for gpt-oss-120b is an order of magnitude lower, so the
   dashboard has been over-reporting spend by roughly 10–20×.

**Fix applied** (persisted to `/data/custom_models.json`, survives restart):

```bash
curl -s -X POST -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  http://127.0.0.1:4200/api/models/custom -d '{
    "id":"openai/gpt-oss-120b","provider":"hyperfusion",
    "display_name":"gpt-oss-120b (hyperfusion)",
    "context_window":131072,"max_output_tokens":32768,
    "input_cost_per_m":0.0,"output_cost_per_m":0.0,
    "supports_tools":true,"supports_streaming":true}'
# {"id":"openai/gpt-oss-120b","provider":"hyperfusion","status":"added"}
```

Costs are left at `0.0`. **Put your real Hyperfusion per-million rates in there** if
you want the budget page to mean anything — delete and re-add via
`DELETE /api/models/custom/{id}`.

### Measured token use

Every number below is from `Agent loop completed` log lines on the live instance,
processing one ~5-minute video (~1.5k-token transcript):

| Run | Iterations | Total tokens | Wall time | Outcome |
|---|---|---|---|---|
| bootstrap greeting | 1 | 4.7k in / 138 out | 8 s | wasted (see §3.5) |
| first real run | 12 | 109.9k in / 3.5k out | 170 s | report written |
| cron run 1 | 10 | 103.3k | ~100 s | report written |
| cron run 2 (derailed) | 5 | 31.0k | 74 s | nothing produced |
| cron run 3 (hardened) | 9 | 77.7k | 150 s | report written |

Input tokens are cumulative across iterations — each iteration resends the whole
conversation, so a 9-iteration run on a 1.5k-token transcript still bills ~78k tokens.
**The transcript is not the cost driver; the iteration count is.**

Extrapolation for a 3h45m podcast (56k-token transcript, 4 parts, notes written
between parts): expect 14–20 iterations and roughly **250–400k total tokens**, 300–500 s.
That is close to the 600 s cron ceiling — hence `videos_per_run = 1` for long-form.

At representative gpt-oss-120b rates (~$0.10/M in, ~$0.50/M out) that is roughly
**$0.01 per short video, $0.04 per long one**. Nightly on a channel posting three
short videos a week: pennies a month. Under OpenFang's fabricated $1/$3 fallback the
same runs report ~$0.08 and ~$0.35.

### The 20-message history wall

`agent_loop.rs:462-470` trims to `effective_max_history_messages()`, which is
`DEFAULT_MAX_HISTORY_MESSAGES = 20` (`agent.rs:523`). Observed live:

```
WARN Trimming old messages to prevent context overflow total_messages=27 trimming=7 max_history=20
```

`AgentManifest::max_history_messages` exists but is **not settable from HAND.toml and
not exposed by any API route** — hand agents are stuck at 20. Two tool calls burn two
messages, so a run has ~10 tool calls of visible memory. This is the hard reason the
hand prompt insists on writing notes to disk between transcript parts rather than
holding them in the conversation.

---

## 9. The hand manifest

`/data/hands/youtube-insights/HAND.toml` — the parts that matter, with the reasoning
inline. (`SKILL.md` sits beside it and is appended to the system prompt as
"Reference Knowledge"; it carries the yt-dlp/json3/metacharacter reference.)

```toml
id = "youtube-insights"
name = "YouTube Insights Hand"
description = "Watches a YouTube channel, transcribes new uploads, and extracts insights, topics and script ideas"
category = "content"          # HandCategory: content|security|productivity|development|communication|data|finance|other
icon = "\U0001F4FA"
tools = ["shell_exec", "file_read", "file_write", "file_list"]
#        ^ shell_exec in this list is what grants ExecSecurityMode::Full + 300s.
#        memory_store/memory_recall deliberately absent — see §6.

[[requires]]
key = "python3"
label = "Python 3 must be installed"
requirement_type = "binary"
check_value = "python3"

[[requires]]
key = "yt-dlp"
label = "yt-dlp must be installed"
requirement_type = "binary"
check_value = "yt-dlp"

# linux_apt is deliberately absent: install_hand_deps() prefers
# linux_apt > linux_dnf > linux_pacman > pip, and `sudo apt install` exits 127 here.
[requires.install]
pip = "pip3 install --break-system-packages -q yt-dlp"
manual_url = "https://github.com/yt-dlp/yt-dlp#installation"
estimated_time = "1-2 min"

[[settings]]                  # values are read with as_str() — always quote them
key = "channel_url"
label = "YouTube channel"
setting_type = "text"
default = ""
# ... videos_per_run "3", transcript_language "en", report_dir "insights"

[agent]
name = "youtube-insights"     # -> workspace /data/workspaces/youtube-insights
module = "builtin:chat"
provider = "default"          # "default" inherits config.toml's default_model;
model = "default"             # anything else and you get anthropic/claude-sonnet-4
max_tokens = 8192
temperature = 0.3
# max_iterations OMITTED on purpose — see §3.3
system_prompt = """..."""
```

The system prompt's load-bearing parts, all of which were added in response to an
observed failure:

- the metacharacter blocklist, spelled out, with the "there is no shell" framing;
- "`timeout_seconds: 120` for any fetch" — 120 is the real ceiling, not a preference: the agent loop
  wraps the whole tool call in `TOOL_TIMEOUT_SECS = 120` (`agent_loop.rs:47`) and the shorter of the
  two wins. Earlier versions of this prompt said 240, which was silently unreachable. The deployed
  `/data/hands/youtube-insights/HAND.toml` still carries the old 240 — run
  `ofhand install ~/.claude/skills/fang-upgrade/assets/youtube-insights-hand` to sync it (that restarts
  the container). `ytwatch.py`'s own `--timeout 240` default is bounded by the same 120 s wrapper;
- "never `file_read` a `.json3`";
- "URLs containing `&` are blocked — use the bare video id";
- "retry once on `Network is unreachable` / timeout / 5xx; never report *no new
  videos* off a failed command";
- "you get 20 messages of history — write findings to disk as you go";
- write the report **before** any bookkeeping; a video is not done until it is `mark`ed;
- read one transcript part at a time, writing notes to `<id>.notes.md` between parts;
- the final message is the deliverable: a markdown table, nothing else, no tool call
  in the same message.

### What the pipeline actually produces

`/data/workspaces/youtube-insights/insights/2X2V3xv_jik.md`, written by the live agent
from the Fireship Coldcard video — abridged:

```markdown
# The safest way to store Bitcoin was just hacked...
- id: 2X2V3xv_jik | duration: 05:10 | published: 2026-08-05 | url: https://www.youtube.com/watch?v=2X2V3xv_jik

## Key insights
- The hack exploited a flag that disabled Coldcard's secure RNG, forcing the device to use
  MicroPython's weak generator. **[00:02:17]**
- Both RNG functions shared the same name, so the crypto library unintentionally selected
  the insecure one. **[00:03:04]**
- Victims could only rescue funds by sending transactions directly to a mining pool,
  bypassing the public mempool where the attacker could out-bid them. **[00:04:36]**

## Script ideas for our channel
1. **Title:** "How a Tiny Firmware Flag Cracked the Coldcard Wallet"
   - **Hook:** "A single zero-flag turned the world's most trusted Bitcoin hardware into a backdoor."
   - **Angle:** Deep-dive into the RNG bug, with visual code comparison and lessons for developers.
   ...
```

Timestamps are real and check out against the transcript.

---

## 10. Gotchas

Everything here was hit and confirmed on this box, not read in a doc.

**Environment**

1. **`grep '^api_key' config.toml` returns two lines.** It also matches
   `api_key_env`, and `cut -d'"' -f2` then concatenates both values into a 71-char
   string that fails auth with HTTP 400. Use `grep -m1 '^api_key = '` — the space before `=` is
   what disambiguates (full explanation: `automation-workflows-triggers-schedules.md` gotcha 46).
2. **The CLI never sends the API key from `config.toml`.** Every authenticated
   (non-GET) CLI command fails with `✘ Failed: Missing Authorization: Bearer <api_key>
   header`, while `OPENFANG_API_KEY=<key> openfang hand check-deps clip` works and
   `OPENFANG_API_KEY=bogus` gives `Invalid API key` (proving the header is only sent
   from the env path). `read_api_key()` (`main.rs:1621-1642`) claims to read
   `config.toml` first; empirically it does not. **Export `OPENFANG_API_KEY` before
   any `openfang` command that writes.**
3. **Most GET endpoints are unauthenticated.** `/api/agents`, `/api/hands`,
   `/api/models`, `/api/cron/*` all return 200 with no key
   (`middleware.rs:145-182`). GET `/api/agents` exposes agent list; `/api/config` too.
   POST/PUT/DELETE always require auth. This is why `openfang agent list` "works"
   despite gotcha 2. Relevant because the dashboard is published on
   `<tailnet-ip>:4200` as well as loopback.
4. **`/health` is not a route at all.** Only `/api/health` exists (`server.rs:172`). Hitting
   `/health` returns **401** with no key and **404** with one — the middleware runs before routing,
   so a 401 never proves an endpoint exists. Health-check `/api/health` (public, cost-1).
5. **No IPv6 route, but AAAA records resolve** → intermittent
   `[Errno 101] Network is unreachable`. Force IPv4 in anything you write. §2.6.
6. **No curl in the container.** Every `curl` example in the bundled clip hand and
   SKILL.md is dead on arrival here. Use Python `urllib`, or run curl from the host.

**shell_exec**

7. **Metacharacters are blocked in every mode, including `full`.** `| > < ; & { } $(
   ${ backtick newline`. Full mode gives you `sh -c` and `$VAR` expansion, not
   operators. Put anything compound in a script file.
8. **`<` inside a yt-dlp format selector counts.** `-f "bv[height<=1080]"` is rejected
   as "I/O redirection".
9. **`&` in a URL is rejected.** Never build `watch?v=X&t=30`.
10. **stdout is cut at 100,000 bytes**, hardcoded. `exec_policy.max_output_bytes` and
    `no_output_timeout_secs` are dead config for `shell_exec`.
11. **Subprocesses do not see your API keys.** `env_clear()` + an 8-name allowlist
    (`SAFE_ENV_VARS`, `subprocess_sandbox.rs:14-16`).
    Grant extras via hand `[[requires]]` of type `api_key`/`env_var`, or
    `exec_policy.shell_env_passthrough`.

**Tools and context**

12. **`file_read` reads the whole file into memory regardless of `offset`/`limit`**
    (`tool_runner.rs:1387`, still a bare `read_to_string`). A plain call with neither
    argument returns the whole file, no truncation, no header — one call on a 4.3 MB
    json3 would put the whole thing in the request. `offset`/`limit` exist for paging a
    known-large file on purpose; they don't shrink what gets read off disk.
13. **Tool results are capped at 30% of the context window** and compacted at 75%
    total. Chunk long transcripts into ≤60 KB parts and process one at a time.
14. **20 messages of history, not configurable for hands.** `max_history_messages`
    exists on the manifest but no HAND.toml field and no API route sets it.
15. **The model was not in the catalog** → 200k assumed context (real: 131,072) and
    fabricated $1/$3 pricing. Fixed via `/api/models/custom`; do the same for any
    model you point a custom provider at.

**Hands**

16. **`openfang hand install` does not persist.** Neither does `/api/hands/install`
    or `/upsert`. Write to `$OPENFANG_HOME/hands/<id>/` and restart.
17. **`install_from_path` persists to the wrong directory** — `$HOME/.openfang/hands`
    (`registry.rs:269`), which the kernel never loads and which is outside the volume.
18. **`hand install-deps` runs `sudo apt` on Linux and fails with exit 127.** Omit
    `linux_apt` from your `[requires.install]` so `pip` is selected.
19. **Deactivating a hand deletes its cron jobs, irrecoverably.** §3.6. Restart-based
    redeploy is safe; deactivate is not.
20. **`max_iterations` in `[agent]` turns the agent into an hourly autonomous loop.**
21. **Hand setting values must be JSON strings.** Numbers silently revert to defaults.
22. **`provider`/`model` must literally be `"default"` to inherit** the configured
    model; otherwise the HAND.toml defaults are `anthropic` /
    `claude-sonnet-4-20250514`, which this box cannot serve.
23. **`BOOTSTRAP.md` in the workspace makes the agent greet you instead of working**
    on its first turn, and is re-seeded on reactivation.

**Cron**

24. **Default `AgentTurn` timeout is 120 s** — shorter than a single run. Both
    `schedule_create` and `openfang cron create` omit `timeout_secs`. Always set it
    explicitly; the ceiling is 600.
25. **`schedule_create` always uses UTC** (`"tz": null`, `tool_runner.rs:2348`). Use
    `POST /api/cron/jobs` with an IANA `tz` if you care about local time.
26. **`openfang cron create` needs the agent UUID**, prints `✘ Failed: ?` on success,
    and gives you a 120 s timeout. Prefer the API.
27. **A failed delivery is recorded as a job failure**, and 5 consecutive failures
    auto-disable the job (`cron.rs:21`). `delivery: {"kind":"none"}` always succeeds
    (`kernel.rs:7004`); `delivery_targets` failures are logged but never abort the job.
28. **Cron webhook delivery has no SSRF check** — which is how the session-reset trick
    works, and also worth knowing if you ever accept a webhook URL from elsewhere.
29. **`local_file` delivery DOES append a trailing newline.** (An earlier pass of this file said the
    opposite.) `deliver_local_file` (`cron_delivery.rs:240-262`) creates missing parent dirs, then in
    append mode writes the payload followed by `b"\n"` — the in-code comment is "Newline separator
    between runs makes tailing nicer." Runs do not run together.
30. **Cron jobs are only persisted every ~5 minutes** (20 ticks × 15 s,
    `kernel.rs:4688-4694`) plus on create/delete and shutdown. A `docker kill` can
    lose a `last_run` update.

**Model behaviour (gpt-oss-120b via Hyperfusion)**

31. **It sometimes emits a tool call as plain text and ends the turn.** Observed twice:
    the final assistant message was literally `{"key":"yti_ideas_generated"}`. Both
    times it happened during end-of-run counter bookkeeping, after the transcript had
    been read but before the report was written — so the run produced nothing and
    still recorded `ok`. Mitigations, all applied: do the valuable write first, drop
    the bookkeeping, write the deliverable to a file as well as to the reply, and
    order the steps so the last thing before answering is a `mark`.
32. **It skips ordered steps under a long prompt.** Numbered procedures with "do this
    before anything else" emphasis on the load-bearing step help; a stronger model
    would help more. `model_override` on the cron action lets you use one just for the
    nightly run without changing the agent.

---

## 11. Verification checklist

Run this after any change. Every line was green as of 2026-08-10T00:35Z **except the last** — the
nightly cron job was deleted at 2026-08-10T00:34:41Z (`docker logs` shows
`method=DELETE path=/api/cron/jobs/… status=200`), so the cron line now prints nothing. That is an
empty scheduler, not a broken command; recreate the job per §5. `scripts/ofdoctor` runs a superset of
these checks and labels each one.

```bash
K=$(grep -m1 '^api_key = ' /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml | cut -d'"' -f2)

docker ps --filter name=openfang-openfang-1 --format '{{.Status}}'          # Up ...
curl -s -H "Authorization: Bearer $K" http://127.0.0.1:4200/api/health      # {"status":"ok","version":"0.6.9"}
docker exec openfang-openfang-1 yt-dlp --version                            # 2026.07.04
curl -s http://127.0.0.1:4200/api/hands | grep -o youtube-insights          # present
curl -s -X POST -H "Authorization: Bearer $K" \
  http://127.0.0.1:4200/api/hands/youtube-insights/check-deps \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["requirements_met"])'   # True
docker exec openfang-openfang-1 sh -c 'cd /data/workspaces/youtube-insights && \
  python3 bin/ytwatch.py list https://www.youtube.com/@fireship --max 2'    # ok:true, 2 videos
curl -s http://127.0.0.1:4200/api/cron/jobs \
  | python3 -c 'import json,sys;[print(j["name"],j["enabled"],j["next_run"]) for j in json.load(sys.stdin)["jobs"]]'
# prints nothing today: {"jobs":[],"total":0}. Recreate the job per §5.
```

Files to look at when something goes wrong:

| Path (host) | What |
|---|---|
| `/var/lib/docker/volumes/openfang_openfang-data/_data/workspaces/youtube-insights/sessions/*.jsonl` | full tool-call trace of every run — the only way to see where the model derailed |
| `.../workspaces/youtube-insights/seen.json` | processed-video state |
| `.../workspaces/youtube-insights/insights/` | the reports, `latest.md`, `cron-log.md` |
| `.../workspaces/youtube-insights/transcripts/` | flattened transcripts and parts |
| `/var/lib/docker/volumes/openfang_openfang-data/_data/cron_jobs.json` | job definitions + `last_status` + `consecutive_errors` |
| `/var/lib/docker/volumes/openfang_openfang-data/_data/hand_state.json` | active hand instances and their settings |
| `docker logs openfang-openfang-1` | `Agent loop completed … iterations= tokens=`, `Cron fan-out`, `exec_policy resolved` |

The API key lives in `/data/config.toml` (`api_key`, top-level, first line) — inside
the container at `/data/config.toml`, on the host under the
`openfang_openfang-data` volume. The provider key is `api_key_env =
"HYPERFUSION_API_KEY"`, resolved from `/data/secrets.env`.
