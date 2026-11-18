"""E2E：目录扫描 → 加入播放列表 → 播放 → 切歌

依赖调试端口：
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=9222" ./ttplayer.exe
"""
import json
import os
import sys
import time

import cdp

ROOT = r"E:\MP3"

ps = cdp.pages()
main = ps["main"]
pl = ps.get("playlist")

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


# ---------- 1. 目录递归扫描 ----------
print("1. 扫描 E:\\MP3")
raw = main.eval("window.__TAURI__.core.invoke('scan_audio_dir', {path: %s})"
                % json.dumps(ROOT, ensure_ascii=False))
paths = raw if isinstance(raw, list) else []
print(f"   命中 {len(paths)} 个音频文件")
check("扫描到足够多文件", len(paths) > 100, f"{len(paths)} 个")
subdir = [p for p in paths if p[len(ROOT):].replace("/", os.sep).count(os.sep) >= 2]
check("递归进子目录", len(subdir) > 0, f"{len(subdir)} 个来自子目录")
check("只含 mp3/flac/wav",
      all(p.lower().endswith((".mp3", ".flac", ".wav")) for p in paths))
check("排除 .downloading 临时文件",
      not any("downloading" in p.lower() for p in paths))
check("已排序", paths == sorted(paths, key=str.lower))

# ---------- 2. 加入播放列表 ----------
print("\n2. 加入前 3 首到播放列表")
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'clear'})")
time.sleep(0.4)
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'addPaths', paths: %s})"
          % json.dumps(paths[:3], ensure_ascii=False))
time.sleep(1.2)
cnt = pl.eval("document.querySelectorAll('#playlist li').length") if pl else -1
check("播放列表窗口显示 3 条", cnt == 3, f"{cnt} 条")

dup = pl.eval("""(async () => {
  const before = document.querySelectorAll('#playlist li').length;
  window.__TAURI__.event.emit('tt:cmd', {t:'addPaths', paths: %s});
  await new Promise(r => setTimeout(r, 600));
  return before + ':' + document.querySelectorAll('#playlist li').length;
})()""" % json.dumps(paths[:3], ensure_ascii=False))
check("重复添加不产生重复项", dup == "3:3", f"加前/加后 = {dup}")

# ---------- 3. 播放 ----------
print("\n3. 播放第 1 首")
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'playAt', index:0})")
time.sleep(2.6)
info = json.loads(main.eval(
    "JSON.stringify({t: document.getElementById('time-cur').textContent,"
    " dur: document.getElementById('time-total').textContent,"
    " title: document.getElementById('info-title').textContent})"))
check("标题显示曲名", bool(info["title"]) and info["title"] != "宁宁静听", f"title={info['title']}")
check("时长已解析", info["dur"] != "00:00", f"duration={info['dur']}")
check("进度在走", info["t"] != "00:00", f"current={info['t']}")

# ---------- 4. 切歌 ----------
print("\n4. 切到下一首")
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'next'})")
time.sleep(2.2)
info2 = json.loads(main.eval(
    "JSON.stringify({t: document.getElementById('time-cur').textContent,"
    " dur: document.getElementById('time-total').textContent,"
    " title: document.getElementById('info-title').textContent})"))
check("标题已变化", info2["title"] != info["title"], f"{info['title']} → {info2['title']}")
check("新曲在播放", info2["t"] != "00:00", f"current={info2['t']} duration={info2['dur']}")

# ---------- 5. 拖动进度 ----------
print("\n5. 拖动进度到 50%")
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'seek', ratio:0.5})")
time.sleep(0.9)
cur = main.eval("document.getElementById('time-cur').textContent")
check("进度跳转生效", cur > "01:00", f"current={cur}")

print("\n汇总: %d/%d 通过" % (sum(1 for _, ok in results if ok), len(results)))
for p in ps.values():
    p.close()
sys.exit(0 if all(ok for _, ok in results) else 1)
