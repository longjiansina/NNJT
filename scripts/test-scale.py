"""验证「拖动右下角手柄整体缩放」与「皮肤下拉」"""
import ctypes
import json
import re
import subprocess
import time
from ctypes import wintypes

import cdp

user32 = ctypes.windll.user32
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  %-32s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %-32s %s" % (name, extra))


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def find_win(title):
    out = subprocess.run(["tasklist", "/fi", "imagename eq ttplayer.exe", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    m = re.search(r'"([^"]+)","(\d+)"', out)
    if not m:
        return None
    pid_target = int(m.group(2))
    found = [None]

    def cb(h, l):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == pid_target:
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(h, buf, 512)
            if buf.value == title:
                found[0] = h
                return False
        return True

    user32.EnumWindows(PROC(cb), 0)
    return found[0]


def rect_of(title):
    h = find_win(title)
    if not h:
        return None
    r = RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def drag(x0, y0, dx, dy, steps=12):
    user32.SetCursorPos(x0, y0)
    time.sleep(0.12)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.1)
    for i in range(1, steps + 1):
        user32.SetCursorPos(int(x0 + dx * i / steps), int(y0 + dy * i / steps))
        time.sleep(0.035)
    time.sleep(0.15)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.5)


def main():
    ps = cdp.pages()
    m = ps["main"]
    m.eval("setUiScale(1)")
    time.sleep(0.2)

    print("1. 初始状态")
    r0 = rect_of("宁宁静听")
    check("主窗口存在", r0 is not None, str(r0))

    print("\n2. 拖右下手柄放大 90px")
    gx, gy = r0[0] + r0[2] - 8, r0[1] + r0[3] - 8
    drag(gx, gy, 90, 60)
    s1 = m.eval("state.uiScale")
    r1 = rect_of("宁宁静听")
    check("缩放比变大", s1 > 1.1, "%.2f" % s1)
    check("主窗口变宽", r1[2] > r0[2] + 40, "%d → %d" % (r0[2], r1[2]))
    check("主体 zoom 生效", abs(float(m.eval("getComputedStyle(document.getElementById('app')).zoom")) - s1) < 0.02)

    print("\n3. 面板跟随放大")
    pl = rect_of("播放列表")
    eq = rect_of("均衡器")
    check("播放列表宽度跟随主体", abs(pl[2] - r1[2]) <= 2, "%d vs %d" % (pl[2], r1[2]))
    check("均衡器宽度跟随主体", abs(eq[2] - r1[2]) <= 2, "%d vs %d" % (eq[2], r1[2]))
    check("播放列表横向对齐", abs(pl[0] - r1[0]) <= 2, "%d vs %d" % (pl[0], r1[0]))
    check("播放列表紧贴主体下方", abs(pl[1] - (r1[1] + r1[3])) <= 3, "gap=%d" % (pl[1] - r1[1] - r1[3]))
    check("面板 CSS 也缩放", abs(float(m.eval("getComputedStyle(document.getElementById('app')).zoom")) - s1) < 0.02)

    print("\n4. 拖回去缩小")
    r2 = rect_of("宁宁静听")
    drag(r2[0] + r2[2] - 8, r2[1] + r2[3] - 8, -80, -50)
    s2 = m.eval("state.uiScale")
    r3 = rect_of("宁宁静听")
    check("缩放比回落", s2 < s1, "%.2f → %.2f" % (s1, s2))
    check("窗口变窄", r3[2] < r2[2], "%d → %d" % (r2[2], r3[2]))
    check("已写入 localStorage", m.eval("localStorage.getItem('ttplayer-uiscale')") is not None,
          m.eval("localStorage.getItem('ttplayer-uiscale')"))

    print("\n5. 皮肤下拉")
    n = m.eval("document.querySelectorAll('#skin-menu .om-item').length")
    check("菜单有两项皮肤", n == 2, str(n))
    m.eval("document.getElementById('btn-skin-menu').click()")
    time.sleep(0.3)
    check("点击后菜单展开", not bool(m.eval("document.getElementById('skin-menu').hidden")))
    cur = m.eval("state.skin")
    m.eval("document.querySelector('#skin-menu .om-item[data-skin=\"modern\"]').click()")
    time.sleep(0.6)
    check("切换为现代深色", m.eval("state.skin") == "modern", cur + " → " + m.eval("state.skin"))
    check("body 皮肤同步", m.eval("document.body.dataset.skin") == "modern")
    check("菜单已收起", bool(m.eval("document.getElementById('skin-menu').hidden")))
    check("面板同步换肤", ps.get("eq") and ps["eq"].eval("state.skin") == "modern")
    m.eval("document.querySelector('#skin-menu .om-item[data-skin=\"retro\"]')")
    m.eval("applySkin('retro'); broadcast(true)")
    time.sleep(0.4)

    print("\n6. 恢复 1.0")
    m.eval("window.__TAURI__.core.invoke('set_ui_scale',{scale:1})")
    time.sleep(0.6)
    r4 = rect_of("宁宁静听")
    check("回到原始宽度", abs(r4[2] - 390) <= 2, str(r4[2]))
    check("zoom 回到 1", abs(float(m.eval("getComputedStyle(document.getElementById('app')).zoom")) - 1) < 0.02)

    for p in ps.values():
        p.close()
    print("\n=== %d 通过 / %d 失败" % (PASS, FAIL))


if __name__ == "__main__":
    main()
