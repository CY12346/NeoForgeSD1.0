/* =====================================================================
   img2img.js · 图生图
   源图选择（上传 / 画布）、参数装配、开跑。
   ===================================================================== */
import { S, $, setMsg, poll, loadBtn, loadHist, showImage, readAsDataURL,
         pickedLoras, adArgs, stageAsB64, mode } from './core.js';

export function toggleSize() {
  const keep = $('sizeMode').value === 'keep';
  $('w2').disabled = keep;
  $('h2').disabled = keep;
}

export async function pickSrc(inp) {
  const f = inp.files[0];
  if (!f) return;
  S.SRC_B64 = await readAsDataURL(f);
  $('srcNote').textContent = '已选源图：' + f.name;
  setMsg('m2', '');
}

export function i2iBody(extra) {
  const keep = $('sizeMode').value === 'keep';
  return Object.assign({
    prompt: $('p2i').value,
    negative: $('n2i').value,
    width: keep ? 0 : +$('w2').value,
    height: keep ? 0 : +$('h2').value,
    size_mode: $('sizeMode').value,
    resize_mode: +$('resizeMode').value,
    steps: +$('s2').value,
    cfg: +$('c2').value,
    sampler: $('sampler').value,
    scheduler: $('scheduler').value,
    seed: +$('seed2').value,
    n: +$('nq2').value,
    denoise: +$('dn').value,
    image_b64: S.SRC_B64,
    loras: pickedLoras(),
    adetailer: adArgs()
  }, extra || {});
}

export async function doEdit() {
  if (!S.SRC_B64) {
    alert('先选源图（上传，或点「用画布这张」）');
    return;
  }
  loadBtn(false);
  setMsg('m2', '提交中…');
  $('bar2').style.width = '0';
  const j = await (await fetch('/api/img2img', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(i2iBody())
  })).json();
  if (j.error) {
    setMsg('m2', j.error, true);
    loadBtn(true);
    return;
  }
  poll(j.job_id, 'bar2', 'm2', r => {
    S.LASTJOB = r;
    setMsg('m2', `图生图完成 · ${(r.image_urls || []).length} 张 · ${r.elapsed}s · denoise ${$('dn').value}`);
    if ((r.image_urls || []).length) showImage(r.image_urls[0], '图生图 · denoise ' + $('dn').value);
    loadHist();
    loadBtn(true);
  });
}

/* 把画布那张当源图（局部重绘页签也用同一个入口） */
export async function useStage() {
  if (!S.STAGE_URL) {
    alert('画布还没有图');
    return false;
  }
  S.SRC_B64 = await stageAsB64();
  $('srcNote').textContent = '已用画布这张当源图';
  setMsg('m2', '已设为源图，可以直接点「图生图」');
  return true;
}

export async function editFromCanvas() {
  if (await useStage()) {
    mode('i2i');
    doEdit();
  }
}
