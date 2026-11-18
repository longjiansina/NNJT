"""宁宁静听窗口行为回归测试
用真实鼠标事件（SetCursorPos + mouse_event）模拟拖拽，覆盖：
  1. 磁吸贴合
  2. 拖主窗口 → 全体窗口一起跟随
  3. 拖子窗口 → 主窗口不动
  4. 贴边 → 整组窗口一起收起（不能只收主控）
  5. 鼠标碰屏幕边缘 → 整组一起弹出
"""
import ctypes
import re
import subprocess
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

PANELS = ("宁宁静听", "播放列表", "歌词秀", "均衡器")
results = []
KEEP = 5  # 与 Rust 侧 DOCK_KEEP 保持一致


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def pid_of(name):
    out = subprocess.run(["tasklist", "/fi", f"imagename eq {name}", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    m = re.search(r'"([^"]+)","(\d+)"', out)
    return int(m.group(2)) if m else None


TARGET = pid_of("ttplayer.exe")
if not TARGET:
    print("NO PROCESS")
    raise SystemExit

wins = {}


def enum():
    wins.clear()

    def cb(h, l):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == TARGET:
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(h, buf, 512)
            if buf.value:
                r = RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                if r.right > r.left and r.bottom > r.top:
                    wins[buf.value] = (h, r.left, r.top, r.right - r.left, r.bottom - r.top)
        return True

    user32.EnumWindows(PROC(cb), 0)


def pos(name):
    enum()
    w = wins.get(name)
    return (w[1], w[2]) if w else None


def geom(name):
    enum()
    return wins.get(name)


def drag(x0, y0, x1, y1, steps=14, settle=1.0):
    user32.SetCursorPos(x0, y0)
    time.sleep(0.25)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.15)
    for i in range(1, steps + 1):
        user32.SetCursorPos(int(x0 + (x1 - x0) * i / steps), int(y0 + (y1 - y0) * i / steps))
        time.sleep(0.025)
    time.sleep(0.2)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(settle)


def check(name, ok, detail):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


screen_w = user32.GetSystemMetrics(0)
screen_h = user32.GetSystemMetrics(1)

# ---------- 0. 面板宽度与主体一致 ----------
print("0. 播放列表 / 歌词秀 / 均衡器 宽度应与主体一致")
enum()
mw = wins["宁宁静听"][3]
for k in ("播放列表", "歌词秀", "均衡器"):
    if k in wins:
        check(f"{k} 宽度 {wins[k][3]}", abs(wins[k][3] - mw) <= 1, f"主体={mw}")

# ---------- 0.5 启动布局：播放列表紧贴主体下方 ----------
print("0.5 启动时播放列表应紧贴主体下方")

# 布局记忆会把上次开着的面板照原样打开；本脚本假设「只开播放列表」，
# 先把歌词秀 / 均衡器收起来，否则整组高度算不对
try:
    import cdp
    _ps = cdp.pages()
    for _lab in ("lyric", "eq"):
        if _lab in _ps and _ps[_lab].eval("ME.isVisible()"):
            _ps["main"].eval("togglePanelWin('%s')" % _lab)
            time.sleep(0.9)
    for _p in _ps.values():
        _p.close()
    time.sleep(0.8)
except Exception as _e:
    print("   (收起面板失败，继续)", _e)

# 必须先提到前台，否则模拟点击会落到遮挡在上面的其他窗口
enum()
user32.ShowWindow(wins["宁宁静听"][0], 9)
user32.SetForegroundWindow(wins["宁宁静听"][0])
time.sleep(0.6)


enum()
m = wins["宁宁静听"]
pl = wins["播放列表"]
check("播放列表贴合主体", abs(pl[1] - m[1]) <= 1 and abs(pl[2] - (m[2] + m[4])) <= 2,
      f"列表({pl[1]},{pl[2]}) 主体底边 y={m[2] + m[4]}")

# 面板堆叠行为由 test-stack.py 单独覆盖（走 CDP 精确点击，不猜坐标）

# ---------- 1. 磁吸 ----------
print("1. 播放列表拖到主控右侧 8px 处，应磁吸贴合")
enum()
m = wins["宁宁静听"]
p = wins["播放列表"]
edge = m[1] + m[3]
drag(p[1] + 80, p[2] + 11, edge + 8 + 80, p[2] + 11)
h, px, py, pw, ph = geom("播放列表")
check("右侧磁吸贴合", abs(px - edge) <= 1, f"列表x={px} 主控右边缘={edge}")

# ---------- 2. 拖主窗口，全体跟随 ----------
print("2. 拖动主窗口，全体跟随")
before = {k: pos(k) for k in PANELS if k in wins}
m = geom("宁宁静听")
drag(m[1] + 80, m[2] + 11, m[1] + 80 - 120, m[2] + 11 - 80)
after = {k: pos(k) for k in PANELS if k in wins}
dm = (after["宁宁静听"][0] - before["宁宁静听"][0], after["宁宁静听"][1] - before["宁宁静听"][1])
dpl = (after["播放列表"][0] - before["播放列表"][0], after["播放列表"][1] - before["播放列表"][1])
check("主控+列表同步位移", dm == dpl and dm != (0, 0), f"主控{dm} 列表{dpl}")

# ---------- 3. 拖子窗口，主控不动 ----------
print("3. 拖动播放列表，主控保持不动")
mb = pos("宁宁静听")
p = geom("播放列表")
drag(p[1] + 80, p[2] + 11, p[1] + 80 - 140, p[2] + 11)
check("主控未被带动", pos("宁宁静听") == mb, f"主控 {mb} → {pos('宁宁静听')}")

# ---------- 4. 贴边整组收起 ----------
print("4. 主控拖到屏幕顶部，整组窗口应一起收起")
enum()
m = wins["宁宁静听"]
mh = m[4]
drag(m[1] + 80, m[2] + 11, m[1] + 80, 11, settle=0.4)

# 收起前记录整组归位几何（此时鼠标仍停在主控上，尚未收起）
enum()
home = {k: wins[k][1:3] for k in ("宁宁静听", "播放列表") if k in wins}
g_top = min(home[k][1] for k in home)
g_bot = max(home[k][1] + wins[k][4] for k in home)
g_h = g_bot - g_top
shift = -(g_h - KEEP)
print(f"   组高={g_h} 期望位移={shift}")

user32.SetCursorPos(60, 900)   # 鼠标移到远处
t_out = time.time()
while time.time() - t_out < 3:
    enum()
    if abs(wins["宁宁静听"][2] - home["宁宁静听"][1]) > 50:
        break
    time.sleep(0.02)
hide_ms = (time.time() - t_out) * 1000
# 整组的 set_position 是逐个下发的，主控先动；再给 250ms 让后面几个落地
time.sleep(0.25)
enum()
hid = {k: wins[k][1:3] for k in ("宁宁静听", "播放列表") if k in wins}

for k in home:
    got = hid[k][1] - home[k][1]
    check(f"{k} 一起收起", abs(got - shift) <= 6, f"位移={got} 期望={shift}")
check("收起响应延迟", hide_ms < 1200, f"{hide_ms:.0f}ms（含 420ms 静默等待）")

# ---------- 5. 碰边缘整组弹出 ----------
print("5. 鼠标碰屏幕顶部边缘，整组应一起弹出")
t_in = time.time()
user32.SetCursorPos(home["宁宁静听"][0] + 80, 2)
while time.time() - t_in < 2:
    enum()
    if abs(wins["宁宁静听"][2] - home["宁宁静听"][1]) <= 6:
        break
    time.sleep(0.02)
pop_ms = (time.time() - t_in) * 1000
time.sleep(0.35)
enum()
rev = {k: wins[k][1:3] for k in ("宁宁静听", "播放列表") if k in wins}
for k in home:
    check(f"{k} 恢复归位", abs(rev[k][1] - home[k][1]) <= 6,
          f"y={rev[k][1]} 期望={home[k][1]}")
check("弹出响应延迟", pop_ms < 250, f"{pop_ms:.0f}ms")

user32.SetCursorPos(screen_w // 2, screen_h // 2)
print("\n" + "=" * 44)
passed = sum(1 for _, ok in results if ok)
print(f"结果: {passed}/{len(results)} 通过")
for name, ok in results:
    if not ok:
        print(f"  未通过: {name}")
