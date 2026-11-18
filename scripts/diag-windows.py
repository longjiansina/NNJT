import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]

def get_pid(name):
    import subprocess, re
    out = subprocess.run(["tasklist", "/fi", f"imagename eq {name}", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    m = re.search(r'"([^"]+)","(\d+)"', out)
    return int(m.group(2)) if m else None

target = get_pid("ttplayer.exe")
if not target:
    print("NO PROCESS")
    raise SystemExit

rows = []

def cb(hwnd, lparam):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == target:
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w = r.right - r.left
        h = r.bottom - r.top
        if w > 0 and h > 0:
            rows.append((buf.value, bool(user32.IsWindowVisible(hwnd)), w, h, r.left, r.top))
    return True

user32.EnumWindows(WNDENUMPROC(cb), 0)

print(f"PID={target}")
print("TITLE | VISIBLE | SIZE | POS")
for t, v, w, h, l, tp in rows:
    print(f"  {t!r} | vis={v} | {w}x{h} @ ({l},{tp})")

print(f"total windows: {len(rows)}")
sw = user32.GetSystemMetrics(0)
sh = user32.GetSystemMetrics(1)
print(f"screen: {sw}x{sh}")
