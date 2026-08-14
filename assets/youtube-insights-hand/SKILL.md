---
name: youtube-insights-skill
version: "1.0.0"
description: "yt-dlp channel/caption reference and the OpenFang shell_exec constraints that shape it"
runtime: prompt_only
---

# YouTube channel mining — reference

## shell_exec is not a shell

There is no `sh -c`. The command string is split with POSIX lexer rules and the
binary is exec'd directly. These characters are rejected **before** the command
runs, in every exec mode including `full`:

```
`   $(   ${   ;   |   >   <   {   }   &   \n   \r   \0
```

Consequences you will hit:

| You wanted | Blocked because | Do this instead |
|---|---|---|
| `yt-dlp ... \| head -5` | pipe | let `ytwatch.py` produce compact JSON |
| `cmd > out.txt` | redirection | `file_write`, or have the script write the file |
| `yt-dlp -f "bv[height<=1080]"` | `<` inside the format selector | never needed — we do not download video |
| `https://youtu.be/x?v=a&t=30` | `&` | use the bare 11-char video id |
| `a && b` | ampersand | two separate `shell_exec` calls |

`timeout_seconds` is yours to set on each call. The exec policy default for a
hand is 300s; the enclosing cron turn is capped at 600s, so a single run must
fit inside that.

## yt-dlp, captions only

We never download a video. Two calls per video:

```
yt-dlp --dump-json --no-playlist --skip-download https://www.youtube.com/watch?v=ID
yt-dlp --write-auto-subs --write-subs --sub-langs en --sub-format json3 --skip-download --no-playlist -o STEM https://www.youtube.com/watch?v=ID
```

- `--write-subs` before `--write-auto-subs` in preference order: yt-dlp takes
  the human-made track when one exists, which is noticeably cleaner.
- Output lands at `STEM.en.json3`.
- Two harmless warnings always appear and are not errors:
  `No supported JavaScript runtime could be found` (deno is absent; the
  android-vr client path still returns captions) and `ffmpeg not found`
  (irrelevant — nothing is being muxed).
- `ERROR: ... This video is unavailable` and a missing `.json3` mean no usable
  caption track. Mark the video skipped and move on.

## Channel listing

`--flat-playlist` on `/@handle/videos` returns ids and titles but
`upload_date`, `timestamp` and `channel_id` are all `NA`, so it cannot answer
"what is new". The channel's Atom feed can:

```
https://www.youtube.com/feeds/videos.xml?channel_id=UC...
```

15 most recent entries, newest first, each with `<yt:videoId>` and a real
`<published>` timestamp. `ytwatch.py new` already does this and diffs against
`seen.json`.

## json3 caption format

```json
{"events": [
  {"tStartMs": 1230, "dDurationMs": 5000,
   "segs": [{"utf8": "hello ", "tOffsetMs": 0}, {"utf8": "world", "tOffsetMs": 200}]}
]}
```

- Word start = `tStartMs + tOffsetMs` milliseconds.
- Events with no `segs` are positioning records — skip them.
- The file is enormous relative to its text: a 3h46m video is 4.3 MB of json3
  that flattens to 222 KB of prose. **Never read the json3 with `file_read`** —
  a bare call (no `offset`/`limit`) still `read_to_string`s and returns the whole file with no
  truncation; passing `offset`/`limit` avoids returning it all at once, but you'd have to know
  where to cut a json3 blob to get valid JSON back, which isn't worth it here. Flatten it with
  a script instead.

## Size budget

| Video length | Flattened transcript | Approx tokens |
|---|---|---|
| 5 min | ~6 KB | ~1.5k |
| 30 min | ~35 KB | ~9k |
| 3h45m | ~222 KB | ~56k |

Tool results are capped at 30% of the context window (78,643 chars at 131,072
tokens). `ytwatch.py fetch` therefore splits anything long into
`.partNN.txt` pieces. Read a part, write your notes for it to disk, then read
the next part. Do not try to hold four parts in one conversation — the context
guard will compact the earliest ones out from under you.
