"""v0.10 测试
A 真实编码：状态行显示容器里探出的编码（不是扩展名），改过后缀的 FLAC 也认得出
B 遮挡唤出：主体从后台回到前台（等同点任务栏图标）时，
  播放列表 / 歌词 / 均衡器一起提到最前，不再只剩主体
"""
import ctypes
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from ctypes import wintypes

import cdp

PASS = FAIL = 0
EXE = r"D:\AI\BFQ\ttplayer\src-tauri\target\release\ttplayer.exe"
CONF = os.path.expandvars(r"%APPDATA%\com.ttplayer.player")
MP3 = r"E:\MP3"
TMP = tempfile.gettempdir()

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %-38s %s" % (name, detail))
    else:
        FAIL += 1
        print("  FAIL  %-38s %s" % (name, detail))


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


def pid_of():
    out = subprocess.run(["tasklist", "/fi", "imagename eq ttplayer.exe", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    m = re.search(r'"([^"]+)","(\d+)"', out)
    return int(m.group(2)) if m else None


def windows_of(pid):
    """pid 的可见顶层窗口：{标题: hwnd}"""
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


def z_index(hwnd):
    """全屏 Z 序里的位置，0 = 最前"""
    order = []

    def cb(h, l):
        if user32.IsWindowVisible(h):
            order.append(h)
        return True

    user32.EnumWindows(PROC(cb), 0)
    return order.index(hwnd) if hwnd in order else -1


def force_foreground(hwnd, tries=6):
    """抢前台。跨进程 SetForegroundWindow 会被前台锁挡掉，
    先 AttachThreadInput 到当前前台线程，多试几次就稳了。"""
    for _ in range(tries):
        if user32.GetForegroundWindow() == hwnd:
            return True
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None)
        tid_me = kernel32.GetCurrentThreadId()
        if tid_fg:
            user32.AttachThreadInput(tid_me, tid_fg, True)
        ok = user32.SetForegroundWindow(hwnd)
        if tid_fg:
            user32.AttachThreadInput(tid_me, tid_fg, False)
        if ok:
            return True
        pump(0.2)
    return user32.GetForegroundWindow() == hwnd


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


def pump(seconds):
    """抽消息，让覆盖窗口保持「响应」状态；否则它会被系统标成未响应"""
    end = time.time() + seconds
    msg = MSG()
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.02)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def make_overlay():
    """铺满屏幕的普通顶层窗口，用来把整套播放器盖住（不依赖 tkinter）"""
    inst = kernel32.GetModuleHandleW(None)
    name = "TTCoverWindow"

    def proc(h, msg, wp, lp):
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
        return user32.DefWindowProcW(h, msg, wp, lp)

    wndproc = WNDPROC(proc)          # 留个引用，别被 GC 掉
    wc = WNDCLASS()
    wc.lpfnWndProc = wndproc
    wc.hInstance = inst
    wc.lpszClassName = name
    wc.hbrBackground = ctypes.cast(6, wintypes.HBRUSH)   # COLOR_WINDOW
    user32.RegisterClassW(ctypes.byref(wc))

    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    hwnd = user32.CreateWindowExW(
        0, name, "cover", 0x00CF0000, 0, 0, sw, sh, None, None, inst, None)
    user32.ShowWindow(hwnd, 5)       # SW_SHOW
    user32.UpdateWindow(hwnd)
    make_overlay.proc = wndproc
    time.sleep(0.3)
    return hwnd



def find_fake_ext():
    """找一个「后缀是 .mp3（或别的），内容其实是 FLAC」的文件"""
    if not os.path.isdir(MP3):
        return None
    for p in cdp.audio_files(MP3, exts=(".mp3",)):
        try:
            with open(p, "rb") as fh:
                if fh.read(4) == b"fLaC":
                    return p
        except OSError:
            continue
    return None


def main():
    global PASS, FAIL
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

    # 用户的真实歌单先备份：测试要干净启动，跑完原样还回去
    backup = os.path.join(TMP, "ttplayer_conf_backup")
    os.makedirs(backup, exist_ok=True)
    saved = []
    for f in os.listdir(CONF) if os.path.isdir(CONF) else []:
        if f.startswith("session") or f.startswith("window-layout"):
            src = os.path.join(CONF, f)
            shutil.copy2(src, os.path.join(backup, f))
            saved.append(f)
            os.remove(src)

    kill()
    root = None
    ps = None
    try:
        print("\n干净启动 v0.10")
        ps = start()

        # ---------------- A 真实编码 ----------------
        print("\nA. 状态行显示真实编码")
        files = cdp.audio_files(MP3, limit=8, exts=(".mp3",))
        fake = find_fake_ext()
        if fake and fake not in files:
            files = [fake] + files
        js(ps, "main", "window.__TAURI__.event.emit('tt:cmd',{t:'addPaths',paths:%s})"
           % json.dumps(files, ensure_ascii=False))

        for _ in range(40):
            time.sleep(0.4)
            if js(ps, "main", "state.playlist.filter(t=>t.tagged).length") >= len(files):
                break

        codecs = js(ps, "main", "JSON.stringify(state.playlist.map(t=>t.codec))")
        arr = json.loads(codecs or "[]")
        check("每首都有 codec", all(arr) and len(arr) == len(files), str(arr))
        check("MP3 认成 MP3", "MP3" in arr, str(sorted(set(arr))))

        if fake:
            j = js(ps, "main",
                   "JSON.stringify(state.playlist.find(t=>t.path===%s)||{})"
                   % json.dumps(fake, ensure_ascii=False))
            o = json.loads(j or "{}")
            check("伪装成 .mp3 的 FLAC 认成 FLAC",
                  o.get("codec") == "FLAC" and o.get("name", "").lower().endswith(".mp3"),
                  "%s -> %s" % (o.get("name"), o.get("codec")))
            # 选中它，看状态行实际渲染
            js(ps, "main", "state.curr=state.playlist.findIndex(t=>t.path===%s);"
                           "broadcast(true)" % json.dumps(fake, ensure_ascii=False))
            time.sleep(0.6)
            txt = js(ps, "main", "document.getElementById('stat-meta').textContent")
            check("状态行写的是 FLAC 不是 MP3",
                  "FLAC" in (txt or "") and "MP3" not in (txt or ""), str(txt))
            check("状态行带采样率", "kHz" in (txt or ""), str(txt))
            check("状态行带位深", "bit" in (txt or ""), str(txt))
        else:
            print("  SKIP  没在 E:\\MP3 里找到伪装后缀的 FLAC")

        # 编码要能存进 session，否则下次启动又退回扩展名
        time.sleep(0.6)
        sess = ""
        try:
            with open(os.path.join(CONF, "session.json"), "r", encoding="utf-8") as fh:
                sess = fh.read()
        except OSError:
            pass
        check("session 里存了 codec", '"codec"' in sess, "")

        # ---------------- B 被遮挡后唤出全部窗口 ----------------
        print("\nB. 后台唤出：面板跟着主体一起回来")
        for k in ("playlist", "lyric", "eq"):
            if not js(ps, k, "ME.isVisible()"):
                js(ps, k, "togglePanelWin('%s')" % k)
                time.sleep(0.9)
        time.sleep(0.8)

        pid = pid_of()
        hw = windows_of(pid)
        main_h = hw.get("宁宁静听")
        pl_h, ly_h, eq_h = hw.get("播放列表"), hw.get("歌词秀"), hw.get("均衡器")
        check("四个窗口都在", all([main_h, pl_h, ly_h, eq_h]),
              "main=%s pl=%s ly=%s eq=%s" % (bool(main_h), bool(pl_h), bool(ly_h), bool(eq_h)))

        ov = make_overlay()
        check("覆盖窗口拿到前台", force_foreground(ov), "")
        pump(1.0)
        covered = z_index(main_h) > z_index(ov)
        check("覆盖窗口已经把主体压下去", covered,
              "main#%d overlay#%d" % (z_index(main_h), z_index(ov)))

        # 用户点任务栏图标 == 系统把主体提到前台
        got = force_foreground(main_h)
        check("主体抢回前台（等同点任务栏）", got, "")
        pump(1.2)

        zi_main, zi_ov = z_index(main_h), z_index(ov)
        check("主体回到覆盖窗口之上", zi_main < zi_ov, "main#%d overlay#%d" % (zi_main, zi_ov))
        for name, h in (("播放列表", pl_h), ("歌词秀", ly_h), ("均衡器", eq_h)):
            if not h:
                continue
            zi = z_index(h)
            check("%s 也回来了" % name, zi < zi_ov,
                  "z=#%d overlay#%d" % (zi, zi_ov))

        # 三个面板仍然各自可见、位置没被挪乱
        for k in ("playlist", "lyric", "eq"):
            check("%s 仍可见" % k, bool(js(ps, k, "ME.isVisible()")), "")

    finally:
        if root:
            try:
                user32.DestroyWindow(root)
            except Exception:
                pass
        if ps:
            quit_app(ps)
        kill()
        # 还回用户原来的歌单 / 布局
        for f in saved:
            try:
                shutil.copy2(os.path.join(backup, f), os.path.join(CONF, f))
            except OSError:
                pass

    print("\n合计: %d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
