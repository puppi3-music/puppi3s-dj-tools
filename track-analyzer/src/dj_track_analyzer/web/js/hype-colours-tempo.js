// ---------- hype ----------
const HF = { colour: "", bpm: "", tolerance: 4, key: "", gems: false, least: false };
async function pageHype(main) {
  main.innerHTML = `
    <div class="page-head"><div><h2>Hype</h2><div class="muted">1-10 within your collection: rave sound and energy. Uses the scores from the last scan.</div></div>
      <div class="spacer"></div>${SNAP ? "" : `<button id="h-write" data-tip="Adds 'Hype 07' to Comment in ${DJ()}, after your own text (shows what changes first)">Write to ${DJ()}'s Comment…</button>`}</div>
    <div class="filters">
      <select id="h-colour"><option value="">Any colour</option>${COLOURS.map(c => `<option ${c === HF.colour ? "selected" : ""}>${c}</option>`).join("")}</select>
      <label class="inline">BPM <input type="number" id="h-bpm" value="${esc(HF.bpm)}" placeholder="any"> ± <input type="number" id="h-tol" value="${HF.tolerance}" style="width:56px">%</label>
      <label class="inline">Mixes with key <input type="text" id="h-key" value="${esc(HF.key)}" placeholder="8A" style="width:56px"></label>
      <label class="inline" data-tip="hype 8+ but 4 stars or fewer, or played at most once"><input type="checkbox" id="h-gems" ${HF.gems ? "checked" : ""}> gems</label>
      <label class="inline"><input type="checkbox" id="h-least" ${HF.least ? "checked" : ""}> least hype first</label>
    </div><div id="hres" class="loading">Loading…</div>`;
  const read = () => { HF.colour = $("#h-colour").value; HF.bpm = $("#h-bpm").value; HF.tolerance = $("#h-tol").value; HF.key = $("#h-key").value; HF.gems = $("#h-gems").checked; HF.least = $("#h-least").checked; load(); };
  main.querySelectorAll(".filters select, .filters input").forEach(el => el.addEventListener("change", read));
  $("#h-write")?.addEventListener("click", () => writeDialog("hype_traktor"));
  let seq = 0;
  async function load() {
    const box = $("#hres"), ask = ++seq;  // a later filter change wins; leaving the tab drops the answer
    try {
      const d = await api("hype", { lib: S.lib, colour: HF.colour, bpm: HF.bpm, tolerance: HF.tolerance, key: HF.key, gems: HF.gems ? 1 : 0, least: HF.least ? 1 : 0, limit: 500 });
      if (ask !== seq || !box.isConnected) return;
      box.className = "";
      if (!d.scored) { box.innerHTML = empty("No hype scores yet", "Hype ranks every track 1-10 by rave sound and energy within your collection. It needs 20 scanned tracks and takes seconds.", toSetup("Score hype")); return; }
      if (!d.matches) { box.innerHTML = empty("No tracks match", `None of the ${d.scored} scored tracks match ${esc(d.filters.join(", "))}.`); return; }
      box.innerHTML = `<div class="muted small" style="margin-bottom:8px">${d.matches} of ${d.scored} tracks match (${esc(d.filters.join(", "))}); ${esc(d.order)}</div>
        <div class="card" style="padding:0">${table([
          { h: "Hype", r: 1, f: t => hypeBadge(t.hype) }, { h: "Stars", f: t => t.stars ? "★".repeat(t.stars) : `<span class="dim">–</span>` },
          { h: "Plays", r: 1, f: t => fmt(t.plays) }, { h: "Colour", f: t => colour(t.colour, !t.colour_is_yours) }, { h: "Track", f: trackName },
          { h: "BPM", r: 1, f: t => fmt(t.bpm) }, { h: "Key", f: t => esc(t.camelot || "–") },
        ], d.tracks, { rowAttr: t => `class="click" data-path="${esc(t.path)}"` })}</div>`;
      box.onclick = e => { const tr = e.target.closest("tr[data-path]"); if (tr) openTrack(tr.dataset.path); };
    } catch (e) { if (ask === seq && box.isConnected) { box.className = ""; box.innerHTML = errBox(e); } }
  }
  load();
}

// ---------- colours ----------
async function pageColours(main) {
  const me = S.routeId, viewOn = S.libs.loaded_view, viewTip = viewOn ? `${S.libs.loaded_app || DJ()} has the ${viewOn} BPM view loaded. Switch back first (Tempo view tab), then write` : "";
  const RSEL = new Set();  // ticked review rows (paths)
  const markCell = t => t.kept || t.recoloured ? "" : t.marked
    ? `<span class="nowrap"><span class="chip ${t.marked === "approve" ? "warn" : ""}" data-tip="Marked: happens when you press Write">${t.marked === "approve"
        ? `will recolour to&nbsp; ${sw(t.sounds_like)}${esc(t.sounds_like)}` : `will keep&nbsp; ${sw(t.your_colour)}${esc(t.your_colour)} always`}</span> <button class="mini" data-mark="" data-p="${esc(t.path)}" data-tip="Unmark">✕</button></span>`
    : `<span class="nowrap"><button class="mini" data-mark="approve" data-p="${esc(t.path)}" data-tip="Mark: recolour it ${esc(t.sounds_like)} when you press Write">Approve ${esc(t.sounds_like)}${t.confidence_exact != null ? ` (${pct(t.confidence_exact)})` : ""}</button> <button class="mini" data-mark="keep" data-p="${esc(t.path)}" data-tip="Mark: keep your ${esc(t.your_colour)} always when you press Write">Keep ${esc(t.your_colour)}</button></span>`;
  const reviewTable = rows => table([
          ...(SNAP ? [] : [{ h: `<input type="checkbox" data-ckall aria-label="Tick every track in this list">`, raw: 1,
            f: t => t.kept || t.recoloured ? "" : `<input type="checkbox" data-ck="${esc(t.path)}" aria-label="Tick" ${RSEL.has(t.path) ? "checked" : ""}>` }]),
          { h: "Track", f: t => `<div class="trk"><div class="t">${esc(t.file)}</div></div>` },
          { h: "Yours", f: t => colour(t.your_colour) }, { h: "Sounds like", f: t => colour(t.sounds_like) },
          { h: "Gap", r: 1, f: t => fmt(t.gap) }, { h: "Confidence", r: 1, f: t => t.confidence == null ? "–" : `<span class="nowrap">${pct(t.confidence)} <span class="dim small">${esc(nearRange(t.sounds_like))}</span></span>` },
          { h: "", f: t => (t.meets_apply_threshold ? `<span class="chip warn">over threshold</span> ` : "") + (t.kept ? `<span class="chip">kept</span>` : "")
            + (t.recoloured ? `<span class="chip good" data-tip="You recoloured it since training. Retrain to learn from it; it then leaves this list.">recoloured&nbsp; ${sw(t.recoloured)}${esc(t.recoloured)}</span>` : "") },
          ...(SNAP ? [] : [{ h: "", r: 1, f: markCell }]),
        ], rows, { rowAttr: t => `class="click" data-path="${esc(t.path)}"` });
  main.innerHTML = `<div class="page-head"><div><h2>Colours</h2><div class="muted">What your colour scale means, and where the model disagrees with you.</div></div></div>
    <div id="c-learn"></div><div id="c-model" class="loading">Loading model…</div><div id="c-explain" class="loading">Loading profile (reads ${DJ()}'s collection and History)…</div>`;
  const [rv, ex, kp] = await Promise.allSettled([api("colours/review", { limit: 200 }), api("colours/explain"), api("colours/kept")]);
  const m = $("#c-model"); if (!m || me !== S.routeId) return;  // left the tab (or came back to it) while it loaded
  m.className = "";
  if (rv.status === "rejected") m.innerHTML = errBox(rv.reason);
  else {
    let r = rv.value, byPath = new Map(r.tracks.map(t => [t.path, t]));
    const draw = () => {
      const a = r.accuracy || {};
      const retrain = r.recoloured_since_training;
      const marks = r.tracks.filter(t => t.marked), nA = marks.filter(t => t.marked === "approve").length, nK = marks.length - nA;
      m.innerHTML = `<div class="tiles">
        <div class="tile"><div class="v">${fmt(a.average_error_per_colour)}</div><div class="l">average error per colour (in colours)</div></div>
        <div class="tile"><div class="v">${pct(a.within_one_colour)}</div><div class="l">within one colour</div></div>
        <div class="tile"><div class="v">${pct(a.exact)}</div><div class="l">exactly right</div></div>
        <div class="tile"><div class="v" style="font-size:15px;padding-top:6px">${esc(r.trained_at)}</div><div class="l">trained · ${esc(r.model || "")}</div></div>
      </div>
      <div class="card"><h3>Tracks that disagree with their colour <span class="dim small">(cross-validated: predicted without seeing the track)</span></h3>
        <div class="note" style="margin:0 0 10px">${r.apply_threshold.tracks_meeting_it} meet the recolour threshold (gap ≥ ${r.apply_threshold.min_gap}, confidence ≥ ${pct(r.apply_threshold.min_confidence)}).
        ${r.always_kept ? `${r.always_kept} you chose to keep always.` : ""}
        ${retrain ? `${plural(retrain, "track")} you recoloured since training ${retrain === 1 ? "is" : "are"} marked <b>recoloured</b> until you retrain.` : ""}
        ${SNAP ? "" : `<button class="mini" id="c-recolour" style="margin-left:4px" ${viewOn ? `disabled title="${viewTip}"` : ""}>Recolour them in ${DJ()}…</button>`}
        ${SNAP ? "" : `<br>Or mark tracks one by one (or tick several): <b>Approve</b> recolours to what it sounds like, <b>Keep</b> keeps your colour always. Nothing changes until you press Write.`}</div>
        ${marks.length && !SNAP ? `<div class="selbar"><b>Marked: ${[nA && `${nA} to recolour`, nK && `${nK} to keep`].filter(Boolean).join(", ")}</b>
          <button class="primary" id="c-write" ${viewOn && nA ? `disabled title="${viewTip}"` : `data-tip="Preview first; ${DJ()} closed${nA ? "" : ` isn't needed: keeping writes nothing to ${DJ()}`}"`}>Write…</button>
          <button id="c-unmark">Unmark all</button><span class="small muted" id="c-markmsg"></span></div>` : ""}
        <div id="c-sel"></div>
        ${reviewTable(r.tracks.slice(0, 25))}
        ${r.tracks.length > 25 ? `<details ${m.dataset.more ? "open" : ""}><summary class="small muted" style="margin-top:8px;cursor:pointer">Show ${r.tracks.length - 25} more</summary>${reviewTable(r.tracks.slice(25))}</details>` : ""}
      </div>`;
      m.querySelector("details")?.addEventListener("toggle", e => { m.dataset.more = e.target.open ? "1" : ""; });
      $("#c-recolour")?.addEventListener("click", e => { e.stopPropagation(); writeDialog("colors_recolour", {}, () => route()); });
      $("#c-write")?.addEventListener("click", () => writeDialog("colors_write_marked", {}, () => route()));
      $("#c-unmark")?.addEventListener("click", () => mark(marks.map(t => t.path), null));
      selbar();
    };
    const mark = async (paths, action) => {
      try {
        await postJSON("/api/colours/mark", { paths, action });
        for (const p of paths) { const t = byPath.get(p); if (t) { if (action) t.marked = action; else delete t.marked; } }
        colourMarked(paths, action); RSEL.clear(); draw();
      } catch (err) { const el = $("#c-markmsg") || $("#c-selmsg") || $("#c-sel"); if (el) el.textContent = err.message; }  // a row's button with no bar yet: #c-sel
    };
    S.colourMarked = (paths, action) => { if (me !== S.routeId) return; for (const p of paths) { const t = byPath.get(p); if (t) { if (action) t.marked = action; else delete t.marked; } } draw(); };  // marked in the track panel
    const selbar = () => {
      const box = $("#c-sel"), n = RSEL.size;
      m.querySelectorAll("table").forEach(tb => {  // each list's header box: ticked when all its rows are
        const all = tb.querySelector("[data-ckall]"), cks = [...tb.querySelectorAll("[data-ck]")];
        if (all) { all.checked = cks.length > 0 && cks.every(c => c.checked); all.indeterminate = !all.checked && cks.some(c => c.checked); }
      });
      if (!box) return;
      if (!n) { box.innerHTML = ""; return; }
      box.innerHTML = `<div class="selbar"><b>${plural(n, "track")} ticked</b>
        <button data-sel="approve" data-tip="Mark them: recolour to what they sound like when you press Write">Approve</button>
        <button data-sel="keep" data-tip="Mark them: keep your colours always when you press Write">Keep</button>
        <button data-sel="unmark">Unmark</button>
        <button data-sel="clear">Clear</button><span class="small muted" id="c-selmsg"></span></div>`;
      box.querySelector('[data-sel="clear"]').onclick = () => { RSEL.clear(); m.querySelectorAll("[data-ck]").forEach(c => { c.checked = false; }); selbar(); };
      box.querySelector('[data-sel="approve"]').onclick = () => mark([...RSEL], "approve");
      box.querySelector('[data-sel="keep"]').onclick = () => mark([...RSEL], "keep");
      box.querySelector('[data-sel="unmark"]').onclick = () => mark([...RSEL], null);
    };
    m.onchange = e => {
      const ck = e.target.closest("[data-ck], [data-ckall]");
      if (!ck) return;
      const boxes = ck.dataset.ckall != null ? [...ck.closest("table").querySelectorAll("[data-ck]")] : [ck];
      for (const b of boxes) { b.checked = ck.checked; if (ck.checked) RSEL.add(b.dataset.ck); else RSEL.delete(b.dataset.ck); }
      selbar();
    };
    m.onclick = e => {
      if (e.target.closest("input, button:not([data-mark])")) return;  // a tick box or a bar button, not the row
      const mk = e.target.closest("button[data-mark]");
      if (mk) return mark([mk.dataset.p], mk.dataset.mark || null);
      const tr = e.target.closest("tr[data-path]"); if (tr) openTrack(tr.dataset.path);
    };
    const learn = () => retrainBars($("#c-learn"), ["colours"], { colours: r.recoloured_since_training && `${r.recoloured_since_training} of them on the list below` });
    draw(); learn();
    // a hand edit in the track panel changes your colour: list the track as recoloured / kept straight away
    S.reloadColours = async () => {
      if (!m.isConnected) return;
      let n;
      try { n = await api("colours/review", { limit: 200 }); } catch (e) { return; }  // the list stays as it was
      if (!m.isConnected) return;
      r = n; byPath = new Map(r.tracks.map(t => [t.path, t]));
      for (const p of [...RSEL]) if (!byPath.has(p)) RSEL.delete(p);
      draw(); learn();
    };
  }
  const x = $("#c-explain"); x.className = "";
  if (ex.status === "rejected") { x.innerHTML = `<div class="card"><h3>Colour profile</h3>${errBox(ex.reason)}</div>`; return; }
  const e = ex.value, names = e.colours;
  const profRows = Object.entries(e.profile).map(([feat, per]) => {
    const vals = names.map(n => per[n]).filter(v => v != null), lo = Math.min(...vals), hi = Math.max(...vals);
    return `<tr><td>${esc(feat)}${e.correlations[feat] != null ? ` <span class="dim small" data-tip="Spearman correlation with Red=1 … Magenta=7">ρ ${e.correlations[feat] > 0 ? "+" : ""}${e.correlations[feat]}</span>` : ""}</td>${names.map(n => {
      const v = per[n], s = v == null || hi === lo ? 0 : (v - lo) / (hi - lo);
      return `<td class="h" style="background:color-mix(in srgb, var(--bar) ${Math.round(s * 55)}%, transparent)" data-tip="${esc(feat)} · ${n}: ${v ?? "–"}">${v == null ? "–" : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2)}</td>`;
    }).join("")}</tr>`;
  }).join("");
  const sets = e.sets.by_colour;
  x.innerHTML = `
    <div class="card"><h3>Profile per colour <span class="dim small">(${e.tracks} coloured tracks; darker = highest of the row)</span></h3>
      <div class="tablewrap"><table class="heat"><thead><tr><th>Feature</th>${names.map(n => `<th class="r">${colour(n)}<div class="dim small">${e.tracks_per_colour[n]}</div></th>`).join("")}</tr></thead><tbody>${profRows}</tbody></table></div>
      <div class="note">${e.notes.map(esc).join("<br>")}</div></div>
    <div class="grid two">
      <div class="card"><h3>Strongest links to the scale</h3>${bars(e.strongest_links.map(l => ({ label: l.feature, value: Math.abs(l.correlation), valText: (l.correlation > 0 ? "+" : "") + l.correlation + (l.correlation > 0 ? " → Magenta" : " → Red") })), { max: 1 })}</div>
      <div class="card"><h3>Where in a set <span class="dim small">(${e.sets.sessions} History sessions)</span></h3>
        ${table([{ h: "Colour", f: n => colour(n) }, { h: "Plays", r: 1, f: n => sets[n].plays },
          ...["first_third", "middle_third", "last_third"].map(k => ({ h: k.replace("_", " "), r: 1, f: n => `<span style="display:inline-block;width:${Math.round(sets[n][k] * 60)}px;height:8px;background:var(--bar);border-radius:0 3px 3px 0;margin-right:6px"></span>${pct(sets[n][k])}` }))],
          names.filter(n => sets[n]))}</div>
    </div>
    <div class="grid two">
      <div class="card"><h3>Playlists <span class="dim small">(3+ coloured tracks, hardest first)</span></h3>
        ${table([{ h: "Playlist", f: p => esc(p.playlist) }, { h: "Tracks", r: 1, f: p => p.tracks }, { h: "Avg", r: 1, f: p => p.average },
          { h: "Mix", f: p => `<div class="stack">${names.filter(n => p.counts[n]).map(n => `<div class="sw-${n}" style="flex:${p.counts[n]}" data-tip="${n}: ${p.counts[n]}"></div>`).join("")}</div>` },
          { h: "Stands out", f: p => p.stands_out.map(n => colour(n)).join(" ") }], e.playlists)}</div>
      <div class="card"><h3>Artists</h3>${e.artists ? `<div class="muted small" style="margin-bottom:8px">${e.artists.artists_with_3_plus} artists with 3+ tracks. Their tracks sit ${e.artists.spread_within_artist} colours from the artist's average, random groups ${e.artists.spread_random_groups}.</div>
        ${table([{ h: "Artist", f: a => esc(a.artist) }, { h: "Tracks", r: 1, f: a => a.tracks }, { h: "Average", f: a => `${colour(a.average_colour)} <span class="dim small">${a.average}</span>` }], e.artists.top)}` : `<div class="dim">Not enough artists with 3+ tracks.</div>`}</div>
    </div>
    <div id="c-kept"></div>`;
  if (kp.status !== "fulfilled") return;
  let kept = kp.value.always_kept;
  const k = $("#c-kept");
  const drawKept = (msg = "") => {
    k.innerHTML = kept.length ? `<div class="card"><h3>Kept always <span class="dim small">(Recolour them… skips these)</span></h3>
      ${table([{ h: "Track", f: t => esc(t.file) }, { h: "Your colour", f: t => colour(t.your_colour) }, { h: "Since", f: t => esc(t.kept_since) },
        ...(SNAP ? [] : [{ h: "", f: t => `<button class="mini" data-forget="${esc(t.path)}" data-tip="Take it off this list, so the review can suggest a new colour for it again">Forget</button>` }])], kept)}
      <div class="small muted" id="c-keptmsg">${esc(msg)}</div></div>` : "";
  };
  k.onclick = async e => {
    const b = e.target.closest("button[data-forget]"); if (!b) return;
    b.disabled = true;
    try {
      kept = (await postJSON("/api/colours/forget", { paths: [b.dataset.forget] })).always_kept;
      drawKept(); S.reloadColours?.();  // the review list drops its "kept" chip
    } catch (err) { b.disabled = false; const el = $("#c-keptmsg"); if (el) el.textContent = err.message; }
  };
  drawKept();
}

// ---------- tempo ----------
async function pageTempo(main, bpmArg) {
  const views = S.libs.libraries.filter(l => l.bpm);
  if (!views.length) {
    main.innerHTML = `<div class="page-head"><div><h2>Tempo view</h2></div></div>` + empty("No tempo view scanned yet",
      "A tempo view hears your catalogue stretched to the tempo you play at, with key lock, and shows which genres, colours and hype change.",
      toSetup("Scan a tempo view"));
    return;
  }
  const bpm = +(bpmArg || views[0].bpm), me = S.routeId;
  let d;
  try { d = await api("tempo/report", { bpm, limit: 25 }); }
  catch (e) { if (me === S.routeId) main.innerHTML = `<div class="page-head"><div><h2>Tempo view</h2></div></div>${errBox(e)}`; return; }
  if (me !== S.routeId) return;  // another tab, or another view picked, meanwhile
  const brief = rows => table([{ h: "Track", f: r => esc(r.track) }, { h: "BPM", r: 1, f: r => fmt(r.bpm) }, { h: "Genres", f: r => esc(r.genres) },
    { h: "Colour", f: r => esc(r.colour || "–") }, { h: "Hype", f: r => esc(r.hype) }], rows);
  const toBars = o => Object.entries(o).map(([k, v]) => ({ label: k, value: v }));
  const max = Math.max(1, ...Object.values(d.genres_native), ...Object.values(d.genres_at_bpm));  // 1: a view with no genres or colours yet
  const cmax = Math.max(1, ...Object.values(d.colours_native), ...Object.values(d.colours_at_bpm));
  const ordered = o => COLOURS.filter(c => o[c]).map(c => ({ label: c, value: o[c] }));
  main.innerHTML = `
    <div class="page-head"><div><h2>Tempo view: ${d.bpm} BPM</h2><div class="muted">Your catalogue as heard stretched to ${d.bpm} BPM with key lock, compared with each track at its own tempo.</div></div>
      <div class="spacer"></div>${views.length > 1 ? `<select onchange="location.hash='tempo/'+this.value">${views.map(v => `<option value="${v.bpm}" ${v.bpm === bpm ? "selected" : ""}>${v.bpm} BPM</option>`).join("")}</select>` : ""}
      ${SNAP ? "" : S.libs.loaded_view ? `<button id="tv-native">Switch ${esc(S.libs.loaded_app || DJ())} back to normal…</button>` : `<button id="tv-load" data-tip="${DJ()}'s genres, colours and hype as heard at ${d.bpm} BPM (shows what changes first; switch back any time)">Load into ${DJ()}…</button>`}</div>
    <div class="tiles">
      <div class="tile"><div class="v">${d.tracks}</div><div class="l">tracks compared</div></div>
      <div class="tile"><div class="v">${d.tempo_change.median_pct > 0 ? "+" : ""}${d.tempo_change.median_pct}%</div><div class="l">median tempo change (${d.tempo_change.min_pct > 0 ? "+" : ""}${d.tempo_change.min_pct}% … ${d.tempo_change.max_pct > 0 ? "+" : ""}${d.tempo_change.max_pct}%)</div></div>
      <div class="tile"><div class="v">${d.genre_changed}</div><div class="l">top genre changed</div></div>
      <div class="tile"><div class="v">${d.colour_changed} <span class="dim" style="font-size:14px">/ ${d.colours_coloured_tracks}</span></div><div class="l">colour changed</div></div>
      <div class="tile"><div class="v">${d.colour_mean_shift > 0 ? "+" : ""}${fmt(d.colour_mean_shift)}</div><div class="l">mean colour shift (+ = prettier)</div></div>
    </div>
    <div class="grid two">
      <div class="card"><h3>Genres at own tempo</h3>${bars(toBars(d.genres_native), { max })}</div>
      <div class="card"><h3>Genres at ${d.bpm} BPM</h3>${bars(toBars(d.genres_at_bpm), { max })}</div>
    </div>
    <div class="grid two">
      <div class="card"><h3>Most common genre changes</h3>${table([{ h: "From", f: f => esc(f.from) }, { h: "", f: () => "→" }, { h: "To", f: f => esc(f.to) }, { h: "Tracks", r: 1, f: f => f.tracks }], d.genre_flips)}</div>
      <div class="card"><h3>Your colours at ${d.bpm} BPM</h3>
        ${table([{ h: "Yours", f: ([n]) => colour(n) }, { h: "Tracks", r: 1, f: ([, v]) => v.tracks }, { h: "Mean shift", r: 1, f: ([, v]) => (v.mean_shift > 0 ? "+" : "") + v.mean_shift },
          { h: `Now`, f: ([, v]) => `<div class="stack">${COLOURS.filter(c => v.now[c]).map(c => `<div class="sw-${c}" style="flex:${v.now[c]}" data-tip="${c}: ${v.now[c]}"></div>`).join("")}</div>` }],
          COLOURS.filter(c => d.by_your_colour[c]).map(c => [c, d.by_your_colour[c]]))}
        <div class="grid" style="margin-top:12px;grid-template-columns:1fr 1fr;gap:12px"><div><div class="small muted">own tempo</div>${bars(ordered(d.colours_native), { max: cmax, colourLabels: true })}</div>
        <div><div class="small muted">at ${d.bpm} BPM</div>${bars(ordered(d.colours_at_bpm), { max: cmax, colourLabels: true })}</div></div></div>
    </div>
    <div class="card"><h3>Turn hardest at ${d.bpm} BPM</h3>${brief(d.hardest_turns)}</div>
    <div class="card"><h3>Stay prettiest / turn prettier</h3>${brief(d.prettiest_turns)}</div>
    <div class="grid two"><div class="card"><h3>Most hype at ${d.bpm} BPM</h3>${brief(d.most_hype)}</div><div class="card"><h3>Biggest hype risers</h3>${brief(d.hype_risers)}</div></div>`;
  const lexicon = () => (S.libs.status?.loaded_in_lexicon || "").includes("BPM");
  $("#tv-load")?.addEventListener("click", () => writeDialog("tempo_load", { bpm: d.bpm, lexicon: SF.lexicon === true }));  // Lexicon too: the Setup tab's tick box
  $("#tv-native")?.addEventListener("click", () => writeDialog("tempo_native", { lexicon: lexicon() }));
}

