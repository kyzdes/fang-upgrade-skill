# Bundled assets

## `youtube-insights-hand/`
A complete, working custom hand — `HAND.toml` + `SKILL.md`. It ran against a real
channel and produced insight reports, so use it as the template for any new hand
rather than writing one from a blank file.

Install (the only method that survives a restart — see `references/hands.md`):

    ofhand install /root/.claude/skills/fang-upgrade/assets/youtube-insights-hand

`ofhand install` lints the manifest, copies the two files into
`$OPENFANG_HOME/hands/youtube-insights/`, restarts the container, and then proves
the hand loaded by grepping the boot log for `Loaded workspace hand
hand=youtube-insights` and confirming `GET /api/hands/youtube-insights`.

By hand, copy through the host volume:

    mkdir -p /var/lib/docker/volumes/openfang_openfang-data/_data/hands/youtube-insights
    cp /root/.claude/skills/fang-upgrade/assets/youtube-insights-hand/HAND.toml \
       /root/.claude/skills/fang-upgrade/assets/youtube-insights-hand/SKILL.md \
       /var/lib/docker/volumes/openfang_openfang-data/_data/hands/youtube-insights/
    docker restart openfang-openfang-1

Do **not** write `docker cp <srcdir> openfang-openfang-1:/data/hands/youtube-insights`.
When the destination directory already exists — and it does on this box — `docker cp`
copies the source *into* it, producing
`/data/hands/youtube-insights/youtube-insights-hand/{HAND.toml,SKILL.md}`. The kernel
reads `/data/hands/<id>/HAND.toml`, so it reloads the **old** definition after the
restart and logs `Loaded workspace hand hand=youtube-insights` anyway — the update
silently does nothing. If you want `docker cp`, the trailing `/.` is required:

    docker cp /root/.claude/skills/fang-upgrade/assets/youtube-insights-hand/. \
        openfang-openfang-1:/data/hands/youtube-insights/

## Note on the deployed copy

`HAND.toml` here now instructs `timeout_seconds: 120` for `ytwatch.py fetch`. The copy
currently live at `/data/hands/youtube-insights/HAND.toml` still says 240, which the
agent loop silently caps at 120 anyway (`TOOL_TIMEOUT_SECS`, `agent_loop.rs:47`).
Running `ofhand install` above syncs it — that restarts the container.
