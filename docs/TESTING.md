# 测试说明

## 原理

所有端到端测试都通过 **WebView2 远程调试协议（CDP）** 直接操作页面，不猜坐标、不靠截图比对。

启动播放器时带上调试端口：

```bash
export WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=9222"
./ttplayer.exe
```

`scripts/cdp.py` 封装了这层：

| 接口 | 用途 |
|---|---|
| `cdp.pages()` | 列出当前所有页面（main / playlist / lyric / eq / about） |
| `cdp.Page(ws_url)` | 建立连接，`.eval(js)` 在页面里执行表达式并取回值 |
| `cdp.list_targets()` | 原始 `/json` 响应 |
| `cdp.audio_files(root, limit, exts)` | **递归**列音频文件，测试取样本统一用它 |

`audio_files` 必须递归 —— 音频样本目录已按歌手分成子文件夹，用 `os.listdir` 一个文件都拿不到。

脚本里清掉了 `http_proxy` 等环境变量，否则 127.0.0.1 的调试请求会走系统代理然后超时。

右键菜单这类交互用 `Input.dispatchMouseEvent` 发**真右键**，比直接调 `contextmenu` 事件更接近用户行为（能验证坐标定位、遮罩层级这些真问题）。

## 两类脚本

### 自己启动播放器的

`test-v08` `test-v09` `test-v10` `test-v12` `test-v13` `test-v14` `test-session` `test-layout` `test-layout2`

跑之前先 `taskkill /F /IM ttplayer.exe`，起一个干净进程，跑完收掉。

```bash
python scripts/test-v14.py
```

### 只附加、不启动的

`test-open` `test-play` `test-lyric` `test-all` `test-stack` `test-resize`

这类需要「程序已经在跑且状态正确」才能测，所以用引导脚本一次跑完：

```bash
python scripts/_regress.py                 # 默认跑 open play lyric all stack resize
python scripts/_regress.py play stack      # 只跑指定的
```

`_regress.py` 负责：杀旧进程 → 带调试端口启动 → 等 `main` 出现 → 依次拉起各脚本 → 收进程。

## 破坏性测试

**这批测试会改动真实数据。** 已知破坏行为：

| 脚本 | 破坏 |
|---|---|
| `test-session` | 删除列表中的歌曲 |
| `test-v09` | 清空整个歌单 |

跑回归前先备份，跑完写回：

```bash
cp "$APPDATA/com.ttplayer.player/session.json" .userstate/pre-regress-session.json
# …… 跑测试 ……
cp .userstate/pre-regress-session.json "$APPDATA/com.ttplayer.player/session.json"
```

`.userstate/` 里放的就是历次回归的现场备份（`user-571.json` 是 571 首的完整歌单快照）。**不要把 `.userstate` 提交进仓库**，它是个人听歌数据。

## 几个必守的前置条件

- **尺寸 / 贴合类断言前必须先删 `window-layout.txt`**。布局表会记住上次的位置尺寸，断言「默认 390×160」就会失败 —— 不是程序错了，是测试没清场。
- **测试会改皮肤**。跑完用 `applySkin('classic')` 复位，并走正常退出（`save_session` + 关窗）让 localStorage 落盘。用 `taskkill /F` 硬杀可能丢皮肤设置。
- **起始状态要按当前实际情况推算期望值**。播放器不是每次都从「空歌单」开始，脚本里要读当前状态而不是写死「应该有 0 首」。

## 现有覆盖

15 个脚本、369 项断言，全部通过：

| 脚本 | 项数 | 覆盖 |
|---|---|---|
| `test-v14` | 60 | 模式下拉、单曲循环真重播、列表右键改名、扫描进度 |
| `test-v13` | 46 | 关于窗口（居中、单例、三路关闭）、入口按钮 |
| `test-v12` | 45 | 界面改版：频谱屏、示波形态、背景模式 |
| `test-layout2` | 19 | 布局持久化细节 |
| `test-session` | 19 | 歌单存取、`.bak` 备份 |
| `test-layout` | 11 | 布局还原（位置 / 尺寸 / 可见性） |
| `test-v10` | 18 | 状态行、进度显示 |
| `test-v09` | 34 | 播放列表增删、清空、切换 |
| `test-v08` | 41 | 均衡器、皮肤、窗口按钮 |
| `test-play` | 13 | 播放 / 暂停 / 切歌 / 进度拖动 |
| `test-all` | 13 | 全流程串联 |
| `test-stack` | 12 | 面板贴合与堆叠顺序 |
| `test-resize` | 20 | 各窗口缩放手柄、高度上限联动 |
| `test-lyric` | 11 | LRC 解析、逐行高亮、歌词窗 |
| `test-open` | 7 | 添加文件 / 添加文件夹 |

`test-audio` 与 `test-scale` 是早期单点验证脚本，未纳入上面这条回归链。

诊断脚本（非断言，排查用）：`diag-cors.py` `diag-sound.py` `diag-windows.py` `lyric-probe.py` `fetch-lyrics.py`。

截图脚本：`shot.py`（按窗口抓图）、`_shot_about.py`（关于窗专项）。

## 页面上可断言的钩子

`app.js` 里留了几个供测试用的出口：

```js
window.__err = [];   // 未捕获异常与 promise rejection 都往这里 push
```

断言「跑完没有报错」就查 `window.__err.length === 0`。状态快照可以直接读 DOM 上的 `data-*` 属性，不必反推内部对象。
