"""诊断无声问题：AudioContext 状态 / 采样率 / 频谱是否有能量"""
import time

import cdp

ps = cdp.pages()
main = ps["main"]

DIAG = """
(() => {
  return JSON.stringify({
    ctxExists: !!audioCtx,
    ctxState: audioCtx ? audioCtx.state : null,
    ctxRate: audioCtx ? audioCtx.sampleRate : null,
    ctxTime: audioCtx ? audioCtx.currentTime : null,
    ctxLatency: audioCtx ? audioCtx.baseLatency : null,
    eqLen: eqFilters ? eqFilters.length : -1,
    analyser: !!analyser,
    vol: audio ? audio.volume : null,
    muted: audio ? audio.muted : null,
    paused: audio ? audio.paused : null,
    dur: audio ? audio.duration : null,
    t: audio ? audio.currentTime : null,
    ready: audio ? audio.readyState : null,
    net: audio ? audio.networkState : null,
    err: audio && audio.error ? audio.error.code : null,
    sink: audio && 'sinkId' in audio ? audio.sinkId : 'n/a',
    stateVol: state.volume, stateMuted: state.muted, playing: state.playing
  });
})()
"""

METER = """
(() => {
  if (!analyser) return 'no-analyser';
  const d = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(d);
  let m = 0; for (const v of d) if (v > m) m = v;
  const td = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(td);
  let peak = 0; for (const v of td) peak = Math.max(peak, Math.abs(v - 128));
  return JSON.stringify({maxFreq: m, peak, bins: Array.from(d).slice(0, 6)});
})()
"""

print("== 播放前 ==")
print(main.eval(DIAG))

# 加入一首并播放
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'addPaths', paths:[%r]})" % r"E:\MP3\02 约定.mp3")
time.sleep(0.6)
main.eval("window.__TAURI__.event.emit('tt:cmd', {t:'playAt', index:0})")
time.sleep(2.5)
print("\n== 播放 2.5s 后 ==")
print(main.eval(DIAG))
print("meter:", main.eval(METER))
time.sleep(1.0)
print("meter:", main.eval(METER))

for p in ps.values():
    p.close()
