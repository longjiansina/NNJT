"""批量抓取 LRC 歌词并保存到歌曲同目录（同名 .lrc）

用法：
  python fetch-lyrics.py "E:\\MP3"            # 跳过已有 .lrc
  python fetch-lyrics.py "E:\\MP3" --force    # 重新覆盖
  python fetch-lyrics.py "E:\\MP3" --limit 20 # 只跑前 20 首（试跑）
  python fetch-lyrics.py "E:\\MP3" --dry      # 只报告匹配结果，不写文件
"""
import argparse
import concurrent.futures as futures
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

from mutagen import File as MutaFile

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

AUDIO_EXT = (".mp3", ".flac", ".wav")

# 版本/伴奏类标记：本地与候选不一致时要扣分
VARIANT_WORDS = ["伴奏", "和声", "纯音乐", "inst", "karaoke", "live", "现场",
                 "变速", "降调", "升调", "dj", "remix", "cover", "翻唱",
                 "铃声", "片段", "抖音", "女声版", "男声版", "钢琴版"]

_print_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_req = [0.0]
MIN_GAP = 0.38          # 全局限速：网易云匿名接口很敏感，快了就 429


def pace():
    with _rate_lock:
        wait = MIN_GAP - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()


def log(*a):
    with _print_lock:
        print(*a, flush=True)


class FetchError(Exception):
    pass


def get_json(url, referer=None, timeout=12, retries=3):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if referer:
        h["Referer"] = referer
    last = None
    for i in range(retries + 1):
        pace()
        try:
            req = urllib.request.Request(url, headers=h)
            with OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(3.0 * (i + 1) ** 1.5)
            elif e.code >= 500:
                time.sleep(1.2 * (i + 1))
            else:
                break
        except Exception as e:
            last = e
            time.sleep(0.8 * (i + 1))
    raise FetchError(str(last)[:90])


def post_json(url, body, referer=None, timeout=15, retries=3):
    h = {"User-Agent": UA, "Accept": "application/json",
         "Content-Type": "application/x-www-form-urlencoded", "Cookie": "os=pc; NMTID=1"}
    if referer:
        h["Referer"] = referer
    last = None
    for i in range(retries + 1):
        pace()
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            last = e
            time.sleep(3.0 * (i + 1) ** 1.5 if e.code in (429, 405) else 1.2 * (i + 1))
        except Exception as e:
            last = e
            time.sleep(0.8 * (i + 1))
    raise FetchError(str(last)[:90])


# ---------------- 元信息 ----------------
def norm(s):
    """归一化用于比较：全角转半角、去空格标点、小写"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[\(\（\[【].*?[\)\）\]】]", "", s)   # 去掉括号内容
    s = re.sub(r"[\s\-_·.,，、'\"!！?？/&+]", "", s)
    return s.strip()


def search_title(t):
    """把「歌名-《剧名》插曲」「歌名 (伴奏版)」这类后缀剥掉，提高搜索命中"""
    o = t
    t = re.sub(r"[-–—]\s*《[^》]*》.*$", "", t)
    t = re.sub(r"[（(【\[].*?[）)】\]]", "", t)
    t = t.strip(" -–—_·")
    return t or o


def variants(s):
    t = unicodedata.normalize("NFKC", s or "").lower()
    return {w for w in VARIANT_WORDS if w in t}


def is_lyric_meta(line):
    """网易云歌词头部的制作人信息行，保留但截断过长"""
    return bool(re.match(r"^\[\d\d:\d\d(\.\d+)?\][^\[\]]{0,4}(作词|作曲|编曲|制作|混音|母带|和声|录音|监制|出品|发行|统筹|吉他|贝斯|鼓|弦乐|出品人|原唱|演唱)", line))


def read_meta(path):
    """读 ID3/Vorbis 标签 + 时长"""
    title = artist = album = None
    dur = 0.0
    try:
        f = MutaFile(path, easy=True)
        if f is not None:
            if f.tags:
                def first(k):
                    v = f.tags.get(k)
                    return v[0] if v else None
                title = first("title")
                artist = first("artist")
                album = first("album")
            if f.info and getattr(f.info, "length", None):
                dur = float(f.info.length)
    except Exception:
        pass

    base = os.path.splitext(os.path.basename(path))[0]
    artists, name = [], base
    if not title or not artist:
        # 文件名解析：a_b-c / a - b / c
        m = re.split(r"\s*[-–—]\s*", base, maxsplit=1)
        if len(m) == 2 and m[0].strip() and m[1].strip():
            left, right = m[0].strip(), m[1].strip()
            right = re.sub(r"^\d{1,3}[.\s]+", "", right)      # 去掉 "02 歐若拉" 的编号
            artists = [x for x in re.split(r"[_&,、]", left) if x.strip()]
            name = right
        else:
            name = re.sub(r"^\d{1,3}[.\s]+", "", base)
    if not title:
        title = name
    if not artist and artists:
        artist = "/".join(artists)
    return {"title": title.strip(), "artist": (artist or "").strip(),
            "album": (album or "").strip(), "dur": dur, "base": base}


# ---------------- 源：网易云 ----------------
def netease_search(kw, limit=20):
    """用 cloudsearch/pc（旧的 get/web 已被 405 封禁）"""
    body = urllib.parse.urlencode({
        "s": kw, "type": "1", "limit": str(limit), "offset": "0", "total": "true",
    }).encode()
    d = post_json("https://music.163.com/api/cloudsearch/pc", body,
                  referer="https://music.163.com/")
    code = d.get("code")
    if code != 200:
        raise FetchError("netease code=%s %s" % (code, d.get("message") or d.get("msg") or ""))
    out = []
    for s in ((d.get("result") or {}).get("songs") or []):
        out.append({
            "src": "netease",
            "title": s.get("name") or "",
            "artists": [a.get("name") for a in (s.get("ar") or s.get("artists") or []) if a.get("name")],
            "dur": (s.get("dt") or s.get("duration") or 0) / 1000.0,
            "id": s.get("id"),
        })
    return out


def netease_lyric(cand):
    url = ("https://music.163.com/api/song/lyric?id=%s&lv=1&kv=1&tv=-1" % cand["id"])
    d = get_json(url, referer="https://music.163.com/")
    code = d.get("code")
    if code not in (200, None):
        raise FetchError("lyric code=%s" % code)
    if d.get("nolyric") or d.get("uncollected"):
        return ""
    return ((d.get("lrc") or {}).get("lyric") or "").strip()


# ---------------- 源：lrclib ----------------
def lrclib_search(kw):
    url = "https://lrclib.net/api/search?q=" + urllib.parse.quote(kw)
    d = get_json(url)
    out = []
    for x in d[:12]:
        if not x.get("syncedLyrics"):
            continue
        out.append({
            "src": "lrclib",
            "title": x.get("trackName") or "",
            "artists": [x.get("artistName") or ""],
            "dur": float(x.get("duration") or 0),
            "id": x.get("id"),
        })
    return out


def lrclib_lyric(cand):
    url = "https://lrclib.net/api/get/%s" % cand["id"]
    d = get_json(url)
    return (d.get("syncedLyrics") or "").strip()


# ---------------- 打分 ----------------
def score(meta, cand):
    s = 0
    mt, ct = norm(meta["title"]), norm(cand["title"])
    if not ct:
        return -999
    if mt == ct:
        s += 50
    elif mt and (mt in ct or ct in mt):
        s += 22
    else:
        # 标题都不像，直接淘汰
        if mt and len(mt) > 2 and mt[:2] not in ct and ct[:2] not in mt:
            return -999

    local_artists = [a for a in re.split(r"[_/&,、]", meta["artist"] or "") if a.strip()]
    cand_artists = [a for a in (cand.get("artists") or []) if a]
    if local_artists:
        hit = 0
        for la in local_artists:
            na = norm(la)
            if not na:
                continue
            for ca in cand_artists:
                nca = norm(ca)
                if na == nca or na in nca or nca in na:
                    hit += 1
                    break
        if hit:
            s += 34 + min(8, (hit - 1) * 4)
        else:
            s -= 26           # 歌手完全不符：大概率是翻唱
    if meta["dur"] and cand["dur"]:
        diff = abs(meta["dur"] - cand["dur"])
        if diff <= 3:
            s += 30
        elif diff <= 6:
            s += 14
        elif diff <= 12:
            s += 2
        elif diff > 20:
            s -= 34

    lv, cv = variants(meta["base"] + meta["title"]), variants(cand["title"] + " ".join(cand_artists))
    if cv - lv:
        s -= 26               # 候选带本地没有的版本标记（伴奏/Live/翻唱…）
    if lv - cv:
        s -= 20
    return s


def loose_ok(meta, cand):
    """严判不过时的兜底：标题完全一致 + 时长贴近，才认（Live/舞蹈版等变体歌词与原版相同）"""
    mt, ct = norm(meta["title"]), norm(cand["title"])
    if not mt or mt != ct:
        return False
    la = [a for a in re.split(r"[_/&,、]", meta["artist"] or "") if a.strip()]
    ca = [a for a in (cand.get("artists") or []) if a]
    hit = False
    for x in la:
        for y in ca:
            nx, ny = norm(x), norm(y)
            if nx and ny and (nx == ny or nx in ny or ny in nx):
                hit = True
                break
        if hit:
            break
    diff = abs(meta["dur"] - cand["dur"]) if (meta["dur"] and cand["dur"]) else 999
    if hit and diff <= 6:
        return True
    if diff <= 2.5:          # 同名 + 时长几乎一致，基本就是同一首（翻唱/合辑）
        return True
    return False


def clean_lrc(text):
    lines = []
    for ln in text.splitlines():
        ln = ln.rstrip()
        if not ln:
            continue
        if re.match(r"^\[(kana|ti|ar|al|by|offset|re|ve|length):", ln, re.I):
            continue
        if ln.startswith("[") and "]" in ln and not re.match(r"^(\[\d+:\d+[.:]\d+\])+", ln):
            continue          # 非时间轴的行（版权声明等）
        lines.append(ln)
    # 至少要有几行带时间轴的才算有效
    timed = [l for l in lines if re.match(r"^\[\d+:\d+", l)]
    if len(timed) < 4:
        return ""
    return "\n".join(lines) + "\n"


# ---------------- 单曲 ----------------
def fetch(path, dry=False, force=False, loose=False):
    lrc_path = os.path.splitext(path)[0] + ".lrc"
    if not force and os.path.exists(lrc_path) and os.path.getsize(lrc_path) > 40:
        return {"path": path, "status": "skip"}

    meta = read_meta(path)
    kw = search_title(meta["title"])
    if meta["artist"]:
        kw2 = "%s %s" % (meta["artist"].split("/")[0], kw)
    else:
        kw2 = kw

    need = 62 if meta["artist"] else 52
    first_artist = (meta["artist"] or "").split("/")[0].split("_")[0].split("&")[0].strip()
    queries = []
    if first_artist:
        queries.append("%s %s" % (first_artist, kw))   # 先带歌手：冷门曲目靠热度排不进前 20
    if kw not in queries:
        queries.append(kw)

    cands, errs, seen = [], [], set()
    for q in queries:
        try:
            pool = netease_search(q)
        except FetchError as e:
            errs.append("网易云 " + str(e))
            continue
        for c in pool:
            key = ("netease", c["id"])
            if key in seen:
                continue
            seen.add(key)
            cands.append(c)
        # 已经有足够好的候选就不用再搜了
        if cands and max(score(meta, c) for c in cands) >= need:
            break

    # 网易云没命中（或无候选）时，再让 lrclib 补一轮
    if not cands or max(score(meta, c) for c in cands) < need:
        try:
            pool = lrclib_search("%s %s" % (first_artist, kw) if first_artist else kw)
            have = {c["id"] for c in cands if c["src"] == "lrclib"}
            cands.extend([c for c in pool if c["id"] not in have])
        except FetchError as e:
            errs.append("lrclib " + str(e))

    if not cands:
        if errs:
            return {"path": path, "status": "error", "why": errs[-1], "meta": meta}
        return {"path": path, "status": "miss", "why": "无搜索结果", "meta": meta}

    ranked = sorted(((score(meta, c), c) for c in cands), key=lambda x: -x[0])
    best_s, best = ranked[0]
    # 没有歌手信息时只能靠标题+时长判断，阈值放宽但要求时长贴近
    if not meta["artist"] and best["dur"] and meta["dur"]:
        if abs(best["dur"] - meta["dur"]) > 10:
            best_s = min(best_s, need - 15)
    if best_s < need:
        if loose:
            picked = next((c for _, c in ranked if loose_ok(meta, c)), None)
            if picked is not None:
                best = picked
            else:
                return {"path": path, "status": "lowscore", "score": best_s,
                        "best": "%s - %s" % ("/".join(best["artists"]), best["title"]),
                        "why": "最高分不足", "meta": meta}
        else:
            return {"path": path, "status": "lowscore", "score": best_s,
                    "best": "%s - %s" % ("/".join(best["artists"]), best["title"]),
                    "why": "最高分不足", "meta": meta}

    try:
        text = netease_lyric(best) if best["src"] == "netease" else lrclib_lyric(best)
    except Exception as e:
        return {"path": path, "status": "error", "why": str(e)[:80], "meta": meta}

    lrc = clean_lrc(text or "")
    if not lrc:
        return {"path": path, "status": "miss", "why": "该曲目无歌词", "meta": meta,
                "best": best["title"]}

    if not dry:
        with open(lrc_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(lrc)
    return {"path": path, "status": "ok", "score": best_s, "src": best["src"],
            "hit": "%s - %s" % ("/".join(best["artists"]), best["title"]),
            "lines": lrc.count("\n"), "meta": meta}


def scan(root):
    out = []
    for dp, _, fs in os.walk(root):
        for fn in fs:
            if fn.lower().endswith(AUDIO_EXT) and "downloading" not in fn.lower():
                out.append(os.path.join(dp, fn))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--loose", action="store_true", help="对严判失败的曲子放宽匹配")
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    files = scan(a.root)
    if a.limit:
        files = files[:a.limit]
    log("共 %d 首，开始匹配歌词…" % len(files))

    results, done = [], 0
    t0 = time.time()
    with futures.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(fetch, p, a.dry, a.force, a.loose): p for p in files}
        for fu in futures.as_completed(futs):
            r = fu.result()
            results.append(r)
            done += 1
            if a.dry:
                tag = r.get("hit") or ("[%s] %s" % (r["status"], r.get("why") or ""))
                m = r.get("meta") or {}
                log("  %-34s (%s|%s|%.0fs) -> %s" % (
                    os.path.basename(r["path"])[:34], (m.get("title") or "")[:14],
                    (m.get("artist") or "-")[:10], m.get("dur") or 0, tag))
            if done % 20 == 0 or done == len(files):
                ok = sum(1 for x in results if x["status"] == "ok")
                log("  进度 %d/%d  成功 %d  用时 %.0fs" % (done, len(files), ok, time.time() - t0))

    ok = [r for r in results if r["status"] == "ok"]
    skip = [r for r in results if r["status"] == "skip"]
    bad = [r for r in results if r["status"] not in ("ok", "skip")]
    log("\n=== 完成：成功 %d / 跳过(已有) %d / 未取到 %d / 共 %d"
        % (len(ok), len(skip), len(bad), len(results)))

    if bad:
        log("\n未取到的曲目：")
        for r in bad[:40]:
            log("  [%s] %s  %s" % (r["status"], os.path.basename(r["path"]),
                                   r.get("why") or r.get("best") or ""))
        if len(bad) > 40:
            log("  …另有 %d 首" % (len(bad) - 40))

    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        log("\n明细已写入 %s" % a.report)


if __name__ == "__main__":
    main()
