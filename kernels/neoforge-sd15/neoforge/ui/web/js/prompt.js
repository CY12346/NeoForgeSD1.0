/* =====================================================================
   prompt.js · 提示词助手
   中⇄英（词库优先）、输入补全、词库面板、风格预设、轻提示。
   ===================================================================== */
import { $, esc } from './core.js';

/* 正向框 → 对应反向框；pIp 没有反向框 */
const PT = { p1: 'n1', p2i: 'n2i', pIp: null };

let LAST_FIELD = 'p1';     // 最后一次聚焦的提示词框
let SUG = null;            // 当前补全状态 {el,id,frag,items,idx}
let LIB = null;            // 词库数据（懒加载一次）
let LIB_CAT = 0;           // 词库当前分类
let LIB_MODE = 'lib';      // lib | style
let LIB_FIELD = 'p1';      // 词库要插进哪个框

/* ---------- 轻提示（带撤销） ---------- */
export function toast(msg, err, undoField) {
  const t = $('toast'), m = $('toastMsg');
  m.textContent = msg;
  t.className = 'on' + (err ? ' err' : '');
  if (t._undo) { t._undo.remove(); t._undo = null; }
  if (undoField) {
    const b = document.createElement('button');
    b.textContent = '↶ 撤销';
    b.onclick = () => {
      const el = $(undoField);
      if (el && el.dataset.prev != null) el.value = el.dataset.prev;
      t.className = '';
    };
    t.appendChild(b);
    t._undo = b;
  }
  clearTimeout(t._tm);
  t._tm = setTimeout(() => { t.className = ''; }, err ? 6000 : 4000);
}

/* ---------- 给每个提示词框挂上工具条与补全 ---------- */
export function initPromptTools() {
  Object.keys(PT).forEach(id => {
    const el = $(id);
    if (!el) return;
    const row = document.createElement('div');
    row.className = 'prow';
    row.innerHTML = '<span class="lbl">提示词助手</span>'
      + `<button class="mini" onclick="trDoc('${id}','zh2en')">中→英</button>`
      + `<button class="mini" onclick="trDoc('${id}','en2zh')">英→中</button>`
      + `<button class="mini" onclick="openLib('${id}')">词库</button>`
      + `<button class="mini" onclick="openStyle('${id}')">风格</button>`;
    el.parentNode.insertBefore(row, el);
    el.addEventListener('focus', () => { LAST_FIELD = id; });
    el.addEventListener('input', () => suggest(id));
    el.addEventListener('blur', () => setTimeout(hideSug, 200));
    el.addEventListener('keydown', ev => sugKey(ev, id));
  });

  ['n1', 'n2i'].forEach(id => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('focus', () => { LAST_FIELD = id; });
    el.addEventListener('blur', () => setTimeout(hideSug, 200));
    el.addEventListener('keydown', ev => sugKey(ev, id));
  });

  /* focusin 冒泡委托：比逐元素 focus 稳（hidden 页签 / 程序 focus 都能收到） */
  document.addEventListener('focusin', e => {
    const id = e.target && e.target.id;
    if (id && (id in PT || id === 'n1' || id === 'n2i')) LAST_FIELD = id;
  });

  /* 预热词典：首次加载约 1.5s，开局就打一次 */
  fetch('/api/i18n').then(r => r.json())
    .then(j => { if (j.error) console.warn('[词典]', j.error); })
    .catch(() => {});
}

/* ---------- 中⇄英 ---------- */
export async function trDoc(id, dir) {
  const el = $(id);
  const txt = (el.value || '').trim();
  if (!txt) { toast('这个框里还没有内容', true); return; }
  toast(dir === 'zh2en' ? '翻译中…（词库优先，整句才联网）' : '转换中…');

  let j;
  try {
    j = await (await fetch('/api/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: txt, dir })
    })).json();
  } catch (e) { toast('请求失败：' + e, true); return; }
  if (j.error) { toast('失败：' + j.error, true); return; }

  el.dataset.prev = txt;
  el.value = j.out;
  const miss = (j.unknown || []).filter(Boolean);
  const web = (j.hits || []).filter(h => h[2] === 'web').length;
  toast(`${dir === 'zh2en' ? '中→英' : '英→中'}完成 · ${j.elapsed}s · 词库 ${(j.hits || []).length - web} 词`
    + (web ? ` · 联网 ${web} 词` : '') + (miss.length ? ` · 没认出来：${miss.join(' ')}` : ''),
    false, id);
}

/* ---------- 输入补全 ---------- */
export function hideSug() {
  const b = $('sug');
  if (b) { b.style.display = 'none'; b.innerHTML = ''; }
  SUG = null;
}

export async function suggest(id) {
  const el = $(id);
  const pos = el.selectionStart || 0;
  const frag = (el.value.slice(0, pos).match(/[A-Za-z0-9_\- \u4e00-\u9fff]{2,}$/) || [''])[0];
  const q = frag.trim();
  if (q.length < 2) { hideSug(); return; }

  let j;
  try {
    j = await (await fetch('/api/suggest?q=' + encodeURIComponent(q) + '&limit=12')).json();
  } catch (e) { return; }
  if (!j.items || !j.items.length || LAST_FIELD !== id) { hideSug(); return; }

  SUG = { el, id, frag, items: j.items, idx: 0 };
  renderSug();
  placeSug(el);
}

function renderSug() {
  const b = $('sug');
  b.innerHTML = SUG.items.map((it, i) =>
    `<div class="it${i === SUG.idx ? ' sel' : ''}" data-i="${i}" `
    + `onmousedown="event.preventDefault();applySug(${i})">`
    + `<b>${esc(it.tag)}</b><span>${esc(it.zh || '')}</span>`
    + `<i>${it.count > 9999 ? (it.count / 1000 | 0) + 'k' : (it.count || '')}</i></div>`
  ).join('');
  b.style.display = 'block';
}

/* 下拉定位：贴输入框下沿，空间不够就翻到上方（几何计算，不是样式特效） */
function placeSug(el) {
  const r = el.getBoundingClientRect(), b = $('sug');
  const h = Math.min(280, b.scrollHeight || 200);
  let top = r.bottom + 4;
  if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
  b.style.left = Math.min(r.left, window.innerWidth - 320) + 'px';
  b.style.top = top + 'px';
}

export function applySug(i) {
  if (!SUG) return;
  const { el, frag, items } = SUG;
  const it = items[i];
  if (!it) { hideSug(); return; }
  const pos = el.selectionStart || el.value.length;
  const tag = it.tag.replace(/_/g, ' ');
  el.setRangeText(tag + ', ', pos - frag.length, pos, 'end');
  hideSug();
  el.focus();
  el.dispatchEvent(new Event('input'));
}

export function sugKey(ev, id) {
  if (!SUG || SUG.id !== id) return;
  if (ev.key === 'ArrowDown') {
    SUG.idx = (SUG.idx + 1) % SUG.items.length; renderSug(); ev.preventDefault();
  } else if (ev.key === 'ArrowUp') {
    SUG.idx = (SUG.idx - 1 + SUG.items.length) % SUG.items.length; renderSug(); ev.preventDefault();
  } else if (ev.key === 'Enter' || ev.key === 'Tab') {
    applySug(SUG.idx); ev.preventDefault();
  } else if (ev.key === 'Escape') {
    hideSug();
  }
}

/* ---------- 插入 tag ---------- */
export function insertAtCursor(id, text) {
  const el = $(id);
  if (!el) return;
  const pos = el.selectionStart == null ? el.value.length : el.selectionStart;
  el.setRangeText(text + ', ', pos, pos, 'end');
  el.focus();
  el.dispatchEvent(new Event('input'));
  toast('已插入：' + text);
}

/* ---------- 词库面板 ---------- */
export async function openLib(id) {
  LIB_FIELD = id || LAST_FIELD;
  LIB_MODE = 'lib';
  $('mTitle').textContent = '提示词库';
  LIB_CAT = 0; LIB = null;
  $('libQ').value = '';
  $('libModal').className = 'modal on';
  $('libList').innerHTML = '<div class="note">加载词库…（3958 条）</div>';

  try { LIB = await (await fetch('/api/promptlib')).json(); }
  catch (e) { $('libList').innerHTML = '<div class="note">词库读取失败</div>'; return; }
  if (LIB.error) { $('libList').innerHTML = '<div class="note">' + esc(LIB.error) + '</div>'; return; }
  renderCats();
  renderLib();
}

export function setCat(i) {
  LIB_CAT = i;
  renderCats();
  renderLib();
}

export function renderCats() {
  const box = $('libCats');
  box.innerHTML = (LIB.cats || []).map((c, i) =>
    `<button class="${i === LIB_CAT ? 'on' : ''}" onclick="setCat(${i})">${esc(c.name)}</button>`
  ).join('');
}

export function renderLib() {
  const q = $('libQ').value.trim().toLowerCase();
  const box = $('libList');
  if (!LIB) return;

  if (q) {
    const hits = [];
    (LIB.cats || []).forEach(c => (c.groups || []).forEach(g => (g.tags || []).forEach(t => {
      if ((t.zh || '').toLowerCase().includes(q) || (t.en || '').toLowerCase().includes(q)) {
        hits.push(Object.assign({ grp: g.name }, t));
      }
    })));
    box.innerHTML = '<div class="libgrp"><h4>搜索「' + esc(q) + '」· ' + hits.length + ' 条</h4><div class="chips">'
      + hits.slice(0, 300).map(t =>
        `<button onclick="insertAtCursor('${LIB_FIELD}','${esc(t.en).replace(/'/g, '')}')">`
        + `${esc(t.zh)}<small>${esc(t.en)}</small></button>`).join('')
      + '</div></div>';
    return;
  }

  const cat = (LIB.cats || [])[LIB_CAT];
  if (!cat) { box.innerHTML = ''; return; }
  box.innerHTML = (cat.groups || []).map(g =>
    `<div class="libgrp"><h4>${esc(g.name)}</h4><div class="chips">`
    + (g.tags || []).map(t =>
      `<button onclick="insertAtCursor('${LIB_FIELD}','${esc(t.en).replace(/'/g, '')}')">`
      + `${esc(t.zh)}<small>${esc(t.en)}</small></button>`).join('')
    + '</div></div>').join('');
}

/* ---------- 风格预设 ---------- */
let STYLES = [];

export async function openStyle(id) {
  LIB_FIELD = id || LAST_FIELD;
  LIB_MODE = 'style';
  $('mTitle').textContent = '风格预设（SD styles）';
  $('libQ').value = '';
  $('libCats').innerHTML = '';
  $('libList').innerHTML = '<div class="note">读取中…</div>';
  $('libModal').className = 'modal on';

  let j;
  try { j = await (await fetch('/api/styles')).json(); } catch (e) { j = { items: [] }; }
  if (!j.items || !j.items.length) {
    $('libList').innerHTML =
      '<div class="note">没有可用风格：SD 后端未启动，或后端里没配 prompt styles。</div>';
    return;
  }
  STYLES = j.items;
  $('libList').innerHTML = '<div class="libgrp"><h4>点一下套用（正向 + 反向一起填）</h4><div class="chips">'
    + j.items.map((it, i) =>
        `<button data-i="${i}" onclick="useStyle(${i})">${esc(it.name || '(无名)')}</button>`).join('')
    + '</div></div>';
}

export function useStyle(i) {
  const it = STYLES[i];
  if (!it) return;
  const el = $(LIB_FIELD);
  if (el && it.prompt) {
    el.value = (el.value.trim() ? el.value.trim() + ', ' : '') + it.prompt.trim();
    el.dispatchEvent(new Event('input'));
  }
  const nid = PT[LIB_FIELD];
  const n = nid ? $(nid) : null;
  if (n && it.negative_prompt) {
    n.value = (n.value.trim() ? n.value.trim() + ', ' : '') + it.negative_prompt.trim();
  }
  closeLib();
  toast('已套用风格：' + (it.name || ''));
}

export function closeLib() {
  $('libModal').className = 'modal';
}

/* 面板外点击 / Esc 关闭 */
export function bindOverlays() {
  document.addEventListener('click', e => {
    if (e.target && e.target.id === 'libModal') closeLib();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeLib(); hideSug(); }
  });
}
