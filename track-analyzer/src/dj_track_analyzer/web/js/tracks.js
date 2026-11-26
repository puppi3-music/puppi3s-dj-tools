// ---------- tracks ----------
const TF0 = { q: "", genre: "", colour: "", key: "", harmonic: false, bpmMin: "", bpmMax: "", type: "track", held: "", cues: "", errors: false, genreTop: false };
const TF = { ...TF0 };
try { const { _sort, ...tf } = JSON.parse(sessionStorage.getItem("ta-tf") || "{}"); Object.assign(TF, tf); if (_sort) S.trackSort = _sort; } catch (e) {}  // a refresh keeps the Tracks filters
addEventListener("pagehide", () => { try { sessionStorage.setItem("ta-tf", JSON.stringify({ ...TF, _sort: S.trackSort })); sessionStorage.setItem("ta-scroll", JSON.stringify({ hash: location.hash, y: scrollY })); } catch (e) {} });
// the open track lives in the address (#tracks/<path>), so a refresh opens it again; replaceState doesn't fire hashchange or add history
const hashTrack = path => { if (S.page === "tracks") try { history.replaceState(null, "", "#tracks" + (path ? "/" + encodeURIComponent(path) : "")); } catch (e) {} };
// the Tracks tab with only this filter set (Everything, so the counts match the Overview's)
function showTracks(filter) { Object.assign(TF, TF0, { type: "" }, filter); location.hash = "tracks"; }
document.addEventListener("click", e => { const g = e.target.closest("[data-go]"); if (g) showTracks(JSON.parse(g.dataset.go)); });
const SEL = new Set();
function camelotNeighbours(code) {
  const m = code.trim().toUpperCase().match(/^(\d{1,2})([AB])$/); if (!m) return [code.trim().toUpperCase()];
  const n = +m[1], l = m[2], w = x => (x - 1 + 12) % 12 + 1;
  return [`${n}${l}`, `${w(n - 1)}${l}`, `${w(n + 1)}${l}`, `${n}${l === "A" ? "B" : "A"}`];
}
const colourOf = t => t.traktor_color || t.color;
const UNBIASED_TIP = "Judged without knowing your colour (from training's cross-validation), so a colour you set by mistake doesn't pull it your way";
// the Tracks tab's "All tracks" menu: value → which tracks it lists
const CHECKS = {
  some: t => t.own_cues > 0, none: t => !t.own_cues, fixes: t => t.corrections > 0, approved: t => t.cues_approved,
  grid: t => t.grid_off != null || t.grid_drift, grid_ok: t => t.grid_approved != null,
  key: t => t.traktor_camelot && t.camelot && !camelotNeighbours(t.traktor_camelot).includes(t.camelot), guess: t => t.colour_guess,
};
const colourPos = t => t.traktor_color ? COLOURS.indexOf(t.traktor_color) + 1 : t.color_position ?? 99;
const SORTS = {
  track: t => `${t.artist || ""} ${t.title || t.filename}`.toLowerCase(), bpm: t => t.mix_bpm ?? 1e9, key: t => {
    const m = (t.mix_camelot || "").match(/(\d+)([AB])/); return m ? +m[1] * 2 + (m[2] === "B") : 1e9; },
  genre: t => (t.genre || "~").toLowerCase(), colour: colourPos, cues: t => -((t.corrections || 0) * 100 + (t.own_cues || 0)), hype: t => -(t.hype ?? -1), lufs: t => -(t.lufs ?? -99), length: t => t.duration_sec ?? 0,
};
// ---- the Tracks table's columns: right-click the header row to show / hide them, drag a header to move it ----
const dj = (t, k) => t.dj?.[k];
const txtSort = v => (v ?? "").toString().toLowerCase() || "\uffff";  // A to Z, empty last
const hiSort = v => -(v ?? -1e15);  // highest (or newest) first, empty last
const dateSort = v => hiSort(v ? Date.parse(v) : null);
const djText = k => ({ sort: t => txtSort(dj(t, k)), cell: t => dj(t, k) ? `<span class="clip" data-tip="${esc(dj(t, k))}">${esc(dj(t, k))}</span>` : "" });
const djNum = (k, f = v => fmt(v)) => ({ r: 1, sort: t => hiSort(dj(t, k)), cell: t => dj(t, k) != null ? f(dj(t, k)) : "" });
const djDate = k => ({ sort: t => dateSort(dj(t, k)), cell: t => `<span class="nowrap">${esc(dj(t, k) || "")}</span>` });
const isRb = () => DJ() === "Rekordbox";
// id: {h: header (text or function), sort: key for SORTS, r: right-aligned, cell, apps: only for these DJ apps, group}
const TCOLS = {
  colour: { h: "Colour", sort: SORTS.colour, cell: t => `${colour(colourOf(t), !t.traktor_color)}${t.colour_guess ? guessMark() : ""}${t.held_colour ? lock("Colour") : ""}` },
  track: { h: "Track", fixed: 1, sort: SORTS.track, cell: t => `${trackName(t)}${t.names_from_dj ? ` <span class="chip" data-tip="The file has no ${t.names_from_dj.join(" or ")} tag: this is ${DJ()}'s. The button at the top writes it into the file.">${DJ()}'s name</span>` : ""}${t.type === "track" && !t.own_cues ? ` <span class="chip" data-tip="${t.own_cues === 0 ? `No cue points or loops set in ${DJ()}` : `Not in ${DJ()}'s collection (or no beat grid)`}">no cues</span>` : ""}` },
  bpm: { h: "BPM", r: 1, sort: SORTS.bpm, td: t => t.source_bpm ? `data-tip="own tempo ${Math.round(t.source_bpm)} BPM"` : "", cell: t => fmt(t.mix_bpm) },
  key: { h: "Key", sort: SORTS.key, td: t => t.traktor_camelot && t.camelot && t.traktor_camelot !== t.camelot ? `data-tip="${DJ()} ${esc(t.traktor_camelot)}, ours ${esc(t.camelot)}"` : "",
    cell: t => `${esc(t.mix_camelot || "–")}${t.traktor_camelot && t.camelot && t.traktor_camelot !== t.camelot ? ` <span class="dim small">(${esc(t.camelot)})</span>` : ""}` },
  genre: { h: "Genre", sort: SORTS.genre, cell: t => `${esc(t.my_genre || t.genre || "–")}${t.held_genre ? lock("Genre") : ""}${t.genre_2 ? `<div class="dim small">${esc(t.genre_2)}</div>` : ""}${t.error && !t.error.startsWith("tags:") ? `<div class="chip bad" data-tip="${esc(t.error)}">error</div>` : ""}` },
  hype: { h: "Hype", r: 1, sort: SORTS.hype, cell: t => hypeBadge(t.hype) },
  cues: { h: "Cues", r: 1, sort: SORTS.cues, td: t => `data-tip="${t.own_cues ? `${t.own_cues} cue${t.own_cues > 1 ? "s" : ""} set in ${DJ()}` : `no cues set in ${DJ()}`}${t.corrections ? ` · ${t.corrections} proposed correction${t.corrections > 1 ? "s" : ""}` : ""}${t.cues_approved ? ` · approved, not written yet: ${approvedText(t.cues_approved, true)}` : ""}"`,
    cell: t => `${t.own_cues ?? "–"}${t.corrections ? ` <span class="chip warn">${t.corrections} fix${t.corrections > 1 ? "es" : ""}</span>` : ""}${t.cues_approved ? ` <span class="chip good">${approvedText(t.cues_approved)}</span>` : ""}${S.fixesPending && t.own_cues ? ` <span class="dim small">counting…</span>` : ""}${t.grid_off != null ? ` <span class="chip warn" data-tip="${DJ()}'s beat grid is ${Math.abs(t.grid_off).toFixed(0)} ms ${t.grid_off > 0 ? "before" : "after"} the kicks: see Beat grid in the track's panel">grid ${t.grid_off > 0 ? "+" : ""}${t.grid_off.toFixed(0)} ms</span>` : t.grid_approved != null ? ` <span class="chip good" data-tip="Grid fix approved, not written yet">grid fix approved</span>` : t.grid_drift ? ` <span class="chip warn" data-tip="The kick drifts against the grid along the track: the BPM looks off">BPM drifts</span>` : ""}` },
  lufs: { h: "LUFS", r: 1, sort: SORTS.lufs, cell: t => fmt(t.lufs, 1) },
  length: { h: "Length", r: 1, sort: SORTS.length, cell: t => mmss(t.duration_sec) },
  // this app's own extras
  lra: { h: "Loudness range", group: "app", r: 1, sort: t => hiSort(t.loudness_range), cell: t => fmt(t.loudness_range, 1) },
  file: { h: "File", group: "app", sort: t => txtSort(t.filename), cell: t => `<span class="clip" data-tip="${esc(t.path)}">${esc(t.filename || "")}</span>` },
  folder: { h: "Folder", group: "app", sort: t => txtSort(t.folder), cell: t => `<span class="clip" data-tip="${esc(t.folder || "")}">${esc(t.folder || "")}</span>` },
  format: { h: "File type", group: "app", sort: t => txtSort(ext(t)), cell: t => esc(ext(t)) },
  // the DJ app's own fields (only those the chosen app has)
  dj_album: { h: () => isRb() ? "Album" : "Release", ...djText("album") },
  dj_track_no: { h: "Track #", ...djNum("track_no") },
  dj_genre: { h: () => `Genre in ${DJ()}`, ...djText("genre") },
  dj_label: { h: "Label", ...djText("label") },
  dj_remixer: { h: "Remixer", ...djText("remixer") },
  dj_producer: { h: () => isRb() ? "Composer" : "Producer", ...djText("producer") },
  dj_org_artist: { h: "Original artist", apps: ["Rekordbox"], ...djText("org_artist") },
  dj_lyricist: { h: "Lyricist", apps: ["Rekordbox"], ...djText("lyricist") },
  dj_mix: { h: () => isRb() ? "Mix name" : "Mix", ...djText("mix") },
  dj_catalog: { h: "Catalog no.", apps: ["Traktor"], ...djText("catalog") },
  dj_comment: { h: () => isRb() ? "Comments" : "Comment", ...djText("comment") },
  dj_comment2: { h: "Comment 2", apps: ["Traktor"], ...djText("comment2") },
  dj_rating: { h: "Rating", ...djNum("rating", v => `<span class="stars" data-tip="${v} of 5">${"★".repeat(v)}</span>`), r: 0 },
  dj_plays: { h: "Plays", ...djNum("plays") },
  dj_last_played: { h: "Last played", apps: ["Traktor"], ...djDate("last_played") },
  dj_added: { h: "Date added", ...djDate("added") },
  dj_released: { h: "Released", ...djDate("released") },
  dj_bitrate: { h: "Bitrate", ...djNum("bitrate", v => `${fmt(v)} kbps`) },
  dj_sample_rate: { h: "Sample rate", apps: ["Rekordbox"], ...djNum("sample_rate", v => `${fmt(v / 1000, 1)} kHz`) },
  dj_size: { h: "Size", ...djNum("size_mb", v => `${fmt(v, 1)} MB`) },
};
const ext = t => (t.filename || t.path || "").match(/\.([^./\\]+)$/)?.[1]?.toUpperCase() || "";
const colHead = c => typeof c.h === "function" ? c.h() : c.h;
const TCOLS_DEFAULT = ["colour", "track", "bpm", "key", "genre", "hype", "cues", "lufs", "length"];
for (const [id, c] of Object.entries(TCOLS)) SORTS[id] ??= c.sort;
// a table whose columns you pick (right-click the headers), move (drag a header) and sort (click one).
// spec: cols (id → column as in TCOLS), def: the usual columns, store: localStorage key, sortKey: where in S the sort lives,
// groups: [group, menu heading] in menu order (a column's group: its own, else "dj" for dj_…, else "main")
const TSPEC = { cols: TCOLS, def: TCOLS_DEFAULT, store: "ta-cols", sortKey: "trackSort",
  groups: [["main", "Columns <span class=\"dim\">· drag a header to move it</span>"], ["dj", () => `From ${esc(DJ())}`], ["app", "From this app"]] };
const colOffered = (spec, id) => { const c = spec.cols[id]; return !!c && (!c.apps || c.apps.includes(DJ())); };
const colGroup = (c, id) => c.group || (id.startsWith("dj_") ? "dj" : "main");
function shownCols(spec = TSPEC) {  // the columns you picked, in your order (remembered in this browser); fixed ones always
  let ids = null;
  try { ids = JSON.parse(localStorage.getItem(spec.store)); } catch (e) {}
  ids = (Array.isArray(ids) ? ids : spec.def).filter(id => colOffered(spec, id));
  const fixed = Object.keys(spec.cols).filter(id => spec.cols[id].fixed && !ids.includes(id));
  return [...fixed, ...ids];
}
function saveCols(spec, ids) { try { localStorage.setItem(spec.store, JSON.stringify(ids)); } catch (e) {} }
// the header cells of the columns shown, marked with the sort
const colHeads = (spec, cols) => { const s = S[spec.sortKey] || {}; return cols.map(id => { const c = spec.cols[id];
  return `<th class="sort ${c.r ? "r" : ""} ${s.key === id ? (s.dir > 0 ? "asc" : "desc") : ""}" data-k="${id}" draggable="true" data-tip="Click to sort · right-click to pick columns · drag to move">${esc(colHead(c))}</th>`; }).join(""); };
// rows sorted by the spec's sort (none set: as they came)
function colSort(spec, rows) {
  const { key, dir } = S[spec.sortKey] || {}, f = spec.cols[key]?.sort; if (!f) return rows;
  return rows.sort((a, b) => { const x = f(a), y = f(b); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
}
// a header row: click sorts, right-click picks columns, drag moves one
function wireColHead(th, spec, redraw) {
  th.onclick = e => { const k = e.target.dataset?.k; if (!k) return; const s = S[spec.sortKey] || {}; S[spec.sortKey] = { key: k, dir: s.key === k ? -s.dir : 1 }; redraw(); };
  th.oncontextmenu = e => { e.preventDefault(); colMenu(e.clientX, e.clientY, redraw, spec); };
  th.ondragstart = e => { const k = e.target.dataset?.k; if (!k) return; e.dataTransfer.setData("text/plain", k); e.dataTransfer.effectAllowed = "move"; };
  th.ondragover = e => { if (e.target.closest("th[data-k]")) { e.preventDefault(); th.querySelectorAll(".dropat").forEach(x => x.classList.remove("dropat")); e.target.closest("th").classList.add("dropat"); } };
  th.ondragleave = e => e.target.closest?.("th")?.classList.remove("dropat");
  th.ondrop = e => {  // the dragged column goes before the one it's dropped on (after it when dragged rightwards)
    e.preventDefault(); const from = e.dataTransfer.getData("text/plain"), to = e.target.closest("th[data-k]")?.dataset.k;
    if (!from || !to || from === to || !spec.cols[from]) { redraw(); return; }
    const cols = shownCols(spec), ids = cols.filter(x => x !== from); ids.splice(ids.indexOf(to) + (cols.indexOf(from) < cols.indexOf(to) ? 1 : 0), 0, from);
    saveCols(spec, ids); redraw();
  };
}
// the right-click menu: tick a column to show it; Reset brings back the usual ones
function colMenu(x, y, redraw, spec = TSPEC) {
  document.querySelectorAll(".usemenu").forEach(m => m.remove());
  const menu = document.createElement("div"); menu.className = "usemenu colmenu";
  const close = () => { menu.remove(); removeEventListener("keydown", key); document.removeEventListener("mousedown", out); removeEventListener("scroll", scr, true); };
  const key = e => { if (e.key === "Escape") close(); };
  const out = e => { if (!menu.contains(e.target)) close(); };
  const scr = e => { if (!menu.contains(e.target)) close(); };
  const fill = () => {
    const on = shownCols(spec), C = spec.cols, item = id => `<label class="${C[id].fixed ? "dim" : ""}"><input type="checkbox" data-col="${id}" ${on.includes(id) ? "checked" : ""} ${C[id].fixed ? "disabled" : ""}> ${esc(colHead(C[id]))}</label>`;
    const ids = Object.keys(C).filter(id => colOffered(spec, id));
    menu.innerHTML = spec.groups.map(([g, title]) => { const its = ids.filter(id => colGroup(C[id], id) === g);
      return its.length ? `<div class="hd">${typeof title === "function" ? title() : title}</div>${its.map(item).join("")}` : ""; }).join("")
      + `<div class="sep"></div><div data-reset>Reset to the usual columns</div>`;
  };
  fill();
  menu.addEventListener("change", e => {
    const id = e.target.dataset.col; if (!id) return;
    const on = shownCols(spec).filter(x => x !== id);
    saveCols(spec, e.target.checked ? [...on, id] : on); redraw();
  });
  menu.addEventListener("click", e => { if (e.target.closest("[data-reset]")) { try { localStorage.removeItem(spec.store); } catch (e) {} redraw(); fill(); } });
  document.body.append(menu);
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, innerHeight - menu.offsetHeight - 8))}px`;
  addEventListener("keydown", key); document.addEventListener("mousedown", out); addEventListener("scroll", scr, true);
}
// the Tracks tab's columns for another table whose rows carry a path: each row's track from the Tracks list (row.tr)
// fills them, blank until that list has loaded. skip: the ids the table has columns of its own for
function trackCols(skip) {
  const out = {};
  for (const [id, c] of Object.entries(TCOLS)) if (!skip.includes(id))
    out[id] = { ...c, fixed: 0, group: c.group || (id.startsWith("dj_") ? "dj" : "tracks"),
      sort: r => c.sort(r.tr || {}), cell: r => r.tr ? c.cell(r.tr) : "", td: c.td && (r => r.tr ? c.td(r.tr) : "") };
  return out;
}
const TRACKS_GROUPS = [["tracks", "From the Tracks tab"], ["dj", () => `From ${esc(DJ())}`], ["app", "From this app"]];
// gives each row its track (row.tr): at once when the Tracks list is in, else when it comes, then() redraws. A failed list leaves those columns blank
function joinTracks(rows, then) {
  const put = list => { const by = new Map(list.map(t => [t.path, t])); for (const r of rows) r.tr = by.get(r.path); };
  if (S.tracks[S.lib]) { put(S.tracks[S.lib]); return; }
  getTracks().then(list => { put(list); then(); }, () => {});
}
// a table with picked columns: lead / tail are fixed columns as in table() (a tick box, buttons)
function colTable(spec, rows, { rowAttr, lead = [], tail = [] } = {}) {
  const cols = shownCols(spec), C = spec.cols, fx = c => `<th class="${c.r ? "r" : ""}">${c.raw ? c.h : esc(c.h)}</th>`;
  return `<div class="tablewrap"><table><thead><tr>${lead.map(fx).join("")}${colHeads(spec, cols)}${tail.map(fx).join("")}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr ${rowAttr ? rowAttr(r, i) : ""}>${lead.map(c => `<td class="${c.r ? "r num" : ""}">${c.f(r)}</td>`).join("")}${cols.map(id => {
      const c = C[id]; return `<td class="${c.r ? "r num" : ""}" ${c.td ? c.td(r) : ""}>${c.cell(r)}</td>`; }).join("")}${tail.map(c => `<td class="${c.r ? "r num" : ""}">${c.f(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
// a tempo view shown that was scanned before tracks were added to the normal library: they're not in this list
function viewBehind() {
  const n = S.viewMissing?.[S.lib];
  if (!n || S.lib === "native") return "";
  const label = S.libs?.libraries.find(l => l.id === S.lib)?.label || "This tempo view";
  return `<div class="selbar"><span><b>${esc(label)} is missing ${plural(n, "track")} from your normal library</b>
    (added since the view was scanned), so this list, its counts and sorts leave them out.</span>
    <span style="display:flex;gap:8px;flex-wrap:wrap;margin-left:auto"><button id="t-native">Show the normal library</button>${toSetup("Scan new tracks on Setup")}</span></div>`;
}
async function pageTracks(main, open) {
  const me = S.routeId;
  let all = await getTracks();
  if (me !== S.routeId) return;  // a newer page or library was picked meanwhile
  // a filter kept from before (a refresh, the Overview, another library) stays in its menu even when no track has it now
  const genres = [...new Set([TF.genre, ...all.flatMap(t => [t.genre, t.genre_2, t.my_genre])].filter(Boolean))].sort();
  const nNames = all.filter(t => t.names_from_dj).length;  // names shown from the DJ app: the files' tags have none
  main.innerHTML = `
    <div class="page-head"><div><h2>Tracks</h2><div class="muted" id="tcount"></div></div><div class="spacer"></div>
      ${!SNAP && nNames ? `<button id="t-names" data-tip="${nNames} files have no artist or title tags, so this table shows ${DJ()}'s names for them. Writes those names into the files' empty tags (shows what changes first)">Write ${DJ()}'s names into ${nNames} files…</button>` : ""}${!SNAP ? `<button id="t-grids" data-tip="Move every approved beat grid (and its cues) in ${DJ()} (shows what changes first)">Write grid fixes to ${DJ()}…</button><button id="t-write" data-tip="Write every approved track's cues into ${DJ()} (shows what changes first)">Write approved cues to ${DJ()}…</button>` : ""}</div>
    <div class="filters">
      <input type="search" id="f-q" placeholder="Search artist, title, file, album…" style="width:260px" value="${esc(TF.q)}">
      <select id="f-genre"><option value="">All genres</option>${genres.map(g => `<option ${g === TF.genre ? "selected" : ""}>${esc(g)}</option>`).join("")}</select>
      <select id="f-colour"><option value="">All colours</option>${[...COLOURS, ...(TF.colour && !COLOURS.includes(TF.colour) ? [TF.colour] : [])].map(c => `<option ${c === TF.colour ? "selected" : ""}>${c}</option>`).join("")}</select>
      <label class="inline">BPM <input type="number" id="f-bmin" placeholder="min" value="${esc(TF.bpmMin)}"> – <input type="number" id="f-bmax" placeholder="max" value="${esc(TF.bpmMax)}"></label>
      <label class="inline">Key <input type="text" id="f-key" placeholder="8A" style="width:56px" value="${esc(TF.key)}"></label>
      <label class="inline"><input type="checkbox" id="f-harm" ${TF.harmonic ? "checked" : ""}> mixes with it</label>
      <select id="f-type"><option value="track">Tracks</option><option value="dj_mix">Mixes</option><option value="">Everything</option></select>
      <select id="f-held"><option value="">Held or not</option><option value="genre">Genre held</option><option value="colour">Colour held</option><option value="any">Anything held</option><option value="none">Nothing held</option></select>
      <select id="f-cues"><option value="">All tracks</option><optgroup label="Cues"><option value="some">Has cues in ${DJ()}</option><option value="none">No cues in ${DJ()}</option><option value="fixes">Corrections proposed for its cues</option><option value="approved">Cues approved, not written to ${DJ()} yet</option></optgroup><optgroup label="Beat grid"><option value="grid">Grid off the kicks or drifting</option><option value="grid_ok">Grid fix approved, not written to ${DJ()} yet</option></optgroup><optgroup label="Key"><option value="key">Key unsure: ours and ${DJ()}'s don't agree</option></optgroup><optgroup label="Colour"><option value="guess">Colour guessed by the app, not checked yet</option></optgroup></select>
      <span id="f-chips" style="display:contents"></span>
    </div>
    ${viewBehind()}
    <div id="t-learn"></div>
    <div id="selbar"></div>
    <div class="card" style="padding:0"><div id="ttable"></div></div>`;
  retrainBars($("#t-learn"), ["cues", "colours"]);
  $("#t-write")?.addEventListener("click", () => writeToTraktor(() => S.reloadTracks()));
  $("#t-grids")?.addEventListener("click", () => writeGrids(() => S.reloadTracks()));
  $("#t-names")?.addEventListener("click", () => writeDialog("names", {}, () => S.reloadTracks()));
  $("#t-native")?.addEventListener("click", () => { const sel = $("#lib"); sel.value = "native"; sel.onchange(); });
  $("#f-type").value = TF.type; $("#f-held").value = TF.held; $("#f-cues").value = TF.cues;
  const bind = (id, key, ev = "input", get = el => el.value) => { $(id).addEventListener(ev, e => { TF[key] = get(e.target); draw(); }); };
  bind("#f-q", "q"); bind("#f-genre", "genre", "change"); bind("#f-colour", "colour", "change"); bind("#f-bmin", "bpmMin");
  bind("#f-bmax", "bpmMax"); bind("#f-key", "key"); bind("#f-harm", "harmonic", "change", el => el.checked); bind("#f-type", "type", "change"); bind("#f-held", "held", "change"); bind("#f-cues", "cues", "change");
  let shown = [];
  function selbar() {
    const n = SEL.size, box = $("#selbar");
    if (SNAP || !n) { box.innerHTML = ""; return; }
    const guessed = all.filter(t => t.colour_guess && SEL.has(t.path)).map(t => t.path);
    box.innerHTML = `<div class="selbar"><b>${n} selected</b>
      ${guessed.length ? `<button data-accept data-tip="${plural(guessed.length, "selected colour")} guessed by the app: they're right, learn from them">Guessed colours are right (${guessed.length})</button>` : ""}
      <button data-h="genre" data-on="1">Hold genre</button><button data-h="colour" data-on="1">Hold colour</button>
      <button data-h="genre" data-on="0">Release genre</button><button data-h="colour" data-on="0">Release colour</button>
      <button data-clear>Clear selection</button><span class="small muted" id="selmsg"></span></div>`;
    box.querySelector("[data-clear]").onclick = () => { SEL.clear(); draw(); };
    const acc = box.querySelector("[data-accept]");
    if (acc) acc.onclick = async () => {
      try { await acceptColours(guessed); draw(); $("#selmsg").textContent = `${plural(guessed.length, "colour")} now count as yours`; }
      catch (e) { $("#selmsg").textContent = e.message; }
    };
    box.querySelectorAll("[data-h]").forEach(b => b.onclick = async () => {
      try { await hold([...SEL], b.dataset.h, b.dataset.on === "1"); draw(); $("#selmsg").textContent = b.dataset.on === "1" ? `${b.dataset.h} held` : `${b.dataset.h} released`; }
      catch (e) { $("#selmsg").textContent = e.message; }
    });
  }
  function draw() {
    const q = TF.q.toLowerCase(), keys = TF.key ? (TF.harmonic ? camelotNeighbours(TF.key) : [TF.key.trim().toUpperCase()]) : null;
    let rows = all.filter(t => (!TF.type || t.type === TF.type)
      && (!q || [t.artist, t.title, t.filename, t.album, t.folder].some(v => (v || "").toLowerCase().includes(q)))
      && (!TF.genre || [t.genre, t.genre_2, t.my_genre].includes(TF.genre))
      && (!TF.colour || colourOf(t) === TF.colour)
      && (TF.bpmMin === "" || (t.mix_bpm ?? -1) >= +TF.bpmMin) && (TF.bpmMax === "" || (t.mix_bpm ?? 1e9) <= +TF.bpmMax)
      && (!keys || keys.includes(t.mix_camelot))
      && (!TF.held || (TF.held === "genre" ? t.held_genre : TF.held === "colour" ? t.held_colour : TF.held === "any" ? t.held_genre || t.held_colour : !t.held_genre && !t.held_colour)));
    if (TF.errors) rows = rows.filter(t => t.error && !t.error.startsWith("tags:"));  // as the error chip and the Overview's count
    if (TF.genre && TF.genreTop) rows = rows.filter(t => t.genre === TF.genre);  // from the Overview: its count is the top genre's
    // each check's count among the tracks the other filters leave, so it's what picking it shows
    $("#f-cues").querySelectorAll("option[value]:not([value=''])").forEach(o => {
      o.dataset.label ??= o.textContent; o.textContent = `${o.dataset.label} (${rows.filter(CHECKS[o.value]).length})`; });
    if (TF.cues) rows = rows.filter(CHECKS[TF.cues]);
    shown = rows;
    // filters the Overview's links set that have no control of their own, as chips: ✕ takes one off
    const chip = (k, text, tipText) => `<span class="fchip" data-tip="${tipText}">${text} <button data-unchip="${k}" aria-label="Remove this filter">✕</button></span>`;
    $("#f-chips").innerHTML = (TF.errors ? chip("errors", "Only analysis errors", "Files that couldn't be analysed") : "")
      + (TF.genre && TF.genreTop ? chip("genreTop", "Top genre only", `Only tracks whose top genre is ${esc(TF.genre)}; ✕ also lists tracks with it as their 2nd genre`) : "");
    $("#f-chips").querySelectorAll("[data-unchip]").forEach(b => b.addEventListener("click", () => { TF[b.dataset.unchip] = false; draw(); }));
    const { key, dir } = S.trackSort, f = SORTS[key] || SORTS.track;  // a sort kept from an older page may be gone
    rows.sort((a, b) => { const x = f(a), y = f(b); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
    $("#tcount").innerHTML = `${rows.length} of ${all.length} files${S.fixesPending ? ` · <span class="counting">counting proposed corrections (about 30 s)…</span>` : ""}`;
    const cols = shownCols();
    const scroll = $("#ttable .tablewrap")?.scrollTop || 0;
    if (!rows.length) {
      $("#ttable").innerHTML = empty("No tracks match", all.length ? "Nothing matches all of these filters." : "Nothing is scanned yet.",
        all.length ? `<button data-clear-filters>Clear filters</button>` : toSetup("Scan your music"), false);
      $("#ttable [data-clear-filters]")?.addEventListener("click", () => { Object.assign(TF, TF0); pageTracks(main); });
      selbar(); return;
    }
    $("#ttable").innerHTML = `<div class="tablewrap" style="max-height:calc(100vh - 230px);overflow:auto"><table>
      <thead><tr>${SNAP ? "" : `<th class="cb"><input type="checkbox" id="selall" data-tip="Select every track shown" ${rows.length && rows.every(t => SEL.has(t.path)) ? "checked" : ""}></th>`}${colHeads(TSPEC, cols)}</tr></thead>
      <tbody>${rows.map(t => `<tr class="click ${SEL.has(t.path) ? "sel" : ""}" data-path="${esc(t.path)}">
        ${SNAP ? "" : `<td class="cb"><input type="checkbox" data-sel ${SEL.has(t.path) ? "checked" : ""}></td>`}
        ${cols.map(id => { const c = TCOLS[id]; return `<td class="${c.r ? "r num" : ""}" ${c.td ? c.td(t) : ""}>${c.cell(t)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>`;
    $("#ttable .tablewrap").scrollTop = scroll;
    wireColHead($("#ttable thead"), TSPEC, draw);
    $("#ttable tbody").onclick = e => {
      const tr = e.target.closest("tr[data-path]"); if (!tr) return;
      if (e.target.closest(".cb")) {  // the checkbox cell selects instead of opening
        const p = tr.dataset.path; SEL.has(p) ? SEL.delete(p) : SEL.add(p); draw(); return;
      }
      openTrack(tr.dataset.path);
    };
    const all_ = $("#selall");
    if (all_) all_.onchange = () => { shown.forEach(t => all_.checked ? SEL.add(t.path) : SEL.delete(t.path)); draw(); };
    selbar();
  }
  S.redrawTracks = draw;
  S.reloadTracks = async () => { delete S.tracks[S.lib]; const list = await getTracks(); if ($("#ttable")) { all = list; draw(); } };  // a write that ends after you left the tab
  S.refreshTracks = () => { if (S.tracks[S.lib] && $("#ttable")) { all = S.tracks[S.lib]; draw(); } };
  draw();
  if (open) openTrack(open);
}

// ---------- track drawer ----------
function drawerWide() {  // the panel's wide mode, remembered between visits
  if (S.drawerWide === undefined) { try { S.drawerWide = localStorage.getItem("ta-drawer-wide") === "1"; } catch (e) { S.drawerWide = false; } }
  return S.drawerWide;
}
// the waveform measures its width only when it draws: draw it again once the panel has its new width
$("#drawer").addEventListener("transitionend", e => { if (e.target === $("#drawer") && e.propertyName === "width") $("#cuebox")?._wave?.redraw(); });
// The open track's slow preparations (Rubber Band for the tempo view, Demucs for the loop ideas) run with this signal:
// opening another track or closing the panel aborts them, the server stops them, and coming back asks again
const PREP = { path: null, ctl: new AbortController() };
function prepFor(path) {
  if (PREP.path !== path) { PREP.ctl.abort(); PREP.ctl = new AbortController(); PREP.path = path; }
  return PREP.ctl.signal;
}
const aborted = e => e?.name === "AbortError";
function closeDrawer() { prepFor(null); gridStop(); deckStopLoop(); DECK.audio.pause(); $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); markOpen(null); hashTrack(null); }
// the track open in the panel, highlighted in every track list: one style rule, so it holds through any redraw
function markOpen(path) {
  let st = document.getElementById("open-row");
  if (!st) { st = document.createElement("style"); st.id = "open-row"; document.head.appendChild(st); }
  const sel = path ? `tr[data-path="${CSS.escape(path)}"]` : null;
  st.textContent = sel ? `${sel} td { background: color-mix(in srgb, var(--accent) 24%, var(--surface, transparent)); } ${sel} td:first-child { box-shadow: inset 3px 0 0 var(--accent); }` : "";
  if (sel) document.querySelector(sel)?.scrollIntoView({ block: "nearest" });
}
// a dialog or menu open over the panel takes the keys (Escape closes just that)
const overlayOpen = () => !!document.querySelector("dialog[open], .usemenu");
addEventListener("keydown", e => {
  if (e.key !== "Escape" || overlayOpen() || !$("#drawer").classList.contains("open")) return;
  const f = e.target.closest?.("input, textarea, select, [contenteditable]");
  if (f && f.closest("#drawer")) { f.blur(); return; }  // typing in the panel: Escape leaves the field, the edit stays
  if (f) return;  // Escape in a field outside the panel (the search box) is that field's
  closeDrawer();
});
addEventListener("pointerdown", e => {  // a click outside the panel closes it; a track row opens that track instead
  if (!$("#drawer").classList.contains("open") || !e.target.isConnected) return;
  if (e.target.closest("#drawer, tr[data-path], dialog, .usemenu")) return;
  closeDrawer();
});
function scaleView(t) {
  const pos = t.color_position, yours = t.traktor_color ? COLOURS.indexOf(t.traktor_color) + 1 : null;
  const at = p => `left:${(p - 0.5) / 7 * 100}%`;
  return `<div class="scale-marks top">${yours ? `<div class="m" style="${at(yours)}">yours</div>` : ""}</div>
    <div class="scale">${COLOURS.map(c => `<div class="sw-${c}" data-tip="${c}"></div>`).join("")}</div>
    <div class="scale-marks">${pos != null ? `<div class="m" style="${at(pos)}" data-tip="where the model places this track (1 = Red … 7 = Magenta)${t.colour_unbiased ? `. ${UNBIASED_TIP}` : ""}">model ${pos.toFixed(2)}</div>` : ""}</div>`;
}
