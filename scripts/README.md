# Bundled scripts

Twelve scripts. `ofctl`, `ofdoctor`, `ofhand`, `ofcron`, `ofbackup` run on the host and
talk to the running daemon; `ytwatch.py` and `rtwatch.py` run *inside* the container as
flat-argv helpers (`shell_exec` rejects pipes/redirection, so intake logic has to live in
a script file, not a prompt); `ofcheck-rs` runs on the host but talks to neither — it
drives a throwaway `rust:1-slim-bookworm` container to `cargo check` a worktree, so
patches can be checked without installing Rust on the host — but see the warning in its
section: **its exit code is always 0**, so it cannot be used as proof. Neither can
`ofgate`, for two different reasons set out in its own section — the gate that decides
"done" is **CI**, and the only artefact that counts as evidence is a CI run id.
`ofmutate` reverse-applies a patch to check its test would go red without it,
`ofledger` reads the workflow journals, and `ofscrub` greps this repo for text that names
one particular install. Everything is POSIX `sh` or
python3 stdlib, so they have no dependency beyond what is already on the host, and
nothing to install in the container (which has no `curl`). Each of the five API tools,
`ofcheck-rs` and `ofscrub` takes `--help`.

Put them on `$PATH` once per session, or call them by absolute path:

    export PATH="$HOME/.claude/skills/fang-upgrade/scripts:$PATH"

The five API tools can be pointed at a second instance or a scratch directory. They no
longer disagree about how: `OPENFANG_HOME_HOST` and `OPENFANG_CONTAINER` are read in one
place, `scripts/oftarget.py`, and every tool goes through it. Read out of the scripts on
2026-08-24:

| | `OPENFANG_URL` | `OPENFANG_CONFIG` | `OPENFANG_API_KEY` | `OPENFANG_HOME_HOST` | `OPENFANG_CONTAINER` |
|---|---|---|---|---|---|
| `ofctl` | yes | yes | yes | via `oftarget.py` | via `oftarget.py` |
| `ofdoctor` | yes | yes | **no** | via `oftarget.py` | via `oftarget.py` |
| `ofhand` | yes | yes | yes | via `oftarget.py` | via `oftarget.py` |
| `ofcron` | yes | yes | yes | via `oftarget.py` | via `oftarget.py` |
| `ofbackup` | **no** | **no** | **no** | via `oftarget.py` | via `oftarget.py` |

That closes a gap this file used to record as unfixed: pointing `ofctl` at the staging
instance with `OPENFANG_HOME_HOST` left it reading the *other* install's `config.toml` and
sending that key to staging. Both runs below are on the same box, same command:

    $ OPENFANG_URL=http://127.0.0.1:4201 \
      OPENFANG_HOME_HOST=/var/lib/docker/volumes/openfang-staging-data/_data \
      ofctl --show-key-source
    # before: source: /var/lib/docker/volumes/openfang_openfang-data/_data/config.toml (top-level api_key)
    # after:  source: /var/lib/docker/volumes/openfang-staging-data/_data/config.toml (top-level api_key)

`ofcheck-rs`, `ofgate` and `ofmutate` are unrelated to the OpenFang API and ignore all of
these — they take a worktree path.

## `oftarget.py` — which install is this tool about to act on?

    oftarget.py container   # the container name, or a refusal
    oftarget.py home        # the host path of its data volume, or a refusal
    oftarget.py show        # both, with how each was decided

`ofdoctor`, `ofhand`, `ofcron`, `ofbackup` and `ofctl` used to carry a built-in default
target: a container name and a volume path. Those are not exotic strings — they are exactly
what `docker compose` derives from the upstream compose file (project `openfang`, service
`openfang`, volume `openfang-data`), so every reader of the quick start has a container by
that name if they have one at all. The tools restart containers and write into data
volumes; the default meant a stranger acted on whichever install answered to the name, and
anybody running a staging box beside a live one silently got the live one. Protocol rule 5
— prove the target before a destructive action, and check the name you act under — was
being broken by a constant.

The replacement does not pick a better name, it stops picking:

1. `OPENFANG_CONTAINER` set → that is the target; naming it is the proof.
2. unset → look at the running containers, keep the ones that are recognisably OpenFang
   (project name in the container name or the image reference — deliberately loose, since a
   false candidate produces a refusal that lists it, never a silent wrong target). Exactly
   one → use it and say so. Zero or several → exit 2 and list what was seen.
3. `OPENFANG_HOME_HOST` set → that is the data directory.
4. unset → read it off the chosen container's own mount table, so it cannot name a
   different install than the container about to be restarted.

Measured on a box running two of them:

    $ ofdoctor
    ofdoctor: 2 running containers look like OpenFang daemons, so the target is ambiguous
    and nothing will be guessed:
        <container A>   (<image>)
        <container B>   (<image>)
    Name the one you mean:
        OPENFANG_CONTAINER=<container> <tool> ...
    $ echo $?
    2

    $ OPENFANG_CONTAINER=<container B> oftarget.py show
    container    <container B>
      decided by OPENFANG_CONTAINER
    home on host /var/lib/docker/volumes/<container B>-data/_data
      decided by mount table of <container B> (/data)

`--help` still works with no install present at all, and `ofctl` with `OPENFANG_API_KEY`
set never calls docker: it has no reason to open a config file.

## `ofctl` — authenticated API calls

    ofctl GET  /api/agents
    ofctl -x total GET /api/models                      # 206
    ofctl POST /api/agents/$AID/message '{"message":"Reply with exactly: PONG"}'
    ofctl PUT  /api/cron/jobs/$JID/enable '{"enabled":true}'
    ofctl -s -n GET /api/cron/jobs                      # no credential: 200 without
                                                        # passkey, 401 with it

Replaces `K=$(grep … config.toml); curl -H "Authorization: Bearer $K" …`.
It reads the **top-level** `api_key` with awk that stops at the first `[table]`,
so it cannot pick up `api_key_env` the way `grep '^api_key'` does (that mistake
produces a 71-char string and an empty-bodied HTTP 400 on every call). The key
goes to curl through a 0600 `--config` file, so it never appears in `ps` output
or shell history. `-x` pulls one value out of the JSON (`-x jobs.0.id`), `-n`
sends no credential so you can test what is genuinely public, and a non-2xx
status becomes a non-zero exit.

**With passkey auth on, `ofctl`'s default URL stops working, and the failure looks like a
wrong key.** The machine key is only accepted from loopback and the tailnet
(`middleware.rs`, `is_operator_network` + `machine_key_allowed_here`), and the address the
daemon sees is the socket peer, never `X-Forwarded-For`. A container behind a *loopback*
published port never sees loopback: Docker's userland proxy rewrites the source to the
bridge gateway. A port published on the **tailnet** address is DNAT'd instead, and the real
source survives. Same scratch daemon, `auth.enabled = true`, same key, one instant:

    127.0.0.1:4298     published loopback port   -> 401 {"error":"Invalid API key"}
    <tailnet-ip>:4298  published tailnet port    -> 200
    127.0.0.1:4200     from inside the container -> 200

    $ OPENFANG_URL=http://127.0.0.1:4298    ofctl GET /api/version ; echo $?
    ofctl: HTTP 401 on GET /api/version
    1
    $ OPENFANG_URL=http://<tailnet-ip>:4298 ofctl -x git_sha GET /api/version ; echo $?
    1009ed230dcbbc86afd81d0dd17c5cd83e1b7231
    0

So on a passkey box `ofctl` needs `OPENFANG_URL=http://<tailnet-ip>:4200`, or the call has
to be made from inside the container. `401 Invalid API key` here means "right key, wrong
source address", which is exactly what it does not say.

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

**Its `authenticated request rejected` remedy is wrong on a passkey box**, and it is the
first line an operator reads. Run against staging (`auth.enabled = true`, correct 51-char
key) it prints:

    [FAIL] authenticated request rejected   /api/security with key -> 401
                                            -> an empty-bodied 400 means a malformed header:
                                               `ofctl --show-key-source` should say 51 chars
    [WARN] cron jobs unreadable             GET /api/cron/jobs -> 401

The key is fine and the header is fine; the request came through a loopback published port,
so the daemon saw the bridge gateway and declined the machine-key short path — see the
`ofctl` section above. Read those two lines as "passkey is on and I am calling the wrong
address", not as a broken key. `ofdoctor` has not been taught the difference.

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

> **Its exit code is always 0 — it cannot report failure.** The last line of the script
> is `cargo check $ARGS 2>&1 | tail -40`, and a pipeline's status is the status of its
> last command. Measured: `docker run --rm alpine sh -c "false | tail -40"; echo $?` → `0`.
> Read its *output*, never its code, and never cite it as evidence a patch builds —
> use `ofgate` for that.

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

## `ofmutate` — proves a test would go red without the patch

    ofmutate /root/src/wt/<branch> --test file_read -p openfang-runtime
    ofmutate <worktree> --test <filter> -p <crate> --dry-run     # hunk inventory only
    ofmutate <worktree> --test <filter> -p <crate> --per-hunk     # which hunks the test ignores

Mechanical red-before-green, checked after the fact: it runs the filtered test as
committed (must be **green and non-empty**), reverse-applies the patch's
*production* hunks while leaving its *test* hunks in place (must go **red**), then
restores the tree. Rust keeps unit tests in the same file under `#[cfg(test)]`, so
reverting whole files would delete the test along with the fix and prove nothing —
hence hunk-level splitting, by whether a hunk's new line range falls inside a
`#[cfg(test)]` module.

Verdicts, and why the distinction matters:

| Verdict | Exit | Meaning |
|---|---|---|
| `ДОКАЗАНО (RED-ASSERT)` | 0 | Test compiled without the patch and failed an assertion. Real proof. |
| `СЛАБОЕ КРАСНОЕ (RED-COMPILE)` | 0 | Without the patch the test does not compile. Proves only that it *knows* the new API, not that it checks its behaviour — such a test could call the new function and assert nothing. Needs `--per-hunk` or a second test. |
| `ТАВТОЛОГИЯ` | 1 | Test passes without the patch too. |
| `passed=0` refusal | 4 | The filter matched no test. A green empty run is the purest form of tautology: `cargo test <typo>` prints `test result: ok. 0 passed` and exits 0. |

It refuses to run on a dirty worktree (exit 2) — the revert would overwrite
uncommitted work — and refuses below 12 GB free (exit 3, same threshold as
`ofcheck-rs`). **Exit 3 is the tool declining, not a defect in your patch**; that
misreading cost two debugging rounds over the fork sprints. Override the threshold
only to exercise that branch: `OFMUTATE_DISK_MIN_G=999 ofmutate …`.

Runs in a container on a `fang-target-<slug>` build volume, so builds stay incremental and
no extra disk is consumed. Measured on `crates/openfang-runtime`: 203 s cold for the first
run, 35 s for the second.

**The volume is now chosen by owner, not by directory name** (`target_volume()`,
`ofmutate:178`), using the same `.ofgate-owner` marker `ofgate` writes (`ofmutate:68`,
`ofgate:82`) and the same hashed fallback name (`ofmutate:147`, `ofgate:185`). The marker
describes the *volume*, not the tool, so the two tools share a warm volume for one tree and
never share one between two.

That this mattered is not a hypothesis. Two trees whose directories are both named `twin`,
with different code — tree A's test passes, tree B's must fail:

    # before the fix
    A  volume fang-target-twin-   GREEN passed=1   Compiling twin v0.1.0 (/build)  0.85s
    B  volume fang-target-twin-   GREEN passed=1   Finished in 0.07s   (nothing compiled)
       both ran the SAME test binary, twin-62218c97741a28ad
    # B on a volume of its own, which is what B's code actually does:
       FAILED. 0 passed; 1 failed   assertion `left == right` failed: -1 vs 42

    # after the fix
    A  volume fang-target-twin-               GREEN      passed=1
    B  ofmutate: том fang-target-twin- принадлежит /root/ofmfix-twinA/twin;
       беру fang-target-twin-ad378dd327d2
    B  volume fang-target-twin-ad378dd327d2   RED-ASSERT passed=0 failed=1

`ofcheck-rs` still names its volume by `basename` alone (`ofcheck-rs:21-22`) and writes no
marker, so a volume left behind by it has no owner. `ofmutate` will **not** adopt an
unmarked volume silently — it moves to the hashed name and says so; adopt it deliberately
with `OFMUTATE_ADOPT_LEGACY_VOL=1`. If even the hashed volume belongs to another tree,
`ofmutate` exits 3 (tool refusal, not a patch defect).

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

---
## `ofgate` — a fast local check before you push. Not a gate.

    ofgate <worktree>                        # full run, 9-minute self-deadline
    ofgate <worktree> --wait                 # same, 4-hour budget, for Bash run_in_background
    ofgate <worktree> --only fmt
    ofgate <worktree> --only fmt,clippy --wait

Runs the **three cargo commands** of the `check` job in `.github/workflows/fork-ci.yml`, in
the CI's order, with the command lines copied out of that file rather than from memory:

| step | command |
|---|---|
| `fmt` | `cargo fmt --all -- --check` |
| `clippy` | `cargo clippy --workspace --all-targets -- -D warnings` |
| `test` | `cargo test --workspace -- --test-threads=2` |

The `-- --test-threads=2` on the test step is in the workflow (it bounds peak memory on
GitHub runners), so it is here too. Like CI, `ofgate` stops at the first red step.

**It is not "everything CI runs", and an earlier revision of this file said it was.**
Diffed against the workflow on 2026-08-24, `ofgate` does *not* run:

* `actions/checkout` and `dtolnay/rust-toolchain` — `ofgate` uses whatever tree you point
  it at and a toolchain image it pins itself, so a `rust-toolchain.toml` bump can pass here
  and fail there;
* the **"версии тулчейна сходятся во всех четырёх местах"** step — the one that fails the
  build when `rust-toolchain.toml`, `Cargo.toml`'s `rust-version`, the `FROM rust:` lines in
  `Dockerfile` and the workflow's own `toolchain:` disagree (FANG-92). This is a real red
  that `ofgate` will never show you;
* `Swatinem/rust-cache` and the Tauri system-dep `apt-get install`;
* the entire second job, **`image`** (`needs: [check]`) — the Dockerfile build that produces
  what the servers actually pull. A branch that is green here can still fail there, and the
  workflow's own comment on that job records it happening (`fork-ci.yml:125-128`): "ветка
  пасскея прибила Dockerfile к rust 1.88 — ниже MSRV, объявленного в Cargo.toml, — PR прошёл
  зелёным … а упало при первой же сборке образа с main". That history is the comment's, not a
  run of mine.

So a green `ofgate` means "the three cargo commands pass on my tree", nothing wider.

### What it is, and what decides "done"

An earlier revision of this file titled this section "the one command that decides
'done'". That was false on two counts, and both were established by running the tool, not
by argument.

**One: evidence printed by the claimant is forgeable in a minute.** The verdict block is
self-signed — its closing `sha256` covers the body printed above it, the step hashes cover
files the same process wrote, and there is no key. That last one is checkable, but the
plain `grep -n 'hmac\|secret\|OFGATE_KEY\|openssl' ofgate` an earlier revision printed here
does **not** return nothing: it returns the comment inside `ofgate` that quotes the very
same pattern. Strip the comments first and it is empty —

    $ sed 's/#.*//' ofgate | grep -c 'hmac\|secret\|OFGATE_KEY\|openssl'
    0

Redone from scratch on 2026-08-23: a full three-step GREEN block typed into a file, one `sha256sum` of the body
appended as the closing line, and it self-checks —

    $ head -n -1 fake.txt | sha256sum | cut -d' ' -f1
    fb087f2141c8344469a9a6aa52b904bf1d15ea14bd25ee0f5b63e9ffe0953c2b
    $ grep -o 'body-sha256=[0-9a-f]*' fake.txt
    body-sha256=fb087f2141c8344469a9a6aa52b904bf1d15ea14bd25ee0f5b63e9ffe0953c2b
    $ docker ps -a --filter ancestor=ofgate-build:1.91 -q | wc -l
    0

— zero containers, elapsed under a minute. The tool says so itself on every run, on stderr:

    ofgate: блок выше НЕ заверен — он самоподписан и подделывается вручную.
            Доказательство — перезапуск гейта проверяющим.

**Two, and worse: it can print GREEN without building anything.** See the next subsection.
A tool that can lie green is worse than no tool, because people believe it.

**What counts as evidence is a CI run id.** `.github/workflows/fork-ci.yml` triggers on
push to `main` and on pull requests into it, so pushing a branch runs nothing: branch → PR
→ green CI → merge. The run id cannot be invented, and it carries the commit it ran on:

    $ gh run view -R kyzdes/fang-upgrade 32627036232 --json conclusion,headSha,workflowName
    {"conclusion":"success",
     "headSha":"1009ed230dcbbc86afd81d0dd17c5cd83e1b7231",
     "workflowName":"Fork CI (ours)"}

    $ gh run view -R kyzdes/fang-upgrade 99999999999 --json conclusion,headSha >/dev/null 2>&1; echo $?
    1

Three things about that command, each a way to read a red run as green:

* **`-R` is not optional.** `/root/src/openfang` has three remotes and no default repo set,
  so `gh` picks `upstream`. Without `-R` the same id gives
  `HTTP 404: Not Found (…/repos/RightNow-AI/openfang/actions/runs/32627036232)`.
* **An empty `conclusion` is not a pass.** A run in flight reports
  `{"conclusion":"","status":"in_progress"}` — measured on run `32666318113` while it was
  still going. Ask for `status` alongside `conclusion` and require `completed` + `success`.
* **Check `headSha` against what you are claiming green.** A run id proves that *some*
  commit passed.

So: use `ofgate` to fail fast locally and save CI round trips. Cite CI.

### The false green: a reused build volume and an mtime that did not move

`ofgate` keys its build-target volume to the worktree **path** and mounts the tree at a
fixed `/build`. Cargo's freshness check keys on path plus mtime. Put those together: if the
tree at that path is replaced by different content whose mtimes are **not newer** than the
previous run's artefacts, cargo finds its fingerprints valid, rebuilds nothing, and
`ofgate` reports the previous run's result on code it never compiled.

Every ordinary way of deploying a tree hits this, because none of them stamps "now":

    source file mtime 2026-08-01 10:00:00, commit dated 2026-08-05 12:00:00,
    wall clock at the time of the test 2026-08-23 22:09
      cp -a               -> 2026-08-01 10:00:00     (source mtime preserved)
      rsync -a            -> 2026-08-01 10:00:00     (source mtime preserved)
      git archive | tar -x-> 2026-08-05 12:00:00     (commit date)
      docker cp           -> 2026-08-01 09:00:00 UTC (source mtime preserved)

Reproduced with `cp -a`, on a two-file canary, no `touch` anywhere — both source trees were
created before the first run, so nothing had to be back-dated. Run 1 on the clean tree, run
2 after `cp -a` of a tree carrying `let pi = 3.14159265358979;` (`clippy::approx_constant`),
run 3 on the same bad tree after deleting the volume:

    # 1 — clean tree
    binding    : … tree-sha256:970fd64586bdaf2475fcbc72ec6d38e9 (2 файлов)
    target-vol : fang-target-skp-fg-tree-
    --- clippy : GREEN  exit=0  1s        log 115 B
    exit       : 0

    # 2 — bad tree at the same path, same volume, cp -a
    binding    : … tree-sha256:36c108a9161ef8c4d8032737f6aab131 (2 файлов)   ← different tree
    target-vol : fang-target-skp-fg-tree-
    --- clippy : GREEN  exit=0  0s        log 72 B                          ← FALSE GREEN
    exit       : 0
    $ cat …/clippy.log
        Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.02s   ← no `Checking` line

    # 3 — same bad tree, volume removed first
    $ docker volume rm fang-target-skp-fg-tree-
    binding    : … tree-sha256:36c108a9161ef8c4d8032737f6aab131 (2 файлов)   ← identical to run 2
    --- clippy : RED  exit=101  0s
          | error: approximate value of `f{32, 64}::consts::PI` found
          |  --> src/lib.rs:2:14
    exit       : 1

Runs 2 and 3 differ in nothing but the build volume. The `binding` line does change — it
hashes tree content — so a reader comparing two blocks can *notice*, but `ofgate` does not
compare, and `result : GREEN` is what goes into a report.

**The workaround, from the code rather than from memory.** `ofgate` has no environment
variable for the target volume: the whole list is `OFGATE_DEADLINE_S`, `OFGATE_DISK_MIN_G`,
`OFGATE_LOG_DIR`, `OFGATE_ADOPT_LEGACY_VOL`. The volume name is computed from the worktree
path — `fang-target-$(basename)` while that name is free, `fang-target-<basename><12 hex of
sha256(path)>` when another tree owns it. So a fresh target for a run means one of:

* `docker volume rm fang-target-<basename>-` before the run (note the trailing `-`: it is
  the newline `basename` prints, turned into a dash by `tr`), which is what run 3 above
  does; or
* run against a **worktree path that has never been used before** — a new path gets a new
  volume.

Both cost a cold build. The figures for the fork are **not** re-measured here — they are
the ones already on record further down this file, from the previous revision: 22 GB of
target, 1248–1615 s for a full run. A cold full run needs disk this box does not currently
have free (19 GB), which is itself part of why the honest answer is to let CI do it.

Use the fresh-volume form whenever the tree arrived by any of the four methods above, and
whenever a green result would be quoted at anyone.

**The error is one-way, which is why the tool is still worth running.** Cargo does not
fingerprint a failed compile, so a volume that has just gone red does not keep returning
red. Measured on a second canary: bad tree on a fresh volume → `--- clippy : RED exit=101`,
then the good tree `cp -a`'d over it (mtime *older* than the failed run) on that same
volume → `--- clippy : GREEN exit=0`. So a **red** `ofgate` is worth acting on immediately;
it is **green** that carries no information until CI says so.

Exit codes: `0` every requested check green · `1` at least one red · `2` bad arguments ·
`3` the tool declining · `4` the run did not fit `ofgate`'s own deadline. **`3` and `4`
are the tool, not a defect in the tree** — reading a tool refusal as a bad patch has
happened here twice. Override the disk arithmetic only to exercise that branch:

    $ OFGATE_DISK_MIN_G=999 ofgate /root/ofgfix-plainname --only fmt
    ofgate: свободно 31G, этому прогону нужно 999G.
      ...
      это ОТКАЗ ИНСТРУМЕНТА, а не дефект проверяемого дерева.
    $ echo $?
    3

A non-numeric override is refused rather than ignored. Before this was fixed a typo
disabled the disk gate outright — `[: Illegal number: lots`, `[` returns 2, `if` reads
that as "there is enough room", exit 0. Measured, both versions:

    $ OFGATE_DISK_MIN_G=lots ofgate /root/ofgfix-plainname --only fmt   # old
    /root/.claude/skills/openfang/scripts/ofgate: 181: [: Illegal number: lots
    result     : GREEN — все запрошенные проверки прошли
    $ echo $?
    0

    $ OFGATE_DISK_MIN_G=lots ofgate /root/ofgfix-plainname --only fmt   # now
    ofgate: OFGATE_DISK_MIN_G должно быть целым неотрицательным числом, а получено 'lots'
    $ echo $?
    2

This is the same class as `ofcheck-rs`'s `tail`: a comparison that fails is read as a
comparison that passed. Every number that reaches a comparison in `ofgate` is checked
first, and an unparseable one is a refusal.

### Why this exists next to `ofcheck-rs`, which is not going away

**`ofcheck-rs` cannot report a failure — it is broken by exit code.** Its last line is

    cargo check $ARGS 2>&1 | tail -40

and a pipeline's status is the status of its *last* command, here `tail`, which always
succeeds. Measured, not read off the source:

    $ docker run --rm alpine sh -c "false | tail -40"; echo $?
    0

No call to `ofcheck-rs`, past or future, can return non-zero, and `tail -40` truncates
the error list on top of that. `ofgate` has no pipelines — each step is its own
container, whose exit code comes from `docker wait` and is then checked for *being a
number at all* before it is compared — and the complete output goes to a file, with only
a summary and the path on stdout.

Two more gaps it closes:

* **No script on this box ran `cargo fmt --check`** before `ofgate`.
  `grep -rl 'cargo fmt' /root/.claude/skills /root/src/openfang --exclude-dir=.git`
  returns **14** files as of 2026-08-21 (an earlier revision of this file said nine —
  that count had gone stale). Exactly two of them are executable, and only one runs the
  check: `ofgate` itself, and `docs/subagent-report.schema.check.py`, which merely
  mentions the string. The other twelve are documents, the two workflow files, agent
  templates, and one hand-written record of a single ad-hoc run
  (`tests/fang/after-v6/build-checks.txt`).
* **`ofcheck-rs`'s `--workspace` branch cannot work.** It substitutes `--workspace` when
  handed no crate list, but its `rust:1-slim-bookworm` + `pkg-config libssl-dev perl
  make` image cannot build `openfang-desktop`: that is step 1 of the image bisection
  below, and the same wall is recorded independently in
  `/root/src/openfang/tests/fang/after-v6/build-checks.txt` ("`cargo test --workspace`
  does not build on this box because a GUI crate wants glib-2.0 dev headers the builder
  image does not carry"). With `ofgate`'s image the whole workspace builds, so
  `--workspace` is the normal mode and not a dead branch.

### The build image

`scripts/ofgate-image/Dockerfile`, tagged `ofgate-build:1.91`. `ofgate` builds it on
first use and reuses it, instead of `apt-get install`-ing into a throwaway container on
every call the way `ofcheck-rs` does.

Its contents were established by running the build and reading what broke:

1. `rust:1.91-slim-bookworm` plus what `ofcheck-rs` installs (`pkg-config libssl-dev
   perl make`) → `cargo check -p openfang-desktop` dies in `glib-sys`:
   `The system library glib-2.0 required by crate glib-sys was not found.`
2. adding `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev
   patchelf` → the same command finishes `exit=0`, 5m43s from cold.

So **`--workspace --exclude openfang-desktop` is not needed** — the exclusion turned out
to be five apt packages.

The toolchain is installed under the name the fork asks for. `rust-toolchain.toml` says
`channel = "1.91"`; `rust:1.91-slim-bookworm` ships the toolchain named
`1.91.1-x86_64-unknown-linux-gnu`, which is a different name to `rustup`, so it
re-downloaded the whole toolchain on every run. Same green `--only fmt` run, before and
after baking `rustup toolchain install 1.91` into the image: **19s → 4s**, and the log
went from 511 B of `info: downloading component …` to 0 B.

### `/build` is writable, and the tree is still never touched

The tree is mounted read-only at `/src`; `/build` is a tmpfs into which each step does
`cp -a /src/. /build/` before running cargo.

The first version mounted the worktree straight onto `/build` with `:ro`, and that broke
the thing the gate exists for. Cargo rewrites `Cargo.lock` whenever a patch adds a
dependency, and on a read-only mount that produced

    error: failed to write /build/Cargo.lock
    Caused by: Read-only file system (os error 30)
    --- clippy : RED  exit=101
    exit 1

— a **tool failure printed as a red patch**, on code Fork CI (which checks out writable)
would have passed. Reproduced on a canary and then re-run after the fix:

    $ ofgate /root/ofgfix-deptest --only clippy --wait     # before
    --- clippy : RED  exit=101  1s
          | error: failed to write /build/Cargo.lock
          | Read-only file system (os error 30)
    $ echo $?
    1

    $ ofgate /root/ofgfix-deptest --only clippy --wait     # after
    --- clippy : GREEN  exit=0  1s
    note       : cargo переписал Cargo.lock (дерево не изменено; как вышло — …/Cargo.lock.after)
    $ echo $?
    0
    $ wc -c /root/ofgfix-deptest/Cargo.lock                # user's tree, unchanged
    0 /root/ofgfix-deptest/Cargo.lock

The copy costs 1.2 s on `/root/src/openfang` (35 MB including `.git`) and does **not**
cost a rebuild: the path stays `/build` and `cp -a` preserves mtimes, so cargo's
fingerprints stay valid. Measured on a canary with a dependency, two consecutive runs —
the second log reads `Finished dev profile … in 0.09s`, with no `Checking` line. The
`Cargo.lock` cargo produced is lifted out of the tmpfs into the log directory as
`Cargo.lock.after`, and a `note :` line in the verdict says so. If a step ever does fail
on a read-only write anyway, that is reported as `TOOLFAIL` and exit **3**, not as a red
check.

### Build target isolation, and who owns a build volume

The disease is described in `ofcheck-rs`'s own header: one shared `CARGO_TARGET_DIR`
across worktrees returned another tree's result *silently*, because cargo fingerprints
key on path and mtime while every tree is mounted at the same path.

Naming the volume after the worktree's `basename` alone does **not** cure it, and the
first version of `ofgate` did exactly that. Two different trees whose directories share a
name share a volume. Measured on two canaries, `/root/ofgfix-twinA/twin` and
`/root/ofgfix-twinB/twin`, the second containing `let pi = 3.14159265358979;`
(`clippy::approx_constant`), which in isolation is `exit=101`:

    # old ofgate
    twinA: target-vol : fang-target-twin-   --- clippy : GREEN  exit=0
    twinB: target-vol : fang-target-twin-   --- clippy : GREEN  exit=0     ← false green

    # now
    twinA: target-vol : fang-target-twin-
           --- clippy : GREEN  exit=0
    twinB: target-vol : fang-target-twin-1dd290d414e4  (fang-target-twin- принадлежит /root/ofgfix-twinA/twin)
           --- clippy : RED  exit=101   error: approximate value of `f{32,64}::consts::PI` found
           exit 1

Ownership is recorded *inside* the volume, in `.ofgate-owner`, which holds the real path
of the tree that claimed it:

* volume absent → create it, write the marker;
* marker names this very tree → the volume belongs to it, build incrementally;
* marker names another tree → use `fang-target-<slug><hash-of-path>` instead;
* **no marker** (a volume from the old naming scheme) → do not adopt it silently. The
  default is to move to the path-hashed volume and rebuild from cold. Adopting the old
  volume is possible but must be explicit, `OFGATE_ADOPT_LEGACY_VOL=1`, and only by
  someone who knows which tree filled it. This is what preserves the warm 25 GB
  `fang-target-openfang-` across the change instead of stranding it.

The disk guard is measured arithmetic rather than `ofcheck-rs`'s flat 12 GB: a full green
`fmt+clippy+test` target for this fork is 22 GB, so a long mode demands only what its own
volume still lacks of those 22, and an incremental run on an already-filled volume
legitimately proceeds on a nearly full disk.

Those figures are read out of `du` and `df` **by first field**. The first version piped
the whole `du` line through `tr -dc '0-9'` and so swallowed the digits in the *path*:

    $ du -sBG /var/lib/docker/volumes/fang-target-ofgfix-2026-08-21-/_data
    1G	/var/lib/docker/volumes/fang-target-ofgfix-2026-08-21-/_data
    $ ... | tr -dc '0-9'          # old
    120260821
    $ ... | awk 'NR==1{gsub(/[^0-9]/,"",$1);print $1+0}'   # now
    1

With the old parse, `NEED_G = 22 - 120260821` went negative, clamped to the 2 GB
headroom, and the disk gate was off for any directory with a digit in its name. Two
byte-identical trees differing only in directory name, with the constant scaled so the
threshold is crossable on today's 31 GB disk:

    old:  /root/ofgfix-plainname → EXIT=3     /root/ofgfix-2026-08-21 → GREEN, EXIT=0
    now:  /root/ofgfix-plainname → EXIT=3     /root/ofgfix-2026-08-21 → EXIT=3

### The ten-minute ceiling: `ofgate` watches its own clock

The Bash tool's `timeout` ceiling is 600000 ms; a larger request is silently truncated to
ten minutes and returns `Command timed out after 10m 0s` while the command keeps running.

The first version "refused" to run long modes in the foreground — but it never
determined whether it *was* in the foreground. It looked only at the `--wait` flag, so
the refusal was bypassed by an argument and was therefore not a refusal. It is now a
budget that `ofgate` enforces against itself:

* no `--wait` → deadline **540 s**, comfortably inside the ceiling;
* `--wait` → deadline **14400 s**, the value for a `run_in_background` job;
* `OFGATE_DEADLINE_S` overrides both.

When the budget runs out `ofgate` kills the container, frees the volume, prints a partial
verdict with `result : TIMEOUT`, and exits **4** with the background command to use.
Measured with the budget dialled down to 25 s against a canary whose `build.rs` sleeps
3000 s:

    $ OFGATE_DEADLINE_S=25 ofgate /root/ofgfix-hang --only clippy
    deadline   : 25s (--wait=0); по исчерпании — контейнер убит, exit 4
    --- clippy : TIMEOUT  не уложился в дедлайн 25s  (26s)
    result     : TIMEOUT — прогон не уложился в 25s на шаге clippy; контейнер убит
    $ echo $?      # wall clock 28 s
    4
    $ docker ps -a --filter ancestor=ofgate-build:1.91 -q | wc -l
    0

So an agent that forgets `--wait` gets a clean code 4 at nine minutes instead of a silent
truncation at ten. Nothing decides "long" by guessing any more: the old
`/target/.ofgate-clippy-warm` stamp is gone, along with the hole where a stamp left by
*any* green clippy legalised a foreground `fmt,clippy` no matter how much the patch had
invalidated.

### An interrupted run leaves nothing behind

Each step runs detached, its id is held in a variable, and a `trap` on `EXIT INT TERM
HUP` removes it. Before this, an interrupted run left the container **alive** — `--rm`
never fires — still holding the build-target volume and the shared registry volume:

    $ timeout 100 ofgate /root/ofgfix-hang --only clippy --wait     # old
    $ docker ps --filter ancestor=ofgate-build:1.91
    f8a312973670   Up 43 seconds   "sh -c 'cargo clippy…"
    $ docker inspect f8a312973670 --format '{{range .Mounts}}{{.Name}} {{end}}'
    fang-target-ofgfix-hang- fang-cargo-registry

The first attempt at the trap **did not work**, and the canary is why it was caught:
`dash` defers a signal handler until the current foreground command finishes, so a trap
written above a blocking `timeout N docker wait $CID` never runs. `timeout 40 sh ofgate
…` left `ofgate` alive at second 621 with its container. The wait is therefore a
background child collected with `wait`, which *is* interruptible:

    $ timeout 30 ofgate /root/ofgfix-hang --only clippy --wait ; echo $?   # now
    124
    $ ps -eo pid,cmd | grep -E 'ofgate|docker wait' | grep -v grep
    (none)
    $ docker ps -a --filter ancestor=ofgate-build:1.91 -q | wc -l
    0

`kill -INT` on a running `ofgate` behaves the same: exit 130, zero containers left.

### Measured on this box, this revision

`/root/src/openfang` at `81057f9`, clean tree, **warm** `fang-target-openfang-` volume
adopted with `OFGATE_ADOPT_LEGACY_VOL=1`, shared cargo registry:

| mode | time |
|---|---|
| full `fmt,clippy,test`, warm volume | **81 s** (fmt 4 · clippy 3 · test 74), `result : GREEN`, exit 0 |
| `--only fmt` on a full copy of the fork | 3 s |
| the `cp -a /src/. /build/` prologue alone, 35 MB tree incl. `.git` | 1.2 s |

The warm volume was measured at 146 s for the same full run under the previous revision,
so moving `/build` to a tmpfs copy did **not** cost the incremental build — the artifacts
in the volume were still valid after the copy. Cold-volume timings for this revision have
not been re-measured; the figures on record (fmt 3 s · clippy 433 s · test 812–1175 s,
whole run 1248–1615 s) are the previous revision's and a cold full run needs 22 GB the
disk does not currently have free.

### The verdict block, and what it is not

Every run prints a fixed-shape block meant to be pasted into a report verbatim:

    === OFGATE VERDICT ===
    worktree   : /root/src/openfang
    commit     : <sha> (dirty=no)
    binding    : коммит <sha> · дерево чистое · tree-sha256:<h> (N файлов)
    image      : ofgate-build:1.91 sha256:…
    toolchain  : rust-toolchain.toml=1.91 · в образе: rustc 1.91.1 (ed61e7d7e 2025-11-07)
    target-vol : fang-target-openfang-
    checks     : fmt,clippy,test  (порядок CI, останов на первой красной)
    started    : …
    deadline   : 14400s (--wait=1); по исчерпании — контейнер убит, exit 4
    logs       : /var/tmp/ofgate/openfang-/<run-id>
    --- fmt : GREEN  exit=0  Ns
          cmd : cargo fmt --all -- --check
          log : …/fmt.log  (0 B, sha256:e3b0c44298fc1c14)
    …
    result     : GREEN — все запрошенные проверки прошли
    elapsed    : Ns
    exit       : 0
    === END OFGATE VERDICT run=<run-id> body-sha256=<h> ===

**The block is not tamper-proof and cannot be, in this scheme.** The closing line is the
`sha256` of the very body printed above it; the step lines are `sha256`s of files the
same process wrote; there is no key —
`sed 's/#.*//' ofgate | grep -c 'hmac\|secret\|OFGATE_KEY\|openssl'` prints `0`. (Without
the `sed` it prints `1`: `ofgate` carries a comment quoting that pattern, so the grep finds
itself. An earlier revision of this file offered the bare grep as the proof.) A complete,
self-consistent GREEN block is assembled by hand with one `sha256sum` call and no cargo
container is started; that was measured, not assumed. Earlier revisions of this file
claimed "retelling it is detectable" — that claim was false and has been removed.

What the block actually gives, and only this:

* a fixed-width machine-readable shape that can be parsed;
* a pointer to logs that stay on disk under `logs:`, so they can be read and re-hashed;
* a `binding` line tying the result to tree *content* and to the toolchain image.

The `binding` line exists because a non-git tree and a dirty tree both used to produce a
plain `result : GREEN` bound to nothing at all — `commit : не git-дерево (dirty=?)`
went into reports as evidence. Now every run hashes the tree (tracked plus untracked
non-ignored files under git; everything outside `.git/` and `target/` otherwise) and says
plainly when the result is *not* bound to a commit:

    commit     : 1aa58ee0225b5cea906bdd6cff4c1f371d6fffb0 (dirty=no)
    binding    : коммит 1aa58ee… · дерево чистое · tree-sha256:736a43bc20540a72d8dafbf2051e38d7 (2 файлов)

    commit     : 1aa58ee0225b5cea906bdd6cff4c1f371d6fffb0 (dirty=yes)      ← same commit
    binding    : НЕ привязано к коммиту: дерево грязное (HEAD 1aa58ee…) · tree-sha256:440c1c2db63b7c2e139e5107a00f9cae (2 файлов)

That is a binding, not a signature: it does not stop forgery, it just means the same
block quoted against a different tree is identifiable as a different tree.

An earlier revision closed this subsection with "a verdict block is evidence only after
the reader re-runs the gate — `ofverify` exists for that". Both halves are wrong and are
removed. There is no `ofverify`: `ls ~/.claude/skills/*/scripts/ofverify` →
`No such file or directory`, and `find /root/src/openfang -name 'ofverify*'` returns
nothing. And re-running is not enough on its own — a re-run on the same warm volume can
reproduce the same false green, which is the whole point of the subsection above. A
verdict block is a convenience for reading; the evidence is a CI run id.

### Proof that it can go red

The predecessor is broken precisely because nobody ran it against a known-bad tree, so
every check here has been run against one. These are runs of *this* revision, on
canaries built for it — `git log -- scripts/ofgate` shows one commit, the import, so the
script has not moved since. A green run on a clean tree proves nothing on its own, since a
tool that always exits 0 produces one too. (Note what this section does and does not
establish: that the tool *can* report red on a bad tree — not that a green result means a
tree is good. See "The false green" above.)

| canary | what is wrong with it | result |
|---|---|---|
| `/root/ofgfix-forkcopy` — a full copy of the fork (711 files) | `pub fn ofgfix_fmt_canary( x : i32 )->i32{   x   +1 }` appended to `crates/openfang-types/src/lib.rs` | `--- fmt : RED exit=1`, diff quoted in the block, exit **1** |
| `/root/ofgfix-twinB/twin` | `let pi = 3.14159265358979;` (`clippy::approx_constant`) | `--- fmt : GREEN`, `--- clippy : RED exit=101`, exit **1** |
| `/root/ofgfix-testred` | a `#[cfg(test)]` test asserting `"red" == "green"` | `--- fmt : GREEN`, `--- clippy : GREEN`, `--- test : RED exit=101`, exit **1** |

And the tool-refusal codes are exercised separately, so that they are never mistaken for
a red patch: `3` with `OFGATE_DISK_MIN_G=999` and with `OFGATE_DISK_MIN_G=lots` → `2`,
`4` with `OFGATE_DEADLINE_S=25` against a canary whose `build.rs` sleeps 3000 s.

The unmodified copy of the fork and the fork itself produce the **same** `binding` line —
`tree-sha256:2545f3c1fbb2c96389c913204f6b782a (711 файлов)` — which is what a content
binding is supposed to do; appending one function to the copy changes it to
`b4ce53a6ae9d9d7c7612bbd00ad03600`.


## `ofledger` — sums up every run's "не проверял" list, and finds the repeats

    ofledger --dir ~/.claude/projects/-root/<session-id>/workflows
    ofledger --dir <path-to-workflows> --since 2026-08-16
    ofledger --dir <path-to-workflows> --json | python3 -m json.tool

Every subagent report ends with an "unfinished/несделано" list — direct, honest,
and, until now, unread. `ofledger` walks `wf_*.json` run files, pulls every such
field out of `result` (wherever it's nested — the schema is not consistent run to
run), drops items that explicitly say there's nothing unfinished ("нет — все пункты
приёмки выполнены", bare "none"), splits the rest into individual items, and groups
items that are the same gap worded differently. A gap that shows up in **two or
more** separate runs is flagged as an escalation instead of a third silent repeat.

Grouping (2026-08-21 rewrite — v1 glued unrelated escalations together; an
adversarial pass hand-counted 23 foreign items out of 45 escalated, mostly by
treating a single shared ticket-number or CLI flag as proof of sameness):
one shared ticket key (`A-6`, `FANG-43`) is no longer enough by itself — it now
needs a *second* shared ticket key, or one key plus >= 2 shared significant words,
or (without any shared key) >= 30% word-Jaccard with >= 2 shared words. CLI flags
(`--workspace`, `--stat`, ...) never glue on their own — a lone `--stat` in common
was exactly what merged two unrelated escalations in v1. A small hand-curated
synonym list (`CAUSE_TAGS`) merges worded-differently mentions of the *same* root
cause — `--workspace`, `glib-2.0`, `gobject-2.0`, `gtk-*` all fold into one
"build image missing glib/gtk" tag, which is what finally shows that gap as one
6-run escalation instead of two disjoint 4-run ones. See the script's own docstring
for the exact rules and, just as importantly, where each of them is known to
misfire (transitive over-merging, missed cross-language duplicates, ticket-shaped
substrings inside volume/branch names, and the synonym list gluing any unrelated
gtk/glib mention).

It also rolls up `workflowProgress` into an agent summary: done/error counts,
retries, tokens/time by model, and — separately — errors at `attempt == 1`, which
mean the work was lost outright, not just delayed. Exit 0 only when it actually
found something to report on **two or more** runs; exit 2 on missing/empty input,
a single-run directory (nothing to compare — a lone run can't "repeat"), or bad
arguments — never a quiet empty success.

---
## `ofscrub` — keeps README's placeholder promise checkable

    ofscrub                 # scan the skill tree this script lives in
    ofscrub <dir>           # scan another tree (a git worktree, a release tarball)
    ofscrub --list          # print the rules and exit 0

`README.md` promises that addresses and host names appear as `<tailnet-ip>`, `<public-ip>`,
`<tailnet-host>`, the passkey relying party as `<rp-host>` and a passkey slot as `<slot>`.
Nothing enforced it, and it was broken by hand twice. This repo is public.

**It checks shapes, not a list of names, and that is a decision with a history.** Three
earlier versions carried the leaked strings — first as plain text, then as
`(length, FNV-1a, SHA-256)` triples with a docstring claiming the names could not be read
back. They could: a 1603-word dictionary recovered all four in a fraction of a second,
because an unsalted digest of a short guessable string with its length published is a
dictionary attack and not a secret. A public file cannot hold a secret salt. So the guard
stopped asking *whose* a value is:

- **the value's shape** — an IP literal that is globally routable designates exactly one
  host on the public internet, whoever owns it; an address in Tailscale's carrier-NAT range
  designates one node of one tailnet; a name under `.ts.net` is minted per tailnet. The
  innocent/guilty line is `ipaddress.ip_address(x).is_global` minus multicast — IANA's list
  of special-purpose ranges as the standard library maintains it, so loopback, RFC 1918, the
  RFC 5737/3849 documentation ranges and the benchmark range pass without being enumerated
  here. `100.64.0.0/10` written as a range and Tailscale's fixed `100.100.100.200` resolver
  are forgiven: neither designates a host.
- **the slot's shape** — a relying-party host or a passkey slot name is an ordinary word;
  what gives it away is the key it is assigned to. A value in `rp_id`, `rp_origin`, the `id`
  of a WebAuthn `rp` object, a `slot`, an `openfang auth invite|revoke|reset-slot` argument
  or `OPENFANG_URL` **is** the install's identity by definition of the key. Forgiven only
  when it is a `<placeholder>`, empty, an RFC 2606/6761 documentation name, or an address
  the first leg already calls innocent. The file holds a list of KEYS, never of values.

What it cannot see is stated in its own header rather than left for a reader to discover: a
bare domain in prose (`moone.dev`, `github.com` and `northwind-lab.io` are one shape), a
container or volume name, and an unstructured secret — that last one is `ofdoctor`'s job.

**It scans its own source like every other file.** No `--exclude`, no self-exemption. The
rules describe shapes, so they do not contain the shapes they describe; needing an exemption
would be evidence it was still carrying a list. Proved by planting a foreign install's
values inside `ofscrub` itself:

    $ printf '# rp_id = "<a foreign host>"  and  <a foreign public IPv4>\n' >> copy/ofscrub
    #   ...with the two placeholders replaced by real values of an install that is
    #   not this one. They are not printed here for the same reason the tool does
    #   not print them: this file is scanned by the tool it documents.
    $ python3 copy/ofscrub copy
    HIT  globally routable IP literal (must be <public-ip>)
           ofscrub:287
    HIT  identity slot holding a real value (must be <rp-host> / <slot>)
           ofscrub:287
    1

**A hit is reported by file and line only.** The previous version printed the matching line
in full, so its own report republished the string it exists to keep out of the tree — and
reports get pasted into issues and chat. A path and a line number are everything to whoever
holds the tree and nothing to whoever does not.

Exit 0 clean · 1 at least one hit · 2 bad arguments. Red-before-green, on a canary holding
five values of an install that is not this one, in the shapes the old rules had no rule for
(JSON `rp_id`, a slot name as a command argument, a public IPv4, a public IPv6):

    $ sh old-ofscrub canary ; echo $?          # every rule the old version had
    ok   public domain of this install
    ok   retired host name
    ok   public IPv4 of this host
    ok   tailnet address literal (must be <tailnet-ip>)
    ok   MagicDNS host name (must be <tailnet-host>)
    ok   passkey slot name printed literally
    ok   relying-party id printed literally
    ok   relying-party origin printed literally
    ofscrub: clean
    0
    $ python3 ofscrub canary ; echo $?         # the same file, this version
    HIT  globally routable IP literal (must be <public-ip>)
           planted.md:4
           planted.md:5
    HIT  identity slot holding a real value (must be <rp-host> / <slot>)
           planted.md:1
           planted.md:2
           planted.md:3
    1

and green on a canary of legitimate text — loopback, the docker bridges, RFC 1918, the
RFC 5737/3849 documentation ranges, multicast and broadcast, `100.64.0.0/10`,
`100.100.100.200`, `rp_id = "rp.example"`, `rp_id = ""`, `"slot": "<slot>"`,
`OPENFANG_URL=http://127.0.0.1:4200`, `github.com` and a version number `0.6.9`:

    $ python3 ofscrub legit-canary ; echo $?
    ofscrub: clean
    0
