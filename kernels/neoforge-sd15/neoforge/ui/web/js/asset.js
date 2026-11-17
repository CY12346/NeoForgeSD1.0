/* =====================================================================
   asset.js · 资产组
   历史缩略图复用参数、从 PNG 里读参数回填文生图表单。
   ===================================================================== */
import { S, $, setMsg, readAsDataURL, stageAsB64, showG, mode } from './core.js';

export async function pickInfo(inp) {
  const f = inp.files[0];
  if (!f) return;
  S.INFO_B64 = await readAsDataURL(f);
  $('infoBox').textContent = '已选：' + f.name + '（点「读参数」）';
}

export async function infoFromCanvas() {
  if (!S.STAGE_URL) {
    alert('画布还没有图');
    return;
  }
  S.INFO_B64 = await stageAsB64();
  $('infoBox').textContent = '已用画布这张（点「读参数」）';
}

export async function readInfo() {
  if (!S.INFO_B64) {
    alert('先选一张图');
    return;
  }
  setMsg('mInfo', '读取中…');
  const j = await (await fetch('/api/pnginfo', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_b64: S.INFO_B64 })
  })).json();
  if (j.error) {
    setMsg('mInfo', j.error, true);
    return;
  }
  $('infoBox').textContent = j.info || '（这张图没有参数：可能被压缩过/不是 SD 出的）';
  setMsg('mInfo', '读取完成');
}

export function applyInfo() {
  const t = $('infoBox').textContent || '';
  const g = k => {
    const m = t.match(new RegExp('^' + k + ':\\s*(.*)$', 'm'));
    return m ? m[1].trim() : null;
  };
  const prompt = t.split('\n')[0] || '';
  const neg = g('Negative prompt');
  if (prompt && prompt.indexOf(':') < 0) $('p1').value = prompt;
  if (neg) $('n1').value = neg;
  const s = g('Steps'), cfg = g('CFG scale'), seed = g('Seed'), sm = g('Size');
  if (s) $('s1').value = parseInt(s) || 20;
  if (cfg) $('c1').value = parseFloat(cfg) || 7;
  if (seed && /^\d+$/.test(seed)) $('seed1').value = parseInt(seed);
  if (sm) {
    const [w, h] = sm.split('x').map(Number);
    if (w) $('w1').value = w;
    if (h) $('h1').value = h;
  }
  showG('create');
  mode('t2i');
  setMsg('mInfo', '已回填（提示词/反向/步数/CFG/seed/尺寸）');
}

/* 复用历史里最后一张的参数 */
export function reuseParams() {
  if (!S.LASTREC || !S.LASTREC.params) {
    alert('还没有历史');
    return;
  }
  const p = S.LASTREC.params;
  $('p1').value = p.prompt || '';
  $('n1').value = p.negative_prompt || '';
  $('w1').value = p.width || 512;
  $('h1').value = p.height || 512;
  $('s1').value = p.steps || 20;
  $('c1').value = p.cfg_scale || 7;
  if (p.sampler_name) $('sampler').value = p.sampler_name;
  if (p.scheduler) $('scheduler').value = p.scheduler;
  $('seed1').value = S.LASTREC.seed ?? -1;
  showG('create');
  mode('t2i');
  setMsg('m1', '已回填最后一张的参数（seed ' + $('seed1').value + '）');
}
