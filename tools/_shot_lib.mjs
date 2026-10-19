/** 截图：打开词库面板后的页面（Edge headless + CDP） */
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const PORT = 9334, sleep = (ms) => new Promise(r => setTimeout(r, ms));
const prof = "C:/Users/" + (process.env.USERNAME || "Public") + "/AppData/Local/Temp/neoforge-shot";
const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${prof}`, "--window-size=1500,1000", "about:blank"], { stdio: "ignore" });
let t;
for (let i = 0; i < 60; i++) {
  try { const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); t = l.find(x => x.type === "page"); if (t) break; } catch {}
  await sleep(300);
}
const ws = new WebSocket(t.webSocketDebuggerUrl);
const waits = new Map(); let id = 0;
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && waits.has(m.id)) { waits.get(m.id)(m.result); waits.delete(m.id); } };
const send = (method, params = {}) => new Promise(r => { const i = ++id; waits.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await new Promise(r => { ws.onopen = r; });
await send("Page.enable"); await send("Runtime.enable");
await send("Page.navigate", { url: "http://127.0.0.1:8899/" });
await sleep(3000);
await send("Runtime.evaluate", { expression: "openLib('p1')", awaitPromise: true });
await sleep(1800);
const shot = await send("Page.captureScreenshot", { format: "png" });
writeFileSync("_smoke/ui_promptlib.png", Buffer.from(shot.data, "base64"));
console.log("saved _smoke/ui_promptlib.png");
edge.kill();
process.exit(0);
