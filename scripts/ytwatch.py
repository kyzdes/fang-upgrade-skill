#!/usr/bin/env python3
"""ytwatch — deterministic YouTube channel -> transcript helper for OpenFang.

Stdlib + the `yt-dlp` binary only. Designed to be driven by an OpenFang agent
through `shell_exec`, which forbids shell metacharacters (| > < ; & ` $( ${ { }),
so every invocation here is a flat argv with no redirection and all output is
compact JSON on stdout.

Subcommands
  resolve  <channel>                 -> {"channel_id": "UC..."}
  list     <channel> [--max N]       -> {"videos":[{id,title,published,url}]}
  new      <channel> [--max N] [--state P] [--min-secs S]
                                     -> only videos not yet in the state file
  fetch    <video_id> [--outdir D] [--lang en] [--stride 30]
                                     -> writes D/<id>.txt, prints stats
  mark     <video_id> [--state P] [--note TEXT]
  state    [--state P]               -> dump state summary
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
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
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
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
        except Exception as e:  # transient DNS / routing / 5xx
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
    m = re.search(r'"externalId":"(UC[0-9A-Za-z_-]{22})"', html) or re.search(
        r'channel_id=(UC[0-9A-Za-z_-]{22})', html
    )
    if not m:
        raise RuntimeError("could not resolve channel_id from " + c)
    return m.group(1)


def list_videos(channel_id, limit=15):
    root = ET.fromstring(http_get(FEED + channel_id))
    vids = []
    for e in root.findall("a:entry", NS)[:limit]:
        vid = e.findtext("yt:videoId", namespaces=NS)
        vids.append(
            {
                "id": vid,
                "title": (e.findtext("a:title", namespaces=NS) or "").strip(),
                "published": e.findtext("a:published", namespaces=NS),
                "url": "https://www.youtube.com/watch?v=" + vid,
            }
        )
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
    os.replace(tmp, path)


def json3_to_text(path, stride=30):
    """Flatten a YouTube json3 caption file to timestamped plain text."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    lines, cur, cur_start, last_stamp = [], [], None, -10**9
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
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
        p = subprocess.run(
            ["yt-dlp", "--force-ipv4", "--no-progress"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        rc, so, se = p.returncode, p.stdout, p.stderr
        if rc == 0:
            return rc, so, se
        if "unavailable" in se or "Private video" in se or "members-only" in se:
            return rc, so, se  # permanent, do not retry
        if i + 1 < attempts:
            time.sleep(3)
    return rc, so, se


def cmd_fetch(a):
    os.makedirs(a.outdir, exist_ok=True)
    stem = os.path.join(a.outdir, a.video_id)
    url = "https://www.youtube.com/watch?v=" + a.video_id

    rc, so, se = ytdlp(
        ["--dump-json", "--no-playlist", "--skip-download", url], timeout=a.timeout
    )
    meta = {}
    if rc == 0 and so.strip():
        j = json.loads(so.splitlines()[0])
        meta = {
            "title": j.get("title"),
            "duration": j.get("duration"),
            "upload_date": j.get("upload_date"),
            "channel": j.get("channel"),
            "view_count": j.get("view_count"),
            "has_manual_subs": bool(j.get("subtitles")),
            "auto_caption_langs": sorted(list((j.get("automatic_captions") or {}).keys()))[:5],
        }

    rc, so, se = ytdlp(
        [
            "--write-auto-subs", "--write-subs",
            "--sub-langs", a.lang,
            "--sub-format", "json3",
            "--skip-download", "--no-playlist",
            "-o", stem,
            url,
        ],
        timeout=a.timeout,
    )
    cands = [stem + "." + a.lang + ".json3", stem + ".en.json3"]
    src = next((c for c in cands if os.path.exists(c)), None)
    if src is None:
        for f in os.listdir(a.outdir):
            if f.startswith(a.video_id) and f.endswith(".json3"):
                src = os.path.join(a.outdir, f)
                break
    if src is None:
        out(
            {
                "ok": False,
                "video_id": a.video_id,
                "error": "no_captions",
                "meta": meta,
                "stderr": se[-800:],
            },
            2,
        )

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

    out(
        {
            "ok": True,
            "video_id": a.video_id,
            "meta": meta,
            "transcript_path": txt_path,
            "chars": len(text),
            "approx_tokens": len(text) // 4,
            "lines": text.count("\n") + 1,
            "parts": parts,
        }
    )


_YT_ID_RE = re.compile(r"^-[A-Za-z0-9_-]{10}$")


def _rescue_leading_dash_id(argv):
    """Идентификаторы YouTube бывают с ведущим дефисом, и argparse читает такой
    токен как связку коротких флагов.

    `ytwatch fetch -fF1ufNJoho` умирает на
    "the following arguments are required: video_id", потому что `-fF1ufNJoho`
    разбирается как -f -F -1 -u ... Ролик при этом настоящий: он был первым в
    очереди 2026-08-25 и заблокировал весь прогон — агент повторял ту же команду,
    пока не выбрал 50 итераций, и до остальных двенадцати не дошёл.

    Форма, которую argparse принимает, — флаги, затем `--`, затем значение
    (проверено прогоном). Приводим argv к ней сами, чтобы вызывающему не надо
    было знать этой тонкости.

    Распознаём по форме, а не по позиции: ровно 11 знаков алфавита YouTube,
    первый из которых дефис. Ни один флаг ytwatch такой формы не имеет — все
    длинные (`--stride`) либо `-h`, — поэтому ложное срабатывание невозможно.
    """
    if len(argv) < 2 or argv[0] not in ("fetch", "mark"):
        return argv
    ids = [i for i, tok in enumerate(argv[1:], 1) if _YT_ID_RE.match(tok)]
    if not ids:
        return argv
    i = ids[0]
    return argv[:i] + argv[i + 1:] + ["--", argv[i]]


class _JsonArgParser(argparse.ArgumentParser):
    """Ошибка разбора аргументов — не повод повторять команду.

    Обычный argparse печатает usage в stderr и выходит с кодом 2. Агент читает
    это как сбой без признака «повторять бессмысленно» и, следуя правилу
    «повтори один раз при сбое», уходит в цикл. Отдаём тот же контракт, что и
    остальные ветви скрипта: JSON с ok=false и явным retryable=false.
    """

    def error(self, message):
        out = {
            "ok": False,
            "error": "bad_arguments",
            "detail": message,
            "retryable": False,
            "hint": "проверь аргументы; повтор той же команды даст тот же результат",
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.exit(2)


def main():
    p = _JsonArgParser(prog="ytwatch")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_JsonArgParser)

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

    a = p.parse_args(_rescue_leading_dash_id(sys.argv[1:]))
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
