"""通过 WebView2 远程调试协议操作播放器页面（比猜坐标可靠）

启动方式（必须带调试端口）：
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=9222" ./ttplayer.exe
"""
import json
import os
import urllib.request

import websocket

PORT = 9222

# 本机调试端口不能走系统代理（WorkBuddy 环境里 http_proxy 指向 127.0.0.1:58738）
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def audio_files(root, limit=None, exts=None):
    """递归列出音频文件（按完整路径排序）。

    MP3 目录已被整理成「按歌手分子文件夹」，非递归的 os.listdir 会一个文件都拿不到，
    所以所有测试统一用这个函数取样本。
    """
    if exts is None:
        exts = (".mp3", ".flac", ".wav", ".ogg", ".oga", ".opus", ".m4a", ".aac")
    out = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if n.lower().endswith(tuple(exts)):
                out.append(os.path.join(dirpath, n))
    out.sort()
    return out[:limit] if limit else out


def list_targets():
    with _OPENER.open(f"http://127.0.0.1:{PORT}/json", timeout=5) as r:
        return json.load(r)


class Page:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=10)
        self.i = 0

    def call(self, method, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                return msg

    def eval(self, expr):
        r = self.call("Runtime.evaluate", expression=expr,
                      returnByValue=True, awaitPromise=True)
        res = r.get("result", {})
        if "exceptionDetails" in res:
            return {"__error": res["exceptionDetails"].get("text")}
        return res.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def pages():
    """返回 {label: Page}"""
    out = {}
    for t in list_targets():
        if t.get("type") != "page":
            continue
        if not t.get("webSocketDebuggerUrl"):
            continue
        p = Page(t["webSocketDebuggerUrl"])
        label = p.eval("document.body ? document.body.dataset.w : null")
        if label:
            out[label] = p
        else:
            p.close()
    return out


if __name__ == "__main__":
    ps = pages()
    print("已连接窗口:", list(ps.keys()))
    for label, p in ps.items():
        rect = p.eval("JSON.stringify({w:innerWidth,h:innerHeight})")
        print(f"  {label}: {rect}")
    for p in ps.values():
        p.close()
