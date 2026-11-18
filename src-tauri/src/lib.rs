use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{
    Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindowBuilder,
};

mod meta;

const MAIN_W: i32 = 390;
const MAIN_H: i32 = 160;

/// 收起后仍留在屏幕内的厚度（像素）
const DOCK_KEEP: i32 = 5;
/// 弹出感应区在可见条带外额外放宽的像素
const DOCK_REVEAL: i32 = 12;
/// 鼠标离开贴合组后，多久才收起
const DOCK_LINGER: u64 = 420;
/// 判定两个窗口「贴合」的最大间隙
const GROUP_GAP: i32 = 12;

/* ===========================================================
   拖动跟随
   =========================================================== */

/// 拖动会话：记录谁在拖，以及其余窗口相对它的偏移
#[derive(Default)]
struct DragState {
    active: bool,
    leader: Option<String>,
    offsets: HashMap<String, (i32, i32)>,
    last_move: Option<Instant>,
}

type SharedDrag = Arc<Mutex<DragState>>;

/* ===========================================================
   吸边收起 / 弹出
   =========================================================== */

#[derive(Clone, Copy, Default)]
struct Rect {
    x: i32,
    y: i32,
    w: i32,
    h: i32,
}

#[derive(Default)]
struct DockState {
    /// 是否已贴边（贴边后才会自动收起）
    armed: bool,
    /// 当前是否处于收起状态
    hidden: bool,
    /// 贴的是哪条边：top / bottom / left / right
    edge: String,
    /// 组内各窗口的归位坐标
    homes: Vec<(String, i32, i32)>,
    /// 组的外接矩形（归位状态下的屏幕坐标）
    group: Rect,
    /// 收起时的整体位移
    shift: (i32, i32),
    /// 所在显示器边界
    mon: Rect,
    /// 鼠标开始离开的时刻
    out_since: Option<Instant>,
}

type SharedDock = Arc<Mutex<DockState>>;

#[cfg(windows)]
fn left_button_down() -> bool {
    use windows::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_LBUTTON};
    unsafe { (GetAsyncKeyState(VK_LBUTTON.0 as i32) as u16 & 0x8000) != 0 }
}

#[cfg(not(windows))]
fn left_button_down() -> bool {
    false
}

#[cfg(windows)]
fn cursor_pos() -> Option<(i32, i32)> {
    use windows::Win32::Foundation::POINT;
    use windows::Win32::UI::WindowsAndMessaging::GetCursorPos;
    let mut p = POINT::default();
    unsafe { GetCursorPos(&mut p).ok()? };
    Some((p.x, p.y))
}

#[cfg(not(windows))]
fn cursor_pos() -> Option<(i32, i32)> {
    None
}

/// 两个矩形是否贴合（相邻或重叠），用于把吸附在一起的窗口归为一组
fn rect_touching(a: Rect, b: Rect) -> bool {
    let ox = (a.x + a.w).min(b.x + b.w) - a.x.max(b.x);
    let oy = (a.y + a.h).min(b.y + b.h) - a.y.max(b.y);

    // 直接重叠
    if ox > 4 && oy > 4 {
        return true;
    }

    // 间隙（分离时为正数）
    let gx = (b.x - (a.x + a.w)).max(a.x - (b.x + b.w));
    let gy = (b.y - (a.y + a.h)).max(a.y - (b.y + b.h));

    (gx <= GROUP_GAP && oy > 8) || (gy <= GROUP_GAP && ox > 8)
}

/// 从 leader 出发，广度优先收集与它连成一片的可见窗口
fn collect_group(app: &tauri::AppHandle, leader: &str) -> Vec<(String, Rect)> {
    let mut all: Vec<(String, Rect)> = Vec::new();

    for (name, win) in app.webview_windows() {
        if !win.is_visible().unwrap_or(false) {
            continue;
        }
        let (Ok(p), Ok(s)) = (win.outer_position(), win.outer_size()) else {
            continue;
        };
        all.push((
            name,
            Rect {
                x: p.x,
                y: p.y,
                w: s.width as i32,
                h: s.height as i32,
            },
        ));
    }

    let Some(seed) = all.iter().find(|(n, _)| n == leader).cloned() else {
        return Vec::new();
    };

    let mut group: Vec<(String, Rect)> = vec![seed];
    let mut i = 0;
    while i < group.len() {
        let cur = group[i].1;
        for (name, r) in all.iter() {
            if group.iter().any(|(n, _)| n == name) {
                continue;
            }
            if rect_touching(cur, *r) {
                group.push((name.clone(), *r));
            }
        }
        i += 1;
    }

    group
}

/// 鼠标是否还停留在贴合组范围内（含少量余量）
fn in_group_zone(st: &DockState, cx: i32, cy: i32) -> bool {
    let g = st.group;
    cx >= g.x - 6 && cx <= g.x + g.w + 6 && cy >= g.y - 6 && cy <= g.y + g.h + 6
}

/// 鼠标是否已进入「露出条带」的感应区
fn in_reveal_zone(st: &DockState, cx: i32, cy: i32) -> bool {
    let g = st.group;
    let m = st.mon;
    let ex = DOCK_REVEAL;

    match st.edge.as_str() {
        "top" => {
            cy <= m.y + DOCK_KEEP + ex && cy >= m.y - 4 && cx >= g.x - ex && cx <= g.x + g.w + ex
        }
        "bottom" => {
            cy >= m.y + m.h - DOCK_KEEP - ex
                && cy <= m.y + m.h + 4
                && cx >= g.x - ex
                && cx <= g.x + g.w + ex
        }
        "left" => {
            cx <= m.x + DOCK_KEEP + ex && cx >= m.x - 4 && cy >= g.y - ex && cy <= g.y + g.h + ex
        }
        "right" => {
            cx >= m.x + m.w - DOCK_KEEP - ex
                && cx <= m.x + m.w + 4
                && cy >= g.y - ex
                && cy <= g.y + g.h + ex
        }
        _ => false,
    }
}

/// 前端拖动结束、判定贴边后调用：记录整组的归位坐标并开始监听鼠标
#[tauri::command]
fn dock_arm(
    app: tauri::AppHandle,
    dock: tauri::State<'_, SharedDock>,
    leader: String,
    edge: String,
) {
    let group = collect_group(&app, &leader);
    if group.is_empty() {
        return;
    }

    let mut gx = i32::MAX;
    let mut gy = i32::MAX;
    let mut gr = i32::MIN;
    let mut gb = i32::MIN;
    for (_, r) in &group {
        gx = gx.min(r.x);
        gy = gy.min(r.y);
        gr = gr.max(r.x + r.w);
        gb = gb.max(r.y + r.h);
    }
    let grect = Rect {
        x: gx,
        y: gy,
        w: gr - gx,
        h: gb - gy,
    };

    let mon = app
        .get_webview_window(&leader)
        .and_then(|w| w.current_monitor().ok().flatten())
        .map(|m| Rect {
            x: m.position().x,
            y: m.position().y,
            w: m.size().width as i32,
            h: m.size().height as i32,
        })
        .unwrap_or(Rect {
            x: 0,
            y: 0,
            w: 1920,
            h: 1080,
        });

    let shift = match edge.as_str() {
        "top" => (0, -(grect.h - DOCK_KEEP)),
        "bottom" => (0, grect.h - DOCK_KEEP),
        "left" => (-(grect.w - DOCK_KEEP), 0),
        "right" => (grect.w - DOCK_KEEP, 0),
        _ => (0, 0),
    };

    let mut st = dock.lock().unwrap();
    st.armed = true;
    st.hidden = false;
    st.edge = edge;
    st.group = grect;
    st.mon = mon;
    st.shift = shift;
    st.out_since = None;
    st.homes = group.into_iter().map(|(n, r)| (n, r.x, r.y)).collect();
}

/// 解除贴边：若当前处于收起状态，先把窗口归位
#[tauri::command]
fn dock_disarm(app: tauri::AppHandle, dock: tauri::State<'_, SharedDock>) {
    let restore = {
        let mut st = dock.lock().unwrap();
        if !st.armed {
            return;
        }
        let was_hidden = st.hidden;
        st.armed = false;
        st.hidden = false;
        st.out_since = None;
        if was_hidden {
            Some(std::mem::take(&mut st.homes))
        } else {
            st.homes.clear();
            None
        }
    };

    if let Some(homes) = restore {
        for (label, x, y) in homes {
            if let Some(w) = app.get_webview_window(&label) {
                let _ = w.set_position(PhysicalPosition::new(x, y));
            }
        }
    }
}

enum DockAct {
    None,
    Hide,
    Show,
}

/// 后台轮询鼠标：鼠标离开贴合组 → 整组收起；鼠标碰露出条 → 整组弹出
fn spawn_dock_watch(app: tauri::AppHandle, dock: SharedDock) {
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(55));

        let Some((cx, cy)) = cursor_pos() else {
            continue;
        };

        let (act, homes, shift) = {
            let mut st = dock.lock().unwrap();
            if !st.armed {
                continue;
            }

            let homes = st.homes.clone();
            let shift = st.shift;

            if st.hidden {
                if in_reveal_zone(&st, cx, cy) {
                    st.hidden = false;
                    st.out_since = None;
                    (DockAct::Show, homes, shift)
                } else {
                    (DockAct::None, homes, shift)
                }
            } else if in_group_zone(&st, cx, cy) || left_button_down() {
                st.out_since = None;
                (DockAct::None, homes, shift)
            } else {
                let now = Instant::now();
                let since = *st.out_since.get_or_insert(now);
                if now.duration_since(since) >= Duration::from_millis(DOCK_LINGER) {
                    st.hidden = true;
                    (DockAct::Hide, homes, shift)
                } else {
                    (DockAct::None, homes, shift)
                }
            }
        };

        match act {
            DockAct::Hide => {
                for (label, x, y) in &homes {
                    if let Some(w) = app.get_webview_window(label) {
                        let _ = w.set_position(PhysicalPosition::new(x + shift.0, y + shift.1));
                    }
                }
                let _ = app.emit("tt:dock", true);
            }
            DockAct::Show => {
                for (label, x, y) in &homes {
                    if let Some(w) = app.get_webview_window(label) {
                        let _ = w.set_position(PhysicalPosition::new(*x, *y));
                    }
                }
                let _ = app.emit("tt:dock", false);
            }
            DockAct::None => {}
        }
    });
}

/* ===========================================================
   面板堆叠布局：所有面板统一 x 对齐主体，纵向依次排在主体下方
   =========================================================== */

/// 面板的固定堆叠顺序
const PANEL_ORDER: [&str; 3] = ["playlist", "lyric", "eq"];

/// 三个面板的高度（顺序与 PANEL_ORDER 一致）
const PANEL_H: [i32; 3] = [266, 166, 250];

/* ===========================================================
   各窗口独立调整大小
   =========================================================== */

/// 布局基准尺寸：CSS 按这个尺寸设计，窗口默认就是这个大小
const BASE_W: i32 = MAIN_W;
const BASE_H: i32 = MAIN_H;
const BASE_PANEL_H: [i32; 3] = PANEL_H;

/// 窗口布局：label -> (x, y, w, h)，物理像素。
/// 隐藏窗口的 set_size 不生效，所以显示时要靠这份记录补设；
/// 退出时整表写进配置目录，下次启动照着还原（位置 + 尺寸一起记）。
type Layout = (i32, i32, u32, u32);

fn layout_store() -> &'static std::sync::Mutex<std::collections::HashMap<String, Layout>> {
    static L: std::sync::OnceLock<std::sync::Mutex<std::collections::HashMap<String, Layout>>> =
        std::sync::OnceLock::new();
    L.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
}

/// 各窗口上次退出时是否可见：下次启动照原样打开（否则关掉的面板会自己冒出来、
/// 打开的面板又不见了，用户会以为「没记住」）
fn vis_store() -> &'static std::sync::Mutex<std::collections::HashMap<String, bool>> {
    static V: std::sync::OnceLock<std::sync::Mutex<std::collections::HashMap<String, bool>>> =
        std::sync::OnceLock::new();
    V.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
}

fn saved_vis(app_label: &str) -> Option<bool> {
    vis_store().lock().unwrap().get(app_label).copied()
}

fn layout_path(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    use tauri::Manager as _;
    let dir = app.path().app_config_dir().ok()?;
    let _ = std::fs::create_dir_all(&dir);
    Some(dir.join("window-layout.txt"))
}

fn load_layout(app: &tauri::AppHandle) -> std::collections::HashMap<String, Layout> {
    let mut out = std::collections::HashMap::new();
    let Some(path) = layout_path(app) else {
        return out;
    };
    let Ok(text) = std::fs::read_to_string(path) else {
        return out;
    };
    for line in text.lines() {
        let f: Vec<&str> = line.split_whitespace().collect();
        if f.len() < 5 {
            continue;
        }
        match (
            f[1].parse::<i32>(),
            f[2].parse::<i32>(),
            f[3].parse::<u32>(),
            f[4].parse::<u32>(),
        ) {
            (Ok(x), Ok(y), Ok(w), Ok(h)) => {
                out.insert(f[0].to_string(), (x, y, w, h));
                // 第 6 列是可见性；老版本文件没有这一列，主窗口默认可见、面板默认按索引处理
                if let Some(v) = f.get(5) {
                    vis_store()
                        .lock()
                        .unwrap()
                        .insert(f[0].to_string(), *v == "1");
                }
                // 第 7 列：这个高度里是否已经含有示波带（老文件没有这一列）
                if f[0] == "main" {
                    if let Some(o) = f.get(6) {
                        OSC_ON.store(*o == "1", std::sync::atomic::Ordering::Relaxed);
                    }
                }
            }
            _ => {}
        }
    }
    out
}

/// 把内存里的布局写盘
fn flush_layout(app: &tauri::AppHandle) {
    let map = layout_store().lock().unwrap().clone();
    if map.is_empty() {
        return;
    }
    let Some(path) = layout_path(app) else {
        return;
    };
    let vis = vis_store().lock().unwrap().clone();
    let mut s = String::new();
    // 固定顺序写盘，方便排查（HashMap 顺序是随机的）
    for k in ["main", "playlist", "lyric", "eq"] {
        if let Some((x, y, w, h)) = map.get(k) {
            let v = match vis.get(k) {
                Some(b) => *b,
                None => k == "main",
            };
            s.push_str(&format!("{} {} {} {} {} {}", k, x, y, w, h, v as u8));
            if k == "main" {
                // 记下高度是否已含示波带，重启时才知道该不该补 26px
                s.push_str(&format!(
                    " {}\n",
                    OSC_ON.load(std::sync::atomic::Ordering::Relaxed) as u8
                ));
            } else {
                s.push('\n');
            }
        }
    }
    for (k, (x, y, w, h)) in map {
        if ["main", "playlist", "lyric", "eq"].iter().any(|n| *n == k) {
            continue;
        }
        s.push_str(&format!("{} {} {} {} {} 1\n", k, x, y, w, h));
    }
    let _ = std::fs::write(path, s);
}

/// 高频事件（拖动 / 缩放）里调用，1.2 秒内最多写一次盘
fn flush_layout_throttled(app: &tauri::AppHandle) {
    use std::sync::atomic::{AtomicU64, Ordering};
    static LAST: AtomicU64 = AtomicU64::new(0);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    if now.saturating_sub(LAST.load(Ordering::Relaxed)) < 1200 {
        return;
    }
    LAST.store(now, Ordering::Relaxed);
    flush_layout(app);
}

/// 记下某个窗口当前的几何
fn remember_win(window: &tauri::WebviewWindow) {
    // 关于窗是临时弹出来的，位置由「居中于主体」每次现算，不进布局表，
    // 否则 window-layout.txt 会多一行、下次启动还要处理这个不该还原的窗口
    if window.label() == "about" {
        return;
    }
    let Ok(p) = window.outer_position() else {
        return;
    };
    let Ok(s) = window.outer_size() else {
        return;
    };
    layout_store().lock().unwrap().insert(
        window.label().to_string(),
        (p.x, p.y, s.width, s.height),
    );
    vis_store()
        .lock()
        .unwrap()
        .insert(window.label().to_string(), window.is_visible().unwrap_or(true));
}

fn remember(app: &tauri::AppHandle, label: &str) {
    if let Some(w) = app.get_webview_window(label) {
        remember_win(&w);
    }
}

/// 让位置落在工作区内（换显示器 / 改分辨率后窗口不至于跑到屏幕外）
fn clamp_to_work_area(x: i32, y: i32, w: i32, h: i32) -> (i32, i32) {
    let Some(wa) = work_area() else {
        return (x, y);
    };
    let nx = x.clamp(wa.x, (wa.x + wa.w - w).max(wa.x));
    let ny = y.clamp(wa.y, (wa.y + wa.h - h).max(wa.y));
    (nx, ny)
}

fn default_size(label: &str) -> (u32, u32) {
    if label == "main" {
        return (BASE_W as u32, BASE_H as u32);
    }
    match PANEL_ORDER.iter().position(|x| *x == label) {
        Some(i) => (BASE_W as u32, BASE_PANEL_H[i] as u32),
        None => (BASE_W as u32, BASE_H as u32),
    }
}

/// 各窗口能缩到多小（再小按钮就挤坏了）
fn min_size(label: &str) -> (u32, u32) {
    match label {
        "main" => (330, 140),
        "playlist" => (300, 108),
        _ => (300, 96),
    }
}

/// 上限取工作区，别让窗口大到无法操作
fn max_size() -> (u32, u32) {
    work_area()
        .map(|r| (r.w as u32, r.h as u32))
        .unwrap_or((1920, 1080))
}

/// 某个窗口此刻是否还待在「主体那一列」里。
/// 用户把面板拖到旁边独立摆放后，它有自己的一条命，不参与列的重排与限高。
fn in_main_column(app: &tauri::AppHandle, label: &str) -> bool {
    if label == "main" {
        return true;
    }
    let Some(m) = main_rect(app) else { return true };
    match app
        .get_webview_window(label)
        .and_then(|w| w.outer_position().ok())
    {
        Some(p) => (p.x - m.x).abs() <= 6,
        None => true,
    }
}

/// 单个窗口的高度上限。
/// 列里的窗口要扣掉同列其他人，否则拉长一个会把整列顶出屏幕；
/// 独立摆放的窗口不该被别人牵连，上限就是整个工作区的高度。
fn max_size_for(app: &tauri::AppHandle, label: &str) -> (u32, u32) {
    let (mw, mh) = max_size();

    if !in_main_column(app, label) {
        return (mw, mh.saturating_sub(12));
    }

    let mut used = 0i32;
    for other in ["main", "playlist", "lyric", "eq"] {
        if other == label {
            continue;
        }
        if !in_main_column(app, other) {
            continue;
        }
        if let Some(w) = app.get_webview_window(other) {
            if w.is_visible().unwrap_or(false) {
                used += w.outer_size().map(|s| s.height as i32).unwrap_or(0);
            }
        }
    }
    // 再扣掉主体上边距：列已经往下摆了多少，就只能再长多少
    let wa_y = work_area().map(|r| r.y).unwrap_or(0);
    let top = main_rect(app).map(|m| m.y - wa_y).unwrap_or(0).max(0);
    let hmax = (mh as i32 - used - top - 10).max(96) as u32;
    (mw, hmax)
}

/// 主显示器工作区（已扣掉任务栏）
#[cfg(windows)]
fn work_area() -> Option<Rect> {
    use windows::Win32::Foundation::RECT;
    use windows::Win32::UI::WindowsAndMessaging::{
        SystemParametersInfoW, SPI_GETWORKAREA, SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS,
    };

    let mut r = RECT::default();
    unsafe {
        SystemParametersInfoW(
            SPI_GETWORKAREA,
            0,
            Some(&mut r as *mut _ as *mut core::ffi::c_void),
            SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS(0),
        )
        .ok()?;
    }
    Some(Rect {
        x: r.left,
        y: r.top,
        w: r.right - r.left,
        h: r.bottom - r.top,
    })
}

#[cfg(not(windows))]
fn work_area() -> Option<Rect> {
    None
}

/// 窗口所在显示器的可用上下边界（主屏按工作区算，避免被任务栏盖住）
fn screen_limits(win: &tauri::WebviewWindow) -> Option<(i32, i32)> {
    let mon = win.current_monitor().ok().flatten()?;
    let ms = mon.size();
    let mp = mon.position();
    let mut top = mp.y;
    let mut bottom = mp.y + ms.height as i32;

    let is_primary = win
        .primary_monitor()
        .ok()
        .flatten()
        .map(|pm| pm.position() == mp && pm.size() == ms)
        .unwrap_or(false);
    if is_primary {
        if let Some(w) = work_area() {
            top = w.y;
            bottom = w.y + w.h;
        }
    }

    Some((top + 8, bottom - 8))
}

/// 整列（主体 + 可见面板）超出屏幕底部时整体上移，
/// 免得放大后下半截跑到任务栏下面去
fn fit_column_in_screen(app: &tauri::AppHandle) {
    let Some(m) = main_rect(app) else { return };
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let Some((top, bottom)) = screen_limits(&win) else {
        return;
    };

    let mut total = m.h;
    for label in PANEL_ORDER {
        // 独立摆放的面板不在这一列里，别把它算进列高
        if !in_main_column(app, label) {
            continue;
        }
        if let Some(w) = app.get_webview_window(label) {
            if w.is_visible().unwrap_or(false) {
                total += w.outer_size().map(|s| s.height as i32).unwrap_or(0);
            }
        }
    }

    let mut y = m.y;
    if y + total > bottom {
        y = bottom - total;
    }
    if y < top {
        y = top;
    }
    if y != m.y {
        let _ = win.set_position(PhysicalPosition::new(m.x, y));
    }
}

/// 设定一个窗口的尺寸并记进布局表
fn set_win_size(app: &tauri::AppHandle, label: &str, w: u32, h: u32) {
    let (w, h) = (w.max(80), h.max(40));
    let mut pos = layout_store()
        .lock()
        .unwrap()
        .get(label)
        .map(|(x, y, _, _)| (*x, *y));
    if let Some(win) = app.get_webview_window(label) {
        let _ = win.set_size(PhysicalSize::new(w, h));
        if let (None, Ok(p)) = (pos, win.outer_position()) {
            pos = Some((p.x, p.y));
        }
    }
    let (x, y) = pos.unwrap_or((0, 0));
    layout_store()
        .lock()
        .unwrap()
        .insert(label.to_string(), (x, y, w, h));
}

/* ===========================================================
   示波形态：条带（+26px）/ 铺满（原高度）
   =========================================================== */

/// 条带模式比基准高度多出来的逻辑像素
const OSC_H: i32 = 26;

/// 当前窗口是否已经含了示波带（含了 = 上次是条带模式）。
/// 这是唯一权威：切模式时按它算增量，不会因为用户在两种模式之间
/// 手动拉伸过窗口而把 26px 加两遍或减成负数。
static OSC_ON: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// 从 session.json 里取上次的示波形态（"strip" / "bg" / "off"）
/// v0.12 之后前端不再需要这个判断，留着备查。
#[allow(dead_code)]
fn osc_from_session(app: &tauri::AppHandle) -> String {
    let Some(path) = session_path(app) else {
        return "strip".into();
    };
    let Ok(text) = std::fs::read_to_string(path) else {
        return "strip".into();
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) else {
        return "strip".into();
    };
    v.get("osc")
        .and_then(|x| x.as_str())
        .unwrap_or("strip")
        .to_string()
}

/// 切换示波带，返回窗口是否真的动了。
///
/// 向下长高；底下不够（贴着任务栏 / 屏幕下沿）时改成向上长，
/// 保证按钮行始终留在屏幕里。
fn resize_for_osc(app: &tauri::AppHandle, on: bool) {
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let Ok(p) = win.outer_position() else { return };
    let Ok(s) = win.outer_size() else { return };

    let sf = win.scale_factor().unwrap_or(1.0);
    let d = (OSC_H as f64 * sf).round() as i32;
    let cur_h = s.height as i32;
    let mut h = if on { cur_h + d } else { cur_h - d };
    h = h.max((min_size("main").1 as f64 * sf).round() as i32);

    let mut y = p.y;
    if on {
        if let Some((top, bottom)) = screen_limits(&win) {
            if p.y + h > bottom {
                // 下方放不下：整体上移，顶部不能越界
                y = (p.y - (h - cur_h)).max(top);
            }
        }
    }

    let _ = win.set_size(PhysicalSize::new(s.width, h as u32));
    if y != p.y {
        let _ = win.set_position(PhysicalPosition::new(p.x, y));
    }

    layout_store()
        .lock()
        .unwrap()
        .insert("main".to_string(), (p.x, y, s.width, h as u32));

    // 主体高度变了，下面贴着排的面板要跟着挪
    stack_panels_inner(app, None);
    fit_column_in_screen(app);
    stack_panels_inner(app, None);
    flush_layout(app);
}

/// 前端切换示波形态时调用（幂等：重复调用同一状态不会把高度改两次）
#[tauri::command]
fn set_main_osc(app: tauri::AppHandle, on: bool) {
    use std::sync::atomic::Ordering;
    if OSC_ON.swap(on, Ordering::Relaxed) == on {
        return;
    }
    resize_for_osc(&app, on);
    let _ = app.emit("tt:osc", on);
}

/// 前端在拖动 / 缩放结束后调用，把布局立刻落盘
#[tauri::command]
fn save_layout(app: tauri::AppHandle) {
    for label in ["main", "playlist", "lyric", "eq"] {
        if let Some(w) = app.get_webview_window(label) {
            remember_win(&w);
        }
    }
    flush_layout(&app);
}

/* ===========================================================
   播放列表记忆：上次加进来的文件 / 文件夹，下次启动照旧
   =========================================================== */

fn session_path(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    use tauri::Manager as _;
    let dir = app.path().app_config_dir().ok()?;
    let _ = std::fs::create_dir_all(&dir);
    Some(dir.join("session.json"))
}

/// 内容由前端拼成 JSON 传过来，Rust 只负责原子落盘（先写临时文件再改名，
/// 免得写到一半断电留下半个文件，下次启动整个歌单都读不出来）
#[tauri::command]
fn save_session(app: tauri::AppHandle, data: String) {
    let Some(path) = session_path(&app) else {
        return;
    };
    let tmp = path.with_extension("json.tmp");
    if std::fs::write(&tmp, data).is_ok() {
        // 留一份上一版：误删列表 / 清空歌单还能从 session.json.bak 找回
        if path.exists() {
            let _ = std::fs::copy(&path, path.with_extension("json.bak"));
        }
        let _ = std::fs::rename(&tmp, &path);
    }
}

#[tauri::command]
fn load_session(app: tauri::AppHandle) -> Option<String> {
    let path = session_path(&app)?;
    std::fs::read_to_string(path).ok()
}

/// 恢复某个窗口的默认尺寸（双击手柄）
#[tauri::command]
fn reset_size(app: tauri::AppHandle, label: String) {
    let (w0, h) = default_size(&label);
    // 面板回默认高度时，宽度跟主体对齐
    let w = if label == "main" {
        w0
    } else {
        main_rect(&app).map(|m| m.w as u32).unwrap_or(w0)
    };
    set_win_size(&app, &label, w, h);
    stack_panels_inner(&app, None);
    fit_column_in_screen(&app);
    let _ = app.emit("tt:resizeend", label);
}

/// 拖右下角手柄：按住左键期间持续按鼠标位移调整**这一个窗口**的尺寸。
/// 只有主窗口会顺带把面板宽度拉齐（整列保持等宽），面板改尺寸绝不反向影响别人。
#[tauri::command]
fn resize_begin(app: tauri::AppHandle, label: String) {
    let Some(win) = app.get_webview_window(&label) else {
        return;
    };
    let Ok(sz) = win.outer_size() else { return };
    let Some((cx0, cy0)) = cursor_pos() else { return };

    let start_w = sz.width as f64;
    let start_h = sz.height as f64;
    let (minw, minh) = min_size(&label);
    let (maxw, maxh) = max_size_for(&app, &label);
    let is_main = label == "main";
    // 独立摆放的窗口：往下拉到底时整体上移，让它能一直长到屏幕顶，
    // 而不是「一到屏幕底就再也拉不长」
    let floating = !in_main_column(&app, &label);
    let y0 = win.outer_position().map(|p| p.y).unwrap_or(0);
    let wa = work_area();
    let app2 = app.clone();
    let lb = label.clone();

    std::thread::spawn(move || {
        let mut last = (sz.width, sz.height);
        let mut last_y = y0;
        while left_button_down() {
            if let Some((cx, cy)) = cursor_pos() {
                let w = (start_w + (cx - cx0) as f64).clamp(minw as f64, maxw as f64) as u32;
                let h = (start_h + (cy - cy0) as f64).clamp(minh as f64, maxh as f64) as u32;
                if (w, h) != last {
                    let _ = win.set_size(PhysicalSize::new(w, h));
                    last = (w, h);
                    if floating {
                        if let Some(wa) = wa {
                            let bottom = wa.y + wa.h - 8;
                            let top_lim = wa.y + 4;
                            // 先试着保持在原位；装不下就把窗口往上顶
                            let ny = (bottom - h as i32).min(y0).max(top_lim);
                            if ny != last_y {
                                let _ = win.set_position(PhysicalPosition::new(
                                    win.outer_position().map(|p| p.x).unwrap_or(0),
                                    ny,
                                ));
                                last_y = ny;
                            }
                        }
                    }
                    if is_main {
                        for p in PANEL_ORDER {
                            if let Some(pw) = app2.get_webview_window(p) {
                                let ph = pw.outer_size().map(|s| s.height).unwrap_or(0);
                                let (px, py) = pw
                                    .outer_position()
                                    .map(|q| (q.x, q.y))
                                    .unwrap_or((0, 0));
                                if ph > 0 {
                                    let _ = pw.set_size(PhysicalSize::new(w, ph));
                                    layout_store()
                                        .lock()
                                        .unwrap()
                                        .insert(p.to_string(), (px, py, w, ph));
                                }
                            }
                        }
                    }
                    stack_panels_inner(&app2, None);
                }
            }
            std::thread::sleep(Duration::from_millis(14));
        }
        if let Some(win) = app2.get_webview_window(&lb) {
            remember_win(&win);
        }
        flush_layout_throttled(&app2);
        stack_panels_inner(&app2, None);
        // 拉长后整列可能顶出工作区（被任务栏盖住），整体上移让位
        fit_column_in_screen(&app2);
        let _ = app2.emit("tt:resizeend", lb);
    });
}

fn main_rect(app: &tauri::AppHandle) -> Option<Rect> {
    let w = app.get_webview_window("main")?;
    let p = w.outer_position().ok()?;
    let s = w.outer_size().ok()?;
    Some(Rect {
        x: p.x,
        y: p.y,
        w: s.width as i32,
        h: s.height as i32,
    })
}

/// 把面板按 PANEL_ORDER 重新竖向排列在主体下方
///
/// `focus`：刚被显示出来的面板（此刻 is_visible 可能还没刷新，强制按可见处理）
///
/// 只有「水平方向仍与主体对齐」的面板才参与重排；被用户拖到别处的面板
/// 保留原位，也不占位（否则会在列里留一个空洞）。
fn stack_panels_inner(app: &tauri::AppHandle, focus: Option<&str>) {
    let Some(m) = main_rect(app) else { return };
    let mut cy = m.y + m.h;

    for label in PANEL_ORDER {
        let Some(w) = app.get_webview_window(label) else {
            continue;
        };
        let Ok(s) = w.outer_size() else { continue };
        let h = s.height as i32;

        let is_focus = focus == Some(label);
        let visible = is_focus || w.is_visible().unwrap_or(false);
        if !visible {
            continue;
        }

        let in_column = if is_focus {
            true
        } else {
            w.outer_position()
                .map(|p| (p.x - m.x).abs() <= 6)
                .unwrap_or(false)
        };
        if !in_column {
            continue;
        }

        let _ = w.set_position(PhysicalPosition::new(m.x, cy));
        cy += h;
    }
}

/// 启动时把所有可见面板强制重排一遍（保证和主体严丝合缝）
#[tauri::command]
fn align_all(app: tauri::AppHandle) {
    stack_panels_inner(&app, None);
}

/// 把窗口提到 Z 序最前面，但不抢焦点。
///
/// 三个面板都 `skip_taskbar`，任务栏上只有主体一个图标。点它的时候
/// Windows 只把主体拎到前台，面板本身没被最小化、也没被 hide，
/// 只是「被别的程序盖住了」——`is_visible()` 依旧是 true，
/// unminimize / show 都当无事发生，只能显式把它们提上 Z 序。
#[cfg(windows)]
fn raise_no_activate(w: &tauri::WebviewWindow) {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, HWND_TOP, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOOWNERZORDER, SWP_NOSIZE,
    };
    let Ok(h) = w.hwnd() else { return };
    // tauri 与本地依赖的 windows crate 版本可能不同，过一手裸指针再重建句柄
    let raw = h.0 as isize;
    if raw == 0 {
        return;
    }
    unsafe {
        let _ = SetWindowPos(
            HWND(raw as _),
            Some(HWND_TOP),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
        );
    }
}

#[cfg(not(windows))]
fn raise_no_activate(_w: &tauri::WebviewWindow) {}

/// 把整套窗口拉回前台：主体 + 所有「用户没关掉」的面板。
///
/// 两种情况都会调到这里：
/// 1. 任务栏图标 / 单实例第二次启动（重新双击 exe）
/// 2. 主体从后台重新获得焦点（Win+D 之后、被最大化窗口盖住之后）
///
/// 跳过 `is_visible()` 为 false 的面板：用户主动关掉的窗口不该自己冒出来，
/// 贴边自动收起用的也是 hide。只有「可见但被遮挡」的才需要提 Z 序。
fn bring_all_forward(app: &tauri::AppHandle) {
    dock_restore_positions(app);

    let mut revived = false;
    for label in PANEL_ORDER {
        let Some(w) = app.get_webview_window(label) else {
            continue;
        };
        if !w.is_visible().unwrap_or(false) {
            continue;
        }
        if w.is_minimized().unwrap_or(false) {
            let _ = w.unminimize();
            let _ = w.show();
            revived = true;
        }
        raise_no_activate(&w);
    }
    if revived {
        // 最小化期间 set_size 不生效，恢复后重新贴一遍列
        stack_panels_inner(app, None);
    }

    if let Some(m) = app.get_webview_window("main") {
        if m.is_minimized().unwrap_or(false) {
            let _ = m.unminimize();
            let _ = m.show();
        }
        // 主体最后提，保证它在面板之上
        raise_no_activate(&m);
    }
}

/// 整组贴着屏幕边缘自动收起时，窗口其实停在屏幕外（只留一条边）。
/// 这时把窗口 show 出来等于什么都没看见，得先把它弹回原位。
fn dock_restore_positions(app: &tauri::AppHandle) {
    let st = app.state::<SharedDock>();
    let homes = {
        let mut g = st.lock().unwrap();
        if !g.hidden {
            return;
        }
        g.armed = false;
        g.hidden = false;
        g.out_since = None;
        std::mem::take(&mut g.homes)
    };
    for (label, x, y) in homes {
        if let Some(w) = app.get_webview_window(&label) {
            let _ = w.set_position(PhysicalPosition::new(x, y));
        }
    }
}

/// 又一次双击图标启动了新实例 → 新实例被单实例插件拦下，
/// 由已在运行的这个实例把整套窗口唤回前台。
fn wake_all(app: &tauri::AppHandle) {
    bring_all_forward(app);
    if let Some(m) = app.get_webview_window("main") {
        let _ = m.set_focus();
    }
}

/// 面板刚显示时调用：按记录补设尺寸（隐藏期间 set_size 不生效），
/// 再把它插进列里正确的位置，并把下方面板顺次下推
#[tauri::command]
fn place_panel(app: tauri::AppHandle, label: String) {
    let rec = layout_store().lock().unwrap().get(&label).copied();
    let want = rec
        .map(|(_, _, w, h)| (w, h))
        .unwrap_or_else(|| default_size(&label));
    // 宽度跟主体对齐（面板自己拉窄过就保留自己的宽度）
    let main_w = main_rect(&app).map(|m| m.w as u32).unwrap_or(want.0);
    let w = if want.0 == default_size(&label).0 {
        main_w
    } else {
        want.0
    };
    set_win_size(&app, &label, w, want.1);

    // 上次这个面板是被用户单独摆在外面的（不在主体那一列）→ 回到它自己的位置，
    // 别一把把它揪回列里
    let main_x = main_rect(&app).map(|m| m.x).unwrap_or(0);
    if let Some((x, y, _, _)) = rec {
        if (x - main_x).abs() > 6 {
            let (cx, cy) = clamp_to_work_area(x, y, w as i32, want.1 as i32);
            if let Some(win) = app.get_webview_window(&label) {
                let _ = win.set_position(PhysicalPosition::new(cx, cy));
            }
            layout_store()
                .lock()
                .unwrap()
                .insert(label.clone(), (cx, cy, w, want.1));
            return;
        }
    }

    stack_panels_inner(&app, Some(&label));
    // 面板一显示，整列就变长，可能顶出工作区；上移后要按新位置再排一次
    fit_column_in_screen(&app);
    stack_panels_inner(&app, Some(&label));
}

/// 面板隐藏后调用：把空出来的位置补上，下方面板顺次上移
#[tauri::command]
fn compact_panels(app: tauri::AppHandle) {
    stack_panels_inner(&app, None);
}

/* ===========================================================
   窗口创建 / 文件选择
   =========================================================== */

/// 创建一个面板子窗口（无边框 / 透明 / 不进任务栏）
fn make_panel(
    app: &tauri::AppHandle,
    label: &str,
    title: &str,
    w: i32,
    h: i32,
    x: i32,
    y: i32,
    visible: bool,
) {
    let builder = WebviewWindowBuilder::new(app, label, WebviewUrl::App("index.html".into()))
        .title(title)
        .inner_size(w as f64, h as f64)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .resizable(false)
        .skip_taskbar(true)
        .visible(visible)
        .focused(false);

    match builder.build() {
        Ok(win) => {
            let _ = win.set_position(PhysicalPosition::new(x, y));
        }
        Err(e) => eprintln!("创建窗口 {} 失败: {}", label, e),
    }
}

/* ---------- 关于窗口 ---------- */

/// 关于窗的物理尺寸（窗体内留 12px 给投影，所以比 340×258 的对话框大一圈）
const ABOUT_W: i32 = 364;
const ABOUT_H: i32 = 282;

/// 让关于窗居中压在主体上；比主体高，就整体往上挪一点，别把标题栏顶出屏幕
fn about_center(app: &tauri::AppHandle) -> (i32, i32) {
    let (mw, mh, mx, my) = match app.get_webview_window("main") {
        Some(m) => {
            let s = m.outer_size().unwrap_or(PhysicalSize::new(390, 160));
            let p = m.outer_position().unwrap_or(PhysicalPosition::new(0, 0));
            (s.width as i32, s.height as i32, p.x, p.y)
        }
        None => (390, 160, 0, 0),
    };
    let x = mx + (mw - ABOUT_W) / 2;
    let y = my + (mh - ABOUT_H) / 2;
    clamp_to_work_area(x, y, ABOUT_W, ABOUT_H)
}

/// 打开「关于」小窗：已存在就挪到主体中间并提到前面，不存在才创建（单例）
///
/// 必须是 async：同步命令跑在主线程上，而 `build()` 要等主线程派发窗口创建，
/// 两边互等就死锁了（表现为前端 invoke 永远不 resolve、窗口不出现）。
#[tauri::command]
async fn open_about(app: tauri::AppHandle) {
    let (x, y) = about_center(&app);
    // 主体置顶时，关于窗也得跟着置顶，否则会被主体自己盖住
    let top = app
        .get_webview_window("main")
        .and_then(|m| m.is_always_on_top().ok())
        .unwrap_or(false);

    if let Some(w) = app.get_webview_window("about") {
        let _ = w.set_position(PhysicalPosition::new(x, y));
        let _ = w.set_always_on_top(top);
        let _ = w.show();
        let _ = w.set_focus();
        return;
    }

    let builder = WebviewWindowBuilder::new(&app, "about", WebviewUrl::App("index.html".into()))
        .title("关于 宁宁静听")
        .inner_size(ABOUT_W as f64, ABOUT_H as f64)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .resizable(false)
        .always_on_top(top)
        .skip_taskbar(true)
        .visible(false)
        .focused(false);

    match builder.build() {
        Ok(win) => {
            // 先摆好位置再显示，避免先在默认位置闪一下
            let _ = win.set_position(PhysicalPosition::new(x, y));
            let _ = win.show();
            let _ = win.set_focus();
        }
        Err(e) => eprintln!("创建关于窗口失败: {}", e),
    }
}

/// 判断扩展名是否是支持的音频格式
fn is_audio_file(p: &std::path::Path) -> bool {
    matches!(
        p.extension()
            .and_then(|e| e.to_str())
            .map(|e| e.to_ascii_lowercase())
            .as_deref(),
        Some("mp3") | Some("flac") | Some("wav") | Some("ogg") | Some("oga")
            | Some("opus") | Some("m4a") | Some("aac")
    )
}

/// 递归收集目录下的音频文件（跳过隐藏目录、回收站、系统目录与符号链接）
fn collect_audio(dir: &std::path::Path, out: &mut Vec<String>, depth: usize) {
    collect_audio_cb(dir, out, depth, &mut |_| {});
}

/// 同上，但每发现一批就回调一次已找到的数量（给前端报进度用）。
/// 回调拿到的是「累计已找到」而不是增量，前端直接显示就行。
fn collect_audio_cb(
    dir: &std::path::Path,
    out: &mut Vec<String>,
    depth: usize,
    cb: &mut dyn FnMut(usize),
) {
    if depth > 16 || out.len() > 20000 {
        return;
    }
    let Ok(rd) = std::fs::read_dir(dir) else { return };

    cb(out.len());
    for entry in rd.flatten() {
        let path = entry.path();
        let Ok(ft) = entry.file_type() else { continue };
        if ft.is_symlink() {
            continue;
        }
        if ft.is_dir() {
            let name = entry.file_name().to_string_lossy().to_string();
            let skip = name.starts_with('.')
                || name.eq_ignore_ascii_case("$RECYCLE.BIN")
                || name.eq_ignore_ascii_case("System Volume Information")
                || name.eq_ignore_ascii_case("node_modules");
            if !skip {
                collect_audio_cb(&path, out, depth + 1, cb);
            }
        } else if is_audio_file(&path) {
            out.push(path.to_string_lossy().to_string());
            cb(out.len());
        }
    }
}

/// 扫描进度：phase 目前只有 walk（还在走目录，总数未知）
#[derive(Clone, serde::Serialize)]
struct ScanTick {
    phase: &'static str,
    found: usize,
}

/// 原生「选择文件夹」对话框。
/// 只返回目录路径——遍历交给 `scan_audio_dir_progress`，
/// 这样选完目录能立刻在界面上报进度，而不是干等几十秒。
#[tauri::command]
async fn pick_music_dir(app: tauri::AppHandle) -> Vec<String> {
    use tauri_plugin_dialog::DialogExt;

    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().pick_folder(move |dir| {
        let _ = tx.send(dir);
    });

    match rx.recv_timeout(std::time::Duration::from_secs(600)) {
        Ok(Some(dir)) => dir
            .into_path()
            .ok()
            .map(|p| vec![p.to_string_lossy().to_string()])
            .unwrap_or_default(),
        _ => Vec::new(),
    }
}

/// 扫描一个路径（异步 + 报进度）：文件直接判定，目录递归收集。
/// 走目录期间每隔 70ms 发一次 `tt:scan`，前端拿它在状态行里画进度。
#[tauri::command]
async fn scan_audio_dir_progress(app: tauri::AppHandle, path: String) -> Vec<String> {
    tauri::async_runtime::spawn_blocking(move || {
        let p = std::path::PathBuf::from(&path);
        let mut out = Vec::new();
        if p.is_file() {
            if is_audio_file(&p) {
                out.push(p.to_string_lossy().to_string());
            }
            return out;
        }
        let mut last = std::time::Instant::now();
        {
            let mut tick = |n: usize| {
                if last.elapsed().as_millis() >= 70 {
                    last = std::time::Instant::now();
                    let _ = app.emit("tt:scan", ScanTick { phase: "walk", found: n });
                }
            };
            collect_audio_cb(&p, &mut out, 0, &mut tick);
        }
        out.sort_by_key(|a| a.to_lowercase());
        // 结束再发一次，把最终数量交出去（同时让前端知道「这一遍走完了」）
        let _ = app.emit("tt:scan", ScanTick { phase: "walk", found: out.len() });
        out
    })
    .await
    .unwrap_or_default()
}

/// 扫描一个路径（同步版，给不需要进度的场合用）
#[tauri::command]
fn scan_audio_dir(path: String) -> Vec<String> {
    scan_audio_dir_impl(std::path::Path::new(&path))
}

fn scan_audio_dir_impl(p: &std::path::Path) -> Vec<String> {
    if p.is_file() {
        return if is_audio_file(p) {
            vec![p.to_string_lossy().to_string()]
        } else {
            Vec::new()
        };
    }
    let mut out = Vec::new();
    collect_audio(p, &mut out, 0);
    out.sort_by_key(|a| a.to_lowercase());
    out
}

/// 读取与音频同目录同名的 .lrc 歌词（UTF-8，自动去掉 BOM）
#[tauri::command]
fn read_lyric_file(path: String) -> Option<String> {
    let p = std::path::Path::new(&path);
    let lrc = p.with_extension("lrc");
    let bytes = std::fs::read(&lrc).ok()?;
    let text = String::from_utf8(bytes).ok()?;
    Some(text.trim_start_matches('\u{feff}').to_string())
}

/// 原生文件选择对话框，返回选中的音频文件绝对路径
#[tauri::command]
async fn pick_audio_files(app: tauri::AppHandle) -> Vec<String> {
    use tauri_plugin_dialog::DialogExt;

    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog()
        .file()
        .add_filter("音频文件", &["mp3", "flac", "wav", "ogg", "oga", "opus", "m4a", "aac"])
        .pick_files(move |files| {
            let _ = tx.send(files);
        });

    match rx.recv_timeout(std::time::Duration::from_secs(600)) {
        Ok(Some(paths)) => paths
            .into_iter()
            .filter_map(|p| p.into_path().ok())
            .map(|p| p.to_string_lossy().to_string())
            .collect(),
        _ => Vec::new(),
    }
}

/// 导出 WAV 的保存对话框，返回目标绝对路径
#[tauri::command]
async fn pick_wav_target(app: tauri::AppHandle, default_name: String) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;

    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog()
        .file()
        .set_file_name(&default_name)
        .add_filter("WAV 音频", &["wav"])
        .save_file(move |p| {
            let _ = tx.send(p);
        });

    match rx.recv_timeout(std::time::Duration::from_secs(600)) {
        Ok(Some(p)) => p.into_path().ok().map(|p| p.to_string_lossy().to_string()),
        _ => None,
    }
}

/* ===========================================================
   拖动跟随
   =========================================================== */

/// 前端在标题栏 mousedown 时调用：快照所有窗口相对该窗口的偏移，开启跟随
/// 只有拖动主窗口（main）才会带动其余窗口，拖子窗口时其余窗口保持不动
#[tauri::command]
fn drag_begin(app: tauri::AppHandle, drag: tauri::State<'_, SharedDrag>, label: String) {
    let mut st = drag.lock().unwrap();
    st.active = true;
    st.leader = Some(label.clone());
    st.offsets.clear();
    st.last_move = Some(Instant::now());

    if label != "main" {
        return;
    }

    let leader_pos = app
        .get_webview_window(&label)
        .and_then(|w| w.outer_position().ok());

    let Some(lp) = leader_pos else { return };

    for (name, win) in app.webview_windows() {
        if name == label {
            continue;
        }
        if let Ok(p) = win.outer_position() {
            st.offsets.insert(name, (p.x - lp.x, p.y - lp.y));
        }
    }
}

/// 被拖动的窗口移动时，按快照偏移带动其余窗口
fn sync_followers(app: &tauri::AppHandle, label: &str, pos: PhysicalPosition<i32>, drag: &SharedDrag) {
    let offsets = {
        let mut st = drag.lock().unwrap();
        if !st.active {
            return;
        }
        if st.leader.as_deref() != Some(label) {
            return;
        }
        st.last_move = Some(Instant::now());
        st.offsets.clone()
    };

    for (name, (dx, dy)) in offsets {
        if let Some(w) = app.get_webview_window(&name) {
            let _ = w.set_position(PhysicalPosition::new(pos.x + dx, pos.y + dy));
        }
    }
}

/// 拖动结束判定：位移静默 + 鼠标左键已松开 → 通知前端做磁吸/吸边
fn spawn_watchdog(app: tauri::AppHandle, drag: SharedDrag) {
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(90));

        let leader = {
            let mut st = drag.lock().unwrap();
            if !st.active {
                continue;
            }

            let idle = st
                .last_move
                .map(|t| t.elapsed() > Duration::from_millis(240))
                .unwrap_or(true);

            if idle && !left_button_down() {
                st.active = false;
                let l = st.leader.clone();
                l
            } else {
                continue;
            }
        };

        if let Some(l) = leader {
            let _ = app.emit("tt:dragend", l);
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let drag: SharedDrag = Arc::new(Mutex::new(DragState::default()));
    let drag_for_event = drag.clone();
    let drag_for_watch = drag.clone();

    let dock: SharedDock = Arc::new(Mutex::new(DockState::default()));
    let dock_for_event = dock.clone();
    let dock_for_watch = dock.clone();

    tauri::Builder::default()
        // 单实例必须第一个注册：第二份进程在窗口创建前就被拦下
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            wake_all(app);
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(drag)
        .manage(dock)
        .invoke_handler(tauri::generate_handler![
            pick_audio_files,
            pick_music_dir,
            pick_wav_target,
            meta::read_audio_meta,
            meta::export_wav_file,
            scan_audio_dir,
            scan_audio_dir_progress,
            read_lyric_file,
            drag_begin,
            dock_arm,
            dock_disarm,
            align_all,
            place_panel,
            compact_panels,
            open_about,
            save_layout,
            save_session,
            load_session,
            reset_size,
            resize_begin,
            set_main_osc
        ])
        .on_window_event(move |window, event| match event {
            tauri::WindowEvent::Moved(pos) => {
                let app = window.app_handle();
                sync_followers(app, window.label(), *pos, &drag_for_event);
                // 贴边收起时窗口被挪到屏幕外，那是个「假位置」，不能记成用户意图
                let docked_hidden = dock_for_event.lock().unwrap().hidden;
                if !docked_hidden {
                    // 主体和面板都要记：面板可以被用户拖到列外独立摆放
                    remember(app, window.label());
                    if window.label() == "main" {
                        flush_layout_throttled(app);
                    }
                }
            }
            tauri::WindowEvent::Resized(_) => {
                remember(window.app_handle(), window.label());
            }
            tauri::WindowEvent::Focused(true) => {
                // 主体回到前台（点任务栏图标 / Win+D 之后 / 被最大化窗口盖住后切回来）：
                // 面板很可能只是被压在别的程序下面，一并提到 Z 序前面
                if window.label() == "main" {
                    bring_all_forward(window.app_handle());
                }
            }
            tauri::WindowEvent::CloseRequested { .. } => {
                // 关闭主窗口 = 退出整个程序（子窗口一起走）
                if window.label() == "main" {
                    for label in ["main", "playlist", "lyric", "eq"] {
                        remember(window.app_handle(), label);
                    }
                    flush_layout(&window.app_handle());
                    window.app_handle().exit(0);
                }
            }
            _ => {}
        })
        .setup(move |app| {
            let handle = app.handle().clone();

            spawn_watchdog(handle.clone(), drag_for_watch.clone());
            spawn_dock_watch(handle.clone(), dock_for_watch.clone());

            let saved = load_layout(&handle);

            if let Some(main) = handle.get_webview_window("main") {
                let mut size = main
                    .outer_size()
                    .map(|s| (s.width as i32, s.height as i32))
                    .unwrap_or((MAIN_W, MAIN_H));

                // 上次的布局优先：位置 + 尺寸一起还原
                let restored = if let Some(&(sx, sy, sw, sh)) = saved.get("main") {
                    let (minw, minh) = min_size("main");
                    let (maxw, maxh) = max_size();
                    let w = (sw as i32).clamp(minw as i32, maxw as i32);
                    let h = (sh as i32).clamp(minh as i32, maxh as i32);
                    size = (w, h);
                    let _ = main.set_size(PhysicalSize::new(w as u32, h as u32));
                    let (cx, cy) = clamp_to_work_area(sx, sy, w, h);
                    let _ = main.set_position(PhysicalPosition::new(cx, cy));
                    Some((cx, cy))
                } else {
                    None
                };

                let (mx, my) = match restored {
                    Some(p) => p,
                    None => {
                        let pos = main
                            .outer_position()
                            .map(|p| (p.x, p.y))
                            .unwrap_or((0, 0));
                        // 位置兜底：读不到就用屏幕居中
                        let (mx, my) = if pos == (0, 0) {
                            match handle.primary_monitor() {
                                Ok(Some(m)) => {
                                    let ms = m.size();
                                    let mp = m.position();
                                    (
                                        mp.x + (ms.width as i32 - size.0) / 2,
                                        mp.y + (ms.height as i32 - size.1) / 2,
                                    )
                                }
                                _ => (300, 300),
                            }
                        } else {
                            pos
                        };

                        // 主体 + 三个面板是一整列，默认把它整体居中放，
                        // 免得面板全开时均衡器被挤到屏幕外面去
                        let col_h = size.1 + PANEL_H.iter().sum::<i32>();
                        let mut my = my;
                        if let Ok(Some(m)) = handle.primary_monitor() {
                            let ms = m.size();
                            let mp = m.position();
                            let top = mp.y + 24;
                            let bottom = mp.y + ms.height as i32 - 24;
                            let want = (mp.y + (ms.height as i32 - col_h) / 2).max(top);
                            my = if want + col_h > bottom { top } else { want };
                            let _ = main.set_position(PhysicalPosition::new(mx, my));
                        }
                        (mx, my)
                    }
                };

                // 上次的布局原样还原：位置、尺寸、以及「哪些面板开着」。
                // 没有记录的才退回「纵向堆在主体下方」的默认布局。
                let w0 = size.0;
                let mut y1 = my + size.1;
                let titles = ["播放列表", "歌词秀", "均衡器"];
                for (i, label) in PANEL_ORDER.iter().enumerate() {
                    let (minw, minh) = min_size(label);
                    let (maxw, maxh) = max_size();
                    let vis = saved_vis(label).unwrap_or(i == 0);
                    let (pw, ph, px, py) = match saved.get(*label) {
                        Some(&(x, y, w, h)) => {
                            let w = (w as i32).clamp(minw as i32, maxw as i32);
                            let h = (h as i32).clamp(minh as i32, maxh as i32);
                            let (cx, cy) = clamp_to_work_area(x, y, w, h);
                            (w, h, cx, cy)
                        }
                        None => (w0, PANEL_H[i], mx, y1),
                    };
                    make_panel(&handle, label, titles[i], pw, ph, px, py, vis);
                    y1 += ph;
                }

                // 列里的面板按新主体位置重新贴合（独立摆放的不会被动）
                stack_panels_inner(&handle, None);

                // 先把当前几何记进表，免得没动过窗口就退出时丢掉
                for label in ["main", "playlist", "lyric", "eq"] {
                    remember(&handle, label);
                }

                // v0.12 起主体改成左屏右控，示波屏占满内容区，
                // 不再有「条带要额外 26px」这回事。
                // 老布局第 7 列 = 1 说明当前高度里含过那条带子，把这 26px 收回来一次。
                if OSC_ON.swap(false, std::sync::atomic::Ordering::Relaxed) {
                    if let Some(m) = handle.get_webview_window("main") {
                        if let (Ok(p), Ok(os)) = (m.outer_position(), m.outer_size()) {
                            let sf = m.scale_factor().unwrap_or(1.0).max(0.1);
                            let drop = (OSC_H as f64 * sf).round() as u32;
                            let new_h = os.height.saturating_sub(drop);
                            let _ = m.set_size(PhysicalSize::new(os.width, new_h));
                            // 记忆表要跟着改，否则下次启动又从旧高度算一遍
                            layout_store().lock().unwrap().insert(
                                "main".to_string(),
                                (p.x, p.y, os.width, new_h),
                            );
                            stack_panels_inner(&handle, None);
                        }
                    }
                    flush_layout(&handle);
                }
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
