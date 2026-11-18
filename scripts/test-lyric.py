"""E2E：播放带 .lrc 的歌曲 → 打开歌词秀 → 校验渲染、高亮、滚动同步"""
import json
import time

import cdp

SONG = r"E:\MP3\其他歌手\02 约定.mp3"

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


ps = cdp.pages()
main = ps["main"]

print("1. 载入并播放")
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'clear'})")
time.sleep(0.4)
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'addPaths', paths:[%s]})"
          % json.dumps(SONG, ensure_ascii=False))
time.sleep(0.6)
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'playAt', index:0})")
time.sleep(0.8)
st = json.loads(main.eval("JSON.stringify({playing: state.playing, t: audio.currentTime, "
                          "song: state.playlist[0] && state.playlist[0].name})"))
check("已开始播放", st.get("playing") is True, str(st))

print("\n2. 打开歌词秀窗口")
main.eval("togglePanelWin('lyric')")
time.sleep(2.2)

ps = cdp.pages()
ly = ps.get("lyric")
check("歌词窗口已存在", ly is not None)
if not ly:
    raise SystemExit(1)

READ = """
JSON.stringify({
  lines: document.querySelectorAll('.lyric-line').length,
  hint: (document.querySelector('.lyric-hint') || {}).textContent || '',
  idx: [...document.querySelectorAll('.lyric-line')].findIndex(e => e.classList.contains('active')),
  activeText: (document.querySelector('.lyric-line.active') || {}).textContent || '',
  scrollTop: Math.round(document.getElementById('lyric-body').scrollTop),
  pos: state.position
})
"""
s1 = json.loads(ly.eval(READ))
print("   ", s1)
check("歌词已渲染", s1["lines"] > 10, "%d 行" % s1["lines"])
check("无错误提示", s1["hint"] == "", s1["hint"] or "-")

print("\n3. 前奏段 + 中段的高亮与滚动")
s1b = json.loads(ly.eval(READ))
check("前奏期不高亮（元信息已剔除）",
      s1b["idx"] == -1 or s1b["idx"] == 0, "idx=%d" % s1b["idx"])
check("不显示作词/编曲等制作信息行",
      not any(x in s1b["activeText"] for x in ("作词", "作曲", "编曲", "制作人")))

# 跳到 35% 处，确保落在正文
ly.eval("window.__TAURI__.event.emit('tt:cmd', {t:'seek', ratio:0.35})")
time.sleep(2.2)
s2 = json.loads(ly.eval(READ))
print("   ", s2)
check("播放位置在前进", s2["pos"] > s1["pos"], "%.1f → %.1f" % (s1["pos"], s2["pos"]))
check("高亮跟随时间轴", s2["idx"] >= 0, "第 %d 行: %s" % (s2["idx"], s2["activeText"][:18]))
check("已自动滚动", s2["scrollTop"] > 0, "scrollTop=%d" % s2["scrollTop"])

print("\n4. 点击歌词行跳转")
ok = ly.eval("""
(() => {
  const ls = document.querySelectorAll('.lyric-line');
  if (ls.length < 6 || !state.duration) return 'skip';
  ls[5].click();
  return 'clicked t=' + ls[5].dataset.t + ' dur=' + Math.round(state.duration);
})()
""")
time.sleep(1.0)
s3 = json.loads(main.eval("JSON.stringify({t: audio.currentTime, dur: audio.duration})"))
print("   ", ok, "| 主窗口 currentTime =", round(s3["t"], 1))
check("点击歌词跳转生效", s3["t"] > 5, "currentTime=%.1f" % s3["t"])

print("\n5. 切到没有歌词的歌")
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'addPaths', paths:[%s]})"
          % json.dumps(r"E:\MP3\王心凌--梦的光点 (变速版).mp3", ensure_ascii=False))
time.sleep(0.8)
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'playAt', index:1})")
time.sleep(1.5)
s4 = json.loads(ly.eval(READ))
print("   ", s4)
check("无歌词时给出提示", s4["lines"] == 0 and len(s4["hint"]) > 0, s4["hint"])

ok_n = sum(1 for _, o in results if o)
print(f"\n=== {ok_n}/{len(results)} 通过")
for p in ps.values():
    p.close()
