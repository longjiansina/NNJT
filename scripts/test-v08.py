"""v0.8 界面增强测试
① 音量刻度 0/50/100  ② 播放行三角  ③ 查找过滤  ④ 信息行序号 + 状态文字 + 播放键放大
⑤ 均衡器 ON / RESET / PRESETS + dB 刻度   ⑥ 深色皮肤不糊   ⑦ 播放列表左栏多列表
"""
import json
import os
import subprocess
import time

import cdp

PASS = FAIL = 0
EXE = r"D:\AI\BFQ\ttplayer\src-tauri\target\release\ttplayer.exe"
CONF = os.path.expandvars(r"%APPDATA%\com.ttplayer.player")
SESSION = os.path.join(CONF, "session.json")
MP3 = r"E:\MP3"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %-34s %s" % (name, detail))
    else:
        FAIL += 1
        print("  FAIL  %-34s %s" % (name, detail))


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
                time.sleep(2.4)
                return cdp.pages()
        except Exception:
            pass
    raise RuntimeError("启动超时")


def quit_app(ps):
    try:
        ps["main"].eval("ME.close()")
    except Exception:
        pass
    for _ in range(25):
        time.sleep(0.3)
        try:
            cdp.list_targets()
        except Exception:
            return True
    kill()
    return False


def js(ps, win, expr):
    return ps[win].eval(expr)


def add(ps, files):
    js(ps, "main", "window.__TAURI__.event.emit('tt:cmd',{t:'addPaths',paths:%s})"
       % json.dumps(files, ensure_ascii=False))


def open_panels(ps):
    for k in ("playlist", "eq"):
        if not js(ps, k, "ME.isVisible()"):
            js(ps, k, "togglePanelWin('%s')" % k)
            time.sleep(0.7)
    time.sleep(0.5)


def main():
    global PASS, FAIL
    kill()
    for f in os.listdir(CONF):
        if f.startswith("session") or f.startswith("window-layout"):
            os.remove(os.path.join(CONF, f))
    print("干净启动 v0.8")
    ps = start()
    open_panels(ps)

    print("\n1. 主窗：音量条 / 播放键 / 序号 + 状态行")
    # v0.12 主体改成左屏右控，右栏只剩 260px：0/50/100 刻度放不下，已经撤掉
    check("有音量条和静音键",
          js(ps, "main", "!!document.getElementById('vol-track')") is not False
          and js(ps, "main", "!!document.getElementById('btn-mute')") is not False,
          "track/mute 都在")
    btns = json.loads(js(ps, "main", """JSON.stringify(
        ['btn-prev','btn-play','btn-next'].map(id=>{
          const r=document.getElementById(id).getBoundingClientRect();return Math.round(r.width);}))"""))
    # 右栏只有 260px 宽，圆键缩到 22px，播放键 28px
    check("播放键比其它键大", btns[1] > btns[0] and btns[1] == 28, str(btns))
    check("状态文字存在", "状态:" in js(ps, "main", "document.getElementById('stat-state').textContent"),
          js(ps, "main", "document.getElementById('stat-state').textContent"))

    print("\n2. 加入 5 首，播放第 3 首")
    files = cdp.audio_files(MP3, limit=5, exts=(".mp3",))
    add(ps, files)
    time.sleep(1.4)
    js(ps, "main", "window.__TAURI__.event.emit('tt:cmd',{t:'playAt',index:2})")
    time.sleep(2.2)
    idx = js(ps, "main", "document.getElementById('info-idx').textContent")
    check("序号显示「第 3 / 5 首」", "3" in str(idx) and "5" in str(idx), str(idx))
    meta = js(ps, "main", "document.getElementById('stat-meta').textContent")
    check("状态行显示格式与采样率", "MP3" in meta and "kHz" in meta, meta)
    check("状态显示播放", "播放" in js(ps, "main", "document.getElementById('stat-state').textContent"),
          js(ps, "main", "document.getElementById('stat-state').textContent"))

    print("\n3. 播放列表：三角标记 + 查找")
    tri = js(ps, "playlist", """(()=>{const li=document.querySelector('#playlist li.playing');
        if(!li) return 'no-playing';
        const s=getComputedStyle(li,'::before');
        const o=document.querySelector('#playlist li:not(.playing)');
        return JSON.stringify({playing:s.backgroundColor, w:s.width,
                               other:getComputedStyle(o,'::before').backgroundColor});})()""")
    t = json.loads(tri) if tri.startswith("{") else {}
    check("播放行有三角、其它行透明",
          t and t["playing"] not in ("rgba(0, 0, 0, 0)", "transparent")
          and t["other"] in ("rgba(0, 0, 0, 0)", "transparent"), str(t))

    check("工具栏有查找按钮", js(ps, "playlist", "!!document.getElementById('btn-find')"))
    js(ps, "playlist", "document.getElementById('btn-find').click()")
    time.sleep(0.4)
    check("查找栏展开", js(ps, "playlist", "document.getElementById('findbar').hidden") is False)
    js(ps, "playlist", """(()=>{const i=document.getElementById('find-input');
        i.value='失眠'; i.dispatchEvent(new Event('input'));})()""")
    time.sleep(0.4)
    hidden = js(ps, "playlist",
                "[...document.querySelectorAll('#playlist li')].filter(l=>l.hidden).length")
    check("过滤后只剩命中行", hidden == 4, "隐藏 %s 行 / 共 5 行" % hidden)
    check("命中计数显示 1 / 1",
          js(ps, "playlist", "document.getElementById('find-num').textContent").replace(" ", "") == "1/1",
          js(ps, "playlist", "document.getElementById('find-num').textContent"))
    js(ps, "playlist", """document.getElementById('find-input').dispatchEvent(
        new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))""")
    time.sleep(1.6)
    check("回车播放命中那首",
          "失眠" in js(ps, "main", "state.playlist[state.curr].name"),
          js(ps, "main", "state.playlist[state.curr].name"))
    js(ps, "playlist", "document.getElementById('find-close').click()")
    time.sleep(0.4)
    hidden = js(ps, "playlist",
                "[...document.querySelectorAll('#playlist li')].filter(l=>l.hidden).length")
    check("关闭查找后全部恢复", hidden == 0, "隐藏 %s 行" % hidden)

    print("\n4. 多列表：新建 / 切换 / 改名 / 删除")
    js(ps, "playlist", "document.getElementById('btn-new-list').click()")
    time.sleep(1.0)
    side = json.loads(js(ps, "playlist",
                         "JSON.stringify([...document.querySelectorAll('#pl-lists li')].map(l=>l.textContent))"))
    check("左栏出现两个列表", len(side) == 2, str(side))
    check("新列表是空的", js(ps, "playlist", "document.querySelectorAll('#playlist li').length") == 0,
          js(ps, "playlist", "document.querySelectorAll('#playlist li').length"))
    check("标题显示当前列表",
          js(ps, "playlist", "document.querySelector('.pane[data-pane=\\'playlist\\'] .t-name').textContent").endswith("列表 2"),
          js(ps, "playlist", "document.querySelector('.pane[data-pane=\\'playlist\\'] .t-name').textContent"))

    add(ps, files[1:3])
    time.sleep(1.3)
    check("新列表加了 2 首",
          js(ps, "playlist", "document.querySelectorAll('#playlist li').length") == 2,
          js(ps, "playlist", "document.querySelectorAll('#playlist li').length"))
    js(ps, "playlist", "document.querySelectorAll('#pl-lists li')[0].click()")
    time.sleep(1.2)
    check("切回默认列表仍是 5 首",
          js(ps, "playlist", "document.querySelectorAll('#playlist li').length") == 5,
          js(ps, "playlist", "document.querySelectorAll('#playlist li').length"))

    js(ps, "main", "window.__TAURI__.event.emit('tt:cmd',{t:'renameList',index:1,name:'雅尼音乐会'})")
    time.sleep(0.8)
    names = json.loads(js(ps, "playlist",
                          "JSON.stringify([...document.querySelectorAll('#pl-lists li .lname')].map(e=>e.textContent))"))
    check("改名生效", names[1] == "雅尼音乐会", str(names))

    js(ps, "playlist", "document.querySelectorAll('#pl-lists li .ldel')[1].click()")
    time.sleep(1.0)
    left = js(ps, "playlist", "document.querySelectorAll('#pl-lists li').length")
    check("删除列表后只剩一个", left == 1, str(left))
    check("删完后不再显示删除按钮", js(ps, "playlist", "document.querySelectorAll('.ldel').length") == 0)

    print("\n5. 均衡器：ON / RESET / PRESETS / dB 刻度")
    db = json.loads(js(ps, "eq", "JSON.stringify([...document.querySelectorAll('.eq-db span')].map(e=>e.textContent))"))
    check("dB 三档刻度", db == ["+12", "0", "-12"], str(db))
    check("预设菜单 6 项", js(ps, "eq", "document.querySelectorAll('#eq-menu .om-item').length") == 6)
    js(ps, "eq", "document.getElementById('eq-presets').click()")
    time.sleep(0.3)
    check("PRESETS 能展开", js(ps, "eq", "document.getElementById('eq-menu').hidden") is False)
    js(ps, "eq", "document.querySelectorAll('#eq-menu .om-item')[2].click()")   # 摇滚
    time.sleep(0.7)
    rock = json.loads(js(ps, "main", "JSON.stringify(state.eq)"))
    check("选中摇滚后曲线生效", rock == [5, 4, 2, -1, -2, -1, 2, 4, 5, 5], str(rock))
    check("选完菜单自动收起", js(ps, "eq", "document.getElementById('eq-menu').hidden") is True)

    js(ps, "eq", "document.getElementById('eq-on').click()")
    time.sleep(0.6)
    check("ON 可旁路", js(ps, "main", "state.eqOn") is False,
          js(ps, "eq", "document.getElementById('eq-on').textContent"))
    check("按钮变成 OFF 且压暗",
          js(ps, "eq", "document.getElementById('eq-on').textContent") == "OFF"
          and "off" in js(ps, "eq", "document.getElementById('eq-on').className"))
    js(ps, "eq", "document.getElementById('eq-on').click()")
    time.sleep(0.5)
    check("再点恢复开启", js(ps, "main", "state.eqOn") is True)
    js(ps, "eq", "document.getElementById('eq-reset').click()")
    time.sleep(0.6)
    check("RESET 全部归零", json.loads(js(ps, "main", "JSON.stringify(state.eq)")) == [0] * 10,
          str(json.loads(js(ps, "main", "JSON.stringify(state.eq)"))))

    print("\n6. 深色皮肤下新控件仍可读")
    js(ps, "main", "window.__TAURI__.event.emit('tt:cmd',{t:'skin',value:'modern'})")
    time.sleep(0.9)
    c1 = js(ps, "playlist", "getComputedStyle(document.querySelector('.findbar input')).backgroundImage")
    c2 = js(ps, "eq", "getComputedStyle(document.getElementById('eq-on')).backgroundImage")
    c3 = js(ps, "playlist", "getComputedStyle(document.querySelector('.side-add')).backgroundImage")
    check("查找框在深色皮肤下有底色", "gradient" in c1, c1[:40])
    check("EQ 按钮在深色皮肤下重绘", "gradient" in c2, c2[:40])
    check("新建按钮跟随皮肤", "gradient" in c3, c3[:40])
    js(ps, "main", "window.__TAURI__.event.emit('tt:cmd',{t:'skin',value:'retro'})")
    time.sleep(0.5)

    print("\n7. 多列表落盘与还原")
    js(ps, "playlist", "document.getElementById('btn-new-list').click()")
    time.sleep(0.9)
    add(ps, files[3:5])
    time.sleep(1.3)
    before = json.loads(js(ps, "main", """JSON.stringify({
        active: state.active,
        lists: state.lists.map(l=>({name:l.name,n:l.tracks.length})),
        curr: state.curr})"""))
    time.sleep(0.8)
    raw = json.load(open(SESSION, encoding="utf-8"))
    check("session.json 升级为 v3", raw.get("v") == 3, str(raw.get("v")))
    check("落盘了两个列表", len(raw.get("lists", [])) == 2,
          str([l["name"] for l in raw.get("lists", [])]))
    quit_app(ps)
    ps = start()
    after = json.loads(js(ps, "main", """JSON.stringify({
        active: state.active,
        lists: state.lists.map(l=>({name:l.name,n:l.tracks.length})),
        curr: state.curr})"""))
    check("重启后列表数与活动项一致",
          after["lists"] == before["lists"] and after["active"] == before["active"],
          "%s vs %s" % (after, before))
    check("当前曲目还原", after["curr"] == before["curr"],
          "%s vs %s" % (after["curr"], before["curr"]))
    open_panels(ps)
    side = json.loads(js(ps, "playlist",
                         "JSON.stringify([...document.querySelectorAll('#pl-lists li .lname')].map(e=>e.textContent))"))
    check("面板左栏重建出两个列表", len(side) == 2, str(side))

    print("\n8. 运行期无 JS 异常")
    for w in ("main", "playlist", "eq"):
        err = js(ps, w, "JSON.stringify(window.__err||[])")
        check("%s 窗口无报错" % w, err == "[]", err)

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    quit_app(ps)


if __name__ == "__main__":
    main()
