// Which elements stick out past the screen (sideways page scroll), per page, at a given width; optionally a
// screenshot of each page in light or dark. Drives headless Chrome over the DevTools protocol (node 22+).
// Real device emulation: Chrome's --window-size can't go below ~500 px, so CLI screenshots at 390 are a wider
// layout cropped, not a phone.
//   node overflow.mjs <chrome> <base url> <width> [--shots DIR --scheme light|dark] <hash>...
// checks.py `overflow` and `shots` run it against their own server.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = process.argv.slice(2), opt = k => { const i = args.indexOf(k); return i < 0 ? null : args.splice(i, 2)[1]; };
const shots = opt("--shots"), scheme = opt("--scheme") || "light";
const [chrome, base, width, ...pages] = args;
const port = 9300 + Math.floor(Math.random() * 500);
const proc = spawn(chrome, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${port}`,
  `--user-data-dir=${mkdtempSync(join(tmpdir(), "audit-web-"))}`, `--window-size=${width},900`, "about:blank"], { stdio: "ignore" });
const sleep = ms => new Promise(ok => setTimeout(ok, ms));
let target;
for (let i = 0; i < 50 && !target; i++) {
  try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(t => t.type === "page"); } catch { await sleep(200); }
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(ok => ws.addEventListener("open", ok));
let id = 0;
const pending = new Map();
ws.addEventListener("message", e => { const m = JSON.parse(e.data); pending.get(m.id)?.(m); pending.delete(m.id); });
const send = (method, params = {}) => new Promise(ok => { pending.set(++id, ok); ws.send(JSON.stringify({ id, method, params })); });
await send("Emulation.setDeviceMetricsOverride", { width: +width, height: 900, deviceScaleFactor: 1, mobile: +width < 600 });
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });

const probe = `(() => {
  const W = document.documentElement.clientWidth, out = [];
  const clipped = el => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const o = getComputedStyle(p).overflowX; if (o === "auto" || o === "scroll" || o === "hidden") return true; } return false; };
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.width && r.right > W + 1 && !clipped(el) && !el.closest(".drawer:not(.open)")) {
      const name = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\\s+/).join(".") : "");
      out.push(name + " right=" + Math.round(r.right) + " w=" + Math.round(r.width) + (el.children.length ? "" : " '" + el.textContent.trim().slice(0, 30) + "'"));
    }
  }
  return JSON.stringify({ scrollWidth: document.documentElement.scrollWidth, width: W, over: out.slice(0, 25) });
})()`;
for (const page of pages) {  // a track's panel is fixed to the window: a tall one shows its cues
  await send("Emulation.setDeviceMetricsOverride", { width: +width, height: page.includes("/") ? 3200 : 900, deviceScaleFactor: 1, mobile: +width < 600 });
  await send("Page.navigate", { url: `${base}/#${page}` });
  await sleep(4000);
  const r = JSON.parse((await send("Runtime.evaluate", { expression: probe, returnByValue: true })).result.result.value);
  console.log(`  #${page.slice(0, 40)}: page ${r.scrollWidth} px wide at ${r.width}${r.scrollWidth > r.width ? "  <-- scrolls sideways" : ""}`);
  for (const o of r.over) console.log(`    ${o}`);
  if (shots) {
    const name = `${shots}/${page.split("/")[0]}${page.includes("/") ? "-panel" : ""}-${scheme}-${width}.png`;
    const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(name, Buffer.from(shot.result.data, "base64"));
    console.log(`    ${name}`);
  }
}
ws.close();
proc.kill();
