"""一次引导，跑完所有「只附加不启动」的测试（play/open/lyric/all/stack/resize）"""
import os, subprocess, sys, time
import cdp

EXE = r"D:/AI/BFQ/ttplayer/src-tauri/target/release/ttplayer.exe"
ROOT = r"D:/AI/BFQ/ttplayer"
TODO = sys.argv[1:] or ["open", "play", "lyric", "all", "stack", "resize"]

subprocess.run(["taskkill", "/F", "/IM", "ttplayer.exe"],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.8)
e = dict(os.environ)
e["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=9222"
subprocess.Popen([EXE], env=e, cwd=ROOT)
ok = False
for _ in range(45):
    time.sleep(0.4)
    try:
        if "main" in cdp.pages():
            ok = True
            break
    except Exception:
        pass
print("booted:", ok)
time.sleep(3.0)

for t in TODO:
    print("=== %s ===" % t, flush=True)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "test-%s.py" % t)],
                       capture_output=True, text=True, cwd=ROOT, encoding="utf-8",
                       errors="ignore", timeout=420)
    lines = [l for l in (r.stdout or "").splitlines() if ("PASS" in l and "FAIL" in l) or "FAIL" in l or "结果" in l or "通过" in l or "合计" in l]
    print("\n".join(lines[-8:]) if lines else (r.stdout or "")[-400:] + (r.stderr or "")[-300:])
subprocess.run(["taskkill", "/F", "/IM", "ttplayer.exe"],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
