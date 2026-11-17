/* =====================================================================
   model.js · 模型组
   底模 / VAE / CLIP skip 切换。采样器与调度器由 core.loadStatus 填充。
   ===================================================================== */
import { $, setMsg, loadStatus } from './core.js';

const post = (url, body) => fetch(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body)
}).then(r => r.json());

export async function setModel() {
  const name = $('model').value;
  if (!name) return;
  setMsg('mModel', '切换底模…（10–30 秒）');
  const j = await post('/api/model', { name });
  setMsg('mModel', j.error ? ('失败：' + j.error) : ('已切换：' + j.model), !!j.error);
  loadStatus();
}

export async function setVae() {
  const name = $('vae').value;
  setMsg('mModel', '切换 VAE…');
  const j = await post('/api/vae', { name });
  setMsg('mModel', j.error ? ('VAE 失败：' + j.error) : ('VAE 已设为 ' + (j.opts || {}).vae), !!j.error);
  loadStatus();
}

export async function applyClip() {
  const v = +$('clipSkip').value || 1;
  const j = await post('/api/options', { clip_skip: v });
  setMsg('mModel', j.error ? ('CLIP skip 失败：' + j.error) : ('CLIP skip 已设为 ' + v), !!j.error);
  loadStatus();
}
