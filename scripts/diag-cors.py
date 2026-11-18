"""检查 asset 协议的 CORS 头，判断 Web Audio 是否被跨域污染"""
import json
import time

import cdp

ps = cdp.pages()
main = ps["main"]

EXPR = """
(async () => {
  const p = %s;
  const url = window.__TAURI__.core.convertFileSrc(p);
  const out = {origin: location.origin, href: location.href, url};
  try {
    const r = await fetch(url, {headers: {Range: 'bytes=0-1'}});
    out.status = r.status;
    out.acao = r.headers.get('access-control-allow-origin');
    out.acac = r.headers.get('access-control-allow-credentials');
    out.ctype = r.headers.get('content-type');
    out.all = [...r.headers.entries()].map(([k, v]) => k + ': ' + v);
  } catch (e) { out.fetchError = String(e); }

  // 用 CORS 模式重新加载一个 Audio，看是否被污染导致 MediaElementSource 静音
  try {
    const a2 = new Audio();
    a2.crossOrigin = 'anonymous';
    a2.src = url;
    a2.volume = 0;             // 静音，只为验证加载是否成功
    await new Promise((res, rej) => {
      a2.addEventListener('loadedmetadata', res, {once: true});
      a2.addEventListener('error', () => rej(new Error('media error ' + (a2.error && a2.error.code))), {once: true});
      setTimeout(() => rej(new Error('timeout')), 5000);
    });
    out.corsLoad = 'ok, dur=' + a2.duration;
  } catch (e) { out.corsLoad = 'fail: ' + String(e); }
  return JSON.stringify(out, null, 1);
})()
""" % json.dumps(r"E:\MP3\02 约定.mp3", ensure_ascii=False)

print(main.eval(EXPR))
for p in ps.values():
    p.close()
