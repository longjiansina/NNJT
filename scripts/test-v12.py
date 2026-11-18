"""v0.12 测试：主体改「左屏右控」+ 紫皮 + 均衡器频率标签对齐

A 布局：左右分栏、屏宽占比、右栏元素齐全、标题栏按钮
B 三种可视化：点击左屏 spec → wave → bg → spec 轮换，canvas 真的在画
C 频谱：频段按对数分（不是左边一堵墙），有进度头
D 暂停：rAF 停掉，只留静止画面
E 持久化：形态进 session.json
F 皮肤：默认紫皮，可切回 retro / modern
G 均衡器：10 个频率标签正对 10 根滑块中心（v0.11 的偏差约 14px）
"""
import json
import os
import subprocess
import time

import cdp

PASS = FAIL = 0
EXE = r"D:\AI\BFQ\ttplayer\src-tauri\target\release\ttplayer.exe"
CONF = os.path.expandvars(r"%APPDATA%\com.ttplayer.player")
MP3 = r"E:\MP3\其他歌手\02 约定.mp3"


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


def main():
    kill()
    ps = start()
    m = ps["main"]

    print("\n[A] 布局：左屏右控")
    check("页面无 JS 报错", not m.eval("String(window.__err||'')"),
          str(m.eval("String(window.__err||'')"))[:80])
    scr = rect(m, "#screen")
    side = rect(m, ".side")
    vw = m.eval("innerWidth")
    check("左侧屏存在", scr is not None, str(scr))
    check("右侧栏存在", side is not None, str(side))
    if scr and side:
        check("两者左右并排（屏在左）", scr[0] < side[0],
              "screen.x=%d side.x=%d" % (scr[0], side[0]))
        check("右栏紧接屏右侧", abs((scr[0] + scr[2]) - side[0]) <= 2,
              "屏右沿 %d / 右栏左沿 %d" % (scr[0] + scr[2], side[0]))
        ratio = scr[2] / vw * 100
        check("屏宽约占 40~46%", 38 <= ratio <= 48, "%.1f%%" % ratio)
        check("屏占满内容区高度", abs(scr[3] - side[3]) <= 2,
              "屏高 %d / 右栏高 %d" % (scr[3], side[3]))

    for sel, name in [("#info-title", "歌名"), ("#time-cur", "当前时间"),
                      ("#time-total", "总时长"), ("#stat-state", "状态"),
                      ("#stat-meta", "编码信息"), ("#seek-track", "进度条"),
                      ("#info-album", "专辑行"), ("#info-idx", "曲目序号"),
                      ("#btn-prev", "上一首"), ("#btn-play", "播放"),
                      ("#btn-stop", "停止"), ("#btn-next", "下一首"),
                      ("#btn-mute", "静音"), ("#vol-track", "音量条")]:
        check("右栏有" + name, rect(m, sel) is not None)

    caps = m.eval("""[...document.querySelectorAll('.caps .cap')].map(b=>b.textContent)""")
    check("三个面板胶囊是 FL/EQ/LRC", caps == ["FL", "EQ", "LRC"], str(caps))
    check("标题栏有打开按钮", rect(m, ".t-open") is not None)

    print("\n[B] 三种可视化轮换")
    wav = MP3 if os.path.exists(MP3) else None
    if wav:
        m.eval("window.__TAURI__.event.emit('tt:cmd',{t:'addPaths',paths:[%s]})"
               % json.dumps(wav, ensure_ascii=False))
        time.sleep(1.0)

    MODES = ["spec", "wave", "bg"]

    def force_spec(pg):
        """把形态推回频谱态：bg 点底图，其余点左屏"""
        for _ in range(6):
            cur = pg.eval("state.osc")
            if cur == "spec":
                return True
            if cur == "bg":
                pg.eval("document.getElementById('osc-bg').click()")
            else:
                pg.eval("document.getElementById('screen').click()")
            time.sleep(0.9)
        return False

    check("能推回频谱态", force_spec(m), m.eval("state.osc"))

    # 从当前态开始连点三次，应当走完 spec → wave → bg → spec
    cx, cy = scr[0] + scr[2] // 2, scr[1] + scr[3] // 2
    start_mode = m.eval("state.osc")
    i0 = MODES.index(start_mode) if start_mode in MODES else 0
    for k in range(3):
        click(m, cx, cy)
        time.sleep(1.1)
        want = MODES[(i0 + k + 1) % 3]
        got = m.eval("state.osc")
        check("第 %d 击 → %s" % (k + 1, want), got == want, "得到 %s" % got)
        if want == "wave":
            check("左上标签跟着换", m.eval(
                "document.getElementById('spec-tag').textContent") == "WAVEFORM",
                m.eval("document.getElementById('spec-tag').textContent"))
        if want == "bg":
            check("铺满时左屏让位", m.eval(
                "getComputedStyle(document.getElementById('screen')).visibility") == "hidden")
            bgw = m.eval("document.getElementById('osc-bg').getBoundingClientRect().width") or 0
            check("底图铺满整个内容区", bgw > vw - 10, "底图 %.0f / 视口 %d" % (bgw, vw))

    check("三击回到起点", m.eval("state.osc") == start_mode,
          "%s → %s" % (start_mode, m.eval("state.osc")))

    print("\n[C] 频谱真的在画")
    force_spec(m)
    if wav:
        m.eval("window.__TAURI__.event.emit('tt:cmd',{t:'playAt',index:0})")
        time.sleep(2.2)

    def spec_px():
        return m.eval("""(()=>{const c=document.getElementById('spec-cv');
            const g=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
            let n=0; for(let i=0;i<g.length;i+=4) if(g[i+3]>30 && g[i+2]>g[i]) n++;
            return n})()""") or 0

    info = m.eval("window.__osc()") or {}
    check("播放中 rAF 在跑", info.get("raf") is True, str(info))
    if wav:
        check("音频真的在走", (info.get("cur") or 0) > 0.2,
              "currentTime=%.2f" % (info.get("cur") or 0))
        a = spec_px()
        time.sleep(0.4)
        b = spec_px()
        check("屏上有蓝色谱柱", a > 200, "%d px" % a)
        check("谱柱在变（不是死图）", a != b, "%d → %d" % (a, b))

    # 频段按对数分：高频段不能全是 0，低频段也不能全满
    shape = m.eval("""(()=>{
      const c=document.getElementById('spec-cv');
      const ctx=c.getContext('2d');
      const d=ctx.getImageData(0,0,c.width,c.height).data;
      const colH=[];
      for(let x=0;x<c.width;x+=2){
        let top=c.height;
        for(let y=0;y<c.height;y++){
          const i=(y*c.width+x)*4;
          if(d[i+3]>60){ top=y; break; }
        }
        colH.push(c.height-top);
      }
      const n=colH.length, third=Math.floor(n/3);
      const avg=a=>a.reduce((s,v)=>s+v,0)/a.length;
      return {left:+avg(colH.slice(0,third)).toFixed(1),
              mid:+avg(colH.slice(third,third*2)).toFixed(1),
              right:+avg(colH.slice(third*2)).toFixed(1)};
    })()""")
    if isinstance(shape, dict) and shape.get("left"):
        check("低频段高于高频段（不是一堵墙）", shape["left"] > shape["right"],
              "左 %.1f / 中 %.1f / 右 %.1f" % (shape["left"], shape["mid"], shape["right"]))
        check("高频段仍有可见高度", shape["right"] > 1.5,
              "右段均高 %.1f px" % shape["right"])

    print("\n[D] 暂停停 rAF")
    if wav:
        m.eval("window.__TAURI__.event.emit('tt:cmd',{t:'toggle'})")
        time.sleep(1.2)
        check("暂停后 rAF 停掉", m.eval("window.__osc().raf") is False,
              str(m.eval("window.__osc()")))
        check("屏上仍留静止画面", spec_px() > 0, "%d px" % spec_px())

    print("\n[E] 形态进 session")
    m.eval("document.getElementById('screen').click()")
    time.sleep(1.2)
    mode = m.eval("state.osc")
    raw = ""
    try:
        raw = open(os.path.join(CONF, "session.json"), encoding="utf-8").read()
    except OSError:
        pass
    check("session 里记了当前形态", ('"osc": "%s"' % mode) in raw.replace(" ", " ") or
          ('"osc":"%s"' % mode) in raw, "mode=%s" % mode)

    print("\n[F] 皮肤")
    check("默认是紫皮 classic", m.eval(
        "document.getElementById('app').dataset.skin") == "classic",
        m.eval("document.getElementById('app').dataset.skin"))
    m.eval("applySkin('retro')")
    time.sleep(0.4)
    check("能切回钢蓝", m.eval("document.getElementById('app').dataset.skin") == "retro")
    m.eval("applySkin('modern')")
    time.sleep(0.4)
    check("能切到现代深色", m.eval("document.getElementById('app').dataset.skin") == "modern")
    m.eval("applySkin('classic')")
    time.sleep(0.4)

    print("\n[G] 均衡器频率标签对齐")
    ps = cdp.pages()
    if "eq" in ps:
        eq = ps["eq"]
        off = eq.eval("""(()=>{
          const f=[...document.querySelectorAll('.eq-freqs span')].slice(1);
          const s=[...document.querySelectorAll('.eq-col')];
          return f.map((e,i)=>{
            const a=e.getBoundingClientRect(), b=s[i]&&s[i].getBoundingClientRect();
            return b ? +(a.left+a.width/2-(b.left+b.width/2)).toFixed(1) : null;
          });
        })()""")
        check("拿到 10 个标签位置", isinstance(off, list) and len(off) == 10, str(off))
        if isinstance(off, list) and len(off) == 10:
            worst = max(abs(v) for v in off if v is not None)
            check("每个标签都对准滑块中心（≤1px）", worst <= 1.0, "最大偏差 %.1f px" % worst)
    else:
        check("均衡器窗口在", False, "没找到 eq")

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    kill()
    return FAIL


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
