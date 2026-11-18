"""布局记忆 v2 测试

覆盖三件事：
1. 把面板拖到主体那一列之外（独立摆放）→ 重启后仍在原位
2. 面板开/关状态 → 重启后保持
3. 独立摆放的窗口能一直拉长到屏幕高度（不再被同列其他窗口挤掉上限）
"""
import ctypes
import json
import os
import subprocess
import time

import cdp

u = ctypes.windll.user32
LEFT_DOWN, LEFT_UP = 0x0002, 0x0004

PASS = FAIL = 0
EXE = r"D:\AI\BFQ\ttplayer\src-tauri\target\release\ttplayer.exe"
LAYOUT = os.path.expandvars(r"%APPDATA%\com.ttplayer.player\window-layout.txt")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %-34s %s" % (name, detail))
    else:
        FAIL += 1
        print("  FAIL  %-34s %s" % (name, detail))


def geom(page):
    return json.loads(page.eval(
        "(async()=>{const s=await ME.outerSize();const p=await ME.outerPosition();"
        "return JSON.stringify({x:p.x,y:p.y,w:s.width,h:s.height})})()"
    ))


def all_geom(ps):
    return {k: geom(p) for k, p in ps.items()}


def drag(page, ox, oy, dx, dy, steps=18):
    g = geom(page)
    x, y = g["x"] + ox, g["y"] + oy
    u.SetCursorPos(x, y)
    time.sleep(0.15)
    u.mouse_event(LEFT_DOWN, 0, 0, 0, 0)
    time.sleep(0.2)
    for i in range(1, steps + 1):
        u.SetCursorPos(x + int(dx * i / steps), y + int(dy * i / steps))
        time.sleep(0.03)
    time.sleep(0.2)
    u.mouse_event(LEFT_UP, 0, 0, 0, 0)
    time.sleep(0.9)


def resize_grip(page, dx, dy, steps=20):
    g = geom(page)
    x, y = g["x"] + g["w"] - 6, g["y"] + g["h"] - 6
    u.SetCursorPos(x, y)
    time.sleep(0.15)
    u.mouse_event(LEFT_DOWN, 0, 0, 0, 0)
    time.sleep(0.2)
    for i in range(1, steps + 1):
        u.SetCursorPos(x + int(dx * i / steps), y + int(dy * i / steps))
        time.sleep(0.03)
    time.sleep(0.2)
    u.mouse_event(LEFT_UP, 0, 0, 0, 0)
    time.sleep(1.0)


def kill():
    subprocess.run(["taskkill", "/F", "/IM", "ttplayer.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.8)


def start():
    env = dict(os.environ)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=9222"
    subprocess.Popen([EXE], env=env, cwd=r"D:\AI\BFQ\ttplayer")
    for _ in range(45):
        time.sleep(0.4)
        try:
            ps = cdp.pages()
            if "main" in ps:
                time.sleep(1.2)
                return cdp.pages()
        except Exception:
            pass
    raise RuntimeError("启动超时")


def quit_app(ps):
    """走真实退出路径：关主窗口 -> CloseRequested -> 全量落盘 + exit"""
    try:
        ps["main"].eval("ME.close()")
    except Exception:
        pass
    for _ in range(20):
        time.sleep(0.3)
        try:
            cdp.list_targets()
        except Exception:
            return True
    kill()
    return False


def show(ps, label):
    if not ps[label].eval("ME.isVisible()"):
        ps[label].eval("togglePanelWin('%s')" % label)
        time.sleep(0.8)


def hide(ps, label):
    if ps[label].eval("ME.isVisible()"):
        ps[label].eval("togglePanelWin('%s')" % label)
        time.sleep(0.7)


def dump_rows():
    if not os.path.exists(LAYOUT):
        return {}
    rows = {}
    for line in open(LAYOUT, encoding="utf-8").read().strip().splitlines():
        f = line.split()
        if len(f) >= 5:
            rows[f[0]] = f[1:]
    return rows


def main():
    global PASS, FAIL
    kill()
    if os.path.exists(LAYOUT):
        os.replace(LAYOUT, LAYOUT + ".pre")
    print("清空布局文件，干净启动")
    ps = start()

    print("\n1. 打开歌词与播放列表，把歌词拖到列的右边独立摆放")
    show(ps, "playlist")
    show(ps, "lyric")
    hide(ps, "eq")
    time.sleep(0.6)

    g0 = all_geom(ps)
    print("   起点:", {k: (v["x"], v["y"], v["w"], v["h"]) for k, v in g0.items()})
    mw = g0["main"]["w"]
    # 从歌词标题栏拖到主体右侧（离开列）
    drag(ps["lyric"], 120, 11, mw + 90, -g0["lyric"]["y"] + 24)
    g1 = all_geom(ps)
    print("   拖后:", {k: (v["x"], v["y"], v["w"], v["h"]) for k, v in g1.items()})
    check("歌词已离开主体那一列", abs(g1["lyric"]["x"] - g1["main"]["x"]) > 40,
          "x=%d vs main %d" % (g1["lyric"]["x"], g1["main"]["x"]))
    check("播放列表仍在列中(未被带走)",
          abs(g1["playlist"]["x"] - g1["main"]["x"]) <= 2,
          "x=%d" % g1["playlist"]["x"])

    print("\n2. 把独立摆放的歌词使劲拉长（旧版会被别的窗口挤掉上限）")
    resize_grip(ps["lyric"], 60, 620)
    g2 = geom(ps["lyric"])
    print("   歌词:", g2)
    check("能拉长到 700 以上", g2["h"] >= 700, "h=%d" % g2["h"])
    check("拉长后仍在屏幕内", g2["y"] + g2["h"] <= 1080, "bottom=%d" % (g2["y"] + g2["h"]))

    want = all_geom(ps)
    print("\n3. 落盘内容（label x y w h vis）")
    ps["main"].eval("window.__TAURI__.core.invoke('save_layout')")
    time.sleep(0.9)
    rows = dump_rows()
    print("   " + "\n   ".join("%s %s" % (k, " ".join(v)) for k, v in rows.items()))
    check("歌词记了 6 列（含可见性）", len(rows.get("lyric", [])) == 5,
          str(rows.get("lyric")))
    check("均衡器记为隐藏", rows.get("eq", [""] * 5)[4] == "0", str(rows.get("eq")))
    check("歌词记为显示", rows.get("lyric", [""] * 5)[4] == "1", str(rows.get("lyric")))

    print("\n4. 正常退出再启动，看是否照原样复原")
    quit_app(ps)
    ps = start()
    time.sleep(0.8)
    back = all_geom(ps)
    for k, v in back.items():
        print("   %-9s %4dx%-4d @ %d,%d visible=%s" % (k, v["w"], v["h"], v["x"], v["y"],
                                                    ps[k].eval("ME.isVisible()")))
    check("歌词位置复原", abs(back["lyric"]["x"] - want["lyric"]["x"]) <= 4 and
          abs(back["lyric"]["y"] - want["lyric"]["y"]) <= 4,
          "want %d,%d got %d,%d" % (want["lyric"]["x"], want["lyric"]["y"],
                                    back["lyric"]["x"], back["lyric"]["y"]))
    check("歌词尺寸复原", abs(back["lyric"]["w"] - want["lyric"]["w"]) <= 4 and
          abs(back["lyric"]["h"] - want["lyric"]["h"]) <= 4,
          "%dx%d vs %dx%d" % (back["lyric"]["w"], back["lyric"]["h"],
                              want["lyric"]["w"], want["lyric"]["h"]))
    check("歌词仍是独立摆放（没被揪回列里）",
          abs(back["lyric"]["x"] - back["main"]["x"]) > 40,
          "x=%d vs main %d" % (back["lyric"]["x"], back["main"]["x"]))
    check("主体位置复原", abs(back["main"]["x"] - want["main"]["x"]) <= 4 and
          abs(back["main"]["y"] - want["main"]["y"]) <= 4,
          "want %d,%d got %d,%d" % (want["main"]["x"], want["main"]["y"],
                                    back["main"]["x"], back["main"]["y"]))
    check("播放列表仍在列中贴合主体",
          abs(back["playlist"]["x"] - back["main"]["x"]) <= 2,
          "x=%d" % back["playlist"]["x"])
    check("主窗口可见", ps["main"].eval("ME.isVisible()") is True, "")
    check("均衡器仍是关闭状态", ps["eq"].eval("ME.isVisible()") is False, "")
    check("歌词仍是打开状态", ps["lyric"].eval("ME.isVisible()") is True, "")

    print("\n5. 关掉歌词再重启，验证「关掉的不会自己冒出来」")
    hide(ps, "lyric")
    time.sleep(0.5)
    rows = dump_rows()
    check("歌词记为隐藏", rows.get("lyric", [""] * 5)[4] == "0", str(rows.get("lyric")))
    quit_app(ps)
    ps = start()
    time.sleep(0.8)
    check("重启后歌词仍关着", ps["lyric"].eval("ME.isVisible()") is False, "")
    old = geom(ps["lyric"])
    show(ps, "lyric")
    new = geom(ps["lyric"])
    check("再打开时回到上次独立摆放的位置",
          abs(new["x"] - old["x"]) <= 4 and abs(new["y"] - old["y"]) <= 4,
          "旧 %d,%d 新 %d,%d" % (old["x"], old["y"], new["x"], new["y"]))
    check("再打开时尺寸也还原", abs(new["h"] - old["h"]) <= 4, "%d vs %d" % (new["h"], old["h"]))

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    quit_app(ps)


if __name__ == "__main__":
    main()
