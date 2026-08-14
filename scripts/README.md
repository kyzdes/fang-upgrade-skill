# Bundled scripts

Eight scripts: `ofctl`, `ofdoctor`, `ofhand`, `ofcron`, `ofbackup` run on the host and
talk to the running daemon; `ytwatch.py` and `rtwatch.py` run *inside* the container as
flat-argv helpers (`shell_exec` rejects pipes/redirection, so intake logic has to live in
a script file, not a prompt); `ofcheck-rs` runs on the host but talks to neither — it
drives a throwaway `rust:1-slim-bookworm` container to `cargo check` a worktree, so
patches can be checked without installing Rust on the host. Everything is POSIX `sh` or
python3 stdlib, so they have no dependency beyond what is already on the host, and
nothing to install in the container (which has no `curl`). Each of the five API tools and
`ofcheck-rs` takes `--help`.

Put them on `$PATH` once per session, or call them by absolute path:

    export PATH="$HOME/.claude/skills/fang-upgrade/scripts:$PATH"

`ofctl`, `ofdoctor`, `ofhand`, `ofcron` and `ofbackup` honour `OPENFANG_URL`,
`OPENFANG_HOME_HOST`, `OPENFANG_CONTAINER` and `OPENFANG_API_KEY`, so they can be pointed
at a second instance or at a scratch directory for a dry run. `ofcheck-rs` is unrelated to
the OpenFang API and ignores all four — it only takes a worktree path and optional crate
names.

## `ofctl` — authenticated API calls

    ofctl GET  /api/agents
    ofctl -x total GET /api/models                      # 206
    ofctl POST /api/agents/$AID/message '{"message":"Reply with exactly: PONG"}'
    ofctl PUT  /api/cron/jobs/$JID/enable '{"enabled":true}'
    ofctl -s -n GET /api/cron/jobs                      # 200 = served with no credential

Replaces `K=$(grep … config.toml); curl -H "Authorization: Bearer $K" …`.
It reads the **top-level** `api_key` with awk that stops at the first `[table]`,
so it cannot pick up `api_key_env` the way `grep '^api_key'` does (that mistake
produces a 71-char string and an empty-bodied HTTP 400 on every call). The key
goes to curl through a 0600 `--config` file, so it never appears in `ps` output
or shell history. `-x` pulls one value out of the JSON (`-x jobs.0.id`), `-n`
sends no credential so you can test what is genuinely public, and a non-2xx
status becomes a non-zero exit.

## `ofdoctor` — preflight that knows what the real doctor gets wrong

    ofdoctor              # read-only, <1 s
    ofdoctor --full       # also runs the in-container `openfang doctor` and translates it
    ofdoctor --quiet      # only NOTE/WARN/FAIL
    ofdoctor --strict     # exit non-zero on WARN too

`openfang doctor` checks 11 hardcoded provider env-var names, so it ends with
`✘ No LLM provider API keys found!` on a working box with a custom provider.
`ofdoctor` says so explicitly and then checks what actually breaks here: key
extractable, auth enforced, provider registered in `/api/providers`, model
resolves with a real context window and real pricing, the #1195 provider-prefix
collision, **the daemon key or a provider secret appearing verbatim in an
unauthenticated response**, cron jobs carrying credential-shaped delivery
targets or nearing the 5-failure auto-disable, hands on disk that were never
loaded, missing container binaries, file modes, WAL size. Exit 1 on any FAIL.

## `ofhand` — the hand lifecycle that survives a restart

    ofhand list
    ofhand lint    /root/.claude/skills/fang-upgrade/assets/youtube-insights-hand
    ofhand install /root/.claude/skills/fang-upgrade/assets/youtube-insights-hand
    ofhand activate youtube-insights videos_per_run=3
    ofhand set youtube-insights videos_per_run=1        # edits hand_state.json + restart

`openfang hand install` and `POST /api/hands/{install,upsert}` register in memory
only. `install` copies into `$OPENFANG_HOME/hands/<id>/`, restarts, then greps
the boot log for `Loaded workspace hand hand=<id>` and confirms
`GET /api/hands/<id>` — so a manifest that failed to parse cannot pass silently.
`lint` runs first and blocks on the traps that are invisible at runtime: a
missing `provider`/`model` (defaults to Anthropic, not your LLM), a
`max_iterations` that starts an hourly autonomous loop, a non-string setting
default that silently reverts, `linux_apt` in the install block. `set` exists
because `PUT /api/hands/{id}/settings` never persists and deactivate-then-
reactivate permanently deletes the hand's cron jobs — there is no `deactivate`
subcommand for that reason. `install` and `set` both take `--dry-run`.

## `ofcron` — the only reliable scheduler

    ofcron list
    ofcron create --agent assistant --name nightly-brief \
        --cron "0 3 * * *" --tz Europe/Berlin \
        --message "Run the nightly procedure." --file /data/out/log.md
    ofcron disable $JID ; ofcron enable $JID
    ofcron run $JID ; ofcron status $JID ; ofcron rm $JID --yes

Resolves an agent name to the UUID the API insists on, and rejects locally what
the API would 400 on with a terse message: the name charset, a cron expression
that is not exactly 5 letter-free fields (`MON-FRI` and `@daily` are rejected
upstream), a timeout outside 10..600. Enable and disable both go through
`PUT …/enable` with a body, because `POST …/enable` is 405 and `…/disable` is
404 while the CLI prints a checkmark for both. Delivery defaults to
`{"kind":"none"}`, which always counts as delivered — a failing `channel`
delivery is recorded as a *job* failure and five of those auto-disable the job.
There is no flag for a webhook auth header: `GET /api/cron/*` is public and
returns `delivery_targets` verbatim. `create` takes `--dry-run`, `rm` prints the
job unless given `--yes`.

## `ofbackup` — WAL-safe snapshot and restore

    ofbackup create /srv/backups
    ofbackup verify /srv/backups/openfang-20260810-064909Z
    ofbackup restore /srv/backups/openfang-20260810-064909Z          # prints the plan
    ofbackup restore /srv/backups/openfang-20260810-064909Z --yes    # carries it out

The database is SQLite in WAL mode, so `cp` of a live file can be torn — the
committed tail is in the `-wal` sibling. `create` goes through SQLite's own
backup API inside the container (no `sqlite3` binary there, but the module is
present), giving a consistent snapshot with no downtime, then tars
`config.toml secrets.env cron_jobs.json custom_models.json hand_state.json
daemon.json hands/ workspaces/` and writes a MANIFEST with `integrity_check`,
`user_version` and the table list. Output is 0600 because it contains
`secrets.env` in clear. `restore` refuses a snapshot that fails
`integrity_check` and deletes any stale `-wal`/`-shm` next to the restored file,
which is the classic way to replay a half-written transaction over a good
backup. Triggers and workflow runs never persisted and do not come back.

## `ofcheck-rs` — `cargo check` a worktree without installing Rust on the host

    ofcheck-rs /root/src/openfang-worktrees/patch-123
    ofcheck-rs /root/src/openfang-worktrees/patch-123 openfang-kernel openfang-api

Runs `cargo check` for a fork worktree inside a throwaway `rust:1-slim-bookworm`
container, so a review pass never needs a Rust toolchain on the host. Every worktree
gets its **own** Docker volume as `CARGO_TARGET_DIR` (named `fang-target-<slug>` from the
worktree's basename) instead of one shared target dir — an earlier version shared one
volume across worktrees, and cargo's fingerprinting keys on mtime as well as path, so a
check of a freshly patched tree could silently reuse a stale artifact built from a
*different* copy at the same in-container path (`/build`) and report someone else's
result. That failure mode looks exactly like a bad patch, not a broken tool, which is why
the isolation is not optional. Cost: the first check of a new worktree is a cold build
(~4.5 min); later checks are incremental. The package registry itself stays a single
shared volume (`fang-cargo-registry`) since it's read-only from cargo's perspective and
protected by its own file lock.

That per-worktree target directory is 5-12 GB, and nine worktrees on a 77 GB disk have
filled it before — `cargo` then dies mid-`clippy` with "No space left on device", which
again looks like a patch defect. `ofcheck-rs` refuses to start with **less than 12 GB**
free on `/` (exit 3, with a hint to `docker volume rm fang-target-<slug>` for merged
patches) and prints a warning under 25 GB. There is no automatic cleanup — delete the
volumes for merged/abandoned worktrees by hand.

## `ytwatch.py` — YouTube intake, runs *inside* the container

Lists a channel's newest videos, fetches auto-captions **without downloading
video**, and keeps a `seen.json` so nothing is processed twice. It exists as a
file because `shell_exec` rejects pipes and redirection — it takes flat argv
only.

    docker exec openfang-openfang-1 mkdir -p /data/workspaces/<agent>/bin
    docker cp /root/.claude/skills/fang-upgrade/scripts/ytwatch.py \
        openfang-openfang-1:/data/workspaces/<agent>/bin/ytwatch.py
    docker exec openfang-openfang-1 pip3 install --break-system-packages yt-dlp

`mkdir -p` first: OpenFang does not create `bin/`, and `docker cp` to a path whose
parent is missing fails with `Could not find the file /…/bin in container`. The
source path is absolute because the skill is rarely loaded from its own directory.

Full build guide, tested output and token budget: `references/youtube-pipeline.md`.

## `rtwatch.py` — RuTube intake, runs *inside* the container

Same shape as `ytwatch.py` (flat argv, one JSON object per invocation, `seen.json` state
file), for RuTube instead of YouTube. Not a drop-in reuse: RuTube has no channel RSS feed,
so listing goes through `yt-dlp --flat-playlist --dump-json` instead of an Atom fetch;
subtitles are `srt`, not `json3`, and live under `subtitles` rather than
`automatic_captions`, so fetching needs `--write-subs`, not `--write-auto-subs`; and video
ids are 32-char hex instead of YouTube's 11-char base64 form. Install the same way as
`ytwatch.py` — `mkdir -p` the workspace `bin/` first, then `docker cp`.
