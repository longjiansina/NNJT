"""验证假设：crossOrigin + MediaElementSource 是否还能输出真实音频数据"""
import json

import cdp

ps = cdp.pages()
main = ps["main"]

EXPR = """
(async () => {
  const p = %s;
  const url = window.__TAURI__.core.convertFileSrc(p);
  const ctx = new AudioContext();
  const a = new Audio();
  a.crossOrigin = 'anonymous';
  a.src = url;
  a.volume = 0.3;
  const src = ctx.createMediaElementSource(a);
  const an = ctx.createAnalyser();
  an.fftSize = 128;
  src.connect(an);
  an.connect(ctx.destination);
  await ctx.resume();
  await a.play();
  await new Promise((r) => setTimeout(r, 1500));
  const d = new Uint8Array(an.frequencyBinCount);
  an.getByteFrequencyData(d);
  let mx = 0; for (const v of d) if (v > mx) mx = v;
  const t = Math.floor(a.currentTime * 10) / 10;
  // 停掉，别打扰用户
  a.pause();
  await ctx.close();
  return JSON.stringify({ctxState: 'running', ctxRate: ctx.sampleRate,
                         playedSec: t, maxFreqBin: mx, audible: mx > 0});
})()
""" % json.dumps(r"E:\MP3\02 约定.mp3", ensure_ascii=False)

print(main.eval(EXPR))

# 对照：不带 crossOrigin 的元素（即旧代码的写法）
EXPR2 = """
(async () => {
  const p = %s;
  const url = window.__TAURI__.core.convertFileSrc(p);
  const ctx = new AudioContext();
  const a = new Audio();
  a.src = url;
  a.volume = 0.3;
  const src = ctx.createMediaElementSource(a);
  const an = ctx.createAnalyser();
  an.fftSize = 128;
  src.connect(an);
  an.connect(ctx.destination);
  await ctx.resume();
  await a.play();
  await new Promise((r) => setTimeout(r, 1500));
  const d = new Uint8Array(an.frequencyBinCount);
  an.getByteFrequencyData(d);
  let mx = 0; for (const v of d) if (v > mx) mx = v;
  a.pause();
  await ctx.close();
  return JSON.stringify({noCrossOrigin_maxFreqBin: mx, audible: mx > 0});
})()
""" % json.dumps(r"E:\MP3\02 约定.mp3", ensure_ascii=False)

print(main.eval(EXPR2))
for p in ps.values():
    p.close()
