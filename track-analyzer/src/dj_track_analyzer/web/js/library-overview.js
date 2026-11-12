// ---------- library ----------
const VIEWAPP = () => S.libs?.loaded_app || DJ();  // the DJ app holding the loaded tempo view (maybe not the chosen one)
async function loadLibs() {
  S.libs = await api("libraries");
  const sel = $("#lib");
  const inTraktor = l => (l.bpm || null) === (S.libs.loaded_view || null);  // the one Traktor has loaded
  sel.innerHTML = S.libs.libraries.map(l => `<option value="${esc(l.id)}" title="${esc(l.label)}">${l.bpm ? `all tracks at ${esc(l.bpm)} BPM` : "tracks at own BPM"}${!SNAP && inTraktor(l) ? ` (as ${esc(VIEWAPP())})` : ""}</option>`).join("");
  if (!S.libs.libraries.some(l => l.id === S.lib)) S.lib = "native";
  sel.value = S.lib;
  sel.onchange = () => { S.lib = sel.value; try { localStorage.setItem("ta-lib", S.lib); } catch (e) {} route(); };
  libNote();
  lexNote();
}
async function lexNote() {  // tracks in Traktor that Lexicon hasn't imported: its "Sync to Traktor" only goes the other way
  const n = $("#lexnote");
  if (SNAP || !S.libs.lexicon || DJ() !== "Traktor") { n.hidden = true; return; }
  let d; try { d = await api("lexicon/missing"); } catch (e) { return; }
  n.hidden = !d.count;
  if (!d.count) return;
  const names = d.missing.map(esc).join(", ") + (d.count > d.missing.length ? ", …" : "");
  n.innerHTML = `<b>${d.count} track${d.count > 1 ? "s" : ""} in Traktor ${d.count > 1 ? "aren't" : "isn't"} in Lexicon yet</b> (${names}). In Lexicon, choose <b>Import → Traktor</b>: its <i>Sync to Traktor</i> only sends tracks the other way.`
    + (d.lexicon_view ? ` Lexicon has the ${esc(d.lexicon_view)} BPM tempo view loaded: switch it back on the Tempo view tab first.` : "");
}
function libNote() {
  const n = $("#libnote"), v = S.libs.loaded_view, usesLib = ["overview", "tracks", "hype"].includes(S.page);
  $("#libwrap").style.visibility = usesLib ? "visible" : "hidden";
  loadedChips();
  const parts = [], shown = S.lib !== "native" ? +S.lib.replace("at-", "") : null;
  if (v) parts.push(`${esc(VIEWAPP())} has the <b>${v} BPM view</b> loaded, so its Genre/Colour/Comment show ${v} BPM values.`);
  if (usesLib && shown) parts.push(`This page shows <b>every track at ${esc(shown)} BPM</b>: as heard stretched to that tempo with key lock.`);
  if (!SNAP && usesLib && shown && shown !== v) parts.push(`${v ? esc(VIEWAPP()) : DJ()} itself has ${v ? `the ${v} BPM tempo view` : "<b>no tempo view</b> loaded (each track's own values)"}.`);
  n.innerHTML = parts.join(" "); n.hidden = !parts.length;
}
function loadedChips() {  // what Traktor (and Lexicon, if you use it) has loaded right now, on every tab
  const el = $("#loaded"), L = S.libs;
  el.hidden = !!SNAP;  // a snapshot can't know what's loaded now
  if (SNAP) return;
  const chip = (app, bpm) => `<span class="chip${bpm ? " warn" : ""}">${app} tempo view: ${bpm ? `${esc(bpm)} BPM` : "off"}</span>`;
  el.innerHTML = L.lexicon && L.lexicon_view === L.loaded_view ? chip(`${esc(VIEWAPP())} + Lexicon`, L.loaded_view)  // saves a header row
    : chip(esc(VIEWAPP()), L.loaded_view) + (L.lexicon ? chip("Lexicon", L.lexicon_view) : "");
  el.dataset.tip = `${VIEWAPP()}: ${L.loaded_view ? `the ${L.loaded_view} BPM tempo view is loaded (genres, colours and Comment as heard with every track at ${L.loaded_view} BPM)` : "no tempo view loaded, so genres, colours and Comment are each track's own, at its own BPM"}`
    + (L.lexicon ? `. Lexicon: ${L.lexicon_view ? `the ${L.lexicon_view} BPM tempo view is loaded` : "no tempo view loaded"}` : "")
    + ". The Library picker only changes what this page shows. Switch on the Tempo view tab.";
}
document.addEventListener("visibilitychange", () => {  // `tempo load` / `native` may have run in a terminal meanwhile
  if (!document.hidden && S.libs && !SNAP) loadLibs().then(() => { if (S.page === "overview") route(); }, () => {});  // its Tempo views card too
});
async function getTracks(lib = S.lib) {
  if (!S.tracks[lib]) {
    S.tracksLoading ||= {};  // one request per library at a time: a second caller waits for it, no second retry timer
    if (S.tracksLoading[lib]) { await S.tracksLoading[lib]; return S.tracks[lib] || getTracks(lib); }
    const req = S.tracksLoading[lib] = api("tracks", { lib });
    let d;
    try { d = await req; } finally { delete S.tracksLoading[lib]; }
    S.tracks[lib] = d.tracks;
    S.fixesPending = !!d.corrections_pending;
    navCounts();
    if (d.corrections_pending) setTimeout(async () => {  // corrections are still being counted: fetch again
      delete S.tracks[lib];
      try { await getTracks(lib); } catch (e) { return; }
      if (S.page === "tracks" && S.lib === lib) S.refreshTracks?.();  // the new counts into the list as it is now
    }, 4000);
  }
  return S.tracks[lib];
}

// the Tracks tab's count, and a dot when the model proposes corrections to your cues
function navCounts() {
  const list = S.tracks[S.lib], a = document.querySelector('#nav a[href="#tracks"]');
  if (!list || !a) return;
  const n = list.filter(t => t.type === "track").length, fixes = list.filter(t => t.corrections > 0).length;
  a.innerHTML = `Tracks<span class="count">${n}</span>${fixes ? `<span class="dot" data-tip="${plural(fixes, "track")} with proposed corrections to your cues (Tracks: All tracks → Corrections proposed for its cues)"></span>` : ""}`;
}

// ---------- router ----------
const PAGES = { overview: pageOverview, tracks: pageTracks, hype: pageHype, colours: pageColours, tempo: pageTempo, mixes: pageMixes, files: pageFiles, setup: pageSetup };
async function route() {
  const [page, ...rest] = (location.hash.slice(1) || "overview").split("/");
  S.page = PAGES[page] && !(SNAP && (page === "files" || page === "setup")) ? page : "overview";  // a snapshot has neither
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.getAttribute("href") === "#" + S.page));
  if (S.libs) libNote();
  if (!SNAP) { jobBar(); refreshSetupNote(); }
  if (S.page !== "tracks") getTracks().then(navCounts, () => {});  // in the background (cached): the tab's count
  const main = $("#main"), me = S.routeId = (S.routeId || 0) + 1;
  main.innerHTML = `<div class="loading">Loading…</div>`;
  try { await PAGES[S.page](main, decodeURIComponent(rest.join("/"))); }
  catch (e) { if (me === S.routeId) main.innerHTML = errBox(e); }
}
addEventListener("hashchange", route);

// ---------- overview ----------
async function pageOverview(main) {
  const me = S.routeId, d = await api("stats", { lib: S.lib });
  if (me !== S.routeId) return;  // a newer page or library was picked meanwhile
  if (!d.tracks) {
    main.innerHTML = `<div class="page-head"><div><h2>Overview</h2></div></div>` + empty("Nothing scanned yet",
      "Once your music is scanned, this shows your genres, tempos, keys and colours at a glance.", toSetup("Scan your music"));
    return;
  }
  const genres = Object.entries(d.genres).map(([g, v]) => ({ label: g, value: v.tracks, valText: `${v.tracks} · ${v.avg_bpm ?? "–"} BPM`, go: { genre: g, genreTop: true },
    tip: `${g}: ${v.tracks} tracks with it as their top genre, average ${v.avg_bpm ?? "–"} BPM` }));
  const hist = Object.entries(d.bpm_histogram).map(([b, n]) => { const [lo, hi] = b.split("-"); return { label: `${b} BPM`, short: lo, value: n, go: n ? { bpmMin: lo, bpmMax: hi } : null }; });
  const keys = Object.entries(d.top_camelot_keys).map(([k, n]) => ({ label: k, value: n, go: { key: k } }));
  const cols = COLOURS.filter(c => d.colours[c]).map(c => ({ label: c, value: d.colours[c], go: { colour: c } }));
  const st = S.libs.status;
  main.innerHTML = `
    <div class="page-head"><div><h2>Overview</h2><div class="muted">${esc(S.libs.libraries.find(l => l.id === S.lib)?.label || "")}</div></div></div>
    <div class="tiles">
      <div class="tile go" data-go="{}" data-tip="Click to show every file"><div class="v">${d.tracks}</div><div class="l">scanned files</div></div>
      <div class="tile"><div class="v">${Object.keys(d.genres).length}</div><div class="l">genres in use</div></div>
      <div class="tile ${d.errors ? "bad go" : "good"}" ${d.errors ? `data-go='{"errors":true}' data-tip="Click to show the files that couldn't be analysed"` : ""}><div class="v">${d.errors}</div><div class="l">analysis errors</div></div>
      <div class="tile"><div class="v" style="font-size:16px;padding-top:6px">${esc(d.last_scan || "–")}</div><div class="l">last scan</div></div>
    </div>
    <div class="grid two">
      <div class="card"><h3>Genres <span class="dim small">(top genre per track)</span></h3>${bars(genres)}</div>
      <div>
        <div class="card"><h3>BPM <span class="dim small">(${DJ()}'s where it has one, in 5-BPM bins)</span></h3>${hist.length ? columns(hist) : `<div class="dim">Nothing yet.</div>`}</div>
        <div class="card"><h3>Colours <span class="dim small">(yours, else predicted)</span></h3>${bars(cols, { colourLabels: true })}</div>
        <div class="card"><h3>Most common keys <span class="dim small">(Camelot)</span></h3>${bars(keys)}</div>
      </div>
    </div>
    <div class="card"><h3>Tempo views</h3>
      <dl class="facts">${SNAP ? "" : `<dt>${esc(VIEWAPP())}</dt><dd>${esc(st.loaded_in_traktor)}</dd>`}${!SNAP && S.libs.lexicon ? `<dt>Lexicon</dt><dd>${esc(st.loaded_in_lexicon)}</dd>` : ""}
      ${Object.entries(st.views).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v.tracks} tracks <span class="dim mono">${esc(v.folder)}</span></dd>`).join("") || "<dt>Views</dt><dd class='dim'>none scanned yet: make one on the <a href='#tempo'>Tempo view</a> tab</dd>"}</dl>
    </div>`;
}

