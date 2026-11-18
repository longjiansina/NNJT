"""验证面板纵向堆叠：点「歌词」「均衡器」应依次排在播放列表下方并与主体左对齐

依赖 WebView2 调试端口：
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


def geom(name):
    """返回 (x, y, w, h)"""
    w = wins[name]
    return (w[1], w[2], w[3], w[4])


def shown(name):
    """窗口是否存在且当前可见"""
    return name in wins and bool(user32.IsWindowVisible(wins[name][0]))


results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


# ---------- 连接页面 ----------
ps = cdp.pages()
print("已连接窗口:", list(ps.keys()))
if "main" not in ps:
    print("找不到 main 页面，退出")
    sys.exit(1)

main = ps["main"]

print("按钮实际位置:", main.eval(
    "JSON.stringify(Array.from(document.querySelectorAll('.cap'))"
    ".map(e => ({p: e.dataset.panel, r: e.getBoundingClientRect().toJSON()})))"
))


def click_panel(label):
    ok = main.eval(
        "(() => { const b = document.querySelector('.cap[data-panel=\"%s\"]');"
        " if (!b) return false; b.click(); return true; })()" % label
    )
    print(f"  点击 [{label}] -> {ok}")
    time.sleep(1.3)


# ---------- 基线 ----------
print("\n0. 启动基线")
# 布局记忆会把上次开着的面板照原样打开，先统一收起来，保证基线一致
for lab, btn in (("歌词秀", "lyric"), ("均衡器", "eq")):
    if shown(lab):
        click_panel(btn)
time.sleep(0.8)
enum()
m = geom("宁宁静听")
check("基线：只剩播放列表", shown("播放列表") and not shown("歌词秀") and not shown("均衡器"),
      f"列表={shown('播放列表')} 歌词={shown('歌词秀')} 均衡器={shown('均衡器')}")
check("播放列表紧贴主体下方",
      shown("播放列表") and abs(geom("播放列表")[1] - (m[1] + m[3])) <= 2,
      f"列表y={geom('播放列表')[1] if '播放列表' in wins else 'N/A'} 期望={m[1] + m[3]}")

pl = geom("播放列表")

# ---------- 1. 点「歌词」 ----------
print("\n1. 点「歌词」应出现在播放列表正下方并与主体左对齐")
click_panel("lyric")
enum()
if not shown("歌词秀"):
    check("歌词秀显示", False, "窗口未显示")
else:
    ly = geom("歌词秀")
    check("歌词秀左对齐主体", abs(ly[0] - m[0]) <= 2, f"x={ly[0]} 主体x={m[0]}")
    check("歌词秀在播放列表下方", abs(ly[1] - (pl[1] + pl[3])) <= 2,
          f"y={ly[1]} 期望={pl[1] + pl[3]}")

# ---------- 2. 点「均衡器」 ----------
print("\n2. 点「均衡器」应出现在歌词秀正下方")
click_panel("eq")
enum()
if not shown("均衡器") or not shown("歌词秀"):
    check("均衡器显示", False, f"均衡器={shown('均衡器')} 歌词={shown('歌词秀')}")
else:
    ly = geom("歌词秀")
    eq = geom("均衡器")
    check("均衡器左对齐主体", abs(eq[0] - m[0]) <= 2, f"x={eq[0]} 主体x={m[0]}")
    check("均衡器在歌词秀下方", abs(eq[1] - (ly[1] + ly[3])) <= 2,
          f"y={eq[1]} 期望={ly[1] + ly[3]}")

# ---------- 3. 收起 ----------
print("\n3. 再点一次应收起，且下方成员补位")
click_panel("eq")
enum()
check("均衡器已收起", not shown("均衡器"))
click_panel("lyric")
enum()
check("歌词秀已收起", not shown("歌词秀"))
check("收起后列表仍在主体下方",
      shown("播放列表") and abs(geom("播放列表")[1] - (m[1] + m[3])) <= 2,
      f"列表y={geom('播放列表')[1] if '播放列表' in wins else 'N/A'}")

# ---------- 4. 反序点：先均衡器再歌词，仍应顺序堆叠 ----------
print("\n4. 反序点（先均衡器再歌词），仍应 playlist->lyric->eq 顺序")
click_panel("eq")
enum()
if shown("均衡器"):
    eq = geom("均衡器")
    check("均衡器单独出现时紧跟播放列表", abs(eq[1] - (pl[1] + pl[3])) <= 2,
          f"y={eq[1]} 期望={pl[1] + pl[3]}")
else:
    check("均衡器显示", False, "窗口未显示")
click_panel("lyric")
enum()
if shown("均衡器") and shown("歌词秀"):
    ly = geom("歌词秀")
    eq = geom("均衡器")
    check("歌词插在列表与均衡器之间",
          abs(ly[1] - (pl[1] + pl[3])) <= 2 and abs(eq[1] - (ly[1] + ly[3])) <= 2,
          f"歌词y={ly[1]} 期望={pl[1] + pl[3]} / 均衡器y={eq[1]} 期望={ly[1] + ly[3]}")
else:
    check("歌词插在列表与均衡器之间", False, f"歌词={shown('歌词秀')} 均衡器={shown('均衡器')}")

# ---------- 5. 收起歌词后均衡器应上移补位 ----------
print("\n5. 收起中间的歌词，均衡器应上移补位")
click_panel("lyric")
enum()
if shown("均衡器"):
    eq = geom("均衡器")
    check("均衡器上移补位", abs(eq[1] - (pl[1] + pl[3])) <= 2,
          f"y={eq[1]} 期望={pl[1] + pl[3]}")
else:
    check("均衡器上移补位", False, "均衡器不见了")
click_panel("eq")

# ---------- 汇总 ----------
print("\n========== 汇总 ==========")
bad = [n for n, ok in results if not ok]
print(f"通过 {len(results) - len(bad)}/{len(results)}")
if bad:
    print("失败项：")
    for n in bad:
        print("  -", n)
for p in ps.values():
    p.close()
sys.exit(1 if bad else 0)
