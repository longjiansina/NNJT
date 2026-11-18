"""歌单记忆测试：上次加进来的文件 / 文件夹，下次启动原样恢复。"""
import json
import os
import subprocess
import time

import cdp

PASS = FAIL = 0
EXE = r"D:\AI\BFQ\ttplayer\src-tauri\target\release\ttplayer.exe"
SESSION = os.path.expandvars(r"%APPDATA%\com.ttplayer.player\session.json")
MP3 = r"E:\MP3"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %-30s %s" % (name, detail))
    else:
        FAIL += 1
        print("  FAIL  %-30s %s" % (name, detail))


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
                time.sleep(2.6)   # 等 restoreSession 读完 + 装载第一首
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


def pl(ps):
    return json.loads(ps["main"].eval(
        "JSON.stringify({n:state.playlist.length,curr:state.curr,"
        "names:state.playlist.slice(0,4).map(t=>t.name),"
        "title:(document.getElementById('info-title')||{}).textContent})"))


def panel_count(ps):
    return ps["playlist"].eval(
        "(document.getElementById('list-count')||{}).textContent")


def add_paths(ps, paths):
    ps["main"].eval("window.__TAURI__.event.emit('tt:cmd', {t:'addPaths', paths:%s})"
                    % json.dumps(paths))


def main():
    global PASS, FAIL
    kill()
    for f in (SESSION,):
        if os.path.exists(f):
            os.replace(f, f + ".pre")
    print("清空 session.json，干净启动")
    ps = start()
    m0 = pl(ps)
    check("首次启动是空歌单", m0["n"] == 0, str(m0))

    # 选 3 个真实文件
    files = cdp.audio_files(MP3, limit=3, exts=(".mp3",))
    print("\n1. 加入 3 个文件:", [os.path.basename(f) for f in files])
    add_paths(ps, files)
    time.sleep(1.6)
    m1 = pl(ps)
    print("   ", m1)
    check("歌单已加入 3 首", m1["n"] == 3, str(m1["n"]))
    check("自动选中第 1 首", m1["curr"] == 0, str(m1["curr"]))
    check("session.json 已写", os.path.exists(SESSION), SESSION)

    print("\n2. 再拖进一个目录（扫描展开）")
    folder = MP3
    got = ps["main"].eval(
        "(async()=>{const p=await window.__TAURI__.core.invoke('scan_audio_dir',{path:%s});"
        "window.__TAURI__.event.emit('tt:cmd',{t:'addPaths',paths:p});return p.length})()"
        % json.dumps(folder))
    time.sleep(1.8)
    m2 = pl(ps)
    print("   目录扫描到 %s 个文件 -> 歌单 %d 首" % (got, m2["n"]))
    check("目录里的歌全部进列表", m2["n"] == got, "%d vs %d" % (m2["n"], got))

    print("\n3. 切到第 2 首，然后正常退出")
    ps["main"].eval("window.__TAURI__.event.emit('tt:cmd', {t:'playAt', index:1})")
    time.sleep(2.0)
    m3 = pl(ps)
    check("当前曲目 = 1", m3["curr"] == 1, str(m3["curr"]))
    saved = m3
    time.sleep(0.6)          # 等 250ms 防抖落盘
    data = json.load(open(SESSION, encoding="utf-8"))
    # v2：{v, active, lists:[{name, curr, tracks}]}；v1 是顶层 tracks/curr
    lists = data.get("lists") or []
    cur_list = lists[data.get("active", 0)] if lists else {"tracks": data.get("tracks") or [],
                                                          "curr": data.get("curr", -1)}
    tracks = cur_list.get("tracks", [])
    check("落盘曲目数与内存一致", len(tracks) == saved["n"],
          "%d vs %d" % (len(tracks), saved["n"]))
    check("落盘 curr = 1", cur_list.get("curr") == 1, str(cur_list.get("curr")))
    # 只有放过的那首才会读到时长，检查当前曲目的时长有没有存下来
    cur_dur = tracks[cur_list.get("curr")].get("duration")
    check("落盘带时长", bool(cur_dur), str(cur_dur))
    quit_app(ps)

    print("\n4. 重启，看是否原样恢复")
    ps = start()
    b = pl(ps)
    print("   ", b)
    check("歌单数量一致", b["n"] == saved["n"], "%d vs %d" % (b["n"], saved["n"]))
    check("顺序一致", b["names"] == saved["names"], str(b["names"]))
    check("当前曲目还原", b["curr"] == saved["curr"], str(b["curr"]))
    check("标题栏显示上次那首", b["title"] and b["title"] != "宁宁静听", str(b["title"]))
    check("没有自动播放", ps["main"].eval("state.playing") is False, "")
    check("面板窗口也显示这些歌", panel_count(ps).strip().endswith("首")
          and not panel_count(ps).strip().startswith("0"), panel_count(ps))

    print("\n5. 删掉第 1 首再重启")
    ps["main"].eval("window.__TAURI__.event.emit('tt:cmd', {t:'remove', index:0})")
    time.sleep(1.2)
    after_del = pl(ps)
    check("已删除", after_del["n"] == saved["n"] - 1, str(after_del["n"]))
    saved2 = after_del
    quit_app(ps)
    ps = start()
    c = pl(ps)
    check("删除结果也记住了", c["n"] == saved2["n"], "%d vs %d" % (c["n"], saved2["n"]))
    check("剩下的第一首对得上", c["names"][0] == saved2["names"][0],
          "%s vs %s" % (c["names"][0], saved2["names"][0]))

    print("\n6. 清空歌单再重启，应该是空的")
    ps["main"].eval("window.__TAURI__.event.emit('tt:cmd', {t:'clear'})")
    time.sleep(1.2)
    quit_app(ps)
    ps = start()
    d = pl(ps)
    check("清空后重启仍为空", d["n"] == 0, str(d["n"]))

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    quit_app(ps)


if __name__ == "__main__":
    main()
