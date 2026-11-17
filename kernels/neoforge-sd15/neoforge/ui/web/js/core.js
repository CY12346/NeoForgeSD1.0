/* =====================================================================
   core.js · 基础层
   工具函数、共享状态、顶栏与分组切换、作业轮询、画布与历史。
   规矩：这里不 import 任何其它页面模块（所有模块单向依赖它），
        也不写任何样式 —— 效果全在 css/fx.css。
   ===================================================================== */

/* ---------- 共享状态：全站唯一一份，别的模块改了这里就是真改了 ---------- */
export const S = {
  STATUS: {},          // /api/status 快照
  SRC_B64: null,       // 图生图/局部重绘的源图
  SRC_UP: null,        // 放大用的源图
  INFO_B64: null,      // PNG 信息用的图
  STAGE_URL: null,     // 画布当前图
  TIMER: null,         // 轮询定时器
  LASTJOB: null,       // 最近一次作业结果（复用 seed 用）
  LASTREC: null,       // 最近一条历史记录（复用参数用）
  IP_READY: false,     // 局部重绘画布已载图
  painting: false,     // 正在涂抹
  AUTOSTART: false     // 已尝试过自动拉起后端
};

export const $ = id => document.getElementById(id);

export function esc(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;');
}

export function setMsg(id, t, err) {
  const e = $(id);
  if (!e) return;
  e.textContent = t || '';
  e.className = 'msg' + (err ? ' err' : '');
}

/* 把数组灌进 <select>；keep=true 时保留当前选中值 */
export function fill(sel, arr, keep) {
  const el = $(sel);
  if (!el || !arr || !arr.length) return;
  const cur = keep ? el.value : null;
  el.innerHTML = arr.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  if (cur && arr.includes(cur)) el.value = cur;
}

export function pickDefault(sel, want) {
  const el = $(sel);
  if (!el || el.dataset.init) return;
  if (el.value === want || !want) return;
  el.value = want;
  el.dataset.init = '1';
}

export function readAsDataURL(f) {
  return new Promise(res => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.readAsDataURL(f);
  });
}

/* ---------- 分组切换（①创作 ②后期 ③模型 ④资产） ---------- */
export function showG(g) {
  ['create', 'late', 'model', 'asset'].forEach(k => {
    const main = $('g-' + k), btn = $('gbtn-' + k);
    if (main) main.hidden = k !== g;
    if (btn) btn.classList.toggle('on', k === g);
  });
  history.replaceState(null, '', '?g=' + g);
}

/* ---------- 创作页签（文生图 / 图生图 / 局部重绘） ---------- */
export function mode(m) {
  const map = { t2i: 'paneT2I', i2i: 'paneI2I', inp: 'paneINP' };
  Object.entries(map).forEach(([k, id]) => {
    const el = $(id);
    if (el) el.hidden = k !== m;
  });
  const tb = { t2i: 'tbT2I', i2i: 'tbI2I', inp: 'tbINP' };
  Object.entries(tb).forEach(([k, id]) => {
    const el = $(id);
    if (el) el.classList.toggle('on', k === m);
  });
  const hint = $('modeHint');
  if (hint) hint.textContent = { t2i: '文生图 txt2img', i2i: '图生图 img2img', inp: '局部重绘 inpaint' }[m];
}

/* ---------- 后端状态 ---------- */
export async function loadStatus() {
  try {
    const r = await fetch('/api/status');
    S.STATUS = await r.json();
  } catch (e) {
    S.STATUS = { online: false };
  }
  const st = S.STATUS, bk = st.backend || {}, opts = st.opts || {};
  const stat = $('stat');

  if (st.online) {
    stat.innerHTML = `<span class="dot on"></span>SD 在线 · ${esc(st.model || '?')}`;
    const mi = $('modelInfo');
    if (mi) {
      mi.textContent = `底模：${st.model || '-'}\nVAE：${opts.vae || '-'}\nCLIP skip：${opts.clip_skip}\n`
        + `LoRA：${(st.loras || []).length} 个 · 放大模型：${(st.upscalers || []).length} 个`;
    }
  } else if (bk.state === 'starting') {
    const el = (typeof bk.elapsed === 'number') ? bk.elapsed.toFixed(0) : '0';
    stat.innerHTML = `<span class="dot" style="background:#fbbf24;box-shadow:0 0 8px #fbbf24"></span>后端启动中… ${el}s`;
  } else if (bk.state === 'failed') {
    stat.innerHTML = `<span class="dot off"></span>后端启动失败：${esc(bk.msg || '')}`;
  } else {
    stat.innerHTML = `<span class="dot off"></span>SD 离线（${esc(st.api || '')}）`;
  }

  $('btnStart').style.display = st.online ? 'none' : 'inline-block';
  $('btnStop').style.display = st.online ? 'inline-block' : 'none';

  if (st.online) {
    fill('model', st.models, true);
    if (st.model && $('model')) $('model').value = st.model;
    fill('vae', st.vaes, true);
    if (opts.vae && $('vae')) $('vae').value = opts.vae;
    fill('sampler', st.samplers, true); pickDefault('sampler', 'DPM++ 2M');
    fill('scheduler', st.schedulers, true); pickDefault('scheduler', 'karras');
    fill('ups', st.upscalers, true);
    fill('hrUp', ['Latent'].concat(st.upscalers || []), true);
    if ($('ups') && !$('ups').value) {
      const a = (st.upscalers || []).find(u => /Anime/i.test(u));
      if (a) $('ups').value = a;
    }
    if ($('loraSel') && !$('loraSel').dataset.filled) {
      fill('loraSel', st.loras, false);
      $('loraSel').dataset.filled = '1';
    }
    if (opts.clip_skip && $('clipSkip') && !$('clipSkip').dataset.init) {
      $('clipSkip').value = opts.clip_skip;
      $('clipSkip').dataset.init = '1';
    }
  } else if (!S.AUTOSTART && bk.state === 'idle') {
    S.AUTOSTART = true;
    startSD(true);
  }
}

export async function startSD(silent) {
  const b = $('btnStart');
  b.disabled = true; b.textContent = '启动中…';
  const j = await (await fetch('/api/start_sd', { method: 'POST' })).json();
  b.disabled = false; b.textContent = '启动后端';
  if (!silent) alert(j.message);
  loadStatus();
}

export async function stopSD() {
  if (!confirm('结束后端会释放显存；下次出图要重新等 60–120 秒。确定？')) return;
  const j = await (await fetch('/api/stop_sd', { method: 'POST' })).json();
  alert(j.message);
  S.AUTOSTART = false;
  loadStatus();
}

/* 当前站在哪个页签 → 产物子目录（打开产物目录按这个走，文生图/图生图不互相跳） */
export function currentKind() {
  const visible = id => { const el = $(id); return el && !el.hidden; };
  if (visible('g-late')) return 'upscale';          // ② 后期 = 放大 / 修脸
  if (visible('g-create')) {                        // ① 创作：看三个页签哪个亮
    if (visible('paneI2I')) return 'img2img';
    if (visible('paneINP')) return 'inpaint';
    return 'txt2img';
  }
  return '';                                        // 模型/资产页：打开产物根
}

export async function openFolder(kind) {
  const k = (typeof kind === 'string') ? kind : currentKind();
  const j = await (await fetch('/api/open_folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: k })
  })).json();
  if (!j.ok) alert(j.error || '打开失败');
}

/* ---------- 作业轮询 ---------- */
export function poll(jid, bar, msg, onDone) {
  const t0 = Date.now();
  clearInterval(S.TIMER);
  S.TIMER = setInterval(async () => {
    let pr = 0;
    try {
      const p = await (await fetch('/api/progress')).json();
      pr = (p.progress || 0) * 100;
    } catch (e) { /* 后端忙时进度可能读不到，不影响作业查询 */ }
    const barEl = $(bar);
    if (barEl) barEl.style.width = pr.toFixed(0) + '%';
    const j = await (await fetch('/api/job/' + jid)).json();
    const el = ((Date.now() - t0) / 1000).toFixed(0);
    if (j.state === 'done') {
      clearInterval(S.TIMER); S.TIMER = null;
      if (barEl) barEl.style.width = '100%';
      onDone(j);
    } else if (j.state === 'error') {
      clearInterval(S.TIMER); S.TIMER = null;
      setMsg(msg, '失败：' + j.error, true);
      loadBtn(true);
    } else {
      setMsg(msg, `生成中… ${el}s ｜ 进度 ${pr.toFixed(0)}%`);
    }
  }, 1500);
}

export function loadBtn(on) {
  ['btnImg', 'btnEdit', 'btnInp', 'btnUp'].forEach(id => {
    const el = $(id);
    if (el) el.disabled = !on;
  });
}

/* ---------- 共享参数装配（三个页签共用） ---------- */
export function pickedLoras() {
  const el = $('loraSel'), wEl = $('loraW');
  if (!el) return [];
  const w = wEl ? +wEl.value : 0.8;
  const wt = isNaN(w) ? 0.8 : w;
  return [...el.selectedOptions].map(o => ({ name: o.value, weight: wt }));
}

export function adArgs() {
  return {
    enabled: $('adOn').checked,
    confidence: +$('adConf').value,
    denoise: +$('adDn').value,
    model: $('adModel').value
  };
}

/* ---------- 画布与历史 ---------- */
export function showImage(url, info) {
  S.STAGE_URL = url;
  $('stage').innerHTML = '<img src="' + url + '">';
  if (info) $('info').textContent = info;
}

export function renderGrid(el, urls) {
  const box = $(el);
  if (!box) return;
  if (!urls || !urls.length) {
    box.innerHTML = '<div class="ph">暂无</div>';
    return;
  }
  box.innerHTML = urls.map(u => `<img src="${u}" onclick="showImage('${u}')">`).join('');
}

export async function loadHist() {
  try {
    const j = await (await fetch('/api/history?limit=36')).json();
    const items = j.items || [];
    S.LASTREC = items[0] || null;
    const urls = [];
    items.forEach(it => (it.urls || []).forEach(u => urls.push(u)));
    renderGrid('hist', urls.slice(0, 36));
  } catch (e) { /* 历史读不到不影响出图 */ }
}

/* 把画布那张图取成 base64 当源图（图生图/局部重绘共用） */
export async function stageAsB64() {
  if (!S.STAGE_URL) return null;
  const blob = await (await fetch(S.STAGE_URL)).blob();
  return await readAsDataURL(blob);
}
