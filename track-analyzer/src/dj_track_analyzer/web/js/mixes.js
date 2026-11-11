// ---------- mixes ----------
let LOGS = null;
async function getLogs(force) { if (!LOGS || force) LOGS = await api("saved"); return LOGS; }
const ago = t => new Date(t * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
// Traktor names its recordings Recording_2026-10-27_14h57m24s: show when the set was played, then the file only if it says more.
const REC_NAME = /Recording_(\d{4})-(\d\d)-(\d\d)_(\d\d)h(\d\d)m/, VDJ_NAME = /VDJ-(\d{4})-(\d\d)-(\d\d)-(\d\d)_(\d\d) ?([AP]M)/i;
function recDate(name) {  // the date and time in a recording's name (Traktor's or VirtualDJ's), else null
  let m = (name || "").match(REC_NAME);
  if (m) return new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5]);
  m = (name || "").match(VDJ_NAME);
  return m ? new Date(+m[1], m[2] - 1, +m[3], m[4] % 12 + (m[6].toUpperCase() === "PM" ? 12 : 0), +m[5]) : null;
}
function mixTitle(f) {
  const t = recDate(f.recording);
  return t ? t.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : f.recording || f.name;
}
const baseName = p => (p || "").split(/[\\/]/).pop();  // a path's last part, also a Windows one
const mixFile = f => { const stem = n => (n || "").replace(/\.[^.]+$/, ""); return recDate(f.recording) && stem(f.name) === stem(f.recording) ? "" : f.name; };
async function pageMixes(main, id) {
  const me = S.routeId, L = await getLogs(true), mixes = L.files.filter(f => f.kind === "mix");
  if (me !== S.routeId) return;
  if ((!id || id !== "~all" && !mixes.some(f => f.id === id)) && mixes.length) id = mixes[0].id;  // also a link to a deleted one
  const all = id === "~all", sets = new Set(mixes.map(f => f.recording || f.id)).size;  // the overview counts each recording once
  const seen = new Set(), older = new Set(mixes.filter(f => f.recording && (seen.has(f.recording) || !seen.add(f.recording))).map(f => f.id));  // newest first: a later save of the same recording
  main.innerHTML = `<div class="split">
    <div class="card list" id="drop" style="padding:8px"><div class="filters" style="padding:4px 10px 8px;margin:0"><h3 style="margin:0">Mixes</h3><div class="spacer"></div>
        ${sets > 1 ? `<button id="mx-all" class="${all ? "on" : ""}" data-tip="What your mixes add up to: keys to check, mood as played, your sets by fifth">All ${sets} mixes</button>` : ""}
        ${SNAP ? "" : `<button class="primary" id="mx-new">Analyse a mix…</button>`}</div>
      ${mixes.map(f => `<a href="#mixes/${encodeURIComponent(f.id)}" class="${f.id === id ? "on" : ""}"><div>${esc(mixTitle(f))}${older.has(f.id) ? ` <span class="chip warn small" data-tip="The same recording is saved again, newer: All ${sets} mixes counts that one. Open this one and delete it if you don't need it.">older copy</span>` : ""}</div><div class="k">${esc([f.duration, mixFile(f)].filter(Boolean).join(" · "))}</div></a>`).join("") || `<div class="dim small" style="padding:10px">No mixes yet.</div>`}
      <div class="small dim" style="padding:10px">Saved in ${L.folders.map(f => `<span class="mono" title="${esc(f)}">${esc(baseName(f))}/</span>`).join(", ")}${SNAP ? "" : ` · <a href="#" id="pick" style="display:inline;padding:0">Open a saved result…</a>`}<input type="file" id="file" accept=".json,.txt" hidden></div>
    </div><div id="mix">${id ? `<div class="loading">Loading…</div>` : empty("No mix analysed yet",
      `Analyse a recorded DJ mix to see its genre, mood, a loudness timeline, a tracklist check against ${DJ()}'s history and clip ideas.
      ${SNAP ? "" : `<div style="margin:12px 0"><button class="primary" id="mx-new2">Analyse a mix…</button></div>`}`)}</div></div>`;
  $("#mx-new")?.addEventListener("click", () => mixDialog());
  $("#mx-all")?.addEventListener("click", () => { location.hash = all && mixes.length ? `#mixes/${encodeURIComponent(mixes[0].id)}` : "#mixes/~all"; });
  $("#mx-new2")?.addEventListener("click", () => mixDialog());
  let shownDrop = false;
  dropZone($("#drop"), $("#file"), $("#pick"), data => { shownDrop = true; $("#mix").innerHTML = data.type === "dj_mix" ? renderMix(data) : `<div class="card tree">${tree(data)}</div>`; },
    SNAP ? null : mixDialog);  // a recording dropped on the list: analyse it
  if (id) {
    const me = S.routeId;  // a newer click wins over a slower earlier answer
    let d; try { d = all ? await api("mixes-report") : await api("saved-file", { id }); if (me === S.routeId && !shownDrop) MIX_ID = id, $("#mix").innerHTML = all ? renderMixReport(d) : renderMix(d); }
    catch (e) { if (me === S.routeId && !shownDrop) $("#mix").innerHTML = errBox(e); }
    const f = mixes.find(x => x.id === id), head = $("#mix .page-head");
    if (f && head && !SNAP && me === S.routeId && !shownDrop) {
      head.insertAdjacentHTML("beforeend", `<div class="spacer"></div>${f.recording ? `<button id="mx-again" data-tip="Analyse ${esc(f.recording)} again with today's models and options: ${f.name === f.recording.replace(/\.[^.]+$/, "") + ".json" ? "the new result replaces this one" : "the new result is saved as well, and this one shows as an older copy"}">Reanalyse…</button>` : ""}<button id="mx-del" data-tip="Moves ${esc(f.name)} to ${esc(baseName(f.folder))}/deleted/: gone from this list, and you can move it back">Delete…</button>`);
      $("#mx-again")?.addEventListener("click", () => mixDialog(null, id, { separated: Object.values(d?.standalone?.measured_with || {}).includes("separated") }));
      $("#mx-del").onclick = async () => {
        if (!confirm(`Delete this analysis of ${mixTitle(f)}?\n\n${f.name} moves to ${f.folder}${/\\/.test(f.folder) ? "\\" : "/"}deleted. The recording itself isn't touched.`)) return;
        try { await postJSON("/api/saved-file/delete", { id }); } catch (e) { $("#mix").insertAdjacentHTML("afterbegin", errBox(e)); return; }
        LOGS = null;
        const left = mixes.filter(x => x.id !== id);
        const to = left.length ? `#mixes/${encodeURIComponent(left[0].id)}` : "#mixes";
        if (location.hash === to) route(); else location.hash = to;
      };
    }
  }
}
// Pick one of Traktor's recordings and analyse it as a job; the result is saved where the Mixes tab looks.
// again: the id of a saved analysis, whose recording comes ticked (Reanalyse on a mix's page);
// was.separated: that analysis separated tracks without a stem file, so the option comes ticked again
async function mixDialog(file, again, was = {}) {
  const dlg = document.createElement("dialog"); dlg.className = "write"; document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.innerHTML = `<h3>Analyse a mix</h3><div class="loading">Looking for your recordings…</div>`; dlg.showModal();
  let R;
  try { R = await api("recordings"); } catch (e) { dlg.innerHTML = `<h3>Analyse a mix</h3>${errBox(e)}<div class="filters" style="margin-top:12px"><button data-close>Close</button></div>`; dlg.querySelector("[data-close]").onclick = () => dlg.close(); return; }
  const recs = R.recordings, mine = again ? recs.findIndex(r => r.saved.includes(again)) : -1,
    first = again ? mine : recs.findIndex(r => !r.saved.length);
  const gone = again && mine < 0 ? (LOGS?.files || []).find(x => x.id === again)?.recording : "";  // not in Traktor's Recordings folder
  dlg.innerHTML = `<h3>${again ? "Analyse again" : "Analyse a mix"}</h3>
    ${gone ? `<div class="note">${esc(gone)} isn't in Traktor's Recordings folder: drop it below or choose its file to analyse it again.</div>` : ""}
    <div class="small muted" title="${esc(R.folders.join("\n"))}">${R.folders.length ? "Traktor's recordings longer than 12 minutes" : "No Traktor Recordings folder found: drop your mix below or choose its file"}; tick several to analyse them one after another${recs.some(r => !r.saved.length) ? ` · <a href="#" id="mx-new-all">tick all not analysed yet</a>` : ""}</div>
    <div class="list" style="max-height:300px;overflow-y:auto;margin:6px 0">${recs.map((r, i) => `<label class="inline" style="display:flex;padding:4px 2px;white-space:normal">
      <input type="checkbox" name="mx-rec" value="${i}" ${i === (again ? first : Math.max(first, 0)) ? "checked" : ""}><span style="overflow-wrap:anywhere">${esc(mixTitle({ recording: r.name }))}
      ${r.saved.length ? `<span class="chip good">analysed</span>` : ""}<br><span class="dim small">${r.seconds ? mmss(r.seconds) + " · " : ""}${esc(r.name)}</span></span></label>`).join("") || `<div class="dim small">No recordings found.</div>`}</div>
    <div class="drop" id="mx-drop" style="margin-top:8px;padding:12px">Or drop a mix here, or <a href="#" id="mx-pick">choose a file</a></div><input type="file" id="mx-file" accept="audio/*,${[...AUDIO_EXTS].join(",")}" hidden>
    <div style="margin-top:10px"><label class="inline" style="white-space:normal"><input type="checkbox" id="mx-tl" checked> Check each against ${DJ()}'s history of that session <span class="dim small">(skipped, with a note, when there's none)</span></label><br>
      <div id="mx-startrow" style="margin:2px 0 4px 22px"><label class="inline" style="white-space:normal;flex-wrap:wrap">Started at <input type="datetime-local" step="1" id="mx-start"> <span class="dim small">only for a recording whose name doesn't say (Traktor's do)</span></label></div>
      <div id="mx-sarow" style="margin:2px 0 4px 0"><label class="inline" style="white-space:normal"><input type="checkbox" id="mx-sa" checked> Find possible sub-mixes: long blends where both tracks play near full level, to post as their own track</label><br>
        <label class="inline" style="white-space:normal;margin-left:22px"><input type="checkbox" id="mx-sas"${was.separated ? " checked" : ""}> Separate tracks without a stem file <span class="dim small">(slow: a few minutes per track; without it they're measured whole)</span></label></div>
      <label class="inline" style="white-space:normal"><input type="checkbox" id="mx-hl" checked> Suggest clips for social media: the 2 best 40-second stretches, where a drop lands after a build</label></div>
    <div class="note">Takes a minute or two per hour of music. It only reads the recordings; each result is added to your mixes when it's done${jobRunning() && JOB.job.action === "analyze_mix" ? ", after the mix being analysed now" : ""}.</div>
    <div class="filters" style="margin-top:12px"><button class="primary" id="mx-go">Analyse</button><button data-close>Close</button><span class="small" id="mx-msg"></span></div>`;
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  let dropped = null;  // a File from the drop zone (the browser gives its contents, never its path)
  const zone = $("#mx-drop"), input = $("#mx-file"), zoneText = zone.innerHTML;
  const choose = f => {
    if (!f) return;
    if (!isAudio(f)) { $("#mx-msg").innerHTML = `<span class="err">${esc(f.name)} isn't an audio file</span>`; return; }
    dropped = f; $("#mx-msg").textContent = "";
    dlg.querySelectorAll("input[name=mx-rec]").forEach(r => { r.checked = false; });
    count();
    zone.innerHTML = `✓ <b>${esc(f.name)}</b> <span class="dim small">${(f.size / 1e6).toFixed(0)} MB</span> · <a href="#" id="mx-pick">choose another</a>`;
    $("#mx-pick").onclick = e => { e.preventDefault(); input.click(); };
  };
  $("#mx-pick").onclick = e => { e.preventDefault(); input.click(); };
  input.onchange = () => choose(input.files[0]);
  zone.ondragover = e => { e.preventDefault(); zone.classList.add("over"); };
  zone.ondragleave = () => zone.classList.remove("over");
  zone.ondrop = e => { e.preventDefault(); zone.classList.remove("over"); choose(e.dataTransfer.files[0]); };
  const picked = () => [...dlg.querySelectorAll("input[name=mx-rec]:checked")].map(r => recs[+r.value].path);
  const count = () => {
    const n = dropped ? 1 : picked().length;
    $("#mx-go").textContent = n > 1 ? `Analyse ${n} mixes` : "Analyse"; $("#mx-go").disabled = !n;
    $("#mx-startrow").hidden = n !== 1 || !$("#mx-tl").checked;  // one start time is for one recording
    $("#mx-sarow").hidden = !$("#mx-tl").checked;  // the pieces are found from the tracklist
    $("#mx-sas").disabled = !$("#mx-sa").checked;
  };
  $("#mx-tl").addEventListener("change", count); $("#mx-sa").addEventListener("change", count);
  dlg.querySelectorAll("input[name=mx-rec]").forEach(r => r.addEventListener("change", () => {
    if (dropped) { dropped = null; zone.innerHTML = zoneText; $("#mx-pick").onclick = e => { e.preventDefault(); input.click(); }; }
    count();
  }));
  choose(file); count();
  if (again && mine >= 0) dlg.querySelectorAll("input[name=mx-rec]")[mine].scrollIntoView({ block: "nearest" });
  $("#mx-new-all")?.addEventListener("click", e => {
    e.preventDefault();
    dlg.querySelectorAll("input[name=mx-rec]").forEach(r => { r.checked = !recs[+r.value].saved.length; });
    if (dropped) { dropped = null; zone.innerHTML = zoneText; $("#mx-pick").onclick = ev => { ev.preventDefault(); input.click(); }; }
    count();
  });
  $("#mx-go").onclick = async () => {
    const files = dropped ? [] : picked(), go = $("#mx-go"), msg = $("#mx-msg");
    if (!dropped && !files.length) return;
    go.disabled = true; msg.textContent = "Starting…";
    let file = files[0] || "";
    if (dropped) {
      try {
        file = (await postJSON("/api/recordings/find", { name: dropped.name, size: dropped.size })).path
          || await uploadMix(dropped, pct => { msg.textContent = `Copying it over… ${pct}%`; });
      } catch (e) { go.disabled = false; msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; return; }
    }
    const running = () => { if (JOB.job?.action === "analyze_mix" && JOB.job.running) dlg.close(); };  // the bar at the top shows it from here
    JOB.listeners.add(running); dlg.addEventListener("close", () => JOB.listeners.delete(running));
    let j;
    const queued = jobRunning() && JOB.job.action === "analyze_mix";  // goes after the one running: no waiting here
    const start = $("#mx-startrow").hidden ? "" : $("#mx-start").value;
    const opts = { ...(files.length > 1 ? { files } : { file, start }), tracklist: $("#mx-tl").checked, highlights: $("#mx-hl").checked,
      standalone: $("#mx-sa").checked, standalone_separate: $("#mx-sa").checked && $("#mx-sas").checked };
    if (queued) {
      try { await postJSON("/api/jobs/start", { action: "analyze_mix", options: opts }); await jobPoll(); dlg.close(); }
      catch (e) { go.disabled = false; msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
      return;
    }
    try { j = await startJob("analyze_mix", opts); }
    catch (e) { go.disabled = false; msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; return; }
    if (j.error) { go.disabled = false; msg.innerHTML = `<span class="err">${esc(j.error)}</span>`; return; }  // shown here if it failed at once
    if (S.page !== "mixes") return;
    const stem = n => n.replace(/\.[^.]+$/, ""), name = stem(baseName(file)), L = await getLogs(true);
    if (S.page !== "mixes") return;  // went to another tab while the list loaded
    const f = L.files.find(x => x.kind === "mix" && stem(x.recording || "") === name), to = f ? `#mixes/${encodeURIComponent(f.id)}` : "#mixes";
    if (location.hash === to) route(); else location.hash = to;
  };
}
const AUDIO_EXTS = new Set([".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg", ".opus", ".wma"]);  // = analysis.AUDIO_EXTS
// A mix that isn't one of Traktor's recordings, copied to the server for the analysis (XHR: fetch reports no upload progress).
function uploadMix(file, onPct) {
  return new Promise((ok, fail) => {
    const x = new XMLHttpRequest();
    x.open("POST", `/api/mix-upload?name=${encodeURIComponent(file.name)}`);
    x.setRequestHeader("X-Track-Analyzer", "upload");
    x.upload.onprogress = e => e.lengthComputable && onPct(Math.round(e.loaded / e.total * 100));
    x.onload = () => { let d; try { d = JSON.parse(x.responseText); } catch (e) { d = { error: `The server answered ${x.status}` }; } d.path ? ok(d.path) : fail(new Error(d.error || "Upload failed")); };
    x.onerror = () => fail(new Error("The upload was cut off"));
    x.send(file);
  });
}
const isAudio = f => AUDIO_EXTS.has((f.name.match(/\.[^.]+$/) || [""])[0].toLowerCase());
function dropZone(zone, input, pick, onData, onAudio) {
  const read = file => { if (isAudio(file)) { onAudio ? onAudio(file) : alert(`${file.name} is audio, not a saved JSON result`); return; } const fr = new FileReader(); fr.onload = () => { try { const txt = fr.result, i = txt.indexOf("{"); onData(JSON.parse(txt.slice(i, findEnd(txt, i)))); } catch (e) { alert("Not a JSON file: " + e.message); } }; fr.readAsText(file); };
  if (pick) pick.onclick = e => { e.preventDefault(); input.click(); };
  input.onchange = () => input.files[0] && read(input.files[0]);
  zone.ondragover = e => { e.preventDefault(); zone.classList.add("over"); };
  zone.ondragleave = () => zone.classList.remove("over");
  zone.ondrop = e => { e.preventDefault(); zone.classList.remove("over"); e.dataTransfer.files[0] && read(e.dataTransfer.files[0]); };
}
function findEnd(txt, i) { // end of the first JSON object (stderr may follow it in a teed file)
  let depth = 0, str = false;
  for (let k = i; k < txt.length; k++) {
    const c = txt[k];
    if (str) { if (c === "\\") k++; else if (c === '"') str = false; continue; }
    if (c === '"') str = true; else if (c === "{" || c === "[") depth++; else if ((c === "}" || c === "]") && --depth === 0) return k + 1;
  }
  return txt.length;
}
const toSec = s => { if (s == null) return null; const p = String(s).split(":").map(Number); return p.reduce((a, b) => a * 60 + b, 0); };
function keyChip(t) {
  const k = t.key_check; if (!k) return "";
  const cls = k === "match" ? "good" : /one step|neighbour|close/.test(k) ? "warn" : /short|unknown/.test(k) ? "" : "bad";
  return `<span class="chip ${cls}">${esc(k)}</span>`;
}
// Which of the set's tracks plays in each timeline segment, from the tracklist: a track is the main one from its start
// (+ the measured blend lag) to the next one's. Per segment: hype weighted by time, the colour of the track there longest.
function trackSpans(d) {
  const tracks = d.tracklist?.tracks || [], lib = d.library || {}, lag = d.tracklist?.keylock?.blend_lag_seconds || 0;
  return tracks.map((t, i) => ({ t, lib: lib[t.n], a: toSec(t.start) + lag, b: i + 1 < tracks.length ? toSec(tracks[i + 1].start) + lag : Infinity }));
}
function segmentPlays(d) {
  const spans = trackSpans(d);
  return (d.timeline || []).map(s => {
    const a = toSec(s.start), b = toSec(s.end);
    const here = spans.map(x => ({ ...x, o: Math.min(b, x.b) - Math.max(a, x.a) })).filter(x => x.o > 0 && x.lib);
    const rated = here.filter(x => x.lib.hype != null), w = rated.reduce((n, x) => n + x.o, 0);
    const main = here.slice().sort((x, y) => y.o - x.o)[0];
    return { hype: w ? rated.reduce((n, x) => n + x.o * x.lib.hype, 0) / w : null, colour: main?.lib.colour, guessed: main?.lib.guessed,
      tracks: here.sort((x, y) => x.a - y.a).map(x => `${x.t.title || ""}${x.lib.hype != null ? ` · hype ${x.lib.hype}` : ""}${x.lib.colour ? ` · ${x.lib.colour}${x.lib.guessed ? "?" : ""}` : ""}`) };
  });
}
let MIX_SHOWN = null;  // the mix on screen, drawn again when the timeline's view changes
let MIX_SEL = null;  // the genre, style or mood clicked in it: {kind: "genre" | "style" | "mood", label}
const toMax = v => { const m = Math.max(0, ...v.filter(x => x != null)); return v.map(x => x == null ? null : m ? x / m : 0); };
// How strongly each timeline segment has it, 0-1 against the strongest segment (null: not measured there): genre = the
// segment's own; style = its score among the segment's top 3; mood = the share of the segment that sounds it
const segSelValue = (s, sel) => sel.kind === "genre" ? (s.dj_genre ? +(s.dj_genre === sel.label) : null)
  : sel.kind === "mood" ? s.mood?.[sel.label] ?? null
  : s.styles ? +((s.styles.map(x => x.match(/^(.*) ([\d.]+)$/)).find(m => m && m[1] === sel.label) || [])[2] || 0) : null;
const mixSelStrength = (d, sel) => toMax((d.timeline || []).map(s => segSelValue(s, sel)));
// Each tracklist row's: its heard genre for a genre, else the segments' values over its main stretch, time-weighted
// (`value`, as in the segments), against the strongest track (`strength`)
function trackSelStrength(d, sel) {
  const tl = d.timeline || [];
  const value = trackSpans(d).map(({ t, a, b }) => {
    if (sel.kind === "genre") return +((t.genre || t.library_genre) === sel.label);
    let w = 0, n = 0;
    tl.forEach(s => { const o = Math.min(b, toSec(s.end)) - Math.max(a, toSec(s.start)), v = segSelValue(s, sel); if (o > 0 && v != null) { w += o; n += o * v; } });
    return w ? n / w : null;
  });
  return { value, strength: toMax(value) };
}
const mixView = () => { try { return localStorage.getItem("ta-mix-view") || "hype"; } catch (e) { return "hype"; } };
// Draw the mix again in place: the open folds (Table, Raw JSON) stay open, and the clicked element's tip goes
function redrawMix(b) {
  const box = b.closest("#mix, #fview"); if (!box) return;
  const open = [...box.querySelectorAll("details")].map(x => x.open);
  box.innerHTML = renderMix(MIX_SHOWN);
  box.querySelectorAll("details").forEach((x, i) => { if (open[i]) x.open = true; });
  tip.style.display = "none";
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-mixview]"); if (!b || !MIX_SHOWN) return;
  try { localStorage.setItem("ta-mix-view", b.dataset.mixview); } catch (err) {}
  redrawMix(b);
});
document.addEventListener("click", e => {
  const b = e.target.closest("[data-mixsel]"); if (!b || !MIX_SHOWN) return;
  const sel = JSON.parse(b.dataset.mixsel);
  MIX_SEL = MIX_SEL && MIX_SEL.kind === sel.kind && MIX_SEL.label === sel.label ? null : sel;
  redrawMix(b);
});
// A track's mood as played (top two), with the tempo view's and its own-tempo moods in the tip
function moodCell(t) {
  const h = t.heard_mood; if (!h) return "";
  const top = Object.entries(h).sort((a, b) => b[1] - a[1]).slice(0, 2);
  const line = (name, m) => m ? `\n${name}: ${Object.entries(m).map(([k, v]) => `${k} ${pct(v)}`).join(" · ")}` : "";
  return `<span class="small" data-tip="${esc(`Mood as played${line("as played", h)}${line(`tempo view${t.view_bpm ? ` (${t.view_bpm} BPM)` : ""}`, t.view_mood)}${line("own tempo", t.own_mood)}${t.view_genre ? `\nview genre: ${t.view_genre}` : ""}`)}">${top.map(([k, v]) => `${esc(k)} ${pct(v)}`).join("<br>")}</span>`;
}
// Time labels under a timeline: marks = [[0..1, text]], end = the label at the right edge. A label runs over the empty
// space after it; on a phone every other one hides, and those near the end (they'd overlap it).
function tlAxis(marks, end) {
  const keep = marks.filter(([x]) => x < 0.9);
  return `<div class="tl-x">${keep.map(([x, t], k) => `<span class="${[k % 2 ? "alt" : "", x > 0.75 ? "late" : ""].join(" ").trim()}" style="left:${(x * 100).toFixed(2)}%">${esc(t)}</span>`).join("")}${end != null ? `<span class="end">${esc(end)}</span>` : ""}</div>`;
}
// Your sets stretched to the same length (mixreport.shape): bars = the mean per slice, a faint line per mix
let SHAPE_SHOWN = null, SHAPE_PICK = null;  // SHAPE_PICK: the set whose own line is drawn (its file)
function shapeTimeline(sh) {
  const n = sh.slices, canHype = sh.hype.some(x => x.value != null), view = canHype ? mixView() : "loudness";
  const key = view === "hype" ? "hype" : "lufs", all = [...sh[key].map(x => x.value), ...sh.mixes.flatMap(m => m[key] || [])].filter(v => v != null);
  const lo = Math.min(...all) - 2, hi = Math.max(...all);
  const h = v => v == null ? null : view === "hype" ? v * 10 : (v - lo) / (hi - lo || 1) * 100;
  const step = 100 / n, val = v => v == null ? "–" : view === "hype" ? fmt(v, 1) : `${fmt(v, 1)} LUFS`;
  const name = m => mixTitle({ recording: m.file });
  // per slice the sets, highest first; with many, the 3 highest and 3 lowest
  const perSet = i => {
    const rows = sh.mixes.filter(m => m[key]?.[i] != null).sort((a, b) => b[key][i] - a[key][i])
      .map(m => `${name(m)}: ${val(m[key][i])}${m.colour[i] && view === "hype" ? ` · ${m.colour[i]}` : ""}`);
    return rows.length > 8 ? [...rows.slice(0, 3), `… ${rows.length - 6} more …`, ...rows.slice(-3)] : rows;
  };
  const bar = (x, i) => {
    const c = sh.colour[i], tip = `${Math.round(i * step)}–${Math.round((i + 1) * step)}% of the set\n${view === "hype" ? "hype" : "loudness"} ${val(x.value)} · average of ${x.mixes} ${x.mixes === 1 ? "set" : "sets"}${c ? `\nmostly ${c}` : ""}\n`
      + perSet(i).join("\n");
    return x.value == null ? `<div class="seg none" data-tip="${esc(tip)}"></div>`
      : `<div class="seg" style="height:${h(x.value)}%;${c ? `background:var(--c-${c.toLowerCase()});` : ""}" data-tip="${esc(tip)}"></div>`;
  };
  // per slice, a line from the lowest set to the highest, with caps; a halo keeps it readable on any bar colour
  const range = i => {
    const v = sh.mixes.map(m => m[key]?.[i]).filter(x => x != null);
    if (v.length < 2) return "";
    const y0 = 100 - h(Math.max(...v)), y1 = 100 - h(Math.min(...v)), x = i + 0.5;
    const d = `<line x1="${x}" y1="${y0}" x2="${x}" y2="${y1}"/><line x1="${x - 0.12}" y1="${y0}" x2="${x + 0.12}" y2="${y0}"/><line x1="${x - 0.12}" y1="${y1}" x2="${x + 0.12}" y2="${y1}"/>`;
    return `<g class="halo">${d}</g><g class="range">${d}</g>`;
  };
  // the picked set's own line, broken where it has no value (before the first track, or only unknown tracks)
  const pick = sh.mixes.find(m => m.file === SHAPE_PICK);
  const line = m => (m[key] || []).reduce((runs, v, i) => { if (v == null) runs.push([]); else runs[runs.length - 1].push(`${i + 0.5},${100 - h(v)}`); return runs; }, [[]])
    .filter(r => r.length > 1).map(r => `<polyline class="halo" points="${r.join(" ")}"/><polyline class="one" points="${r.join(" ")}"/>`).join("");
  const used = [...new Set(sh.colour.filter(Boolean))].slice(0, 4);
  return `<div class="filters" style="margin-bottom:2px"><h3 style="margin:0">Your sets start to end <span class="dim small">(${sh.mixes.length} ${sh.mixes.length === 1 ? "set" : "sets"} stretched to the same length)</span></h3><div class="spacer"></div>
      ${canHype ? `<div class="seg-toggle"><button data-shapeview="hype" class="${view === "hype" ? "on" : ""}">Hype</button><button data-shapeview="loudness" class="${view === "loudness" ? "on" : ""}">Loudness</button></div>` : ""}</div>
    <div class="dim small" style="margin-bottom:8px">${view === "hype" ? "Hype 1-10" : "Loudness"} per ${Math.round(step)}% of the set: 50% is the middle of every set, whatever its length. Hover a bar for each set.</div>
    <div class="tl-wrap"><div class="timeline">${sh[key].map(bar).join("")}</div>
      <svg viewBox="0 0 ${n} 100" preserveAspectRatio="none" aria-hidden="true">${sh[key].map((x, i) => range(i)).join("")}${pick ? line(pick) : ""}</svg></div>
    ${tlAxis([0, 0.25, 0.5, 0.75].map(x => [x, `${x * 100}%`]), "100%")}
    <div class="tl-key">
      <div class="sw">${(used.length ? used : [null]).map((c, i) => `<i style="height:${[60, 100, 75, 45][i]}%;background:${c ? `var(--c-${c.toLowerCase()})` : "var(--bar)"}"></i>`).join("")}</div>
      <div><b>Bar</b>: the average ${view === "hype" ? "hype" : "loudness"} of your sets there${used.length ? `; its colour is the ${DJ()} colour played there most` : ""}</div>
      <svg viewBox="0 0 26 16" aria-hidden="true"><g class="halo"><line x1="13" y1="2" x2="13" y2="14"/><line x1="9" y1="2" x2="17" y2="2"/><line x1="9" y1="14" x2="17" y2="14"/></g><g class="range"><line x1="13" y1="2" x2="13" y2="14"/><line x1="9" y1="2" x2="17" y2="2"/><line x1="9" y1="14" x2="17" y2="14"/></g></svg>
      <div><b>Range</b>: from your lowest set to your highest there. Short: your sets agree; long: they don't, so read the bar loosely</div>
      <svg viewBox="0 0 26 16" aria-hidden="true"><polyline points="1,12 9,5 17,10 25,3" style="fill:none;stroke:var(--accent);stroke-width:2.5"/></svg>
      <div class="sets"><span>Show one set:</span>${sh.mixes.map(m => `<button data-shapepick="${esc(m.file)}" class="${m.file === SHAPE_PICK ? "on" : ""}">${esc(name(m))}</button>`).join("")}</div>
    </div>`;
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-shapeview], [data-shapepick]"); if (!b || !SHAPE_SHOWN) return;
  if (b.dataset.shapeview) try { localStorage.setItem("ta-mix-view", b.dataset.shapeview); } catch (err) {}
  if (b.dataset.shapepick) SHAPE_PICK = SHAPE_PICK === b.dataset.shapepick ? null : b.dataset.shapepick;
  b.closest("#shape").innerHTML = shapeTimeline(SHAPE_SHOWN);
  tip.style.display = "none";
});
// What the analysed mixes add up to (mixreport.py)
function renderMixReport(r) {
  const old = r.mixes.filter(m => !m.mood_timeline).length, k = r.keys, v = r.view, a = r.arc;
  const moodRows = Object.entries(v.moods);
  SHAPE_SHOWN = r.shape?.mixes.length ? r.shape : null;
  return `
    <div class="page-head"><div><h2>All your mixes</h2><div class="muted">${r.mixes.length} recorded mixes${old ? ` · ${old} analysed before the CNN key and per-segment mood: analyse them again to count them in` : ""}</div></div></div>
    ${SHAPE_SHOWN ? `<div class="card" id="shape">${shapeTimeline(SHAPE_SHOWN)}</div>` : ""}
    <div class="card"><h3>Keys ${DJ()} may have wrong <span class="dim small">(heard in your mixes = our key, not ${DJ()}'s; ${k.tracks} tracks heard in ${k.mixes} mixes)</span></h3>
      ${k.flags.length ? table([{ h: "Track", f: f => `${esc(f.title || baseName(f.path))}<div class="dim small">${esc(f.artist || "")}</div>` },
        { h: DJ(), f: f => esc(f.traktor) }, { h: "Ours", f: f => esc(f.ours) }, { h: "Heard", f: f => `${f.agree} of ${f.heard.length} ${f.heard.length === 1 ? "mix" : "mixes"}` }], k.flags)
        : `<div class="dim">None${k.mixes ? "" : " yet: needs mixes analysed with the CNN reading the heard key, played with key lock on"}.</div>`}
      <div class="note">A track heard in one mix only is a hint; the same key in several mixes is a good reason to check it in ${DJ()}.</div></div>
    <div class="card"><h3>Mood as played vs the track alone <span class="dim small">(${v.tracks} tracks)</span></h3>
      ${moodRows.length ? table([{ h: "Mood", f: ([m]) => esc(m) }, { h: "As played", r: 1, f: ([, x]) => pct(x.heard) },
        { h: "Tempo view", r: 1, f: ([, x]) => `${pct(x.view)} <span class="dim small">off by ${pct(x.view_error)}</span>` },
        { h: "Own tempo", r: 1, f: ([, x]) => `${pct(x.own)} <span class="dim small">off by ${pct(x.own_error)}</span>` }, { h: "Tracks", r: 1, f: ([, x]) => x.tracks }], moodRows)
        : `<div class="dim">Nothing yet: needs mixes analysed with this version, and a tempo view scanned near the tempo you played.</div>`}
      ${v.genre.tracks ? `<div class="note">Genre as played is the tempo view's on ${v.genre.same_as_view} of ${v.genre.tracks} tracks, the track's own on ${v.genre.same_as_own}.</div>` : ""}
      <div class="note">"Off by": the mean distance from what was heard in the mix. The smaller one predicts what you play better. The mix has blends, the other deck and your effects in it, so expect some distance either way.</div></div>
    <div class="card"><details${SHAPE_SHOWN ? "" : " open"}><summary><h3 style="display:inline">Mood by fifth of the set</h3> <span class="dim small">(measured in ${a.mixes} recordings, each counted once)</span></summary>
      ${a.parts.length ? table([{ h: "Part", f: p => `<span class="nowrap">${p.part} of 5</span>` }, ...[...new Set(a.parts.flatMap(p => Object.keys(p.mood)))].map(m => ({ h: m, r: 1, f: p => pct(p.mood[m]) })), { h: "LUFS", r: 1, f: p => fmt(p.lufs, 1) }, { h: "Mixes", r: 1, f: p => p.mixes }], a.parts)
        : `<div class="dim">Nothing yet: needs mixes analysed with this version.</div>`}
      <div class="note">At the tempo you play, the models may hear sped-up tracks as more aggressive or party than they are alone; compare with the table above before reading much into it.</div></details></div>
    <div class="card"><h3>Changes of track <span class="dim small">(key to key as heard)</span></h3>
      ${Object.keys(r.transitions).length ? `<div>${Object.entries(r.transitions).map(([t, n]) => `<span class="chip ${t === "clash" ? "bad" : t === "two steps" ? "warn" : "good"}">${n} ${esc(t)}</span>`).join(" ")}</div>` : `<div class="dim">Nothing yet.</div>`}
      ${table([{ h: "Mix", f: m => esc(mixTitle({ recording: m.file })) }, { h: "Tracks", r: 1, f: m => m.tracks }, { h: "Key lock", f: m => esc(m.keylock || "–") },
        { h: "Blend lag", f: m => m.blend_lag_seconds == null ? "–" : `${m.blend_lag_seconds} s${m.matches_per_lag ? ` <span class="dim small">${Object.entries(m.matches_per_lag).map(([l, n]) => `${l}:${n}`).join(" ")}</span>` : ""}` },
        { h: "Changes", f: m => m.transitions ? esc(Object.entries(m.transitions).map(([t, n]) => `${n} ${t}`).join(", ")) : "–" }, { h: "Keys heard by", f: m => m.key_model ? esc(m.key_model) : `<span class="dim small" data-tip="Analysed before the model was saved with it: Essentia before 1 Nov 2026, the CNN since">not saved</span>` }], r.mixes)}</div>`;
}
function skippedNote(d) {
  const kind = d.tracklist_skipped_kind || (/^Can't tell when/.test(d.tracklist_skipped) ? "start" : /^No Traktor history/.test(d.tracklist_skipped) ? "history" : "");  // saved before the kind was
  if (kind === "start") return `Its name doesn't say when it started, so ${DJ()}'s history of that session can't be found. If you played it in ${DJ()}, analyse it again with its start time (Analyse a mix… → Started at).`;
  const rb = /Rekordbox/.test(d.tracklist_skipped || "");  // the app it was checked against, maybe not today's
  if (kind === "history" && rb) return `No Rekordbox History playlist covers it${d.recording_start ? ` (it started ${esc(new Date(d.recording_start.replace(" ", "T")).toLocaleString())})` : ""}. Rekordbox writes one per session as you play: check its History folder, and if the start is wrong, analyse the mix again with the right one (Analyse a mix… → Started at).`;
  if (kind === "history") return `No ${DJ()} history covers it${d.recording_start ? ` (it started ${esc(new Date(d.recording_start.replace(" ", "T")).toLocaleString())})` : ""}. ${DJ()} saves a session's history when it closes: quit ${DJ()} once after the set, then analyse the mix again.`;
  return esc(d.tracklist_skipped);
}
// Standalone pieces (standalone.py): long blends that work as their own track, ready to post on SoundCloud
let SA_SHOWN = null;
let SA_MISS = null;  // the closest miss shown when nothing qualifies (has a player too)
const COPY_BTN = `<button class="small" data-sa-copy>Copy</button>`;
const copyBox = (ta) => `<div class="copybox">${ta}${COPY_BTN}</div>`;  // Copy sits in the box's top-right corner
function saFill(card) {  // the chosen name and the full-set link, written into each pick's title and credits
  const link = card.querySelector("#sa-link")?.value.trim() || "<link>";
  card.querySelectorAll("[data-sa-at]").forEach(el => {
    const c = SA_SHOWN.find(x => x.at === el.dataset.saAt), name = el.querySelector("[data-sa-name]").value;
    el.querySelector(".sa-title").value = c.title.replace(/\([^()]*\)$/, `(${name})`);
    el.querySelector(".sa-credits").value = c.credits.replace("<link>", link);
  });
}
const SA_FIT = "min-height:0;field-sizing:content";  // as tall as the text (rows= where field-sizing isn't supported)
// Each change's per-bar levels as a strip: both tracks against their own level, the 12 dB line, the qualifying run
// shaded; click it to hear the mix recording from there (preview player below)
const SA_TOP = 6, SA_LOW = -30;  // dB at the strip's top and bottom
function saRunIdx(c) {  // [first bar, bars] of the run in c.bars (run.from is rounded to the second)
  const t = toSec(c.run.from), bars = c.bars || [];
  let i = 0; bars.forEach((b, k) => { if (Math.abs(b[0] - t) < Math.abs(bars[i][0] - t)) i = k; });
  return [i, c.run.bars || 0];
}
const SA_ADJ = {};  // per piece: {lead, tail} bars added before / after the measured run (− / + on the page)
const saAdj = c => SA_ADJ[c.at] = SA_ADJ[c.at] || { lead: c.run?.lead || 0, tail: c.run?.tail || 0 };  // starts widened to whole phrases
function saRun(c) {  // [start, bars, bar length] of the piece as adjusted; bars 0 when there's no run
  const [i0, n] = saRunIdx(c), [t0, , L] = saSpan(c), j = saAdj(c);
  return n ? [c.bars[i0][0] - j.lead * L, n + j.lead + j.tail, L] : [t0, 0, L];
}
function saSpan(c) {  // [start, end] mix seconds of the strip
  const bars = c.bars, L = c.bar || (bars.length > 1 ? (bars[bars.length - 1][0] - bars[0][0]) / (bars.length - 1) : 2);
  return [bars[0][0], bars[bars.length - 1][0] + L, L];
}
function saStrip(c, nameA, nameB, near) {
  if (!c.bars?.length) return "";
  const [t0, t1, L] = saSpan(c), W = 1000, H = 100, x = t => ((t - t0) / (t1 - t0) * W).toFixed(1);
  const y = db => ((SA_TOP - Math.max(SA_LOW, Math.min(SA_TOP, db ?? SA_LOW))) / (SA_TOP - SA_LOW) * H).toFixed(1);
  const line = k => c.bars.map((b, i) => `${i ? "L" : "M"}${x(b[0] + L / 2)} ${y(b[k])}`).join("");
  const [a, n] = saRun(c), run = n ? `<rect x="${x(a)}" width="${x(a + n * L) - x(a)}" y="0" height="${H}" fill="var(--accent-soft)"/>` : "";
  return `<div class="sa-strip" data-sa-strip="${esc(c.at)}" data-t0="${t0}" data-t1="${t1}" data-tip="Click to hear the mix from there">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Level of both tracks per bar">${run}
      <line x1="0" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line-strong)" vector-effect="non-scaling-stroke"/>
      <line x1="0" x2="${W}" y1="${y(-near)}" y2="${y(-near)}" stroke="var(--text-3)" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>
      <path d="${line(1)}" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>
      <path d="${line(2)}" fill="none" stroke="var(--warn)" stroke-width="2" vector-effect="non-scaling-stroke"/>
      <line class="sa-head" x1="-10" x2="-10" y1="0" y2="${H}" stroke="var(--text)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
    <div class="sa-x"><span>${esc(clockS(t0))}</span><span>${esc(clockS(t1))}</span></div></div>
    <div class="legend small"><span><i style="background:var(--accent)"></i>${nameA}</span><span><i style="background:var(--warn)"></i>${nameB}</span>
      <span>dashed: ${near} dB below its own level · line above: its own level${n ? " · shaded: the run" : ""}</span></div>`;
}
const clockS = t => { t = Math.max(0, Math.round(t)); const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = String(t % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`; };
function saPlayer(c) {  // ▶ from a few bars before the run; jumps into it; stops at the piece's end (but "After the end")
  const [a, n, L] = saRun(c);
  const jump = (label, t, tip, on) => `<button class="small" data-sa-play="${esc(c.at)}" data-t="${Math.max(0, t).toFixed(1)}"${on ? ' data-on="1"' : ""} data-tip="${esc(tip)}">${label}</button>`;
  return `<div class="filters sa-player" data-sa-player="${esc(c.at)}" style="margin:6px 0">
    <button class="small" data-sa-play="${esc(c.at)}" data-t="${Math.max(0, a - 4 * L).toFixed(1)}" data-toggle="1" data-tip="Play the mix recording from 4 bars before ${n ? "the run" : "the change"}">▶ Preview</button>
    ${n ? jump("Lead-in", a - 16 * L, "Play the 16 bars before the run: hear whether the lead-in belongs in the piece")
      + jump("Run start", a, "Jump to where the run starts") + jump("Middle", a + n * L / 2, "Jump to the middle of the run")
      + jump("Last 8 bars", a + (n - 8) * L, "Jump to 8 bars before the piece ends; it stops at the end")
      + jump("After the end", a + (n - 1) * L, "Play from the piece's last bar on into what follows, without stopping: hear whether the piece should run longer", 1) : ""}
    <span class="dim small sa-time"></span></div>`;
}
let SA_NAMES = null;  // [track title by number, near dB] of the panel on screen, to redraw a piece's strip
function saExportRow(c) {  // move the piece's edges in 8-bar steps, fades on/off, export as WAV beside the recording
  const step = (k, d, tip) => `<button class="small" data-sa-adj="${esc(c.at)}" data-k="${k}" data-d="${d}" data-tip="${esc(tip)}">${d < 0 ? "−" : "+"}${Math.abs(d)}</button>`;
  const here = (k, label) => `<button class="small" data-sa-here="${esc(c.at)}" data-k="${k}" data-tip="${esc(`Move the ${k === "lead" ? "start" : "end"} to the bar nearest where the preview is (play or click the strip first)`)}">${label}</button>`;
  return `<div class="filters" style="margin:8px 0 0">
    <span class="small">Start ${step("lead", 8, "Start 8 bars earlier")}${step("lead", 1, "Start 1 bar earlier")}${step("lead", -1, "Start 1 bar later")}${step("lead", -8, "Start 8 bars later")} ${here("lead", "Start here")}</span>
    <span class="small">End ${step("tail", -8, "End 8 bars earlier")}${step("tail", -1, "End 1 bar earlier")}${step("tail", 1, "End 1 bar later")}${step("tail", 8, "End 8 bars later")} ${here("tail", "End here")}</span>
    <label class="inline small" data-tip="1 bar fade in, 2 bars fade out; off: a hard cut on the bar"><input type="checkbox" data-sa-fade checked> Fades</label>
    <button class="small" data-sa-export="${esc(c.at)}" data-tip="Save this piece as a WAV in a 'pieces' folder next to the mix recording, with the title and credits above as tags">Export WAV</button>
    <span class="small" data-sa-out></span></div>`;
}
function saHead(c) {  // [span, bars] of the piece as adjusted
  const [a, n, L] = saRun(c), j = saAdj(c), r = c.run, phrased = (r.lead || r.tail) && j.lead === r.lead && j.tail === r.tail;
  return [`${clockS(a)}–${clockS(a + n * L)}`,
    `${n} bars · ${clockS(n * L)} long` + (j.lead || j.tail ? ` (found: ${r.bars}${phrased ? ", widened to whole phrases" : ""})` : "")];
}
function saAdjust(at, k, d) {
  const c = SA_SHOWN.find(x => x.at === at), j = saAdj(c);
  if (c.run.bars + j.lead + j.tail + d < 8) return;  // a piece keeps 8 bars or more
  j[k] += d;
  const el = document.querySelector(`[data-sa-at="${CSS.escape(at)}"]`), [a, n, L] = saRun(c), [title, near] = SA_NAMES;
  [el.querySelector("[data-sa-span]").textContent, el.querySelector("[data-sa-bars]").textContent] = saHead(c);
  el.querySelector("[data-sa-view]").innerHTML = saPlayer(c) + saStrip(c, title(c.from), title(c.to), near);
  el.querySelector("[data-sa-out]").textContent = "";
  if (SAP.at === at && SAP.end != null) SAP.end = a + n * L;  // the preview playing now stops at the new end
  saUi();
}
function saHere(at, k) {  // move the start (lead) or end (tail) to the bar nearest the preview's playhead
  const c = SA_SHOWN.find(x => x.at === at), a = SAP.audio;
  if (!c || SAP.at !== at || !a || !(a.currentTime > 0)) return;
  const [i0, n] = saRunIdx(c), [, , L] = saSpan(c), j = saAdj(c), r0 = c.bars[i0][0], t = a.currentTime;
  const want = k === "lead" ? Math.round((r0 - t) / L) : Math.round((t - (r0 + n * L)) / L);
  saAdjust(at, k, want - j[k]);
}
// For SoundCloud: genre, BPM and key of a stretch of the set, from the tracklist (genre as heard, tempo as played)
const tGenre = t => t?.genre || t?.library_genre || "", tBpm = t => Math.round(+(t?.played_bpm || t?.bpm) || 0) || null;
const tKey = t => t?.heard_camelot || t?.camelot || "";
const GENRE_PARENT = { "Darkpsy": "Psytrance", "Hi-Tech": "Psytrance" };  // tempo splits of one style: tag the wider genre too, for listeners browsing it
function scInfo(ts, extraTags = []) {
  const g = [...new Set(ts.map(tGenre).filter(Boolean))], b = ts.map(tBpm).filter(Boolean), k = ts.map(tKey).filter(Boolean);
  const lo = Math.min(...b), hi = Math.max(...b);
  const tags = [...new Set([...g, ...g.map(x => GENRE_PARENT[x]).filter(Boolean), ...extraTags, "DJ mix"])].map(x => /\s/.test(x) ? `"${x}"` : x);
  return [`Genre: ${g.join(" / ") || "–"}`, `BPM: ${b.length ? (lo === hi ? lo : `${lo}–${hi}`) : "–"}`,
    ...(k.length ? [`Key: ${k.join(" → ")}`] : []), `Tags: ${tags.join(" ")}`].join("\n");
}
function fullPost(d) {  // the full set's SoundCloud post: main genre, genres, BPM range, tracklist with times
  const ts = d.tracklist?.tracks || []; if (!ts.length) return "";
  const top = (d.dj_genre || []).filter(g => g.score >= 0.1).map(g => `${g.genre} ${Math.round(g.score * 100)}%`);
  const styles = [...new Set((d.timeline || []).flatMap(s => s.styles || []))].slice(0, 5);
  const list = d.standalone?.picks?.length && d.standalone.timestamps?.length ? d.standalone.timestamps
    : ts.map(t => `${t.start} ${t.artist ? t.artist + " - " : ""}${t.title || t.filename || ""}`);
  const txt = [scInfo(ts, styles), ...(top.length ? [`Mix: ${top.join(", ")}`] : []), "", "Tracklist:", ...list].join("\n");
  return `<div class="card"><h3>SoundCloud post <span class="dim small">for the full set: genre, BPM, key, tags, tracklist</span></h3>
    <div class="small muted">SoundCloud's Genre field: <b>${esc((d.dj_genre || [])[0]?.genre || tGenre(ts[0]) || "–")}</b></div>
    ${copyBox(`<textarea readonly rows="${txt.split("\n").length + 1}" style="${SA_FIT}">${esc(txt)}</textarea>`)}</div>`;
}
async function saExport(b) {
  const at = b.dataset.saExport, el = b.closest("[data-sa-at]"), out = el.querySelector("[data-sa-out]"), j = saAdj(SA_SHOWN.find(x => x.at === at));
  b.disabled = true; out.style.color = ""; out.textContent = "exporting…";
  try {
    const d = await postJSON("/api/standalone/export", { id: MIX_ID, at, lead: j.lead || 0, tail: j.tail || 0,
      fade: el.querySelector("[data-sa-fade]").checked, title: el.querySelector(".sa-title").value, credits: el.querySelector(".sa-credits").value });
    out.style.color = "var(--good)";
    out.innerHTML = `✓ ${esc(d.from)}–${esc(d.to)} saved as ${esc(baseName(d.saved))} <button class="mini" data-sa-reveal="${esc(d.saved)}">Show in ${esc(d.file_manager || "file manager")}</button>`;
  } catch (err) { out.style.color = "var(--bad)"; out.textContent = err.message; }
  finally { b.disabled = false; }
}
let MIX_ID = null;  // the saved analysis on screen
const SAP = { audio: null, id: null, at: null, raf: 0 };  // the preview player: the mix recording of the mix on screen
function saAudio() {
  if (SAP.audio) return SAP.audio;
  const a = SAP.audio = new Audio(); a.preload = "none";
  ["play", "pause", "ended"].forEach(ev => a.addEventListener(ev, saUi));
  a.addEventListener("loadedmetadata", () => { if (SAP.seekTo != null) { a.currentTime = SAP.seekTo; SAP.seekTo = null; } });
  a.addEventListener("error", async () => {  // the server says why (moved recording, …)
    let msg = "can't play the recording";
    try { const r = await fetch(a.src, { headers: { Range: "bytes=0-0" } }); if (!r.ok) msg = (await r.json()).error || msg; } catch (e) {}
    SAP.err = msg; a.pause(); saUi();
  });
  DECK.audio.addEventListener("play", () => a.pause());  // one player at a time
  return a;
}
function saPieceEnd(at) {  // mix seconds where the piece (as adjusted) ends
  const c = (SA_SHOWN || []).find(x => x.at === at) || SA_MISS;
  if (!c || c.at !== at) return null;
  const [a, n, L] = saRun(c); return n ? a + n * L : null;
}
function saPlay(at, t, toggle, playOn) {  // playOn: carry on past the piece's end
  const a = saAudio(), id = MIX_ID;
  if (toggle && SAP.at === at && !a.paused) { a.pause(); return; }
  if (toggle && SAP.at === at && SAP.id === id && a.currentTime > 0 && !SAP.err
      && !(SAP.end != null && a.currentTime >= SAP.end - 0.1)) { a.play(); return; }  // ▶ again: carry on (from the start once it reached the end)
  const end = playOn ? null : saPieceEnd(at);
  SAP.end = end != null && t < end - 0.1 ? end : null;  // started past the end (strip click): play on
  if (SAP.id !== id || SAP.err) { SAP.err = null; a.src = `/api/mix-audio?id=${encodeURIComponent(id)}`; SAP.id = id; }
  DECK.audio.pause(); if (DECK.src) deckStopLoop();
  a.volume = deckVolume(); SAP.at = at;
  if (a.readyState >= 1) a.currentTime = t; else SAP.seekTo = t;  // before the file's length is known a seek can be lost
  a.play().catch(e => { if (e.name !== "AbortError") { SAP.err = SAP.err || e.message; saUi(); } });
  saUi();
}
function saUi() {
  const a = SAP.audio, playing = a && !a.paused && !SAP.err;
  document.querySelectorAll("[data-sa-player]").forEach(p => {
    const me = p.dataset.saPlayer === SAP.at;
    p.querySelector("[data-toggle]").textContent = me && playing ? "⏸ Pause" : "▶ Preview";
    p.querySelector(".sa-time").textContent = me && SAP.err ? SAP.err : me && a?.currentTime ? clockS(a.currentTime) : "";
  });
  cancelAnimationFrame(SAP.raf);
  const tick = () => {
    document.querySelectorAll("[data-sa-strip]").forEach(st => {
      const t0 = +st.dataset.t0, t1 = +st.dataset.t1, t = a?.currentTime ?? -1, on = st.dataset.saStrip === SAP.at && t >= t0 && t <= t1;
      const x = on ? ((t - t0) / (t1 - t0) * 1000).toFixed(1) : -10;
      st.querySelector(".sa-head")?.setAttribute("x1", x); st.querySelector(".sa-head")?.setAttribute("x2", x);
    });
    if (playing && SAP.end != null && a.currentTime >= SAP.end) { a.pause(); a.currentTime = SAP.end; return; }  // the piece's end
    const p = document.querySelector(`[data-sa-player="${CSS.escape(SAP.at || "")}"] .sa-time`);
    if (p && a && !SAP.err) p.textContent = clockS(a.currentTime);
    if (playing) SAP.raf = requestAnimationFrame(tick);
  };
  tick();
}
addEventListener("hashchange", () => { if (SAP.audio && !location.hash.startsWith("#mixes/" + encodeURIComponent(SAP.id || ""))) SAP.audio.pause(); });
function saRule(sa) {
  if (sa.near_db == null) return `<div class="small muted">${esc(sa.rule)}</div>`;  // analysed before the page worded it
  return `<div class="small muted" style="margin-bottom:4px">Picked when both tracks stay up together ${sa.min_seconds ? `for ${clockS(sa.min_seconds)} or more (long enough to release as a track; short edits: ${sa.min_bars} bars in whole phrases)` : `for ${sa.min_bars} bars or more (whole phrases)`}: each no more than ${sa.near_db} dB
    below how loud it is on its own (with a stem file, one of its stems is enough)${sa.gap ? `; ${sa.gap === 1 ? "a single bar" : `up to ${sa.gap} bars`} below doesn't break the run` : ""}. Nothing picked is a valid answer.</div>`;
}
function standalonePanel(d) {
  const sa = d.standalone; if (!sa) return "";
  const byAt = Object.fromEntries(sa.changes.map(c => [c.at, c])), tracks = Object.fromEntries((d.tracklist?.tracks || []).map(t => [t.n, t]));
  const title = n => esc(tracks[n]?.title || `track ${n}`), picks = sa.picks.map(at => byAt[at]), shorts = (sa.shorts || []).map(at => byAt[at]), near = sa.near_db ?? 12;
  SA_SHOWN = picks.concat(shorts); SA_NAMES = [title, near];
  for (const k in SA_ADJ) delete SA_ADJ[k];  // a fresh panel shows the runs as found
  const how = Object.entries(sa.measured_with || {}), whole = how.filter(([, h]) => h === "whole").map(([n]) => title(+n));
  const skipped = sa.changes.filter(c => c.status !== "measured"), miss = sa.closest_miss && byAt[sa.closest_miss];
  SA_MISS = miss || null;
  const card = (c, i) => `<details class="sa-piece" open data-sa-at="${esc(c.at)}">
        <summary><span class="sa-num">${i + 1}</span><b data-sa-span>${esc(saHead(c)[0])}</b> · ${title(c.from)} into ${title(c.to)} <span class="dim small" data-sa-bars>${esc(saHead(c)[1])}</span></summary>
        <div class="sa-body"><div class="small muted" style="margin:0 0 6px">Why: ${esc(c.why)}</div>
        <div data-sa-view>${saPlayer(c)}${saStrip(c, title(c.from), title(c.to), near)}</div>
        ${saExportRow(c)}
        <div class="filters" style="flex-wrap:wrap;margin-top:8px"><label class="inline">Name <select data-sa-name>${sa.names.map(n => `<option>${esc(n)}</option>`).join("")}</select></label>
          <span class="dim small">your call: pick what fits how much you changed it</span></div>
        <div style="margin-top:6px">${copyBox(`<textarea class="sa-title" readonly rows="2" style="${SA_FIT};font:inherit;resize:none">${esc(c.title)}</textarea>`)}</div>
        <div style="margin-top:6px"><div class="small muted">Credits</div>${copyBox(`<textarea class="sa-credits" readonly rows="${c.credits.split("\n").length + 1}" style="${SA_FIT}">${esc(c.credits)}</textarea>`)}</div>
        <div style="margin-top:6px"><div class="small muted">Genre, BPM, key and tags</div>${copyBox(`<textarea readonly rows="5" style="${SA_FIT}">${esc(scInfo([tracks[c.from], tracks[c.to]].filter(Boolean)))}</textarea>`)}</div></div></details>`;
  return `<div class="card" id="sa"><h3>Possible sub-mixes <span class="dim small">long blends that work as their own track</span></h3>${saRule(sa)}
    ${SA_SHOWN.length ? (picks.length ? picks.map(card).join("") : `<div class="small muted">No blend long enough to release as a track.</div>`)
      + (shorts.length ? `<h4 style="margin:12px 0 4px">Short edits <span class="dim small">a moment from the set, too short to be a track of its own</span></h4>` + shorts.map(card).join("") : "")
      + `<div class="filters" style="border-top:1px solid var(--line);padding-top:10px;margin-top:8px"><label class="inline" style="flex:1 1 260px">Full set link <input id="sa-link" type="url" placeholder="https://soundcloud.com/…" style="flex:1;min-width:0"></label>
        <span class="dim small">goes into each piece's credits above</span></div>`
      : `<div>Nothing qualifies in this mix, not even as a short edit.${miss ? ` Closest miss: the change at ${esc(miss.at)} into ${title(miss.to)}, ${esc(miss.why)}.` : ""}</div>
        ${miss ? `<div data-sa-at="${esc(miss.at)}" style="margin-top:6px">${saPlayer(miss)}${saStrip(miss, title(miss.from), title(miss.to), near)}</div>` : ""}`}
    ${whole.length && whole.length < how.length ? `<div class="note">Measured without stems (no stem file): ${whole.join(", ")}. Tick "Separate tracks without a stem file" when you analyse it again to measure these by stem.</div>` : ""}
    ${skipped.length ? `<details style="margin-top:8px"><summary class="small muted">Couldn't measure ${skipped.length} ${skipped.length === 1 ? "change" : "changes"}</summary>
      <ul class="small">${skipped.map(c => `<li>${esc(c.at)} ${title(c.to)}: ${esc(c.status)}</li>`).join("")}</ul></details>` : ""}
    </div>
`;
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-sa-play]");
  if (b) { saPlay(b.dataset.saPlay, +b.dataset.t, !!b.dataset.toggle, !!b.dataset.on); return; }
  const adj = e.target.closest("[data-sa-adj]");
  if (adj) { saAdjust(adj.dataset.saAdj, adj.dataset.k, +adj.dataset.d); return; }
  const hr = e.target.closest("[data-sa-here]");
  if (hr) { saHere(hr.dataset.saHere, hr.dataset.k); return; }
  const ex = e.target.closest("[data-sa-export]");
  if (ex) { saExport(ex); return; }
  const rv = e.target.closest("[data-sa-reveal]");
  if (rv) { postJSON("/api/standalone/reveal", { id: MIX_ID, file: rv.dataset.saReveal }).catch(err => { rv.textContent = err.message; }); return; }
  const st = e.target.closest("[data-sa-strip]");
  if (st) { const r = st.getBoundingClientRect(), f = (e.clientX - r.left) / r.width; saPlay(st.dataset.saStrip, +st.dataset.t0 + f * (st.dataset.t1 - st.dataset.t0)); return; }
  const go = e.target.closest("[data-sa-go]");
  if (go) { const el = document.querySelector(`[data-sa-at="${CSS.escape(go.dataset.saGo)}"]`); if (el) { if (el.tagName === "DETAILS") el.open = true; el.scrollIntoView({ behavior: "smooth", block: "center" }); } }
});
// The pieces on the main timeline: segments that overlap one, click to go to it
function saTimelineRow(d, tl) {
  const sa = d.standalone, picks = [...(sa?.picks || []), ...(sa?.shorts || [])].map(at => sa.changes.find(c => c.at === at)).filter(Boolean);
  if (!picks.length) return "";
  const runs = picks.map(c => [toSec(c.run.from), toSec(c.run.to), c.at]);
  return `<div class="tl-clips tl-sa">${tl.map(s => { const r = runs.find(([a, b]) => toSec(s.start) < b && toSec(s.end) > a);
    return r ? `<span class="on" data-sa-go="${esc(r[2])}" data-tip="Possible sub-mix ${esc(clockS(r[0]))}–${esc(clockS(r[1]))}: click to go to it"></span>` : "<span></span>"; }).join("")}</div>`;
}
document.addEventListener("input", e => { if (e.target.closest("#sa-link, [data-sa-name]")) saFill(e.target.closest("#sa")); });
document.addEventListener("click", e => {
  const b = e.target.closest("[data-sa-copy]"); if (!b) return;
  const box = b.closest(".copybox").querySelector("textarea");
  navigator.clipboard.writeText(box.value).then(() => { b.textContent = "Copied"; setTimeout(() => { b.textContent = "Copy"; }, 1500); });
});
function renderMix(d) {
  const tl = d.timeline || [], tlist = d.tracklist, hl = d.highlights || [];
  const lufs = tl.map(s => s.lufs ?? -40), lo = Math.min(...lufs) - 2, hi = Math.max(...lufs);
  const hlSecs = hl.map(h => [toSec(h.start), toSec(h.end)]);
  const segHl = s => hlSecs.some(([a, b]) => toSec(s.start) < b && toSec(s.end) > a);
  const everyX = Math.ceil(tl.length / 10);
  const segLength = s => { const n = Math.round(toSec(s.end) - toSec(s.start)); return n % 60 ? `${n}-second` : `${n / 60}-minute`; };
  const kl = tlist?.keylock;
  if (MIX_SHOWN !== d) MIX_SEL = null;
  MIX_SHOWN = d;
  const sel = MIX_SEL, isOn = (kind, label) => !!sel && sel.kind === kind && sel.label === label;
  const str = sel ? mixSelStrength(d, sel) : null, trackStr = sel && tlist ? trackSelStrength(d, sel) : null;
  const segMood = tl.some(s => s.mood);
  const selName = sel ? `${sel.label}${sel.kind === "mood" ? " (mood)" : ""}` : "";
  // a segment without it fades; with it, fully there (a style or mood in proportion)
  const fade = i => !str || str[i] == null ? "" : `opacity:${(0.12 + 0.88 * str[i] ** 2).toFixed(2)};`;
  const plays = segmentPlays(d), canHype = plays.some(x => x.hype != null), view = canHype ? mixView() : "loudness";
  const lib = d.library || {};
  const segBar = (s, i) => {
    const pl = plays[i], tip = `${s.start}–${s.end}${str?.[i] != null && sel.kind !== "genre" ? `\n${selName}: ${sel.kind === "mood" ? pct(s.mood[sel.label]) : `${Math.round(str[i] * 100)}% of its strongest segment`}` : ""}\n${pl.hype != null ? `hype ${pl.hype.toFixed(1)} · ` : ""}${s.lufs} LUFS\n${s.dj_genre || "–"} · ${s.bpm ?? "–"} BPM · ${s.camelot || "–"} (${s.key || ""})\n${(s.styles || []).join(", ")}${s.mood ? `\nmood: ${Object.entries(s.mood).sort((x, y) => y[1] - x[1]).map(([k, v]) => `${k} ${pct(v)}`).join(" · ")}` : ""}${pl.tracks.length ? "\n♪ " + pl.tracks.join("\n♪ ") : s.tracks?.length ? "\n♪ " + s.tracks.join("\n♪ ") : ""}`;
    const colour = pl.colour ? `background:var(--c-${pl.colour.toLowerCase()});` : "";
    if (view === "loudness") return `<div class="seg ${pl.guessed && !str ? "pred" : ""}" style="height:${((s.lufs ?? lo) - lo) / (hi - lo || 1) * 100}%;${colour}${fade(i)}" data-tip="${esc(tip)}"></div>`;
    return pl.hype == null ? `<div class="seg none" style="${fade(i)}" data-tip="${esc(tip)}"></div>`
      : `<div class="seg ${pl.guessed && !str ? "pred" : ""}" style="height:${pl.hype * 10}%;${colour}${fade(i)}" data-tip="${esc(tip)}"></div>`;
  };
  return `
    <div class="page-head"><div><h2>${esc(mixTitle({ recording: baseName(d.file) }))}</h2><div class="muted">${recDate(d.file) ? `${esc(baseName(d.file))} · ` : ""}${esc(d.duration)} · ${fmt(d.loudness?.integrated_lufs, 1)} LUFS · range ${fmt(d.loudness?.loudness_range_lu, 1)} LU${d.analysis_seconds ? ` · analysed in ${Math.round(d.analysis_seconds)} s` : ""}</div></div></div>
    ${d.tracklist_skipped ? `<div class="note" style="margin-bottom:12px"><b>No tracklist check.</b> ${skippedNote(d)}</div>` : ""}
    <div class="grid">
      <div class="card"><h3>Genre of the mix</h3>${bars((d.dj_genre || []).map(g => ({ label: g.genre, value: g.score, sel: { kind: "genre", label: g.genre }, on: isOn("genre", g.genre) })))}
        ${d.genre_basis?.dj_genre ? `<div class="note">${esc(d.genre_basis.dj_genre)}</div>` : ""}
        ${d.dj_genre_uncorrected ? `<div class="note">Without the psy gate: ${d.dj_genre_uncorrected.map(g => `${esc(g.genre)} ${g.score}`).join(", ")}</div>` : ""}</div>
      <div class="card"><h3>Mood</h3>${bars(Object.entries(d.mood || {}).map(([k, v]) => ({ label: k, value: v, ...(segMood ? { sel: { kind: "mood", label: k }, on: isOn("mood", k) } : {}) })), { max: 1, fmtVal: pct })}
        ${tl.length && !segMood ? `<div class="note">Analyse the mix again to see its moods along the timeline.</div>` : ""}
        <div class="note">vocals ${pct(d.vocal_share)} of the mix · approachability ${fmt(d.approachability)}</div></div>
      <div class="card"><h3>Styles</h3>${bars((d.discogs_styles || []).map(s => ({ label: s.style, value: s.score, sel: { kind: "style", label: s.style }, on: isOn("style", s.style) })))}
        ${d.mixing ? `<div class="note">What the mixing (effects, stems, overlap) added: ${[...(d.mixing.up || []), ...(d.mixing.down || [])].map(esc).join(", ") || "nothing notable"}</div>` : ""}</div>
    </div>
    ${tl.length ? `<div class="card"><div class="filters" style="margin-bottom:2px"><h3 style="margin:0">Timeline</h3><div class="spacer"></div>
        ${sel ? `<button class="chip" data-mixsel="${esc(JSON.stringify(sel))}" data-tip="Stop showing it">${esc(selName)} ✕</button>` : ""}
        ${canHype ? `<div class="seg-toggle"><button data-mixview="hype" class="${view === "hype" ? "on" : ""}">Hype</button><button data-mixview="loudness" class="${view === "loudness" ? "on" : ""}">Loudness</button></div>` : ""}</div>
      <div class="dim small" style="margin-bottom:8px">${view === "hype" ? "Hype" : "Loudness"}${plays.some(x => x.colour) ? ` and ${DJ()} colour of the tracks you played` : ""} per ${segLength(tl[0])} segment; hover for more, click a genre, style or mood above to show where it is</div>
      <div class="timeline">${tl.map(segBar).join("")}</div>
      ${str ? `<div class="tl-clips tl-sel">${tl.map((s, i) => `<span style="opacity:${str[i] ?? 0}"></span>`).join("")}</div>` : ""}
      ${hl.length ? `<div class="tl-clips">${tl.map(s => `<span class="${segHl(s) ? "on" : ""}"></span>`).join("")}</div>` : ""}
      ${saTimelineRow(d, tl)}
      ${tlAxis(tl.flatMap((s, i) => i % everyX ? [] : [[i / tl.length, s.start]]), tl[tl.length - 1].end)}
      <div class="legend">${sel ? `<span><i style="background:var(--accent)"></i>${esc(selName)}: ${sel.kind === "genre" ? "the segment's genre" : sel.kind === "mood" ? "brighter = more of the segment sounds it, against the most" : "brighter = higher among the segment's top 3 styles, against the highest"}${str.every(v => !v) ? " (in no segment)" : ""}</span>` : ""}
        ${view === "hype" || plays.some(x => x.colour) ? `<span>height = ${view === "hype" ? "hype 1-10" : "loudness"}${plays.some(x => x.colour) ? ` · colour = your ${DJ()} colour of the track playing longest${plays.some(x => x.guessed) ? " (faded: the model's guess)" : ""}` : ""}</span>` : ""}
        ${view === "hype" && plays.some(x => x.hype == null) ? `<span><i style="background:var(--line-strong)"></i>track not in your library</span>` : ""}
        ${hl.length ? `<span><i style="background:var(--text)"></i>contains a video clip suggestion</span>` : ""}${d.standalone?.picks?.length ? `<span><i style="background:var(--good)"></i>standalone piece (click to go to it)</span>` : ""}${canHype || d.tracklist ? "" : `<span>${d.tracklist_skipped ? "No tracklist check (see above), so no hype or colours of what you played." : "Analyse it with the tracklist check to see the hype and colours of what you played."}</span>`}</div>
      <details style="margin-top:10px"><summary class="small muted">Table</summary>${table([{ h: "Time", f: s => `${esc(s.start)}–${esc(s.end)}` }, { h: "BPM", r: 1, f: s => fmt(s.bpm) }, { h: "Key", f: s => esc(s.camelot || "–") },
        { h: "LUFS", r: 1, f: s => fmt(s.lufs, 1) }, ...(canHype ? [{ h: "Hype", r: 1, f: s => { const pl = plays[tl.indexOf(s)]; return pl.hype == null ? "–" : `${sw(pl.colour, pl.guessed)} ${fmt(pl.hype, 1)}`; } }] : []), { h: "Genre", f: s => esc(s.dj_genre || "–") }, { h: "Styles", f: s => `<span class="small">${esc((s.styles || []).join(", "))}</span>` }, { h: "Tracks", f: s => `<span class="small">${esc((s.tracks || []).join(" · "))}</span>` }], tl)}</details></div>` : ""}
    ${tlist ? `<div class="card"><h3>Tracklist check <span class="dim small">against ${DJ()}'s history</span></h3>
      <dl class="facts"><dt>History</dt><dd class="mono" style="word-break:break-all">${esc(tlist.history)}</dd><dt>Recording start</dt><dd>${esc(tlist.recording_start)}</dd>
      ${kl ? `<dt>Key lock</dt><dd><b>${esc(kl.verdict)}</b> · ${esc(kl.summary)} <span class="dim small">(${esc(kl.basis)}; blend lag ${kl.blend_lag_seconds} s)</span></dd>` : ""}
      ${kl?.matches_per_lag ? `<dt>Blend lag</dt><dd>${Object.entries(kl.matches_per_lag).map(([lag, n]) => `<span class="${+lag === kl.blend_lag_seconds ? "" : "dim"}">${lag} s: ${n}</span>`).join(" · ")} <span class="dim small">keys matching at each lag; a clear winner means the tracks' stretches are placed right</span></dd>` : ""}
      ${tlist.transitions ? `<dt>Changes</dt><dd>${Object.entries(tlist.transitions).map(([k, n]) => `${n} ${esc(k)}`).join(", ")} <span class="dim small">key to key as heard, on the Camelot wheel</span></dd>` : ""}
      ${tlist.overlap_share != null ? `<dt>Overlap</dt><dd>${pct(tlist.overlap_share)} of the mix has two or more decks playing</dd>` : ""}</dl>
      ${table([{ h: "#", r: 1, f: t => t.n }, { h: "Start", f: t => esc(t.start) }, { h: "Deck", f: t => esc(t.deck || "") }, { h: "Track", f: trackName },
        ...(canHype ? [{ h: "Hype", r: 1, f: t => lib[t.n] ? `<span class="nowrap">${sw(lib[t.n].colour, lib[t.n].guessed)} ${lib[t.n].hype ?? "–"}</span>` : "–" }] : []),
        { h: "BPM", f: t => `${fmt(t.bpm)}${t.played_bpm ? ` → ${t.played_bpm} <span class="dim small">${esc(t.speed || "")}</span>` : ""}` },
        { h: "Key", f: t => `${esc(t.camelot || "–")}${t.heard_camelot && t.heard_camelot !== t.camelot ? ` <span class="dim small">heard ${esc(t.heard_camelot)}</span>` : ""} ${keyChip(t)}`
          + (t.library_camelot && t.library_camelot !== t.camelot ? `<div class="dim small" data-tip="Our key (the CNN's, of the track alone) differs from ${DJ()}'s">ours ${esc(t.library_camelot)}</div>` : "")
          + (t.transition ? `<div class="small"><span class="chip ${t.transition === "clash" ? "bad" : t.transition === "two steps" ? "warn" : "good"}" data-tip="From the track before, key to key as heard">${esc(t.transition)}</span></div>` : "") },
        ...(tlist.tracks?.some(t => t.heard_mood) ? [{ h: "Mood", f: moodCell }] : []),
        { h: "Genre", f: t => `${esc(t.library_genre || "–")}${t.genre && t.genre !== t.library_genre ? ` → <b>${esc(t.genre)}</b>` : ""}${t.genre_uncorrected && t.genre_uncorrected !== t.genre ? `<div class="dim small">ungated ${esc(t.genre_uncorrected)}</div>` : ""}` },
        { h: "Mixing", f: t => t.mixing ? `<span class="small">${[...(t.mixing.up || []), ...(t.mixing.down || [])].map(esc).join(", ")}</span>` : "" },
        { h: "Judged on", f: t => `<span class="small dim">${esc(t.judged_on || t.note || "")}</span>` }], tlist.tracks || [],
        { rowAttr: (t, i) => { const k = trackStr?.strength[i]; if (k == null) return ""; const v = trackStr.value[i];
          return k < 0.5 ? `style="opacity:.45"` : `style="background:color-mix(in srgb, var(--accent) ${Math.round(k ** 2 * 35)}%, transparent)" data-tip="${esc(`${selName}: ${sel.kind === "genre" ? "its genre as heard" : sel.kind === "mood" ? `${pct(v)} over its stretch of the mix` : `${fmt(v, 2)} over its stretch of the mix`}`)}"`; } })}</div>` : ""}
    ${hl.length ? `<div class="card"><h3>Video clip suggestions</h3>${table([{ h: "Rank", r: 1, f: h => h.rank }, { h: "Mix time", f: h => `${esc(h.start)}–${esc(h.end)}` },
        { h: "Video time", f: h => esc(h.video || "–") }, { h: "LUFS", r: 1, f: h => fmt(h.lufs, 1) }, { h: "Rise", r: 1, f: h => `${fmt(h.rise_lu, 1)} LU` }, ...(hl.some(h => h.energy != null) ? [{ h: "Energy", r: 1, f: h => pct(h.energy) }] : []), { h: "Track", f: h => esc(h.track || "") }], hl)}
      <div class="note">Make previews: <span class="mono">clips &lt;this.json&gt; --video &lt;final.mp4&gt;</span></div></div>` : ""}
    ${standalonePanel(d)}
    ${fullPost(d)}
    ${d.chapters?.length ? `<div class="card"><h3>YouTube chapters</h3>${copyBox(`<textarea readonly>${esc(d.chapters.join("\n"))}</textarea>`)}</div>` : ""}
    <details class="card"><summary class="muted">Raw JSON</summary><div class="tree" style="margin-top:8px">${tree(d)}</div></details>`;
}

// Every section on a mix's page and the all-mixes page folds: click its heading. What is folded is remembered per section name.
const FOLD_KEY = "ta-mix-folded";
const foldedSet = () => { try { return new Set(JSON.parse(localStorage.getItem(FOLD_KEY) || "[]")); } catch (e) { return new Set(); } };
const foldName = h => (h.firstChild?.textContent || h.textContent).trim();
function setupFolds() {
  const folded = foldedSet();
  document.querySelectorAll("#mix .card, #fview .card").forEach(card => {
    const h = card.querySelector(":scope > h3, :scope > .filters > h3");
    if (!h || h.dataset.fold) return;
    h.dataset.fold = "1"; h.classList.add("foldable");
    (h.parentElement === card ? h : h.parentElement).classList.add("fold-head");
    card.classList.toggle("folded", folded.has(foldName(h)));
  });
}
new MutationObserver(setupFolds).observe(document.body, { childList: true, subtree: true });
document.addEventListener("click", e => {
  const h = e.target.closest("h3.foldable"); if (!h || e.target.closest("button, a, input, select, label, textarea")) return;
  const card = h.closest(".card"), folded = foldedSet(), name = foldName(h);
  card.classList.toggle("folded") ? folded.add(name) : folded.delete(name);
  try { localStorage.setItem(FOLD_KEY, JSON.stringify([...folded])); } catch (e) { /* not remembered */ }
});
