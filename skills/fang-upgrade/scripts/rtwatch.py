#!/usr/bin/env python3
"""RuTube intake: list a channel, pull auto-subtitles, flatten to timestamped text.

The YouTube sibling (ytwatch.py) cannot be reused as-is because RuTube differs in three ways:
  * no RSS feed — the channel listing comes from yt-dlp --flat-playlist
  * subtitles arrive as **srt**, not json3, and sit under `subtitles`, not `automatic_captions`,
    so the flag is --write-subs (--write-auto-subs finds nothing)
  * video ids are 32-char hex, not 11-char base64

Same design constraints as ytwatch.py: flat argv only (shell_exec rejects metacharacters), one
JSON object per invocation on stdout, state in a plain file.

    rtwatch.py list    <channel_url> [--max N]
    rtwatch.py fetch   <video_id> [--outdir DIR] [--stride SEC] [--timeout SEC]
    rtwatch.py new     <channel_url> [--max N] [--state FILE]
    rtwatch.py mark    <video_id> [--state FILE] [--note TEXT]
    rtwatch.py state   [--state FILE]
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys

# The host resolves AAAA records but has no IPv6 route, which surfaces as an intermittent
# "[Errno 101] Network is unreachable". Pin every lookup to IPv4.
_gai = socket.getaddrinfo


def _gai4(host, port, family=0, type=0, proto=0, flags=0):
    res = _gai(host, port, socket.AF_INET, type, proto, flags)
    return res or _gai(host, port, family, type, proto, flags)


socket.getaddrinfo = _gai4

VIDEO_URL = "https://rutube.ru/video/%s/"
DEFAULT_STATE = "seen_rt.json"


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)


def ytdlp(args, timeout=300, attempts=2):
    last = (1, "", "never ran")
    for _ in range(attempts):
        try:
            p = subprocess.run(["yt-dlp", *args], capture_output=True, text=True, timeout=timeout)
            if p.returncode == 0:
                return 0, p.stdout, p.stderr
            last = (p.returncode, p.stdout, p.stderr)
        except subprocess.TimeoutExpired:
            last = (124, "", "timeout after %ss" % timeout)
    return last


def load_state(path):
    if not os.path.exists(path):
        return {"seen": {}}
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    st.setdefault("seen", {})
    return st


def save_state(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def list_videos(channel_url, limit=25, timeout=600):
    rc, so, se = ytdlp(["--flat-playlist", "--dump-json", "--playlist-end", str(limit), channel_url],
                       timeout=timeout)
    if rc != 0:
        raise RuntimeError("yt-dlp list failed: " + se.strip()[-300:])
    vids = []
    for line in so.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        vids.append({
            "id": d.get("id"),
            "title": d.get("title"),
            "duration": d.get("duration") or 0,
            "url": VIDEO_URL % d.get("id"),
        })
    return vids


TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->")


def _stamp(t):
    return "[%02d:%02d:%02d]" % (t // 3600, (t % 3600) // 60, t % 60)


def parse_srt(path):
    """Yield (start_seconds, text) per cue.

    Parsed block-wise rather than line-wise on purpose: an SRT cue is index / timestamp / text /
    blank, and a line-wise filter cannot tell a cue's index number from a line of speech. Doing it
    line-wise leaks every index into the transcript ("2 Так меня слышно. 3 Так включила звук.").
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n\s*\n", raw):
        rows = [r for r in block.split("\n") if r.strip()]
        if not rows:
            continue
        ts_at = next((i for i, r in enumerate(rows) if TS.match(r)), None)
        if ts_at is None:
            continue
        h, mi, s, _ms = (int(x) for x in TS.match(rows[ts_at]).groups())
        text = " ".join(r.strip() for r in rows[ts_at + 1:] if r.strip())
        if text:
            yield h * 3600 + mi * 60 + s, text


def srt_to_text(path, stride=30):
    """Flatten an SRT to one timestamped line per `stride` seconds of speech.

    RuTube's auto-subs are one short clause per cue, so emitting them verbatim would give thousands
    of near-empty lines. Grouping into ~stride-second blocks keeps the transcript readable while the
    timestamps stay precise enough to cite.
    """
    lines, cur, cur_start = [], [], None
    for t, text in parse_srt(path):
        if cur_start is None:
            cur_start = t
        if t - cur_start >= stride and cur:
            lines.append("%s %s" % (_stamp(cur_start), " ".join(cur)))
            cur, cur_start = [], t
        cur.append(text)
    if cur:
        lines.append("%s %s" % (_stamp(cur_start or 0), " ".join(cur)))
    return "\n".join(lines)


def cmd_fetch(a):
    os.makedirs(a.outdir, exist_ok=True)
    stem = os.path.join(a.outdir, a.video_id)
    url = VIDEO_URL % a.video_id

    meta = {}
    rc, so, _se = ytdlp(["--dump-json", "--skip-download", "--no-playlist", url], timeout=a.timeout)
    if rc == 0 and so.strip():
        j = json.loads(so.splitlines()[0])
        meta = {
            "title": j.get("title"),
            "duration": j.get("duration"),
            "upload_date": j.get("upload_date"),
            "description": (j.get("description") or "")[:600],
            "sub_langs": sorted((j.get("subtitles") or {}).keys()),
        }

    # RuTube exposes its machine-generated track under `subtitles`, so --write-subs is correct here.
    rc, _so, se = ytdlp(["--write-subs", "--sub-langs", a.lang, "--sub-format", "srt",
                         "--skip-download", "--no-playlist", "-o", stem, url], timeout=a.timeout)
    srt = None
    for cand in (stem + "." + a.lang + ".srt", stem + ".ru.srt"):
        if os.path.exists(cand):
            srt = cand
            break
    if not srt:
        out({"ok": False, "video_id": a.video_id, "error": "no subtitle file produced",
             "meta": meta, "stderr": se.strip()[-200:]}, 1)

    text = srt_to_text(srt, stride=a.stride)
    txt_path = stem + ".txt"
    header = "# %s\n# %s | %s sec | %s\n\n" % (meta.get("title") or a.video_id, url,
                                               meta.get("duration"), meta.get("upload_date") or "")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(header + text + "\n")
    if not a.keep_srt:
        os.remove(srt)
    out({"ok": True, "video_id": a.video_id, "txt": txt_path, "chars": len(text),
         "lines": text.count("\n") + 1, "meta": meta})


def main():
    p = argparse.ArgumentParser(prog="rtwatch")
    sub = p.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("list"); l.add_argument("channel"); l.add_argument("--max", type=int, default=25)
    n = sub.add_parser("new"); n.add_argument("channel"); n.add_argument("--max", type=int, default=25)
    n.add_argument("--state", default=DEFAULT_STATE)
    m = sub.add_parser("mark"); m.add_argument("video_id")
    m.add_argument("--state", default=DEFAULT_STATE); m.add_argument("--note", default="")
    s = sub.add_parser("state"); s.add_argument("--state", default=DEFAULT_STATE)
    f = sub.add_parser("fetch"); f.add_argument("video_id")
    f.add_argument("--outdir", default="transcripts"); f.add_argument("--lang", default="ru")
    f.add_argument("--stride", type=int, default=30); f.add_argument("--timeout", type=int, default=300)
    f.add_argument("--keep-srt", action="store_true")

    a = p.parse_args()
    try:
        if a.cmd == "list":
            out({"ok": True, "videos": list_videos(a.channel, a.max)})
        if a.cmd == "new":
            st = load_state(a.state)
            vids = list_videos(a.channel, a.max)
            fresh = [v for v in vids if v["id"] not in st["seen"]]
            out({"ok": True, "total": len(vids), "new": len(fresh), "videos": fresh})
        if a.cmd == "mark":
            st = load_state(a.state)
            st["seen"][a.video_id] = {"note": a.note}
            save_state(a.state, st)
            out({"ok": True, "marked": a.video_id, "total_seen": len(st["seen"])})
        if a.cmd == "state":
            st = load_state(a.state)
            out({"ok": True, "total_seen": len(st["seen"]), "ids": sorted(st["seen"])})
        if a.cmd == "fetch":
            cmd_fetch(a)
    except Exception as e:
        out({"ok": False, "error": type(e).__name__ + ": " + str(e)[:300]}, 1)


if __name__ == "__main__":
    main()
