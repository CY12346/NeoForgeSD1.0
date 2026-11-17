/* =====================================================================
   upscale.js · 放大 / 修脸（extras）
   ===================================================================== */
import { S, $, setMsg, poll, loadBtn, loadHist, showImage, readAsDataURL, stageAsB64 } from './core.js';

export async function pickUp(inp) {
  const f = inp.files[0];
  if (!f) return;
  S.SRC_UP = await readAsDataURL(f);
  setMsg('m3', '已选源图：' + f.name);
}

export async function doUpscale() {
  if (!S.SRC_UP && !S.SRC_B64) {
    alert('先选源图（上传，或点「用画布这张」）');
    return;
  }
  const src = S.SRC_UP || S.SRC_B64;
  loadBtn(false);
  setMsg('m3', '放大中…');
  $('bar3').style.width = '0';
  const j = await (await fetch('/api/upscale', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      image_b64: src,
      upscaler: $('ups').value,
      scale: +$('scale').value,
      gfpgan: +$('gfpgan').value,
      codeformer: +$('codeformer').value
    })
  })).json();
  if (j.error) {
    setMsg('m3', j.error, true);
    loadBtn(true);
    return;
  }
  poll(j.job_id, 'bar3', 'm3', r => {
    setMsg('m3', `放大完成 · ${r.elapsed}s`);
    if ((r.image_urls || []).length) showImage(r.image_urls[0], '放大 ' + $('scale').value + '× · ' + $('ups').value);
    loadHist();
    loadBtn(true);
  });
}

export async function upFromCanvas() {
  if (!S.STAGE_URL) {
    alert('画布还没有图');
    return;
  }
  S.SRC_UP = await stageAsB64();
  setMsg('m3', '已用画布这张');
  doUpscale();
}
