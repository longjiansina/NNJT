import ctypes, os, re, subprocess, sys, time
from ctypes import wintypes
from PIL import ImageGrab
import cdp

EXE = r"D:/AI/BFQ/ttplayer/src-tauri/target/release/ttplayer.exe"
OUT = r"D:/AI/BFQ/ttplayer"
user32 = ctypes.windll.user32
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

def kill():
    subprocess.run(["taskkill", "/F", "/IM", "ttplayer.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.6)

def pid_of():
    out = subprocess.run(["tasklist", "/fi", "imagename eq ttplayer.exe", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    m = re.search(r'"([^"]+)","(\d+)"', out)
    return int(m.group(2)) if m else None

def hwnd_of(title, pid):
    found = [None]
    def cb(h, l):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid:
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(h, buf, 512)
            if buf.value == title:
                found[0] = h
                return False
        return True
    user32.EnumWindows(PROC(cb), 0)
    return found[0]

def grab(title, path):
    pid = pid_of()
    h = hwnd_of(title, pid)
    if not h:
        print("窗口未找到:", title); return False
    user32.ShowWindow(h, 9)
    user32.SetForegroundWindow(h)
    time.sleep(0.9)
    r = RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
    img = img.resize((img.width * 3, img.height * 3), 1)
    img.save(path)
    print("saved", path, img.size, "win %dx%d" % (r.right - r.left, r.bottom - r.top))
    return True

kill()
e = dict(os.environ)
e["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=9222"
subprocess.Popen([EXE], env=e, cwd=r"D:/AI/BFQ/ttplayer")
for _ in range(45):
    time.sleep(0.4)
    try:
        if "main" in cdp.pages():
            break
    except Exception:
        pass
time.sleep(2.5)
ps = cdp.pages()
m = ps["main"]
m.eval("window.__TAURI__.core.invoke('open_about')")
time.sleep(3.0)
ps = cdp.pages()
print("pages:", sorted(ps))
grab("关于 宁宁静听", os.path.join(OUT, "shot-v013-about.png"))
grab("宁宁静听", os.path.join(OUT, "shot-v013-main.png"))
for p in ps.values():
    try: p.close()
    except Exception: pass
kill()
