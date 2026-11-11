const COLOURS = ["Red", "Orange", "Yellow", "Green", "Blue", "Violet", "Magenta"];
const DJ = () => S.libs?.app_name || "Traktor";  // the DJ app chosen on the Setup tab
const S = { lib: "native", libs: null, tracks: {}, trackSort: { key: "track", dir: 1 }, page: "overview" };
try { S.lib = localStorage.getItem("ta-lib") || "native"; } catch (e) {}
try { const t = localStorage.getItem("ta-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}

// ---------- helpers ----------
const $ = (s, el = document) => el.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => v == null || v === "" ? "–" : typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(d)) : esc(v);
const pct = v => v == null ? "–" : Math.round(v * 100) + "%";
// a colour and its neighbours on the scale: "Blue–Magenta" for Violet, "Red–Orange" for Red
const nearRange = c => { const i = COLOURS.indexOf(c); return i < 0 ? c : `${COLOURS[Math.max(0, i - 1)]}–${COLOURS[Math.min(COLOURS.length - 1, i + 1)]}`; };
const mmss = s => { if (s == null) return "–"; const t = Math.round(s); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`; };  // 59.6 s is 1:00, not 0:60
const sw = (c, predicted) => c ? `<span class="sw sw-${esc(c)}${predicted ? " pred" : ""}"></span>` : "";
const colour = (c, predicted) => c ? `<span class="colour" data-tip="${predicted ? "Predicted by the colour model (not set by you)" : `Your colour in ${DJ()}`}">${sw(c, predicted)}${esc(c)}${predicted ? "?" : ""}</span>` : `<span class="dim">–</span>`;
const trackName = t => `<div class="trk"><div class="t">${esc(t.title || t.filename || t.file || "")}</div>${t.artist ? `<div class="a">${esc(t.artist)}</div>` : ""}</div>`;
function hypeBadge(h) {
  if (h == null) return `<span class="dim">–</span>`;
  const a = 0.12 + (h - 1) / 9 * 0.7;
  return `<span class="hype" style="background:color-mix(in srgb, var(--bar) ${Math.round(a * 100)}%, transparent);color:var(--text)">${h}</span>`;
}
const parseScores = s => (s || "").split(";").map(x => x.trim()).filter(Boolean).map(x => {
  const m = x.match(/^(.*)\s+(-?[\d.]+)$/); return m ? { label: m[1], value: +m[2] } : { label: x, value: null };
});

const SNAP = window.SNAPSHOT || null;
const LOCK = `<svg width="11" height="12" viewBox="0 0 11 12" aria-hidden="true"><rect x="1" y="5" width="9" height="7" rx="1.5" fill="currentColor"/><path d="M3 5V3.5a2.5 2.5 0 0 1 5 0V5" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>`;
const lock = what => `<span class="lock" data-tip="${what} held: kept as it is when writing to files, ${DJ()} or Lexicon">${LOCK}</span>`;
const GUESS_TIP = () => `The app guessed this colour into ${DJ()} and nobody has checked it yet, so the colour model doesn't learn from it. Change it in ${DJ()}, or mark it right here, and it counts as yours.`;
const guessMark = () => `<span class="guess" data-tip="${GUESS_TIP()}">?</span>`;
if (SNAP) {
  $("#mode").textContent = `· snapshot of ${SNAP.made}`;
  $("#export").remove();
  document.querySelector('#nav a[href="#files"]').remove();
  document.querySelector('#nav a[href="#setup"]').remove();
} else $("#mode").textContent = "· local";
function snapHype(h, p) {
  const src = p.gems == 1 ? h.gems : p.least == 1 ? h.least : h.most;
  if (src.error) return src;
  const keys = p.key ? camelotNeighbours(p.key) : null, bpm = p.bpm ? +p.bpm : null, tol = +(p.tolerance || 4);
  const tracks = src.tracks.filter(t => (!p.colour || (t.colour || "").toLowerCase() === p.colour.toLowerCase())
    && (!bpm || (t.bpm && Math.abs(t.bpm - bpm) <= bpm * tol / 100)) && (!keys || keys.includes(t.camelot)));
  const filters = [p.colour && `colour ${p.colour}`, bpm && `BPM ${bpm} ±${tol}%`, keys && `Camelot ${keys.join(", ")}`,
    p.gems == 1 && "gems: hype 8+ but 4 stars or fewer, or played at most once"].filter(Boolean);
  return { scored: src.scored, matches: tracks.length, showing: Math.min(+p.limit || 100, tracks.length), order: p.least == 1 ? "least hype first" : "most hype first",
    filters: filters.length ? filters : ["none"], tracks: tracks.slice(0, +p.limit || 100) };
}
function snapApi(path, p) {
  const D = SNAP, lib = p.lib || "native";
  const res = (() => { switch (path) {
    case "libraries": return D.libraries;
    case "stats": return D.stats[lib];
    case "tracks": return D.tracks[lib];
    case "suggest": {
      const sg = D.suggest[lib]?.[p.path];
      if (!sg || sg.error) return sg;
      const by = new Map(D.tracks[lib].tracks.map(t => [t.path, t]));
      return { rule: sg.rule, matches: sg.matches, tracks: sg.paths.map(x => by.get(x)).filter(Boolean).map(t => ({ ...t, bpm: t.mix_bpm, camelot: t.mix_camelot })) };
    }
    case "hype": return D.hype[lib] && snapHype(D.hype[lib], p);
    case "colours/review": return D.review;
    case "colours/explain": return D.explain;
    case "colours/kept": return D.kept;
    case "tempo/report": return D.tempo[String(+p.bpm)];
    case "cues": return D.cues?.[p.path] || { error: "No cue suggestions for this track in this snapshot." };
    case "saved": return { files: D.saved, folders: ["this snapshot"] };
    case "saved-file": return D.files[p.id];
    case "mixes-report": return D.mixes_report || { error: "This snapshot was made before the report across mixes existed." };
  } })();
  if (!res) throw new Error("Not in this snapshot.");
  if (res.error) throw new Error(res.error);
  return res;
}
// an answer's JSON; one that isn't JSON (a crash, a page from something else on the port) becomes its status
const readJSON = async r => { try { return await r.json(); } catch (e) { return { error: `The server answered ${r.status} ${r.statusText}` }; } };
async function acceptColours(paths) {  // guessed colours checked and right: yours from now on
  const d = await postJSON("/api/colours/accept", { paths });
  const set = new Set(paths);
  for (const list of Object.values(S.tracks)) for (const t of list) if (set.has(t.path)) delete t.colour_guess;
  SETUP = null;  // the Setup tab's count of new colours to learn from changed
  return d;
}
async function hold(paths, kind, on) {
  const d = await postJSON("/api/hold", { paths, kind, on });
  const set = new Set(paths);
  for (const list of Object.values(S.tracks)) for (const t of list) if (set.has(t.path)) { if (on) t["held_" + kind] = true; else delete t["held_" + kind]; }
  return d;
}
async function api(path, params = {}, { signal } = {}) {
  if (SNAP) return snapApi(path, params);
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  let r;
  for (let attempt = 0; ; attempt++) {
    try { r = await fetch(`/api/${path}${q.toString() ? "?" + q : ""}`, { signal }); break; }
    catch (e) { // no answer at all: the server is restarting, or was stopped
      if (e.name === "AbortError") throw e;  // given up on by the page (another track opened)
      if (attempt >= 2) throw new Error(`Can't reach Puppi3's Track Analyzer: it was stopped or is restarting. Start it again: double-click Start Puppi3's Track Analyzer.${/Windows/.test(navigator.userAgent) ? "bat" : "command"} in its folder, then press Retry.`);
      await new Promise(ok => setTimeout(ok, 600 * (attempt + 1)));
    }
  }
  const d = await readJSON(r);
  if (!r.ok) throw new Error(d?.error || `The server answered ${r.status} ${r.statusText}`);
  return d;
}
const errBox = e => `<div class="err">${esc(e.message || e)}${SNAP ? "" : ` <button style="margin-left:8px" onclick="route()">Retry</button>`}</div>`;
// a friendly "nothing here yet": what it is, what to do, and (live only) a link to do it
const empty = (title, body, action = "", card = true) => `<div class="${card ? "card " : ""}empty"><div class="et">${title}</div><div class="eb small">${body}</div>${action ? `<div class="ea">${action}</div>` : ""}</div>`;
const toSetup = text => SNAP ? "" : `<a class="btn" href="#setup">${text} →</a>`;  // a snapshot has no Setup

function bars(items, { max, fmtVal = v => fmt(v), colourLabels = false } = {}) {
  if (!items.length) return `<div class="dim">Nothing yet.</div>`;
  const m = max ?? Math.max(...items.map(i => i.value || 0), 1e-9);
  // an item with `go` (a Tracks filter) opens the Tracks tab filtered that way when clicked
  // one with `sel` (a mix's genre, style or mood) shows where it is in the mix (`on` = the one shown)
  const go = i => i.go ? ` go" data-go="${esc(JSON.stringify(i.go))}` : i.sel ? ` go${i.on ? " on" : ""}" data-mixsel="${esc(JSON.stringify(i.sel))}` : "";
  const tipOf = i => esc((i.tip || `${i.label}: ${fmtVal(i.value)}`) + (i.go ? "\nClick to show these tracks" : i.sel ? `\nClick to ${i.on ? "stop showing it" : "show where it is in the mix"}` : ""));
  return `<div class="bars${items.some(i => i.on) ? " has-on" : ""}">${items.map(i => `
    <div class="lab${go(i)}" title="${esc(i.label)}">${colourLabels ? sw(i.label) : ""}${esc(i.label)}</div>
    <div class="track row-h${go(i)}" data-tip="${tipOf(i)}"><div class="fill" style="width:${Math.max(0, (i.value || 0) / m * 100)}%"></div></div>
    <div class="val">${i.valText ?? fmtVal(i.value)}</div>`).join("")}</div>`;
}
function columns(items) {
  const m = Math.max(...items.map(i => i.value), 1);
  const every = Math.ceil(items.length / 12);
  return `<div class="cols">${items.map(i => `<div class="c${i.go ? ` go" data-go="${esc(JSON.stringify(i.go))}` : ""}" style="height:${i.value / m * 100}%" data-tip="${esc(i.label)}: ${i.value}${i.go ? "\nClick to show these tracks" : ""}"></div>`).join("")}</div>
    <div class="cols-x">${items.map((i, k) => `<span>${k % every ? "" : esc(i.short ?? i.label)}</span>`).join("")}</div>`;
}
function table(cols, rows, { onRow, rowAttr } = {}) {
  return `<div class="tablewrap"><table><thead><tr>${cols.map(c => `<th class="${c.r ? "r" : ""}">${c.raw ? c.h : esc(c.h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr ${rowAttr ? rowAttr(r, i) : ""}>${cols.map(c => `<td class="${c.r ? "r num" : ""}">${c.f(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

// tooltip
const tip = $("#tip");
document.addEventListener("mouseover", e => {
  const el = e.target.closest("[data-tip]");
  if (!el) { tip.style.display = "none"; return; }
  tip.textContent = el.dataset.tip; tip.style.display = "block";
});
document.addEventListener("mousemove", e => {
  if (tip.style.display !== "block") return;
  const x = Math.max(8, Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8)), y = e.clientY + 16 + tip.offsetHeight > innerHeight ? e.clientY - tip.offsetHeight - 10 : e.clientY + 16;
  tip.style.left = x + "px"; tip.style.top = y + "px";
});

// theme: follow the system → light → dark → system
const THEMES = { "": ["◐", "Theme: follows your system (click for light)"], light: ["☀", "Theme: light (click for dark)"], dark: ["☾", "Theme: dark (click to follow your system)"] };
function themeButton() { const [icon, tipText] = THEMES[document.documentElement.dataset.theme] || THEMES[""]; $("#theme").textContent = icon; $("#theme").dataset.tip = tipText; $("#theme").setAttribute("aria-label", tipText); }
$("#theme").onclick = () => {
  const root = document.documentElement, next = { light: "dark", dark: "" }[root.dataset.theme] ?? "light";
  if (next) root.dataset.theme = next; else delete root.dataset.theme;
  try { if (next) localStorage.setItem("ta-theme", next); else localStorage.removeItem("ta-theme"); } catch (e) {}
  themeButton(); tip.textContent = $("#theme").dataset.tip;
};
themeButton();

