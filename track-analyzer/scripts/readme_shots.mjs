// The README's screenshots: Overview and one track's panel (analysis, waveform and cues), light and dark, at 2x.
// Track names, artists, albums and file paths are swapped for made-up ones in the browser before each capture,
// so the pictures show no one's collection. Drives headless Chrome over the DevTools protocol (node 22+).
//   node readme_shots.mjs <chrome> <base url> <out dir> <track path>
// readme_shots.py runs it against its own server.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [chrome, base, out, track] = process.argv.slice(2);
const port = 9300 + Math.floor(Math.random() * 500);
const proc = spawn(chrome, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${port}`,
  `--user-data-dir=${mkdtempSync(join(tmpdir(), "readme-shots-"))}`, "about:blank"], { stdio: "ignore" });
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
const evaluate = async expression => (await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true })).result.result.value;

// Runs in the page before its own script: wide panel, and every API answer's names replaced as it arrives.
const ANON = `(() => {
  try { localStorage.setItem("ta-drawer-wide", "1"); localStorage.removeItem("ta-theme"); } catch (e) {}
  const A = ["Nova", "Kaelo", "Mirren", "Sola", "Vantage", "Lumen", "Oskar Vale", "Ilse", "Tidewalk", "Arden", "Juno Park", "Halcyon",
    "Rhea Moss", "Calder", "Fenwick", "Seren", "Atlas Grey", "Maren", "Velo", "Quill", "Ember Lane", "Orrin", "Talis", "Wren"];
  const W1 = ["Velvet", "Neon", "Silver", "Midnight", "Golden", "Distant", "Electric", "Hidden", "Endless", "Crystal", "Northern", "Paper",
    "Solar", "Glass", "Wild", "Quiet", "Burning", "Open", "Falling", "Lunar"];
  const W2 = ["Horizon", "Tide", "Signal", "Garden", "Engine", "Mirage", "Pulse", "Harbour", "Echo", "Skyline", "Current", "Orbit",
    "Bloom", "Static", "Meridian", "Rain", "Circuit", "Ritual", "Shore", "Lights"];
  const h = s => { let x = 2166136261; for (const c of s) x = Math.imul(x ^ c.codePointAt(0), 16777619) >>> 0; return x; };
  const MAP = new Map(), SAFE = new Set();  // SAFE: genre, style and instrument words (an artist may be called "Techno")
  const fake = (real, make) => { if (!real || typeof real !== "string") return real; if (!MAP.has(real)) MAP.set(real, make(real)); return MAP.get(real); };
  const title = s => { const m = s.match(/\\s*(\\([^)]*\\))\\s*$/), x = h(s); return W1[x % W1.length] + " " + W2[(x >>> 8) % W2.length] + (m ? " " + m[1] : ""); };
  const artist = s => s.split(/\\s*(?:,|&| x | feat\\.? | ft\\. )\\s*/i).slice(0, 3).map(p => A[h(p) % A.length]).join(", ");
  const walk = o => {
    if (Array.isArray(o)) return o.forEach(walk);
    if (!o || typeof o !== "object") return;
    for (const k of ["genre", "genre_2", "genre_3", "my_genre", "tag_genre", "styles", "instruments", "label"])
      if (typeof o[k] === "string") o[k].split(/[;,&]|\\s[\\d.]+/).forEach(w => w.trim() && SAFE.add(w.trim()));
    const row = typeof o.path === "string" || "title" in o || "artist" in o;  // a track, not e.g. a tempo view's folder
    if (typeof o.title === "string") o.title = fake(o.title, title);
    if (typeof o.artist === "string") o.artist = fake(o.artist, artist);
    if (typeof o.album === "string") o.album = fake(o.album, () => "Compilation " + (h(o.album) % 90 + 10));
    if (row) for (const k of ["filename", "file", "folder"]) if (typeof o[k] === "string") o[k] = fake(o[k], () => "track-" + (h(o[k]) % 900 + 100));
    if (typeof o.path === "string" && (o.title || o.artist)) MAP.set("__path__" + o.path, (o.artist || "Artist") + " - " + (o.title || "Track") + ".mp3");
    Object.values(o).forEach(walk);
  };
  const orig = window.fetch;
  window.fetch = async (...a) => {
    const r = await orig(...a);
    if (!String(a[0]?.url || a[0]).includes("/api/") || !(r.headers.get("content-type") || "").includes("json")) return r;
    const d = await r.json();
    walk(d);
    return new Response(JSON.stringify(d), { status: r.status, statusText: r.statusText, headers: r.headers });
  };
  // Anything a name still reaches (text built from a file name, a tooltip) and every path, just before a capture.
  window.__scrub = () => {
    const pairs = [...MAP].filter(([k]) => !k.startsWith("__path__") && k.length > 2 && ![...SAFE].some(w => w.includes(k))).sort((a, b) => b[0].length - a[0].length);
    const clean = s => { for (const [k, v] of pairs) s = s.split(k).join(v); return s.replace(/(\\/Users|\\/Volumes|\\/home)\\/\\S*/g, p => p.includes("/data/") ? "data/" + p.split("/data/").pop() : "~/Music/Tracks"); };
    const dd = document.querySelector("#drawer.open .facts dd.mono"), p = document.querySelector("#drawer")?.dataset.path;
    if (dd && p) dd.textContent = "~/Music/Tracks/" + (MAP.get("__path__" + p) || "track.mp3");
    const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n; (n = tw.nextNode());) { const c = clean(n.nodeValue); if (c !== n.nodeValue) n.nodeValue = c; }
    for (const el of document.querySelectorAll("[title],[data-tip],input[value]"))
      for (const at of ["title", "data-tip", "value"]) if (el.hasAttribute(at)) el.setAttribute(at, clean(el.getAttribute(at)));
    // The panel's Hold switches are about writing, not what the page shows: left out of the picture.
    document.querySelector("#drawer .holds")?.closest(".section")?.style.setProperty("display", "none");
  };
})()`;
await send("Page.enable");
await send("Page.addScriptToEvaluateOnNewDocument", { source: ANON });

// in page coordinates, as the screenshot clip takes them (the page may be scrolled under the fixed panel)
const R = `const R = e => { const b = e.getBoundingClientRect(), y = b.y + scrollY; return { x: b.x + scrollX, y, w: b.width, h: b.height, b: y + b.height }; };
  const sec = h3 => [...document.querySelectorAll("#drawer .section")].find(s => s.querySelector("h3")?.textContent.startsWith(h3));`;
// clip: an expression giving {x, y, w, h}, measured after the scrub (it hides a section, so the layout moves)
async function shot(name, expr) {
  await evaluate("window.__scrub()");
  await sleep(500);
  const clip = await evaluate(expr);
  const r = await send("Page.captureScreenshot", { format: "png", clip: { x: clip.x, y: clip.y, width: clip.w, height: clip.h, scale: 1 } });
  writeFileSync(join(out, name), Buffer.from(r.result.data, "base64"));
  console.log(`  ${join(out, name)}  ${Math.round(clip.w)}x${Math.round(clip.h)}`);
}
const until = async (test, ms) => { for (let t = 0; t < ms && !(await evaluate(test)); t += 500) await sleep(500); };

for (const scheme of ["light", "dark"]) {
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });

  await send("Emulation.setDeviceMetricsOverride", { width: 1400, height: 1400, deviceScaleFactor: 2, mobile: false });
  await send("Page.navigate", { url: "about:blank" });
  await send("Page.navigate", { url: `${base}/#overview` });
  await until(`!!document.querySelector("main .card, main section") && !document.querySelector(".loading")`, 15000);
  await sleep(1500);
  await shot(`overview-${scheme}.png`, `(() => { ${R} const b = Math.max(...[...document.querySelectorAll("main > *, main section, main .card")].map(e => R(e).b));
    return { x: 0, y: 0, w: 1400, h: Math.min(b + 20, 1400) }; })()`);

  // Tall window: the panel is fixed to it, so all of it is on screen at once.
  await send("Emulation.setDeviceMetricsOverride", { width: 1400, height: 4200, deviceScaleFactor: 2, mobile: false });
  await send("Page.navigate", { url: "about:blank" });
  await send("Page.navigate", { url: `${base}/#tracks/${encodeURIComponent(track)}` });
  // waveform drawn, loop parts heard (Demucs, the first time a few seconds a loop), the grid checked
  await until(`!!document.querySelector("#cuebox canvas") && !/Listening for|Checking where/.test(document.querySelector("#drawer").innerText)`, 90000);
  await sleep(3000);
  // the analysis: from the panel's top down to the beat grid
  await shot(`track-${scheme}.png`, `(() => { ${R} const d = R(document.querySelector("#drawer")), g = R(sec("Beat grid")); return { x: d.x, y: d.y, w: d.w, h: g.y - d.y }; })()`);
  // the deck and waveform: from "Cues and phrases" down to the Keys / Legend line under the waveform
  await shot(`cues-${scheme}.png`, `(() => { ${R} const d = R(document.querySelector("#drawer")), c = R(sec("Cues and phrases"));
    const l = [...document.querySelectorAll("#cuebox summary")].find(e => /Legend/.test(e.textContent));
    return { x: d.x, y: c.y - 12, w: d.w, h: (l ? R(l).b + 12 : c.b) - c.y + 12 }; })()`);
}
ws.close();
proc.kill();
