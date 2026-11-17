/* =====================================================================
   inpaint.js · 局部重绘
   双层 canvas：底层画原图，上层涂蒙版；提交时把蒙版导成 PNG 贴进 img2img。
   ===================================================================== */
import { S, $, setMsg, poll, loadBtn, loadHist, showImage, readAsDataURL,
         stageAsB64, pickedLoras, mode } from './core.js';

const ipCtx = () => $('ipBase').getContext('2d');
const maskCtx = () => $('ipMask').getContext('2d');

export async function ipLoad(dataUrl, label) {
  const img = new Image();
  await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = dataUrl; });
  const b = $('ipBase'), m = $('ipMask');
  b.width = img.width; b.height = img.height;
  m.width = img.width; m.height = img.height;
  ipCtx().drawImage(img, 0, 0);
  maskCtx().fillStyle = '#000';
  maskCtx().fillRect(0, 0, img.width, img.height);
  $('ipwrap').hidden = false;
  S.IP_READY = true;
  S.SRC_B64 = dataUrl;
  $('ipNote').textContent =
    `已载入 ${img.width}×${img.height}（${label}）。涂抹要重绘的区域后点「局部重绘」。`;
}

export async function pickIp(inp) {
  const f = inp.files[0];
  if (f) ipLoad(await readAsDataURL(f), f.name);
}

export async function ipFromCanvas() {
  if (!S.STAGE_URL) {
    alert('画布还没有图');
    return;
  }
  await ipLoad(await stageAsB64(), '画布');
  mode('inp');
}

/* 屏幕坐标 → 画布像素坐标（画布被 CSS 缩放过，必须换算） */
function pos(e, cv) {
  const r = cv.getBoundingClientRect();
  return {
    x: (e.clientX - r.left) * cv.width / r.width,
    y: (e.clientY - r.top) * cv.height / r.height
  };
}

export function stroke(e) {
  if (!S.painting) return;
  const m = $('ipMask'), ctx = maskCtx(), p = pos(e, m), w = +$('brush').value;
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = w;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.lineTo(p.x, p.y);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(p.x, p.y);
}

export function clearMask() {
  const m = $('ipMask');
  if (!m.width) return;
  maskCtx().fillStyle = '#000';
  maskCtx().fillRect(0, 0, m.width, m.height);
}

/* 指针事件只绑一次，交给 boot 在片段注入后调用 */
export function bindCanvas() {
  const cv = $('ipMask');
  if (!cv) return;
  cv.addEventListener('pointerdown', e => {
    S.painting = true;
    const p = pos(e, cv), c = maskCtx();
    c.beginPath();
    c.moveTo(p.x, p.y);
    cv.setPointerCapture(e.pointerId);
  });
  cv.addEventListener('pointermove', stroke);
  cv.addEventListener('pointerup', () => { S.painting = false; });
  cv.addEventListener('pointerleave', () => { S.painting = false; });
}

export async function doInpaint() {
  if (!S.IP_READY) {
    alert('先载入一张图（上传，或用画布这张）');
    return;
  }
  const j = await (await fetch('/api/img2img', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt: $('pIp').value,
      negative: $('nIp').value,
      image_b64: S.SRC_B64,
      mask_b64: $('ipMask').toDataURL('image/png'),
      size_mode: 'keep',
      steps: +$('sIp').value,
      cfg: +$('cIp').value,
      sampler: $('sampler').value,
      scheduler: $('scheduler').value,
      denoise: +$('dnIp').value,
      n: +$('nqIp').value,
      seed: -1,
      mask_blur: +$('maskBlur').value,
      inpainting_fill: +$('inpaintFill').value,
      loras: pickedLoras(),
      adetailer: { enabled: false }
    })
  })).json();
  if (j.error) {
    setMsg('m4', j.error, true);
    return;
  }
  $('bar4').style.width = '0';
  setMsg('m4', '提交中…');
  poll(j.job_id, 'bar4', 'm4', r => {
    setMsg('m4', `局部重绘完成 · ${(r.image_urls || []).length} 张 · ${r.elapsed}s`);
    if ((r.image_urls || []).length) showImage(r.image_urls[0], '局部重绘 · denoise ' + $('dnIp').value);
    loadHist();
  });
}
