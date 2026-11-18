"""探测歌词源可用性与命中率（网易云 / lrclib / QQ音乐）"""
import json
import os
import urllib.parse
import urllib.request

for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy"):
    os.environ.pop(k, None)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def get(url, headers=None, timeout=12):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with OPENER.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def netease(kw, limit=5):
    url = ("https://music.163.com/api/search/get/web?s=%s&type=1&limit=%d&offset=0"
           % (urllib.parse.quote(kw), limit))
    d = json.loads(get(url, {"Referer": "https://music.163.com/"}))
    songs = (d.get("result") or {}).get("songs") or []
    return [(s["name"], "/".join(a["name"] for a in s.get("artists", [])), s["id"],
             s.get("duration", 0) // 1000) for s in songs]


def netease_lyric(song_id):
    url = ("https://music.163.com/api/song/lyric?id=%s&lv=1&kv=1&tv=-1" % song_id)
    d = json.loads(get(url, {"Referer": "https://music.163.com/"}))
    return (d.get("lrc") or {}).get("lyric") or ""


def lrclib(q):
    url = "https://lrclib.net/api/search?q=" + urllib.parse.quote(q)
    d = json.loads(get(url))
    return [(x.get("trackName"), x.get("artistName"), x.get("id"),
             x.get("duration"), bool(x.get("syncedLyrics"))) for x in d[:5]]


TESTS = [
    ("王心凌", "Honey"),
    ("周深", "微光海洋"),
    ("单依纯", "全世界失眠"),
    ("张韶涵", "欧若拉"),
    ("周深", "大鱼"),
]

for artist, title in TESTS:
    print(f"\n=== {artist} - {title}")
    try:
        r = netease(f"{artist} {title}")
        print("  网易云:", r[:3])
        if r:
            ly = netease_lyric(r[0][2])
            print("  歌词行数:", len(ly.splitlines()), "| 首行:", (ly.splitlines() or [""])[0][:40])
    except Exception as e:
        print("  网易云失败:", type(e).__name__, str(e)[:120])
    try:
        print("  lrclib:", lrclib(f"{artist} {title}")[:2])
    except Exception as e:
        print("  lrclib 失败:", type(e).__name__, str(e)[:120])
