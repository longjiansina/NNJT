/* ===========================================================
   宁宁静听 · 四窗口架构
   main 窗口持有音频引擎与播放状态，其余窗口通过事件同步
   =========================================================== */

const T = window.__TAURI__;
const { emit, listen } = T.event;const { getCurrentWindow, getAllWindows, currentMonitor } = T.window;
const { convertFileSrc, invoke } = T.core;

const PhysPos = (T.dpi && T.dpi.PhysicalPosition)
  ? T.dpi.PhysicalPosition
  : class { constructor(x, y) { this.type = 'Physical'; this.x = x; this.y = y; } };

const ME = getCurrentWindow();
const LABEL = ME.label;
const IS_MAIN = LABEL === 'main';
const LYRIC = LABEL === 'lyric';
const ABOUT = LABEL === 'about';
document.body.dataset.w = LABEL;

/* 未捕获异常留在 window.__err 里，自动化测试可以断言「没报错」 */
window.__err = [];
window.addEventListener('error', (e) => window.__err.push(String(e.message)));
window.addEventListener('unhandledrejection', (e) => window.__err.push('promise: ' + e.reason));

const $ = (id) => document.getElementById(id);
const appEl = $('app');

/* 打开方式菜单：点按钮弹「添加文件 / 添加文件夹」小菜单 */
function bindOpenMenu(btnId, menuId, onPick) {
  const btn = $(btnId);
  const menu = $(menuId);
  if (!btn || !menu) return;

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
  });

  menu.querySelectorAll('.om-item').forEach((it) => {
    it.addEventListener('click', (e) => {
      e.stopPropagation();
      menu.hidden = true;
      onPick(it.dataset.act);
    });
  });

  document.addEventListener('mousedown', (e) => {
    if (menu.hidden) return;
    if (e.target.closest('#' + menuId) || e.target.closest('#' + btnId)) return;
    menu.hidden = true;
  });
}

/* ================= 状态 ================= */
/* 能直接播的格式（Chromium 解码）：MP3 / FLAC / WAV / OGG / Opus / M4A / AAC
   WMA、APE、MPC、RM 等需外部转码，暂不支持 */
const AUDIO_RE = /\.(mp3|flac|wav|ogg|oga|opus|m4a|aac)$/i;

const state = {
  playlist: [],      // 始终指向「当前列表」的 tracks 数组
  lists: [],         // [{ name, tracks: [], curr }]，只有主窗口是权威
  active: 0,
  listMeta: [],      // 面板窗口用：由主窗口广播过来 [{ name, n }]
  plRev: 0,
  curr: -1,
  playing: false,
  position: 0,
  duration: 0,
  volume: 0.8,
  muted: false,
  mode: 0,
  skin: localStorage.getItem('ttplayer-skin') || 'classic',
  eq: new Array(10).fill(0),
  eqOn: true,
  // 可视化形态：spec = 频谱（默认）/ wave = 示波波形 / bg = 铺满整窗做底纹
  osc: 'spec',
};

state.lists = [{ name: '默认', tracks: state.playlist, curr: -1 }];

/* v0.12 主体换成左屏右控，紫皮才是配套配色：
   升级后第一次启动无条件切过去（旧的 retro/modern 是给老布局调的），
   之后用户自己选什么就记住什么。 */
if (!localStorage.getItem('ttplayer-skin-v12')) {
  localStorage.setItem('ttplayer-skin-v12', '1');
  localStorage.setItem('ttplayer-skin', 'classic');
  state.skin = 'classic';
}

/* 可选皮肤（新增皮肤只需在这里加一项 + 在 styles.css 里补变量） */
const SKINS = [
  { id: 'classic', name: '经典紫罗兰' },
  { id: 'retro', name: '经典钢蓝' },
  { id: 'modern', name: '现代深色' },
];

/* 播放模式：0 顺序 / 1 列表循环 / 2 随机 / 3 单曲循环
   索引顺序被播放逻辑直接依赖（0 = 顺序到头就停，2 = 随机取轨），
   新加的模式只能往后排，不能插在中间 */
const MODES = ['顺序', '循环', '随机', '单曲'];
const MODE_TIP = ['顺序播放（放完最后一首就停）', '列表循环', '随机播放', '单曲循环']; 

const EQ_FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
const EQ_PRESETS = {
  flat:    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  pop:     [-1, 2, 4, 5, 3, 0, -1, -1, 0, 1],
  rock:    [5, 4, 2, -1, -2, -1, 2, 4, 5, 5],
  classic: [4, 3, 2, 1, -1, -1, 0, 2, 3, 4],
  jazz:    [3, 2, 1, 2, -1, -1, 0, 1, 2, 3],
  vocal:   [-3, -2, -1, 2, 5, 5, 4, 2, 0, -1],
};

function fmt(sec) {
  if (!isFinite(sec) || sec < 0) return '00:00';
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
function baseName(name) {
  return name.replace(/\.[^.]+$/, '');
}

/* 列表里显示的名字：有标签就用「歌手 - 歌名」，没有就退回文件名去掉扩展名 */
function trackLabel(t) {
  if (!t) return '';
  const title = t.title || baseName(t.name || '');
  return t.artist ? `${t.artist} - ${title}` : title;
}

/* 轻量提示条：导出结果这类一次性反馈，不弹系统对话框 */
let toastTimer = 0;
function notify(msg, ms = 3000) {
  let el = $('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('on'), ms);
}

/* ================= 加歌进度 =================
   加一个上百首的目录时，「走目录」和「读标签」两段都要几十秒。
   原来这两段一句话都不说，界面看着就是死了。
   现在统一走 tt:scan 事件：Rust 走目录时发 walk，主窗读标签时发 meta，
   两个窗口各自把进度画在自己身上（主窗状态行 / 播放列表进度条）。 */
const busy = { on: false, phase: '', done: 0, total: 0, hide: 0, hold: 0 };

/* 主窗第二行：平时是「状态 + 编码 + 采样率 + 码率」，加歌时让给进度 */
function paintStatusLine() {
  const st = $('stat-state');
  const meta = $('stat-meta');
  if (!st && !meta) return;
  if (busy.on) {
    if (st) st.textContent = '状态: ' + (busy.phase === 'meta' ? '读标签' : '扫描中');
    if (meta) {
      meta.textContent = busy.total
        ? `${busy.done} / ${busy.total}`
        : (busy.done ? `已找到 ${busy.done} 首` : '正在读取目录…');
    }
    return;
  }
  const t = state.playlist[state.curr];
  if (st) st.textContent = '状态: ' + (state.playing ? '播放' : (state.position > 0 ? '暂停' : '停止'));
  if (!meta) return;
  const parts = [];
  if (t) {
    // 编码取 symphonia 从容器里探出来的真值；读不到才退回扩展名
    const ext = /\.([^.]+)$/.exec(t.name);
    const fmt = t.codec || (ext ? ext[1].toUpperCase() : '');
    if (fmt) parts.push(fmt);
    // 文件自己的采样率优先，还没读到才用音频上下文的
    const sr = t.sampleRate || (audioCtx && audioCtx.sampleRate);
    if (sr) parts.push((sr / 1000).toFixed(1).replace(/\.0$/, '') + 'kHz');
    // 无损 / PCM 有位深，压缩格式看码率
    if (t.bits) {
      parts.push(t.bits + 'bit');
    } else if (t.bitrate) {
      parts.push(t.bitrate >= 1000
        ? (t.bitrate / 1000).toFixed(1) + 'Mbps'
        : Math.round(t.bitrate) + 'kbps');
    }
  }
  meta.textContent = parts.join('  ·  ');
}

function paintBusy() {
  paintStatusLine();
  const pct = busy.total ? Math.max(2, Math.min(100, Math.round(busy.done / busy.total * 100))) : 0;

  /* 播放列表窗口：菜单栏下面那条 */
  const bar = $('scanbar');
  if (bar) {
    bar.hidden = !busy.on;
    bar.classList.toggle('ind', busy.on && !busy.total);
    const fill = $('sb-fill');
    if (fill) fill.style.width = (busy.on && busy.total) ? pct + '%' : '0%';
    const tx = $('sb-text');
    if (tx) tx.textContent = busy.phase === 'meta' ? '读取标签' : '正在扫描';
    const nm = $('sb-num');
    if (nm) {
      nm.textContent = busy.total ? `${busy.done} / ${busy.total}`
        : (busy.done ? `已找到 ${busy.done}` : '');
    }
  }

  /* 主窗口：状态行下沿那条细线 */
  const line = document.querySelector('.stline');
  const sp = $('st-prog');
  if (line) {
    line.classList.toggle('busy', busy.on);
    line.classList.toggle('ind', busy.on && !busy.total);
  }
  if (sp) sp.style.width = (busy.on && busy.total) ? pct + '%' : '0%';
}

function scanStart(text) {
  clearTimeout(busy.hide);
  clearTimeout(busy.hold);
  busy.hold = 0;
  busy.on = true;
  busy.phase = text || 'walk';
  busy.done = 0;
  busy.total = 0;
  paintBusy();
}

function scanTick(done, total) {
  busy.on = true;
  busy.done = done | 0;
  busy.total = total | 0;
  paintBusy();
}

/* 结束：先填满再淡出，避免「啪一下没了」看着像失败 */
function scanEnd() {
  if (!busy.on) return;
  clearTimeout(busy.hold);
  busy.hold = 0;
  if (busy.total) busy.done = busy.total;
  paintBusy();
  clearTimeout(busy.hide);
  busy.hide = setTimeout(() => {
    busy.on = false;
    paintBusy();
  }, 420);
}

/* 收到任何窗口 / Rust 发来的进度 */
function applyScan(p) {
  if (!p || p.phase === 'done') { scanEnd(); return; }
  busy.hold = 0;
  busy.on = true;
  busy.phase = p.phase || 'walk';
  busy.total = p.total | 0;
  busy.done = (p.total ? p.done : p.found) | 0;
  // 走目录这一段是 Rust 在发，万一它中途挂了没人收尾，
  // 超过 2.5 秒没有新事件就自己收起来，别留一条永远停在那的进度条
  if (busy.phase === 'walk') {
    busy.hold = setTimeout(() => {
      if (busy.phase === 'walk') { busy.on = false; paintBusy(); }
    }, 2500);
  }
  paintBusy();
}
try { listen('tt:scan', (e) => applyScan(e.payload)); } catch (e) { /* ignore */ }

/* 主窗读标签时把进度也广播出去，播放列表窗口才同步得到 */
function emitScan(p) { try { emit('tt:scan', p); } catch (e) { /* ignore */ } }

/* 收尾要走两个窗口：本窗直接关，另一头靠事件通知 */
function scanDone() {
  scanEnd();
  emitScan({ phase: 'done' });
}

/* ================= 命令 / 状态总线 ================= */
function cmd(t, payload = {}) { emit('tt:cmd', { t, ...payload }); }

/* ---------- 右键菜单 ----------
   全局关掉浏览器自带的右键菜单（保存图片 / 刷新那种），
   需要的右键功能自己画。 */
let ctxEl = null;
function closeCtx() {
  if (ctxEl) { ctxEl.remove(); ctxEl = null; }
}
function openCtx(x, y, items) {
  closeCtx();
  const m = document.createElement('div');
  m.className = 'openmenu ctx';
  items.forEach((it) => {
    const b = document.createElement('button');
    b.className = 'om-item' + (it.danger ? ' danger' : '');
    b.textContent = it.label;
    b.addEventListener('mousedown', (e) => e.stopPropagation());
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      closeCtx();
      try { it.act(); } catch (err) { /* ignore */ }
    });
    m.appendChild(b);
  });
  // 必须挂进 #app：皮肤的颜色变量都定义在 #app 上，
  // 挂到 body 上 var(--shell-1) 全是空值，菜单会变成一块透明的字
  appEl.appendChild(m);
  // 贴着鼠标，但别伸到窗口外面去
  const r = m.getBoundingClientRect();
  const px = Math.max(2, Math.min(x, window.innerWidth - r.width - 2));
  const py = Math.max(2, Math.min(y, window.innerHeight - r.height - 2));
  m.style.left = px + 'px';
  m.style.top = py + 'px';
  ctxEl = m;
}
document.addEventListener('contextmenu', (e) => e.preventDefault());
document.addEventListener('mousedown', (e) => {
  if (ctxEl && !e.target.closest('.openmenu.ctx')) closeCtx();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && ctxEl) { closeCtx(); e.stopPropagation(); }
}, true);
window.addEventListener('blur', closeCtx);


let lastBroadcast = 0;
let lastPlRev = -1;
function broadcast(force = false) {
  const now = performance.now();
  if (!force && now - lastBroadcast < 180) return;
  lastBroadcast = now;
  // 歌单很大时每帧都发会很浪费，只在版本变化（或强制同步）时带上
  const sendList = force || state.plRev !== lastPlRev;
  if (sendList) lastPlRev = state.plRev;
  emit('tt:state', {
    ...(sendList ? {
      playlist: state.playlist,
      plRev: state.plRev,
      lists: state.lists.map((l) => ({ name: l.name, n: l.tracks.length })),
      active: state.active,
    } : {}),
    curr: state.curr,
    playing: state.playing,
    position: state.position,
    duration: state.duration,
    volume: state.volume,
    muted: state.muted,
    mode: state.mode,
    skin: state.skin,
    eq: state.eq.slice(),
    eqOn: state.eqOn,
  });
  paintMainUI();
  // 歌单 / 当前曲目一变就记一份，下次启动恢复
  if (IS_MAIN) saveSessionSoon();
}

/* ================= 皮肤 ================= */
function paintSkinMenu() {
  const menu = $('skin-menu');
  if (!menu) return;
  menu.innerHTML = SKINS.map((s) =>
    `<button class="om-item${s.id === state.skin ? ' on' : ''}" data-skin="${s.id}">${s.name}</button>`
  ).join('');
}

function applySkin(v) {
  state.skin = v;
  appEl.dataset.skin = v;
  document.body.dataset.skin = v;
  localStorage.setItem('ttplayer-skin', v);
  paintSkinMenu();
}
applySkin(state.skin);

/* ================= 窗口尺寸 / 位置 ================= */
/* 每个窗口都能单独拉大拉小，内容自适应填充（窗口变高就看到更多行）。
   尺寸与位置都由 Rust 侧真实设置窗口，并记进配置目录的 window-layout.txt，
   下次启动照着还原（前端不再参与存储）。 */
function persistLayout() {
  invoke('save_layout').catch(() => {});
}

/* ================= 播放列表记忆 ================= */
/* 上次加进来的文件 / 文件夹整张歌单存到配置目录的 session.json，
   下次启动原样恢复（不自动播放，只装载并选中上次那一首）。 */
let sessionReady = false;   // 还没读完就落盘会把旧歌单冲掉
let sessionTimer = 0;
let savedRev = -1, savedCurr = -2;

function snapshotSession() {
  return JSON.stringify({
    v: 3,
    active: state.active,
    osc: state.osc,
    lists: state.lists.map((l, i) => ({
      name: l.name,
      // 非活动列表的「当前曲目」是它自己的记忆，活动列表用实时值
      curr: i === state.active ? state.curr : l.curr,
      tracks: l.tracks.map((t) => ({
        name: t.name, path: t.path, duration: t.duration || null,
        // 标签一并存下：下次启动直接显示「歌手 - 歌名」，不用再把 500 首读一遍
        title: t.title || null,
        artist: t.artist || null,
        album: t.album || null,
        codec: t.codec || null,
        sampleRate: t.sampleRate || null,
        bits: t.bits || null,
        bitrate: t.bitrate || null,
        tagged: !!t.tagged,
      })),
    })),
  });
}

function saveSessionNow() {
  if (!sessionReady) return;
  clearTimeout(sessionTimer);
  sessionTimer = 0;
  invoke('save_session', { data: snapshotSession() }).catch(() => {});
}

/** 歌单或当前曲目变了才存，250ms 防抖（拖一堆文件夹进来只写一次） */
function saveSessionSoon() {
  if (!sessionReady) return;
  if (state.plRev === savedRev && state.curr === savedCurr) return;
  savedRev = state.plRev;
  savedCurr = state.curr;
  clearTimeout(sessionTimer);
  sessionTimer = setTimeout(saveSessionNow, 250);
}

/* ===========================================================
   主窗口：音频引擎 + 播放列表
   =========================================================== */
let audio = null;
let audioCtx = null, analyser = null, eqFilters = [];
let fadeGain = null;          // 淡入淡出用的总增益，串在输出前
let pauseTimer = 0;
let meterRaf = null;
let dragSeek = false, dragVol = false;

if (IS_MAIN) {
  audio = new Audio();
  audio.preload = 'metadata';
  // asset:// 与页面不同源，必须走 CORS 模式，否则 createMediaElementSource
  // 拿到的是「被污染」的媒体流，Web Audio 会强制输出静音（表现为能播但没声音）
  audio.crossOrigin = 'anonymous';
  audio.volume = state.volume;

  /* ---------- 音频图 ---------- */
  function ensureAudioGraph() {
    if (audioCtx) {
      if (audioCtx.state !== 'running') audioCtx.resume().catch(() => {});
      return;
    }
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const srcNode = audioCtx.createMediaElementSource(audio);
      eqFilters = EQ_FREQS.map((f) => {
        const bq = audioCtx.createBiquadFilter();
        bq.type = 'peaking';
        bq.frequency.value = f;
        bq.Q.value = 1.1;
        bq.gain.value = 0;
        return bq;
      });
      analyser = audioCtx.createAnalyser();
      // 时域波形要够密的采样点才画得出形状；频谱那边按 frequencyBinCount
      // 等分 16 段，分辨率变高不影响语义
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.75;

      fadeGain = audioCtx.createGain();
      fadeGain.gain.value = 1;

      let node = srcNode;
      eqFilters.forEach((f) => { node.connect(f); node = f; });
      node.connect(analyser);
      analyser.connect(fadeGain);
      fadeGain.connect(audioCtx.destination);

      // 诊断钩子：能在运行时给任意节点再挂一个分析器，量各段电平
      window.__audioGraph = { ctx: audioCtx, src: srcNode, eq: eqFilters,
                              analyser, fade: fadeGain, audio };

      // 首次创建常是 suspended（自动播放策略），不 resume 就彻底没声
      if (audioCtx.state !== 'running') audioCtx.resume().catch(() => {});
      audioCtx.addEventListener('statechange', () => {
        if (audioCtx.state !== 'running') audioCtx.resume().catch(() => {});
      });
    } catch (err) {
      console.warn('音频图初始化失败', err);
    }
  }

  function applyEQ() {
    if (!eqFilters.length) return;
    // ON 关闭 = 旁路：增益全部归零，但保留各频段的值，重新打开即恢复
    eqFilters.forEach((f, i) => {
      f.gain.value = state.eqOn ? (state.eq[i] || 0) : 0;
    });
  }

  /* 诊断钩子：淡入淡出是听感型的改动，自动化测试只能靠这个读总增益 */
  window.__dbgAudio = () => ({
    fade: fadeGain ? Number(fadeGain.gain.value.toFixed(3)) : -1,
    ctx: audioCtx ? audioCtx.state : 'none',
    playing: state.playing,
    paused: audio ? audio.paused : true,
    pos: audio ? Number(audio.currentTime.toFixed(2)) : -1,
  });

  /* ---------- 淡入淡出 ----------
     直接切 src / pause 会有咔哒声（波形被硬截断）。用一条总增益做
     几百毫秒的斜坡，听感上就是「轻轻起、轻轻落」。 */
  function fadeTo(target, ms) {
    if (!fadeGain || !audioCtx) return;
    const now = audioCtx.currentTime;
    const g = fadeGain.gain;
    g.cancelScheduledValues(now);
    if (ms <= 0) {
      g.setValueAtTime(target, now);
      return;
    }
    g.setValueAtTime(g.value, now);
    g.linearRampToValueAtTime(target, now + ms / 1000);
  }

  function playFaded() {
    clearTimeout(pauseTimer);
    pauseTimer = 0;
    fadeTo(0, 0);            // 从静音起步，避免上一首的残余波形漏出来
    state.playing = true;
    audio.play().then(() => {
      fadeTo(1, 180);
      broadcast(true);
    }).catch(() => {
      state.playing = false;
      broadcast(true);
    });
  }

  function pauseFaded() {
    if (audio.paused) { state.playing = false; stopOsc(); broadcast(true); return; }
    clearTimeout(pauseTimer);
    fadeTo(0, 100);
    state.playing = false;
    // 淡出还有 110ms 才真正 pause，事件不一定来（本来就 paused 时不会触发），
    // 这里直接停，画面立刻停住，也不留一个 rAF 在后台空转
    stopOsc();
    broadcast(true);
    // 等斜坡走完再真停，否则淡出被截断
    pauseTimer = setTimeout(() => { audio.pause(); }, 110);
  }

  /* ---------- 频谱广播 ---------- */
  function startMeter() {
    if (!analyser || meterRaf) return;
    const data = new Uint8Array(analyser.frequencyBinCount);
    let last = 0;
    const tick = (ts) => {
      analyser.getByteFrequencyData(data);
      if (!last || ts - last > 90) {
        last = ts;
        const out = [];
        const step = Math.max(1, Math.floor(data.length / 16));
        for (let i = 0; i < 16; i++) {
          let sum = 0;
          for (let k = 0; k < step; k++) sum += data[i * step + k] || 0;
          out.push(Math.round(sum / step / 255 * 100));
        }
        emit('tt:meter', out);
      }
      meterRaf = requestAnimationFrame(tick);
    };
    meterRaf = requestAnimationFrame(tick);
  }

  /* ---------- 可视化：频谱 / 示波波形 / 铺满背景 ----------
     spec（默认）和 wave 都画在左屏那块 canvas 上，bg 画在铺满整窗的底图 canvas。 */
  let oscRaf = null;
  let oscBuf = null;      // 时域
  let specBuf = null;     // 频域
  let specSmooth = null;

  function oscCanvas() {
    if (state.osc === 'bg') return $('osc-bg');
    if (state.osc === 'spec' || state.osc === 'wave') return $('spec-cv');
    return null;
  }

  /* canvas 的 CSS 尺寸跟窗口一起变，每帧对齐一次 */
  function fitOsc(cv) {
    const r = cv.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return null;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.round(r.width * dpr);
    const h = Math.round(r.height * dpr);
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    return { ctx: cv.getContext('2d'), w, h, dpr };
  }

  /* 示波器的垂直档位。真实音乐在 46ms 窗口里的瞬时幅度常在 5%~50% 之间，
     1:1 画就是一条细线，所以放大；用 tanh 做软膝而不是硬削顶，副歌不会变成方波。 */
  function oscAmp(v, gain) {
    return Math.tanh(((v - 128) / 128) * gain);
  }

  /* 波形：屏是 165×137，2048 个采样点挤进去太密，抽稀到每像素约 1.6 个点 */
  function drawOscStrip(ctx, w, h, dpr, buf) {
    const mid = h / 2;
    const span = Math.floor(buf.length * 0.4);   // 只看 18ms 左右，波形才拉得开
    const step = Math.max(1, Math.floor(span / Math.max(64, (w / dpr) * 1.1)));
    ctx.save();
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    for (let pass = 0; pass < 2; pass++) {
      ctx.beginPath();
      let first = true;
      for (let i = 0; i < span; i += step) {
        const x = (i / (span - 1)) * w;
        const y = mid - oscAmp(buf[i], 3.0) * (h * 0.40);
        if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = pass === 0 ? 'rgba(60,255,140,0.24)' : 'rgba(205,255,228,0.92)';
      ctx.lineWidth = (pass === 0 ? 5.0 : 1.3) * dpr;
      ctx.shadowColor = 'rgba(70,255,150,0.9)';
      ctx.shadowBlur = (pass === 0 ? 11 : 4) * dpr;
      ctx.stroke();
    }
    ctx.restore();
  }

  /* 铺满态：三条青蓝曲线压在下半区，避开歌名与时间行 */
  function drawOscBg(ctx, w, h, dpr, buf) {
    const n = buf.length;
    const LAYERS = [
      [0.52, 0.060, 0.16, 0.00],
      [0.71, 0.115, 0.30, 0.23],
      [0.89, 0.135, 0.42, 0.41],
    ];
    const step = Math.max(1, Math.floor(n / Math.max(80, (w / dpr) * 0.55)));
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.lineJoin = 'round';
    LAYERS.forEach(([yr, ar, alpha, ph], k) => {
      ctx.beginPath();
      let first = true;
      for (let i = 0; i < n; i += step) {
        const x = (i / (n - 1)) * w;
        const j = Math.floor((((i / n) + ph) % 1) * n);
        const y = h * yr - oscAmp(buf[j], 2.0) * (h * ar);
        if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = `rgba(150,215,245,${alpha})`;
      ctx.lineWidth = (k === 2 ? 1.6 : 1.1) * dpr;
      ctx.shadowColor = 'rgba(120,205,240,0.9)';
      ctx.shadowBlur = (k === 2 ? 11 : 6) * dpr;
      ctx.stroke();
    });
    ctx.restore();
  }

  /* 频谱：必须按对数切频段。
     getByteFrequencyData 拿到的 1024 个 bin 是线性均分的，低频（0~100Hz）
     只占前 5 个 bin，直接等分会把全部能量堆在左边第一根柱上，右边一片空。 */
  const SPEC_F_MIN = 40;
  const SPEC_F_MAX = 15000;

  function specValues(n) {
    if (!analyser) return null;
    const bins = analyser.frequencyBinCount;
    if (!specBuf || specBuf.length !== bins) specBuf = new Uint8Array(bins);
    analyser.getByteFrequencyData(specBuf);
    if (!specSmooth || specSmooth.length !== n) specSmooth = new Float32Array(n);

    const nyq = (audioCtx && audioCtx.sampleRate ? audioCtx.sampleRate : 44100) / 2;
    const ratio = SPEC_F_MAX / SPEC_F_MIN;
    const out = new Array(n);
    for (let i = 0; i < n; i++) {
      const f0 = SPEC_F_MIN * Math.pow(ratio, i / n);
      const f1 = SPEC_F_MIN * Math.pow(ratio, (i + 1) / n);
      let b0 = Math.floor(f0 / nyq * bins);
      let b1 = Math.ceil(f1 / nyq * bins);
      b0 = Math.max(0, Math.min(bins - 1, b0));
      b1 = Math.max(b0 + 1, Math.min(bins, b1));
      let sum = 0;
      for (let k = b0; k < b1; k++) sum += specBuf[k];
      let v = sum / (b1 - b0) / 255;
      // 高频能量天然低，抬一点点让起伏可见（抬太多中间会鼓成包）
      v = Math.min(1, v * (1 + 0.34 * (i / n)));
      // attack 立刻跟上，release 缓慢下落（跟真实电平表一致）
      if (v > specSmooth[i]) specSmooth[i] = v;
      else specSmooth[i] += (v - specSmooth[i]) * 0.20;
      out[i] = specSmooth[i];
    }
    return out;
  }

  function drawSpec(ctx, w, h, dpr, vals) {
    const n = vals.length;
    const gap = Math.max(1, Math.round(dpr));
    const bw = Math.max(1, (w - gap * (n - 1)) / n);
    const mirrorH = h * 0.15;          // 底部留给倒影
    const baseY = h - mirrorH;
    const maxH = baseY - 3 * dpr;

    /* 不做「已播亮 / 未播暗」分区：进度刚走到 2% 时整屏会被压成一片黑，
       频谱看着像不动了。进度交给右边那条进度条，谱面保持全亮。 */
    for (let i = 0; i < n; i++) {
      const x = i * (bw + gap);
      const bh = Math.max(1.2 * dpr, vals[i] * maxH);

      const g = ctx.createLinearGradient(0, baseY, 0, baseY - bh);
      g.addColorStop(0, 'rgba(30,68,185,0.95)');
      g.addColorStop(0.34, 'rgba(52,116,242,0.97)');
      g.addColorStop(0.74, 'rgba(102,182,255,0.98)');
      g.addColorStop(1, 'rgba(164,226,255,1)');
      ctx.fillStyle = g;
      ctx.fillRect(x, baseY - bh, bw, bh);

      // 柱顶 1px 亮边
      ctx.fillStyle = 'rgba(200,240,255,0.8)';
      ctx.fillRect(x, baseY - bh, bw, Math.max(1, dpr));

      // 倒影：底部那截的镜像，淡一点
      if (mirrorH > 2) {
        const gh = ctx.createLinearGradient(0, baseY, 0, baseY + bh * 0.55);
        gh.addColorStop(0, 'rgba(70,140,255,0.26)');
        gh.addColorStop(1, 'rgba(30,55,130,0)');
        ctx.fillStyle = gh;
        ctx.fillRect(x, baseY, bw, Math.min(mirrorH, bh * 0.55));
      }
    }

    const prog = state.duration > 0 ? Math.min(1, state.position / state.duration) : null;

    // 播放头：一根很淡的青线 + 顶端小亮点，别抢频谱的戏
    if (prog != null) {
      const px = Math.round(w * prog);
      const gl = ctx.createLinearGradient(px - 5 * dpr, 0, px + 5 * dpr, 0);
      gl.addColorStop(0, 'rgba(120,190,255,0)');
      gl.addColorStop(0.5, 'rgba(120,190,255,0.16)');
      gl.addColorStop(1, 'rgba(120,190,255,0)');
      ctx.fillStyle = gl;
      ctx.fillRect(px - 5 * dpr, 0, 10 * dpr, baseY);
      const lg = ctx.createLinearGradient(0, 0, 0, baseY);
      lg.addColorStop(0, 'rgba(150,215,255,0.12)');
      lg.addColorStop(0.35, 'rgba(175,232,255,0.66)');
      lg.addColorStop(1, 'rgba(150,212,255,0.78)');
      ctx.fillStyle = lg;
      ctx.fillRect(px, 0, Math.max(1, dpr), baseY);
      ctx.beginPath();
      ctx.arc(px + Math.max(1, dpr) / 2, 4 * dpr, 1.5 * dpr, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(220,245,255,0.85)';
      ctx.fill();
    }

    // 扫描线：压一层很淡的横向条纹，像 CRT
    ctx.save();
    ctx.globalAlpha = 0.10;
    ctx.fillStyle = '#06030f';
    for (let y = 0; y < h; y += 3 * dpr) ctx.fillRect(0, y, w, Math.max(1, dpr));
    ctx.restore();
  }

  function specBarCount(w, dpr) {
    return Math.max(12, Math.min(48, Math.round(w / dpr / 4.6)));
  }

  function oscLoop() {
    oscRaf = null;
    if (!state.playing) { paintOscIdle(); return; }
    const cv = oscCanvas();
    if (!cv || !analyser) return;
    const f = fitOsc(cv);
    if (!f) return;

    f.ctx.clearRect(0, 0, f.w, f.h);

    if (state.osc === 'spec') {
      const vals = specValues(specBarCount(f.w, f.dpr));
      if (vals) drawSpec(f.ctx, f.w, f.h, f.dpr, vals);
    } else {
      if (!oscBuf || oscBuf.length !== analyser.fftSize) {
        oscBuf = new Uint8Array(analyser.fftSize);
      }
      analyser.getByteTimeDomainData(oscBuf);
      if (state.osc === 'wave') drawOscStrip(f.ctx, f.w, f.h, f.dpr, oscBuf);
      else drawOscBg(f.ctx, f.w, f.h, f.dpr, oscBuf);
    }
    oscRaf = requestAnimationFrame(oscLoop);
  }

  function startOsc() {
    if (oscRaf || !oscCanvas() || !analyser) return;
    oscRaf = requestAnimationFrame(oscLoop);
  }

  /* 暂停 / 关掉时停掉 rAF（不跑动画就不吃 CPU），留一帧静止画面 */
  function stopOsc() {
    if (oscRaf) { cancelAnimationFrame(oscRaf); oscRaf = null; }
    paintOscIdle();
  }

  function paintOscIdle() {
    const cv = oscCanvas();
    if (!cv) return;
    const f = fitOsc(cv);
    if (!f) return;
    f.ctx.clearRect(0, 0, f.w, f.h);
    if (state.osc === 'bg') return;

    if (state.osc === 'spec') {
      // 没信号时留一排贴地的矮柱，看得出"屏在那儿但没动静"
      const n = specBarCount(f.w, f.dpr);
      const gap = Math.max(1, Math.round(f.dpr));
      const bw = Math.max(1, (f.w - gap * (n - 1)) / n);
      const baseY = f.h - f.h * 0.15;
      for (let i = 0; i < n; i++) {
        f.ctx.fillStyle = 'rgba(56,96,180,0.34)';
        f.ctx.fillRect(i * (bw + gap), baseY - 2 * f.dpr, bw, 2 * f.dpr);
      }
      f.ctx.fillStyle = 'rgba(70,120,220,0.22)';
      f.ctx.fillRect(0, baseY, f.w, Math.max(1, f.dpr));
      return;
    }

    if (state.osc === 'wave') {
      f.ctx.save();
      f.ctx.strokeStyle = 'rgba(90,255,170,0.28)';
      f.ctx.lineWidth = 1 * f.dpr;
      f.ctx.shadowColor = 'rgba(70,255,150,0.5)';
      f.ctx.shadowBlur = 5 * f.dpr;
      f.ctx.beginPath();
      f.ctx.moveTo(0, f.h / 2);
      f.ctx.lineTo(f.w, f.h / 2);
      f.ctx.stroke();
      f.ctx.restore();
    }
  }

  /* 切形态。左屏那块 canvas 在 spec / wave 之间复用，高度不随形态变，
     所以这里不用再改窗口尺寸（v0.11 的条带要额外 26px，现在屏占满内容区）。 */
  const OSC_MODES = ['spec', 'wave', 'bg'];
  const OSC_TAG = { spec: 'SPECTRUM', wave: 'WAVEFORM', bg: '' };

  async function setOsc(mode, quiet) {
    const next = OSC_MODES.includes(mode) ? mode : 'spec';
    if (next === state.osc) return;

    state.osc = next;
    appEl.dataset.osc = next;
    const tag = $('spec-tag');
    if (tag) tag.textContent = OSC_TAG[next] || '';

    if (state.playing) { stopOsc(); startOsc(); } else paintOscIdle();
    if (!quiet) saveSessionNow();
  }

  /* 点一下左屏按 spec → wave → bg 轮换；铺满态点任意空白回到频谱 */
  function cycleOsc() {
    const i = OSC_MODES.indexOf(state.osc);
    setOsc(OSC_MODES[(i + 1) % OSC_MODES.length]);
  }

  function applyOscUI() {
    appEl.dataset.osc = state.osc;
    const tag = $('spec-tag');
    if (tag) tag.textContent = OSC_TAG[state.osc] || '';
    if (state.playing) startOsc(); else paintOscIdle();
    // 启动瞬间 canvas 还没完成布局（宽高为 0），等一帧再画一次
    setTimeout(() => { if (!oscRaf) paintOscIdle(); }, 120);
    setTimeout(() => { if (!oscRaf) paintOscIdle(); }, 600);
  }

  $('screen')?.addEventListener('click', cycleOsc);
  $('osc-bg')?.addEventListener('click', () => setOsc('spec'));

  /* 测试钩子：oscRaf 是块级变量，Runtime.evaluate 在全局取不到 */
  window.__osc = () => {
    let mn = null, mx = null;
    if (analyser) {
      const b = new Uint8Array(analyser.fftSize);
      analyser.getByteTimeDomainData(b);
      mn = 255; mx = 0;
      for (let i = 0; i < b.length; i++) { if (b[i] < mn) mn = b[i]; if (b[i] > mx) mx = b[i]; }
    }
    return { raf: !!oscRaf, mode: state.osc, hasAnalyser: !!analyser,
      playing: state.playing, cur: audio ? (audio.currentTime || 0) : 0,
      bufMin: mn, bufMax: mx };
  };

  /* 手动拉窗口后 canvas 像素要重算，不然静止时会被拉伸模糊 */
  window.addEventListener('resize', () => { if (!oscRaf) paintOscIdle(); });

  /* ---------- 播放列表 ---------- */
  function addPaths(paths) {
    const ok = (paths || []).filter((p) => AUDIO_RE.test(p));
    if (!ok.length) return 0;
    const had = new Set(state.playlist.map((t) => t.path));
    const fresh = [];
    ok.forEach((p) => {
      if (had.has(p)) return;
      had.add(p);
      const name = p.split(/[\\/]/).pop();
      const t = {
        name, path: p, src: convertFileSrc(p),
        duration: null, title: null, artist: null, album: null,
      };
      state.playlist.push(t);
      fresh.push(t);
    });
    if (!fresh.length) return 0;
    state.plRev++;
    if (state.curr === -1) state.curr = 0;
    broadcast(true);
    // 文件名先顶上，标签一到就换成「歌手 - 歌名」
    readMetaFor(fresh);
    return fresh.length;
  }

  /* 加一批路径：文件直接进列表，目录先扫描（扫描和读标签都报进度） */
  async function ingest(paths) {
    if (!paths || !paths.length) return;
    const files = [], dirs = [];
    paths.forEach((p) => (AUDIO_RE.test(p) ? files : dirs).push(p));
    if (files.length) addPaths(files);
    if (!dirs.length) return;

    scanStart('walk');
    let found = [];
    try {
      for (let i = 0; i < dirs.length; i++) {
        const got = await invoke('scan_audio_dir_progress', { path: dirs[i] });
        if (got && got.length) found = found.concat(got);
      }
    } catch (e) {
      console.warn('扫描目录失败', e);
    }
    if (!found.length) {
      scanDone();
      notify('没有找到支持的音频文件');
      return;
    }
    if (!addPaths(found)) {
      // 全是已经在列表里的，不会有读标签那一段，进度自己收尾
      scanDone();
      notify('这些歌都已经在列表里了');
    }
  }

  /* ---------- 标签 / 时长补全 ----------
     全部在 Rust 里读（symphonia），前端只管结果。分批是为了
     一次丢 500 首也不会让单条 IPC 消息过大。 */
  function applyMeta(t, m) {
    if (!t || !m) return;
    if (m.title) t.title = m.title;
    if (m.artist) t.artist = m.artist;
    if (m.album) t.album = m.album;
    if (typeof m.duration === 'number' && m.duration > 0 && !t.duration) {
      t.duration = m.duration;
    }
    if (m.sampleRate) t.sampleRate = m.sampleRate;
    if (m.codec) t.codec = m.codec;
    if (m.bits) t.bits = m.bits;
    if (m.bitrate) t.bitrate = m.bitrate;
  }

  async function readMetaFor(items) {
    if (!items.length) return;
    const total = items.length;
    // 一两首不到 100ms 就读完了，弹进度反而闪；上量了才报
    const show = total >= 8;
    let done = 0;
    if (show) { scanStart('meta'); emitScan({ phase: 'meta', done: 0, total }); }
    try {
      for (let i = 0; i < items.length; i += 100) {
        const chunk = items.slice(i, i + 100);
        let metas = [];
        try {
          metas = await invoke('read_audio_meta', { paths: chunk.map((t) => t.path) });
        } catch (e) { console.warn('读取标签失败', e); }
        chunk.forEach((t, k) => {
          applyMeta(t, metas && metas[k]);
          // 无论读没读到都算「已读过」：没标签的歌不该每次启动都白读一遍
          t.tagged = true;
        });
        done += chunk.length;
        if (show) { scanTick(done, total); emitScan({ phase: 'meta', done, total }); }
      }
    } finally {
      if (show) scanDone();
    }
    state.plRev++;
    broadcast(true);
  }

  async function pickFiles() {
    try {
      const paths = await invoke('pick_audio_files');
      if (paths && paths.length) ingest(paths);
    } catch (e) { console.warn('选择文件失败', e); }
  }

  async function pickFolder() {
    try {
      // 只拿路径，扫描交给 ingest（扫描要报进度，不能在 Rust 里一把闷完）
      const dirs = await invoke('pick_music_dir');
      if (dirs && dirs.length) ingest(dirs);
    } catch (e) { console.warn('选择文件夹失败', e); }
  }

  function renderInfo() {
    const t = state.playlist[state.curr];
    const el = $('info-title');
    if (el) el.textContent = t ? (t.title || baseName(t.name)) : '宁宁静听';
    const idx = $('info-idx');
    if (idx) idx.textContent = t ? String(state.curr + 1).padStart(2, '0') : '';
    const tc = $('time-total');
    if (tc) tc.textContent = state.duration ? fmt(state.duration) : '00:00';
  }

  function loadCurrent(autoplay = true) {
    const t = state.playlist[state.curr];
    if (!t) return;
    // 音频图要在首次加载前建好：元素在播放中被 createMediaElementSource 接管容易丢音
    ensureAudioGraph();
    fadeTo(0, 0);            // 换源前先静音，硬切波形会有咔哒声
    audio.src = t.src;
    state.duration = t.duration || 0;
    state.position = 0;
    renderInfo();
    if (autoplay) playFaded();
    broadcast(true);
  }

  function playAt(i) {
    if (i < 0 || i >= state.playlist.length) return;
    state.curr = i;
    loadCurrent(true);
  }

  function togglePlay() {
    if (!state.playlist.length) { pickFiles(); return; }
    ensureAudioGraph();  // 点击是用户手势，在这里建/恢复上下文最可靠
    if (state.playing) { pauseFaded(); return; }
    if (!audio.src) { loadCurrent(true); return; }
    playFaded();
  }

  function next() {
    if (!state.playlist.length) return;
    state.curr = state.mode === 2
      ? Math.floor(Math.random() * state.playlist.length)
      : (state.curr + 1) % state.playlist.length;
    loadCurrent(true);
  }

  /* 单曲循环用：不换源，回到 0 秒重新播（比 loadCurrent 快，也不会闪一下标题） */
  function replay() {
    if (!state.playlist.length) return;
    audio.currentTime = 0;
    state.position = 0;
    playFaded();
    broadcast(true);
  }

  function prev() {
    if (!state.playlist.length) return;
    if (audio.currentTime > 3) { audio.currentTime = 0; return; }
    state.curr = (state.curr - 1 + state.playlist.length) % state.playlist.length;
    loadCurrent(true);
  }

  function stop() {
    clearTimeout(pauseTimer);
    fadeTo(0, 120);
    state.playing = false;
    state.position = 0;
    stopOsc();
    broadcast(true);
    pauseTimer = setTimeout(() => {
      audio.pause();
      audio.currentTime = 0;
      broadcast(true);
    }, 130);
  }

  function removeAt(i) {
    if (i < 0 || i >= state.playlist.length) return;
    const wasPlaying = i === state.curr;
    state.playlist.splice(i, 1);
    if (wasPlaying) {
      audio.pause();
      audio.removeAttribute('src');
      state.curr = -1;
      state.playing = false;
      state.position = 0;
      state.duration = 0;
      renderInfo();
    } else if (i < state.curr) state.curr--;
    state.plRev++;
    broadcast(true);
  }

  function sortByName() {
    const playing = state.playlist[state.curr];
    state.playlist.sort((a, b) => a.name.localeCompare(b.name, 'zh'));
    state.curr = playing ? state.playlist.indexOf(playing) : -1;
    state.plRev++;
    broadcast(true);
  }

  function clearAll() {
    audio.pause();
    audio.removeAttribute('src');
    // 必须是原地清空：换新数组会让 lists[active].tracks 还指着旧数据，落盘就清不掉
    state.playlist.length = 0;
    state.plRev++;
    state.curr = -1;
    state.playing = false;
    state.position = 0;
    state.duration = 0;
    renderInfo();
    broadcast(true);
  }

  /* ---------- 播放列表分组（多列表） ---------- */
  function stashCurr() {
    const l = state.lists[state.active];
    if (l) l.curr = state.curr;
  }

  function switchList(i) {
    if (i === state.active || !state.lists[i]) return;
    stashCurr();
    const l = state.lists[i];
    state.active = i;
    state.playlist = l.tracks;
    state.curr = (l.curr >= 0 && l.curr < l.tracks.length)
      ? l.curr
      : (l.tracks.length ? 0 : -1);
    audio.pause();
    audio.removeAttribute('src');
    state.playing = false;
    state.position = 0;
    state.plRev++;
    if (state.playlist[state.curr]) {
      loadCurrent(false);   // 只装载，不自动播放
      audio.pause();
    } else {
      state.duration = 0;
      renderInfo();
      broadcast(true);
    }
    saveSessionNow();
  }

  function newList() {
    stashCurr();
    const l = { name: '列表 ' + (state.lists.length + 1), tracks: [], curr: -1 };
    state.lists.push(l);
    state.active = state.lists.length - 1;
    state.playlist = l.tracks;
    state.curr = -1;
    audio.pause();
    audio.removeAttribute('src');
    state.playing = false;
    state.position = 0;
    state.duration = 0;
    state.plRev++;
    renderInfo();
    broadcast(true);
    saveSessionNow();
  }

  function renameList(i, name) {
    const l = state.lists[i];
    const v = String(name || '').trim().slice(0, 40);
    if (!l || !v || v === l.name) return;
    l.name = v;
    state.plRev++;
    broadcast(true);
    saveSessionNow();
  }

  function deleteList(i) {
    if (state.lists.length <= 1 || !state.lists[i]) return;
    const wasActive = i === state.active;
    state.lists.splice(i, 1);
    if (i < state.active) state.active--;
    else if (wasActive) state.active = Math.min(state.active, state.lists.length - 1);
    const l = state.lists[state.active];
    state.playlist = l.tracks;
    state.curr = (l.curr >= 0 && l.curr < l.tracks.length)
      ? l.curr
      : (l.tracks.length ? 0 : -1);
    if (wasActive) {
      audio.pause();
      audio.removeAttribute('src');
      state.playing = false;
      state.position = 0;
    }
    state.plRev++;
    if (wasActive && state.playlist[state.curr]) {
      loadCurrent(false);
      audio.pause();
    } else {
      state.duration = state.playlist[state.curr]?.duration || 0;
      renderInfo();
      broadcast(true);
    }
    saveSessionNow();
  }

  function setVolume(v) {
    state.volume = Math.max(0, Math.min(1, v));
    if (state.volume > 0) state.muted = false;
    audio.volume = state.muted ? 0 : state.volume;
    broadcast(true);
  }

  function toggleMute() {
    state.muted = !state.muted;
    if (!state.muted && state.volume === 0) state.volume = 0.5;
    audio.volume = state.muted ? 0 : state.volume;
    broadcast(true);
  }

  function seekTo(ratio) {
    if (!audio.duration) return;
    audio.currentTime = Math.max(0, Math.min(1, ratio)) * audio.duration;
    state.position = audio.currentTime;
    broadcast(true);
  }

  /* ---------- 音频事件 ---------- */
  audio.addEventListener('loadedmetadata', () => {
    if (state.curr >= 0) {
      state.playlist[state.curr].duration = audio.duration;
    }
    state.duration = audio.duration || 0;
    renderInfo();
    ensureAudioGraph();
    applyEQ();
    broadcast(true);
    // 时长是这次才知道的，单独存一次（它不改变 plRev / curr，防抖逻辑不会触发）
    if (IS_MAIN) saveSessionNow();
  });
  audio.addEventListener('timeupdate', () => {
    state.position = audio.currentTime;
    broadcast();
  });
  audio.addEventListener('play', () => {
    state.playing = true;
    ensureAudioGraph();
    startMeter();
    startOsc();
    broadcast(true);
  });
  audio.addEventListener('pause', () => {
    state.playing = false;
    stopOsc();
    broadcast(true);
  });
  audio.addEventListener('ended', () => {
    // 单曲循环：原地重播这一首，不走 next()（next 会换歌）
    if (state.mode === 3) { replay(); return; }
    if (state.mode === 0 && state.curr === state.playlist.length - 1) { stop(); return; }
    next();
  });

  /* ---------- 主窗口 UI ---------- */
  const seekTrack = $('seek-track');
  const volTrack = $('vol-track');

  function paintProgress(ratio) {
    const p = Math.max(0, Math.min(1, ratio)) * 100;
    $('seek-fill').style.width = p + '%';
    $('seek-knob').style.left = p + '%';
  }

  const ratioFrom = (el, e) => {
    const r = el.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  };

  seekTrack.addEventListener('mousedown', (e) => {
    if (!audio.duration) return;
    dragSeek = true;
    snap.busy = true;
    const r = ratioFrom(seekTrack, e);
    paintProgress(r);
    audio.currentTime = r * audio.duration;
    e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragSeek || !audio.duration) return;
    const r = ratioFrom(seekTrack, e);
    paintProgress(r);
    audio.currentTime = r * audio.duration;
  });
  window.addEventListener('mouseup', () => {
    if (dragSeek) { dragSeek = false; snap.busy = false; broadcast(true); }
  });

  volTrack.addEventListener('mousedown', (e) => {
    dragVol = true;
    snap.busy = true;
    setVolume(ratioFrom(volTrack, e));
    e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragVol) return;
    setVolume(ratioFrom(volTrack, e));
  });
  window.addEventListener('mouseup', () => {
    if (dragVol) { dragVol = false; snap.busy = false; }
  });

  $('btn-play').addEventListener('click', togglePlay);
  $('btn-prev').addEventListener('click', prev);
  $('btn-next').addEventListener('click', next);
  $('btn-stop').addEventListener('click', stop);
  $('btn-mute').addEventListener('click', toggleMute);
  bindOpenMenu('btn-open', 'openmenu', (act) => {
    if (act === 'folder') pickFolder(); else pickFiles();
  });
  // 主窗口把皮肤入口收进了系统按钮组，这个循环切换按钮只在老布局里存在
  $('btn-skin')?.addEventListener('click', () => {
    const i = SKINS.findIndex((s) => s.id === state.skin);
    applySkin(SKINS[(i + 1) % SKINS.length].id);
    broadcast(true);
  });

  /* ---------- 皮肤下拉 ---------- */
  const skinBtn = $('btn-skin-menu');
  const skinMenu = $('skin-menu');
  if (skinBtn && skinMenu) {
    paintSkinMenu();
    skinBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      paintSkinMenu();
      skinMenu.hidden = !skinMenu.hidden;
    });
    skinMenu.addEventListener('click', (e) => {
      const it = e.target.closest('.om-item');
      if (!it) return;
      skinMenu.hidden = true;
      applySkin(it.dataset.skin);
      broadcast(true);
    });
    document.addEventListener('mousedown', (e) => {
      if (skinMenu.hidden) return;
      if (e.target.closest('#skin-menu') || e.target.closest('#btn-skin-menu')) return;
      skinMenu.hidden = true;
    });
  }

  $('btn-close').addEventListener('click', () => ME.close());
  $('btn-min').addEventListener('click', () => ME.minimize());

  // 标题栏 ⓘ → 弹出「关于」小窗（Rust 侧保证单例 + 居中于主体）
  $('btn-about')?.addEventListener('click', () => {
    invoke('open_about').catch((err) => console.warn('open_about', err));
  });

  let pinned = false;
  $('btn-pin').addEventListener('click', async () => {
    pinned = !pinned;
    $('btn-pin').classList.toggle('active', pinned);
    for (const w of await getAllWindows()) {
      try { await w.setAlwaysOnTop(pinned); } catch (err) { /* ignore */ }
    }
  });

  /* ---------- 命令路由 ---------- */
  listen('tt:cmd', (e) => {
    const p = e.payload || {};
    switch (p.t) {
      case 'toggle': togglePlay(); break;
      case 'stop': stop(); break;
      case 'next': next(); break;
      case 'prev': prev(); break;
      case 'seek': seekTo(p.ratio); break;
      case 'volume': setVolume(p.value); break;
      case 'mute': toggleMute(); break;
      case 'playAt': playAt(p.index); break;
      case 'remove': removeAt(p.index); break;
      case 'sort': sortByName(); break;
      case 'clear': clearAll(); break;
      case 'addPaths': addPaths(p.paths); break;
      case 'pickFiles': pickFiles(); break;
      case 'pickFolder': pickFolder(); break;
      case 'skin': applySkin(p.value); broadcast(true); break;
      case 'mode': {
        state.mode = (state.mode + 1) % MODES.length;
        broadcast(true);
        break;
      }
      case 'setMode': {
        const v = Math.max(0, Math.min(MODES.length - 1, Number(p.value) || 0));
        state.mode = v;
        broadcast(true);
        break;
      }
      case 'eqGain': {
        state.eq[p.index] = p.value;
        if (!eqFilters.length) ensureAudioGraph();
        applyEQ();
        broadcast(true);
        break;
      }
      case 'eqPreset': {
        const preset = EQ_PRESETS[p.name];
        if (preset) {
          preset.forEach((v, i) => state.eq[i] = v);
          if (!eqFilters.length) ensureAudioGraph();
          applyEQ();
          broadcast(true);
        }
        break;
      }
      case 'eqReset': {
        state.eq.fill(0);
        if (!eqFilters.length) ensureAudioGraph();
        applyEQ();
        broadcast(true);
        break;
      }
      case 'eqOn': {
        state.eqOn = !!p.on;
        applyEQ();
        broadcast(true);
        break;
      }
      case 'newList': newList(); break;
      case 'switchList': switchList(p.index); break;
      case 'renameList': renameList(p.index, p.name); break;
      case 'deleteList': deleteList(p.index); break;
    }
  });

  listen('tt:sync', () => {
    // 歌单还没读完就先不回，等读完那次广播统一同步给面板
    if (!sessionReady) return;
    broadcast(true);
    applySkin(state.skin);
  });

  /* ---------- 启动时恢复上次的歌单 ---------- */
  async function restoreSession() {
    try {
      const txt = await invoke('load_session');
      if (txt) {
        const s = JSON.parse(txt);
        const ver = Number(s.v) || 1;
        const toTrack = (t) => ({
          name: t.name || String(t.path).split(/[\\/]/).pop(),
          path: t.path,
          // src 每次重新生成（asset 协议的 URL 由运行时决定，不存盘）
          src: convertFileSrc(t.path),
          duration: typeof t.duration === 'number' ? t.duration : null,
          title: t.title || null,
          artist: t.artist || null,
          album: t.album || null,
          codec: t.codec || null,
          sampleRate: t.sampleRate || null,
          bits: t.bits || null,
          bitrate: t.bitrate || null,
          // v2 及更早的存档没有 codec（那时按扩展名显示格式），
          // 清掉标记让它按真实编码重读一遍
          tagged: ver >= 3 && !!t.tagged,
        });
        const clean = (arr) => (Array.isArray(arr) ? arr : [])
          .filter((t) => t && typeof t.path === 'string' && t.path)
          .map(toTrack);
        const fixCurr = (l) => {
          l.curr = (Number.isInteger(l.curr) && l.curr >= 0 && l.curr < l.tracks.length)
            ? l.curr : (l.tracks.length ? 0 : -1);
          return l;
        };

        let lists = [];
        if (Array.isArray(s.lists)) {
          lists = s.lists.map((l) => fixCurr({
            name: String((l && l.name) || '列表'),
            curr: Number(l && l.curr),
            tracks: clean(l && l.tracks),
          }));
        } else if (Array.isArray(s.tracks)) {
          // 旧版 v1：整张歌单落到「默认」列表
          lists = [fixCurr({ name: '默认', curr: Number(s.curr) || 0, tracks: clean(s.tracks) })];
        }

        // v0.11 存的是 strip / bg / off。新布局默认给频谱：
        // 老的 strip（条带示波）现在没有容身处了，退回默认的频谱态。
        const oscAlias = { strip: 'spec', off: 'spec', spec: 'spec', wave: 'wave', bg: 'bg' };
        if (oscAlias[s.osc]) state.osc = oscAlias[s.osc];

        if (lists.length) {
          state.lists = lists;
          const a = Number(s.active);
          state.active = (Number.isInteger(a) && a >= 0 && a < lists.length) ? a : 0;
          state.playlist = lists[state.active].tracks;
          state.curr = lists[state.active].curr;
          state.plRev++;
        }
      }
    } catch (e) { console.warn('恢复播放列表失败', e); }
    sessionReady = true;
    savedRev = state.plRev;
    savedCurr = state.curr;
    if (state.playlist.length) {
      // 只装载上次那一首（歌名 / 时长先显示出来），不自动播放
      state.playing = false;
      loadCurrent(false);
      audio.pause();
      // 老版本的 session.json 没有标签字段，补读一次，读完就带上 tagged 落盘
      const missing = state.playlist.filter((t) => !t.tagged);
      if (missing.length) readMetaFor(missing);
    } else {
      broadcast(true);
    }
    applyOscUI();
  }

  restoreSession();
} else {
  /* ===========================================================
     普通面板窗口：只发命令、只渲染
     =========================================================== */
  listen('tt:state', (e) => applyState(e.payload));
  listen('tt:meter', (e) => paintMeter(e.payload));
  emit('tt:sync');
}

/* ===========================================================
   面板窗口渲染
   =========================================================== */
function paintMeter(values) {
  const bars = document.querySelectorAll('.meter-bar');
  if (!bars.length) return;
  bars.forEach((b, i) => {
    const v = Math.max(0, Math.min(100, values[i] || 0));
    b.style.height = Math.max(6, v) + '%';
  });
}

function applyState(s) {
  if (!s) return;

  if (s.skin && s.skin !== state.skin) applySkin(s.skin);

  if (s.playlist) state.playlist = s.playlist;
  if (s.lists) state.listMeta = s.lists;
  if (typeof s.active === 'number') state.active = s.active;
  if (typeof s.eqOn === 'boolean') state.eqOn = s.eqOn;
  state.curr = s.curr;
  state.playing = s.playing;
  state.position = s.position;
  state.duration = s.duration;
  state.volume = s.volume;
  state.muted = s.muted;
  state.mode = s.mode;
  state.eq = s.eq || state.eq;
  // 时长是播放时才读到的，靠 plRev 不会同步到面板，这里单独补一笔
  if (s.duration && s.curr >= 0 && state.playlist[s.curr]
      && !state.playlist[s.curr].duration) {
    state.playlist[s.curr].duration = s.duration;
    lastListKey = '';
  }

  paintMainUI();
  syncPlaylist();
  syncEq();
  if (LYRIC) syncLyric();
}

/* ===========================================================
   歌词秀
   =========================================================== */
let lyricLines = [];
let lyricPath = '';
let lyricIdx = -1;
let lyricSeq = 0;
let lyricUserUntil = 0;      // 用户手动滚动后的一段时间内不抢滚动
let lyricScrollAt = 0;

function parseLrc(text) {
  const out = [];
  const tagRe = /\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;
  const om = /\[offset:\s*([+-]?\d+)\s*\]/i.exec(text || '');
  const offset = om ? Number(om[1]) / 1000 : 0;
  for (const raw of (text || '').split(/\r?\n/)) {
    tagRe.lastIndex = 0;
    const tags = [...raw.matchAll(tagRe)];
    if (!tags.length) continue;
    const content = raw.replace(tagRe, '').replace(/\s+/g, ' ').trim();
    if (!content) continue;
    for (const m of tags) {
      let frac = m[3] ? Number(m[3]) : 0;
      if (m[3] && m[3].length === 2) frac *= 10;
      else if (m[3] && m[3].length === 1) frac *= 100;
      out.push({ t: Number(m[1]) * 60 + Number(m[2]) + frac / 1000 + offset, text: content });
    }
  }
  out.sort((a, b) => a.t - b.t);
  const uniq = out.filter((x, i) => i === 0 || x.t !== out[i - 1].t || x.text !== out[i - 1].text);
  return stripHeadMeta(uniq);
}

/** 去掉开头那几行「作词 : X / 编曲 : Y / 制作人 : Z」，它们不是歌词 */
function stripHeadMeta(lines) {
  const META = /^(作词|作曲|编曲|制作人|制作|混音|母带|和声|录音|监制|出品|发行|统筹|吉他|贝斯|鼓|弦乐|键盘|词|曲|原唱|演唱|OP|SP)\s*[:：]/;
  let i = 0;
  while (i < lines.length && lines[i].t < 30 && META.test(lines[i].text)) i++;
  return (i >= 2 && lines.length - i >= 4) ? lines.slice(i) : lines;
}

function lyricHint(msg) {
  const box = $('lyric-body');
  if (!box) return;
  box.innerHTML = '';
  const d = document.createElement('div');
  d.className = 'lyric-hint';
  d.textContent = msg;
  box.appendChild(d);
}

function renderLyric() {
  const box = $('lyric-body');
  if (!box) return;
  box.innerHTML = '';
  const frag = document.createDocumentFragment();
  lyricLines.forEach((ln) => {
    const d = document.createElement('div');
    d.className = 'lyric-line';
    d.dataset.t = ln.t;
    d.textContent = ln.text;
    frag.appendChild(d);
  });
  box.appendChild(frag);
  lyricIdx = -1;
}

async function loadLyricFor(path) {
  const box = $('lyric-body');
  if (!box) return;
  const my = ++lyricSeq;
  lyricPath = path || '';
  lyricLines = [];
  lyricIdx = -1;

  if (!path) { lyricHint('未播放'); return; }
  lyricHint('正在查找歌词…');

  let text = null;
  try {
    text = await invoke('read_lyric_file', { path });
  } catch (e) { /* 忽略，按无歌词处理 */ }
  if (my !== lyricSeq) return;               // 期间切歌了，丢弃

  if (!text) { lyricHint('没有找到歌词文件'); return; }
  const lines = parseLrc(text);
  if (!lines.length) { lyricHint('歌词格式无法识别'); return; }

  lyricLines = lines;
  renderLyric();
  syncLyric(true);
}

function syncLyric(force = false) {
  const box = $('lyric-body');
  if (!box) return;

  const t = state.playlist[state.curr];
  const path = t ? t.path : '';
  if (path !== lyricPath) { loadLyricFor(path); return; }
  if (!lyricLines.length) return;

  const pos = state.position || 0;
  let lo = 0, hi = lyricLines.length - 1, idx = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lyricLines[mid].t <= pos + 0.12) { idx = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  if (idx === lyricIdx && !force) return;
  lyricIdx = idx;

  const kids = box.children;
  for (let i = 0; i < kids.length; i++) {
    kids[i].classList.toggle('active', i === idx);
  }
  if (idx < 0) { box.scrollTop = 0; return; }
  if (performance.now() < lyricUserUntil) return;

  const el = kids[idx];
  const target = el.offsetTop - box.clientHeight / 2 + el.offsetHeight / 2;
  const now = performance.now();
  // 跳转/换歌时瞬间归位，正常播放时平滑滚动
  if (force || now - lyricScrollAt > 900) {
    box.scrollTo({ top: Math.max(0, target), behavior: force ? 'auto' : 'smooth' });
  } else {
    box.scrollTop = Math.max(0, target);
  }
  lyricScrollAt = now;
}

if (LYRIC) {
  // 手动翻歌词时，短暂让出自动滚动
  $('lyric-body').addEventListener('wheel', () => {
    lyricUserUntil = performance.now() + 2600;
  }, { passive: true });
  $('lyric-body').addEventListener('pointerdown', () => {
    lyricUserUntil = performance.now() + 2600;
  });
  // 点歌词行跳到对应时间
  $('lyric-body').addEventListener('click', (e) => {
    const line = e.target.closest('.lyric-line');
    if (!line || !state.duration) return;
    cmd('seek', { ratio: Math.min(1, Number(line.dataset.t) / state.duration) });
  });
}

/* ---------- 主控制区（主窗口自己也用它渲染） ---------- */
function paintMainUI() {
  const t = state.playlist[state.curr];
  const info = $('info-title');
  if (info) info.textContent = t ? (t.title || baseName(t.name)) : '宁宁静听';
  const idx = $('info-idx');
  if (idx) {
    idx.textContent = t
      ? `第 ${state.curr + 1} / ${state.playlist.length} 首`
      : '';
  }
  const alb = $('info-album');
  if (alb) alb.textContent = t && t.album ? t.album : '';

  /* 状态文字：状态 + 真实编码 + 采样率 + 位深 / 码率（不猜声道）
     加歌的时候这一行让给进度，加完自动还原 */
  paintBusy();

  const cur = $('time-cur');
  if (cur) cur.textContent = fmt(state.position);
  const tot = $('time-total');
  if (tot) tot.textContent = fmt(state.duration);

  if (!dragSeek && state.duration) {
    const p = state.position / state.duration * 100;
    const fill = $('seek-fill');
    const knob = $('seek-knob');
    if (fill) fill.style.width = p + '%';
    if (knob) knob.style.left = p + '%';
  }

  const ico = $('ico-play');
  if (ico) {
    ico.innerHTML = state.playing
      ? '<rect x="3" y="2" width="2.4" height="8" fill="currentColor"/><rect x="6.6" y="2" width="2.4" height="8" fill="currentColor"/>'
      : '<path d="M3 2l7 4-7 4z" fill="currentColor"/>';
  }

  const vf = $('vol-fill');
  const vk = $('vol-knob');
  const v = state.muted ? 0 : state.volume;
  if (vf) vf.style.width = v * 100 + '%';
  if (vk) vk.style.left = v * 100 + '%';
  const mute = $('btn-mute');
  if (mute) mute.classList.toggle('muted', state.muted || state.volume === 0);
}

/* ---------- 查找（只作用于播放列表窗口） ---------- */
let findQ = '';        // 当前查询词（已小写）
let findPos = 0;       // 高亮命中项序号
let findHits = 0;      // 命中数量
let appliedKey = '';   // 上次过滤依据，避免每秒重算 500 行
let appliedQ = null;
let lastSideKey = '';
let lastListKey = '';

function syncFindBar() {
  const bar = $('findbar');
  const btn = $('btn-find');
  if (btn && bar) btn.classList.toggle('on', !bar.hidden);
}

function openFind(open) {
  const bar = $('findbar');
  if (!bar) return;
  bar.hidden = !open;
  const inp = $('find-input');
  if (open) {
    if (inp) { inp.focus(); inp.select(); }
  } else {
    findQ = ''; findPos = 0; appliedQ = null;
    if (inp) inp.value = '';
    appliedKey = '';
    applyFilter();
  }
  syncFindBar();
}

function paintFindNum() {
  const el = $('find-num');
  if (!el) return;
  el.textContent = findQ ? `${findHits ? findPos + 1 : 0} / ${findHits}` : '';
}

function markFindSel(scroll = true) {
  const ul = $('playlist');
  if (!ul) return;
  ul.querySelectorAll('li.hitsel').forEach((li) => li.classList.remove('hitsel'));
  if (!findQ) return;
  const rows = [...ul.children].filter((li) => !li.hidden);
  const li = rows[findPos];
  if (!li) return;
  li.classList.add('hitsel');
  if (scroll) li.scrollIntoView({ block: 'nearest' });
}

function applyFilter() {
  const ul = $('playlist');
  if (!ul) return;
  const q = findQ.trim();
  const key = q + '|' + state.plRev + '|' + state.active + '|' + ul.children.length;
  if (key === appliedKey) return;
  const qChanged = q !== appliedQ;
  appliedKey = key;
  appliedQ = q;

  findHits = 0;
  for (const li of ul.children) {
    const hit = !q || (li.dataset.name || '').toLowerCase().includes(q);
    li.hidden = !hit;                       // 只隐藏不删，序号与顺序不受影响
    li.classList.toggle('hit', !!q && hit);
    if (hit) findHits++;
  }
  if (findPos >= findHits) findPos = 0;
  paintFindNum();
  if (qChanged || q) markFindSel(qChanged);
}

function findStep(dir) {
  const ul = $('playlist');
  if (!ul) return;
  const rows = [...ul.children].filter((li) => !li.hidden);
  if (!rows.length) return;
  findPos = (findPos + dir + rows.length) % rows.length;
  markFindSel();
  paintFindNum();
}

function findPlay() {
  const ul = $('playlist');
  if (!ul) return;
  const rows = [...ul.children].filter((li) => !li.hidden);
  const li = rows[findPos];
  if (!li) return;
  cmd('playAt', { index: Number(li.dataset.i) });
}

/* ---------- 左栏：多列表 ---------- */
function renderSidebar() {
  const ul = $('pl-lists');
  if (!ul) return;
  const meta = state.listMeta && state.listMeta.length
    ? state.listMeta
    : [{ name: state.lists[0] ? state.lists[0].name : '默认', n: state.playlist.length }];

  const key = meta.map((m) => m.name + ':' + m.n).join('|') + '#' + state.active;
  if (key === lastSideKey) return;
  lastSideKey = key;
  ul.innerHTML = '';

  meta.forEach((m, i) => {
    const li = document.createElement('li');
    if (i === state.active) li.className = 'on';
    const nm = document.createElement('span');
    nm.className = 'lname';
    nm.textContent = m.name;
    const n = document.createElement('span');
    n.className = 'lnum';
    n.textContent = m.n;
    li.append(nm, n);
    if (meta.length > 1) {
      const del = document.createElement('span');
      del.className = 'ldel';
      del.textContent = '\u2715';
      del.title = '删除该列表';
      del.addEventListener('click', (e) => {
        e.stopPropagation();
        cmd('deleteList', { index: i });
      });
      li.append(del);
    }
    li.addEventListener('click', () => {
      if (i !== state.active) cmd('switchList', { index: i });
    });
    li.addEventListener('dblclick', () => startRename(li, i, m.name));
    // 右键：重命名 / 删除列表（删除只在有多个列表时给，只剩一个删不掉）
    li.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const items = [{ label: '重命名…', act: () => startRename(li, i, m.name) }];
      if (meta.length > 1) {
        items.push({ label: '删除列表', danger: true, act: () => cmd('deleteList', { index: i }) });
      }
      openCtx(e.clientX, e.clientY, items);
    });
    ul.appendChild(li);
  });

  const pane = document.querySelector('.pane[data-pane="playlist"]');
  const title = pane && pane.querySelector('.t-name');
  if (title) title.textContent = '播放列表 · ' + (meta[state.active] ? meta[state.active].name : '默认');

  const count = $('list-count');
  if (count) {
    const total = meta.reduce((a, b) => a + b.n, 0);
    count.textContent = meta.length > 1
      ? `${meta[state.active] ? meta[state.active].n : 0} 首 / 共 ${total} 首`
      : `${meta[state.active] ? meta[state.active].n : 0} 首`;
  }
}

/* 双击改名：就地变成输入框，回车提交、Esc 取消 */
function startRename(li, i, old) {
  const span = li.querySelector('.lname');
  if (!span) return;
  const inp = document.createElement('input');
  inp.value = old;
  span.replaceWith(inp);
  inp.focus();
  inp.select();
  let done = false;
  const commit = (ok) => {
    if (done) return;
    done = true;
    const v = inp.value.trim();
    lastSideKey = '';
    if (ok && v && v !== old) cmd('renameList', { index: i, name: v });
    else renderSidebar();
  };
  inp.addEventListener('keydown', (e) => {
    e.stopPropagation();
    if (e.key === 'Enter') commit(true);
    else if (e.key === 'Escape') commit(false);
  });
  inp.addEventListener('blur', () => commit(true));
  inp.addEventListener('mousedown', (e) => e.stopPropagation());
  inp.addEventListener('dblclick', (e) => e.stopPropagation());
}

/* ---------- 播放列表 ---------- */
function syncPlaylist() {
  const ul = $('playlist');
  if (!ul) return;

  const key = state.playlist
    .map((t) => (t.title || '') + '\u0001' + (t.artist || '') + '\u0001' + t.name + '|' + (t.duration || ''))
    .join('~') + '#' + state.curr;
  if (key !== lastListKey) {
    lastListKey = key;
    appliedKey = '';            // 重排后必须重新套过滤
    ul.innerHTML = '';
    state.playlist.forEach((t, i) => {
      const li = document.createElement('li');
      if (i === state.curr) li.className = 'playing';
      li.dataset.name = trackLabel(t);
      li.dataset.i = i;
      const idx = document.createElement('span');
      idx.className = 'idx';
      idx.textContent = (i + 1) + '.';
      const nm = document.createElement('span');
      nm.className = 'nm';
      nm.textContent = trackLabel(t);
      const dur = document.createElement('span');
      dur.className = 'dur';
      dur.textContent = t.duration ? fmt(t.duration) : '--:--';
      li.append(idx, nm, dur);
      li.addEventListener('dblclick', () => cmd('playAt', { index: i }));
      ul.appendChild(li);
    });
    const empty = $('list-empty');
    if (empty) empty.style.display = state.playlist.length ? 'none' : 'flex';
    const cur = ul.querySelector('li.playing');
    if (cur) cur.scrollIntoView({ block: 'nearest' });
  }

  renderSidebar();
  applyFilter();

  const modeBtn = $('btn-mode');
  if (modeBtn) {
    const lab = $('mode-label');
    if (lab) lab.textContent = MODES[state.mode] || '顺序';
    modeBtn.title = MODE_TIP[state.mode] || '';
    modeBtn.classList.toggle('on', state.mode !== 0);
    const menu = $('mode-menu');
    if (menu) {
      menu.querySelectorAll('.om-item').forEach((it) => {
        it.classList.toggle('on', Number(it.dataset.m) === state.mode);
      });
    }
  }
}

/* ---------- 均衡器 ---------- */
let eqBuilt = false;
function buildEqWindow() {
  const wrap = $('eq-sliders');
  if (!wrap || eqBuilt) return;
  eqBuilt = true;
  wrap.innerHTML = '';

  EQ_FREQS.forEach((_, i) => {
    const col = document.createElement('div');
    col.className = 'eq-col';
    const slot = document.createElement('div');
    slot.className = 'eq-slot';
    const thumb = document.createElement('div');
    thumb.className = 'eq-thumb';
    slot.appendChild(thumb);
    col.appendChild(slot);
    wrap.appendChild(col);

    const paint = () => {
      const g = state.eq[i] || 0;
      thumb.style.top = ((12 - g) / 24 * 100) + '%';
    };
    paint();
    col._paint = paint;

    let down = false;
    const move = (e) => {
      if (!down) return;
      const r = slot.getBoundingClientRect();
      const p = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
      const gain = Math.round(12 - p * 24);
      state.eq[i] = gain;
      paint();
      const sel = $('eq-preset');
      if (sel) sel.value = 'flat';
      cmd('eqGain', { index: i, value: gain });
    };
    slot.addEventListener('mousedown', (e) => {
      down = true;
      snap.busy = true;
      move(e);
      e.preventDefault();
    });
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', () => {
      if (down) { down = false; snap.busy = false; }
    });
    slot.addEventListener('dblclick', () => {
      state.eq[i] = 0;
      paint();
      cmd('eqGain', { index: i, value: 0 });
    });
  });

  /* 标题栏三个按钮：ON 旁路 / RESET 归零 / PRESETS 预设下拉 */
  const EQ_NAMES = [['flat', '默认'], ['pop', '流行'], ['rock', '摇滚'],
                    ['classic', '古典'], ['jazz', '爵士'], ['vocal', '人声']];
  const menu = $('eq-menu');
  if (menu) {
    menu.innerHTML = EQ_NAMES
      .map(([k, n]) => `<button class="om-item" data-p="${k}">${n}</button>`)
      .join('');
    menu.addEventListener('click', (e) => {
      const it = e.target.closest('.om-item');
      if (!it) return;
      menu.hidden = true;
      const preset = EQ_PRESETS[it.dataset.p];
      if (!preset) return;
      preset.forEach((v, i) => state.eq[i] = v);
      syncEq(true);
      cmd('eqPreset', { name: it.dataset.p });
    });
  }
  const pbtn = $('eq-presets');
  if (pbtn && menu) {
    pbtn.addEventListener('click', (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener('mousedown', (e) => {
      if (menu.hidden) return;
      if (e.target.closest('#eq-menu') || e.target.closest('#eq-presets')) return;
      menu.hidden = true;
    });
  }

  const onBtn = $('eq-on');
  if (onBtn) {
    onBtn.addEventListener('click', () => {
      const on = !(state.eqOn !== false);
      state.eqOn = on;
      paintEqOn();
      cmd('eqOn', { on });
    });
  }

  const resetBtn = $('eq-reset');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      state.eq.fill(0);
      syncEq(true);
      cmd('eqReset');
    });
  }
}

function paintEqOn() {
  const b = $('eq-on');
  if (!b) return;
  const on = state.eqOn !== false;
  b.textContent = on ? 'ON' : 'OFF';
  b.classList.toggle('off', !on);
  b.title = on ? '均衡已开启（点击旁路）' : '均衡已旁路（点击开启）';
}

function syncEq() {
  paintEqOn();
  if (!eqBuilt) return;
  document.querySelectorAll('.eq-col').forEach((col, i) => {
    const g = state.eq[i] || 0;
    const thumb = col.querySelector('.eq-thumb');
    if (thumb) thumb.style.top = ((12 - g) / 24 * 100) + '%';
  });
}

if (LABEL === 'eq') {
  buildEqWindow();
  paintMeter(new Array(16).fill(6));
}
if (LABEL === 'playlist') {
  syncPlaylist();
}

/* ---------- 关于窗口：只负责显示，逻辑极简 ---------- */
if (ABOUT) {
  // 版本号以 Cargo.toml 为准，别在前端写死
  const verEl = $('abt-ver');
  if (verEl && T.app && T.app.getVersion) {
    T.app.getVersion()
      .then((v) => { if (v) verEl.textContent = v; })
      .catch(() => { /* 拿不到就保留 HTML 里的兜底值 */ });
  }
  const skinEl = $('abt-skin');
  if (skinEl) {
    const s = SKINS.find((k) => k.id === state.skin);
    if (s) skinEl.textContent = s.name;
  }

  const closeMe = () => { ME.close().catch(() => {}); };
  $('abt-close')?.addEventListener('click', closeMe);
  $('abt-ok')?.addEventListener('click', closeMe);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMe();
  });
}

/* ===========================================================
   窗口行为：拖动 / 磁吸 / 吸边自动隐藏
   =========================================================== */
const snap = {
  busy: false,
  dragging: false,
  suppress: 0,
  dock: null,
};

function bindDrag(el) {
  if (!el) return;
  el.addEventListener('mousedown', async (e) => {
    if (e.button !== 0) return;
    if (e.target.closest('button, select, input, .t-icon')) return;
    // 关于窗是浮在主窗上的对话框，不参与吸附 / 吸边 / 落盘那一套，拖就纯拖
    if (ABOUT) {
      try { ME.startDragging(); } catch (err) { /* ignore */ }
      return;
    }
    // 注意：这里只能有一次 IPC 往返，多余的 await 会错过 startDragging 的时机导致拖不动
    snap.dock = null;
    try { await invoke('drag_begin', { label: LABEL }); } catch (err) { /* ignore */ }
    try { ME.startDragging(); } catch (err) { /* ignore */ }
    snap.dragging = true;
    // startDragging 在拖动结束时才返回，此刻把窗口位置记下来
    setTimeout(persistLayout, 160);
  });
}
document.querySelectorAll('[data-drag]').forEach(bindDrag);

/* ---------- 调整大小：拖右下角手柄，只改当前窗口 ---------- */
const gripEl = $('resize-grip');
if (gripEl) {
  // pointerdown 里 preventDefault 会吞掉 dblclick，所以自己算双击间隔
  let lastGripAt = 0;
  gripEl.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const now = Date.now();
    if (now - lastGripAt < 400) {
      lastGripAt = 0;
      invoke('reset_size', { label: LABEL }).catch(() => {});
      return;
    }
    lastGripAt = now;
    invoke('resize_begin', { label: LABEL }).catch(() => {});
  });
}

listen('tt:resizeend', () => {
  persistLayout();
  // 尺寸变了，贴合组要重新收集
  if (snap.dock) {
    invoke('dock_arm', { leader: LABEL, edge: snap.dock }).catch(() => {});
  }
});

/* ---------- 磁吸 ---------- */
async function trySnap() {
  if (snap.suppress > 0) return;

  let sf = 1;
  try { sf = await ME.scaleFactor(); } catch (e) { /* ignore */ }
  const TH = 14 * sf;

  let me;
  try {
    const p = await ME.outerPosition();
    const s = await ME.outerSize();
    me = { x: p.x, y: p.y, w: s.width, h: s.height };
  } catch (e) { return; }

  let bestX = null, bestY = null, ax = TH + 1, ay = TH + 1;
  let wins = [];
  try { wins = await getAllWindows(); } catch (e) { return; }

  for (const w of wins) {
    if (w.label === ME.label) continue;
    let vis = false;
    try { vis = await w.isVisible(); } catch (e) { continue; }
    if (!vis) continue;

    let op, os;
    try {
      op = await w.outerPosition();
      os = await w.outerSize();
    } catch (e) { continue; }

    const candX = [
      (op.x + os.width) - me.x,            // 我的左 ← 它的右
      op.x - (me.x + me.w),                // 我的右 → 它的左
      op.x - me.x,                         // 左对齐
      (op.x + os.width) - (me.x + me.w),   // 右对齐
    ];
    for (const c of candX) {
      const a = Math.abs(c);
      if (a <= TH && a < ax) { ax = a; bestX = c; }
    }

    const candY = [
      (op.y + os.height) - me.y,           // 我的上 ← 它的下
      op.y - (me.y + me.h),                // 我的下 → 它的上
      op.y - me.y,                         // 上对齐
      (op.y + os.height) - (me.y + me.h),  // 下对齐
    ];
    for (const c of candY) {
      const a = Math.abs(c);
      if (a <= TH && a < ay) { ay = a; bestY = c; }
    }
  }

  if (bestX === null && bestY === null) return;

  const nx = Math.round(me.x + (bestX || 0));
  const ny = Math.round(me.y + (bestY || 0));
  if (nx === me.x && ny === me.y) return;

  snap.suppress = 4;
  try { await ME.setPosition(new PhysPos(nx, ny)); } catch (e) { /* ignore */ }
  setTimeout(() => { snap.suppress = 0; }, 320);
}

/* ---------- 吸边检测 ---------- */
async function checkDock() {
  let mon = null;
  try { mon = await currentMonitor(); } catch (e) { /* ignore */ }
  if (!mon) return;

  let me;
  try {
    const p = await ME.outerPosition();
    const s = await ME.outerSize();
    me = { x: p.x, y: p.y, w: s.width, h: s.height };
  } catch (e) { return; }

  const L = mon.position.x;
  const Tp = mon.position.y;
  const R = L + mon.size.width;
  const B = Tp + mon.size.height;
  let sf = 1;
  try { sf = await ME.scaleFactor(); } catch (e) { /* ignore */ }
  const G = 5 * sf;

  snap.dock = null;
  if (Math.abs(me.y - Tp) <= G) snap.dock = 'top';
  else if (Math.abs(me.y + me.h - B) <= G) snap.dock = 'bottom';
  else if (Math.abs(me.x - L) <= G) snap.dock = 'left';
  else if (Math.abs(me.x + me.w - R) <= G) snap.dock = 'right';

  // 交给 Rust：记录整组归位坐标，之后由全局鼠标轮询负责收起 / 弹出
  try {
    if (snap.dock) await invoke('dock_arm', { leader: LABEL, edge: snap.dock });
    else await invoke('dock_disarm');
  } catch (e) { /* ignore */ }
}

/* ---------- 拖动结束：吸附 + 吸边 ---------- */
/* 拖动过程中由 Rust 端实时带动所有窗口，前端只在拖动真正结束后做一次吸附判定 */
async function onDragEnd() {
  snap.dragging = false;
  await trySnap();
  await checkDock();
  // 面板可能被拖到主体那一列之外独立摆放：让列里剩下的面板补齐空位，
  // （独立摆放的那个不会被 align_all 挪走，它已经不在列里）
  if (LABEL !== 'main') {
    try { await invoke('align_all'); } catch (e) { /* ignore */ }
  }
  // 拖动结束的位置才是用户要的，落盘
  persistLayout();
}

listen('tt:dragend', (e) => {
  if (e.payload !== LABEL) return;
  onDragEnd();
});

// 兜底：万一没收到 Rust 的 dragend，松开鼠标时自己再判一次
window.addEventListener('mouseup', () => {
  if (snap.dragging) onDragEnd();
});

/* ---------- 收起状态（由 Rust 统一驱动） ---------- */
/* 整组窗口的收起与弹出都在 Rust 侧完成，前端只负责同步视觉状态 */
listen('tt:dock', (e) => {
  document.body.classList.toggle('docked', !!e.payload);
});

/* ===========================================================
   面板窗口开关（主窗口）
   =========================================================== */
async function getWin(label) {
  try {
    const wins = await getAllWindows();
    return wins.find((w) => w.label === label) || null;
  } catch (e) { return null; }
}

async function togglePanelWin(label) {
  const w = await getWin(label);
  if (!w) return;
  let vis = false;
  try { vis = await w.isVisible(); } catch (e) { /* ignore */ }
  try {
    if (vis) {
      await w.hide();
      // 空出来的位置由下方成员补上
      try { await invoke('compact_panels'); } catch (e) { /* ignore */ }
    } else {
      // 先定位再显示，避免在旧位置闪一下
      try { await invoke('place_panel', { label }); } catch (e) { /* ignore */ }
      await w.show();
      await w.setFocus();
      // show 之后窗口尺寸才可靠，再校正一次
      setTimeout(() => { invoke('place_panel', { label }).catch(() => {}); }, 90);
    }
  } catch (e) { /* ignore */ }
  refreshPanelBtns();
  // 开关状态也要记住：下次启动该开着的还开着、关掉的别自己冒出来
  persistLayout();
  // 面板增减后贴合组变了，重新收集，免得新出现的面板不跟着一起收起
  if (snap.dock) {
    const edge = snap.dock;
    setTimeout(() => {
      invoke('dock_arm', { leader: LABEL, edge }).catch(() => {});
    }, 160);
  }
}

async function refreshPanelBtns() {
  for (const label of ['playlist', 'lyric', 'eq']) {
    const w = await getWin(label);
    if (!w) continue;
    let vis = false;
    try { vis = await w.isVisible(); } catch (e) { /* ignore */ }
    const btn = document.querySelector(`.cap[data-panel="${label}"]`);
    if (btn) btn.classList.toggle('on', vis);
  }
}

if (IS_MAIN) {
  document.querySelectorAll('.cap').forEach((b) => {
    b.addEventListener('click', () => togglePanelWin(b.dataset.panel));
  });
  refreshPanelBtns();
  setInterval(refreshPanelBtns, 1200);

  // 启动后把面板与主体重新对齐一次（窗口最终定位可能比 DOM 加载晚）
  [140, 420].forEach((d) => {
    setTimeout(() => { invoke('align_all').catch(() => {}); }, d);
  });

  // 窗口位置与尺寸由 Rust 在 startup 时按 window-layout.txt 还原，
  // 这里只需要在拖动结束后（startDragging 返回）把最终位置落盘
}

/* 各面板自己的关闭按钮 = 隐藏自己 */
['pl-close', 'ly-close', 'eq-close'].forEach((id) => {
  const b = $(id);
  if (b) b.addEventListener('click', () => ME.hide());
});

/* ===========================================================
   文件：选择 / 拖入
   =========================================================== */
bindOpenMenu('btn-add', 'pl-openmenu', (act) => {
  cmd(act === 'folder' ? 'pickFolder' : 'pickFiles');
});

const removeBtn = $('btn-remove');
if (removeBtn) removeBtn.addEventListener('click', () => cmd('remove', { index: state.curr }));

const sortBtn = $('btn-sort');
if (sortBtn) sortBtn.addEventListener('click', () => cmd('sort'));

const clearBtn = $('btn-clear');
if (clearBtn) clearBtn.addEventListener('click', () => {
  if (state.playlist.length) cmd('clear');
});

/* ---------- 播放模式：按钮变成下拉菜单 ---------- */
const modeBtn = $('btn-mode');
const modeMenu = $('mode-menu');

function setMode(m) {
  m = Math.max(0, Math.min(MODES.length - 1, Number(m) || 0));
  if (modeMenu) modeMenu.hidden = true;
  if (state.mode === m) return;
  // 本窗先改，按钮立刻响应；真正生效在主窗（播放状态都由它持有）
  state.mode = m;
  syncPlaylist();
  cmd('setMode', { value: m });
  notify('播放模式：' + MODE_TIP[m]);
}

if (modeBtn && modeMenu) {
  modeBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    modeMenu.hidden = !modeMenu.hidden;
  });
  modeMenu.querySelectorAll('.om-item').forEach((it) => {
    it.addEventListener('click', (e) => {
      e.stopPropagation();
      setMode(it.dataset.m);
    });
  });
  document.addEventListener('mousedown', (e) => {
    if (modeMenu.hidden) return;
    if (e.target.closest('#mode-menu') || e.target.closest('#btn-mode')) return;
    modeMenu.hidden = true;
  });
}

/* ---------- 导出 WAV ---------- */
/* 由 Rust（symphonia）解码，前端只负责选路径和报进度。
   导的是解码后的原始音质：不加淡入淡出、不套均衡器。 */
const exportBtn = $('btn-export');
if (exportBtn) {
  exportBtn.addEventListener('click', async () => {
    const t = state.playlist[state.curr];
    if (!t) { notify('先选一首歌再导出'); return; }

    const base = (t.artist && t.title)
      ? `${t.artist} - ${t.title}`
      : (t.title || baseName(t.name));
    let dst = null;
    try {
      dst = await invoke('pick_wav_target', {
        defaultName: base.replace(/[\\/:*?"<>|]/g, '_') + '.wav',
      });
    } catch (e) { notify('打开保存对话框失败'); return; }
    if (!dst) return;
    if (!/\.wav$/i.test(dst)) dst += '.wav';

    const old = exportBtn.textContent;
    exportBtn.disabled = true;
    exportBtn.textContent = '导出中';
    try {
      const r = await invoke('export_wav_file', { src: t.path, dst });
      notify(`已导出 ${fmt(r.duration)} · ${(r.bytes / 1048576).toFixed(1)} MB`);
    } catch (e) {
      notify('导出失败：' + String(e));
    } finally {
      exportBtn.disabled = false;
      exportBtn.textContent = old;
    }
  });
}

/* ---------- 查找栏 ---------- */
const findBtn = $('btn-find');
if (findBtn) {
  findBtn.addEventListener('click', () => {
    const bar = $('findbar');
    if (bar) openFind(bar.hidden);
  });
}
const findInput = $('find-input');
if (findInput) {
  findInput.addEventListener('input', () => {
    findQ = findInput.value.trim().toLowerCase();
    findPos = 0;
    appliedKey = '';
    applyFilter();
  });
  findInput.addEventListener('keydown', (e) => {
    e.stopPropagation();
    if (e.key === 'Enter') {
      e.preventDefault();
      if (e.shiftKey) findStep(-1);
      else findPlay();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault(); findStep(1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault(); findStep(-1);
    } else if (e.key === 'Escape') {
      e.preventDefault(); openFind(false);
    }
  });
}
const findPrev = $('find-prev');
if (findPrev) findPrev.addEventListener('click', () => findStep(-1));
const findNext = $('find-next');
if (findNext) findNext.addEventListener('click', () => findStep(1));
const findClose = $('find-close');
if (findClose) findClose.addEventListener('click', () => openFind(false));

/* ---------- 新建列表 ---------- */
const newListBtn = $('btn-new-list');
if (newListBtn) newListBtn.addEventListener('click', () => cmd('newList'));

try {
  ME.onDragDropEvent((event) => {
    const p = event.payload;
    if (p.type === 'over' || p.type === 'enter') {
      appEl.classList.add('dragging');
    } else if (p.type === 'drop') {
      appEl.classList.remove('dragging');
      if (!p.paths || !p.paths.length) return;
      // 拖进来的可能是文件夹，展开成里面的音频文件（走目录时同样报进度）
      (async () => {
        const all = [];
        const hasDir = p.paths.some((x) => !AUDIO_RE.test(x));
        if (hasDir) scanStart('walk');
        try {
          for (const path of p.paths) {
            if (AUDIO_RE.test(path)) { all.push(path); continue; }
            try {
              const got = await invoke('scan_audio_dir_progress', { path });
              if (got && got.length) all.push(...got);
            } catch (e) { /* ignore */ }
          }
        } finally {
          if (hasDir) scanDone();
        }
        if (all.length) cmd('addPaths', { paths: all });
      })();
    } else {
      appEl.classList.remove('dragging');
    }
  });
} catch (e) { /* ignore */ }
