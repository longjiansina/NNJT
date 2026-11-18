# 构建与打包

## 前置环境

| 工具 | 版本 | 说明 |
|---|---|---|
| Node.js | 18+ | 只用来装 Tauri CLI，前端没有构建步骤 |
| Rust | stable + MSVC 工具链 | `rustup default stable-x86_64-pc-windows-msvc` |
| Visual Studio Build Tools | 2019+ | 需要「使用 C++ 的桌面开发」组件（链接器） |

`npm install` 之后即可开发。

## 开发

```bash
npm run dev      # = tauri dev
```

`tauri.conf.json` 里 `devUrl` 是 `http://localhost:1420`。**注意本项目的 `beforeDevCommand` 是空的** —— 没有 Vite 之类的开发服务器，前端就是 `src/` 下的静态文件。如果 `tauri dev` 起不来或白屏，检查是否有别的东西占着 1420 端口。

前端改动（`src/*.js` / `*.css` / `*.html`）在 `tauri dev` 下刷新窗口即可看到。

## 构建可执行文件

```bash
npm run build:exe
```

等价于：

```bash
cargo build --release --manifest-path src-tauri/Cargo.toml --features custom-protocol
```

产物：`src-tauri/target/release/ttplayer.exe`（约 4.4 MB）

### 为什么必须是这个命令

Tauri 内部用 `cfg!(feature = "custom-protocol")` 决定启动时加载**内嵌资源**还是**去连 `devUrl`**：

- 带 `--features custom-protocol` → 用 `tauri://localhost` 协议加载编译期嵌进去的资源，这是发布版
- 不带 → 当开发版处理，去连 `http://localhost:1420`，结果是**白屏**（控制台报连不上）

所以 `cargo build --release` 直接跑出来的不是发布版，别拿它发人。

### 另一个容易漏的点

`src/` 下的 HTML / CSS / JS 是**编译期嵌进二进制**的（`frontendDist: "../src"`）。改完界面必须重新 `npm run build:exe`，光替换 exe 旁边的文件没用 —— 外面根本没有这些文件。

## 打安装包

```bash
npm run build      # = tauri build
```

会额外产出 NSIS 安装包：

```
src-tauri/target/release/bundle/nsis/宁宁静听_<version>_x64-setup.exe
```

包含开始菜单快捷方式与卸载项。版本号取自 `tauri.conf.json` 的 `version`。

> 安装包里的版本号只在构建时读取，改完 `tauri.conf.json` 必须重新构建，不能改名糊弄 —— 否则会出现「安装包写着 0.4.0、装出来是 0.14.0」的情况。

## 分发

发布版是**单文件绿色版**，直接把 `ttplayer.exe` 发给对方即可，不需要附带 DLL 或资源目录：

- 静态链接了 Rust 标准库与 CRT（`api-ms-win-crt-*` 走系统 UCRT，Win10 起自带）
- 依赖的系统库只有 `kernel32` / `user32` / `comctl32` / `dwmapi` / `shcore` / `ole32` 等
- 唯一外部前提是 **WebView2 Runtime**：Win11 与较新 Win10 内置，个别旧机器需装一次

发布前可自查：

```bash
grep -aoiE "[A-Za-z0-9_.-]+\.dll" src-tauri/target/release/ttplayer.exe | sort -u
```

输出里不该出现任何项目自带的 DLL（如 `ttplayer_lib.dll` 只是 `crate-type` 的副产物，exe 并不依赖它）。

### 未签名的代价

exe 没有代码签名证书，首次运行时 Windows SmartScreen 会拦一次，需要「更多信息 → 仍要运行」。这是预期行为，不是文件损坏。

## 体积优化

`Cargo.toml` 的 release profile 已经开了：

```toml
[profile.release]
panic = "abort"
codegen-units = 1
lto = true
opt-level = "s"
strip = true
```

再往下压就要砍依赖了（比如 symphonia 的音频格式特性只保留实际用到的）。

## 常见问题

| 现象 | 原因 |
|---|---|
| 双击白屏、控制台连不上 1420 | 用了 `cargo build` 而不是 `build:exe`，缺 `custom-protocol` |
| 改了样式但界面没变 | 资源编译期嵌入，没重新构建 |
| 窗口不出现、invoke 永不 resolve | 运行时建窗口的命令写成了同步 `fn`，主线程死锁，改 `async fn` |
| MP3 全部没有标题 / 歌手 | symphonia 的 features 漏了 `id3v1, id3v2, ape` |
| 面板全部挤在屏幕外 | 布局表被写脏，删掉 `%APPDATA%\com.ttplayer.player\window-layout.txt` 重启 |
