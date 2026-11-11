// ---------- files ----------
async function pageFiles(main, id) {
  const me = S.routeId, L = await getLogs(true);
  if (me !== S.routeId) return;
  const gone = id && !L.files.some(f => f.id === id);  // a link to a file that was deleted or moved
  if (gone) id = null;
  main.innerHTML = `<div class="split">
    <div class="card list" style="padding:8px"><div class="small muted" style="padding:4px 10px 8px">Saved JSON output in ${L.folders.map(esc).join(", ")}</div>
      ${L.files.map(f => `<a href="#files/${encodeURIComponent(f.id)}" class="${f.id === id ? "on" : ""}"><div>${esc(f.name)}</div><div class="k">${esc(f.kind)} · ${ago(f.modified)}</div></a>`).join("") || `<div class="dim" style="padding:10px">No JSON files yet. Mixes you analyse on the Mixes tab are saved here.</div>`}
      <div style="padding:10px"><div class="drop" id="drop">Drop a JSON file here or <a href="#" id="pick">open one</a><input type="file" id="file" accept=".json,.txt" hidden></div></div>
    </div><div id="fview">${id ? `<div class="loading">Loading…</div>` : `<div class="card dim">${gone ? "That file is no longer in the folder (deleted or moved). " : ""}Pick a file. Mix analyses also open nicely in the Mixes tab.</div>`}</div></div>`;
  const show = d => { $("#fview").innerHTML = d && d.type === "dj_mix" ? renderMix(d) : `<div class="card tree">${tree(d)}</div>`; };
  let shownDrop = false;  // a file dropped while the picked one loads stays on screen
  dropZone($("#drop"), $("#file"), $("#pick"), d => { shownDrop = true; main.querySelectorAll(".list a.on").forEach(a => a.classList.remove("on")); show(d); },
    SNAP ? null : mixDialog);  // a recording dropped here: analyse it, as on the Mixes tab
  if (id) {
    try { const d = await api("saved-file", { id }); if (me === S.routeId && !shownDrop) show(d); }
    catch (e) { if (me === S.routeId && !shownDrop) $("#fview").innerHTML = errBox(e); }
  }
}
function tree(v, key, depth = 0) {
  const k = key != null ? `<span class="key">${esc(key)}</span>: ` : "";
  if (v === null) return `<div class="kv">${k}<span class="dim">null</span></div>`;
  if (Array.isArray(v) || typeof v === "object") {
    const entries = Array.isArray(v) ? v.map((x, i) => [i, x]) : Object.entries(v);
    const simple = entries.every(([, x]) => x === null || typeof x !== "object") && entries.length <= 8 && JSON.stringify(v).length < 110;
    if (simple) return `<div class="kv">${k}${esc(JSON.stringify(v))}</div>`;
    const label = Array.isArray(v) ? `[${v.length}]` : `{${entries.length}}`;
    return `<details ${depth < 1 ? "open" : ""}><summary>${k}<span class="dim">${label}</span></summary>${entries.map(([kk, x]) => tree(x, kk, depth + 1)).join("")}</details>`;
  }
  const cls = typeof v === "string" ? "s" : typeof v === "number" ? "n" : "b";
  return `<div class="kv">${k}<span class="${cls}">${esc(typeof v === "string" ? JSON.stringify(v) : v)}</span></div>`;
}

async function init() {
  await loadLibs();
  if (!SNAP) {
    jobPoll();
    try { if (!location.hash && !setupReady(await getSetup())) location.hash = "setup"; } catch (e) {}  // a new user starts at Setup
  }
  await route();
  try { const sv = JSON.parse(sessionStorage.getItem("ta-scroll") || "null"); if (sv && sv.hash === location.hash) scrollTo(0, sv.y); sessionStorage.removeItem("ta-scroll"); } catch (e) {}  // back to where the page was scrolled
}
init().catch(e => { $("#main").innerHTML = errBox(e); });
