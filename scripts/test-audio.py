"""诊断本地音频播放：记录 Audio 元素事件序列与错误

用法：python test-audio.py ["E:\\MP3\\02 约定.mp3"]
"""
import json
import sys

import cdp

path = sys.argv[1] if len(sys.argv) > 1 else r"E:\MP3\02 约定.mp3"

ps = cdp.pages()
main = ps["main"]

expr = """
(async () => {
  const p = %s;
  const url = window.__TAURI__.core.convertFileSrc(p);
  const a = new Audio();
  a.preload = 'auto';
  const evs = [];
  ['loadstart','progress','loadedmetadata','loadeddata','canplay','canplaythrough',
   'stalled','suspend','error','abort','emptied'].forEach((n) => {
    a.addEventListener(n, () => evs.push(n + (n === 'error'
      ? ':' + (a.error ? a.error.code + '/' + a.error.message : '?') : '')));
  });
  a.src = url;
  a.load();
  let played = null;
  try {
    await a.play();
    played = 'started';
  } catch (e) { played = 'play() 拒绝: ' + String(e); }
  await new Promise((r) => setTimeout(r, 3500));
  return JSON.stringify({url, played, evs, dur: a.duration, ready: a.readyState,
                         paused: a.paused, t: a.currentTime, net: a.networkState});
})()
""" % json.dumps(path, ensure_ascii=False)

print(main.eval(expr))
for p in ps.values():
    p.close()
