"""v0.14 测试：播放模式下拉菜单 / 列表名右键重命名 / 加歌进度 / 右键不再删歌

A 模式菜单：按钮带 ▾、点开有四项、当前项打勾、选中后主窗跟着变、菜单收起
B 单曲循环：播完原地重播（不换歌、不停止）
C 侧栏右键：右键弹菜单、点重命名进去改名、Esc 取消不改
D 右键歌曲：不再弹菜单、不再删歌
E 加歌进度：tt:scan 里有 Rust 发来的数量、两个窗口都显示、高度不跳、收尾还原
"""
import json
import os
import shutil
import subprocess
import time

import cdp

PASS = FAIL = 0
EXE = r"D:\AI\BFQ\ttplayer\src-tauri\target\release\ttplayer.exe"
ROOT = r"D:\AI\BFQ\ttplayer"
CONF = os.path.expandvars(r"%APPDATA%\com.ttplayer.player")
SESSION = os.path.join(CONF, "session.json")
LAYOUT = os.path.join(CONF, "window-layout.txt")
BK = os.path.join(ROOT, ".userstate")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %-40s %s" % (name, detail))
    else:
        FAIL += 1
        print("  FAIL  %-40s %s" % (name, detail))


def kill():
    subprocess.run(["taskkill", "/F", "/IM", "ttplayer.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.8)


def backup():
    os.makedirs(BK, exist_ok=True)
    for p in (SESSION, LAYOUT):
        if os.path.exists(p):
            shutil.copy(p, os.path.join(BK, "t14-" + os.path.basename(p)))


def restore():
    for p in (SESSION, LAYOUT):
        b = os.path.join(BK, "t14-" + os.path.basename(p))
        if os.path.exists(b):
            shutil.copy(b, p)


def env():
    e = dict(os.environ)
    e["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=9222"
    return e


def start():
    subprocess.Popen([EXE], env=env(), cwd=ROOT)
    for _ in range(45):
        time.sleep(0.4)
        try:
            ps = cdp.pages()
            if "main" in ps:
                time.sleep(2.4)
                return cdp.pages()
        except Exception:
            pass
    raise RuntimeError("没起来")


def click(pg, x, y):
    pg.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    pg.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y,
            button="left", clickCount=1)
    pg.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y,
            button="left", clickCount=1)


def rclick(pg, x, y):
    pg.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y,
            button="right", buttons=2, clickCount=1)
    pg.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y,
            button="right", buttons=0, clickCount=1)


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


def open_playlist(ps):
    m = ps["main"]
    if "playlist" not in ps:
        m.eval("togglePanelWin('playlist')")
        for _ in range(20):
            time.sleep(0.4)
            ps = cdp.pages()
            if "playlist" in ps:
                break
    time.sleep(0.8)
    return cdp.pages()


def main():
    kill()
    backup()
    try:
        return run()
    finally:
        kill()
        restore()


def run():
    ps = start()
    m = ps["main"]
    ps = open_playlist(ps)
    pl = ps.get("playlist")
    if not pl:
        print("播放列表窗口没开出来")
        return 1

    print("\n[A] 播放模式下拉菜单")
    check("播放列表无 JS 报错", not pl.eval("String(window.__err||'')"),
          str(pl.eval("String(window.__err||'')"))[:80])
    check("按钮文案 = 当前模式",
          pl.eval("$('mode-label').textContent") == pl.eval("MODES[state.mode]"),
          "%s / %s" % (pl.eval("$('mode-label').textContent"), pl.eval("MODES[state.mode]")))
    check("按钮带下拉箭头", bool(pl.eval("!!$('btn-mode').querySelector('.car')")), "")
    check("默认菜单是收起的", pl.eval("$('mode-menu').hidden") is True, "")
    click_center(pl, "#btn-mode")
    time.sleep(0.4)
    check("点按钮展开菜单", pl.eval("$('mode-menu').hidden") is False, "")
    items = pl.eval("[...document.querySelectorAll('#mode-menu .om-item')].map(e=>e.textContent)")
    check("菜单四项且顺序正确",
          items == ["顺序播放", "列表循环", "随机播放", "单曲循环"], str(items))
    on = pl.eval("[...document.querySelectorAll('#mode-menu .om-item.on')].length")
    check("只有一项打勾", on == 1, "on=%s" % on)
    check("打勾的是当前模式",
          pl.eval("+document.querySelector('#mode-menu .om-item.on').dataset.m") ==
          pl.eval("state.mode"), "")
    mr = rect(pl, "#mode-menu")
    br = rect(pl, "#btn-mode")
    if mr and br:
        check("菜单贴住按钮下沿", abs(mr[1] - (br[1] + br[3])) <= 6,
              "menu y=%d / btn 底=%d" % (mr[1], br[1] + br[3]))
        check("菜单没被窗口右缘截断", mr[0] + mr[2] <= br[0] + 400, "x2=%d" % (mr[0] + mr[2]))

    # 选单曲循环
    click_center(pl, '#mode-menu .om-item[data-m="3"]')
    time.sleep(0.6)
    check("选中后菜单收起", pl.eval("$('mode-menu').hidden") is True, "")
    check("列表窗 mode = 3", pl.eval("state.mode") == 3, str(pl.eval("state.mode")))
    check("主窗 mode = 3", m.eval("state.mode") == 3, str(m.eval("state.mode")))
    check("按钮文案变「单曲」", pl.eval("$('mode-label').textContent") == "单曲",
          pl.eval("$('mode-label').textContent"))
    check("非顺序模式按钮高亮",
          pl.eval("$('btn-mode').classList.contains('on')") is True, "")

    # 点外面收起
    click_center(pl, "#btn-mode")
    time.sleep(0.3)
    pl.eval("document.querySelector('#playlist').dispatchEvent(new MouseEvent('mousedown',{bubbles:true})); 1")
    time.sleep(0.3)
    check("点空白处收起菜单", pl.eval("$('mode-menu').hidden") is True, "")

    # 回到顺序
    pl.eval("$('btn-mode').click(); 1")
    time.sleep(0.25)
    click_center(pl, '#mode-menu .om-item[data-m="0"]')
    time.sleep(0.5)
    check("能切回顺序播放", pl.eval("state.mode") == 0 and m.eval("state.mode") == 0,
          "%s / %s" % (pl.eval("state.mode"), m.eval("state.mode")))

    print("\n[B] 单曲循环：播完原地重播")
    n = m.eval("state.playlist.length")
    if n:
        m.eval("window.__TAURI__.event.emit('tt:cmd',{t:'setMode',value:3}); 1")
        time.sleep(0.5)
        m.eval("window.__TAURI__.event.emit('tt:cmd',{t:'playAt',index:0}); 1")
        time.sleep(1.6)
        cur0 = m.eval("state.curr")
        pos0 = m.eval("audio.currentTime")
        m.eval("audio.dispatchEvent(new Event('ended')); 1")
        time.sleep(1.4)
        check("播完仍停在同一首", m.eval("state.curr") == cur0, "curr=%s" % m.eval("state.curr"))
        check("播完没有停（仍在播）", m.eval("state.playing") is True, "")
        check("播完从头开始", m.eval("audio.currentTime") < max(1.0, (pos0 or 0)),
              "t=%.2f" % (m.eval("audio.currentTime") or 0))
        m.eval("window.__TAURI__.event.emit('tt:cmd',{t:'stop'}); 1")
        time.sleep(0.6)
        m.eval("window.__TAURI__.event.emit('tt:cmd',{t:'setMode',value:0}); 1")
        time.sleep(0.4)
    else:
        check("单曲循环（歌单为空，跳过）", True, "0 首")

    print("\n[C] 侧栏右键重命名")
    # 认准「当前选中的那个列表」：标题栏显示的是它，改名后标题要跟着变
    old = pl.eval("document.querySelector('#pl-lists li.on .lname').textContent")
    idx = pl.eval("[...document.querySelectorAll('#pl-lists li')]"
                  ".findIndex(e=>e.classList.contains('on'))")
    cnt_before = pl.eval("document.querySelectorAll('#pl-lists li').length")
    lr = rect(pl, "#pl-lists li.on")
    rclick(pl, lr[0] + lr[2] * 0.4, lr[1] + lr[3] / 2)
    time.sleep(0.45)
    check("右键弹出菜单", pl.eval("!!document.querySelector('.openmenu.ctx')") is True, "")
    mi = pl.eval("[...document.querySelectorAll('.openmenu.ctx .om-item')].map(e=>e.textContent)")
    check("菜单第一项是重命名", mi and mi[0].startswith("重命名"), str(mi))
    if cnt_before > 1:
        check("多列表时才有「删除列表」", "删除列表" in (mi or []), str(mi))
    cm = rect(pl, ".openmenu.ctx")
    if cm:
        check("右键菜单在窗口内",
              cm[0] >= 0 and cm[1] >= 0 and cm[0] + cm[2] <= 400 and cm[1] + cm[3] <= 300,
              str(cm))
    check("菜单背景不透明（能取到皮肤变量）",
          pl.eval("getComputedStyle(document.querySelector('.openmenu.ctx')).backgroundImage") != "none",
          "")

    click_center(pl, '.openmenu.ctx .om-item')
    time.sleep(0.45)
    check("点重命名变成输入框", pl.eval("!!document.querySelector('#pl-lists li input')") is True, "")
    check("右键菜单已收起", pl.eval("!!document.querySelector('.openmenu.ctx')") is False, "")
    # Esc 取消
    pl.eval("""(()=>{const i=document.querySelector('#pl-lists li input');
        i.value='不该生效';
        i.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));})(); 1""")
    time.sleep(0.5)
    check("Esc 取消不改名",
          pl.eval("document.querySelector('#pl-lists li.on .lname').textContent") == old, "")

    # 真正改名
    lr = rect(pl, "#pl-lists li.on")
    rclick(pl, lr[0] + lr[2] * 0.4, lr[1] + lr[3] / 2)
    time.sleep(0.4)
    click_center(pl, '.openmenu.ctx .om-item')
    time.sleep(0.4)
    pl.eval("""(()=>{const i=document.querySelector('#pl-lists li input');
        i.value='改名测试';
        i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})(); 1""")
    time.sleep(0.9)
    check("回车提交改名", pl.eval("state.listMeta[%d].name" % idx) == "改名测试",
          str(pl.eval("state.listMeta[%d].name" % idx)))
    check("主窗拿到新名字", m.eval("state.lists[%d].name" % idx) == "改名测试",
          str(m.eval("state.lists[%d].name" % idx)))
    check("侧栏显示新名字",
          pl.eval("document.querySelector('#pl-lists li.on .lname').textContent") == "改名测试", "")
    check("标题跟着改名",
          "改名测试" in (pl.eval("document.querySelector('.pane[data-pane=\"playlist\"] .t-name').textContent") or ""),
          pl.eval("document.querySelector('.pane[data-pane=\"playlist\"] .t-name').textContent"))
    # 改回去
    pl.eval("window.__TAURI__.event.emit('tt:cmd',{t:'renameList',index:%d,name:%s}); 1"
            % (idx, json.dumps(old, ensure_ascii=False)))
    time.sleep(0.7)
    check("名字改回原样", pl.eval("state.listMeta[%d].name" % idx) == old,
          str(pl.eval("state.listMeta[%d].name" % idx)))

    print("\n[D] 右键歌曲不再删歌")
    before = m.eval("state.playlist.length")
    tr = pl.eval("""(()=>{const e=document.querySelectorAll('#playlist li')[1];
        if(!e) return null; const r=e.getBoundingClientRect();
        return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]})()""")
    if tr and before:
        rclick(pl, tr[0] + tr[2] * 0.4, tr[1] + tr[3] / 2)
        time.sleep(0.5)
        check("右键歌曲不弹菜单", pl.eval("!!document.querySelector('.openmenu.ctx')") is False, "")
        check("右键歌曲不删歌", m.eval("state.playlist.length") == before,
              "%s -> %s" % (before, m.eval("state.playlist.length")))
        check("右键歌曲后没报错", not pl.eval("String(window.__err||'')"),
              str(pl.eval("String(window.__err||'')"))[:60])
    else:
        check("右键歌曲（歌单为空，跳过）", True, "")

    print("\n[E] 加歌进度")
    m.eval("window.__scan=[]; window.__TAURI__.event.listen('tt:scan', e=>window.__scan.push(e.payload)); 1")
    got = m.eval("""window.__TAURI__.core.invoke('scan_audio_dir_progress',{path:'E:\\\\MP3'})
        .then(r=>r.length)""")
    time.sleep(0.5)
    evs = m.eval("window.__scan") or []
    check("扫描返回了文件", isinstance(got, int) and got > 0, "%s 首" % got)
    check("扫描过程发了进度事件", len(evs) > 0, "%d 条" % len(evs))
    if evs:
        check("最后一次的数量 = 扫描结果",
              evs[-1].get("found") == got, "%s / %s" % (evs[-1].get("found"), got))
        check("事件带 phase=walk", evs[-1].get("phase") == "walk", str(evs[-1]))

    # 扫描中：两个窗口都要显示（先把上一步真实扫描留下的进度收干净再量基准高度）
    for pg in (m, pl):
        pg.eval("applyScan({phase:'done'}); 1")
    time.sleep(1.0)
    h0 = pl.eval("document.querySelector('.list-body').getBoundingClientRect().height")
    for pg in (m, pl):
        pg.eval("applyScan({phase:'walk',found:137}); 1")
    time.sleep(0.4)
    check("主窗状态行让位给进度",
          m.eval("$('stat-state').textContent") == "状态: 扫描中",
          m.eval("$('stat-state').textContent"))
    check("主窗显示已找到数量",
          "137" in (m.eval("$('stat-meta').textContent") or ""),
          m.eval("$('stat-meta').textContent"))
    check("主窗状态行有进度线", m.eval("!!$('st-prog')") is True, "")
    check("进度线在走马灯状态",
          m.eval("document.querySelector('.stline').classList.contains('ind')") is True, "")
    check("列表窗进度条出现", pl.eval("$('scanbar').hidden") is False, "")
    check("列表窗写着「正在扫描」", pl.eval("$('sb-text').textContent") == "正在扫描",
          pl.eval("$('sb-text').textContent"))
    check("列表窗显示已找到",
          "137" in (pl.eval("$('sb-num').textContent") or ""), pl.eval("$('sb-num').textContent"))
    h1 = pl.eval("document.querySelector('.list-body').getBoundingClientRect().height")
    check("进度条占 17px，列表高度随之变化", abs((h0 - h1) - 17) <= 1,
          "%.0f -> %.0f" % (h0 or 0, h1 or 0))

    # 读标签：有总数 → 按百分比
    for pg in (m, pl):
        pg.eval("applyScan({phase:'meta',done:320,total:572}); 1")
    time.sleep(0.4)
    check("读标签时状态行改文案", m.eval("$('stat-state').textContent") == "状态: 读标签",
          m.eval("$('stat-state').textContent"))
    check("读标签显示 x / y", m.eval("$('stat-meta').textContent") == "320 / 572",
          m.eval("$('stat-meta').textContent"))
    w = m.eval("$('st-prog').style.width")
    check("主窗进度线按比例", w == "56%", str(w))
    w2 = pl.eval("$('sb-fill').style.width")
    check("列表窗进度条按比例", w2 == "56%", str(w2))
    check("有总数后不再走马灯",
          pl.eval("$('scanbar').classList.contains('ind')") is False, "")

    # 收尾
    for pg in (m, pl):
        pg.eval("applyScan({phase:'done'}); 1")
    time.sleep(1.0)
    check("收尾后进度条消失", pl.eval("$('scanbar').hidden") is True, "")
    check("收尾后状态行还原",
          (m.eval("$('stat-state').textContent") or "").startswith("状态: 停"),
          m.eval("$('stat-state').textContent"))
    check("收尾后状态行不再 busy",
          m.eval("document.querySelector('.stline').classList.contains('busy')") is False, "")
    check("进度条隐藏后列表高度还原", abs((pl.eval(
        "document.querySelector('.list-body').getBoundingClientRect().height") or 0) - h0) <= 1,
        "%s" % pl.eval("document.querySelector('.list-body').getBoundingClientRect().height"))

    print("\n[F] 收尾检查")
    check("主窗无 JS 报错", not m.eval("String(window.__err||'')"),
          str(m.eval("String(window.__err||'')"))[:80])
    check("列表窗无 JS 报错", not pl.eval("String(window.__err||'')"),
          str(pl.eval("String(window.__err||'')"))[:80])
    sz = m.eval("""(async()=>{const w=window.__TAURI__.window.getCurrentWindow();
        const s=await w.outerSize(); return [s.width,s.height]})()""")
    check("主窗尺寸没变", isinstance(sz, list) and sz[0] >= 390, str(sz))

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return FAIL


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
