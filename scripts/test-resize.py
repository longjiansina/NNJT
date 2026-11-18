"""窗口独立缩放端到端测试：真实鼠标拖每个窗口右下角手柄，验证互不影响。"""
import ctypes
import json
import time

import cdp

u = ctypes.windll.user32
LEFT_DOWN, LEFT_UP = 0x0002, 0x0004

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %-34s %s" % (name, detail))
    else:
        FAIL += 1
        print("  FAIL  %-34s %s" % (name, detail))


def _geom(page):
    return page.eval(
        "(async()=>{const s=await ME.outerSize();const p=await ME.outerPosition();"
        "return JSON.stringify({x:p.x,y:p.y,w:s.width,h:s.height})})()"
    )


def drag_grip(page, dx, dy, steps=14):
    g = json.loads(_geom(page))
    x = g["x"] + g["w"] - 6
    y = g["y"] + g["h"] - 6
    u.SetCursorPos(x, y)
    time.sleep(0.15)
    u.mouse_event(LEFT_DOWN, 0, 0, 0, 0)
    time.sleep(0.18)
    for i in range(1, steps + 1):
        u.SetCursorPos(x + int(dx * i / steps), y + int(dy * i / steps))
        time.sleep(0.03)
    time.sleep(0.2)
    u.mouse_event(LEFT_UP, 0, 0, 0, 0)
    time.sleep(0.55)


def ensure_visible(page, label):
    """确保面板可见（先问状态，不可见才 toggle）"""
    vis = page.eval("ME.isVisible()")
    if vis is False:
        page.eval("togglePanelWin('%s')" % label)
        time.sleep(0.9)
    return page.eval("ME.isVisible()")


def main():
    ps = cdp.pages()
    m, ly, pl, eq = ps["main"], ps["lyric"], ps["playlist"], ps["eq"]
    for page, label in ((pl, "playlist"), (ly, "lyric"), (eq, "eq")):
        ok = ensure_visible(page, label)
        if not ok:
            print("  !! %s 未能显示" % label)
    time.sleep(0.6)

    print("\n0. 初始状态")
    base = {k: json.loads(_geom(p)) for k, p in ps.items()}
    for k, v in base.items():
        print("   %-9s %4dx%-4d @ %d,%d" % (k, v["w"], v["h"], v["x"], v["y"]))
    check("四窗口等宽", len({v["w"] for v in base.values()}) == 1,
          str([v["w"] for v in base.values()]))

    print("\n1. 拉高歌词窗口（+120），其他窗口尺寸不变")
    before = {k: json.loads(_geom(p)) for k, p in ps.items()}
    drag_grip(ly, 0, 120)
    after = {k: json.loads(_geom(p)) for k, p in ps.items()}
    # 主体默认带 26px 示波带，整列可用高度比 v0.10 少一截，阈值放到 30
    check("歌词变高", after["lyric"]["h"] >= before["lyric"]["h"] + 30,
          "%d → %d（到屏幕底边自动停）" % (before["lyric"]["h"], after["lyric"]["h"]))
    bottom = max(v["y"] + v["h"] for v in after.values())
    check("整列没被顶出屏幕", bottom <= 1080, "底部 %d" % bottom)
    check("歌词宽度不变", abs(after["lyric"]["w"] - before["lyric"]["w"]) <= 1,
          "%d → %d" % (before["lyric"]["w"], after["lyric"]["w"]))
    check("主窗口尺寸不变", (after["main"]["w"], after["main"]["h"]) ==
          (before["main"]["w"], before["main"]["h"]),
          "%dx%d" % (after["main"]["w"], after["main"]["h"]))
    check("播放列表尺寸不变", (after["playlist"]["w"], after["playlist"]["h"]) ==
          (before["playlist"]["w"], before["playlist"]["h"]),
          "%dx%d" % (after["playlist"]["w"], after["playlist"]["h"]))
    check("均衡器尺寸不变", (after["eq"]["w"], after["eq"]["h"]) ==
          (before["eq"]["w"], before["eq"]["h"]),
          "%dx%d" % (after["eq"]["w"], after["eq"]["h"]))
    check("主窗口位置未动", after["main"]["y"] == before["main"]["y"],
          "y=%d" % after["main"]["y"])

    print("\n2. 拉宽歌词窗口（+80），主窗口宽度不变")
    before = {k: json.loads(_geom(p)) for k, p in ps.items()}
    drag_grip(ly, 80, 0)
    after = {k: json.loads(_geom(p)) for k, p in ps.items()}
    check("歌词变宽", after["lyric"]["w"] >= before["lyric"]["w"] + 55,
          "%d → %d" % (before["lyric"]["w"], after["lyric"]["w"]))
    check("主窗口宽度不变", after["main"]["w"] == before["main"]["w"],
          "%d" % after["main"]["w"])
    check("歌词仍左对齐主体", abs(after["lyric"]["x"] - after["main"]["x"]) <= 2,
          "%d vs %d" % (after["lyric"]["x"], after["main"]["x"]))

    print("\n3. 拉宽主窗口（+60），面板宽度跟随")
    before = {k: json.loads(_geom(p)) for k, p in ps.items()}
    drag_grip(m, 60, 0)
    after = {k: json.loads(_geom(p)) for k, p in ps.items()}
    check("主窗口变宽", after["main"]["w"] >= before["main"]["w"] + 40,
          "%d → %d" % (before["main"]["w"], after["main"]["w"]))
    check("播放列表宽度跟随", abs(after["playlist"]["w"] - after["main"]["w"]) <= 1,
          "%d" % after["playlist"]["w"])
    check("均衡器宽度跟随", abs(after["eq"]["w"] - after["main"]["w"]) <= 1,
          "%d" % after["eq"]["w"])
    check("列仍然紧贴无重叠", after["playlist"]["y"] >= after["main"]["y"] + after["main"]["h"] - 1,
          "%d >= %d" % (after["playlist"]["y"], after["main"]["y"] + after["main"]["h"]))

    print("\n4. 内容自适应（拉高后歌词能看到更多行）")
    vis = ly.eval(
        "(()=>{const b=document.getElementById('lyric-body');"
        "return JSON.stringify({ch:b.clientHeight, lines:document.querySelectorAll('.lyric-line').length})})()"
    )
    check("歌词区高度已放大", json.loads(vis)["ch"] > 120, vis)

    print("\n5. 双击手柄恢复默认尺寸")
    g = json.loads(_geom(ly))
    x, y = g["x"] + g["w"] - 6, g["y"] + g["h"] - 6
    u.SetCursorPos(x, y)
    time.sleep(0.15)
    for _ in range(2):
        u.mouse_event(LEFT_DOWN, 0, 0, 0, 0)
        u.mouse_event(LEFT_UP, 0, 0, 0, 0)
        time.sleep(0.06)
    time.sleep(0.8)
    gg = json.loads(_geom(ly))
    check("歌词回到默认高度", 100 <= gg["h"] <= 260, "%d" % gg["h"])
    check("宽度回到主体宽", abs(gg["w"] - json.loads(_geom(m))["w"]) <= 2,
          "%d vs %d" % (gg["w"], json.loads(_geom(m))["w"]))

    print("\n6. 布局落盘")
    import os
    base = os.path.expanduser("~/AppData/Roaming")
    path = None
    for root, dirs, files in os.walk(base):
        if root.count(os.sep) - base.count(os.sep) > 2:
            dirs[:] = []
            continue
        if "window-layout.txt" in files:
            path = os.path.join(root, "window-layout.txt")
    check("配置文件已写", bool(path), str(path))
    if path:
        txt = open(path, encoding="utf-8").read()
        rows = len([l for l in txt.strip().splitlines() if l.strip()])
        check("四个窗口都有记录", rows == 4, "%d 行" % rows)

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    for p in ps.values():
        p.close()


if __name__ == "__main__":
    main()
