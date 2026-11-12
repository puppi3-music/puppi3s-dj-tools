// ---------- jobs: the CLI's own commands, run by the server one at a time (jobs.py) ----------
const JOB = { job: null, lines: [], since: 0, timer: 0, listeners: new Set(), waiters: [], seenEnd: undefined, flash: null, hidden: null,
  endedId: undefined, failed: [] };  // endedId: the last job seen in job.ended; failed: queued mixes that failed meanwhile
async function jobPoll() {
  clearTimeout(JOB.timer);
  let d;
  try { d = await api("jobs", { since: JOB.since }); } catch (e) { JOB.timer = setTimeout(jobPoll, 3000); return; }
  const j = d.job;
  // jobs that ended while the page didn't look (a queued mix starts at once): end them as if seen; the first poll replays none
  for (const e of j?.ended || []) {
    if (JOB.endedId !== undefined && e.id > JOB.endedId && e.id !== j.id && JOB.seenEnd !== e.id) {
      JOB.seenEnd = e.id;
      if (e.error && e.error !== "Stopped.") JOB.failed.push(e);
      JOB.waiters = JOB.waiters.filter(w => w.id !== e.id || (w.ok(e), false));
      jobEnded(e);
    }
  }
  if (j) JOB.endedId = Math.max(JOB.endedId ?? 0, ...(j.ended || []).map(e => e.id));
  if (j && JOB.job && JOB.job.id !== j.id && JOB.since) { JOB.since = 0; JOB.job = null; return jobPoll(); }  // a newer job: its log from the start
  if (j) {
    if (!JOB.job || JOB.job.id !== j.id) JOB.lines = [];
    JOB.lines.push(...j.lines);
    if (JOB.lines.length > 3000) JOB.lines.splice(0, JOB.lines.length - 3000);
    JOB.since = j.line_to; JOB.job = j;
    if (!j.running) {
      if (JOB.seenEnd !== j.id) {
        if (JOB.seenEnd !== undefined) jobEnded(j);
        if (j.error && j.error !== "Stopped." && j.queued?.length) JOB.failed.push(j);  // the next one's bar replaces this
        JOB.seenEnd = j.id;
      }
      JOB.waiters = JOB.waiters.filter(w => w.id !== j.id || (w.ok(j), false));
    }
  }
  JOB.listeners.forEach(f => f());
  jobBar();
  if (j && (j.running || j.queued?.length)) JOB.timer = setTimeout(jobPoll, 1000);
}
// Start a job; resolves with the finished job (its result, or its error message).
async function startJob(action, options = {}, dry_run = false) {
  const d = await postJSON("/api/jobs/start", { action, options, dry_run });
  if (JOB.seenEnd === undefined) JOB.seenEnd = null;
  JOB.flash = null; JOB.failed = [];
  const done = new Promise(ok => { JOB.waiters.push({ id: d.job.id, ok }); });
  await jobPoll();
  rerenderSetup();  // its buttons wait while a job runs
  return done;
}
const jobRunning = () => !!JOB.job?.running;
async function jobEnded(j) {
  S.tracks = {}; SETUP = null; LOGS = null; G_TRAKTOR = null;  // a sync or write changes the playlists and genres to learn from
  if (!j.dry_run) JOB.flash = j;
  try { await loadLibs(); } catch (e) {}
  await refreshSetupNote();
  rerenderSetup();
  S.refreshRetrain?.();  // a write or a training changes what there is to learn
}
// Draw the Setup page again in place (no "Loading…", so it doesn't jump).
function rerenderSetup() { if (S.page === "setup") pageSetup($("#main")).catch(e => { $("#main").innerHTML = errBox(e); }); }
function progressBar(j, width) {
  const p = j.progress;
  return p && p.total ? `<div class="progress" style="${width ? `width:${width}` : ""}"><div style="width:${Math.round(p.done / p.total * 100)}%"></div></div>`
    // the bar is redrawn on every poll: start the sweep where the clock says it is, so it doesn't restart
    : `<div class="progress indet" style="${width ? `width:${width}` : ""}"><div style="animation-delay:-${Math.round(performance.now() % 1400)}ms"></div></div>`;
}
const progressText = j => {
  const p = j.progress;
  if (p && p.total && !p.steps) return `${p.done} of ${p.total}${p.eta ? ` · about ${esc(p.eta)} left` : ""}${p.what ? ` · ${esc(p.what)}` : ""}`;
  const stage = j.stage ? j.stage[0].toUpperCase() + j.stage.slice(1) : "";
  return `${p && p.total ? `Step ${p.done + 1} of ${p.total}: ` : ""}${esc(stage)}${stage ? " · " : ""}${mmss(j.elapsed)} so far`;
};
// Queued mixes that failed before this one (their own bar was never shown): how many, and why on hover
const failedNote = () => JOB.failed.length ? `<span class="err" data-tip="${esc(JOB.failed.map(e => `${e.label}: ${e.error}`).join("\n"))}">${JOB.failed.length} before this failed</span>` : "";
// The job's latest log line, when the progress text doesn't already say it (a stage, a counted step's "what")
const jobLast = j => {
  const raw = (j.last || "").trim();
  if (/^\[\d+\/\d+/.test(raw)) return "";  // the progress line itself: progressText says it
  const l = raw.replace(/^\[[^\]]*\]\s*/, "");
  return j.stage && l.startsWith(j.stage) ? "" : l;
};
function jobBar() {
  const bar = $("#jobbar"), j = JOB.job;
  if (SNAP || !j) { bar.hidden = true; return; }
  if (j.running) {
    bar.className = "jobbar"; bar.hidden = false;
    bar.innerHTML = `<b>${esc(j.label)}${j.dry_run ? " (preview)" : ""}</b>${j.queued?.length ? `<span class="muted" data-tip="${esc(["Then:", ...j.queued].join("\n"))}">${j.queued.length} more to go</span>` : ""}${failedNote()}${progressBar(j)}<span class="muted jobnow" data-tip="${esc(j.live?.trim() || jobLast(j) || "")}">${progressText(j)}${j.live ? ` · ${esc(j.live.trim())}` : jobLast(j) ? ` · ${esc(jobLast(j))}` : ""}</span>
      ${S.page !== "setup" ? `<a href="#setup">Show</a>` : ""}<button id="jb-stop" ${j.stopping ? "disabled" : ""}>${j.stopping ? "Stopping…" : j.queued?.length ? "Stop all" : "Stop"}</button>`;
    $("#jb-stop").onclick = () => postJSON("/api/jobs/stop", {}).then(jobPoll, jobPoll);  // ended meanwhile: the bar catches up
  } else if (JOB.flash && JOB.flash.id === j.id) {
    bar.className = `jobbar ${j.error ? "bad" : "good"}`; bar.hidden = false;
    bar.innerHTML = `<span>${j.error === "Stopped." ? `<b>${esc(j.label)}</b> stopped. It carries on where it stopped next time.` : j.error ? `<b>${esc(j.label)}</b> stopped: ${esc(j.error)}` : `<b>✓ ${esc(j.label)}</b> finished`}</span>${failedNote()}
      ${S.page !== "setup" ? `<a href="#setup">Details</a>` : ""}<button id="jb-x">OK</button>`;
    $("#jb-x").onclick = () => { JOB.flash = null; JOB.failed = []; jobBar(); };
  } else bar.hidden = true;
}

// What a writer reported, readably: the counts, notes, a few examples, and everything else folded away.
const HIDE = new Set(["dry_run", "app", "undone", "changed", "collection", "examples", "changes", "smartlists", "backup", "undo", "fields", "note", "warning",
  "now_loaded", "clock", "smartlist_folder", "kept", "restored_from", "restart", "after_steps", "moved_to", "moved", "not_moved"]);
const LABELS = { would_write: "files that would get a new tag", written: "files tagged", would_restore: "files that would get their old tags back",
  restored: "files given their old tags back", skipped_changed_since_scan: "skipped: changed on disk since the scan (scan again first)",
  unchanged: "already right", missing: "files no longer there", get traktor_tracks() { return `tracks in ${DJ()}`; }, matched_scanned_tracks: "of them scanned",
  new_files: "new files", changed_files: "files changed", removed_files: "files removed", commits: "new commits",
  files_put_back: "files put back", files_removed: "files taken out" };
const human = k => LABELS[k] || k.replace(/_/g, " ");
function exampleText(x) {
  return typeof x === "string" ? x : Object.entries(x).map(([k, v]) => k === "file" ? v : `${k}: ${v}`).join(" · ");
}
function resultSummary(r, depth = 0) {
  if (!r || typeof r !== "object") return r ? `<div>${esc(r)}</div>` : "";
  const nums = Object.entries(r).filter(([k, v]) => typeof v === "number" && v && !HIDE.has(k));
  const nested = Object.entries(r).filter(([k, v]) => v && typeof v === "object" && !Array.isArray(v) && !HIDE.has(k));
  const ex = r.examples || r.changes || [];
  return `${nums.length ? `<ul style="max-height:none">${nums.map(([k, v]) => `<li>${esc(human(k))}: <b>${v}</b></li>`).join("")}</ul>` : ""}
    ${r.warning ? `<div class="warnbox">${esc(forPage(r.warning))}</div>` : ""}
    ${[r.skipped && `Skipped: ${r.skipped}`, r.undone && `Put back: ${r.undone.join(", ")}`, r.note, r.clock && `Clock: ${r.clock}`, r.now_loaded && `Now loaded: ${r.now_loaded}`, r.smartlist_folder && `Smartlist folder: ${r.smartlist_folder}`]
      .filter(Boolean).map(t => `<div class="small">${esc(t)}</div>`).join("")}
    ${Array.isArray(r.smartlists) && r.smartlists.length ? `<div class="small muted">${r.smartlists.length} Smartlists: ${esc(r.smartlists.join(", "))}</div>` : ""}
    ${ex.length ? `<div class="small" style="margin-top:6px"><b>For example</b></div><ul>${ex.slice(0, 15).map(x => `<li>${esc(exampleText(x))}</li>`).join("")}</ul>` : ""}
    ${depth ? "" : nested.map(([k, v]) => `<div class="small" style="margin-top:8px"><b>${esc(human(k))}</b></div>${resultSummary(v, 1)}`).join("")}
    ${depth ? "" : `<details style="margin-top:8px"><summary class="small muted" style="cursor:pointer">Everything it reported</summary><div class="tree">${tree(r)}</div></details>`}`;
}
// The sync's preview: each step that would change something, ticked, to choose what is written; the rest listed after
const SYNC_STEPS = { tracks: "Tracks: relink moved files, add analysed tracks it doesn't have", genres: "Genres and colours", hype: "Hype", cues: "Approved cues", grids: "Grid fixes", crates: "Crates", samples: "Loops saved on this page" };
const COPY_STEPS = { tracks: "Tracks the other DJ app has and this one doesn't", genres: "Genres (the other DJ app's, where it has one)", colours: "Colours (the other DJ app's, where it has one)", cues: "Hot cues from the other DJ app (free slots only)", grids: "Beat grids from Traktor (replaces Rekordbox's; new, check a few tracks first)", playlists: "Playlists, in a \"From the other DJ app\" folder", samples: "Samples from the other DJ app (its sampler or loops folder)" };
function syncSteps(r, st, copy) {
  const other = st?.dj_app?.app === "rekordbox" ? "Traktor" : "Rekordbox";
  const name = s => ((copy ? COPY_STEPS : SYNC_STEPS)[s] || s).replaceAll("the other DJ app", other);
  const steps = Object.entries(r?.steps || {}), todo = steps.filter(([, v]) => v.changed), rest = steps.filter(([, v]) => !v.changed);
  return `<div class="small muted" style="margin-top:6px">Tick what to write into ${esc(r.app || "your DJ app")}:</div>
    ${todo.map(([s, v]) => `<div style="margin-top:6px"><label class="inline"><input type="checkbox" data-step="${esc(s)}" ${copy && s === "grids" ? "" : "checked"}> <b>${esc(name(s))}</b></label>
      <details><summary class="small muted" style="cursor:pointer">What changes</summary>${resultSummary(v, 1)}</details></div>`).join("")}
    ${rest.length ? `<div class="small muted" style="margin-top:8px">Nothing to write for:</div><ul class="small muted">${rest.map(([s, v]) => `<li><b>${esc(name(s))}</b>: ${typeof v.skipped === "string" && v.skipped ? esc(v.skipped) : "already in place"}</li>`).join("")}</ul>` : ""}`;
}
function nothingToDo(r) {  // as the CLI's say_if_nothing
  if (!r) return false;
  const parts = "changed" in r ? [r] : Object.values(r).filter(v => v && typeof v === "object" && !Array.isArray(v));
  const flags = parts.filter(d => "changed" in d).map(d => d.changed);
  const counts = ["written", "would_write", "restored", "would_restore", "top_genre_changed"].filter(k => k in r).map(k => r[k]);
  return (flags.length && !flags.some(Boolean)) || (!flags.length && counts.length && !counts.some(Boolean));
}
const TOUCHES_TRAKTOR = new Set(["sync", "sync_undo", "copy", "copy_undo", "traktor", "traktor_undo", "hype_traktor", "hype_remove", "colors_apply", "colors_recolour", "colors_write_marked", "tempo_load", "tempo_native"]);
const NO_PREVIEW = new Set(["traktor_undo", "sync_undo", "copy_undo"]);
const MULTI = new Set(["sync", "copy"]);  // several steps: the preview ticks which to write
const ONE_UNDO = { sync_undo: "last sync (all of its steps at once)", copy_undo: "last copy from the other DJ app (all of its steps at once)" };
const OPEN_WARN = `<div class="warnbox"><b>Your DJ app is open.</b> Quit it first: it saves its library when it quits and would undo these changes. Then press Check again.</div>`;

// Preview → write → undo for one of the CLI's writers, in a dialog.
async function writeDialog(action, options = {}, onDone) {
  const st = await getSetup().catch(() => null), label = a => st?.actions?.[a] || a;
  const dlg = document.createElement("dialog"); dlg.className = "write"; document.body.appendChild(dlg);
  dlg.addEventListener("close", () => { dlg.remove(); JOB.listeners.delete(live); });
  let title = label(action);
  const show = (html, buttons = "") => {
    dlg.innerHTML = `<h3>${esc(title)}</h3>${html}<div class="filters" style="margin-top:12px">${buttons}<button data-close>Close</button><span class="small muted" id="wd-msg"></span></div>`;
    dlg.querySelector("[data-close]").onclick = () => dlg.close();
  };
  const live = () => { const el = dlg.querySelector("#wd-live"), j = JOB.job; if (el && j?.running) el.textContent = j.live || j.lines.at(-1) || ""; };
  JOB.listeners.add(live);
  const busy = text => show(`<div class="muted">${esc(text)}</div><div class="mono small dim" id="wd-live" style="margin-top:6px;white-space:pre-wrap"></div>`);
  async function run(act, opts, dry) {
    try { return await startJob(act, opts, dry); } catch (e) { return { error: e.message, command: "" }; }
  }
  async function preview(act, opts) {
    title = label(act);
    if (NO_PREVIEW.has(act)) {
      const rb = st?.dj_app?.app === "rekordbox", app = rb ? "Rekordbox" : "Traktor";
      show(`<div>Puts ${rb ? "master.db" : "collection.nml"} back to how it was before the analyzer's ${ONE_UNDO[act] || "last change"}${!ONE_UNDO[act] && st?.traktor_backup ? ` (backup <span class="mono">${esc(st.traktor_backup)}</span>)` : ""}.
        It refuses if ${app} saved ${rb ? "its library" : "the collection"} since then, so nothing of yours is lost. ${app} must be closed.</div>`,
        `<button class="primary" data-go>Undo it</button>`);
      dlg.querySelector("[data-go]").onclick = () => write(act, opts);
      return;
    }
    busy("Checking what would change…");
    const j = await run(act, opts, true);
    if (j.error) return show(`<div class="err">${esc(forPage(j.error))}</div>`, `<button data-again>Check again</button>`), again(act, opts);
    const open = j.traktor_running && TOUCHES_TRAKTOR.has(act), nothing = nothingToDo(j.result);
    show(`${nothing ? `<div><b>Nothing would change:</b> everything is already up to date.</div>` : `<div class="small muted">Preview, nothing written yet:</div>`}
      ${MULTI.has(act) && !nothing ? syncSteps(j.result, st, act === "copy") : resultSummary(j.result)}${open && !nothing ? OPEN_WARN : ""}
      ${!nothing && j.result?.from && j.result?.to ? `<div><b>${act === "update_undo" ? "Back to" : "From"} version ${esc(j.result.from)} to ${esc(j.result.to)}.</b></div>` : ""}
      ${nothing ? "" : `<div class="note">${TOUCHES_TRAKTOR.has(act) ? `A backup of ${(act === "tempo_native" && st?.tempo?.loaded_app ? st.tempo.loaded_app === "Rekordbox" : st?.dj_app?.app === "rekordbox") ? "Rekordbox's library" : "Traktor's collection"} is made first, and you can undo it here afterwards.`
        : act === "update" ? "Your data folder (scans, models, training, settings) isn't touched. The app's own files are backed up first, so you can go back to this version. The page then starts again with the new version."
        : act === "update_undo" ? "The page then starts again with that version." : "The files' original tags are saved first, so you can put them back."}</div>`}`,
      nothing ? "" : open ? `<button class="primary" data-again>Check again</button>` : `<button class="primary" data-go>Write it</button>`);
    again(act, opts);
    const go = dlg.querySelector("[data-go]");
    go?.addEventListener("click", () => {
      if (!MULTI.has(act)) return write(act, opts);
      const only = [...dlg.querySelectorAll("[data-step]:checked")].map(b => b.dataset.step);
      if (only.length) write(act, { ...opts, only });  // never an empty list: the server reads no --only as every step
    });
    // sync: write only the ticked steps; nothing ticked, nothing to write (also at first: a copy's grids start unticked)
    const ticked = () => { if (go && MULTI.has(act)) go.disabled = !dlg.querySelector("[data-step]:checked"); };
    dlg.querySelectorAll("[data-step]").forEach(b => b.onchange = ticked);
    ticked();
  }
  function again(act, opts) { dlg.querySelector("[data-again]")?.addEventListener("click", () => preview(act, opts)); }
  async function write(act, opts) {
    busy("Writing…");
    const j = await run(act, opts, false);
    // An undo refused because Traktor saved the collection since: offer to restore anyway, losing what it saved
    const forceable = (act === "traktor_undo" || act in ONE_UNDO) && !opts.force && /--force/.test(j.error || "");
    if (j.error) {
      show(`<div class="err">${esc(forPage(j.error))}</div>`,
        `${forceable ? `<button data-force>Undo anyway</button>` : ""}<button data-again>Try again</button>`);
      dlg.querySelector("[data-force]")?.addEventListener("click", () => {
        if (confirm(`Put the backup back anyway?\n\nEverything ${DJ()} saved since the analyzer's change is lost: plays, cue points, new tracks, your own edits.`)) write(act, { ...opts, force: true });
      });
      return again(act, opts);
    }
    const r = j.result || {}, undo = act === "colors_write_marked" && !r.undo ? null : j.undo;
    if (r.restart) return restartApp((html, buttons) => show(html, buttons), r);
    show(`<div style="color:var(--good)"><b>✓ Done.</b></div>${resultSummary(r)}
      ${r.backup ? `<div class="note">Backup: <span class="mono">${esc(r.backup)}</span>${undo === "traktor_undo" || undo in ONE_UNDO ? (DJ() === "Rekordbox" ? "<br>Undo works until Rekordbox changes master.db again." : "<br>Undo works until Traktor saves its collection again (it does when it quits).") : ""}</div>` : ""}
      ${r.restored_from ? `<div class="note">Restored from <span class="mono">${esc(r.restored_from)}</span>.</div>` : ""}`,
      undo ? `<button data-undo>${esc(label(undo))}…</button>` : "");
    dlg.querySelector("[data-undo]")?.addEventListener("click", () => preview(undo, opts));
    onDone?.(j);
  }
  dlg.showModal();
  preview(action, options);
}

// ---------- updating the app (update.py) ----------
let UPD = null;
async function getUpdate(force = false, auto = false) { UPD = await api("update", { force: force ? 1 : "", auto: auto ? 1 : "" }); return UPD; }
// Release notes are a little Markdown: bold, code, "- " lists, "## " headings
function notesHtml(md) {
  const inline = t => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, '<span class="mono">$1</span>');
  let out = "", list = false;
  for (const raw of md.split(/\r?\n/)) {
    const line = raw.trimEnd(), li = line.match(/^\s*[-*] (.*)/);
    if (li) { if (!list) { out += "<ul>"; list = true; } out += `<li>${inline(li[1])}</li>`; continue; }
    if (list && /^\s+\S/.test(line)) { out = out.replace(/<\/li>$/, ` ${inline(line.trim())}</li>`); continue; }  // a list item's next line
    if (list) { out += "</ul>"; list = false; }
    if (/^#+ /.test(line)) out += `<div class="small" style="margin-top:8px"><b>${inline(line.replace(/^#+ /, ""))}</b></div>`;
    else if (line.trim()) out += `<div class="small">${inline(line)}</div>`;
  }
  return out + (list ? "</ul>" : "");
}
// After an update (or going back): the server stops and its launcher starts the new version; reload when it answers again
async function restartApp(show, r = {}) {
  const st = UPD || await getUpdate(false, true).catch(() => null);
  const to = r.to && r.to !== "main" ? ` ${esc(r.to)}` : "";
  const steps = (r.after_steps || []).length ? `<div class="note">After it starts, the Setup tab offers what this update needs: ${esc(r.after_steps.join("; "))}.</div>` : "";
  const manual = why => show(`<div style="color:var(--good)"><b>✓ Version${to} is installed.</b></div><div style="margin-top:8px">${esc(why)}</div>${steps}`);
  if (st && !st.restart) return manual(`To start it, close the analyzer's window and double-click ${LAUNCHER} again.`);
  show(`<div style="color:var(--good)"><b>✓ Version${to} is installed.</b></div><div class="muted" style="margin-top:8px">Starting the page again with it: installing its packages first, which can take a minute…</div>${steps}`);
  try { await postJSON("/api/restart", {}); } catch (e) { return manual(e.message); }
  let gone = false;
  for (const t0 = Date.now(); Date.now() - t0 < 5 * 60e3;) {
    await new Promise(ok => setTimeout(ok, 1000));
    try { const res = await fetch("/api/libraries", { cache: "no-store" }); if (res.ok && gone) { location.hash = "setup"; location.reload(); return; } gone ||= !res.ok; }
    catch (e) { gone = true; }
  }
  manual(`It's taking a while. Look at the analyzer's window for what it's doing; if it stopped, double-click ${LAUNCHER} again.`);
}
// What each step after an update runs (update.STEPS' keys)
const AFTER_JOBS = { models: ["setup", {}], regenre: ["regenre", {}], rescan: ["scan", { traktor: true, rescan: true }], train: ["colors_train", { views: true }] };
async function updateCard(force = false) {
  const box = $("#updatecard");
  if (!box || SNAP) return;
  let u;
  try { u = await getUpdate(force, !force); } catch (e) { box.innerHTML = `<div class="card"><div class="err">${esc(e.message)}</div></div>`; return; }
  if (!box.isConnected) return;
  const b = jobRunning() ? "disabled" : "";
  const P = u.pending || { steps: [] };
  const pending = P.steps.length ? `<div class="warnbox" style="margin-top:10px"><b>After updating to version ${esc(P.version)}</b> (from ${esc(P.since)}), do these once:
      <div class="steps" style="margin-top:6px">${P.steps.map(x => `<div class="step todo"><div class="ic">→</div><div><div class="st">${esc(x.label)}</div></div>
        <div class="act"><button class="primary" data-after="${esc(x.key)}" ${b}>Run</button></div></div>`).join("")}</div>
      <div class="filters" style="margin-top:6px"><button id="up-seen">Done, hide this</button></div></div>` : "";
  const notes = (u.notes || []).map(n => `<details ${u.notes.length === 1 ? "open" : ""} style="margin-top:6px"><summary style="cursor:pointer"><b>What's new in ${esc(n.version)}</b></summary><div style="margin:4px 0 0 4px">${notesHtml(n.notes)}</div></details>`).join("");
  const kind = u.kind === "git" ? " (a git clone: updates with git pull)" : u.kind === "container" ? " (Docker)"
    : u.commit ? ` (dev build ${u.commit.slice(0, 7)})` : "";
  // Stable or Dev: only a release-zip install chooses (a clone follows main anyway, Docker is rebuilt)
  const dev = u.channel === "dev";
  const chan = u.kind !== "zip" ? "" : `<div class="filters" style="margin-top:8px"><span class="small">Updates:</span>
      <div class="seg-toggle"><button data-chan="stable" class="${dev ? "" : "on"}">Stable</button><button data-chan="dev" class="${dev ? "on" : ""}">Dev</button></div>
      <span class="small muted">${dev ? "Dev: the newest work in progress, rebuilt after every change. New things arrive first, but may have bugs. Go back to Stable any time: you stay on this build until the next release is out, nothing is undone."
        : "Stable: only finished releases. Choose Dev to try new things before they're released (they may have bugs)."}</span></div>`;
  const state = u.error ? `<span class="err">${esc(u.error)}</span>`
    : u.newer && u.target?.dev ? `<b>A newer dev build is out: ${esc(u.latest)}</b> <span class="muted">(based on ${esc(u.target.version)}${u.published ? `, ${esc(u.published)}` : ""})</span>.`
    : u.newer ? `<b>Version ${esc(u.latest)} is out</b>${u.published ? ` <span class="muted">(${esc(u.published)})</span>` : ""}.`
    : u.latest ? `Up to date${u.checked ? ` <span class="muted">(checked ${esc(u.checked)})</span>` : ""}.`
    : !u.auto ? `<span class="muted">Checking for updates when the page opens is off.</span>` : "";
  box.innerHTML = `<div class="card${u.newer ? " hl" : ""}"><div class="filters" style="margin-bottom:4px"><h3 style="margin:0">Puppi3's Track Analyzer ${esc(u.installed)}<span class="dim small">${esc(kind)}</span></h3><div class="spacer"></div>
      <label class="inline small" data-tip="Asks GitHub for the newest release at most every 6 hours; nothing else is sent"><input type="checkbox" id="up-auto" ${u.auto ? "checked" : ""}> check when the page opens</label>
      <button id="up-check">Check now</button></div>
    <div>${state}</div>${chan}
    ${u.newer && u.can_update ? `<div class="filters" style="margin-top:8px"><button class="primary" id="up-go" ${b}>Update now…</button><span class="small muted">Shows what changes first. Your scans, models and training stay.</span></div>` : ""}
    ${u.newer && !u.can_update && u.why_not ? `<div class="note">${esc(u.why_not)} <a href="${esc(u.url || u.page)}" target="_blank" rel="noopener">The release on GitHub</a></div>` : ""}
    ${notes}${pending}
    ${u.previous ? `<div class="small muted" style="margin-top:8px">Updated from ${esc(u.previous)}. <a href="#" id="up-back">Go back to ${esc(u.previous)}…</a></div>` : ""}</div>`;
  const on = (id, fn) => { const el = $("#" + id); if (el) el.onclick = fn; };
  on("up-check", async e => { e.currentTarget.disabled = true; e.currentTarget.textContent = "Checking…"; await updateCard(true); updateDot(); });
  on("up-go", () => writeDialog("update"));
  on("up-back", e => { e.preventDefault(); writeDialog("update_undo"); });
  const saved = async (body, el) => {  // a setting that didn't save says so instead of looking saved
    try { await postJSON("/api/update/settings", body); await updateCard(); updateDot(); }
    catch (err) { if (el.type === "checkbox") el.checked = !el.checked; el.closest(".card").insertAdjacentHTML("beforeend", `<div class="err small">${esc(err.message)}</div>`); }
  };
  on("up-seen", e => saved({ seen: true }, e.currentTarget));
  box.querySelectorAll("[data-chan]").forEach(el => el.onclick = () => {
    if (el.classList.contains("on")) return;
    el.parentElement.querySelectorAll("button").forEach(x => x.disabled = true);
    saved({ channel: el.dataset.chan }, el);
  });
  $("#up-auto").onchange = e => saved({ auto: e.target.checked }, e.target);
  box.querySelectorAll("[data-after]").forEach(el => el.onclick = () => {
    const [action, opts] = AFTER_JOBS[el.dataset.after];
    startJob(action, opts).catch(err => { const p = $("#jobpanel"); if (p) p.innerHTML = `<div class="err" style="margin-bottom:16px">${esc(err.message)}</div>`; });
  });
}
// The Setup link's dot: an update is out, or steps after one are left
function updateDot() {
  const link = document.querySelector('#nav a[href="#setup"]'), u = UPD;
  if (!link || !u) return;
  link.querySelector(".dot")?.remove();  // drawn again from the latest check: gone once the update or its steps are done
  const why = u.newer ? (u.target?.dev ? `Dev build ${u.latest.replace("dev ", "")} is out` : `Version ${u.latest} is out`) : u.pending?.steps?.length ? "Steps left after the update" : "";
  if (why) link.insertAdjacentHTML("beforeend", `<span class="dot" data-tip="${esc(why)}"></span>`);
}

