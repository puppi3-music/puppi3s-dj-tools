// ---------- beat grid check (gridfix.py): does Traktor's grid sit on the kicks? ----------
const GRIDPLAY = { src: null, btn: null };
function gridStop() {
  if (GRIDPLAY.src) { try { GRIDPLAY.src.stop(); } catch (e) {} }
  if (GRIDPLAY.btn) GRIDPLAY.btn.textContent = GRIDPLAY.btn.dataset.label;
  GRIDPLAY.src = GRIDPLAY.btn = null;
}
// A few bars where the kick plays, looped, with a click on each grid line: as Traktor has it (shift 0) or moved
async function gridClick(path, g, shift, btn) {
  if (GRIDPLAY.btn === btn) return gridStop();
  gridStop(); deckStopLoop(); DECK.audio.pause();
  const t0 = g.preview_ms ?? g.grid_ms, beats = 16, beat = g.beat_ms;  // a stretch whose kick agreed with the reading
  btn.dataset.label ??= btn.textContent; btn.textContent = "Loading…"; GRIDPLAY.btn = btn;
  try {
    if (!DECK.ctx) { DECK.ctx = new AudioContext(); DECK.gain = DECK.ctx.createGain(); DECK.gain.gain.value = deckVolume(); DECK.gain.connect(DECK.ctx.destination); }
    if (DECK.ctx.state === "suspended") await DECK.ctx.resume();
    const r = await fetch(`/api/loop?path=${encodeURIComponent(path)}&start=${t0 / 1000}&len=${beats * beat / 1000}`);
    if (!r.ok) throw new Error((await readJSON(r)).error);
    const src0 = await DECK.ctx.decodeAudioData(await r.arrayBuffer());
    if (GRIDPLAY.btn !== btn) return;  // stopped or another one meanwhile
    const sr = src0.sampleRate, n = src0.length, buf = DECK.ctx.createBuffer(src0.numberOfChannels, n, sr);
    const clickLen = Math.round(0.03 * sr), MUSIC = 0.45;  // the music turned down so the click stands out (mastered tracks sit near full scale)
    for (let ch = 0; ch < src0.numberOfChannels; ch++) {
      const x = buf.getChannelData(ch), y = src0.getChannelData(ch);
      for (let i = 0; i < n; i++) x[i] = MUSIC * y[i];
      for (let k = 0; k < beats; k++) {  // a 3 kHz tick on every grid line, accented on the bar: well above the kick and bass
        const at = Math.round(((k * beat + shift) / 1000) * sr), amp = k % 4 ? 0.4 : 0.55;
        for (let i = 0; i < clickLen; i++) { const j = ((at + i) % n + n) % n; x[j] += amp * Math.sin(2 * Math.PI * 3000 * i / sr) * Math.exp(-i / (0.008 * sr)); }
      }
    }
    const src = DECK.ctx.createBufferSource(); src.buffer = buf; src.loop = true; src.connect(DECK.gain); src.start();
    GRIDPLAY.src = src; btn.textContent = "■ Stop";
  } catch (e) { gridStop(); btn.textContent = e.message; }
}
function renderGrid(g) {
  const ms = v => `${v > 0 ? "+" : ""}${(+v).toFixed(Math.abs(v) < 10 ? 1 : 0)} ms`;
  const n = g.agreeing ?? (g.sections || []).filter(x => x != null).length;  // the stretches that agreed with the reading
  const appr = g.approved != null;  // an approved fix stays visible (and can be taken back) whatever the check reads now
  if (g.state === "unclear" && !appr) return `<div class="small dim">Couldn't tell: no steady kick to measure against the grid.</div>`;
  if (g.state === "drift" && !appr) return `<div class="small"><span class="chip warn">BPM drifts</span> The kick moves ${ms(g.drift_ms)} against the grid from the start of the track to the end: ${DJ()}'s BPM looks slightly off. Moving the grid can't fix that; check the BPM in ${DJ()}.</div>`;
  if (g.state === "on" && !appr) return `<div class="small"><span class="chip good">on the kicks</span> <span class="muted">the kick lands ${ms(g.offset_ms)} from the grid lines (${n} stretches measured).</span></div>`;
  const stem = g.stem;
  const stemNote = !stem ? "" : stem.matches === false
    ? `<div class="warnbox small" style="margin:6px 0"><b>Its stem file is ${ms(stem.shift_ms)} off the track.</b> Traktor uses one grid for both, so they can't both be right: remake the stem first (NUO Stems 4.1 or later lined up in testing), then come back. <span class="mono dim">${esc(stem.stem.split(/[\\/]/).slice(-2).join("/"))}</span></div>`
    : stem.matches ? `<div class="small muted">Its stem file lines up with the track, so the moved grid fits both.</div>`
    : `<div class="small dim">Stem file: ${esc(stem.why || "not compared")}.</div>`;
  const val = g.approved ?? g.suggested_ms;
  return `<div class="small"><span class="chip warn">${appr ? "fix approved" : `kick ${ms(g.offset_ms)}`}</span>
      ${g.offset_ms == null ? `The check now reads ${g.state === "drift" ? `a BPM drift (${ms(g.drift_ms)} from start to end)` : "no steady kick"} on this grid, so it can't confirm the approved move: check it by ear below, or take it back.`
        : `The kick lands <b>${ms(g.offset_ms)}</b> ${g.offset_ms > 0 ? "after" : "before"} ${DJ()}'s grid lines, the same in ${n} stretches across the track.`}
      ${g.lead_in ? `That's the size of an MP3's lead-in (51.2 ms), what you get from a grid set on a stem file made without it, so the fix is set to exactly that.` : ""}</div>
    ${stemNote}
    <div class="filters small" style="margin-top:6px;align-items:center">
      <button type="button" class="mini" id="gr-now" data-tip="A few bars where the kick plays, looped, with a click on each of ${DJ()}'s grid lines as they are now">▶ Click on the grid now</button>
      <button type="button" class="mini" id="gr-moved" data-tip="The same bars with the click on the moved grid lines: it should land on the kick">▶ Click on the moved grid</button>
      <label class="inline">move it <input type="number" id="gr-shift" step="0.1" value="${esc(val)}" style="width:72px"> ms ${g.offset_ms > 0 ? "later" : "(negative: earlier)"}</label>
      ${g.approved != null ? `<span class="chip good">✓ approved: ${ms(g.approved)}, not in ${DJ()} yet</span><button type="button" id="gr-write" class="primary">Write grid fixes to ${DJ()}…</button><button type="button" id="gr-unapprove">Take back</button>`
        : `<button type="button" id="gr-approve" class="primary" data-tip="Your cues, loops and markers on this track move by the same amount, so they stay on the beat">Approve</button><button type="button" id="gr-ignore" data-tip="Leave this grid as it is: it won't be proposed again unless the grid changes">The grid is right</button>`}
      <span id="gr-msg" class="muted"></span></div>`;
}
async function loadGrid(t, g) {
  const box = $("#gridbox"); if (!box) return;
  try { g ??= await api("grid", { path: t.path }); } catch (e) { box.className = "small dim"; box.textContent = e.message; return; }
  if ($("#drawer").dataset.path !== t.path) return;
  gridStop();  // approved, taken back or written: the section is drawn again, and its play button with it
  const state = $("#gridstate");
  if (state) state.innerHTML = g.approved != null ? `<span class="chip good">fix approved</span>` : g.state === "drift" ? `<span class="chip warn">BPM drifts</span>`
    : g.state === "on" ? "on the kicks" : g.state === "unclear" ? "couldn't tell" : `<span class="chip warn">kick ${g.offset_ms > 0 ? "+" : ""}${(+g.offset_ms).toFixed(0)} ms</span>`;
  box.className = ""; box.innerHTML = renderGrid(g) + gridWaveHTML(g);
  const shift = () => +($("#gr-shift")?.value || 0), msg = m => { const e = $("#gr-msg"); if (e) e.textContent = m; };
  gridWave(t.path, g, box, shift);
  const after = async (d, text) => { await loadGrid(t, d); msg(text); S.tracks = {}; if (S.page === "tracks") S.reloadTracks?.(); };
  $("#gr-now")?.addEventListener("click", e => gridClick(t.path, g, 0, e.currentTarget));
  $("#gr-moved")?.addEventListener("click", e => gridClick(t.path, g, shift(), e.currentTarget));  // the moved lines: the old ones + the shift
  $("#gr-shift")?.addEventListener("change", () => { if (GRIDPLAY.btn?.id === "gr-moved") { const b = GRIDPLAY.btn; gridStop(); gridClick(t.path, g, shift(), b); } });
  $("#gr-approve")?.addEventListener("click", async () => { try { await after(await postJSON("/api/grid/approve", { path: t.path, shift_ms: shift() }), "Approved"); } catch (e) { msg(e.message); } });
  $("#gr-unapprove")?.addEventListener("click", async () => { try { await after(await postJSON("/api/grid/approve", { path: t.path, shift_ms: null }), "Taken back"); } catch (e) { msg(e.message); } });
  $("#gr-ignore")?.addEventListener("click", async () => { try { await after(await postJSON("/api/grid/ignore", { path: t.path, on: true }), "Marked as right"); } catch (e) { msg(e.message); } });
  $("#gr-write")?.addEventListener("click", () => writeGrids(async () => { const top = $("#drawer").scrollTop; await openTrack(t.path); $("#drawer").scrollTop = top; S.tracks = {}; if (S.page === "tracks") S.reloadTracks?.(); }));
}
// A close look at a few beats: the waveform with Traktor's grid lines on it (and the moved ones, dashed), to check by eye
const GRID_VIEW = { beats: 2 };  // beats on screen, kept from track to track
function gridWaveHTML(g) {
  const stretches = (g.section_starts_ms || []).map((t, i) => [t, (g.sections || [])[i]]);
  const mmss = ms => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;
  const read = v => v == null ? "no clear kick" : `kick ${v > 0 ? "+" : ""}${v.toFixed(1)} ms`;
  return `<div class="filters small" style="margin-top:10px;align-items:center">
      <button type="button" class="mini" id="gw-prev" data-tip="One bar earlier">‹ bar</button><button type="button" class="mini" id="gw-next" data-tip="One bar later">bar ›</button>
      ${stretches.length ? `<select id="gw-at" data-tip="The stretches the check measured, with where it read the kick in each">${stretches.map(([t, v]) =>
        `<option value="${t}" ${t === g.preview_ms ? "selected" : ""}>${mmss(t)} · ${read(v)}</option>`).join("")}</select>` : ""}
      <span class="inline">show ${[1, 2, 4, 8].map(k => `<button type="button" class="mini ${k === GRID_VIEW.beats ? "on" : ""}" data-gwbeats="${k}">${k === 4 ? "1 bar" : k === 8 ? "2 bars" : `${k} beat${k > 1 ? "s" : ""}`}</button>`).join("")}</span>
      <span id="gw-pos" class="muted mono"></span></div>
    <div class="gridwave"><canvas id="gw-canvas" aria-label="Close-up of the waveform with the beat grid lines on it"></canvas></div>
    <div class="small muted" style="margin-top:4px">White lines: ${DJ()}'s grid now (thicker on the bar). <span style="color:#ffd23f">Yellow dashed: the grid moved</span> by the amount above. The kick's attack (red, low) should start on the line.</div>`;
}
function gridWave(path, g, box, shift) {
  const cv = box.querySelector("#gw-canvas"); if (!cv) return;
  const beat = 60000 / g.bpm, lineAt = ms => g.grid_ms + Math.round((ms - g.grid_ms) / beat) * beat;  // the grid line nearest a time
  let at = lineAt(g.preview_ms ?? (g.section_starts_ms || [])[Math.floor((g.section_starts_ms || []).length / 2)] ?? g.grid_ms);
  let data = null, seq = 0, timer = 0;
  // the stretch on screen: from a quarter beat before a grid line to a quarter beat after the last one shown
  const view = () => { const v0 = Math.max(0, at - beat / 4); return [v0, at + (GRID_VIEW.beats + 0.25) * beat]; };
  function fetchWave() {
    const [v0, v1] = view(), n = Math.round(cv.clientWidth * (window.devicePixelRatio || 1)), k = ++seq;
    if (!n) return;
    if (data && data.t0 <= v0 / 1000 + 1e-6 && data.t0 + data.sec >= v1 / 1000 - 1e-6 && data.n / data.sec >= n / ((v1 - v0) / 1000) * 0.7) return draw();
    clearTimeout(timer);
    timer = setTimeout(() => api("wave", { path, t0: (v0 / 1000).toFixed(4), t1: (v1 / 1000).toFixed(4), n })
      .then(w => { if (k === seq) { data = decodeWave(w); draw(); } }).catch(e => { const p = box.querySelector("#gw-pos"); if (p) p.textContent = e.message; }), data ? 80 : 0);
    draw();
  }
  function draw() {
    const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    if (!W) return;
    if (cv.width !== Math.round(W * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
    const gx = cv.getContext("2d"); gx.setTransform(dpr, 0, 0, dpr, 0, 0); gx.clearRect(0, 0, W, H);
    const [v0, v1] = view(), X = ms => (ms - v0) / (v1 - v0) * W, TOP = 18, BOT = H - 16;
    if (data) waveColumns(gx, data, v0 / 1000, v1 / 1000, TOP, BOT, W, 1);
    else { gx.fillStyle = "rgba(255,255,255,.4)"; gx.font = "11px system-ui, sans-serif"; gx.fillText("Loading…", 8, H / 2); }
    gx.font = "10px system-ui, sans-serif"; gx.textAlign = "left";
    const k0 = Math.ceil((v0 - g.grid_ms) / beat) - 1, k1 = Math.floor((v1 - g.grid_ms) / beat) + 1, sh = shift();
    for (let k = k0; k <= k1; k++) {
      const t = g.grid_ms + k * beat, down = ((k % 4) + 4) % 4 === 0, x = Math.round(X(t));
      gx.fillStyle = down ? "rgba(255,255,255,.95)" : "rgba(255,255,255,.7)"; gx.fillRect(x - (down ? 1 : 0), 4, down ? 2 : 1, H - 8);
      gx.fillText(down ? `bar ${Math.floor(k / 4) + 1}` : `${((k % 4) + 4) % 4 + 1}`, x + 3, 13);
      if (Math.abs(sh) >= 0.05) {
        const m = X(t + sh);
        gx.setLineDash([4, 3]); gx.strokeStyle = "#ffd23f"; gx.lineWidth = 1.5;
        gx.beginPath(); gx.moveTo(m, TOP); gx.lineTo(m, H - 4); gx.stroke(); gx.setLineDash([]); gx.lineWidth = 1;
      }
    }
    // a 10 ms ruler, to judge how far off a kick is
    const r = X(v0 + 10) - X(v0), y = H - 6;
    gx.fillStyle = "rgba(255,255,255,.7)"; gx.fillRect(W - 12 - r, y, r, 2); gx.fillRect(W - 12 - r, y - 3, 1, 5); gx.fillRect(W - 13, y - 3, 1, 5);
    gx.textAlign = "right"; gx.fillText("10 ms", W - 16 - r, y + 3);
    const p = box.querySelector("#gw-pos"); if (p) p.textContent = `${(at / 1000).toFixed(2)} s`;
  }
  const go = ms => { at = lineAt(Math.max(0, ms)); fetchWave(); };
  box.querySelector("#gw-prev")?.addEventListener("click", () => go(at - 4 * beat));
  box.querySelector("#gw-next")?.addEventListener("click", () => go(at + 4 * beat));
  box.querySelector("#gw-at")?.addEventListener("change", e => go(+e.target.value));
  box.querySelectorAll("[data-gwbeats]").forEach(b => b.addEventListener("click", () => {
    GRID_VIEW.beats = +b.dataset.gwbeats; box.querySelectorAll("[data-gwbeats]").forEach(o => o.classList.toggle("on", o === b)); fetchWave();
  }));
  box.querySelector("#gr-shift")?.addEventListener("input", draw);
  box._gwObs?.disconnect();  // the section is redrawn after approve / take back / write: drop the old canvas's watcher
  box._gwObs = new ResizeObserver(() => fetchWave()); box._gwObs.observe(cv);
}
// Write every approved grid fix into the chosen DJ app (= `grids apply`): a preview first, then the write, then Undo.
async function writeGrids(onDone) {
  const dlg = document.createElement("dialog"); dlg.className = "write"; document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  const show = (html, buttons) => {
    dlg.innerHTML = `<h3>Write grid fixes to ${DJ()}</h3>${html}<div class="filters" style="margin-top:12px">${buttons}<button data-close>Close</button><span class="small muted" id="wr-msg"></span></div>`;
    dlg.querySelector("[data-close]").onclick = () => dlg.close();
  };
  const fail = e => { const m = dlg.querySelector("#wr-msg"); if (m) m.textContent = e.message; else show(errBox(e), ""); };
  const list = items => items.length ? `<ul>${items.slice(0, 300).map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  show(`<div class="muted">Checking what would change…</div>`, ""); dlg.showModal();
  let d; try { d = await postJSON("/api/grid/write", { dry_run: true }); } catch (e) { return show(errBox(e), ""); }
  if (d.nothing_approved) return show(`<div>Nothing approved yet.</div>`, "");
  const running = d.traktor_running ? `<div class="warnbox"><b>Quit ${DJ()} first.</b> It would overwrite or clash with this.</div>` : "";
  show(`${running}<div>${d.tracks} track${d.tracks === 1 ? "" : "s"}: grid moved${d.cues_moved ? `, with ${d.cues_moved} cues, loops and markers` : ""}.</div>${list(d.changes || [])}
    ${(d.tempo_changes || []).length ? `<div class="warnbox"><b>${d.tempo_changes.length} track${d.tempo_changes.length === 1 ? "" : "s"} with tempo changes in ${DJ()}'s grid left alone</b> (the fix writes one steady tempo): ${esc(d.tempo_changes.join(", "))}</div>` : ""}
    ${(d.skipped || []).length ? `<div class="small"><b>Left alone:</b>${list(d.skipped)}</div>` : ""}
    <div class="note small">A backup of the collection${DJ() === "Rekordbox" ? " (master.db and the grid files)" : ""} is made first; Undo puts it back.</div>`,
    d.changed ? `<button class="primary" data-write ${d.traktor_running ? "disabled" : ""}>Write to ${DJ()}</button>` : "");
  dlg.querySelector("[data-write]")?.addEventListener("click", async e => {
    const btn = e.currentTarget;  // currentTarget is null once the handler awaits
    btn.disabled = true; btn.textContent = "Writing…";
    let r; try { r = await postJSON("/api/grid/write", { dry_run: false }); } catch (err) { btn.disabled = false; btn.textContent = `Write to ${DJ()}`; return fail(err); }
    show(`<div style="color:var(--good)"><b>✓ Written:</b> ${r.tracks} grid${r.tracks === 1 ? "" : "s"} moved${r.cues_moved ? `, with ${r.cues_moved} cues, loops and markers` : ""}.</div>
      <div class="note">Your approved cues on these tracks moved with them. Backup: <span class="mono">${esc(r.backup || "")}</span><br>${DJ() === "Rekordbox" ? "Undo works until Rekordbox changes master.db again." : "Undo works until Traktor saves the collection again (it does when it quits)."}</div>`, `<button data-undo>Undo</button>`);
    dlg.querySelector("[data-undo]").onclick = async ev => {
      const btn = ev.currentTarget; btn.disabled = true;
      try { const u = await postJSON("/api/grid/undo", {}); show(`<div><b>Undone.</b> ${DJ() === "Rekordbox" ? "master.db and the grid files are" : "collection.nml is"} back to how it was${u.approvals_restored ? `, and your ${u.approvals_restored} grid fix${u.approvals_restored > 1 ? "es are" : " is"} approved again` : ""}.</div>`, ""); onDone?.(); }
      catch (err) { btn.disabled = false; fail(err); }
    };
    onDone?.();
  });
}
let openSeq = 0;  // each openTrack call; a slower earlier call or its cue reply doesn't land over a newer one
async function openTrack(path) {
  const seq = ++openSeq;
  prepFor(path);
  if (DECK.path && DECK.path !== path) { deckStopLoop(); DECK.audio.pause(); }
  if ($("#drawer").dataset.path !== path) gridStop();  // the grid click loop belongs to the track it was started on
  const all = await getTracks(), t = all.find(x => x.path === path);
  if (!t || seq !== openSeq) return;
  markOpen(path); hashTrack(path);
  const d = $("#drawer"), body = $("#drawer-body");
  const moods = ["aggressive", "happy", "party", "relaxed", "sad"].filter(m => t["mood_" + m] != null).map(m => ({ label: m, value: t["mood_" + m] }));
  const genreScores = [[t.genre, t.genre_score], [t.genre_2, t.genre_2_score], [t.genre_3, t.genre_3_score]].filter(g => g[0]).map(([g, s]) => ({ label: g, value: s }));
  const yourG = t.trained_genre || t.nearest_genre;  // your genre where one fits, else the closest of yours ("Mine only" uses it)
  body.innerHTML = `
    <div class="drawer-head"><div><h2>${esc(t.title || t.filename)}</h2><div class="muted">${esc(t.artist || "")}</div></div>
      <div class="acts">${canEdit(t) ? `<button id="dr-edit" class="${editing(t) ? "on" : ""}" aria-pressed="${editing(t)}" data-tip="${editing(t) ? "Stop editing without saving" : "Change this track's BPM, key, colour, genre or hype by hand"}">✎ Edit</button>` : ""}<button class="wide-btn" id="dr-wide" aria-pressed="${drawerWide()}" data-tip="${drawerWide() ? "Narrow the panel again" : "Widen the panel: more room for the waveform and cues"}">${drawerWide() ? "⇥ Narrow" : "⇤ Wide"}</button>
      <button id="dr-close" aria-label="Close" data-tip="Close (Esc)">✕</button></div></div>
    <dl class="facts">
      <dt>BPM</dt><dd>${ed(t, "bpm", fmt(t.mix_bpm))}${t.traktor_bpm != null ? ` <span class="dim small">${DJ()} ${fmt(Math.round(t.traktor_bpm))} · ours ${fmt(t.bpm != null ? Math.round(t.bpm) : null)}</span>` : ""}${t.source_bpm ? ` <span class="dim small">· own tempo ${Math.round(t.source_bpm)}</span>` : ""}</dd>
      <dt>Key</dt><dd>${ed(t, "key", esc(t.mix_camelot || "–"))} <span class="dim small">${t.traktor_camelot ? `${DJ()} ${esc(t.traktor_camelot)} · ` : ""}ours ${esc(t.camelot || "–")} (${esc(t.key || "–")})</span></dd>
      <dt>Colour</dt><dd>${ed(t, "colour", colour(colourOf(t), !t.traktor_color))}${t.your_colour ? ` <span class="dim small">yours at its own tempo: ${esc(t.your_colour)} ·</span>` : ""}${t.traktor_color && t.color ? ` <span class="dim small"${t.colour_unbiased ? ` data-tip="${UNBIASED_TIP}"` : ""}>model: ${esc(t.color)}${t.color_confidence != null ? ` (${pct(t.color_confidence)})` : ""}${t.color_confidence_near != null ? ` · ${pct(t.color_confidence_near)} sure it's ${esc(nearRange(t.color))}` : ""}</span>` : ""}${t.kept ? ` <span class="chip">kept always</span>` : ""}</dd>
      <dt>Genre</dt><dd>${ed(t, "genre", esc(t.my_genre || t.genre || "–") + (t.held_genre ? lock("Genre") : ""), all)}</dd>
      <dt>Hype</dt><dd>${ed(t, "hype", hypeBadge(t.hype))} <span class="dim small">1-10 within your collection</span></dd>
      <dt>Loudness</dt><dd>${t.lufs != null ? `${fmt(t.lufs, 1)} LUFS${t.loudness_range != null ? ` · range ${fmt(t.loudness_range, 1)} LU` : ""}` : "–"}</dd>
      <dt>Length</dt><dd>${mmss(t.duration_sec)}</dd>
      ${t.error ? `<dt>Note</dt><dd class="small">${esc(t.error)}</dd>` : ""}
    </dl>${editBar(t)}
    ${t.type === "track" ? `<div class="section"><h3>Cues and phrases</h3><div id="cuebox" class="loading">Loading…</div></div>` : ""}
    ${t.type === "track" && !SNAP ? fold("grid", "Beat grid", `<div id="gridbox" class="loading small">Checking where the kicks land…</div>`, `<span id="gridstate" class="hint"></span>`) : ""}
    ${t.type === "track" ? fold("mixes", "Mixes well with", `<div class="filters small"><label class="inline"><input type="checkbox" id="sg-same" checked ${SNAP ? "disabled" : ""}> same genre</label>
      <label class="inline">BPM ± <input type="number" id="sg-tol" value="4" min="0" max="50" style="width:56px" ${SNAP ? "disabled" : ""}>%</label></div><div id="sugg" class="loading">Loading…</div>`) : ""}
    ${genreScores.length || t.styles || yourG || t.tag_genre ? fold("genre", "Genre and style", `
      ${genreScores.length ? `<div class="sub">Genres</div>${bars(genreScores, { max: Math.max(...genreScores.map(g => g.value || 0), 0.01) })}` : ""}
      ${t.styles ? `<div class="sub">Discogs styles</div>${bars(parseScores(t.styles))}` : ""}
      ${yourG || t.tag_genre ? `<dl class="facts">${yourG ? `<dt>Your genre</dt><dd>${esc(yourG)} <span class="dim small">${pct(t.trained_genre ? t.trained_genre_confidence : t.nearest_genre_confidence)}
        ${t.trained_genre ? "" : "· closest of yours, doesn't clearly fit "}${t.my_genre === yourG ? "· in use" : t.my_genre ? "· your hand-set genre is used" : SNAP ? "· not in use" : `· not in use: <a href="#setup">Setup › Genres used</a>`}</span></dd>` : ""}${t.tag_genre ? `<dt>Genre tag</dt><dd>${esc(t.tag_genre)}</dd>` : ""}</dl>` : ""}`,
      hint(genreScores[0]?.label)) : ""}
    ${moods.length || t.instruments ? fold("mood", "Mood and instruments", `
      ${moods.length ? `<div class="sub">Mood</div>${bars(moods, { max: 1, fmtVal: v => pct(v) })}` : ""}
      ${t.instruments ? `<div class="sub">Instruments <span class="dim">(compare tracks, not absolute)</span></div>${bars(parseScores(t.instruments), { max: 1 })}` : ""}`,
      hint(moods.length ? moods.reduce((a, b) => b.value > a.value ? b : a).label : "")) : ""}
    ${t.color_position != null || t.traktor_color ? fold("scale", "Colour scale", `<div class="small dim" style="margin-bottom:6px">Red = hardest … Magenta = prettiest</div>${scaleView(t)}
      ${t.colour_guess ? `<div class="note">${guessMark()} ${GUESS_TIP()}${SNAP ? "" : ` <button id="dr-accept">It's right</button>`}</div>` : ""}`,
      hint(colourOf(t) + (t.colour_guess ? " · guessed" : ""))) : ""}
    ${t.type === "track" ? fold("hold", "Hold genre or colour", `<div class="holds">
      ${["genre", "colour"].map(k => `<span>${k === "genre" ? "Genre" : "Colour"}</span><span>${SNAP ? (t["held_" + k] ? `${LOCK} held` : `<span class="dim">not held</span>`)
        : `<button data-hold="${k}" class="${t["held_" + k] ? "on" : ""}">${t["held_" + k] ? `${LOCK} Held · release` : "Hold"}</button>`}</span>`).join("")}</div>
      <div class="note">A held genre or colour stays as it is in your files, ${DJ()} and Lexicon: <span class="mono">tag</span>, <span class="mono">traktor</span>,
      <span class="mono">colors review --apply</span> and <span class="mono">tempo load</span> skip it. Holding only changes the analyzer's list; nothing is written now.</div>`,
      hint([t.held_genre && "genre held", t.held_colour && "colour held"].filter(Boolean).join(" · "))) : ""}
    ${fold("file", "File", `<dl class="facts" style="margin:0">${t.album ? `<dt>Album</dt><dd>${esc(t.album)}</dd>` : ""}<dt>Path</dt><dd class="mono" style="word-break:break-all">${esc(t.path)}</dd></dl>`,
      hint(t.path.split(/[\\/]/).pop()))}`;
  $("#dr-close").onclick = closeDrawer;
  $("#dr-wide").onclick = () => {
    const wide = !drawerWide();
    try { localStorage.setItem("ta-drawer-wide", wide ? "1" : "0"); } catch (e) {}
    S.drawerWide = wide; d.classList.toggle("wide", wide);
    const b = $("#dr-wide"); b.textContent = wide ? "⇥ Narrow" : "⇤ Wide"; b.setAttribute("aria-pressed", wide);
    b.dataset.tip = wide ? "Narrow the panel again" : "Widen the panel: more room for the waveform and cues"; tip.style.display = "none";
  };
  d.classList.toggle("wide", drawerWide());
  const acc = body.querySelector("#dr-accept");
  if (acc) acc.onclick = async () => {
    try { await acceptColours([t.path]); const top = d.scrollTop; await openTrack(t.path); d.scrollTop = top; S.page === "tracks" && S.redrawTracks?.(); }
    catch (e) { acc.insertAdjacentHTML("afterend", ` <span class="small" style="color:var(--bad)">${esc(e.message)}</span>`); }
  };
  $("#dr-edit")?.addEventListener("click", () => { S.editing = editing(t) ? null : t.path; if (S.editing) S.editMsg = null; reopen(t.path); });
  bindEdit(t);
  body.querySelectorAll("[data-hold]").forEach(b => b.onclick = async () => {
    const k = b.dataset.hold;
    try { await hold([t.path], k, !t["held_" + k]); const top = d.scrollTop; await openTrack(t.path); d.scrollTop = top; S.page === "tracks" && S.redrawTracks?.(); }
    catch (e) { b.nextElementSibling?.remove(); b.insertAdjacentHTML("afterend", ` <span class="small" style="color:var(--bad)">${esc(e.message)}</span>`); }
  });
  const keepScroll = d.classList.contains("open") && d.dataset.path === t.path;
  d.dataset.path = t.path;
  d.classList.add("open"); d.setAttribute("aria-hidden", "false"); if (!keepScroll) d.scrollTop = 0;
  if (t.type !== "track") return;
  if (!SNAP) loadGrid(t);
  api("cues", { path: t.path }).then(c => { const b = $("#cuebox"); if (b && seq === openSeq) { b.className = ""; b.innerHTML = renderCues(c); drawWave(c, b); if (!SNAP) { bindApprove(c, b, t); deckAttach(c, b); deckPrefetch(); loadParts(c, b, t.path); } } })
    .catch(e => { const b = $("#cuebox"); if (b && seq === openSeq) { b.className = ""; b.innerHTML = `<div class="dim small">${esc(e.message)}</div>`; } });
  let sgSeq = 0;
  const load = async () => {
    const box = $("#sugg"), k = ++sgSeq;
    const fresh = () => k === sgSeq && box.isConnected && d.dataset.path === t.path;  // a slower earlier reply, or another track's, doesn't land here
    try {
      const tol = Math.min(Math.max(+$("#sg-tol").value || 4, 0), 50); $("#sg-tol").value = tol;
      const r = await api("suggest", { lib: S.lib, path: t.path, same_genre: $("#sg-same").checked ? 1 : 0, tolerance: tol });
      if (!fresh()) return;
      box.className = "";
      box.innerHTML = `<div class="dim small" style="margin-bottom:6px">${esc(r.rule)} · ${r.matches} matches</div>` + table([
        { h: "", f: s => sw(s.traktor_color || s.color, !s.traktor_color) }, { h: "Track", f: trackName },
        { h: "BPM", r: 1, f: s => fmt(s.bpm) }, { h: "Key", f: s => esc(s.camelot || "–") }, { h: "Genre", f: s => esc(s.genre || "–") },
      ], r.tracks, { rowAttr: s => `class="click" data-path="${esc(s.path)}"` });
      box.onclick = e => { const tr = e.target.closest("tr[data-path]"); if (tr) openTrack(tr.dataset.path); };
    } catch (e) { if (fresh()) { box.className = ""; box.innerHTML = errBox(e); } }
  };
  $("#sg-same").onchange = load; $("#sg-tol").onchange = load; load();
}

// Edit by hand (✎ Edit in the panel's head turns its facts into fields): colour, key and genre go into Traktor too
// (edit.edit); genre, hype and BPM stay yours over rescans. Only in the normal library.
const CAMELOTS = [...Array(12)].flatMap((_, i) => [`${i + 1}A`, `${i + 1}B`]);
const canEdit = t => !SNAP && t.type === "track" && S.lib === "native";
const editing = t => canEdit(t) && S.editing === t.path;
const editNow = t => ({ colour: t.traktor_color || "", key: t.mix_camelot || "", genre: t.my_genre || t.genre || "",
  hype: t.hype != null ? String(t.hype) : "", bpm: t.mix_bpm != null ? String(t.mix_bpm) : "" });
const reopen = async path => { const d = $("#drawer"), top = d.scrollTop; await openTrack(path); d.scrollTop = top; };
// a fact as shown, or (editing) its field; a value set by hand says so, and while editing ↺ takes it back to the analysis
function ed(t, f, shown, all = []) {
  const mine = (t.by_hand || {})[f] != null, mark = mine ? ` <span class="chip" data-tip="Set by hand: stays when the analysis runs again">by hand</span>` : "";
  if (!editing(t)) return shown + mark;
  const v = editNow(t)[f];
  const opts = (list, cur) => `${cur ? "" : `<option value="">–</option>`}${list.map(x => `<option${x === cur ? " selected" : ""}>${esc(x)}</option>`).join("")}`;
  const reset = mine ? ` <button type="button" class="mini" data-clear="${f}" data-tip="Back to the analysis">↺</button>` : "";
  if (f === "colour") return `<select data-ed="colour">${opts(COLOURS, v)}</select>`;
  if (f === "key") return `<select data-ed="key">${opts(CAMELOTS, v)}</select>`;
  if (f === "hype") return `<select data-ed="hype">${opts([...Array(10)].map((_, i) => String(i + 1)), v)}</select>${reset}`;
  if (f === "bpm") return `<input data-ed="bpm" type="number" min="40" max="300" step="1" value="${esc(v)}" style="width:80px">${reset}`;
  const genres = [...new Set(all.flatMap(x => [x.genre, x.genre_2, x.genre_3, x.my_genre]).filter(Boolean))].sort();
  return `<input data-ed="genre" list="ed-genres" value="${esc(v)}" style="width:min(220px,100%)"><datalist id="ed-genres">${genres.map(g => `<option value="${esc(g)}">`).join("")}</datalist>${reset}`;
}
// under the facts: Save / Cancel while editing, and after a save what was written and Undo
function editBar(t) {
  if (!canEdit(t)) return "";
  const msg = S.editMsg?.path === t.path ? S.editMsg : null;
  if (!editing(t)) return msg ? `<div class="filters small" id="ed-bar">${msg.undo ? `<button type="button" id="ed-undo">Undo</button>` : ""}<span class="muted" id="ed-msg">${esc(msg.text)}</span></div>` : "";
  return `<div class="filters" id="ed-bar"><button type="button" class="primary" id="ed-save" disabled>Save</button><button type="button" id="ed-cancel">Cancel</button><span class="small muted" id="ed-msg"></span></div>
    <div class="note">${DJ() === "Rekordbox" ? "Colour and genre are written into Rekordbox as well (the key stays in the analyzer; quit Rekordbox first;" : "Colour, key and genre are written into Traktor as well (quit Traktor first;"} a backup is made, and Undo puts it back).
      A colour you set is kept always and teaches the colour model; a genre you set is held. Genre, hype and BPM stay yours when the
      analysis runs again (↺ goes back to it). BPM changes only what the analyzer uses: ${DJ()}'s BPM goes with its beat grid.</div>`;
}
function bindEdit(t) {
  const box = $("#drawer-body");
  if (!canEdit(t) || !box.querySelector("#ed-bar")) return;
  const now = editNow(t), save = box.querySelector("#ed-save"), msg = box.querySelector("#ed-msg");
  const same = (f, x) => f === "bpm" ? x !== "" && now[f] !== "" && +x === +now[f] : x === now[f];
  const fields = () => [...box.querySelectorAll("[data-ed]")].map(e => [e.dataset.ed, e.value.trim()]).filter(([f, x]) => !same(f, x));
  const changes = () => Object.fromEntries(fields().filter(([, x]) => x));
  const clears = () => fields().filter(([f, x]) => !x && f === "genre" && (t.by_hand || {}).genre != null).map(([f]) => f);  // an emptied hand-set genre goes back to the analysis
  const badBpm = () => { const b = changes().bpm; return b != null && !(+b >= 40 && +b <= 300); };
  const check = () => { save.disabled = !(Object.keys(changes()).length || clears().length) || badBpm(); msg.textContent = badBpm() ? "BPM must be 40–300" : ""; };
  box.querySelectorAll("[data-ed]").forEach(e => e.addEventListener("input", check));
  const send = async (body, done) => {
    if (save) save.disabled = true;
    msg.textContent = "Saving…";
    try {
      const d = await postJSON("/api/track/edit", { path: t.path, ...body });
      const tk = typeof d.traktor === "string" ? d.traktor : d.traktor?.changed ? `${DJ()}: ${d.traktor.changed_fields.join(", ")} written${d.traktor.key ? ` (${d.traktor.key})` : ""}` : `${DJ()} already had it`;
      S.editMsg = { path: t.path, text: `${done} · ${tk}${d.traktor?.warning ? ` · ${d.traktor.warning}` : ""}${d.traktor?.hype_comment_skipped ? ` · ${d.traktor.hype_comment_skipped}` : ""}`,
        undo: body.undo ? null : d.undo };
      S.editing = null; S.tracks = {}; SETUP = null;
      await reopen(t.path);
      if (S.page === "tracks") { S.reloadTracks?.(); S.refreshRetrain?.(); }
      if (S.page === "colours") S.reloadColours?.().catch(() => {});
    } catch (e) { msg.textContent = e.message; if (save) check(); }
  };
  if (save) save.onclick = () => send({ changes: changes(), clear: clears() }, "Saved");
  box.querySelector("#ed-cancel")?.addEventListener("click", () => { S.editing = null; reopen(t.path); });
  box.querySelectorAll("[data-clear]").forEach(b => b.onclick = () => send({ changes: {}, clear: [b.dataset.clear] }, `${b.dataset.clear === "bpm" ? "BPM" : b.dataset.clear[0].toUpperCase() + b.dataset.clear.slice(1)} back to the analysis`));
  box.querySelector("#ed-undo")?.addEventListener("click", () => send({ ...S.editMsg.undo, undo: true }, "Undone"));
}

// The loop-idea buttons; each says what plays in it once Demucs has listened (parts: vocals, synth, drums + bass only)
function loopPads(c) {
  if (!(c.loop_ideas || []).length) return "";
  const parts = l => l.parts ? ` · drums ${Math.round(l.parts.drums * 100)}%, bass ${Math.round(l.parts.bass * 100)}%, synth ${Math.round(l.parts.synth * 100)}%, vocals ${Math.round(l.parts.vocals * 100)}% of its beats` : "";
  const wait = c.parts_pending && !SNAP;
  return `<div class="pads${wait ? " listening" : ""}" id="loop-pads"><span class="small muted">Loop ideas:</span>${c.loop_ideas.map((l, k) =>
    `<button class="lp" data-t="${l.ms / 1000}" data-loop="${l.len_ms / 1000}" data-tip="L${k + 1}: ${esc(l.kind)} · ${l.bars} bars from bar ${barLabel(c, l.bar).n} · ${esc(l.why)}${parts(l)}. Plays looped, gapless.">L${k + 1} · ${esc(l.kind.split(" · ")[0])}${l.parts?.label ? " · " + esc(l.parts.label.replaceAll(" · ", ", ")) : ""} · ${barLabel(c, l.bar).n}</button>`).join("")}${wait ? `<span class="small dim parts-wait" id="parts-wait" data-tip="Separating each loop into drums, bass, synths and vocals: about 8 seconds, once per track">Listening for vocals, synths and kicks…</span>` : ""}</div>`;
}

// What plays in each loop idea, separated on the server (~8 s the first time per track); updates the buttons in place
function loadParts(c, b, path) {
  if (!c.parts_pending || SNAP) return;
  api("cues/parts", { path }, { signal: prepFor(path) }).then(r => {
    if (!b.isConnected || $("#drawer")?.dataset.path !== path) return;
    c.loop_ideas = r.loop_ideas; c.parts_pending = false;
    const row = b.querySelector("#loop-pads"); if (row) row.outerHTML = loopPads(c);
  }).catch(e => { if (aborted(e)) return;  // left the track: it asks again when you come back
    const w = b.querySelector("#parts-wait"); if (w) { w.classList.add("failed"); w.textContent = `Couldn't listen for vocals: ${e.message}`; }
    b.querySelector("#loop-pads")?.classList.remove("listening"); });
}

// your cue pads; with cues ticked in the approve panel (pend), those too, dashed, and yours at the slots they'd move to
const pendSlot = (y, p) => {  // the slot one of your (or an approved) cue ends up in once the ticked ones are approved, when that changes
  if (!p?.slots || y.hotcue < 0 || !["yours", "moved", "new"].includes(y.status)) return undefined;
  const to = y.status === "new" ? p.slots.new.find(g => Math.abs(g.ms - y.ms) < 1)?.slot ?? -1 : (h => p.slots.up.get(h) ?? h)(y.was_hotcue ?? y.hotcue);  // -1: approved, but pushed out by the ticked ones
  return to !== y.hotcue ? to : undefined;
};
const yourPads = (c, pend) => [...myCues(c).map(y => { const to = pendSlot(y, pend); return to === -1 ? { ...y, hotcue: -1, nofit: true } : to != null ? { ...y, hotcue: to, was_hotcue: y.was_hotcue ?? y.hotcue, renum: true } : y; }),
    ...(pend?.adds || []).map(a => ({ ...a, status: "pending", bars: a.type === "loop" && a.len_ms ? a.len_ms / 1000 / c.bar_seconds : 0, hotcue: a.nofit || a.hotcue == null ? -1 : a.hotcue }))].sort((a, b) => (a.hotcue < 0) - (b.hotcue < 0) || a.hotcue - b.hotcue || a.ms - b.ms)
      .map(y => { const del = isDel(y, pend), was = origOf(y).hotcue, rn = was >= 0 && y.hotcue >= 0 && was !== y.hotcue;  // rn: pushed to another slot, the pad reads "2→3"
        const x = SNAP || y.status === "new" ? "" : y.status === "pending" ? `<button class="padx" type="button" data-unadd="${y.ms}" data-tip="Take it out: untick it (a cue you set by hand is dropped)">✕</button>`
        : `<button class="padx" type="button" data-del="${esc(delKey(y))}" data-tip="${del ? "Keep it: untick its deletion" : `Delete your ${esc(cueKind(y).toLowerCase())}${was >= 0 ? " " + (was + 1) : ""}${rn ? ` (shown as ${y.hotcue + 1} ${y.renum ? "once the ticked cues are approved" : "after the approved changes"})` : ""} from ${DJ()} (ticks it under Delete your cues; written when you approve and write)`}">${del ? "↺" : "✕"}</button>`;
        return `<span class="pad">` + `<button class="mine${del ? " del" : y.status === "new" ? " new" + (y.nofit ? " nofit" : "") : y.status === "pending" ? " pend" + (y.nofit ? " nofit" : "") : ""}" data-t="${y.ms / 1000}" ${y.type === "loop" && y.bars ? `data-loop="${y.bars * c.bar_seconds}"` : ""} data-tip="${del ? `Marked for deletion: ${y.status === "deleted" ? "approved, " : ""}not deleted in ${DJ()} yet · ` : ""}${y.status === "pending" ? (y.nofit ? `Ticked ${esc(y.name || y.type)}: no free slot, won't be written` : `Ticked ${esc(y.name || y.type)}, not approved yet`) : y.status === "new" ? (y.nofit ? `Approved ${esc(y.name || y.type)}, but with the ticked cues there's no slot left for it` : `Approved ${esc(y.name || y.type)}, not written yet`) : y.status === "moved" ? "Your cue, approved to move here, not written yet" : "Your " + cueKind(y).toLowerCase()}${y.hotcue >= 0 ? " · slot " + (y.hotcue + 1) : ""}${y.was_hotcue != null ? ` (was slot ${y.was_hotcue + 1}, renumbered${y.renum ? " once the ticked cues are approved" : ""})` : ""} · ${barLabel(c, Math.round((y.ms / 1000 - c.first_bar_s) / c.bar_seconds)).text}${y.type === "loop" ? " (plays looped)" : ""}">${y.nofit ? "✕" : y.hotcue >= 0 ? (rn ? `${was + 1}→` : "") + (y.hotcue + 1) : y.type === "loop" ? "L" : "•"}${y.status === "moved" ? "→" : ""}</button>${x}</span>`; }).join("") || `<span class="dim small">none</span>`;
function renderCues(c) {
  if (!c.bars) return `<div class="dim small">${esc(c.note || "No suggestions yet.")}</div>` + ((c.yours || []).length ? cueTable(c, []) : "");
  if (!c.wave) return `<div class="dim small">No waveform for this track yet: run <span class="mono">cues scan</span>.</div>` + cueTable(c, c.cues);
  const deck = SNAP ? "" : `<div class="deck"><button class="primary" id="dk-play" style="min-width:84px">▶ Play</button><span class="time" id="dk-time">0:00.0</span>
      <label class="vol small" data-tip="Playback volume (here only: not the track or ${DJ()})">Vol <input type="range" id="dk-vol" min="0" max="100" step="1" value="${Math.round(deckVolume() * 100)}"></label>
      ${deckViewBpm() ? `<label class="inline small" data-tip="Hear the track as the ${deckViewBpm()} BPM tempo view does: stretched with key lock by Rubber Band, the same way its scan heard it. A few seconds the first time for a track, started when you open it"><input type="checkbox" id="dk-stretch" ${deckBpm() ? "checked" : ""}> at ${deckViewBpm()} BPM</label><span class="small dim parts-wait" id="dk-stretching" hidden data-tip="Rubber Band stretches the track in pieces on every core: a few seconds, once per track (the last few are kept)">Stretching to ${deckViewBpm()} BPM…</span>` : ""}
      <label class="inline small"><input type="checkbox" id="dk-follow" checked> follow when zoomed</label>
      <button id="dk-cue" data-tip="Set a cue where the playhead is, on the nearest beat (C): it's ticked under New cues; Approve ticked keeps it, Write to ${DJ()} writes it">+ Cue</button>
      <button id="dk-rescan" data-tip="Measure this track's audio again and re-read its beat grid and cues from ${DJ()} (${DJ()} saves them when it quits)">Rescan track</button></div>
    <div class="pads"><span class="small muted">${c.approved_view ? "Yours, with approved:" : "Yours:"}</span><span id="your-pads" style="display:contents">${yourPads(c, null)}</span>
      <span class="small muted" style="margin-left:8px">Suggested:</span>${c.cues.filter(q => !sugHidden(c, q)).map(q => `<button class="sug" data-t="${q.ms / 1000}">${esc(q.name)} · ${barLabel(c, q.bar).n}</button>`).join("")}
      </div>${loopPads(c)}`;
  return `${deck}<div class="filters" style="margin:0 0 6px"><button data-zoom="in" data-tip="Zoom in, down to a single beat (or pinch / ctrl+scroll on the waveform, double-click)">+</button><button data-zoom="out" data-tip="Zoom out (shift+double-click)">−</button><button data-zoom="fit">Fit</button>
    <span class="small muted" id="wavezoom"></span><span class="small dim">drag or scroll sideways to move</span></div>
    <div class="wavewrap"><div class="wavehl" id="wavehl"></div><div class="wavebeat" id="wavebeat"></div><div class="playhead" id="ph"></div><div class="playhead ov" id="phov"></div><canvas id="wave" aria-label="Waveform with your cues (white, top) and suggested cues (blue, bottom)"></canvas></div>
    <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start">${SNAP ? "" : helpFold("keys", "Keys", `<div class="small dim">Space play/pause · 1–8 your cues · C cue here · ←/→ 1 bar · shift+←/→ 8 bars · click the waveform to jump</div>`)}
    ${helpFold("legend", "Legend", `<div class="legend" style="margin-top:0"><span><i style="background:var(--text)"></i>your cues (numbered, top; loops shaded over their length, fades and load named)</span><span><i style="background:#6fb4ff"></i>suggested (named, bottom)</span>
    <span><i style="background:#f08a24;opacity:.6"></i>loop ideas (L1 = most like yours)</span>${(c.corrections || []).some(f => !fixSettled(c, f)) ? `<span><i style="background:#ffd23f"></i>proposed correction</span>` : ""}<span><i style="background:#ffd23f"></i>your cue moved (ticked or approved; grey = where it was)</span><span>colour: <i style="background:#ff5a4f"></i>lows · <i style="background:#5fe36b"></i>mids · <i style="background:#5aa8ff"></i>highs · grid: beat, bar, every 4 bars, 8-bar phrase, 32 bars</span></div>`)}</div>
    ${SNAP ? cueTable(c, c.cues) : approveBox(c)}<div class="note">${c.starter ? "Suggestions come from the starter model, learned from another DJ's cues. Learn your own in Setup to get suggestions like yours." : `Suggestions are learned from the cues you set yourself (trained ${esc(c.trained_at || "")}).`}</div>`;
}
// your cues as they'll be in Traktor: with an approval, moved ones at their new place and new ones in their slot
// plus your fade-in, fade-out and load markers (never moved or added by an approval)
const myCues = c => (c.approved_view ? c.approved_view.cues.slice()  // the approval's view lists your markers too (deleted ones as "deleted")
  : [...(c.yours || []), ...(c.marks || [])].map(y => ({ ...y, status: "yours" }))).sort((a, b) => a.ms - b.ms);
// a cue of yours as it is in Traktor now (before any approved move or renumbering): what a deletion names
const origOf = y => ({ hotcue: y.was_hotcue ?? y.hotcue, type: y.type, ms: y.status === "moved" ? y.from_ms : y.ms });
const sameCue = (a, b) => a.hotcue === b.hotcue && a.type === b.type && Math.abs(a.ms - b.ms) < 1;
const delKey = y => { const o = origOf(y); return `r${o.hotcue}:${o.type}:${o.ms}`; };
// marked for deletion: ticked in the approve panel (pend), or without the panel, approved
const isDel = (y, pend) => y.status !== "new" && y.status !== "pending" && (pend ? pend.removes.some(r => sameCue(r, origOf(y))) : y.status === "deleted");
// your cues as the waveform draws them: the approve panel's ticked moves (box._moves, even before Approve) win
const moveKey = (hotcue, ms) => `${hotcue}:${Math.round(ms)}`;
function shownCues(c, box) {
  const mv = box._movesPath === c.path ? box._moves : null, pend = box._pending?.path === c.path ? box._pending : null;
  return myCues(c).map(y => ({ ...y, del: isDel(y, pend) })).map(y => {
    if (!mv || y.status === "new") return y;
    const orig = y.status === "moved" ? y.from_ms : y.ms, k = moveKey(y.was_hotcue ?? y.hotcue, orig);
    if (!mv.has(k)) return y;
    const to = mv.get(k);
    return to == null || Math.abs(to - orig) < 1 ? { ...y, ms: orig, status: "yours" } : { ...y, ms: to, from_ms: orig, status: "moved" };
  });
}
const pickedMove = (box, f) => box._movesPath === box._view?.path && box._moves?.get(moveKey(f.hotcue, f.from_ms)) != null;
const CUE_KIND = { cue: "Cue", loop: "Loop", "fade-in": "Fade in", "fade-out": "Fade out", load: "Load" };
const cueKind = y => y.type === "loop" && y.bars ? `Loop ${y.bars} bar${y.bars === 1 ? "" : "s"}` : CUE_KIND[y.type] || y.type;
const renumOn = c => RENUM.has(c.path) ? RENUM.get(c.path) : !!c.approved?.renumber;
// a cue of yours that's moved off its spot (approved, or ticked in the panel), which frees the spot for a new cue
const movedAway = (c, ms) => c._movedAway ? c._movedAway.some(m => Math.abs(m - ms) < 1) : (c.approved?.move || []).some(m => Math.abs(m.from_ms - ms) < 1);
const isApprovedAdd = (c, ms) => !!c.approved_view && c.approved_view.cues.some(y => y.status === "new" && Math.abs(y.ms - ms) < 1);
const isApprovedMove = (c, f) => !!c.approved_view && c.approved_view.cues.some(y => y.status === "moved" && (y.was_hotcue ?? y.hotcue) === f.hotcue && Math.abs(y.from_ms - f.from_ms) < 1);
const sameFix = (f, g) => f.hotcue === g.hotcue && Math.abs(f.from_ms - g.from_ms) < 1 && Math.abs(f.to_ms - g.to_ms) < 1;
// a deleted cue (approved, or ticked under Delete your cues) has nothing left to correct
const cueGone = (c, f) => (c.approved_view?.cues || []).some(y => y.status === "deleted" && (y.was_hotcue ?? y.hotcue) === f.hotcue && Math.abs(y.ms - f.from_ms) < 1)
  || (c._delTicked || []).some(r => r.hotcue === f.hotcue && Math.abs(r.ms - f.from_ms) < 1);
const fixSettled = (c, f) => f.ignored || isApprovedMove(c, f) || cueGone(c, f);  // approved, ignored or its cue deleted: no longer a pending correction
const sugHidden = (c, q) => isApprovedAdd(c, q.ms) || (q.moves != null && (c.corrections || []).some(f => f.hotcue === q.moves && Math.abs(f.to_ms - q.ms) < 1 && fixSettled(c, f)));
// a folded help text in the track panel; stays open or shut across the panel's redraws
// the track panel's folding sections: open or closed as you last left them, from track to track
const FOLD_DEFAULT = { mixes: true };
const foldOpen = key => { if (!S.folds) { try { S.folds = JSON.parse(localStorage.getItem("ta-folds") || "{}"); } catch (e) { S.folds = {}; } } return S.folds[key] ?? FOLD_DEFAULT[key] ?? false; };
const hint = text => text ? `<span class="hint">${esc(text)}</span>` : "";
const fold = (key, title, html, summaryExtra = "") => `<details class="fold" data-fold="${key}" ${foldOpen(key) ? "open" : ""}><summary>${title}${summaryExtra}</summary>${html}</details>`;
document.addEventListener("toggle", e => { const k = e.target.dataset?.fold; if (!k) return; foldOpen(k); S.folds[k] = e.target.open; try { localStorage.setItem("ta-folds", JSON.stringify(S.folds)); } catch (err) {} }, true);
const helpFold = (key, title, html) => `<details class="help" data-help="${key}" ${S.helpOpen?.[key] ? "open" : ""}><summary>${title}</summary>${html}</details>`;
document.addEventListener("toggle", e => { const k = e.target.dataset?.help; if (k) (S.helpOpen ??= {})[k] = e.target.open; }, true);  // toggle doesn't bubble
const clock = t => { const d = Math.round(t * 10); return `${Math.floor(d / 600)}:${((d % 600) / 10).toFixed(1).padStart(4, "0")}`; };  // 59.96 s is 1:00.0, not 0:60.0
// the track list's approved chip: "+3 approved", "+2 · 1 moved · −1 approved"; long: in words, for its tip
function approvedText(n, long = false) {
  if (n === true) n = { add: 0, move: 0, remove: 0 };  // an older server: no counts
  const pl = (k, w) => `${k} ${w}${k > 1 ? "s" : ""}`;
  if (long) return [n.add && pl(n.add, "new cue"), n.move && pl(n.move, "move"), n.remove && pl(n.remove, "deletion"), n.renumber && "renumbered"].filter(Boolean).join(", ") || "nothing";
  return ([n.add && `+${n.add}`, n.move && `${n.move} moved`, n.remove && `−${n.remove}`, n.renumber && "renumbered"].filter(Boolean).join(" · ") || "") + " approved";
}
function barLabel(c, bar) {  // bar: index into the track's bars; bar 1 = Traktor's grid marker
  const n = bar - c.grid_bar + 1, ph = Math.floor((n - 1) / 8) + 1, inph = ((n - 1) % 8 + 8) % 8 + 1;
  return { n, text: `bar ${n}`, phrase: n < 1 ? "before the grid marker" : inph === 1 ? `first bar of phrase ${ph} (8-bar grid)` : `bar ${inph} of phrase ${ph}` };
}
function approveItems(c) {
  const items = [];
  const free = c.free_slots > 0;
  (c.cues || []).forEach(q => {  // none when the track isn't measured yet (the panel shows c.note)
    if (q.moves != null && !free) return;  // offered below as a correction of your cue; with a free slot, also as a new cue
    const mine = q.matches != null && (c.yours || []).find(y => y.hotcue === q.matches && y.type === "cue"),
      note = q.matches != null && !(mine && movedAway(c, mine.ms)) ? `✓ matches your cue ${q.matches + 1}` : q.at_your_loop ? "your loop is here" : "";
    const also = q.moves != null ? ` <span class="dim small">· or move your cue ${q.moves + 1} here (correction)</span>` : "";
    items.push({ key: "a" + q.ms, kind: "add", also: q.moves != null, label: `${q.time} · bar ${barLabel(c, q.bar).n} · <b>${esc(q.name)}</b>${also}`, disabled: note, data: { ms: q.ms, name: q.name, type: "cue", len_ms: 0 } });
  });
  if (free) (c.more_cues || []).forEach(q => items.push({ key: "a" + q.ms, kind: "add", extra: true,  // the next-best bars, for a free slot
    label: `${q.time} · bar ${barLabel(c, q.bar).n} · <b>${esc(q.name)}</b> <span class="dim small">· scores ${q.score}</span>`, disabled: "", data: { ms: q.ms, name: q.name, type: "cue", len_ms: 0 } }));
  (c.loop_ideas || []).forEach((l, k) => {
    const barMs = l.len_ms / l.bars, mine = (c.approved?.add || []).find(a => a.type === "loop" && Math.abs(a.ms - l.ms) < 1);
    const picked = l.suggested_bars ?? l.bars;  // the length you'd likely give it (cues train learns it), else your most common
    const bars = loopLen(c.path).get("l" + l.ms) ?? (mine ? Math.round(mine.len_ms / barMs * 4) / 4 : picked);  // chosen here, else as approved, else suggested
    items.push({ key: "l" + l.ms, kind: "add", lenOf: { picked, learned: l.suggested_bars != null, bars, room: c.bars - l.bar },  // room: bars left before the track ends
      label: `${l.time} · bar ${barLabel(c, l.bar).n} · <b>Loop ${bars} bar${bars === 1 ? "" : "s"}</b> <span class="dim small">L${k + 1} · ${esc(l.kind)} · ${esc(l.why)}${k === 0 ? " · slot 8 when free" : ""}</span>`,
      sample: { bar: barLabel(c, l.bar).n, kind: `L${k + 1} ${l.kind.split(" · ")[0]}` },
      asCue: { ms: l.ms, name: (k => k.charAt(0).toUpperCase() + k.slice(1))(l.kind.split(" · ")[0] || "Loop").slice(0, 40), type: "cue", len_ms: 0 },  // "as cue point": a hot cue at its start
      data: { ms: l.ms, name: `Loop ${bars}`, type: "loop", len_ms: +(barMs * bars).toFixed(3) } });
  });
  (c.alternatives || []).forEach(y => {  // each of your cues: move it to a bar you choose (the model's pick first)
    if (cueGone(c, y)) return;  // deleted: nothing to move
    const same = (a, b) => a.hotcue === b.hotcue && Math.abs(a.from_ms - b.from_ms) < 1;
    const fx = (c.corrections || []).filter(f => same(f, y)), pend = fx.filter(f => !f.ignored);
    const mv = (c.approved?.move || []).find(m => same(m, y));
    const opts = y.options.slice(), barOf = ms => Math.round((ms / 1000 - c.first_bar_s) / c.bar_seconds) - c.grid_bar + 1;
    const addOpt = ms => { if (!opts.some(o => Math.abs(o.to_ms - ms) < 1)) opts.push({ to_ms: ms, to_bar: barOf(ms), to_time: clock(ms / 1000), score: null }); };
    fx.forEach(f => addOpt(f.to_ms)); if (mv) addOpt(mv.to_ms);
    const dragged = DRAGMV.get(c.path)?.get(`m${y.hotcue}:${y.from_ms}`); if (dragged != null) addOpt(dragged);  // where you dropped it on the waveform
    opts.sort((a, b) => a.to_ms - b.to_ms);
    const proposed = o => fx.some(f => Math.abs(f.to_ms - o.to_ms) < 1);
    const best = opts.reduce((a, o) => (o.score ?? -1) > (a.score ?? -1) ? o : a, opts[0]);
    const chosen = mv ? mv.to_ms : (pend[0] || fx[0] || best).to_ms, key = `m${y.hotcue}:${y.from_ms}`;
    const optLabel = o => { const d = o.to_bar - y.from_bar;
      return `${d ? `bar ${o.to_bar} (${d > 0 ? "+" : ""}${d})` : `bar ${o.to_bar}, onto the downbeat`} · ${o.to_time}${o.score != null ? ` · scores ${o.score}` : ""}${o.name ? ` · suggested: ${o.name}` : ""}${o.phrase_grid ? " · on the 8-bar grid" : ""}${proposed(o) ? " · ★ proposed" : ""}`; };
    const select = `<select class="target" data-target="${esc(key)}">${opts.map(o => `<option value="${o.to_ms}" ${Math.abs(o.to_ms - chosen) < 1 ? "selected" : ""}>${esc(optLabel(o))}</option>`).join("")}</select>`;
    const label = `<span class="move">Cue ${y.hotcue + 1}${y.name ? ` <span class="small dim">${esc(y.name)}</span>` : ""}: bar ${y.from_bar} →</span> ${select}
      <div class="why">${pend.length ? `Scores ${y.score_here} at your spot; ★ = what the model proposes.` : `Scores ${y.score_here} at your spot.`} Pick any bar the model found (a higher score is more like where you put cues) and listen before ticking: some off-grid cues are on purpose.</div>`;
    items.push({ key, kind: "move", cue: y, opts, fixes: fx, ignored: !pend.length && fx.length > 0, group: pend.length ? "fix" : fx.length ? "ignored" : "other",
      label, tip: `The model never saw your cues on this track. About half of your off-grid cues are on purpose, so only tick a move if it was a slip.`,
      data: { from_ms: y.from_ms, to_ms: chosen, hotcue: y.hotcue, name: y.name } });
  });
  [...(c.yours || []), ...(c.marks || [])].sort((a, b) => (a.hotcue < 0) - (b.hotcue < 0) || a.hotcue - b.hotcue || a.ms - b.ms).forEach(y => {  // delete any of yours
    const to = `<span data-renum="${y.hotcue}"></span>`;  // " → 3" once the ticked or approved cues push it to another slot (showSlots fills it)
    const what = y.hotcue >= 0 ? (y.type === "cue" ? `Cue ${y.hotcue + 1}${to}` : `${esc(cueKind(y))} (slot ${y.hotcue + 1}${to})`) : esc(cueKind(y));
    items.push({ key: delKey({ ...y, status: "yours" }), kind: "remove",
      label: `<b>${what}</b>${y.name && !/^(Cue|n\.n\.)\s*\d*$/.test(y.name) ? ` <span class="small dim">${esc(y.name)}</span>` : ""} · ${clock(y.ms / 1000)} · ${barLabel(c, Math.round((y.ms / 1000 - c.first_bar_s) / c.bar_seconds)).text}`,
      data: { ms: y.ms, hotcue: y.hotcue, type: y.type, name: y.name || "", len_ms: y.type === "loop" && y.bars ? y.bars * c.bar_seconds * 1000 : 0 } });
  });
  // cues you set by hand (+ Cue), and approved cues the lists above no longer offer
  const known = ms => items.some(i => i.kind === "add" && (Math.abs(i.data.ms - ms) < 1 || (i.asCue && Math.abs(i.asCue.ms - ms) < 1)));
  const hand = [...(c.approved?.add || []).filter(a => a.type === "cue"), ...(HAND.get(c.path) || [])];
  hand.filter((h, k) => !known(h.ms) && !(c.yours || []).some(y => Math.abs(y.ms - h.ms) < 1 && !movedAway(c, y.ms)) && hand.findIndex(g => Math.abs(g.ms - h.ms) < 1) === k).sort((a, b) => a.ms - b.ms).forEach(h => {
    const k = Math.round((h.ms - c.grid_ms) / (60000 / c.bpm)), n = Math.floor(k / 4) + 1, beat = ((k % 4) + 4) % 4 + 1;
    items.push({ key: "h" + h.ms, kind: "add", hand: true, disabled: "",
      label: `${clock(h.ms / 1000)} · bar ${n}${beat > 1 ? ` beat ${beat}` : ""} · <b>Cue set by hand</b>`,
      data: { ms: h.ms, name: h.name || "Cue", type: "cue", len_ms: 0, hand: true } });
  });
  return items;
}
function approveBox(c) {
  const items = approveItems(c), ap = c.approved;
  const isOn = it => !!ap && (it.kind === "add" ? ap.add.some(a => Math.abs(a.ms - it.data.ms) < 1) : it.kind === "remove" ? (ap.remove || []).some(r => sameCue(r, it.data))
    : ap.move.some(m => m.hotcue === it.data.hotcue && Math.abs(m.from_ms - it.data.from_ms) < 1));
  if (!items.length) return cueTable(c, c.cues);
  const approvedOk = it => isOn(it) && !(ap && ap.grid_changed) && !(c.approved_view?.skipped_ms || []).some(m => Math.abs(m - (it.kind === "move" ? it.data.from_ms : it.data.ms)) < 1);
  const approved = items.filter(approvedOk), pending = items.filter(it => !approvedOk(it));
  const fixes = pending.filter(i => i.group === "fix"), ignoredFixes = pending.filter(i => i.group === "ignored"), others = pending.filter(i => i.group === "other"),
    cues_ = pending.filter(i => i.kind === "add" && i.data.type === "cue" && !i.extra), more = pending.filter(i => i.extra),
    loops = pending.filter(i => i.kind === "add" && i.data.type === "loop"), newCues = cues_.filter(i => !i.disabled).length,
    dels = pending.filter(i => i.kind === "remove");
  const held = new Map([...(c.yours || []), ...(c.marks || [])].filter(y => y.hotcue >= 0).map(y => [y.hotcue, y]));  // slot → your cue
  const row = it => {
    const dim = it.disabled && !it.disabled.startsWith("✓") ? "opacity:.5;" : "";
    const tipAttr = it.tip ? `data-tip="${esc(it.tip)}"` : it.disabled ? `data-tip="${esc(it.disabled)}"` : "";
    const play = it.kind === "remove" ? `<button class="mini" data-t="${it.data.ms / 1000}" ${it.data.len_ms ? `data-loop="${it.data.len_ms / 1000}"` : ""} data-tip="Play from it">▶</button>`
      : it.kind === "move"
      ? `<div style="margin-top:3px"><button class="mini" data-t="${it.data.from_ms / 1000}" data-tip="Play from your cue">▶ yours</button><button class="mini" data-t="${it.data.to_ms / 1000}" data-there="${esc(it.key)}" data-tip="Play from the bar picked in the list">▶ there</button></div>`
      : `<button class="mini" data-t="${it.data.ms / 1000}" ${it.data.type === "loop" ? `data-loop="${it.data.len_ms / 1000}"` : ""} data-tip="Play from here">▶</button>`;
    const had = it.sample && (c.saved_samples || []).find(x => Math.abs(x.start - it.data.ms / 1000) < 0.002 && Math.abs(x.len - it.data.len_ms / 1000) < 0.002 && !!x.smooth === smoothLoops());
    const save = !it.sample || SNAP ? "" : had ? `<span class="small" style="color:var(--good)" data-saved="${esc(it.key)}"> ✓ saved in ${esc(had.folder)} ${showBtn(had.file, c.file_manager)}</span>`
      : `<button class="mini" type="button" data-save="${esc(it.key)}" data-tip="Save this loop as a WAV sample (exact samples, gapless) in the folder chosen above">Save sample</button><span class="small" style="color:var(--good)" data-saved="${esc(it.key)}"></span>`;
    const ign = it.kind === "move" && it.fixes.length && !SNAP && !isOn(it) ? `<button class="mini" type="button" data-ignore="${esc(it.key)}" data-tip="${it.ignored ? "Show this correction again" : "Your cue is right: hide this correction (it comes back only if the cue moves). You can still pick another bar for it"}">${it.ignored ? "Restore" : "Ignore"}</button>` : "";
    const mine = it.kind === "add" && it.data.type === "cue" && !SNAP ? items.filter(m => m.kind === "move" && m.opts.some(o => Math.abs(o.to_ms - it.data.ms) < 1)) : [];
    const useAs = mine.length ? ` <button type="button" class="useas" data-useas="${it.data.ms}" data-add="${esc(it.key)}" data-mine="${esc(mine.map(m => m.key).join(" "))}" data-tip="Move one of your cues here instead of adding a new one (point at one in the list to see it on the waveform)">move my cue here… ▾</button>` : "";
    const mySlot = it.kind === "add" && ap ? ap.add.find(a => Math.abs(a.ms - it.data.ms) < 1)?.slot : undefined;
    const slot = it.kind === "add" && !it.disabled && !SNAP && c.free_slots > 0 ? ` <select class="slot" data-slot="${esc(it.key)}" data-tip="Which hot-cue slot it goes in. Auto: a free one, keeping your cues in time order when they are. A slot your cue holds: that cue and the ones after it move up one">
      <option value="">slot: auto</option>${[0, 1, 2, 3, 4, 5, 6, 7].map(n => { const y = held.get(n);
        return `<option value="${n}" ${mySlot === n ? "selected" : ""} ${y && y.type !== "cue" ? "disabled" : ""}>slot ${n + 1}${!y ? " · free" : y.type === "cue" ? ` · yours move up` : ` · your ${cueKind(y).toLowerCase()}`}</option>`; }).join("")}</select>` : "";
    const asCue = it.asCue && ap?.add.some(a => Math.abs(a.ms - it.data.ms) < 1 && a.type === "cue");
    const as = it.asCue && !SNAP ? `<select class="as" data-as="${esc(it.key)}" data-tip="Write it as a loop, or as a plain hot cue at the loop's start (it then goes in the cue slots 1–7 with your other cues, in time order)">
      <option value="loop" ${asCue ? "" : "selected"}>as loop</option><option value="cue" ${asCue ? "selected" : ""}>as cue point</option></select>` : "";
    const len = it.lenOf && !SNAP ? `<select class="len" data-len="${esc(it.key)}" data-tip="How long the loop is. ${it.lenOf.learned ? `Suggested: ${it.lenOf.picked} bar${it.lenOf.picked === 1 ? "" : "s"}, the length you'd likely give a loop here (learned from your loops by cues train)` : `Suggested: ${it.lenOf.picked} bars, your most common loop length`}. Press ▶ to hear another length before approving">
      ${[...new Set([...LOOP_BARS.filter(n => n <= it.lenOf.room), it.lenOf.picked, it.lenOf.bars])].sort((a, b) => a - b).map(n => `<option value="${n}" ${n === it.lenOf.bars ? "selected" : ""}>${n} bar${n === 1 ? "" : "s"}${n === it.lenOf.picked ? " (suggested)" : ""}</option>`).join("")}</select>` : "";
    const nm = it.hand && !SNAP ? `<input class="nm" data-nm="${esc(it.key)}" value="${esc(it.data.name)}" maxlength="40" placeholder="name" style="width:130px;font-size:12px;padding:1px 4px" data-tip="Its name in ${DJ()} (saved when you press Approve ticked)">${isOn(it) ? "" : ` <button class="mini" type="button" data-unhand="${it.data.ms}" data-tip="Remove this hand-set cue">✕</button>`}` : "";
    const note = it.disabled ? ` <span class="small" style="color:${it.disabled.startsWith("✓") ? "var(--good)" : "var(--text-3)"}">· ${esc(it.disabled)}</span>` : "";
    const from = it.kind === "move" ? it.data.from_ms : it.data.ms;  // a deletion: its place (data.ms)
    const left = isOn(it) && (c.approved_view?.skipped_ms || []).some(m => Math.abs(m - from) < 1);  // approved but won't fit
    const done = isOn(it) && !(ap && ap.grid_changed) && !left;
    const chip = done ? `<span class="chip good" style="margin-right:6px">✓ approved</span>` : left ? `<span class="chip warn" style="margin-right:6px" data-tip="Approved, but it won't be written: see the note above">won't fit</span>` : "";
    const body = it.kind === "remove" ? `${play}${chip}<span style="color:var(--bad)">Delete</span> ${it.label}`
      : it.kind === "move" ? `${chip}${it.label}${play}${ign}${note}`  // a new cue or loop: ▶ and its name, then the rest of its controls on a line of their own
      : `<div>${play}${chip}${it.label}${note}</div><div class="ctl">${nm}${as}${len}${slot}${useAs}${save}</div>`;
    return `<label class="${it.kind === "move" ? "fix" : it.kind === "remove" ? "del" : "add"}${done ? " approved" : ""}" style="${dim}" ${tipAttr}>
      <input type="checkbox" data-key="${esc(it.key)}" ${it.extra || it.also ? "data-more" : ""} ${isOn(it) ? "checked" : ""} ${it.disabled ? "disabled" : ""}> <span>${body}</span></label>`;
  };
  const summary = [fixes.length && `<b style="color:var(--warn)">${fixes.length} correction${fixes.length > 1 ? "s" : ""} to your cues</b>`,
    `${newCues} new cue${newCues === 1 ? "" : "s"}`, loops.length && `${loops.length} loop idea${loops.length > 1 ? "s" : ""}`].filter(Boolean).join(" · ");
  return `<div class="section" style="margin-top:12px"><h3>Approve for ${DJ()}</h3>
    ${ap ? `<div class="note" style="margin:0 0 6px"><b style="color:var(--good)">✓ ${ap.add.length + ap.move.length + (ap.remove || []).length + (ap.renumber ? 1 : 0)} approved</b> ${esc(ap.at)}${ap.grid_changed ? ` · <b>the beat grid changed since: approve again</b>` : ` · shown above as your cues (green = new, → = moved${(ap.remove || []).length ? ", red struck through = deleted" : ""})`}. Not in ${DJ()} yet: press <b>Write to ${DJ()}…</b> below (${DJ()} closed).${(c.approved_view?.skipped || []).length ? `<br>Won't be written: ${c.approved_view.skipped.map(esc).join("; ")}` : ""}</div>` : ""}
    <div class="small muted">${summary}</div>
    <div id="approve">
      ${approved.length ? `<div class="apgroup"><h4 style="color:var(--good)">✓ Approved · not in ${DJ()} yet <span class="small muted" style="font-weight:400">· untick and press Approve ticked to take one back</span></h4>${approved.map(row).join("")}</div>` : ""}
      ${fixes.length ? `<div class="apgroup"><h4>Corrections to your cues <button class="mini" id="ap-fixall" type="button">Tick all corrections</button></h4><div class="fixbox" id="fixes">${fixes.map(row).join("")}</div></div>` : ""}
      ${ignoredFixes.length ? `<details class="apgroup"><summary class="small muted">${ignoredFixes.length} ignored correction${ignoredFixes.length > 1 ? "s" : ""} · show</summary><div class="fixbox">${ignoredFixes.map(row).join("")}</div></details>` : ""}
      ${others.length ? `<div class="apgroup" id="byhand" hidden><h4>Your cues moved by hand</h4><div class="fixbox">${others.map(it => `<div data-hand="${esc(it.key)}" hidden>${row(it)}</div>`).join("")}</div></div>` : ""}
      ${cues_.length || more.length ? `<div class="apgroup"><h4>New cues <span class="small muted" style="font-weight:400">· ${c.free_slots > 0 ? `${c.free_slots} free hot-cue slot${c.free_slots > 1 ? "s" : ""}` : `<b style="color:var(--warn)">all 8 hot-cue slots are used</b>: free one in ${DJ()} first, or use a correction to move a cue`}</span></h4>${cues_.map(row).join("")}${more.length ? `<details class="apgroup"${newCues ? "" : " open"}><summary class="small muted">${more.length} more place${more.length > 1 ? "s" : ""} for a new cue (lower scores than the picks above)</summary>${more.map(row).join("")}</details>` : ""}</div>` : ""}
      ${loops.length ? `<div class="apgroup"><h4>Loops ${SNAP ? "" : `<span class="small muted" style="font-weight:400">· save as WAV sample to <select id="smp-folder" style="font-size:12px;padding:1px 4px"></select> <button class="mini" type="button" id="smp-open" data-tip="Open this folder">Open folder</button></span>`}
        <label class="inline small" style="font-weight:400;margin-left:8px" data-tip="Fade the loop's last 5 ms into the audio just before its start, so a sound ringing over the end isn't cut off with a click. Changes the loop pads and saved samples; off = the track's exact samples"><input type="checkbox" id="smooth-loops" ${smoothLoops() ? "checked" : ""}> smooth loop point</label></h4>${loops.map(row).join("")}</div>` : ""}
      ${dels.length ? `<details class="apgroup" id="delbox"><summary class="small muted">Delete your cues · ${dels.length} · or press ✕ next to a pad above</summary><div class="small dim" style="margin:2px 0 4px">Your cues after it in slots 1–7 slide back to close the gap; slot 8 keeps its cue.</div>${dels.map(row).join("")}</details>` : ""}
    </div>
    <div class="filters" style="margin-top:8px"><button class="primary" id="ap-save">Approve ticked</button><button id="ap-all">Tick all new cues</button>${SNAP ? "" : `<button id="ap-renum" class="${renumOn(c) ? "primary" : ""}" aria-pressed="${renumOn(c)}" data-tip="Number your cue points 1-7 in the order they play, filling the slots no loop holds. Shows on the pads at once; Approve ticked keeps it, Write to ${DJ()} writes it">Number cues in order</button>`}
      ${ap ? `<button id="ap-clear">Clear approval</button>` : ""}${SNAP ? "" : `<button id="ap-write" data-tip="Write every approved track's cues into ${DJ()} (shows what changes first)">Write to ${DJ()}…</button>`}<span class="small muted" id="ap-msg"></span></div>
    ${helpFold("approve", "How approving and writing works", `<div class="note" style="margin-top:0">Approving writes nothing yet. <b>Write to ${DJ()}…</b> writes all approved tracks at once, with ${DJ()} closed: it shows what would change first, makes a backup and can be undone
      (same as <span class="mono">cues apply</span> in a terminal). New cues only go into free hot-cue slots; your own cues are only moved when you ticked a correction. When your hot cues are numbered in time order, they stay so: a new cue between 3 and 4 becomes 4 and your later ones move up a slot (hover a pad above to see its old slot).</div>`)}</div>
    ${cueTable(c, c.cues)}`;
}
async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await readJSON(r);
  // When the server can't see whether Traktor is open (a container): ask, then send the write again with the answer.
  if (r.status === 409 && d.confirm === "traktor_closed" && !body.traktor_closed &&
      confirm(`${d.why || `The analyzer can't see whether ${DJ()} is open.`}\n\nIs ${DJ()} closed?`)) {
    return postJSON(url, { ...body, traktor_closed: true });
  }
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
}
// Write every approval into Traktor's collection.nml (= `cues apply`): a preview first, then the write, then Undo.
async function writeToTraktor(onDone) {
  const dlg = document.createElement("dialog"); dlg.className = "write"; document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  const list = (items, max = 400) => items.length ? `<ul>${items.slice(0, max).map(x => `<li>${esc(x)}</li>`).join("")}${items.length > max ? `<li class="muted">… ${items.length - max} more</li>` : ""}</ul>` : "";
  const show = (html, buttons) => {
    dlg.innerHTML = `<h3>Write approved cues to ${DJ()}</h3>${html}<div class="filters" style="margin-top:12px">${buttons}<button data-close>Close</button><span class="small muted" id="wr-msg"></span></div>`;
    dlg.querySelector("[data-close]").onclick = () => dlg.close();
  };
  const fail = e => { const m = dlg.querySelector("#wr-msg"); if (m) m.textContent = e.message; else show(errBox(e), ""); };
  async function preview() {
    show(`<div class="muted">Checking what would change…</div>`, "");
    let d; try { d = await postJSON("/api/cues/write", { dry_run: true }); } catch (e) { return show(`<div class="err">${esc(e.message)}</div>`, ""); }
    const what = [d.cues_added && `<b>${d.cues_added}</b> new cue${d.cues_added > 1 ? "s" : ""}`, d.cues_moved && `<b>${d.cues_moved}</b> correction${d.cues_moved > 1 ? "s" : ""}`, d.cues_renumbered && `<b>${d.cues_renumbered}</b> cue${d.cues_renumbered > 1 ? "s" : ""} renumbered to stay in time order`,
      d.cues_deleted && `<b>${d.cues_deleted}</b> deleted`].filter(Boolean).join(" and ");
    const running = d.traktor_running ? `<div class="err" style="margin:8px 0">${DJ()} is open. Quit ${DJ()} first: it saves its collection when it closes and would undo these changes. Then press Check again.</div>` : "";
    show(`${d.changed ? `<div>${what} on <b>${d.tracks}</b> track${d.tracks > 1 ? "s" : ""}:</div>${list(d.changes || [])}` : `<div>${d.nothing_approved ? "Nothing approved yet: tick cues in a track's panel and press Approve ticked." : `Nothing to write: everything approved is already in ${DJ()} or can't be placed.`}</div>`}
      ${(d.skipped || []).length ? `<div class="small" style="margin-top:6px"><b>Won't be written</b> (approve these again after freeing a hot-cue slot in ${DJ()}):</div>${list(d.skipped)}` : ""}
      ${running}
      <div class="note">A backup of collection.nml is made first; Undo here (or <span class="mono">traktor --undo</span>) puts it back. Your own cues are only moved where you approved a correction, and only deleted where you approved that.</div>`,
      d.changed ? (d.traktor_running ? `<button class="primary" data-check>Check again</button>` : `<button class="primary" data-write>Write to ${DJ()}</button>`) : "");
    dlg.querySelector("[data-check]")?.addEventListener("click", preview);
    dlg.querySelector("[data-write]")?.addEventListener("click", async e => {
      const btn = e.currentTarget;  // currentTarget is null once the handler awaits
      btn.disabled = true; btn.textContent = "Writing…";
      let r; try { r = await postJSON("/api/cues/write", { dry_run: false }); } catch (err) { btn.disabled = false; btn.textContent = `Write to ${DJ()}`; return fail(err); }
      S.tracks = {};
      show(`<div style="color:var(--good)"><b>✓ Written:</b> ${r.cues_added} new cue${r.cues_added === 1 ? "" : "s"}, ${r.cues_moved} correction${r.cues_moved === 1 ? "" : "s"}${r.cues_renumbered ? `, ${r.cues_renumbered} renumbered` : ""}${r.cues_deleted ? `, ${r.cues_deleted} deleted` : ""} on ${r.tracks} track${r.tracks === 1 ? "" : "s"}.</div>
        <div class="note">Open ${DJ()}: the cues are there. Backup: <span class="mono">${esc(r.backup || "")}</span><br>${DJ() === "Rekordbox" ? "Undo works until Rekordbox changes master.db again." : "Undo works until Traktor saves the collection again (it does when it quits)."}</div>`,
        `<button data-undo>Undo</button>`);
      dlg.querySelector("[data-undo]").onclick = async ev => {
        const btn = ev.currentTarget; btn.disabled = true;
        try { const u = await postJSON("/api/cues/undo", {}); S.tracks = {};
          show(`<div><b>Undone.</b> ${DJ() === "Rekordbox" ? "master.db is" : "collection.nml is"} back to how it was${u.approvals_restored ? `, and your ${u.approvals_restored} approval${u.approvals_restored > 1 ? "s are" : " is"} back` : ""}.</div>`, "");
          onDone?.(); }
        catch (err) { btn.disabled = false; fail(err); }
      };
      onDone?.();
    });
  }
  dlg.showModal();
  preview();
}
function smoothLoops() {
  if (S.smoothLoops === undefined) { try { S.smoothLoops = localStorage.getItem("ta-smooth-loop") === "1"; } catch (e) { S.smoothLoops = false; } }
  return S.smoothLoops;
}
const showBtn = (file, fm) => `<button class="mini" type="button" data-reveal="${esc(file)}" data-tip="${esc(file)}">Show in ${esc(fm || "file manager")}</button>`;
function bindApprove(c, box, t) {
  const items = approveItems(c);
  const send = postJSON;
  const refresh = async (d, msgText) => {  // draw the panel again and update the track list's counts
    for (const list of Object.values(S.tracks)) for (const x of list) if (x.path === t.path) {
      if ("corrections" in d) { if (d.corrections) x.corrections = d.corrections; else delete x.corrections; }
    }
    const fresh = await api("cues", { path: t.path });
    for (const list of Object.values(S.tracks)) for (const x of list) if (x.path === t.path) {  // the list's approved chip
      const ap = fresh.approved; if (ap) x.cues_approved = { add: ap.add.length, move: ap.move.length, remove: (ap.remove || []).length, renumber: ap.renumber ? 1 : 0 }; else delete x.cues_approved;
    }
    if (!box.isConnected || $("#drawer").dataset.path !== t.path) { if (S.page === "tracks") S.redrawTracks?.(); return; }  // approved, then moved on: the open track's panel stays
    box.innerHTML = renderCues(fresh); drawWave(fresh, box); bindApprove(fresh, box, t); deckAttach(fresh, box);
    const msg = box.querySelector("#ap-msg"); if (msg && msgText) msg.textContent = msgText;
    if (S.page === "tracks") S.redrawTracks?.();
  };
  const save = async plan => { const d = await send("/api/cues/approve", { path: t.path, plan }); RENUM.delete(t.path);  // the button follows what's saved now (not after an ignore or a rescan: those keep it as pressed)
    await refresh(d, d.approved ? `${d.approved} approved` : "Approval cleared"); };
  box.querySelectorAll("[data-ignore]").forEach(b => b.addEventListener("click", async e => {
    e.preventDefault(); e.stopPropagation();
    const it = items.find(i => i.key === b.dataset.ignore);
    try { let d; for (const fix of it.fixes) d = await send("/api/cues/ignore", { path: t.path, fix, on: !it.ignored }); await refresh(d, it.ignored ? "Correction restored" : "Correction ignored"); }
    catch (err) { b.textContent = err.message; }
  }));
  box.querySelector("#dk-rescan")?.addEventListener("click", async e => {
    const b = e.currentTarget; b.disabled = true; b.textContent = "Rescanning…";
    try { const d = await send("/api/cues/rescan", { path: t.path }); box._view = null; WAVE_DETAIL.delete(t.path); await refresh(d, "Track rescanned"); }
    catch (err) { b.disabled = false; b.textContent = err.message; }
  });
  const go = async plan => { try { await save(plan); } catch (e) { const m = box.querySelector("#ap-msg"); if (m) m.textContent = e.message; } };
  const asData = i => i.hand ? { ...i.data, name: (box.querySelector(`input[data-nm="${CSS.escape(i.key)}"]`)?.value || "").trim() || "Cue" }  // a hand-set cue: its name as typed
    : i.asCue && box.querySelector(`select[data-as="${CSS.escape(i.key)}"]`)?.value === "cue" ? i.asCue : i.data;  // loop idea: as loop or as cue point
  const ticked = () => { const on = new Set([...box.querySelectorAll("#approve input:checked")].map(i => i.dataset.key)); const sel = items.filter(it => on.has(it.key));
    const target = i => { const s = box.querySelector(`select[data-target="${CSS.escape(i.key)}"]`); return s ? { ...i.data, to_ms: +s.value } : i.data; };
    const slot = i => { const v = box.querySelector(`select[data-slot="${CSS.escape(i.key)}"]`)?.value, d = asData(i); return v ? { ...d, slot: +v } : d; };
    return { add: sel.filter(i => i.kind === "add").map(slot), move: sel.filter(i => i.kind === "move").map(target),
      remove: sel.filter(i => i.kind === "remove").map(i => i.data), ...(renumOn(c) ? { renumber: true } : {}) }; };
  const useAs = (s, k) => {  // a suggestion → one of your cues: show that cue's move, set to here
    const hand = box.querySelector(`[data-hand="${CSS.escape(k)}"]`);
    if (hand) { hand.hidden = false; box.querySelector("#byhand").hidden = false; }
    const tgt = box.querySelector(`select[data-target="${CSS.escape(k)}"]`); if (!tgt) return;
    tgt.closest("details")?.setAttribute("open", "");
    const o = [...tgt.options].find(o => Math.abs(+o.value - +s.dataset.useas) < 1); if (o) tgt.value = o.value;
    tgt.dispatchEvent(new Event("change"));
    const add = box.querySelector(`#approve input[data-key="${CSS.escape(s.dataset.add)}"]`); if (add) add.checked = false;  // moved there, not added
    tgt.closest("label")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  box.querySelectorAll("select[data-target]").forEach(s => s.addEventListener("change", () => {  // picking a bar ticks the move
    const k = CSS.escape(s.dataset.target), cb = box.querySelector(`#approve input[data-key="${k}"]`), pl = box.querySelector(`[data-there="${k}"]`);
    if (cb) cb.checked = true;
    if (pl) pl.dataset.t = +s.value / 1000;
  }));
  box.querySelector("#ap-save")?.addEventListener("click", () => go(ticked()));
  const syncMoves = live => {  // every move in the panel, ticked (its picked bar) or not (null): the waveform draws them
    box._moves = new Map(items.filter(i => i.kind === "move").map(i => {
      const cb = box.querySelector(`#approve input[data-key="${CSS.escape(i.key)}"]`), s = box.querySelector(`select[data-target="${CSS.escape(i.key)}"]`);
      return [moveKey(i.data.hotcue, i.data.from_ms), cb?.checked ? (s ? +s.value : i.data.to_ms) : null];
    }));
    box._movesPath = c.path;
    if (!live) return box._wave?.redraw();  // binding a freshly drawn panel: its ticks aren't restored yet, so they say nothing about moves
    const was = JSON.stringify(c._movedAway || []);
    c._movedAway = items.filter(i => i.kind === "move" && box._moves.get(moveKey(i.data.hotcue, i.data.from_ms)) != null
      && Math.abs(box._moves.get(moveKey(i.data.hotcue, i.data.from_ms)) - i.data.from_ms) >= 1).map(i => i.data.from_ms);
    if (was !== JSON.stringify(c._movedAway)) return redraw();  // a suggestion your moved cue no longer matches opens up
    box._wave?.redraw();
  };
  box.querySelector("#approve")?.addEventListener("change", () => syncMoves(true));
  let slotAsk = 0;
  const showSlots = () => {  // "auto" names its slot: the server's resolve() on what's ticked, the same as apply writes it
    const sels = [...box.querySelectorAll("#approve select.slot")];
    const plan = ticked(), n = ++slotAsk, label = (s, text) => { s.options[0].textContent = text; };
    c._delTicked = plan.remove;  // a cue ticked for deletion can't be moved or corrected: untick its move
    let unticked = false;
    items.filter(i => i.kind === "move").forEach(i => { const cb = box.querySelector(`#approve input[data-key="${CSS.escape(i.key)}"]`); if (!cb) return;
      const gone = cueGone(c, { hotcue: i.data.hotcue, from_ms: i.data.from_ms }); if (gone && cb.checked) { cb.checked = false; unticked = true; } cb.disabled = gone; });
    if (unticked) syncMoves(true);
    // the waveform draws the ticked new cues as pending (dashed), numbered once the server says which slot
    const pend = plan.add.filter(a => !isApprovedAdd(c, a.ms));
    box._pending = PEND = { path: c.path, adds: pend.map(a => ({ ...a, hotcue: a.slot ?? null })), removes: plan.remove, slots: null };
    const pads = () => {
      const y = box.querySelector("#your-pads"); if (y) y.innerHTML = yourPads(c, box._pending); box._wave?.redraw();
      box.querySelectorAll("#approve [data-renum]").forEach(s => {  // Delete your cues names each cue by its slot in Traktor, and where the pads show it
        const h = +s.dataset.renum, cue = myCues(c).find(y => y.status !== "deleted" && origOf(y).hotcue === h), to = cue && (pendSlot(cue, box._pending) ?? cue.hotcue);
        s.textContent = cue && !isDel(cue, box._pending) && to !== h ? (to >= 0 ? ` → ${to + 1}` : " → no slot") : "";
      });
    };
    pads();
    if (!(plan.add.length || plan.remove.length || plan.renumber) || SNAP) return sels.forEach(s => label(s, "slot: auto"));  // a deletion slides the cues after it back
    send("/api/cues/slots", { path: t.path, plan }).then(r => {
      if (n !== slotAsk || !box.isConnected) return;
      if (box._pending) {
        box._pending.adds.forEach(a => { const got = r.new.find(g => Math.abs(g.ms - a.ms) < 1); a.hotcue = got ? got.slot : -1; a.nofit = !got; });
        box._pending.slots = { new: r.new, up: new Map(r.moved_up.map(m => [m.was, m.to])) };  // every cue's slot after this, approved ones too
        pads();
      }
      sels.forEach(s => {
        const it = items.find(i => i.key === s.dataset.slot), cb = box.querySelector(`#approve input[data-key="${CSS.escape(s.dataset.slot)}"]`);
        if (!it || !cb?.checked) return label(s, "slot: auto");
        const got = r.new.find(a => Math.abs(a.ms - it.data.ms) < 1);
        label(s, got ? `slot ${got.slot + 1} (auto)` : "auto: won't fit");
      });
    }).catch(() => {});
  };
  box.querySelector("#approve")?.addEventListener("change", showSlots);
  showSlots();
  box.querySelector("#your-pads")?.addEventListener("click", e => {  // a pad's ✕: tick (or untick) its deletion in the panel
    const u = e.target.closest("[data-unadd]");  // a ticked, not yet approved pad's ✕: untick it; a hand-set cue goes away altogether
    if (u) {
      e.preventDefault(); e.stopPropagation();
      const ms = +u.dataset.unadd, it = items.find(i => i.kind === "add" && Math.abs(i.data.ms - ms) < 1);
      if (it?.hand) { HAND.set(c.path, (HAND.get(c.path) || []).filter(h => Math.abs(h.ms - ms) >= 1)); return redraw(); }
      const cb = it && box.querySelector(`#approve input[data-key="${CSS.escape(it.key)}"]`);
      if (cb) { cb.checked = false; cb.dispatchEvent(new Event("change", { bubbles: true })); }
      return;
    }
    const b = e.target.closest("[data-del]"); if (!b) return;
    e.preventDefault(); e.stopPropagation();
    const cb = box.querySelector(`#approve input[data-key="${CSS.escape(b.dataset.del)}"]`); if (!cb) return;
    cb.checked = !cb.checked;
    if (cb.checked) { const d = cb.closest("details"); if (d) d.open = true; }
    cb.dispatchEvent(new Event("change", { bubbles: true }));
  });
  const redraw = after => {  // draw the panel again from c, keeping what's ticked, picked, typed and open, and the scroll
    const q = sel => [...box.querySelectorAll(sel)];
    const ticks = new Map(q("#approve input[data-key]").map(i => [i.dataset.key, i.checked]));
    const vals = q("#approve [data-target], #approve [data-slot], #approve [data-as], #approve [data-nm]").map(e => [e.dataset.target ?? e.dataset.slot ?? e.dataset.as ?? e.dataset.nm, e.className, e.value]);
    const open = q("#approve details").map(d => d.open), scroll = [], shown = q("#approve [data-hand]:not([hidden])").map(e => e.dataset.hand);
    for (let e = box; e; e = e.parentElement) scroll.push([e, e.scrollTop]);
    box.innerHTML = renderCues(c); drawWave(c, box); bindApprove(c, box, t); deckAttach(c, box);
    q("#approve input[data-key]").forEach(i => { if (ticks.has(i.dataset.key)) i.checked = ticks.get(i.dataset.key); });
    vals.forEach(([k, cls, v]) => { const e = q(`#approve .${cls.split(" ")[0]}`).find(e => (e.dataset.target ?? e.dataset.slot ?? e.dataset.as ?? e.dataset.nm) === k); if (e) e.value = v; });
    const ds = q("#approve details"); if (ds.length === open.length) ds.forEach((d, k) => { d.open = open[k]; });
    shown.forEach(k => { const e = box.querySelector(`[data-hand="${CSS.escape(k)}"]`); if (e) { e.hidden = false; const g = box.querySelector("#byhand"); if (g) g.hidden = false; } });  // moves you opened by hand stay open
    scroll.forEach(([e, y]) => { e.scrollTop = y; });
    after?.();
    box.querySelector("#approve")?.dispatchEvent(new Event("change"));  // the pads, waveform and slot labels follow the ticks
  };
  box.querySelector("#dk-cue")?.addEventListener("click", () => {  // + Cue: a cue of yours at the playhead, on the nearest beat of Traktor's grid, ticked
    const beat = 60000 / c.bpm, ms = +(c.grid_ms + Math.round((deckTime() * 1000 - c.grid_ms) / beat) * beat).toFixed(3), msg = m => { const e = box.querySelector("#ap-msg"); if (e) e.textContent = m; };
    if (ms < 0) return;
    const y = shownCues(c, box).find(y => !y.del && Math.abs(y.ms - ms) < 1);  // as moved: a cue moved off this beat leaves it free
    if (y) return msg(`Your ${cueKind(y).toLowerCase()}${y.hotcue >= 0 ? " " + (y.hotcue + 1) : ""} is already on this beat`);
    const tick = () => { const cb = box.querySelector(`#approve input[data-key="h${ms}"]`) || items.map(i => i.kind === "add" && Math.abs(i.data.ms - ms) < 1 && box.querySelector(`#approve input[data-key="${CSS.escape(i.key)}"]`)).find(Boolean);
      if (!cb || cb.disabled) return;
      cb.checked = true; cb.closest("details")?.setAttribute("open", ""); cb.closest("label")?.scrollIntoView({ block: "nearest" });
      msg(`Cue set at ${clock(ms / 1000)}: ticked under New cues, press Approve ticked to keep it`); };
    if (!items.some(i => i.kind === "add" && Math.abs(i.data.ms - ms) < 1)) { HAND.set(c.path, [...(HAND.get(c.path) || []), { ms, name: "Cue" }]); redraw(tick); }
    else { tick(); box.querySelector("#approve")?.dispatchEvent(new Event("change")); }  // a suggestion is already on this beat: tick that one
  });
  box._cueDrag = (y, ms) => {  // a cue dropped on the waveform at ms (a beat of Traktor's grid)
    const msg = m => { const e = box.querySelector("#ap-msg"); if (e) e.textContent = m; };
    if (Math.abs(ms - y.ms) < 1) return;
    const other = shownCues(c, box).find(o => !o.del && Math.abs(o.ms - ms) < 1);
    if (other) return msg(`Your ${cueKind(other).toLowerCase()}${other.hotcue >= 0 ? " " + (other.hotcue + 1) : ""} is already on that beat`);
    if (y.hand) {
      HAND.set(c.path, (HAND.get(c.path) || []).map(h => Math.abs(h.ms - y.ms) < 1 ? { ...h, ms } : h));
      return redraw(() => { const cb = box.querySelector(`#approve input[data-key="h${ms}"]`); if (cb && !cb.disabled) cb.checked = true; });
    }
    const orig = y.status === "moved" ? y.from_ms : y.ms, key = `m${y.was_hotcue ?? y.hotcue}:${orig}`;
    if (!items.some(i => i.key === key)) return msg("That one can't be moved by dragging; use its slot's list under Your cues moved by hand");
    const back = Math.abs(ms - orig) < 1;
    const m = DRAGMV.get(c.path) || new Map(); DRAGMV.set(c.path, m); if (back) m.delete(key); else m.set(key, ms);
    redraw(() => {
      const cb = box.querySelector(`#approve input[data-key="${CSS.escape(key)}"]`), sel = box.querySelector(`select[data-target="${CSS.escape(key)}"]`);
      if (!cb || !sel) return;
      cb.checked = !back; if (!back) sel.value = String(ms);
      const hand = box.querySelector(`[data-hand="${CSS.escape(key)}"]`); if (hand) { hand.hidden = false; const bh = box.querySelector("#byhand"); if (bh) bh.hidden = false; }
      if (!back) { sel.closest("details")?.setAttribute("open", ""); box._wave?.redraw(); }
    });
    msg(back ? "Back where it was: move unticked" : `Cue moved to ${clock(ms / 1000)}: ticked under Your cues moved by hand, press Approve ticked to keep it`);
  };
  box.querySelectorAll("[data-unhand]").forEach(b => b.addEventListener("click", e => {
    e.preventDefault(); e.stopPropagation();
    HAND.set(c.path, (HAND.get(c.path) || []).filter(h => Math.abs(h.ms - +b.dataset.unhand) >= 1)); redraw();
  }));
  box.querySelectorAll("#approve select.as").forEach(s => s.addEventListener("change", () => {  // loop or cue point: ticks it; approved already, saved now
    const cb = box.querySelector(`#approve input[data-key="${CSS.escape(s.dataset.as)}"]`); if (cb) cb.checked = true;
    const it = items.find(i => i.key === s.dataset.as), ap = c.approved;
    if (!it || !ap || ap.grid_changed || !ap.add.some(a => Math.abs(a.ms - it.data.ms) < 1)) return;
    const add = ap.add.map(a => Math.abs(a.ms - it.data.ms) >= 1 ? a : { ...asData(it), ...(a.slot != null ? { slot: a.slot } : {}) });
    send("/api/cues/approve", { path: t.path, plan: { add, move: ap.move, remove: ap.remove || [], ...(ap.renumber ? { renumber: true } : {}) } }).then(d => refresh(d, `Saved: ${it.data.name} → ${s.value === "cue" ? "as a cue point" : "as a loop"}`))
      .catch(e => { const m = box.querySelector("#ap-msg"); if (m) m.textContent = e.message; });
  }));
  box.querySelectorAll("#approve select.len").forEach(s => s.addEventListener("change", () => {  // a loop's length: ticks it; approved already, saved now
    const k = s.dataset.len; loopLen(c.path).set(k, +s.value);
    const it = items.find(i => i.key === k), ap = c.approved;
    const approved = it && ap && !ap.grid_changed && ap.add.some(a => Math.abs(a.ms - it.data.ms) < 1 && a.type === "loop");
    redraw(() => { const cb = box.querySelector(`#approve input[data-key="${CSS.escape(k)}"]`); if (cb && !cb.disabled) cb.checked = true; });
    if (!approved) return;
    const add = ap.add.map(a => Math.abs(a.ms - it.data.ms) >= 1 ? a : { ...a, len_ms: +(it.data.len_ms / it.lenOf.bars * +s.value).toFixed(3), name: `Loop ${+s.value}` });
    send("/api/cues/approve", { path: t.path, plan: { add, move: ap.move, remove: ap.remove || [], ...(ap.renumber ? { renumber: true } : {}) } }).then(d => refresh(d, `Saved: loop → ${s.value} bars`))
      .catch(e => { const m = box.querySelector("#ap-msg"); if (m) m.textContent = e.message; });
  }));
  box.querySelectorAll("#approve select.slot").forEach(s => s.addEventListener("change", () => {  // picking a slot ticks the cue
    const cb = box.querySelector(`#approve input[data-key="${CSS.escape(s.dataset.slot)}"]`); if (cb && s.value) cb.checked = true;
    const it = items.find(i => i.key === s.dataset.slot), ap = c.approved;  // already approved: save the new slot now, so the pads and waveform show it
    if (!it || !ap || ap.grid_changed || !ap.add.some(a => Math.abs(a.ms - it.data.ms) < 1)) return;
    const add = ap.add.map(a => { if (Math.abs(a.ms - it.data.ms) >= 1) return a; const { slot: _, ...rest } = a; return s.value ? { ...rest, slot: +s.value } : rest; });
    send("/api/cues/approve", { path: t.path, plan: { add, move: ap.move, remove: ap.remove || [], ...(ap.renumber ? { renumber: true } : {}) } }).then(d => refresh(d, `Saved: ${it.data.name || "the cue"} → ${s.value ? `slot ${+s.value + 1}` : "slot auto"}`))
      .catch(e => { const m = box.querySelector("#ap-msg"); if (m) m.textContent = e.message; });
  }));
  syncMoves(false);
  box._hover = [];  // the panel was just drawn again: nothing under the mouse yet
  box.querySelectorAll(".pads").forEach(p => {  // so do the pads (your cues, suggestions, loop ideas): a bar, or the loop's length
    let on = null;
    p.addEventListener("mouseover", e => {
      const b = e.target.closest("button[data-t]");
      if (b === on) return;
      on = b; box._hover = b ? [[+b.dataset.t, +b.dataset.t + (+b.dataset.loop || c.bar_seconds)]] : [];
      box._hoverPath = c.path; box._wave?.redraw();
    });
    p.addEventListener("mouseleave", () => { on = null; box._hover = []; box._wave?.redraw(); });
  });
  const ap = box.querySelector("#approve");
  if (ap) {  // hovering a row lights up where it is on the waveform (a move: where the cue is and where it would go)
    let at = null;
    const show = key => {
      if (key === at) return;
      at = key;
      const it = key && items.find(i => i.key === key), bar = c.bar_seconds;
      if (!it) box._hover = [];
      else if (it.kind === "remove") box._hover = [[it.data.ms / 1000, it.data.ms / 1000 + (it.data.len_ms ? it.data.len_ms / 1000 : bar)]];  // a deletion: the cue itself (it has no from_ms)
      else if (it.kind === "add") box._hover = [[it.data.ms / 1000, it.data.ms / 1000 + (it.data.len_ms ? it.data.len_ms / 1000 : bar)]];
      else {
        const s = box.querySelector(`select[data-target="${CSS.escape(key)}"]`), to = (s ? +s.value : it.data.to_ms) / 1000;
        box._hover = [[it.data.from_ms / 1000, it.data.from_ms / 1000 + bar], [to, to + bar]];
      }
      box._hoverPath = c.path; box._wave?.redraw();
    };
    ap.addEventListener("mouseover", e => show(e.target.closest("label")?.querySelector("input[data-key]")?.dataset.key || null));
    ap.addEventListener("mouseleave", () => show(null));
    ap.addEventListener("change", () => { const k = at; at = null; show(k); });  // a new bar picked while hovering
    // "move my cue here…": a menu of its own, since an open <select> tells the page nothing about the option under the mouse
    const light = spans => { at = undefined; box._hover = spans; box._hoverPath = c.path; box._wave?.redraw(); };
    document.querySelectorAll(".usemenu").forEach(m => m.remove());
    let menu = null;
    const close = () => { if (!menu) return; menu.remove(); menu = null; light([]); removeEventListener("keydown", esc_); document.removeEventListener("mousedown", out); removeEventListener("scroll", scr, true); };
    const scr = e => { if (menu && !menu.contains(e.target)) close(); };  // it stays put while the page moves
    const esc_ = e => { if (e.key === "Escape") close(); };
    const out = e => { if (menu && !menu.contains(e.target) && !e.target.closest("button.useas, select.target")) close(); };
    box.querySelectorAll("button.useas").forEach(b => b.addEventListener("click", e => {
      e.preventDefault(); e.stopPropagation();
      const was = menu?._for === b; close(); if (was) return;
      const here = +b.dataset.useas / 1000, bar = c.bar_seconds;
      menu = document.createElement("div"); menu.className = "usemenu"; menu._for = b;
      menu.innerHTML = b.dataset.mine.split(" ").map(k => items.find(i => i.key === k)).filter(Boolean).map(m =>
        `<div data-k="${esc(m.key)}">Cue ${m.cue.hotcue + 1}${m.cue.name ? ` (${esc(m.cue.name)})` : ""} · bar ${m.cue.from_bar}</div>`).join("");
      document.body.append(menu);
      const r = b.getBoundingClientRect(), h = menu.offsetHeight;
      menu.style.left = `${Math.min(r.left, innerWidth - menu.offsetWidth - 8)}px`;
      menu.style.top = `${r.bottom + 2 + h > innerHeight ? Math.max(8, r.top - 2 - h) : r.bottom + 2}px`;
      menu.addEventListener("mouseover", e => {  // that cue where it is now, and here
        const k = e.target.closest("[data-k]")?.dataset.k, m = k && items.find(i => i.key === k); if (!m) return;
        light([[m.data.from_ms / 1000, m.data.from_ms / 1000 + bar], [here, here + bar]]);
      });
      menu.addEventListener("mouseleave", () => light([]));
      menu.addEventListener("click", e => { const k = e.target.closest("[data-k]")?.dataset.k; if (!k) return; close(); useAs(b, k); });
      addEventListener("keydown", esc_); document.addEventListener("mousedown", out); addEventListener("scroll", scr, true);
    }));
    // a cue's "move to bar…" list the same way: hovering a bar lights it (and the cue where it is now) on the waveform
    box.querySelectorAll("select.target").forEach(sel => sel.addEventListener("mousedown", e => {
      if (e.button) return;
      e.preventDefault(); sel.focus();  // no native list: it can't report the option under the mouse
      const was = menu?._for === sel; close(); if (was) return;
      const it = items.find(i => i.key === sel.dataset.target), bar = c.bar_seconds, from = it.data.from_ms / 1000;
      menu = document.createElement("div"); menu.className = "usemenu"; menu._for = sel;
      menu.innerHTML = [...sel.options].map((o, k) => `<div data-i="${k}"${o.selected ? ' class="on"' : ""}>${esc(o.textContent)}</div>`).join("");
      document.body.append(menu);
      const r = sel.getBoundingClientRect(), h = menu.offsetHeight;
      menu.style.left = `${Math.max(8, Math.min(r.left, innerWidth - menu.offsetWidth - 8))}px`;
      menu.style.top = `${r.bottom + 2 + h > innerHeight ? Math.max(8, r.top - 2 - h) : r.bottom + 2}px`;
      menu.querySelector(".on")?.scrollIntoView({ block: "nearest" });
      menu.addEventListener("mouseover", e => {
        const k = e.target.closest("[data-i]")?.dataset.i; if (k == null) return;
        const to = +sel.options[k].value / 1000;
        light([[from, from + bar], [to, to + bar]]);
      });
      menu.addEventListener("mouseleave", () => light([]));
      menu.addEventListener("click", e => {
        const k = e.target.closest("[data-i]")?.dataset.i; if (k == null) return;
        close(); sel.selectedIndex = +k; sel.dispatchEvent(new Event("change", { bubbles: true }));
      });
      addEventListener("keydown", esc_); document.addEventListener("mousedown", out); addEventListener("scroll", scr, true);
    }));
  }
  box.querySelector("#ap-write")?.addEventListener("click", () => writeToTraktor(async () => { S.tracks = {}; await refresh({}, ""); if (S.page === "tracks") S.reloadTracks?.(); }));
  box.querySelector("#ap-all")?.addEventListener("click", () => box.querySelectorAll('#approve input[data-key^="a"]:not(:disabled):not([data-more])').forEach(i => { i.checked = true; }) || showSlots());
  const sel = box.querySelector("#smp-folder");
  if (sel) {
    (S.sampleFolders ? Promise.resolve(S.sampleFolders) : api("samples/folders").then(f => (S.sampleFolders = f))).then(f => {
      let last = null; try { last = localStorage.getItem("ta-sample-folder"); } catch (e) {}
      sel.innerHTML = f.folders.map(n => `<option ${n === (f.folders.includes(last) ? last : f.default) ? "selected" : ""}>${esc(n)}</option>`).join("");
      sel.title = f.root;
    }).catch(() => {});
    sel.addEventListener("change", () => { try { localStorage.setItem("ta-sample-folder", sel.value); } catch (e) {} });
    box.querySelector("#smp-open")?.addEventListener("click", e => { e.preventDefault(); e.stopPropagation();
      postJSON("/api/samples/reveal", { folder: sel.value }).catch(err => { e.target.textContent = err.message; }); });
  }
  box.querySelectorAll("[data-save]").forEach(b => b.addEventListener("click", async e => {
    e.preventDefault(); e.stopPropagation();
    const it = items.find(i => i.key === b.dataset.save), out = box.querySelector(`[data-saved="${CSS.escape(b.dataset.save)}"]`);
    b.disabled = true; out.textContent = " saving…";
    try {
      const d = await postJSON("/api/samples/save", { path: t.path, start: it.data.ms / 1000, len: it.data.len_ms / 1000, folder: sel.value, bar: it.sample.bar, kind: it.sample.kind, smooth: smoothLoops() });
      out.innerHTML = ` ✓ ${d.already ? "already saved in" : "saved to"} ${esc(d.folder)} ${showBtn(d.saved, c.file_manager)}`;
      b.remove();
    } catch (err) { out.style.color = "var(--bad)"; out.textContent = ` ${err.message}`; }
    finally { b.disabled = false; }
  }));
  if (!box._revealBound) {  // Show in Finder / Explorer (once per panel: bindApprove runs again after each change)
    box._revealBound = true;
    box.addEventListener("click", e => {
      const b = e.target.closest("[data-reveal]"); if (!b) return;
      e.preventDefault(); e.stopPropagation();
      postJSON("/api/samples/reveal", { file: b.dataset.reveal }).catch(err => { b.textContent = err.message; });
    });
  }
  box.querySelector("#smooth-loops")?.addEventListener("change", async e => {
    S.smoothLoops = e.target.checked; try { localStorage.setItem("ta-smooth-loop", S.smoothLoops ? "1" : "0"); } catch (err) {}
    if (DECK.src && DECK.loop) deckLoop(DECK.loop[0], DECK.loop[1]);  // hear the change straight away
    await refresh({}, S.smoothLoops ? "Loop points smoothed" : "Exact loop points");
  });
  box.querySelector("#ap-fixall")?.addEventListener("click", e => { e.preventDefault(); box.querySelectorAll('#fixes input[data-key^="m"]:not(:disabled)').forEach(i => { i.checked = true; });  // not the moves of cues ticked for deletion
    box.querySelector("#approve")?.dispatchEvent(new Event("change")); });  // the moves, pads and slot labels follow the ticks
  box.querySelector("#ap-clear")?.addEventListener("click", () => { RENUM.delete(c.path); go(null); });
  box.querySelector("#ap-renum")?.addEventListener("click", e => {  // pressed: the pads show the new numbers; Approve ticked keeps it
    const on = !renumOn(c); RENUM.set(c.path, on);
    e.currentTarget.classList.toggle("primary", on); e.currentTarget.setAttribute("aria-pressed", on);
    box.querySelector("#approve")?.dispatchEvent(new Event("change"));
    const m = box.querySelector("#ap-msg"); if (m) m.textContent = on ? "Cues numbered in the order they play: press Approve ticked to keep it" : "Numbering left as it is";
  });
}
