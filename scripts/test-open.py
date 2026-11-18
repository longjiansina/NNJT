"""验证「打开」菜单：能弹菜单 → 选「添加文件」出文件框 → 选「添加文件夹」出目录框

依赖调试端口：
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=9222" ./ttplayer.exe
"""
import ctypes
import re
import subprocess
import sys
import time
from ctypes import wintypes

import cdp

user32 = ctypes.windll.user32
PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def dialogs():
    """返回当前可见的系统对话框 (标题, 尺寸)"""
    out = []

    def cb(h, l):
        if not user32.IsWindowVisible(h):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls, 256)
        if cls.value != "#32770":
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, buf, 512)
        r = RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        out.append((h, buf.value, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(PROC(cb), 0)
    return out


def close_all():
    for h, *_ in dialogs():
        user32.PostMessageW(h, 0x0010, 0, 0)   # WM_CLOSE
    time.sleep(0.6)


results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


ps = cdp.pages()
main = ps["main"]

menu_hidden = lambda: main.eval("document.getElementById('openmenu').hidden")
click = lambda sel: main.eval(
    "(document.querySelector(%r) || {click(){}}).click() || true" % sel)


# ---------- 1. 菜单弹出 / 收起 ----------
print("1. 点「打开」应弹出菜单")
check("初始菜单隐藏", menu_hidden() is True, f"hidden={menu_hidden()}")
click("#btn-open")
time.sleep(0.5)
check("菜单已弹出", menu_hidden() is False, f"hidden={menu_hidden()}")
items = main.eval("Array.from(document.querySelectorAll('#openmenu .om-item'))"
                  ".map(e => e.textContent.trim())")
check("菜单含两项", items == ["添加文件…", "添加文件夹…"], f"{items}")

main.eval("document.body.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}))")
time.sleep(0.4)
check("点空白处菜单收起", menu_hidden() is True, f"hidden={menu_hidden()}")

# ---------- 2. 添加文件 ----------
print("\n2. 选「添加文件…」应弹出系统文件框")
click("#btn-open")
time.sleep(0.4)
click('#openmenu .om-item[data-act="files"]')
time.sleep(1.8)
check("菜单已收起", menu_hidden() is True)
ds = dialogs()
check("弹出文件对话框", len(ds) > 0,
      f"{[(d[1], f'{d[2]}x{d[3]}') for d in ds]}")
close_all()

# ---------- 3. 添加文件夹 ----------
print("\n3. 选「添加文件夹…」应弹出目录选择框")
time.sleep(0.5)
click("#btn-open")
time.sleep(0.4)
click('#openmenu .om-item[data-act="folder"]')
time.sleep(1.8)
ds = dialogs()
check("弹出目录对话框", len(ds) > 0,
      f"{[(d[1], f'{d[2]}x{d[3]}') for d in ds]}")
close_all()

print("\n汇总: %d/%d 通过" % (sum(1 for _, ok in results if ok), len(results)))
for p in ps.values():
    p.close()
sys.exit(0 if all(ok for _, ok in results) else 1)
