# Bundled scripts

Five host-side tools plus one that runs inside the container. Everything is POSIX
`sh` or python3 stdlib, so they have no dependency beyond what is already on the
host, and nothing to install in the container (which has no `curl`).
Each takes `--help`.

Put them on `$PATH` once per session, or call them by absolute path:

    export PATH="$HOME/.claude/skills/openfang/scripts:$PATH"

All of them honour `OPENFANG_URL`, `OPENFANG_HOME_HOST`, `OPENFANG_CONTAINER`
and `OPENFANG_API_KEY`, so they can be pointed at a second instance or at a
scratch directory for a dry run.

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
    ofhand lint    /root/.claude/skills/openfang/assets/youtube-insights-hand
    ofhand install /root/.claude/skills/openfang/assets/youtube-insights-hand
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

## `ytwatch.py` — YouTube intake, runs *inside* the container

Lists a channel's newest videos, fetches auto-captions **without downloading
video**, and keeps a `seen.json` so nothing is processed twice. It exists as a
file because `shell_exec` rejects pipes and redirection — it takes flat argv
only.

    docker exec openfang-openfang-1 mkdir -p /data/workspaces/<agent>/bin
    docker cp /root/.claude/skills/openfang/scripts/ytwatch.py \
        openfang-openfang-1:/data/workspaces/<agent>/bin/ytwatch.py
    docker exec openfang-openfang-1 pip3 install --break-system-packages yt-dlp

`mkdir -p` first: OpenFang does not create `bin/`, and `docker cp` to a path whose
parent is missing fails with `Could not find the file /…/bin in container`. The
source path is absolute because the skill is rarely loaded from its own directory.

Full build guide, tested output and token budget: `references/youtube-pipeline.md`.
