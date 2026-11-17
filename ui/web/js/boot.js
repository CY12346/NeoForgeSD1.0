/* =====================================================================
   boot.js · 装配入口
   两件事：把 parts/ 下的页面片段注入 DOM，然后初始化各模块。
   片段注入是串行的 —— group-create 先来，它里面的 #panes / #adv
   才是后面三个页签和高级选项的落点。
   ===================================================================== */
import * as core from './core.js';
import * as txt2img from './txt2img.js';
import * as img2img from './img2img.js';
import * as inpaint from './inpaint.js';
import * as upscale from './upscale.js';
import * as model from './model.js';
import * as asset from './asset.js';
import * as prompt from './prompt.js';

/* [片段文件, 注入到哪个容器] —— 顺序即视觉顺序 */
const PARTS = [
  ['parts/group-create.html', '#views'],
  ['parts/tab-txt2img.html', '#panes'],
  ['parts/tab-img2img.html', '#panes'],
  ['parts/tab-inpaint.html', '#panes'],
  ['parts/tab-advanced.html', '#adv'],
  ['parts/group-late.html', '#views'],
  ['parts/group-model.html', '#views'],
  ['parts/group-asset.html', '#views'],
  ['parts/overlays.html', '#overlays']
];

const MODULES = { core, txt2img, img2img, inpaint, upscale, model, asset, prompt };

async function mountAll() {
  for (const [file, target] of PARTS) {
    const host = document.querySelector(target);
    if (!host) throw new Error('页面缺容器：' + target);
    const res = await fetch('/web/' + file, { cache: 'no-store' });
    if (!res.ok) throw new Error(file + ' 取不到（HTTP ' + res.status + '）');
    host.insertAdjacentHTML('beforeend', await res.text());
  }
}

/* 内联 onclick="genImage()" 只认 window 上的名字：把各模块导出的函数挂上去。
   $ 也要挂 —— 片段里有 oninput="$('dnv').textContent=this.value" 这种写法。 */
function expose() {
  for (const mod of Object.values(MODULES)) {
    for (const [k, v] of Object.entries(mod)) {
      if (typeof v === 'function') window[k] = v;
    }
  }
}

function fail(msg) {
  const box = document.getElementById('views');
  if (box) {
    box.innerHTML = '<div class="card"><h2>页面装配失败</h2>'
      + '<pre>' + String(msg) + '</pre>'
      + '<div class="note">检查 web/parts/ 下的片段是否齐全，以及服务端 /web/ 静态路由是否放行。</div></div>';
  }
}

(async function boot() {
  try {
    await mountAll();
  } catch (e) {
    fail(e && e.message ? e.message : e);
    return;
  }

  expose();
  prompt.bindOverlays();

  const q = new URLSearchParams(location.search);
  core.showG(q.get('g') || 'create');
  core.mode(q.get('mode') === 'i2i' ? 'i2i' : 't2i');
  img2img.toggleSize();
  inpaint.bindCanvas();
  prompt.initPromptTools();

  core.loadStatus();
  core.loadHist();
  /* 没有作业在跑时，每 20 秒刷一次后端状态（顶栏那盏灯） */
  setInterval(() => { if (!core.S.TIMER) core.loadStatus(); }, 20000);
})();
