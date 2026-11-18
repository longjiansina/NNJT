# 架构说明

## 一、窗口模型

程序是**五个独立窗口**，不是一页五块。

| label | 名称 | 默认尺寸 | 说明 |
|---|---|---|---|
| `main` | 主体 | 390 × 160 | 唯一持有音频引擎与播放状态的窗口 |
| `playlist` | 播放列表 | 宽随主体 | 不占任务栏 |
| `lyric` | 歌词秀 | 宽随主体 | 不占任务栏 |
| `eq` | 均衡器 | 宽随主体 | 不占任务栏 |
| `about` | 关于 | 364 × 282 | 临时弹窗，居中于主体，不进布局表 |

`main` 由 `tauri.conf.json` 声明；其余四个用 `WebviewWindowBuilder` 在运行时创建，全部无边框、透明。关闭 `main` 即退出整个程序。

每个窗口加载**同一个** `index.html`，靠 `body[data-w="<label>"]` 决定显示哪个 `section.pane`：

```css
body[data-w="about"] .pane[data-pane="about"] { display: flex; }
```

因此 `app.js` 开头就是按 label 分叉：

```js
const IS_MAIN = LABEL === 'main';
const LYRIC   = LABEL === 'lyric';
const ABOUT   = LABEL === 'about';
```

新增一个窗口要动三处：`capabilities/default.json` 的 `windows` 白名单、`styles.css` 的 pane 显示规则、`app.js` 里一个 label 常量分支。

> **运行时建窗口的命令必须 `async fn`**。同步 `#[tauri::command]` 跑在主线程上，而 `build()` 又要等主线程派发创建，两边互等就死锁 —— 表现为前端 `invoke` 永远不 resolve、窗口不出现、控制台也不报错。`open_about` 就是修这个坑改成的 `async`。

## 二、状态归属与事件总线

**播放状态只有 `main` 持有。** `state` 对象、`AudioContext`、播放列表数据全在 `main` 的 JS 里。其他窗口是「显示器 + 遥控器」。

这一点决定了改状态的方式：

```
面板窗口改状态  ──tt:cmd──▶  main 执行 ──tt:state──▶  所有窗口重绘
```

**面板窗口自己 `broadcast()` 是无效的** —— `main` 根本不监听 `tt:state`。所以模式切换、改名、音量这类操作必须走 `cmd('xxx')`。v0.14 的模式按钮就踩过这个坑：按钮文字变了、歌照旧循环，因为改的是面板本地的一份 `state`。

事件一览：

| 事件 | 方向 | 用途 |
|---|---|---|
| `tt:state` | main → 全部 | 完整状态快照，各窗重绘 |
| `tt:cmd` | 面板 → main | 遥控指令（`{t: 'setMode', ...}`） |
| `tt:sync` | 任意 → 全部 | 轻量通知，仅触发重绘 |
| `tt:meter` | main → 全部 | 频谱柱数据（每帧） |
| `tt:osc` | Rust → 前端 | 示波形态开关 |
| `tt:scan` | main → 全部 | 扫描进度 `{phase, done, total}` |
| `tt:dock` | Rust → 前端 | 贴边收起 / 弹出状态 |
| `tt:dragend` | Rust → 前端 | 拖动结束，触发磁吸判定 |
| `tt:resizeend` | Rust → 前端 | 缩放结束，触发列重排 |

## 三、窗口跟随与贴合

### 跟随拖动

拖动前（标题栏 `mousedown`）前端调 `drag_begin`，Rust 快照**所有窗口相对被拖窗口的偏移**；之后每次 `WindowEvent::Moved` 都按这份偏移同步其余窗口。只有拖动 `main` 才带动别人，拖面板时其他窗口不动。

拖动结束的判定在 Rust 侧：位移静默且鼠标左键已松开 → `emit("tt:dragend")`，前端收到后做磁吸。

### 磁吸成列

`trySnap()` 找到与主体水平对齐、竖直相邻的面板，把它们贴成严丝合缝的一列。列内窗口按 `PANEL_ORDER` 固定顺序排列（播放列表 → 歌词 → 均衡器），`compact_panels` 负责补位与下推。

被用户拖到列外的面板**有自己的一条命**：它不参与列重排，也不占列里的位置 —— 否则会在列中留下空洞。判断依据是「水平方向是否仍与主体对齐」。

### 高度联动

窗口高度上限是动态算的：在列里的窗口要扣掉同列其他窗口占的高度，否则拉长一个就会把整列顶出屏幕；独立摆放的窗口上限就是整个工作区高度。整列超出工作区底部时整体上移，避免被任务栏盖住。

### 贴边收起

拖到屏幕边缘松手后，`dock_arm` 记录整组的归位坐标、外接矩形、贴的哪条边，并起一个后台线程轮询鼠标位置：

- 鼠标离开贴合组 → 整组位移到屏幕外，只留一条 3px 的边的可见条带
- 鼠标进入条带感应区（额外放宽若干像素）→ 整组弹回原位

收起期间窗口的「假位置」不能记进布局表，否则下次启动会还原到屏幕外。

## 四、布局持久化

`window-layout.txt`，一行一个窗口，空格分隔 7 列：

```
label x y w h visible hasStrip
```

- 高频事件（拖动 / 缩放）走 1.2 秒节流的 `flush_layout_throttled`，不让写盘拖慢交互
- 退出时整表落盘，下次启动照原样还原位置、尺寸与面板开合
- **`about` 不在表里** —— `remember_win` 里对 `label == "about"` 直接返回。它是居中的临时弹窗，写进表就会多一行，下次启动还得处理这个不该还原的窗口
- 老版本文件可能只有 6 列（无可见性）或 7 列但含义不同（`hasStrip` 曾表示「高度里含 26px 示波带」），读取时有兼容分支

## 五、界面与皮肤

样式全部由 CSS 变量驱动，变量定义挂在 **`#app`** 上：

```css
#app[data-skin="classic"] { --shell-bg: ...; --text: ...; }
```

**动态生成的弹层（右键菜单、下拉菜单等）必须 `append` 到 `appEl`**，不能挂 `document.body`。挂 body 上 `var(--shell-*)` 全部解析为空，弹层会变成一块只有字的透明块 —— 这是 v0.14 右键菜单踩过的坑。

三套皮肤：`classic`（经典紫罗兰）、`retro`（经典钢蓝）、`modern`（现代深色）。新增一套只需在 `SKINS` 数组加一项，并在 `styles.css` 补一组变量。

皮肤值存 localStorage（`ttplayer-skin`），session.json 里存不住 —— 因为它是同步写盘的，而 localStorage 由 Chromium 异步落盘。用 `taskkill /F` 硬杀进程可能丢掉最后的写入。

## 六、音频与元数据

- **播放**：`<audio>` + Web Audio API。`MediaElementSource` → 十段 `BiquadFilter`（peaking，中心频率 31Hz – 16kHz）→ `AnalyserNode` → 输出
- **频谱**：`AnalyserNode.getByteFrequencyData` 每帧取值，`main` 通过 `tt:meter` 广播给其他窗口
- **元数据**：Rust 侧 symphonia 读标签（标题 / 艺术家 / 时长）。Cargo 依赖里 **features 必须包含 `id3v1, id3v2, ape`** —— 漏掉 MP3 就全部没有标签，只显示文件名
- **扫描**：`scan_audio_dir_progress` 用 `spawn_blocking` 递归走目录，每 70ms `emit("tt:scan")` 报一次累计数量；前端在状态行画进度条。选完目录立刻能报进度，而不是干等几十秒

长耗时操作统一约定：起手 `scanStart()`，收尾 `scanDone()`（= `scanEnd()` + `emitScan({phase:'done'})`）。只关本窗的进度会让另一个窗口永远停在原地。

## 七、单实例

`tauri-plugin-single-instance` **必须第一个注册**，第二份进程在窗口创建之前就被拦下。拦下后不退出得悄无声息，而是让已在运行的实例把主体和所有「用户没关掉的面板」提回前台。

提 Z 序不能靠 `is_visible()` 判断 —— 面板可能只是被别的程序盖住，`is_visible()` 仍是 true，`unminimize` / `show` 都当无事发生，只能显式把窗口句柄提到最前。
