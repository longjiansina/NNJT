"""截取播放器某个窗口的图像（先提到前台，避免被遮挡）"""
import ctypes
import re
import subprocess
import sys
import time
from ctypes import wintypes
from PIL import ImageGrab

user32 = ctypes.windll.user32
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
NAME = sys.argv[1] if len(sys.argv) > 1 else "宁宁静听"
OUT = sys.argv[2] if len(sys.argv) > 2 else r"D:/AI/BFQ/ttplayer/shot.png"


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


out = subprocess.run(["tasklist", "/fi", "imagename eq ttplayer.exe", "/fo", "csv", "/nh"],
                     capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
m = re.search(r'"([^"]+)","(\d+)"', out)
if not m:
    print("NO PROCESS")
    raise SystemExit
TARGET = int(m.group(2))

target_hwnd = [None]


def cb(h, l):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    if pid.value == TARGET:
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, buf, 512)
        if buf.value == NAME:
            target_hwnd[0] = h
            return False
    return True


user32.EnumWindows(PROC(cb), 0)
h = target_hwnd[0]
if not h:
    print("窗口未找到:", NAME)
    raise SystemExit

user32.ShowWindow(h, 9)          # SW_RESTORE
user32.SetForegroundWindow(h)
time.sleep(0.8)

r = RECT()
user32.GetWindowRect(h, ctypes.byref(r))
img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
img = img.resize((img.width * 2, img.height * 2), 1)
img.save(OUT)
print(f"saved {OUT} {img.size} 原窗口 {r.right - r.left}x{r.bottom - r.top} @ ({r.left},{r.top})")
