"""窗口位置记忆测试：移动 / 改尺寸 -> 落盘 -> 重启 -> 还原。"""
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


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %-32s %s" % (name, detail))
    else:
        FAIL += 1
        print("  FAIL  %-32s %s" % (name, detail))


def geom(page):
    return json.loads(page.eval(
        "(async()=>{const s=await ME.outerSize();const p=await ME.outerPosition();"
        "return JSON.stringify({x:p.x,y:p.y,w:s.width,h:s.height})})()"
    ))


def all_geom(ps):
    return {k: geom(p) for k, p in ps.items()}


def drag(page, ox, oy, dx, dy, steps=16):
    g = geom(page)
    x, y = g["x"] + ox, g["y"] + oy
    u.SetCursorPos(x, y)
    time.sleep(0.15)
    u.mouse_event(LEFT_DOWN, 0, 0, 0, 0)
    time.sleep(0.2)
    for i in range(1, steps + 1):
        u.SetCursorPos(x + int(dx * i / steps), y + int(dy * i / steps))
        time.sleep(0.035)
    time.sleep(0.2)
    u.mouse_event(LEFT_UP, 0, 0, 0, 0)
    time.sleep(0.7)


def layout_file():
    base = os.path.expanduser("~/AppData/Roaming")
    for root, dirs, files in os.walk(base):
        if root.count(os.sep) - base.count(os.sep) > 2:
            dirs[:] = []
            continue
        if "window-layout.txt" in files:
            return os.path.join(root, "window-layout.txt")
    return None


def kill():
    subprocess.run(["taskkill", "/F", "/IM", "ttplayer.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.8)


def start():
    env = dict(os.environ)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=9222"
    subprocess.Popen([EXE], env=env, cwd=r"D:\AI\BFQ\ttplayer")
    for _ in range(40):
        time.sleep(0.4)
        try:
            ps = cdp.pages()
            if "main" in ps:
                for p in ps.values():
                    p.close()
                time.sleep(1.0)
                return cdp.pages()
        except Exception:
            pass
    raise RuntimeError("启动超时")


def main():
    global PASS, FAIL
    ps = cdp.pages()
    m = ps["main"]
    for k in ("playlist", "lyric", "eq"):
        if not ps[k].eval("ME.isVisible()"):
            ps[k].eval("togglePanelWin('%s')" % k)
            time.sleep(0.7)
    time.sleep(0.5)

    print("\n1. 拖标题栏把整列移到左上角")
    before = all_geom(ps)
    print("   起点:", {k: (v["x"], v["y"]) for k, v in before.items()})
    drag(m, 160, 11, -320, -60)
    moved = all_geom(ps)
    dx = moved["main"]["x"] - before["main"]["x"]
    dy = moved["main"]["y"] - before["main"]["y"]
    check("主体确实移动了", abs(dx) > 100 and abs(dy) > 30, "位移 %d,%d" % (dx, dy))
    same = all(abs(moved[k]["x"] - before[k]["x"] - dx) <= 2 and
               abs(moved[k]["y"] - before[k]["y"] - dy) <= 2
               for k in ("playlist", "lyric", "eq"))
    check("面板跟着一起走", same,
          str({k: (moved[k]["x"] - before[k]["x"], moved[k]["y"] - before[k]["y"])
               for k in ("playlist", "lyric", "eq")}))

    print("\n2. 拉宽主体 +120、拉长歌词 +140")
    # 主体加宽
    g = geom(m)
    x, y = g["x"] + g["w"] - 6, g["y"] + g["h"] - 6
    u.SetCursorPos(x, y); time.sleep(0.12)
    u.mouse_event(LEFT_DOWN, 0, 0, 0, 0); time.sleep(0.18)
    for i in range(1, 15):
        u.SetCursorPos(x + int(120 * i / 14), y)
        time.sleep(0.035)
    u.mouse_event(LEFT_UP, 0, 0, 0, 0); time.sleep(0.8)

    g = geom(ps["lyric"])
    x, y = g["x"] + g["w"] - 6, g["y"] + g["h"] - 6
    u.SetCursorPos(x, y); time.sleep(0.12)
    u.mouse_event(LEFT_DOWN, 0, 0, 0, 0); time.sleep(0.18)
    for i in range(1, 15):
        u.SetCursorPos(x, y + int(140 * i / 14))
        time.sleep(0.035)
    u.mouse_event(LEFT_UP, 0, 0, 0, 0); time.sleep(0.8)

    want = all_geom(ps)
    for k, v in want.items():
        print("   %-9s %4dx%-4d @ %d,%d" % (k, v["w"], v["h"], v["x"], v["y"]))
    check("主体已加宽", want["main"]["w"] >= 460, "%d" % want["main"]["w"])
    check("歌词已加长", want["lyric"]["h"] >= 260, "%d" % want["lyric"]["h"])

    print("\n3. 落盘")
    m.eval("window.__TAURI__.core.invoke('save_layout')")
    time.sleep(0.8)
    path = layout_file()
    check("配置文件已生成", bool(path), str(path))
    if path:
        txt = open(path, encoding="utf-8").read()
        print("   ---\n" + "\n".join("   " + l for l in txt.strip().splitlines()) + "\n   ---")
        rows = {l.split()[0]: [int(v) for v in l.split()[1:]] for l in txt.strip().splitlines()}
        check("记录了 main 的位置", "main" in rows and rows["main"][1] == want["main"]["y"],
              str(rows.get("main")))
        check("记录了歌词高度", rows.get("lyric", [0, 0, 0, 0])[3] == want["lyric"]["h"],
              str(rows.get("lyric")))

    print("\n4. 关掉再启动，看是否照原位还原")
    for p in ps.values():
        p.close()
    kill()
    ps2 = start()
    m2 = ps2["main"]
    m2.eval("window.__TAURI__.event.emit('tt:cmd', {t:'clear'})")
    back = {}
    for k in ps2:
        if k != "main":
            if not ps2[k].eval("ME.isVisible()"):
                ps2[k].eval("togglePanelWin('%s')" % k)
                time.sleep(0.6)
    time.sleep(0.8)
    back = all_geom(ps2)
    for k, v in back.items():
        print("   %-9s %4dx%-4d @ %d,%d" % (k, v["w"], v["h"], v["x"], v["y"]))
    check("主体位置还原", abs(back["main"]["x"] - want["main"]["x"]) <= 4 and
          abs(back["main"]["y"] - want["main"]["y"]) <= 4,
          "want %d,%d got %d,%d" % (want["main"]["x"], want["main"]["y"],
                                    back["main"]["x"], back["main"]["y"]))
    check("主体尺寸还原", abs(back["main"]["w"] - want["main"]["w"]) <= 3,
          "%d vs %d" % (back["main"]["w"], want["main"]["w"]))
    check("歌词高度还原", abs(back["lyric"]["h"] - want["lyric"]["h"]) <= 3,
          "%d vs %d" % (back["lyric"]["h"], want["lyric"]["h"]))
    check("面板仍紧贴成列",
          all(abs(back[k]["x"] - back["main"]["x"]) <= 2 for k in ("playlist", "lyric", "eq")),
          str({k: back[k]["x"] for k in ("playlist", "lyric", "eq")}))

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    for p in ps2.values():
        p.close()


if __name__ == "__main__":
    main()
