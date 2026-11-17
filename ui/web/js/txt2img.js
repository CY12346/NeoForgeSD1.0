/* =====================================================================
   txt2img.js · 文生图
   尺寸预设、参数装配、出图。
   ===================================================================== */
import { S, $, setMsg, poll, loadBtn, loadHist, showImage, pickedLoras, adArgs } from './core.js';

export function applyPreset() {
  const [w, h] = $('preset').value.split('x');
  $('w1').value = w;
  $('h1').value = h;
}

export function t2iBody() {
  return {
    prompt: $('p1').value,
    negative: $('n1').value,
    width: +$('w1').value,
    height: +$('h1').value,
    steps: +$('s1').value,
    cfg: +$('c1').value,
    sampler: $('sampler').value,
    scheduler: $('scheduler').value,
    seed: +$('seed1').value,
    n: +$('nq').value,
    loras: pickedLoras(),
    adetailer: adArgs(),
    hires: {
      enabled: $('hrOn').checked,
      scale: +$('hrScale').value,
      denoise: +$('hrDn').value,
      upscaler: $('hrUp').value
    }
  };
}

export async function genImage() {
  loadBtn(false);
  setMsg('m1', '提交中…');
  $('bar1').style.width = '0';
  const j = await (await fetch('/api/image', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(t2iBody())
  })).json();
  if (j.error) {
    setMsg('m1', j.error, true);
    loadBtn(true);
    return;
  }
  poll(j.job_id, 'bar1', 'm1', r => {
    S.LASTJOB = r;
    setMsg('m1', `完成 · ${(r.image_urls || []).length} 张 · ${r.elapsed}s · seed ${r.seed ?? '-'}`);
    if ((r.image_urls || []).length) {
      showImage(r.image_urls[0],
        `${r.params.width}×${r.params.height} · ${r.params.steps}步 · ${r.params.sampler_name}/${r.params.scheduler}`);
    }
    loadHist();
    loadBtn(true);
  });
}

export function reuseSeed() {
  const s = S.LASTJOB && S.LASTJOB.seed;
  if (s == null) {
    alert('还没有出过图');
    return;
  }
  $('seed1').value = s;
  setMsg('m1', 'seed 已设为 ' + s);
}
