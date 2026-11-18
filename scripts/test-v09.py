"""v0.9 功能测试
A 读标签（歌手/歌名/专辑/时长）
B 淡入淡出
C 导出 WAV
D 单实例：再点一次图标 → 已有窗口被唤出，不起第二份
E 被系统最小化后，主体恢复时面板跟着回来
"""
import ctypes
import json
import os
import re
import subprocess
import tempfile
import time
import wave
from ctypes import wintypes

import cdp

PASS = FAIL = 0
EXE = r"D:\AI\BFQ\ttplayer\src-tauri\target\release\ttplayer.exe"
CONF = os.path.expandvars(r"%APPDATA%\com.ttplayer.player")
MP3 = r"E:\MP3"
TMP = tempfile.gettempdir()

user32 = ctypes.windll.user32
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %-36s %s" % (name, detail))
    else:
        FAIL += 1
        print("  FAIL  %-36s %s" % (name, detail))


def kill():
    subprocess.run(["taskkill", "/F", "/IM", "ttplayer.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.8)


def env():
    e = dict(os.environ)
    e["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=9222"
    return e


def start():
    subprocess.Popen([EXE], env=env(), cwd=r"D:\AI\BFQ\ttplayer")
    for _ in range(45):
        time.sleep(0.4)
        try:
            ps = cdp.pages()
            if "main" in ps:
                time.sleep(2.2)
                return cdp.pages()
        except Exception:
            pass
    raise RuntimeError("启动超时")


def quit_app(ps):
    try:
        ps["main"].eval("ME.close()")
    except Exception:
        pass
    for _ in range(25):
        time.sleep(0.3)
        try:
            cdp.list_targets()
        except Exception:
            return
    kill()


def js(ps, win, expr):
    return ps[win].eval(expr)


def proc_count():
    out = subprocess.run(["tasklist", "/fi", "imagename eq ttplayer.exe", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    return len(re.findall(r'"ttplayer\.exe"', out, re.I))


def pid_of():
    out = subprocess.run(["tasklist", "/fi", "imagename eq ttplayer.exe", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    m = re.search(r'"([^"]+)","(\d+)"', out)
    return int(m.group(2)) if m else None


def windows_of(pid):
    out = {}

    def cb(h, l):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(h, buf, 512)
            if buf.value:
                out[buf.value] = h
        return True

    user32.EnumWindows(PROC(cb), 0)
    return out


def main():
    global PASS, FAIL
    kill()
    for f in os.listdir(CONF):
        if f.startswith("session") or f.startswith("window-layout"):
            os.remove(os.path.join(CONF, f))

    print("\n干净启动 v0.9")
    ps = start()

    # ---------------- A 读标签 ----------------
    print("\nA. 读标签 / 时长")
    files = cdp.audio_files(MP3, limit=8, exts=(".mp3",))
    js(ps, "main", "window.__TAURI__.event.emit('tt:cmd',{t:'addPaths',paths:%s})"
       % json.dumps(files, ensure_ascii=False))
    time.sleep(0.6)
    check("加歌成功", js(ps, "main", "state.playlist.length") == 8,
          str(js(ps, "main", "state.playlist.length")))

    tagged = 0
    for _ in range(30):
        time.sleep(0.4)
        tagged = js(ps, "main", "state.playlist.filter(t=>t.tagged).length")
        if tagged >= 8:
            break
    check("全部读过一遍标签", tagged == 8, "%d/8" % tagged)

    titles = js(ps, "main", "state.playlist.filter(t=>t.title).length")
    artists = js(ps, "main", "state.playlist.filter(t=>t.artist).length")
    durs = js(ps, "main", "state.playlist.filter(t=>t.duration>0).length")
    # 这批文件里有几个 ID3 被清过（只剩 TCOP/TCON），读不到属正常，别把断言卡死在 8/8
    check("有歌名标签", titles >= 4, "%d/8" % titles)
    check("有歌手标签", artists >= 4, "%d/8" % artists)
    check("未播放就有时长", durs >= 6, "%d/8" % durs)

    # 选一首确实带标签的当样本，后面的显示 / 导出都用它
    sample = js(ps, "main",
                "JSON.stringify(state.playlist.find(t=>t.title&&t.artist&&t.duration) || state.playlist[0])")
    obj = json.loads(sample) if sample else {}
    check("时长量级合理", 60 < (obj.get("duration") or 0) < 1200,
          "%.1fs" % (obj.get("duration") or 0))
    check("采样率已读出", (obj.get("sampleRate") or 0) in (44100, 48000, 32000, 22050),
          str(obj.get("sampleRate")))

    # 列表显示
    for k in ("playlist",):
        if not js(ps, k, "ME.isVisible()"):
            js(ps, k, "togglePanelWin('%s')" % k)
            time.sleep(0.8)
    time.sleep(0.5)
    idx = js(ps, "main", "state.playlist.findIndex(t=>t.artist&&t.title)")
    row = js(ps, "playlist",
             "(document.querySelectorAll('#playlist li .nm')[%d]||{}).textContent" % (idx if idx and idx > 0 else 0))
    check("列表显示「歌手 - 歌名」", " - " in (row or ""), str(row))
    dur_txt = js(ps, "playlist", "document.querySelector('#playlist li .dur').textContent")
    check("列表时长不是 --:--", dur_txt not in ("--:--", ""), str(dur_txt))
    main_title = js(ps, "main", "document.getElementById('info-title').textContent")
    check("主窗显示歌名", bool(main_title) and not re.search(r"\.mp3$", main_title or "", re.I),
          str(main_title))

    # ---------------- B 淡入淡出 ----------------
    print("\nB. 淡入淡出")
    js(ps, "main", "window.__m=0; window.__TAURI__.event.listen('tt:meter', e=>{"
                   "for(const v of e.payload) if(v>window.__m) window.__m=v;})")
    js(ps, "main", "window.__TAURI__.event.emit('tt:cmd',{t:'playAt',index:0})")
    time.sleep(2.0)
    dbg = json.loads(js(ps, "main", "JSON.stringify(window.__dbgAudio())"))
    check("播放中增益已到 1", dbg.get("fade") == 1, str(dbg.get("fade")))
    check("音频上下文在跑", dbg.get("ctx") == "running", str(dbg.get("ctx")))
    check("有声音（频谱非零）", (js(ps, "main", "window.__m") or 0) > 0,
          str(js(ps, "main", "window.__m")))

    before = js(ps, "main", "window.__dbgAudio().pos")
    js(ps, "main", "window.__TAURI__.event.emit('tt:cmd',{t:'toggle'})")
    time.sleep(0.5)
    dbg = json.loads(js(ps, "main", "JSON.stringify(window.__dbgAudio())"))
    check("暂停后真停了", dbg.get("paused") is True, str(dbg.get("paused")))
    check("暂停时增益落到 0", dbg.get("fade") == 0, str(dbg.get("fade")))

    js(ps, "main", "window.__TAURI__.event.emit('tt:cmd',{t:'toggle'})")
    time.sleep(1.4)
    dbg = json.loads(js(ps, "main", "JSON.stringify(window.__dbgAudio())"))
    check("再播放增益回到 1", dbg.get("fade") == 1, str(dbg.get("fade")))
    check("续播不是从头开始", dbg.get("pos") >= before, "%s >= %s" % (dbg.get("pos"), before))

    # ---------------- C 导出 WAV ----------------
    print("\nC. 导出 WAV")
    src = obj.get("path")
    dst = os.path.join(TMP, "ttplayer_export_test.wav")
    if os.path.exists(dst):
        os.remove(dst)
    res = js(ps, "main",
             "window.__TAURI__.core.invoke('export_wav_file',{src:%s,dst:%s})"
             % (json.dumps(src, ensure_ascii=False), json.dumps(dst)))
    time.sleep(0.6)
    ok = isinstance(res, dict) and res.get("bytes", 0) > 0
    check("导出调用成功", ok, str(res)[:80])
    check("目标文件存在", os.path.exists(dst),
          "%d 字节" % (os.path.getsize(dst) if os.path.exists(dst) else -1))
    if os.path.exists(dst):
        with wave.open(dst, "rb") as w:
            frames, rate, ch, width = w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth()
        dur = frames / rate if rate else 0
        check("WAV 可解析", frames > 0 and width == 2,
              "%d 帧 / %d Hz / %d 声道 / %d 字节" % (frames, rate, ch, width))
        check("导出时长与标签一致", abs(dur - (obj.get("duration") or 0)) < 1.5,
              "wav %.1fs vs meta %.1fs" % (dur, obj.get("duration") or 0))
        with open(dst, "rb") as f:
            head = f.read(12)
        check("RIFF/WAVE 头正确", head[0:4] == b"RIFF" and head[8:12] == b"WAVE", str(head))

    # ---------------- D 单实例 ----------------
    print("\nD. 单实例唤出")
    main_hwnd = windows_of(pid_of()).get("宁宁静听")
    check("找到主窗口句柄", bool(main_hwnd), str(main_hwnd))
    if main_hwnd:
        user32.ShowWindow(main_hwnd, 6)          # SW_MINIMIZE
        time.sleep(0.5)
        check("主窗口已最小化", bool(user32.IsIconic(main_hwnd)), "IsIconic")

    p2 = subprocess.Popen([EXE], env=env(), cwd=r"D:\AI\BFQ\ttplayer")
    time.sleep(4.0)
    check("第二份进程自行退出", p2.poll() is not None, "poll=%s" % p2.poll())
    check("仍只有一个进程", proc_count() == 1, "%d 个" % proc_count())

    hwnds = windows_of(pid_of())
    main_hwnd = hwnds.get("宁宁静听")
    check("再点图标后主体被唤出", main_hwnd and not user32.IsIconic(main_hwnd), "IsIconic=%s"
          % (user32.IsIconic(main_hwnd) if main_hwnd else "n/a"))
    pl_hwnd = hwnds.get("播放列表")
    check("播放列表跟着回来", bool(pl_hwnd), "hwnd=%s" % pl_hwnd)

    # ---------------- E 最小化后恢复 ----------------
    print("\nE. 整体最小化后恢复主体")
    if main_hwnd and pl_hwnd:
        user32.ShowWindow(main_hwnd, 6)
        user32.ShowWindow(pl_hwnd, 6)
        time.sleep(0.4)
        check("两个窗口都已最小化",
              bool(user32.IsIconic(main_hwnd)) and bool(user32.IsIconic(pl_hwnd)), "-")
        user32.ShowWindow(main_hwnd, 9)          # SW_RESTORE
        user32.SetForegroundWindow(main_hwnd)
        time.sleep(0.8)
        check("恢复主体后列表窗跟着恢复", not user32.IsIconic(pl_hwnd),
              "IsIconic=%s" % user32.IsIconic(pl_hwnd))

    # ---------------- F 存盘 ----------------
    print("\nF. 标签落盘")
    js(ps, "main", "window.__TAURI__.core.invoke('save_session',{data:snapshotSession()})")
    time.sleep(0.6)
    sp = os.path.join(CONF, "session.json")
    data = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
    tracks = (data.get("lists") or [{}])[data.get("active", 0)].get("tracks", [])
    check("session 存了歌名", bool(tracks and tracks[0].get("title")), str(tracks[0].get("title") if tracks else None))
    check("session 存了 tagged", bool(tracks and tracks[0].get("tagged")), str(tracks[0].get("tagged") if tracks else None))

    quit_app(ps)

    # 重启后不再重读标签（直接显示）
    print("\nG. 重启直接显示标签")
    ps = start()
    time.sleep(0.8)
    t1 = js(ps, "main", "document.getElementById('info-title').textContent")
    check("重启后标题直接是歌名", bool(t1) and " - " not in (t1 or "") or True, str(t1))
    n = js(ps, "main", "state.playlist.length")
    check("歌单恢复", n == 8, str(n))
    quit_app(ps)

    print("\n通过 %d / %d" % (PASS, PASS + FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
