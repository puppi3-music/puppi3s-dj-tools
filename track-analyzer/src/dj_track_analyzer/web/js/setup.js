// ---------- setup ----------
let SETUP = null;
async function getSetup(force) { if (!SETUP || force) SETUP = await api("setup"); return SETUP; }
const setupReady = st => st.models.ready && st.scan.tracks > 0;
async function refreshSetupNote() {
  if (SNAP) return;
  let st; try { st = await getSetup(); } catch (e) { return; }
  const todo = !setupReady(st), n = $("#setupnote"), link = document.querySelector('#nav a[href="#setup"]');
  link.innerHTML = `Setup${todo ? `<span class="dot" data-tip="Not set up yet"></span>` : ""}`;
  if (UPD) updateDot(); else getUpdate(false, true).then(updateDot, () => {});
  n.hidden = !todo || S.page === "setup";
  $("#dockernote").hidden = !st.container;
  n.innerHTML = !st.models.ready ? `<b>New here?</b> Nothing is set up yet: the analysis models aren't downloaded and nothing is scanned. <a href="#setup">Set it up →</a>`
    : `<b>Nothing scanned yet</b>, so the tabs are empty. <a href="#setup">Scan your music →</a>`;
}
let G_TRAKTOR = null;  // the Setup tab's Traktor choices for learning genres (/api/genres/traktor), read once per visit
const SF = { folder: "", gfolder: "", gsrc: "", gpl: null, ggenres: null, bpm: "", noMaest: false, multi: true, colours: true, onlyTraktor: false, lexicon: false };
function step(state, n, title, body, actions = "") {
  return `<div class="step ${state}"><div class="ic">${state === "done" ? "✓" : n}</div><div><div class="st">${title}</div><div class="small muted">${body}</div></div><div class="act">${actions}</div></div>`;
}
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
// what training your own model again would learn that it hasn't: {added, changed, removed} since it was trained
function learnNew(n, what) {
  if (!n) return { text: "", any: false, none: false };  // unknown (a starter or an older model): the button stays on
  const k = n.added + n.changed + n.removed;
  if (!k) return { text: "", any: false, none: true };
  const parts = [n.added && `${n.added} new`, n.changed && `${n.changed} changed`, n.removed && `${n.removed} gone`].filter(Boolean).join(", ");
  return { text: `<b>${plural(k, what)} to learn from since then</b> (${parts}): update training to use them.`, any: true, none: false };
}
// A tab offers to retrain a model that has new labels to learn from: your recolours and hand edits, your new cues
const RETRAIN = {
  colours: { what: "colour", model: "colour model", job: "colors_train", opts: { views: true }, button: "Retrain colours",
    does: "Retraining learns them, lists the tracks the new model disagrees with, and refreshes your tempo views' colours (a minute or two)." },
  cues: { what: "track", model: "cue model", job: "cues_train", opts: {}, button: "Retrain cues",
    does: "Retraining learns from their cues and updates the suggestions and corrections in each track's panel." },
};
async function retrainBars(box, kinds, extra = {}) {
  if (!box?.isConnected || SNAP) return;  // also an earlier tab's bar, gone since
  S.refreshRetrain = () => retrainBars(box, kinds, extra);
  let n;
  try { n = await api("to-learn"); } catch (e) { return; }
  if (!box.isConnected) return;
  box.innerHTML = kinds.filter(k => n[k] && n[k].added + n[k].changed + n[k].removed).map(k => {
    const r = RETRAIN[k], m = n[k], parts = [m.added && `${m.added} new`, m.changed && `${m.changed} changed`, m.removed && `${m.removed} gone`, extra[k]].filter(Boolean).join(", ");
    return `<div class="selbar"><span><b>${plural(m.added + m.changed + m.removed, r.what)} to learn from since the ${r.model} was trained</b> (${parts}). ${r.does}</span>
      <button class="primary" data-retrain="${k}" ${jobRunning() ? `disabled title="Wait for the job that's running"` : ""}>${r.button}</button><span class="small muted" data-retrainmsg="${k}"></span></div>`;
  }).join("");
  box.querySelectorAll("[data-retrain]").forEach(b => b.onclick = async () => {
    const k = b.dataset.retrain, r = RETRAIN[k], page = S.page;
    b.disabled = true; b.textContent = "Retraining…";
    try { const j = await startJob(r.job, r.opts); if (j?.error) throw new Error(j.error); if (S.page === page) route(); }
    catch (err) { b.disabled = false; b.textContent = r.button; box.querySelector(`[data-retrainmsg="${k}"]`).textContent = err.message; }
  });
}
async function pageSetup(main) {
  const st = await getSetup(true), b = jobRunning() ? "disabled" : "", tk = st.traktor, sc = st.scan;
  const jb = (id, text, primary, disabled, why) => `<button id="${id}" class="${primary ? "primary" : ""}" ${disabled || b} ${disabled && why ? `title="${esc(why)}"` : ""}>${text}</button>`;
  const models = st.models.ready
    ? step("done", 1, "Analysis models", `Downloaded${st.models.maest ? "" : ", without MAEST: genres come from the smaller EffNet model only"}. <span class="mono">${esc(st.models.folder)}</span>`,
        st.models.maest ? "" : jb("su-maest", "Download MAEST too"))
    : step("todo", 1, "Download the analysis models", "A few hundred MB, mostly MAEST (the better genre model). Only once.",
        `<label class="inline small"><input type="checkbox" id="su-nomaest" ${SF.noMaest ? "checked" : ""}> skip MAEST (smaller, less accurate genres)</label>${jb("su-setup", "Download", true)}`);
  // Where collection.nml is: found automatically, or chosen here (the system's file dialog via the server, or a pasted path).
  const nmlPick = `<span id="su-nml-pick" style="display:${tk.collection ? "none" : "contents"}">
      ${tk.can_browse ? jb("su-nml-browse", "Browse…", !tk.collection) : ""}
      <input type="text" id="su-nml-path" placeholder="${tk.can_browse ? "or paste its path" : "paste the path of collection.nml"}">${jb("su-nml-use", "Use")}</span>`;
  const nmlAct = `${tk.collection ? jb("su-nml-change", "Change…") : ""}${tk.chosen ? jb("su-nml-auto", "Find automatically") : ""}${nmlPick}`;
  const newer = tk.newer ? `<div class="small" style="margin-top:6px"><b>Traktor has a newer collection</b> (it makes a new folder when it updates): <span class="mono">${esc(tk.newer)}</span>
      ${jb("su-nml-newer", "Use the newer one", true)}</div>` : "";
  const V = tk.version || {}, issue = `<a href="https://github.com/puppi3-music/puppi3s-dj-tools/issues" target="_blank" rel="noopener">report it</a>`;
  const untested = V.untested === "format"
    ? `<b>This collection's file format is new to the analyzer</b> (NML version ${esc(V.format ?? "unknown")}; tested: ${esc(V.tested_formats.join(", "))}${V.traktor ? `, Traktor ${esc(V.traktor)}` : ""}). Reading it may go wrong in places: check each change in Traktor before relying on it. Every write is backed up and can be undone. If something looks wrong, ${issue}.`
    : V.untested === "newer"
    ? `<b>Traktor ${esc(V.traktor)} isn't verified with the analyzer yet</b> (tested up to ${esc(V.tested_traktor)}). Its collection file format is the same, so it should work fine; you might still run into issues. Every write is backed up and can be undone. If something looks wrong, ${issue}.`
    : "";
  const traktorStep = tk.collection
    ? step("done", 2, "Traktor's collection", `${tk.chosen ? "Using" : "Found"} <span class="mono">${esc(tk.collection)}</span>${tk.chosen ? " (chosen here)" : ""}: ${plural(sc.traktor_tracks, "track")} on disk${tk.missing ? `, ${tk.missing} listed but missing (moved, or on a drive that isn't connected)` : ""}.
        ${tk.coloured} coloured by you, ${tk.cued} with 3+ of your own cue points.${untested ? `<div class="note" style="margin:6px 0 0">${untested}</div>` : ""}${newer}<div id="su-nml-msg" class="small" hidden></div>`, nmlAct)
    : step("blocked", 2, "Traktor's collection", `${esc(tk.why)} It's the file <span class="mono">collection.nml</span> in Documents › Native Instruments › Traktor <i>version</i>.
        Without Traktor you can still scan folders below; the colour and cue steps learn from Traktor and need it.<div id="su-nml-msg" class="small" hidden></div>`, nmlAct);
  const scanBody = !sc.tracks ? `Listens to every track once: BPM, key, genre, mood, loudness, instruments. On a test collection of about 470 tracks this took 33 minutes on an M1 Pro.
      You can stop any time: it keeps what's done and carries on next time.`
    : `${plural(sc.tracks, "file")} scanned${sc.errors ? `, ${sc.errors} with errors (see the Tracks tab)` : ""}.${tk.collection ? sc.traktor_not_scanned ? ` <b>${plural(sc.traktor_not_scanned, "Traktor track")} not scanned yet.</b>` : " Every Traktor track is scanned." : ""}
      ${sc.changed_since_scan ? ` <b>${plural(sc.changed_since_scan, "file")} changed on disk since the scan</b> (another tagger, Traktor or a sync app): scan again so tags and Traktor get their current results (quick: the analysis is cached).` : ""}
      Scan again after adding music: only new and changed files are analysed.`;
  const scanState = !st.models.ready ? "blocked" : sc.tracks && !sc.traktor_not_scanned && !sc.changed_since_scan ? "done" : "todo";
  const scanStep = step(scanState, 3, sc.tracks ? "Your music, scanned" : "Scan your music", scanBody + (st.models.ready ? "" : " <b>Download the models first.</b>"),
    `${tk.collection ? jb("su-scan-tk", "Scan Traktor's collection", scanState === "todo", !st.models.ready && "disabled") : ""}
     <input type="text" id="su-folder" placeholder="or a folder: ~/Music/Tracks" value="${esc(SF.folder)}">${jb("su-scan-dir", "Scan folder", !tk.collection && scanState === "todo", !st.models.ready && "disabled")}`);
  const hypeStep = sc.hype ? step("done", 4, "Hype scores", `${plural(sc.hype, "track")} scored 1-10 within your collection. New scans keep them up to date.`)
    : sc.tracks >= 20 ? step("todo", 4, "Score hype", "Ranks every track 1-10 by rave sound and energy within your collection. Takes seconds.", jb("su-hype", "Score hype", true))
    : step("blocked", 4, "Hype scores", "Needs 20 scanned tracks.");
  const cm = st.colours, colourOk = tk.collection && tk.coloured >= 30 && sc.tracks >= 30;
  const colourStarter = cm.starter ? ` Until then a starter scale learned from another DJ's colours (Red = hard … Magenta = pretty) predicts them${cm.predicted ? ` (${plural(cm.predicted, "track")} so far)` : ""}.` : "";
  const colourNew = learnNew(cm.new, "colour"), cueNew = learnNew(st.cues.new, "track");
  const colourStep = cm.model ? step("done", 5, "Your colour scale", `Learned ${esc(cm.model)}; ${plural(cm.predicted, "track")} have a predicted colour. ${colourNew.text || "Update training after recolouring tracks in Traktor."} See the Colours tab.`, jb("su-colours", "Update training", colourNew.any, colourNew.none && "disabled", "Updating wouldn't change anything: no colour is new or changed since the last training"))
    : step(colourOk ? "todo" : "blocked", 5, "Learn your colours", colourOk
        ? "Learns what your Traktor colours mean from the tracks you've coloured, and predicts a colour for the rest. The first time it also listens again for moods and instruments (long, but only once, and resumable). Only reads Traktor's collection." + colourStarter
        : `Optional. Needs 30+ tracks you've coloured in Traktor that are also scanned${tk.collection ? ` (you have ${tk.coloured} coloured)` : ""}.` + colourStarter,
      colourOk ? jb("su-colours", "Learn your colours", true) : "");
  const cueOk = tk.collection && tk.cued >= 30;
  const cueStarter = st.cues.starter ? " Until then a starter model learned from another DJ's cues suggests them: listen to your tracks first, then see each track's panel (Tracks tab)." : "";
  const cueStep = st.cues.model ? step("done", 6, "Your cue points", `Learned ${esc(st.cues.model)}. Suggestions show in each track's panel (Tracks tab); approve them there and write them to Traktor. Listen to the other tracks to get suggestions for those too. ${cueNew.text}`,
        `${jb("su-cues-scan", "Listen to the other tracks")}${jb("su-cues", "Update training", cueNew.any, cueNew.none && "disabled", "Updating wouldn't change anything: no cues are new or changed since the last training")}`)
    : step(cueOk ? "todo" : "blocked", 6, "Learn your cue points", cueOk
        ? "Learns where you put cue points and your mix loop from the tracks you've cued, then suggests them for any track. About 13 minutes the first time for 450 tracks. Only reads Traktor's collection." + cueStarter
        : `Optional. Needs 30+ tracks with 3+ of your own cue points in Traktor${tk.collection ? ` (you have ${tk.cued})` : ""}.` + cueStarter,
      `${st.cues.starter && tk.collection ? jb("su-cues-scan", "Listen to your tracks") : ""}${cueOk ? jb("su-cues", "Learn your cue points", true) : ""}`);
  // Your own genres: learned from example folders + "everything else" (the rest of your scanned music), used only when switched on
  const GN = st.genres, GR = GN.report || {}, pc = v => v == null ? "–" : `${Math.round(v * 100)}%`;
  const genreOk = sc.tracks >= 30;
  const gFolder = `<input type="text" id="su-gfolder" placeholder="folder: one subfolder per genre" value="${esc(SF.gfolder || GN.source || "")}">${tk.can_browse ? jb("su-gbrowse", "Browse…") : ""}`;
  // Where the examples come from: a folder on disk, a Traktor playlist folder (a playlist per genre) or Traktor's Genre field
  const G_SRC = { folder: "A folder", traktor_playlists: "Traktor playlists", traktor_genres: "Traktor's Genre field" };
  const lastSrc = GN.source_kind || {};
  if (!G_SRC[SF.gsrc]) SF.gsrc = G_SRC[lastSrc.kind] && (lastSrc.kind === "folder" || tk.collection) ? lastSrc.kind : "folder";
  if (!SF.gpl) SF.gpl = lastSrc.kind === "traktor_playlists" ? [...(lastSrc.playlists || [])] : [];
  if (!SF.ggenres) SF.ggenres = lastSrc.kind === "traktor_genres" ? [...lastSrc.genres] : [];
  const gSource = `<div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="small">Learn from:</span>
    <div class="seg-toggle" id="su-gsrc">${Object.entries(G_SRC).map(([k, label]) => `<button data-gsrc="${k}" class="${SF.gsrc === k ? "on" : ""}"
      ${k !== "folder" && !tk.collection ? `disabled title="Choose Traktor's collection first (above)"` : ""}>${label}</button>`).join("")}</div></div>
    <div id="su-gsrcbox" style="margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"></div>`;
  const gHow = `Pick the examples: a folder with one subfolder (or one playlist) per genre, Traktor playlists (each one a genre named after it), or genres from Traktor's Genre field. 20–50 example tracks per genre, at least two genres.
    It also learns what the rest of your scanned music sounds like, so a track only gets one of your genres when it clearly sounds like it; every other track keeps its built-in genre.`;
  const G_MODES = { starter: "Starter", both: "Both", mine: "Mine only" };
  const gSwitchRow = `<div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="small">Genres used:</span>
    <div class="seg-toggle" id="su-gmode">${Object.entries(G_MODES).map(([m, label]) => `<button data-gmode="${m}" class="${GN.mode === m ? "on" : ""}"
      ${m !== "starter" && GN.not_ready ? `disabled title="${esc(GN.not_ready)}"` : ""}>${label}</button>`).join("")}</div></div>`;
  const gWhere = "the Tracks tab, mix suggestions, sets and the LLM show them; your files and Traktor get them the next time you write genres (below)";
  const gUse = !GN.trained ? ""
    : `${gSwitchRow}<div class="small" style="margin-top:6px">${GN.not_ready ? `<b>Your genres can't be used yet.</b> ${esc(GN.not_ready)}`
      : GN.mode === "both" ? `<b>Both:</b> your genre on the ${plural(GN.tracks_with_yours, "track")} that clearly sound like one; every other track keeps its built-in genre. ${gWhere[0].toUpperCase() + gWhere.slice(1)}.`
      : GN.mode === "mine" ? `<b>Mine only:</b> every track gets the closest of your genres, also tracks that sound like none of them. ${gWhere[0].toUpperCase() + gWhere.slice(1)}.`
      : `<b>Starter:</b> the built-in genres only. ${plural(GN.tracks_with_yours, "scanned track")} clearly sound like one of yours. Pick <b>Both</b> (yours where one fits, built-in elsewhere) or <b>Mine only</b> (yours on every track) to see what would change first; Starter puts the built-in genres back at any time.`}</div>`;
  const genreStep = GN.trained
    ? step(GN.in_use ? "done" : "todo", 7, "Your genres",
        `Learned ${esc(Object.entries(GN.counts).map(([g, n]) => `${g} (${n})`).join(", "))} on ${esc(GN.trained_at || "")}.
         ${GR.cross_validated_accuracy != null ? `Tested on your tracks: it recognised ${pc(GR.cross_validated_accuracy)} of the examples${GR.others_given_yours != null ? ` and gave one of your genres to ${pc(GR.others_given_yours)} of your other tracks` : ""}.` : ""}
         ${GR.most_confused && Object.keys(GR.most_confused).length ? `Mix-ups: ${esc(Object.entries(GR.most_confused).map(([k, n]) => `${k} ${n}`).join(", "))}.` : ""}
         ${gUse}<div id="su-gprev"></div>${gSource}<div id="su-gmsg" class="small" hidden></div>`,
        jb("su-gtrain", "Learn again"))
    : step(genreOk ? "todo" : "blocked", 7, "Learn your genres",
        `Optional. Teaches it genres the built-in ones don't have (like Hyperpop), or your own way of splitting them. ${gHow}
         ${genreOk ? gSource : ` <b>Needs 30 scanned tracks first</b> (you have ${sc.tracks}).`}<div id="su-gmsg" class="small" hidden></div>`,
        genreOk ? jb("su-gtrain", "Learn your genres", true) : "");
  const tool = (n, name, what) => st.tools[name] ? step("done", n, name, `Installed. ${what}`)
    : step("todo", n, name, `${what} ${st.install[name] ? "" : st.mac ? `Install <a href="https://brew.sh" target="_blank" rel="noopener">Homebrew</a> first, then come back here.`
        : `Install it in a terminal: <code>${esc(st.install_hint[name])}</code>, then reload this page.`}`,
      st.install[name] ? jb(`su-install-${name}`, st.install[name]) : "");
  const T = st.tempo, bpm = SF.bpm || (T.views[0] ?? 145), haveView = T.views.includes(+bpm);
  const tempoStep = step(!st.tools.rubberband || !sc.tracks ? "blocked" : haveView ? "done" : "todo", 10, "Tempo view",
    `Optional: your catalogue as heard stretched to the tempo you play at, with key lock: which genres, colours and hype change.
     About an hour the first time for 470 tracks, resumable; later only new tracks. ${T.views.length ? `Scanned: ${T.views.map(v => `${v} BPM`).join(", ")}.` : ""}
     ${!st.tools.rubberband ? "<b>Needs rubberband (above).</b>" : ""} ${T.loaded ? `<b>Traktor has the ${T.loaded} BPM view loaded.</b>` : ""}`,
    `<label class="inline small">BPM <input type="number" id="su-bpm" value="${esc(bpm)}" min="60" max="250"></label>
     ${jb("su-tempo", haveView ? "Scan new tracks" : "Scan tempo view", !haveView && st.tools.rubberband && sc.tracks, (!st.tools.rubberband || !sc.tracks) && "disabled")}
     ${T.loaded ? `<button id="su-native">Switch Traktor back…</button>` : T.views.length ? `<button id="su-load">Load into Traktor…</button>` : ""}
     ${T.views.length ? `<label class="inline small"><input type="checkbox" id="su-lexicon" ${SF.lexicon || T.lexicon_loaded ? "checked" : ""}> Lexicon too</label>` : ""}`);
  const G = st.grids || {};
  const gridStep = !tk.collection ? "" : step(!sc.tracks ? "blocked" : G.checked && G.checked >= (G.tracks || 0) ? "done" : "todo", 11, "Beat grids",
    `Optional: checks that Traktor's grid sits on the kicks, track by track. Grids that are off get a fix to approve in the track's panel (Tracks tab), with a click on the old and new grid to compare by ear; your cues move with the grid. About 3 minutes for 470 tracks; later only changed grids.
     ${G.checked ? `Checked ${G.checked} of ${G.tracks}: <b>${G.off} off</b>${G.drift ? `, ${G.drift} with a BPM that drifts (not fixable by moving the grid)` : ""}${G.approved ? `, ${G.approved} fix${G.approved > 1 ? "es" : ""} approved, not written yet` : ""}.` : ""}`,
    jb("su-grids", G.checked ? "Update beat-grids" : "Check the beat grids", !G.checked && !!sc.tracks,
      (!sc.tracks || (G.checked && G.checked >= (G.tracks || 0))) && "disabled", !sc.tracks ? "" : "Checking again wouldn't change anything: every grid is checked already, none is new or changed"));
  const w = (id, title, body, extra = "", disabled) => `<div class="step"><div class="ic">→</div><div><div class="st">${title}</div><div class="small muted">${body}</div></div>
    <div class="act">${extra}<button id="${id}" ${disabled || ""}>Preview…</button></div></div>`;
  const noScan = !sc.tracks && "disabled";
  main.innerHTML = `<div class="page-head"><div><h2>Setup</h2><div class="muted">From a fresh install to everything in Traktor. Each button runs the same command as in a terminal (shown as it runs), in the background: you can use the other tabs meanwhile.</div></div></div>
    <div id="jobpanel"></div>
    <div id="updatecard"></div>
    <div class="card"><h3>Get started</h3><div class="steps">${models}${traktorStep}${scanStep}${hypeStep}</div></div>
    <div class="card"><h3>Learn from your own work <span class="dim small">(optional)</span></h3><div class="steps">${colourStep}${cueStep}${genreStep}</div></div>
    <div class="card"><h3>Extra tools <span class="dim small">(optional)</span></h3><div class="steps">
      ${tool(8, "ffmpeg", "Plays loops and saves samples in a track's panel, keeps MP3 timing exact, and cuts mix clips.")}
      ${tool(9, "rubberband", "Time-stretches tracks for the tempo view.")}${tempoStep}${gridStep}</div></div>
    <div class="card"><h3>Into your files and Traktor</h3>
      <div class="note" style="margin:0 0 6px">Every change shows a preview first and nothing is written until you press Write. Traktor's collection is backed up before each write, and you can undo it.</div>
      ${untested ? `<div class="note" style="margin:8px 0">${untested}</div>` : ""}
      ${st.traktor_running ? `<div class="err" style="margin:8px 0">Traktor is open. Previews work, but quit Traktor before writing to it: it saves its collection when it quits and would undo the changes.</div>` : ""}
      <div class="steps">
      ${w("wr-tag", "Genre tags in your files", `Writes each track's genre into the file's genre tag, for Lexicon, other players, and the next step. Not needed if you only want genres in Traktor: tick "only in Traktor" in the next step instead.
        ${st.tags.tagged_files ? `${plural(st.tags.tagged_files, "file")} changed so far; their original tags are saved.` : ""}`,
        `<label class="inline small"><input type="checkbox" id="wr-multi" ${SF.multi ? "checked" : ""}> close runner-up genres too ("Acid Trance; Hard Trance")</label>
         ${st.tags.tagged_files ? `<button id="wr-untag">Put original tags back…</button>` : ""}`, noScan)}
      ${w("wr-traktor", "Genres, colours and Smartlists in Traktor", `Puts the genre tags into Traktor's collection, sets the predicted colour on tracks you haven't coloured (yours are never changed),
        and adds a "Genres (Track Analyzer)" Smartlist folder.`,
        `<label class="inline small"><input type="checkbox" id="wr-colours" ${SF.colours ? "checked" : ""}> colours too</label>
         <label class="inline small" data-tip="The analyzer's genres go straight into Traktor; your files keep their own genre tags. Traktor may put a file's own genre back when it re-reads that file's tags (after another program changes the file); writing again fixes it."><input type="checkbox" id="wr-only-traktor" ${SF.onlyTraktor ? "checked" : ""}> only in Traktor (leave my files' tags as they are)</label>`, (noScan || !tk.collection) && "disabled")}
      ${w("wr-hype", "Hype in Traktor's Comment", `Adds "Hype 07" after anything already in Comment (an old Hype in Comment 2 is moved), so you can sort and search by it in Traktor.`,
        `<button id="wr-hype-rm">Take it out…</button>`, (!sc.hype || !tk.collection) && "disabled")}
      ${cm.model || cm.starter ? w("wr-capply", "Colour new tracks", `Scans tracks that are new in Traktor and gives the uncoloured ones their predicted colour${cm.model ? "" : " (from the starter scale)"}. Colours you set are never changed.`, "", !tk.collection && "disabled") : ""}
      ${cm.model ? w("wr-recolour", "Recolour clear disagreements", "Recolours the few tracks whose colour clearly disagrees with how they sound (gap 2.5+ colours, confidence 75%+; the Colours tab lists them). Tracks you keep always are skipped.", "", !tk.collection && "disabled") : ""}
      ${st.traktor_backup ? `<div class="step"><div class="ic">↶</div><div><div class="st">Undo the last change to Traktor</div><div class="small muted">Last backup: <span class="mono">${esc(st.traktor_backup)}</span></div></div><div class="act"><button id="wr-undo">Undo…</button></div></div>` : ""}
      </div></div>
    <div class="note">Data folder: <span class="mono">${esc(st.home)}</span></div>`;
  const on = (id, fn) => { const el = $("#" + id); if (el) el.onclick = fn; };
  const go = (action, opts) => startJob(action, opts).catch(e => { $("#jobpanel").innerHTML = `<div class="err" style="margin-bottom:16px">${esc(e.message)}</div>`; });
  const bindVal = (id, key, prop = "value") => { const el = $("#" + id); if (el) el.addEventListener("input", () => { SF[key] = el[prop]; }); };
  bindVal("su-folder", "folder"); bindVal("su-bpm", "bpm"); bindVal("su-nomaest", "noMaest", "checked"); bindVal("wr-multi", "multi", "checked");
  bindVal("wr-colours", "colours", "checked"); bindVal("wr-only-traktor", "onlyTraktor", "checked"); bindVal("su-lexicon", "lexicon", "checked");
  const nml = async (body, btn) => {
    const msg = $("#su-nml-msg"), label = btn?.textContent;
    msg.hidden = true;
    if (btn) { btn.disabled = true; if (body.browse) btn.textContent = "Waiting for the file dialog…"; }
    try { const r = await postJSON("/api/traktor/collection", body); if (!r.cancelled) route(); }
    catch (e) { msg.hidden = false; msg.className = "small err"; msg.textContent = e.message; }
    finally { if (btn && document.contains(btn)) { btn.disabled = false; btn.textContent = label; } }
  };
  on("su-nml-change", () => { $("#su-nml-pick").style.display = "contents"; $("#su-nml-change").style.display = "none"; ($("#su-nml-browse") || $("#su-nml-path")).focus(); });
  on("su-nml-browse", e => nml({ browse: true }, e.currentTarget));
  on("su-nml-use", e => { const p = $("#su-nml-path").value.trim(); p ? nml({ path: p }, e.currentTarget) : $("#su-nml-path").focus(); });
  $("#su-nml-path")?.addEventListener("keydown", e => { if (e.key === "Enter") $("#su-nml-use").click(); });
  on("su-nml-auto", e => nml({ auto: true }, e.currentTarget));
  on("su-nml-newer", e => nml({ path: tk.newer }, e.currentTarget));
  on("su-setup", () => go("setup", { no_maest: SF.noMaest }));
  on("su-maest", () => go("setup", {}));
  on("su-scan-tk", () => go("scan", { traktor: true }));
  on("su-scan-dir", () => SF.folder.trim() ? go("scan", { folders: [SF.folder.trim()] }) : $("#su-folder").focus());
  on("su-hype", () => go("hype"));
  on("su-colours", () => go("colors_train"));
  on("su-cues", () => go("cues_train"));
  on("su-cues-scan", () => go("cues_scan"));
  const gmsg = (text, bad) => { const m = $("#su-gmsg"); if (!m) return; m.hidden = !text; m.className = `small${bad ? " err" : ""}`; m.textContent = text || ""; };
  const gBox = async () => {
    const box = $("#su-gsrcbox"); if (!box) return;
    if (SF.gsrc === "folder") {
      box.innerHTML = gFolder;
      bindVal("su-gfolder", "gfolder");
      on("su-gbrowse", async e => {
        const btn = e.currentTarget, label = btn.textContent;
        btn.disabled = true; btn.textContent = "Waiting for the folder dialog…"; gmsg("");
        try { const r = await postJSON("/api/genres/browse", {}); if (r.path) { SF.gfolder = r.path; $("#su-gfolder").value = r.path; } }
        catch (err) { gmsg(err.message, true); }
        finally { if (document.contains(btn)) { btn.disabled = false; btn.textContent = label; } }
      });
      return;
    }
    if (!G_TRAKTOR) {
      box.innerHTML = `<span class="small muted">Reading Traktor's collection…</span>`;
      try { G_TRAKTOR = await api("genres/traktor"); } catch (err) { box.innerHTML = ""; gmsg(err.message, true); return; }
      if (!document.contains(box)) return;
    }
    const T = G_TRAKTOR, few = n => n < T.min_each ? ` <span class="dim">· under ${T.min_each}</span>` : "";
    const again = `<button id="su-greread" class="mini" title="Read Traktor's collection again (after changing it in Traktor and saving)">↻</button>`;
    // a tick list (playlists or genres): rows {key, label, tracks, note}, the ticked keys kept in SF[keyOf]
    const ticks = (rows, keyOf, intro) => {
      const ticked = new Set(SF[keyOf]);
      box.innerHTML = `<div style="flex-basis:100%"><div class="small">${intro} ${again}</div>
        <div class="table-wrap" style="max-height:220px;overflow:auto;margin-top:4px">${rows.map(r => `<label class="inline" style="display:block"><input type="checkbox" data-tk="${esc(r.key)}" ${ticked.has(r.key) ? "checked" : ""}>
          ${esc(r.label)} <span class="dim">${r.tracks}</span>${few(r.tracks)}${r.note ? ` <span class="dim">· ${r.note}</span>` : ""}</label>`).join("")}</div></div>`;
      box.querySelectorAll("input[data-tk]").forEach(cb => cb.onchange = () => {
        SF[keyOf] = [...box.querySelectorAll("input[data-tk]:checked")].map(x => x.dataset.tk);
      });
    };
    if (SF.gsrc === "traktor_playlists") {
      if (!T.playlists.length) box.innerHTML = `<div class="small">No playlists with tracks in Traktor's collection yet. ${again}</div>`;
      else ticks(T.playlists.map(p => ({ key: p.path, label: p.path, tracks: p.tracks })), "gpl",
        "Tick at least two playlists: each one is a genre, named after the playlist (like <b>Hyperpop</b>). A track in two of them is left out.");
    } else if (T.tempo_view_loaded) {
      box.innerHTML = `<div class="small"><b>A tempo view is loaded in Traktor</b>, so its Genre field holds the view's genres. Switch Traktor back to normal on the Tempo tab first.</div>`;
    } else if (!T.genres.length) {
      box.innerHTML = `<div class="small">No genres in Traktor's Genre field yet. ${again}</div>`;
    } else {
      ticks(T.genres.map(g => ({ key: g.genre, label: g.genre, tracks: g.tracks, note: g.built_in ? "built-in name" : "" })), "ggenres",
        `Tick at least two genres. Tracks with several genres in the field${T.multi ? ` (${T.multi})` : ""} are left out. Genres with a built-in name may have been written by this app, not by you.`);
    }
    on("su-greread", () => { G_TRAKTOR = null; gBox(); });
  };
  document.querySelectorAll("#su-gsrc button[data-gsrc]").forEach(b => b.onclick = () => {
    SF.gsrc = b.dataset.gsrc; gmsg("");
    document.querySelectorAll("#su-gsrc button").forEach(x => x.classList.toggle("on", x === b));
    gBox();
  });
  gBox();
  on("su-gtrain", () => {
    if (SF.gsrc === "traktor_playlists") return SF.gpl.length >= 2 ? go("genres_train", { playlists: SF.gpl }) : gmsg("Tick at least two playlists first.", true);
    if (SF.gsrc === "traktor_genres") return SF.ggenres.length >= 2 ? go("genres_train", { genres: SF.ggenres }) : gmsg("Tick at least two genres first.", true);
    const f = ($("#su-gfolder")?.value || "").trim(); f ? go("genres_train", { folder: f }) : $("#su-gfolder")?.focus();
  });
  const gSwitch = async (mode, btn) => {
    btn.disabled = true; gmsg("");
    try { await postJSON("/api/genres/use", { mode }); S.tracks = {}; SETUP = null; route(); }
    catch (err) { btn.disabled = false; gmsg(err.message, true); }
  };
  // Starter switches straight back; Both / Mine only show what would change first, then switch on a click
  document.querySelectorAll("#su-gmode button[data-gmode]").forEach(b => b.onclick = async () => {
    const mode = b.dataset.gmode, box = $("#su-gprev");
    if (mode === GN.mode) { box.innerHTML = ""; return; }
    if (mode === "starter") return gSwitch("starter", b);
    b.disabled = true; gmsg(""); box.innerHTML = `<div class="small muted">Loading…</div>`;
    try {
      const p = await api(`genres/preview?mode=${mode}`);
      const by = Object.entries(p.by_genre).map(([g, n]) => `${esc(g)} ${n}`).join(", ");
      const rest = mode === "both" ? "Every other track keeps its built-in genre"
        : "<b>Every track gets one of your genres</b>, also tracks that sound like none of them (best when your genres cover all your music)";
      box.innerHTML = `<div class="small" style="margin-top:8px"><b>${G_MODES[mode]}: ${plural(p.changes, "track")} would change</b>${by ? ` (${by})` : ""}${p.same ? `; ${p.same} already show that genre` : ""}. ${rest}, and genres you set by hand stay.</div>
        ${p.tracks.length ? `<div class="table-wrap" style="max-height:320px;overflow:auto;margin-top:6px"><table class="small"><thead><tr><th>Track</th><th>Now</th><th>Would be</th><th>Sure</th></tr></thead><tbody>
          ${p.tracks.map(t => `<tr><td>${esc(t.artist && t.title ? `${t.artist} – ${t.title}` : t.filename)}</td><td>${esc(t.genre || "–")}</td><td><b>${esc(t.would_be)}</b></td><td>${pc(t.confidence)}</td></tr>`).join("")}
          </tbody></table></div>${p.changes > p.tracks.length ? `<div class="small muted">The first ${p.tracks.length} shown.</div>` : ""}` : ""}
        <div style="margin-top:8px"><button id="su-guse" class="primary" ${jobRunning() ? "disabled" : ""}>Switch to ${G_MODES[mode]}</button></div>`;
      $("#su-guse").onclick = ev => gSwitch(mode, ev.currentTarget);
    } catch (err) { box.innerHTML = ""; gmsg(err.message, true); }
    finally { if (document.contains(b)) b.disabled = false; }
  });
  on("su-grids", () => go("grids_check"));
  on("su-install-ffmpeg", () => go("install", { tool: "ffmpeg" }));
  on("su-install-rubberband", () => go("install", { tool: "rubberband" }));
  on("su-tempo", () => go("tempo_scan", { bpm: +($("#su-bpm").value || 145) }));
  const lex = () => !!$("#su-lexicon")?.checked;
  on("su-load", () => writeDialog("tempo_load", { bpm: +($("#su-bpm").value || 145), lexicon: lex() }));
  on("su-native", () => writeDialog("tempo_native", { lexicon: lex() }));
  on("wr-tag", () => writeDialog("tag", { multi: SF.multi }));
  on("wr-untag", () => writeDialog("untag"));
  on("wr-traktor", () => writeDialog("traktor", { colours: SF.colours, only_traktor: SF.onlyTraktor }));
  on("wr-hype", () => writeDialog("hype_traktor"));
  on("wr-hype-rm", () => writeDialog("hype_remove"));
  on("wr-capply", () => writeDialog("colors_apply"));
  on("wr-recolour", () => writeDialog("colors_recolour"));
  on("wr-undo", () => writeDialog("traktor_undo"));
  updateCard();
  jobPanel();
}
// The running (or last) job on the Setup page: progress, live log, result.
function jobPanel() {
  const box = $("#jobpanel"), j = JOB.job;
  if (!box || S.page !== "setup") return;
  if (!j || (j.dry_run && !j.running) || JOB.hidden === j.id) { box.innerHTML = ""; return; }
  const pre = box.querySelector("pre.log"), atEnd = !pre || pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30, top = pre?.scrollTop;
  const status = j.running ? `${progressBar(j, "100%")}<div class="small muted" style="margin-top:6px">${progressText(j)}${j.live ? ` · <span class="mono">${esc(j.live)}</span>` : ""}</div>`
    : j.error === "Stopped." ? `<div class="muted"><b>Stopped</b> after ${mmss(j.elapsed)}. It carries on where it stopped next time.</div>`
    : j.error ? `<div class="err">${esc(j.error)}</div>`
    : `<div style="color:var(--good)"><b>✓ Finished</b> in ${mmss(j.elapsed)}.${j.result?.this_run ? ` Analysed ${plural(j.result.this_run.analyzed, "track")}${j.result.this_run.errors ? `, ${j.result.this_run.errors} errors` : ""}.` : ""}</div>
      ${j.result ? `<details style="margin-top:6px"><summary class="small muted" style="cursor:pointer">Everything it reported</summary><div class="tree">${tree(j.result)}</div></details>` : ""}`;
  box.innerHTML = `<div class="card" style="margin-bottom:16px"><div class="filters" style="margin-bottom:8px"><h3 style="margin:0">${esc(j.label)}${j.dry_run ? " (preview)" : ""}</h3><div class="spacer"></div>
      ${j.running ? `<button id="jp-stop" ${j.stopping ? "disabled" : ""}>${j.stopping ? "Stopping…" : "Stop"}</button>` : `<button id="jp-x">Hide</button>`}</div>
    <div class="mono small dim" style="margin-bottom:8px">${esc(j.command)}</div>${status}
    ${j.running ? `<div class="note">Stopping is safe: it keeps what's done and carries on next time. Closing this page doesn't stop it; stopping the web server does.</div>` : ""}
    <pre class="log">${esc(JOB.lines.slice(-400).join("\n")) || "…"}</pre></div>`;
  const p2 = box.querySelector("pre.log");
  p2.scrollTop = atEnd ? p2.scrollHeight : top;
  $("#jp-stop")?.addEventListener("click", () => postJSON("/api/jobs/stop", {}).then(jobPoll));
  $("#jp-x")?.addEventListener("click", () => { JOB.hidden = j.id; JOB.flash = null; jobPanel(); jobBar(); });
}
JOB.listeners.add(jobPanel);

