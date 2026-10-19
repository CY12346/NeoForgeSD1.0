/**
 * 前端功能自测（Edge headless + 原生 CDP，无 npm 依赖）
 * 跑法：node tests/fe_check.mjs [url]
 * 覆盖：提示词助手工具行 / 中→英 / 英→中 / 输入补全 / 词库 / 风格 / JS 报错
 * 输出：_smoke/fe_check.json + 控制台摘要
 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, rmSync } from "node:fs";

const URL_ = process.argv[2] || "http://127.0.0.1:8899/";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const PORT = 9333;
const PROFILE = "C:/Users/" + (process.env.USERNAME || "Public") + "/AppData/Local/Temp/neoforge-cdp";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await r.json();
      const t = list.find((x) => x.type === "page");
      if (t) return t;
    } catch {}
    await sleep(300);
  }
  throw new Error("CDP 没起来");
}

function connect(wsUrl) {
  return new Promise((res, rej) => {
    const ws = new WebSocket(wsUrl);
    const waiters = new Map();
    let id = 0;
    ws.onopen = () => res({
      send(method, params = {}) {
        const mid = ++id;
        ws.send(JSON.stringify({ id: mid, method, params }));
        return new Promise((r, j) => waiters.set(mid, { r, j }));
      },
      close: () => ws.close(),
    });
    ws.onerror = (e) => rej(new Error("ws error"));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && waiters.has(m.id)) {
        const w = waiters.get(m.id);
        waiters.delete(m.id);
        m.error ? w.j(new Error(JSON.stringify(m.error))) : w.r(m.result);
      }
    };
  });
}

const TEST_JS = String.raw`
(async () => {
  const out = { steps: [], errors: [] };
  const ok = (name, pass, detail) => out.steps.push({ name, pass: !!pass, detail: String(detail ?? "") });
  window.__errs = [];
  window.addEventListener("error", e => window.__errs.push(String(e.message)));
  const $ = id => document.getElementById(id);
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const until = async (fn, ms = 8000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (fn()) return true; await wait(120); } return false; };

  // 1) 工具行
  const rows = document.querySelectorAll(".prow");
  ok("提示词助手工具行", rows.length === 3, "rows=" + rows.length);
  const labels = [...document.querySelectorAll(".prow")].map(r => r.textContent.trim());
  ok("三处都有中→英/词库", labels.every(t => t.includes("中→英") && t.includes("词库")), labels.join(" | "));

  // 2) 中→英
  const p1 = $("p1");
  p1.value = "长发，微笑的少女，俯视，森林，逆光";
  await trDoc("p1", "zh2en");
  await wait(900);
  const v2 = p1.value;
  ok("中→英 覆盖为英文", /[a-z]/i.test(v2) && !/[\u4e00-\u9fff]/.test(v2.replace(/的/g, "")), v2);
  ok("中→英 关键 tag", /long hair/.test(v2) && /smile/.test(v2) && /forest/.test(v2) && /from above/.test(v2), v2);

  // 3) 英→中
  p1.value = "1girl, long_hair, smile, backlighting";
  await trDoc("p1", "en2zh");
  await wait(600);
  ok("英→中", /长发/.test(p1.value) && /微笑/.test(p1.value), p1.value);

  // 4) 补全
  p1.value = "long_h";
  p1.focus();
  p1.setSelectionRange(6, 6);
  await suggest("p1");
  await until(() => $("sug").style.display === "block", 5000);
  const items = document.querySelectorAll("#sug .it");
  ok("补全下拉出现", items.length > 0, "items=" + items.length);
  ok("补全命中 long hair", items[0] && /long_hair/.test(items[0].textContent), items[0] ? items[0].textContent : "");
  applySug(0);
  ok("补全插入后带逗号", /long hair,/.test(p1.value), p1.value);

  // 5) 词库
  await openLib("p1");
  await until(() => document.querySelectorAll("#libCats button").length > 0, 8000);
  const cats = document.querySelectorAll("#libCats button").length;
  const chips = document.querySelectorAll("#libList .chips button").length;
  ok("词库分类", cats >= 8, "cats=" + cats);
  ok("词库词条渲染", chips > 5, "chips=" + chips);
  const before = p1.value;
  const firstTag = document.querySelector("#libList .chips button");
  const tagTxt = firstTag ? firstTag.textContent : "";
  if (firstTag) firstTag.click();
  await wait(200);
  ok("词库点选可插入", p1.value.length > before.length, tagTxt + " → " + p1.value.slice(-40));
  // 搜索
  $("libQ").value = "微笑";
  renderLib();
  await wait(150);
  const hits = document.querySelectorAll("#libList .chips button").length;
  ok("词库搜索", hits > 0, "hits=" + hits);
  closeLib();

  // 6) 风格
  await openStyle("p1");
  await until(() => document.querySelectorAll("#libList .chips button").length > 0 || /没有可用/.test($("libList").textContent), 8000);
  const styleBtns = document.querySelectorAll("#libList .chips button").length;
  ok("风格面板有预设", styleBtns > 0, "styles=" + styleBtns);
  closeLib();

  // 7) 反向词框也能聚焦（中→英目标切换）
  const n1 = $("n1");
  n1.focus();
  // headless 无窗口焦点时 Chrome 不派发 focus/focusin（真实点选会），这里手动派发验证委托逻辑
  n1.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
  await wait(120);
  ok("focusin 委托切换目标", LAST_FIELD === "n1", LAST_FIELD + " active=" + (document.activeElement||{}).id);
  $("p1").focus();
  await wait(100);

  ok("页面无 JS 报错", window.__errs.length === 0, window.__errs.join(" / "));
  out.errors = window.__errs;
  return out;
})()
`;

(async () => {
  try { rmSync(PROFILE, { recursive: true, force: true }); } catch {}
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`, "about:blank",
  ], { stdio: "ignore" });

  let res = { ok: false, steps: [], error: "" };
  try {
    const target = await getTarget();
    const cdp = await connect(target.webSocketDebuggerUrl);
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    await cdp.send("Page.navigate", { url: URL_ });
    await sleep(2500);
    const r = await cdp.send("Runtime.evaluate", {
      expression: TEST_JS, awaitPromise: true, returnByValue: true, timeout: 60000,
    });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600));
    res = r.result.value || {};
    cdp.close();
  } catch (e) {
    res.error = String(e).slice(0, 600);
  } finally {
    edge.kill();
  }
  mkdirSync("_smoke", { recursive: true });
  writeFileSync("_smoke/fe_check.json", JSON.stringify(res, null, 2), "utf8");
  const steps = res.steps || [];
  const bad = steps.filter((s) => !s.pass);
  for (const s of steps) console.log(`${s.pass ? "PASS" : "FAIL"}  ${s.name}  ${s.detail}`);
  if (res.error) console.log("ERROR:", res.error);
  console.log(`\n${bad.length === 0 && !res.error ? "ALL PASS" : "FAILED"}  ${steps.length - bad.length}/${steps.length}`);
  process.exit(bad.length === 0 && !res.error ? 0 : 1);
})();
