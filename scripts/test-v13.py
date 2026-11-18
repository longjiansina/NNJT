"""v0.13 测试：标题栏 ⓘ 入口 + 独立的「关于」小窗

A 入口：ⓘ 在标题栏系统按钮组里、尺寸与其它系统按钮一致、有 tooltip
B 打开：点 ⓘ 弹出 about 窗口，单例（连点两次不会开出第二个）
C 内容：标题 / 名称 / 版本 / 作者 / 邮箱 / 简介 / 确定键都在，版本号与 Cargo.toml 一致
D 布局：对话框铺满窗内区域、没有溢出、没有 JS 报错
E 关闭：点「确定」关、点 ✕ 关、按 Esc 关，关掉后能再次打开
F 定位：关于窗居中压在主体上（横纵都在 ±3px 内）
G 隔离：主窗尺寸不变、不写进 window-layout.txt、不影响播放状态
"""
import json
import os
import re
import subprocess
import time

import cdp

PASS = FAIL = 0
EXE = r"D:\AI\BFQ\ttplayer\src-tauri\target\release\ttplayer.exe"
CONF = os.path.expandvars(r"%APPDATA%\com.ttplayer.player")
LAYOUT = os.path.join(CONF, "window-layout.txt")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %-42s %s" % (name, detail))
    else:
        FAIL += 1
        print("  FAIL  %-42s %s" % (name, detail))


def kill():
    subprocess.run(["taskkill", "/F", "/IM", "ttplayer.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.8)


def env():
    e = dict(os.environ)
    e["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=9222"
    return e


def start():
    subprocess.Popen([EXE], env=env(), cwd=r"D:\AI\BFQ\ttplayer")
    for _ in range(45):
        time.sleep(0.4)
        try:
            ps = cdp.pages()
            if "main" in ps:
                time.sleep(2.2)
                return cdp.pages()
        except Exception:
            pass
    raise RuntimeError("没起来")


def click(pg, x, y):
    """真鼠标点击：验证命中测试，不是 dispatchEvent"""
    pg.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    pg.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y,
            button="left", clickCount=1)
    pg.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y,
            button="left", clickCount=1)


def rect(pg, sel):
    v = pg.eval(
        """(()=>{const e=document.querySelector(%s); if(!e) return null;
           const r=e.getBoundingClientRect();
           return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]})()"""
        % json.dumps(sel))
    return v if isinstance(v, list) else None


def click_center(pg, sel):
    r = rect(pg, sel)
    if not r:
        return False
    click(pg, r[0] + r[2] / 2, r[1] + r[3] / 2)
    return True


def wait_about(want=True, timeout=6.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            ps = cdp.pages()
        except Exception:
            ps = {}
        if ("about" in ps) == want:
            return ps
        time.sleep(0.35)
    try:
        return cdp.pages()
    except Exception:
        return {}


def cargo_version():
    p = r"D:\AI\BFQ\ttplayer\src-tauri\Cargo.toml"
    with open(p, encoding="utf-8") as f:
        m = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M)
    return m.group(1) if m else "?"


def main():
    kill()
    ps = start()
    m = ps["main"]
    ver = cargo_version()

    print("\n[A] 入口按钮")
    check("页面无 JS 报错", not m.eval("String(window.__err||'')"),
          str(m.eval("String(window.__err||'')"))[:80])
    b = rect(m, "#btn-about")
    check("标题栏里有 ⓘ", b is not None, str(b))
    tb = rect(m, ".win-title")
    if b and tb:
        check("ⓘ 落在标题栏内", b[1] >= tb[1] and b[1] + b[3] <= tb[1] + tb[3],
              "btn y=%d..%d / title y=%d..%d" % (b[1], b[1] + b[3], tb[1], tb[1] + tb[3]))
        check("ⓘ 在标题栏右半边", b[0] > tb[0] + tb[2] / 2, "x=%d" % b[0])
    # 只看主窗自己的系统按钮：其它 pane 是 display:none，会混进 0×0 的无关按钮
    others = m.eval("""[...document.querySelectorAll('.pane[data-pane="main"] .sysbtns .sysbtn')]
        .map(e=>{const r=e.getBoundingClientRect();return [e.id,Math.round(r.width),Math.round(r.height)]})""")
    check("系统按钮组共 5 个", isinstance(others, list) and len(others) == 5, str(others))
    if isinstance(others, list) and b:
        sizes = {(w, h) for _, w, h in others}
        check("ⓘ 与其它系统按钮同尺寸", len(sizes) == 1, str(sizes))
    check("有 tooltip", "关于" in str(m.eval(
        "document.getElementById('btn-about').title")), "")
    check("图标是单色 SVG（不是 emoji）", m.eval(
        "!!document.querySelector('#btn-about svg')"), "")

    print("\n[B] 打开关于窗")
    check("点之前没有 about 窗口", "about" not in cdp.pages())
    click_center(m, "#btn-about")
    ps = wait_about(True)
    check("点 ⓘ 后出现 about 窗口", "about" in ps, str(sorted(ps)))
    ps2 = wait_about(True)
    click_center(m, "#btn-about")
    time.sleep(1.2)
    ps3 = cdp.pages()
    n_about = sum(1 for k in ps3 if k == "about")
    check("连点两次不会开出第二个", n_about == 1, "about 数量 %d" % n_about)

    if "about" not in ps3:
        print("\n关于窗没开出来，后续用例无法进行")
        print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
        kill()
        return FAIL
    ab = ps3["about"]

    print("\n[C] 关于窗内容")
    check("about 窗口无 JS 报错", not ab.eval("String(window.__err||'')"),
          str(ab.eval("String(window.__err||'')"))[:80])
    check("body 标记为 about", ab.eval("document.body.dataset.w") == "about",
          str(ab.eval("document.body.dataset.w")))
    check("标题是「关于 宁宁静听」",
          "关于" in str(ab.eval("document.querySelector('.pane.abt .t-name').textContent")), "")
    check("名称是「宁宁静听」",
          ab.eval("document.querySelector('.abt-name').textContent.trim()") == "宁宁静听", "")
    got_ver = ab.eval("document.getElementById('abt-ver').textContent.trim()")
    check("版本号与 Cargo.toml 一致", got_ver == ver, "页面 %s / Cargo %s" % (got_ver, ver))
    check("版本号不是占位符", got_ver not in ("", "?", "0.0.0"), got_ver)
    check("作者是「龙哥」",
          ab.eval("document.querySelectorAll('.abt-row .v')[0].textContent.trim()") == "龙哥", "")
    mail = ab.eval("document.querySelector('.abt-row .v.mail').textContent.trim()")
    check("E-MAIL 正确", mail == "6040466@qq.com", mail)
    intro = ab.eval("document.querySelector('.abt-intro').textContent")
    check("简介写了「纯本地」", "纯本地" in str(intro), "")
    check("简介提到了支持格式", "FLAC" in str(intro) and "MP3" in str(intro), "")
    check("有「确定」按钮", rect(ab, "#abt-ok") is not None)
    check("有 ✕ 关闭按钮", rect(ab, "#abt-close") is not None)
    check("没有「复制邮箱」", "复制" not in str(
        ab.eval("document.querySelector('.pane.abt').innerText")), "按需求去掉了")
    check("皮肤名跟着当前皮肤", str(ab.eval(
        "document.getElementById('abt-skin').textContent")) in ("经典紫罗兰", "经典钢蓝", "现代深色"),
        str(ab.eval("document.getElementById('abt-skin').textContent")))

    print("\n[D] 布局：内容不出框")
    pane = rect(ab, ".pane.abt")
    check("对话框铺满窗内区域", pane is not None and pane[3] >= 250, str(pane))
    if pane:
        check("四周留出投影边距（10~14px）",
              10 <= pane[0] <= 14 and 10 <= pane[1] <= 14,
              "left=%d top=%d" % (pane[0], pane[1]))
    overflow = ab.eval("""(()=>{const b=document.querySelector('.abt-body');
        return [b.scrollHeight-b.clientHeight, b.scrollWidth-b.clientWidth]})()""")
    check("内容没有纵向溢出", isinstance(overflow, list) and overflow[0] <= 1, str(overflow))
    check("内容没有横向溢出", isinstance(overflow, list) and overflow[1] <= 1, str(overflow))
    foot = rect(ab, ".abt-foot")
    kvlast = rect(ab, ".abt-kv .abt-row:last-child")
    if foot and kvlast:
        check("邮箱行在「确定」上方且不重叠", kvlast[1] + kvlast[3] <= foot[1] + 1,
              "邮箱底 %d / 按钮顶 %d" % (kvlast[1] + kvlast[3], foot[1]))
    if foot:
        check("「确定」按钮完整可见", foot[1] + foot[3] <= (pane[3] + pane[1] if pane else 999),
              "")
    nm, vr = rect(ab, ".abt-name"), rect(ab, ".abt-ver")
    if nm and vr:
        check("版本行没压住应用名", vr[1] >= nm[1] + nm[3] - 1,
              "名底 %d / 版本顶 %d" % (nm[1] + nm[3], vr[1]))
    logo = rect(ab, ".abt-logo")
    check("标志是 44×44", logo is not None and logo[2] == 44 and logo[3] == 44, str(logo))
    nbar = ab.eval("document.querySelectorAll('.abt-logo i').length")
    check("标志里是 5 根频谱柱", nbar == 5, str(nbar))

    print("\n[E] 关闭与重开")
    click_center(ab, "#abt-ok")
    ps = wait_about(False)
    check("点「确定」能关掉", "about" not in ps, str(sorted(ps)))

    click_center(m, "#btn-about")
    ps = wait_about(True)
    check("关掉后还能再打开", "about" in ps, "")
    if "about" in ps:
        ab = ps["about"]
        click_center(ab, "#abt-close")
        ps = wait_about(False)
        check("点 ✕ 能关掉", "about" not in ps, "")

    click_center(m, "#btn-about")
    ps = wait_about(True)
    if "about" in ps:
        ab = ps["about"]
        ab.call("Input.dispatchKeyEvent", type="keyDown", key="Escape",
                code="Escape", windowsVirtualKeyCode=27)
        ab.call("Input.dispatchKeyEvent", type="keyUp", key="Escape",
                code="Escape", windowsVirtualKeyCode=27)
        ps = wait_about(False)
        check("按 Esc 能关掉", "about" not in ps, "")
    else:
        check("按 Esc 能关掉", False, "没打开")

    print("\n[F] 定位：居中压在主体上")
    click_center(m, "#btn-about")
    ps = wait_about(True)
    if "about" in ps:
        ab = ps["about"]
        pos = ab.eval("""(async()=>{const w=window.__TAURI__.window.getCurrentWindow();
            const p=await w.outerPosition(); const s=await w.outerSize();
            return [p.x,p.y,s.width,s.height]})()""")
        mpos = m.eval("""(async()=>{const w=window.__TAURI__.window.getCurrentWindow();
            const p=await w.outerPosition(); const s=await w.outerSize();
            return [p.x,p.y,s.width,s.height]})()""")
        check("拿到两个窗口的几何", isinstance(pos, list) and isinstance(mpos, list),
              "%s / %s" % (pos, mpos))
        if isinstance(pos, list) and isinstance(mpos, list):
            check("尺寸是 364×282", pos[2] == 364 and pos[3] == 282, str(pos[2:]))
            dcx = (pos[0] + pos[2] / 2) - (mpos[0] + mpos[2] / 2)
            dcy = (pos[1] + pos[3] / 2) - (mpos[1] + mpos[3] / 2)
            mx = m.eval("window.devicePixelRatio")
            tol = 3 * (mx or 1)
            check("水平居中（±3 逻辑像素）", abs(dcx) <= tol, "差 %.1f 物理px" % dcx)
            check("垂直居中（±3 逻辑像素）", abs(dcy) <= tol, "差 %.1f 物理px" % dcy)
        # 关窗会把 CDP 连接一起断掉，这一下别等返回值
        try:
            ab.eval("window.__TAURI__.window.getCurrentWindow().close()")
        except Exception:
            pass
        wait_about(False)

    print("\n[G] 不干扰主体")
    m = cdp.pages()["main"]
    sz = m.eval("""(async()=>{const w=window.__TAURI__.window.getCurrentWindow();
        const s=await w.outerSize(); return [s.width,s.height]})()""")
    check("主体仍是 390×160（逻辑）或等比缩放", isinstance(sz, list) and sz[0] >= 390, str(sz))
    check("主体无 JS 报错", not m.eval("String(window.__err||'')"),
          str(m.eval("String(window.__err||'')"))[:80])
    if os.path.exists(LAYOUT):
        txt = open(LAYOUT, encoding="utf-8").read()
        check("布局文件里没有 about 行", "about" not in txt,
              [l for l in txt.splitlines() if "about" in l][:2])
    else:
        check("布局文件里没有 about 行", True, "文件还没落盘")
    check("ⓘ 没有改变主体可视区高度",
          m.eval("document.querySelector('.side').getBoundingClientRect().height") > 100, "")

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    kill()
    return FAIL


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
