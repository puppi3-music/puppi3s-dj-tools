// ---------- deck: play the track and jump around like on a Traktor deck ----------
const DECK = { audio: new Audio(), path: null, c: null, box: null, loop: null, raf: 0, loaded: false,
  ctx: null, buf: null, bufPath: null, src: null, wa: null };  // Web Audio, for gapless loops
DECK.audio.preload = "none";
function deckViewBpm() {  // the tempo view the player can stretch to: the one shown, else the first scanned
  const views = (S.libs?.libraries || []).filter(l => l.bpm);
  return (S.lib !== "native" && views.find(l => l.id === S.lib)?.bpm) || views[0]?.bpm || null;
}
const deckBpm = () => (S.deckStretch ?? S.lib !== "native") ? deckViewBpm() : null;  // on by default in a tempo view
const DECK_RATIOS = new Map(), DECK_PENDING = new Set();  // "path|bpm" -> stretched time / Traktor time, once the server has the stretched file
function deckRatio(bpm) {
  if (!bpm) return Promise.resolve(1);
  const key = `${DECK.path}|${bpm}`;
  if (!DECK_RATIOS.has(key)) {
    DECK_PENDING.add(key);  // deckUi shows the spinner while it's in here
    DECK_RATIOS.set(key, api("deck/stretch", { path: DECK.path, bpm }, { signal: prepFor(DECK.path) }).then(r => r.ratio)
      .catch(e => { DECK_RATIOS.delete(key); throw e; }).finally(() => { DECK_PENDING.delete(key); deckUi(); }));
    deckUi();
  }
  return DECK_RATIOS.get(key);
}
function deckPrefetch() {  // stretch as soon as a track opens, so it's usually ready by the first play
  const bpm = deckBpm();
  const path = DECK.path;
  if (bpm && path) deckRatio(bpm).then(() => { if (DECK.path === path && !DECK.loaded && !DECK.src && !DECK.preparing) deckMsg(`Ready at ${bpm} BPM`); },
    () => {});  // a failure shows when you press play
}
function deckVolume() {  // 0–1, remembered between visits
  if (DECK.vol === undefined) { let v = NaN; try { v = parseFloat(localStorage.getItem("ta-volume")); } catch (e) {} DECK.vol = v >= 0 && v <= 1 ? v : 1; }
  return DECK.vol;
}
function deckSetVolume(v) {
  DECK.vol = v; DECK.audio.volume = v;
  if (DECK.gain) DECK.gain.gain.value = v;
  try { localStorage.setItem("ta-volume", String(v)); } catch (e) {}
}
DECK.audio.volume = deckVolume();
function deckAttach(c, box) {
  if (DECK.path !== c.path) { deckStopLoop(); DECK.audio.pause(); DECK.audio.removeAttribute("src"); DECK.audio.load(); DECK.path = c.path; DECK.loop = null; DECK.loaded = false; DECK.preparing = false; DECK.buf = null; DECK.bufPath = null; DECK.ready = null;
    DECK.waiting = false; DECK.active = null; DECK.asked = (DECK.asked || 0) + 1; }  // a play still starting for the last track: dropped
  DECK.c = c; DECK.box = box;
  box.querySelector("#dk-play")?.addEventListener("click", deckToggle);
  box.querySelector("#dk-vol")?.addEventListener("input", e => deckSetVolume(+e.target.value / 100));
  box.querySelector("#dk-stretch")?.addEventListener("change", e => {  // carry on from the same beat at the other tempo
    S.deckStretch = e.target.checked; deckPrefetch();
    const playing = deckPlaying() || DECK.waiting, t = deckTime(), loop = DECK.loop, active = DECK.active;
    if (DECK.src) deckStopLoop(); else DECK.audio.pause();
    if (playing) { deckSeek(loop ? loop[0] : t, true, loop); DECK.active = active; }
    else if (DECK.loaded) deckSeek(t, false);
    deckUi();
  });
  if (box._deckBound) { deckUi(); return; }
  box._deckBound = true;
  box.addEventListener("click", e => {  // pads and ▶ buttons: jump there and play (a loop plays looped)
    const b = e.target.closest("[data-t]"); if (!b) return;
    e.preventDefault(); e.stopPropagation();
    const t = +b.dataset.t, len = b.dataset.loop ? +b.dataset.loop : 0, id = `${t}|${len}`;
    if (b.classList.contains("mini") && DECK.active === id && deckPlaying()) {  // its ⏸: stop
      if (DECK.src) deckSeek(deckStopLoop(), false); else DECK.audio.pause();
      DECK.active = null; deckUi(); return;
    }
    deckSeek(t, true, len ? [t, t + len] : null);
    DECK.active = id; deckUi();
  });
  deckUi();
}
function deckReady() {  // the file loads only once you play or jump: opening a track loads nothing. Resolves false if
  // it couldn't, or another track or tempo was asked for meanwhile
  const bpm = deckBpm(), path = DECK.path;
  if (!path) return Promise.resolve(false);
  if (DECK.ready && DECK.readyBpm === bpm) return DECK.ready;
  DECK.audio.pause(); DECK.audio.removeAttribute("src"); DECK.loaded = false; DECK.readyBpm = bpm;
  DECK.ready = deckRatio(bpm).then(k => {
    if (DECK.path !== path || DECK.readyBpm !== bpm) return false;
    DECK.k = k; DECK.bpm = bpm;  // Traktor time t is t × k in this file
    DECK.audio.preload = "auto"; DECK.audio.src = `/api/audio?path=${encodeURIComponent(path)}${bpm ? `&bpm=${bpm}` : ""}`; DECK.loaded = true;
    DECK.preparing = true; deckMsg("Preparing audio… (the first time for a track takes a few seconds)");
    return true;
  }, err => { if (DECK.path !== path) return false;  // another track meanwhile (its stretch was cancelled)
    if (DECK.readyBpm === bpm) DECK.ready = null;  // asks again on the next play
    if (!aborted(err)) deckMsg(`Couldn't stretch: ${forPage(err.message)}`); return false; });
  return DECK.ready;
}
function deckTime() {  // where playback is now, looping or not, in Traktor's time (the track's own tempo)
  if (DECK.src) { const w = DECK.wa, len = w.b - w.a; return w.a + (((DECK.ctx.currentTime - w.t0) / w.k) % len); }
  return (DECK.audio.currentTime || 0) / (DECK.loaded ? DECK.k : 1);
}
const deckPlaying = () => !!DECK.src || !DECK.audio.paused;
function deckStopLoop() {
  if (!DECK.src) return null;
  const t = deckTime();
  try { DECK.src.stop(); } catch (e) {}
  DECK.src.disconnect(); DECK.src = null; DECK.wa = null; DECK.loop = null;
  return t;
}
const LOOP_CLIPS = new Map();  // "path|start|length" -> decoded loop, the last few played
async function deckLoop(a, b) {
  // The <audio> element can only jump back once per screen refresh, which leaves a gap. Web Audio loops on the
  // exact sample: the server cuts just the loop out of the file (~0.15 s), and it plays over and over.
  DECK.audio.pause(); deckStopLoop();
  DECK.loop = [a, b]; const ask = DECK.asked = (DECK.asked || 0) + 1; DECK.waiting = true; deckUi();
  const smooth = smoothLoops(), want = DECK.path, bpm = deckBpm(), key = `${want}|${a}|${b}|${smooth}|${bpm}`;
  try {
    const k = await deckRatio(bpm);  // stretched loops are cut from the stretched track, so it's made first
    if (DECK.path !== want || DECK.asked !== ask) return;
    if (!DECK.ctx) { DECK.ctx = new AudioContext(); DECK.gain = DECK.ctx.createGain(); DECK.gain.gain.value = deckVolume(); DECK.gain.connect(DECK.ctx.destination); }
    if (DECK.ctx.state === "suspended") await DECK.ctx.resume();
    let buf = LOOP_CLIPS.get(key), whole = false;
    if (!buf) {
      try {
        const r = await fetch(`/api/loop?path=${encodeURIComponent(want)}&start=${a}&len=${b - a}${smooth ? "&smooth=1" : ""}${bpm ? `&bpm=${bpm}` : ""}`, { signal: prepFor(want) });
        if (!r.ok) throw new Error((await readJSON(r)).error);
        buf = await DECK.ctx.decodeAudioData(await r.arrayBuffer());
        LOOP_CLIPS.set(key, buf);
        if (LOOP_CLIPS.size > 12) LOOP_CLIPS.delete(LOOP_CLIPS.keys().next().value);
      } catch (e) {  // no ffmpeg or a cut that failed: decode the whole track instead (slower, same loop)
        if (aborted(e) || DECK.path !== want) throw e;
        deckMsg("Preparing a gapless loop…");
        if (DECK.bufPath !== `${want}|${bpm}`) {
          const res = await fetch(`/api/audio?path=${encodeURIComponent(want)}${bpm ? `&bpm=${bpm}` : ""}`, { signal: prepFor(want) });
          if (!res.ok) throw new Error((await readJSON(res)).error);  // the server's reason, not "unable to decode audio data"
          DECK.buf = await DECK.ctx.decodeAudioData(await res.arrayBuffer()); DECK.bufPath = `${want}|${bpm}`;
        }
        buf = DECK.buf; whole = true;
      }
    }
    if (DECK.path !== want || DECK.asked !== ask) return;  // another jump or track meanwhile
    const src = DECK.ctx.createBufferSource();
    src.buffer = buf; src.loop = true;
    if (whole) { src.loopStart = a * k; src.loopEnd = b * k; src.connect(DECK.gain); src.start(0, a * k); }
    else { src.connect(DECK.gain); src.start(0, 0); }  // the clip is exactly the loop
    DECK.src = src; DECK.wa = { t0: DECK.ctx.currentTime, a, b, k, bpm }; DECK.waiting = false;
    deckTick();
  } catch (err) { if (DECK.path !== want || DECK.asked !== ask) return;  // another track meanwhile: its deck isn't this loop's
    DECK.loop = null; DECK.waiting = false; if (!aborted(err)) deckMsg(`Couldn't loop: ${forPage(err.message)}`); deckUi(); }
}
function deckSeek(t, play = true, loop = null) {
  if (!DECK.path) return;
  DECK.active = null;
  if (loop) { deckLoop(loop[0], loop[1]); return; }
  deckStopLoop(); DECK.loop = null;
  const want = DECK.path, ask = DECK.asked = (DECK.asked || 0) + 1;  // a later jump or a cancel wins
  DECK.waiting = play && !(DECK.loaded && DECK.audio.readyState >= 3 && DECK.readyBpm === deckBpm()); deckUi();
  deckReady().then(ok => {
    if (DECK.asked !== ask || DECK.path !== want || DECK.src) return;  // a loop or another jump meanwhile
    if (!ok) { DECK.waiting = false; deckUi(); return; }
    const go = () => {
      if (DECK.asked !== ask) return;
      DECK.audio.currentTime = Math.max(0, t) * DECK.k;
      if (play) DECK.audio.play().catch(err => { DECK.waiting = false; deckMsg(err.message); deckUi(); });
      deckTick();
    };
    if (DECK.audio.readyState >= 1) go(); else DECK.audio.addEventListener("loadedmetadata", go, { once: true });
  });
}
function deckToggle() {
  if (!DECK.path) return;
  if (DECK.waiting) {  // pressed again before the sound started: cancel
    DECK.waiting = false; DECK.asked = (DECK.asked || 0) + 1; DECK.loop = null; DECK.active = null; DECK.audio.pause(); deckUi(); return;
  }
  if (DECK.src) { deckSeek(deckStopLoop(), false); deckUi(); return; }  // pause leaves the loop
  if (!DECK.audio.paused) { DECK.audio.pause(); return; }
  if (DECK.loaded && DECK.readyBpm === deckBpm()) {
    DECK.waiting = DECK.audio.readyState < 3; deckUi();
    DECK.audio.play().catch(err => { DECK.waiting = false; deckMsg(err.message); deckUi(); });
    return;
  }
  deckSeek(deckTime(), true);
}
const setText = (el, s) => { if (el.textContent !== s) el.textContent = s; };
function deckMsg(m) { const el = DECK.box?.querySelector("#dk-time"); if (el) el.textContent = m; }
function deckTick() {
  cancelAnimationFrame(DECK.raf);
  const step = () => { deckUi(); if (deckPlaying()) DECK.raf = requestAnimationFrame(step); };
  step();
}
DECK.audio.addEventListener("play", deckTick);
DECK.audio.addEventListener("playing", () => { DECK.waiting = false; deckUi(); });  // sound is coming out
DECK.audio.addEventListener("pause", () => deckUi());
DECK.audio.addEventListener("canplay", () => { if (DECK.preparing) { DECK.preparing = false; deckUi(); } });
DECK.audio.addEventListener("ended", () => { DECK.active = null; deckUi(); });
DECK.audio.addEventListener("error", () => { DECK.preparing = false; DECK.waiting = false; if (DECK.loaded) deckMsg("This browser can't play this file"); });
function deckUi() {
  const c = DECK.c, box = DECK.box;
  if (!c || !box || !box.isConnected) return;
  const t = deckTime();
  const btn = box.querySelector("#dk-play");
  if (btn) {  // pressed, no sound yet (stretching, loading): say so on the button itself
    const txt = DECK.waiting ? "Starting…" : deckPlaying() ? "⏸ Pause" : "▶ Play";
    if (btn.textContent !== txt) btn.textContent = txt;
    btn.classList.toggle("waiting", !!DECK.waiting);
    const tip = DECK.waiting ? "Getting the audio ready; it starts by itself. Click to cancel" : "";
    if (btn.dataset.tip !== tip) btn.dataset.tip = tip;  // this runs every frame while playing: write only what changed
  }
  const stretching = DECK_PENDING.has(`${DECK.path}|${deckBpm()}`), sw = box.querySelector("#dk-stretching");
  if (sw) sw.hidden = !stretching;
  const bi = Math.floor((t - c.first_bar_s + 1e-6) / c.bar_seconds), bl = barLabel(c, bi), bt = Math.floor((t - c.first_bar_s - bi * c.bar_seconds) / (c.bar_seconds / 4) + 1e-6) + 1;
  const tm = box.querySelector("#dk-time");
  if (tm && !DECK.preparing && (DECK.loaded || DECK.src || t > 0) && !(DECK.loop && !DECK.src)) setText(tm, `${clock(t)} · ${bl.n >= 1 ? `bar ${bl.n}, beat ${bt}` : "before bar 1"}${DECK.loop ? " · looping" : ""}${(DECK.src ? DECK.wa.bpm : DECK.loaded && DECK.bpm) ? ` · at ${DECK.src ? DECK.wa.bpm : DECK.bpm} BPM` : ""}`);
  box.querySelectorAll("button.mini[data-t]").forEach(b => {
    b.dataset.label ??= b.textContent;
    const on = deckPlaying() && DECK.active === `${+b.dataset.t}|${b.dataset.loop ? +b.dataset.loop : 0}`;
    const txt = on ? b.dataset.label.replace("▶", "⏸") : b.dataset.label;
    if (b.textContent !== txt) b.textContent = txt;
    b.classList.toggle("on", on);
  });
  box.querySelectorAll(".pads [data-loop]").forEach(b => b.classList.toggle("on", !!DECK.loop && Math.abs(+b.dataset.t - DECK.loop[0]) < 0.01));
  const w = box._wave, ph = box.querySelector("#ph"), pho = box.querySelector("#phov");
  if (!w || !ph) return;
  const show = (el, on) => { const d = on ? "block" : "none"; if (el.style.display !== d) el.style.display = d; };
  if (!DECK.loaded && !DECK.src && t === 0) { show(ph, false); show(pho, false); return; }
  const W = w.W(), span = w.st.v1 - w.st.v0;
  if (deckPlaying() && box.querySelector("#dk-follow")?.checked && w.zoomed() && (t < w.st.v0 || t > w.st.v0 + span * 0.85)) w.setView(t - span * 0.15, t + span * 0.85);
  const x = w.X(t);
  show(ph, x >= 0 && x <= W); ph.style.transform = `translateX(${x}px)`;  // a transform moves it without a layout
  show(pho, true); pho.style.transform = `translateX(${t / w.dur * W}px)`;
}
document.addEventListener("keydown", e => {
  if (!DECK.path || !$("#drawer").classList.contains("open") || DECK.path !== $("#drawer").dataset.path || e.target.closest("input, textarea, select") || overlayOpen()) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;  // leave the browser's own shortcuts alone (⌘1 switches tab, alt+← goes back)
  const c = DECK.c, t = deckTime();
  if (e.code === "Space") { e.preventDefault(); e.target.closest("button")?.blur(); deckToggle(); }  // blur: a focused ▶ Play would toggle again on keyup
  else if (/^Digit[1-8]$/.test(e.code)) {
    const n = +e.code.slice(5) - 1, p = PEND?.path === c.path ? PEND : null;  // with cues ticked: the slots as the pads show them
    const a = p?.adds.find(a => !a.nofit && a.hotcue === n);
    const y = a ? { ms: a.ms, type: a.type, bars: a.type === "loop" && a.len_ms ? a.len_ms / 1000 / c.bar_seconds : 0 }
      : myCues(c).find(y => y.status !== "deleted" && (pendSlot(y, p) ?? y.hotcue) === n);
    if (y) { e.preventDefault(); deckSeek(y.ms / 1000, true, y.type === "loop" && y.bars ? [y.ms / 1000, y.ms / 1000 + y.bars * c.bar_seconds] : null); }
  } else if (e.code === "KeyC") {
    const b = $("#dk-cue"); if (b) { e.preventDefault(); b.click(); }
  } else if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
    e.preventDefault();
    const d = (e.code === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 8 : 1) * c.bar_seconds;
    deckSeek(Math.max(0, t + d), deckPlaying() || !!DECK.waiting, null);
  }
});

const LOOP_BARS = [1, 2, 4, 8, 16];
const LOOP_LEN = new Map();  // path -> Map(loop idea key -> bars you picked for it in the menu)
const loopLen = path => { if (!LOOP_LEN.has(path)) LOOP_LEN.set(path, new Map()); return LOOP_LEN.get(path); };
const RENUM = new Map();  // path -> true while "Number cues in order" is pressed for that track (saved with Approve ticked)
const DRAGMV = new Map();  // path -> Map("m<slot>:<from_ms>" -> ms): moves of your cues made by dragging them on the waveform
const HAND = new Map();  // path -> cues you set by hand with + Cue, not approved yet: [{ms, name}]
let PEND = null;  // the open track's cues ticked in the approve panel, not approved yet (the pads, waveform and 1–8 keys show them)
const WAVE_DETAIL = new Map(), WAVE_FETCH = new Map();  // path -> detailed waveform, fetched on the first zoom; path -> that fetch
let WAVE_UP = null;  // the drawn waveform's window mouseup listener
function decodeWave(w) {
  const raw = atob(w.data), a = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) a[i] = raw.charCodeAt(i);
  return { n: w.slices, t0: w.t0 || 0, sec: w.seconds, a };
}
function waveColumns(g, data, v0, v1, y0, y1, w, alpha) {  // one column per device pixel
  const mid = (y0 + y1) / 2, half = (y1 - y0) / 2, span = v1 - v0, dpr = window.devicePixelRatio || 1, cols = Math.round(w * dpr), k = data.n / data.sec;
  for (let px = 0; px < cols; px++) {
    const ta = v0 + px * span / cols - data.t0, tb = ta + span / cols;
    const a = Math.max(0, Math.floor(ta * k)), z = Math.min(data.n, Math.max(a + 1, Math.floor(tb * k)));
    if (a >= data.n) break;
    let pk = 0, rm = 0, lo = 0, md = 0, hi = 0;
    for (let i = a; i < z; i++) { const o = i * 5, d = data.a; pk = Math.max(pk, d[o]); rm = Math.max(rm, d[o + 1]); lo += d[o + 2]; md += d[o + 3]; hi += d[o + 4]; }
    const m = Math.max(lo, md, hi, 1), wl = (lo / m) ** 3, wm = (md / m) ** 3, wh = (hi / m) ** 3, sum = wl + wm + wh;
    // lows red, mids green, highs blue; the strongest band sets the colour
    const r = Math.round((255 * wl + 40 * wm + 60 * wh) / sum), gg = Math.round((50 * wl + 225 * wm + 140 * wh) / sum), bb = Math.round((40 * wl + 70 * wm + 255 * wh) / sum);
    const ph = pk / 255 * half, rh = Math.min(ph, rm / 255 * half * 1.6);
    g.fillStyle = `rgba(${r},${gg},${bb},${0.45 * alpha})`; g.fillRect(px / dpr, mid - ph, 1 / dpr, ph * 2);
    g.fillStyle = `rgba(${r},${gg},${bb},${alpha})`; g.fillRect(px / dpr, mid - rh, 1 / dpr, rh * 2);
  }
}
function drawWave(c, box) {
  const cv = box.querySelector("#wave");
  if (!cv || !c.wave) return;
  const over = decodeWave(c.wave), dur = over.sec, beat = c.bar_seconds / 4;
  const kept = box._view && box._view.path === c.path ? box._view : null;  // keep the zoom across re-renders
  const st = box._view = { path: c.path, v0: kept ? kept.v0 : 0, v1: kept ? kept.v1 : dur, detail: WAVE_DETAIL.get(c.path) || null, fine: null, loading: false };
  const minSpan = beat, hl = box.querySelector("#wavehl"), bh = box.querySelector("#wavebeat"), zl = box.querySelector("#wavezoom");
  const barT = b => c.first_bar_s + b * c.bar_seconds, barAt = t => Math.round((t - c.first_bar_s) / c.bar_seconds);
  const TOP = 22, BOT = 132, MID = (TOP + BOT) / 2, HALF = (BOT - TOP) / 2, OV0 = 170, OV1 = 202;
  let W = 0, X = t => t;
  const zoomed = () => st.v1 - st.v0 < dur * 0.98;

  function setView(v0, v1) {
    let span = Math.min(dur, Math.max(minSpan, v1 - v0));
    v0 = Math.min(Math.max(0, v0), dur - span);
    st.v0 = v0; st.v1 = v0 + span;
    if (!SNAP && !st.detail && !st.loading && span < dur * 0.6) {  // close in: fetch the detailed waveform (once, across redraws)
      st.loading = true; render();
      if (!WAVE_FETCH.has(c.path)) WAVE_FETCH.set(c.path, api("wave", { path: c.path }).then(w => { const d = decodeWave(w); WAVE_DETAIL.set(c.path, d); return d; })
        .finally(() => WAVE_FETCH.delete(c.path)));
      WAVE_FETCH.get(c.path).then(d => { st.detail = d; }).catch(() => {}).finally(() => { st.loading = false; render(); });
    }
    render();
    refine();  // after render: it measures the canvas
  }
  // closer in than the 100-a-second detail can show: once the view settles, fetch just this stretch (and half a
  // screen either side for panning) at the screen's own resolution; until it comes, the coarser level is stretched
  const rate = () => W * (window.devicePixelRatio || 1) / (st.v1 - st.v0);  // slices a second the screen can show
  const covers = (d, v0, v1) => d && d.t0 <= v0 + 1e-6 && d.t0 + d.sec >= v1 - 1e-6;
  const fineOk = () => covers(st.fine, st.v0, st.v1) && st.fine.n / st.fine.sec >= rate() * 0.7;
  let fineTimer = 0, fineSeq = 0;
  function refine() {
    clearTimeout(fineTimer);
    if (SNAP || !W || rate() <= 100 || fineOk() || box._view !== st) return;
    fineTimer = setTimeout(() => {
      const span = st.v1 - st.v0, t0 = Math.max(0, st.v0 - span / 2), t1 = Math.min(dur, st.v1 + span / 2), seq = ++fineSeq;
      api("wave", { path: c.path, t0: t0.toFixed(4), t1: t1.toFixed(4), n: Math.ceil((t1 - t0) * rate()) })
        .then(w => { if (seq === fineSeq) { st.fine = decodeWave(w); render(); } }).catch(() => {});
    }, 120);
  }
  let render = function () {
    if (box._view !== st) return;  // drawn again since (an approve re-renders the drawer): a late fetch here mustn't paint the old view
    const dpr = window.devicePixelRatio || 1;
    W = cv.clientWidth; const H = cv.clientHeight;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
    const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
    bh.style.display = "none";  // the view moved: the hovered beat line is lit again on the next mouse move
    const span = st.v1 - st.v0; X = t => (t - st.v0) / span * W;
    const pxBar = W * c.bar_seconds / span, pxBeat = pxBar / 4;
    // grid: beats when there's room, bars, 8-bar phrases, 32-bar lines; bar numbers as dense as fits
    const step = [1, 2, 4, 8, 16, 32, 64].find(k => pxBar * k >= 30) || 64;
    g.font = "10px system-ui, sans-serif"; g.textAlign = "left";
    const b0 = Math.max(0, Math.floor((st.v0 - c.first_bar_s) / c.bar_seconds) - 1), b1 = Math.min(c.bars, Math.ceil((st.v1 - c.first_bar_s) / c.bar_seconds) + 1);
    const onTop = [];  // [x, width, alpha]: the grid again, faintly, on top of the waveform (its core is opaque)
    for (let b = b0; b <= b1; b++) {
      const x = Math.round(X(barT(b))), n = b - c.grid_bar + 1, in4 = (n - 1) % 4 === 0, in8 = (n - 1) % 8 === 0, in32 = (n - 1) % 32 === 0;
      if (pxBeat > 80) for (let k = 1; k < 16; k++) if (k % 4) { g.fillStyle = "rgba(255,255,255,.04)"; g.fillRect(Math.round(X(barT(b) + k * beat / 4)), TOP, 1, BOT - TOP); }  // 16ths, very close in
      if (pxBeat > 6) for (let k = 1; k < 4; k++) { const bx = Math.round(X(barT(b) + k * beat)); g.fillStyle = "rgba(255,255,255,.07)"; g.fillRect(bx, TOP, 1, BOT - TOP); onTop.push([bx, 1, .14]); }
      // beat < bar < every 4 bars (as in Traktor) < 8-bar phrase < 32 bars
      if (in32 || in8 || (in4 && pxBar * 4 >= 8) || pxBar > 4) {
        const w = in32 || (in8 && pxBar > 12) ? 2 : 1;
        g.fillStyle = in32 ? "rgba(255,255,255,.6)" : in8 ? "rgba(255,255,255,.42)" : in4 ? "rgba(255,255,255,.28)" : "rgba(255,255,255,.1)";
        g.fillRect(x, TOP, w, BOT - TOP);
        onTop.push([x, w, in32 ? .4 : in8 ? .32 : in4 ? .24 : .18]);
      }
      if (n >= 1 && (n - 1) % step === 0) { g.fillStyle = in8 ? "rgba(255,255,255,.7)" : "rgba(255,255,255,.45)"; g.fillText(String(n), x + 2, BOT - 3); }
    }
    const fine = covers(st.fine, st.v0, st.v1) && st.fine.n / st.fine.sec > 100 ? st.fine : null;
    const data = fine || (st.detail && span < dur * 0.6 ? st.detail : over);
    waveColumns(g, data, st.v0, st.v1, TOP, BOT, W, 1);
    for (const [x, w, a] of onTop) { g.fillStyle = `rgba(255,255,255,${a})`; g.fillRect(x, TOP, w, BOT - TOP); }
    const vis = x => x > -40 && x < W + 40;
    const hov = box._hoverPath === c.path ? box._hover || [] : [];
    for (const [a, b] of hov) {  // the approve-panel row under the mouse: its span (a loop) or bar (a cue), lit up
      const x0 = X(a), x1 = Math.max(x0 + 3, X(b));
      if (!vis(x0) && !vis(x1) && !(x0 < 0 && x1 > W)) continue;
      g.fillStyle = "rgba(91,158,240,.30)"; g.fillRect(x0, TOP, x1 - x0, BOT - TOP);
      g.fillStyle = "#5b9ef0"; g.fillRect(x0, TOP, x1 - x0, 3); g.fillRect(x0, BOT - 3, x1 - x0, 3); g.fillRect(Math.round(x0) - 1, TOP, 3, BOT - TOP);
    }
    (c.loop_ideas || []).forEach((l, k) => {  // loop ideas on top of the waveform: L1 solid edges, the others lighter, numbered
      const lx = X(l.ms / 1000), lw = Math.max(3, X(l.ms / 1000 + l.len_ms / 1000) - lx);
      if (!vis(lx) && !vis(lx + lw)) return;
      g.fillStyle = k === 0 ? "rgba(240,138,36,.30)" : "rgba(240,138,36,.16)"; g.fillRect(lx, TOP, lw, BOT - TOP);
      g.fillStyle = "#f08a24"; const e = k === 0 ? 4 : 2; g.fillRect(lx, TOP, lw, e); g.fillRect(lx, BOT - e, lw, e);
      g.font = "bold 10px system-ui, sans-serif"; g.textAlign = "left"; g.fillStyle = "#ffb067"; g.fillText(`L${k + 1}`, lx + 2, TOP + 14);
    });
    g.font = "11px system-ui, sans-serif"; g.textAlign = "center";
    const pend = box._pending?.path === c.path ? box._pending : null;  // ticked in the approve panel, not approved yet
    for (const y of pend?.adds || []) {  // pending new cues: dashed green line, an outlined slot number ("?" until known, "✕" won't fit)
      const x = Math.round(X(y.ms / 1000));
      if (!vis(x)) continue;
      const col = y.nofit ? "rgba(255,120,110,.95)" : "rgba(76,209,122,.95)";
      g.setLineDash([4, 3]); g.strokeStyle = col; g.lineWidth = zoomed() ? 2 : 1;
      g.beginPath(); g.moveTo(x + 0.5, 19); g.lineTo(x + 0.5, BOT); g.stroke();
      if (y.type === "loop" && y.len_ms) {
        const x1 = X((y.ms + y.len_ms) / 1000);
        g.fillStyle = "rgba(76,209,122,.08)"; g.fillRect(x, TOP, Math.max(2, x1 - x), BOT - TOP);
        g.beginPath(); g.moveTo(x, TOP + 1.5); g.lineTo(x1, TOP + 1.5); g.moveTo(x, BOT - 1.5); g.lineTo(x1, BOT - 1.5); g.moveTo(Math.round(x1) + 0.5, TOP); g.lineTo(Math.round(x1) + 0.5, BOT); g.stroke();
      }
      g.setLineDash([]); g.lineWidth = 1.5;
      g.fillStyle = "#0c0c0e"; g.fillRect(x - 7, 3, 14, 15); g.strokeRect(x - 6.5, 3.5, 13, 14); g.lineWidth = 1;
      g.fillStyle = col; g.fillText(y.nofit ? "✕" : y.hotcue == null ? "?" : y.hotcue >= 0 ? String(y.hotcue + 1) : "•", x, 14);
      g.textAlign = "left"; g.font = "10px system-ui, sans-serif"; g.fillText(y.nofit ? "won't fit" : "new", x + 9, 14);
      g.textAlign = "center"; g.font = "11px system-ui, sans-serif";
    }
    for (const y of shownCues(c, box)) {  // your cues: white lines, slot numbers on top; approved new ones green, moved ones yellow
      if (y.del) {  // marked for deletion: red dashed line, its number outlined and struck through
        const x = Math.round(X(y.ms / 1000));
        if (!vis(x)) continue;
        g.setLineDash([4, 3]); g.strokeStyle = "rgba(255,120,110,.9)"; g.beginPath(); g.moveTo(x + 0.5, 19); g.lineTo(x + 0.5, BOT); g.stroke(); g.setLineDash([]);
        g.fillStyle = "#0c0c0e"; g.fillRect(x - 7, 3, 14, 15); g.lineWidth = 1.5; g.strokeRect(x - 6.5, 3.5, 13, 14); g.lineWidth = 1;
        g.fillStyle = "#ff786e"; g.fillText(y.hotcue >= 0 ? String(y.hotcue + 1) : y.type === "loop" ? "L" : "•", x, 14); g.fillRect(x - 6, 10, 12, 1.5);
        g.textAlign = "left"; g.font = "10px system-ui, sans-serif"; g.fillText(y.type === "cue" ? "delete" : `delete ${cueKind(y).toLowerCase()}`, x + 9, 14);
        g.textAlign = "center"; g.font = "11px system-ui, sans-serif";
        continue;
      }
      if (y.status === "moved") {  // greyed out where it was, with a dotted arrow to where it goes
        const gx = Math.round(X(y.from_ms / 1000)), nx = Math.round(X(y.ms / 1000));
        g.setLineDash([3, 3]); g.strokeStyle = "rgba(160,160,160,.6)";
        if (vis(gx)) {
          g.beginPath(); g.moveTo(gx + 0.5, TOP); g.lineTo(gx + 0.5, BOT); g.stroke();
          g.fillStyle = "rgba(130,130,130,.75)"; g.fillRect(gx - 7, 3, 14, 15); g.fillStyle = "#2a2a2e"; g.fillText(y.hotcue >= 0 ? String(y.hotcue + 1) : "•", gx, 14);
        }
        if (vis(gx) || vis(nx)) { g.strokeStyle = "rgba(255,210,63,.8)"; g.beginPath(); g.moveTo(gx, TOP + 6); g.lineTo(nx, TOP + 6); g.stroke(); }
        g.setLineDash([]);
      }
      const x = Math.round(X(y.ms / 1000));
      if (!vis(x)) continue;
      const col = y.status === "new" ? "#4cd17a" : y.status === "moved" ? "#ffd23f" : "#fff";
      g.fillStyle = y.status === "new" ? "rgba(76,209,122,.95)" : y.status === "moved" ? "rgba(255,210,63,.95)" : "rgba(255,255,255,.9)"; g.fillRect(x, TOP, zoomed() ? 2 : 1, BOT - TOP);
      if (y.type === "loop" && y.bars) {  // the loop's whole span, like Traktor: shaded, edged, its end marked
        const x1 = X(y.ms / 1000 + y.bars * c.bar_seconds), lw = Math.max(2, x1 - x);
        g.fillStyle = y.status === "new" ? "rgba(76,209,122,.14)" : "rgba(255,255,255,.12)"; g.fillRect(x, TOP, lw, BOT - TOP);
        g.fillStyle = y.status === "new" ? "rgba(76,209,122,.8)" : "rgba(255,255,255,.7)"; g.fillRect(x, TOP, lw, 3); g.fillRect(x, BOT - 3, lw, 3);
        g.setLineDash([3, 3]); g.strokeStyle = g.fillStyle; g.beginPath(); g.moveTo(Math.round(x1) + 0.5, TOP); g.lineTo(Math.round(x1) + 0.5, BOT); g.stroke(); g.setLineDash([]);
      }
      if (y.type === "fade-in" || y.type === "fade-out") {  // a ramp after the line: rising for a fade-in, falling for a fade-out
        const w = 26, up = y.type === "fade-in";
        g.fillStyle = "rgba(255,255,255,.22)"; g.beginPath(); g.moveTo(x, BOT); g.lineTo(x + w, BOT);
        if (up) g.lineTo(x + w, TOP + 22); else g.lineTo(x, TOP + 22);
        g.fill();
      }
      const label = y.hotcue >= 0 ? String(y.hotcue + 1) : y.type === "loop" ? "L" : "•", to = pendSlot(y, pend);
      g.fillStyle = col; g.fillRect(x - 7, 3, 14, 15); g.fillStyle = "#0c0c0e"; g.fillText(label, x, 14);
      let kx = x + 9;
      if (to === -1) {  // approved, but the ticked cues leave no slot for it
        g.textAlign = "left"; g.font = "bold 10px system-ui, sans-serif"; g.fillStyle = "#ff786e"; g.fillText("won't fit", kx, 14);
        kx += g.measureText("won't fit").width + 5; g.textAlign = "center"; g.font = "11px system-ui, sans-serif";
      } else if (to != null) {  // a ticked new cue before it pushes this one up a slot: "3 →4"
        g.textAlign = "left"; g.font = "bold 10px system-ui, sans-serif"; g.fillStyle = "#4cd17a"; g.fillText(`→${to + 1}`, kx, 14);
        kx += g.measureText(`→${to + 1}`).width + 5; g.textAlign = "center"; g.font = "11px system-ui, sans-serif";
      }
      if (y.type !== "cue") {  // what it is, next to the number
        g.textAlign = "left"; g.font = "10px system-ui, sans-serif"; g.fillStyle = col; g.fillText(cueKind(y), kx, 14);
        g.textAlign = "center"; g.font = "11px system-ui, sans-serif";
      }
    }
    for (const f of (c.corrections || []).filter(f => !fixSettled(c, f) && !pickedMove(box, f))) {  // proposed corrections: yellow arrows from your cue to the bar the model prefers
      const x0 = X(f.from_ms / 1000), x1 = X(f.to_ms / 1000);
      if (!vis(x0) && !vis(x1)) continue;
      g.strokeStyle = g.fillStyle = "#ffd23f"; g.lineWidth = 2;
      g.beginPath(); g.moveTo(x0, TOP + 10); g.lineTo(x1, TOP + 10); g.stroke(); g.fillRect(Math.round(x1), TOP, 2, BOT - TOP);
      const dir = x1 >= x0 ? 1 : -1; g.beginPath(); g.moveTo(x1, TOP + 10); g.lineTo(x1 - 5 * dir, TOP + 6); g.lineTo(x1 - 5 * dir, TOP + 14); g.fill();
      g.lineWidth = 1;
    }
    c.cues.forEach((q, i) => {  // suggestions: blue ticks and names below (approved ones are drawn as your cues)
      const x = Math.round(X(q.ms / 1000));
      if (!vis(x) || sugHidden(c, q)) return;
      g.fillStyle = "rgba(111,180,255,.9)"; g.fillRect(x, MID + HALF * 0.55, zoomed() ? 2 : 1, BOT - (MID + HALF * 0.55));
      g.beginPath(); g.moveTo(x - 5, BOT + 9); g.lineTo(x + 5, BOT + 9); g.lineTo(x, BOT + 2); g.fill();
      g.fillStyle = "#cfe4ff"; g.fillText(zoomed() ? `${q.name} · bar ${barLabel(c, q.bar).n}` : q.name, Math.min(W - 30, Math.max(30, x)), BOT + 21 + (i % 2) * 12);
    });
    // overview of the whole track with the visible part framed; click or drag it to move
    waveColumns(g, over, 0, dur, OV0, OV1, W, 0.75);
    for (const [a, b] of hov) { g.fillStyle = "rgba(91,158,240,.55)"; g.fillRect(a / dur * W - 1, OV0, Math.max(3, (b - a) / dur * W), OV1 - OV0); }
    const OX = t => t / dur * W;  // every cue on the overview too: yours white + number, suggested blue, loop orange, corrections yellow
    for (const l of c.loop_ideas || []) { g.fillStyle = "#f08a24"; g.fillRect(OX(l.ms / 1000), OV0, Math.max(3, OX(l.len_ms / 1000)), 4); }
    for (const q of c.cues.filter(q => !sugHidden(c, q))) { g.fillStyle = "rgba(111,180,255,.95)"; g.fillRect(Math.round(OX(q.ms / 1000)), OV0 + (OV1 - OV0) / 2, 2, (OV1 - OV0) / 2); }
    for (const f of (c.corrections || []).filter(f => !fixSettled(c, f) && !pickedMove(box, f))) { g.fillStyle = "#ffd23f"; g.fillRect(Math.round(OX(f.to_ms / 1000)), OV0, 2, OV1 - OV0); }
    g.font = "bold 9px system-ui, sans-serif"; g.textAlign = "left";
    for (const y of shownCues(c, box)) {
      if (y.del) { const x = Math.round(OX(y.ms / 1000)) + 0.5; g.setLineDash([2, 2]); g.strokeStyle = "#ff786e"; g.beginPath(); g.moveTo(x, OV0); g.lineTo(x, OV1); g.stroke(); g.setLineDash([]); continue; }
      if (y.status === "moved") { g.fillStyle = "rgba(160,160,160,.7)"; g.fillRect(Math.round(OX(y.from_ms / 1000)), OV0, 1, OV1 - OV0); }
      const x = Math.round(OX(y.ms / 1000)), col = y.status === "new" ? "#4cd17a" : y.status === "moved" ? "#ffd23f" : "#fff";
      g.fillStyle = col; g.fillRect(x, OV0, 1, OV1 - OV0);
      if (y.type === "loop" && y.bars) g.fillRect(x, OV1 - 3, Math.max(3, OX(y.bars * c.bar_seconds)), 3);
      const to = pendSlot(y, pend), label = to != null && to >= 0 ? String(to + 1) : y.hotcue >= 0 ? String(y.hotcue + 1) : y.type === "loop" ? "L" : "";
      if (label) { g.fillStyle = col; g.fillRect(x + 1, OV0, 9, 10); g.fillStyle = "#0c0c0e"; g.fillText(label, x + 3, OV0 + 8); }
    }
    for (const y of pend?.adds || []) {  // pending new cues on the overview: dashed, outlined number
      const x = Math.round(OX(y.ms / 1000)) + 0.5, col = y.nofit ? "rgba(255,120,110,.95)" : "#4cd17a";
      g.setLineDash([2, 2]); g.strokeStyle = col; g.beginPath(); g.moveTo(x, OV0); g.lineTo(x, OV1); g.stroke(); g.setLineDash([]);
      if (y.hotcue != null && y.hotcue >= 0) { g.strokeRect(x + 1, OV0 + 0.5, 9, 10); g.fillStyle = col; g.fillText(String(y.hotcue + 1), x + 3, OV0 + 8); }
    }
    if (zoomed()) {
      const a = st.v0 / dur * W, z = st.v1 / dur * W;
      g.fillStyle = "rgba(12,12,14,.6)"; g.fillRect(0, OV0, a, OV1 - OV0); g.fillRect(z, OV0, W - z, OV1 - OV0);
      g.strokeStyle = "#fff"; g.strokeRect(a + 0.5, OV0 + 0.5, Math.max(2, z - a - 1), OV1 - OV0 - 1);
    }
    if (zl) {  // close in: times to the hundredth, the length in beats, and how fine the waveform is
      const tm = t => { if (span >= 10) return mmss(t); const d = Math.round(t * 100); return `${Math.floor(d / 6000)}:${((d % 6000) / 100).toFixed(2).padStart(5, "0")}`; };
      const len = span < 2 * c.bar_seconds ? `${(span / beat).toFixed(1)} beats` : `${Math.round(span / c.bar_seconds)} bars`;
      const res = data === over ? "" : ` · ${span * 1000 / W < 10 ? (span * 1000 / W).toFixed(2) + " ms" : (span / W).toFixed(2) + " s"} a pixel${rate() <= 100 || fineOk() ? "" : " · sharpening…"}`;
      zl.textContent = zoomed() ? `${tm(st.v0)}–${tm(st.v1)} · ${len}${st.loading ? " · loading detail…" : res}` : "whole track";
    }
  }

  const zoomAt = (t, f) => setView(t - (t - st.v0) * f, t + (st.v1 - t) * f);
  const timeAt = x => st.v0 + x / W * (st.v1 - st.v0);
  cv.addEventListener("wheel", e => {
    const x = e.offsetX;
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoomAt(timeAt(x), Math.exp(e.deltaY * 0.01)); }  // pinch / ctrl+scroll
    else if (Math.abs(e.deltaX) > Math.abs(e.deltaY) && zoomed()) { e.preventDefault(); const d = e.deltaX / W * (st.v1 - st.v0); setView(st.v0 + d, st.v1 + d); }
  }, { passive: false });
  cv.ondblclick = e => zoomAt(timeAt(e.offsetX), e.shiftKey ? 2 : 0.5);
  let drag = null;
  const cueAt = (x, yy) => {  // a cue of yours (not approved yet, not marked for deletion) under the mouse in the big waveform
    if (SNAP || yy >= OV0) return null;
    const pend = box._pending?.path === c.path ? box._pending : null;
    const all = [...shownCues(c, box).filter(y => !y.del && y.status !== "new" && y.type !== "fade-in" && y.type !== "fade-out" && y.type !== "load"),
      ...(pend?.adds || []).filter(a => a.hand && a.type === "cue").map(a => ({ ...a, status: "pending" }))];
    return all.map(y => ({ y, d: Math.abs(X(y.ms / 1000) - x) })).filter(o => o.d <= 5).sort((a, b) => a.d - b.d)[0]?.y || null;
  };
  cv.onmousedown = e => {
    if (e.button !== 0) return;  // right or middle click: no drag, no jump
    const hit = cueAt(e.offsetX, e.offsetY);
    if (hit) { drag = { cue: hit, x: e.clientX, ms: hit.ms, moved: false }; return; }
    if (e.offsetY >= OV0) { const t = e.offsetX / W * dur, h = (st.v1 - st.v0) / 2; setView(t - h, t + h); drag = { ov: true }; return; }
    drag = { x: e.clientX, v0: st.v0, v1: st.v1, moved: false };
  };
  cv.onmouseup = e => {  // a click (not a drag) on the waveform jumps there, like Traktor
    if (e.button === 0 && drag && !drag.ov && !drag.moved && !SNAP) deckSeek(timeAt(e.offsetX), deckPlaying() || !!DECK.waiting, null);  // a click moves the playhead; it only keeps playing if it already was
  };
  WAVE_UP?.abort(); WAVE_UP = new AbortController();  // drawWave runs on every redraw: drop the last one's listener
  window.addEventListener("mouseup", () => {
    if (drag?.cue && drag.moved) { const { cue, ms } = drag; drag = null; bh.style.display = "none"; cv.style.cursor = ""; box._cueDrag?.(cue, ms); return; }
    drag = null; cv.style.cursor = "";
  }, { signal: WAVE_UP.signal });
  // page zoom or a resize: draw again at the new size and pixel ratio, once per frame at most
  let sized = 0;
  const resized = () => { if (!sized) sized = requestAnimationFrame(() => { sized = 0; if (cv.isConnected) { render(); refine(); } }); };
  const ro = new ResizeObserver(resized); ro.observe(cv);
  window.addEventListener("resize", resized, { signal: WAVE_UP.signal });  // zoom changes devicePixelRatio, maybe not the CSS width
  WAVE_UP.signal.addEventListener("abort", () => ro.disconnect());
  const marks = [...myCues(c).map(y => ({ t: y.ms / 1000, s: `${y.status === "new" ? "approved (not written yet): " : y.status === "moved" ? "your cue, approved to move here: " : ""}your ${cueKind(y).toLowerCase()}${y.hotcue >= 0 ? ` (slot ${y.hotcue + 1})` : ""}` })),
    ...c.cues.map(q => ({ t: q.ms / 1000, s: `suggested: ${q.name}` })), ...(c.loop_ideas || []).map((l, k) => ({ t: l.ms / 1000, s: `loop idea L${k + 1}: ${l.kind} (${l.bars} bars, ${l.why})` })),
    ...(c.corrections || []).filter(f => !fixSettled(c, f)).map(f => ({ t: f.to_ms / 1000, s: `proposed: move your cue ${f.hotcue + 1} here` }))];
  cv.onmousemove = e => {
    const x = e.offsetX;
    if (drag && drag.ov) { const t = x / W * dur, h = (st.v1 - st.v0) / 2; setView(t - h, t + h); return; }
    if (drag?.cue) {  // dragging a cue: a line on the beat it would land on
      if (Math.abs(drag.x - e.clientX) > 3) drag.moved = true;
      if (!drag.moved) return;
      const bm = 60000 / c.bpm, ms = +(c.grid_ms + Math.round((timeAt(Math.max(0, Math.min(W, x))) * 1000 - c.grid_ms) / bm) * bm).toFixed(3);
      drag.ms = Math.max(0, Math.min(ms, dur * 1000 - 1));
      cv.style.cursor = "grabbing"; bh.style.left = Math.round(X(drag.ms / 1000)) + "px"; bh.style.display = "block";
      tip.textContent = `Move to ${clock(drag.ms / 1000)}`; tip.style.display = "block"; return;
    }
    if (!drag) cv.style.cursor = cueAt(x, e.offsetY) ? "grab" : "";
    if (drag && Math.abs(drag.x - e.clientX) > 3) drag.moved = true;
    if (drag && drag.moved) { const d = (drag.x - e.clientX) / W * (drag.v1 - drag.v0); cv.style.cursor = "grabbing"; setView(drag.v0 + d, drag.v1 + d); }
    if (e.offsetY >= OV0) { hl.style.display = "none"; tip.textContent = "Click or drag to move the view"; tip.style.display = "block"; return; }
    const near = marks.map(m => ({ ...m, d: Math.abs(X(m.t) - x) })).filter(m => m.d < 7).sort((a, b) => a.d - b.d);
    let lines, bi;
    if (near.length) {  // snap to the marker: its exact bar and time
      const t0 = near[0].t, same = near.filter(m => Math.abs(m.t - t0) < c.bar_seconds / 2), bl = barLabel(c, barAt(t0));
      const off = (t0 - barT(barAt(t0))) / beat;
      lines = [...same.map(m => m.s), `${bl.text} · ${mmss(t0)}${Math.abs(off) > 0.1 ? ` · ${off > 0 ? "+" : ""}${off.toFixed(1)} beats off the bar line` : ""}`, bl.phrase];
      bi = barAt(t0);
    } else {
      const t = timeAt(x); bi = Math.floor((t - c.first_bar_s) / c.bar_seconds); const bl = barLabel(c, bi);
      const bt = Math.floor((t - barT(bi)) / beat) + 1;
      lines = [`${bl.text}${zoomed() ? `, beat ${bt}` : ""} · ${mmss(t)}`, bl.phrase];
    }
    // the beat line under the mouse (within 4 px), a little brighter, where beat lines are drawn
    const lineT = c.first_bar_s + Math.round((timeAt(x) - c.first_bar_s) / beat) * beat, lx = Math.round(X(lineT));
    const lit = !drag?.moved && W * beat / (st.v1 - st.v0) > 6 && Math.abs(lx - x) <= 4;
    bh.style.display = lit ? "block" : "none"; if (lit) bh.style.left = lx + "px";
    hl.style.left = X(barT(bi)) + "px"; hl.style.width = Math.max(2, X(barT(bi) + c.bar_seconds) - X(barT(bi))) + "px"; hl.style.display = "block";
    tip.textContent = lines.join("\n"); tip.style.display = "block";
  };
  cv.onmouseleave = () => { tip.style.display = "none"; hl.style.display = "none"; bh.style.display = "none"; };
  box.querySelectorAll("[data-zoom]").forEach(b => b.onclick = () => {
    const z = b.dataset.zoom, mid = (st.v0 + st.v1) / 2;
    if (z === "fit") setView(0, dur); else zoomAt(mid, z === "in" ? 0.5 : 2);
  });
  box._wave = { X: t => X(t), W: () => W, st, setView: (a, b) => setView(a, b), zoomed, dur, redraw: () => render() };
  const r0 = render;
  render = () => { r0(); deckUi(); };  // keep the playhead in place when the view changes
  setView(st.v0, st.v1);
}
function cueTable(c, sugg) {
  const yours = myCues(c).map(y => ({ when: y.ms / 1000, what: (y.status === "new" ? "✓ approved: " : y.status === "moved" ? "✓ moved: " : "") + `${cueKind(y)}${y.hotcue >= 0 ? ` · slot ${y.hotcue + 1}` : ""}`, who: "yours" }));
  const ours = sugg.map(q => ({ when: q.ms / 1000, what: q.name, who: "suggested" }));
  (c.loop_ideas || []).forEach((l, k) => ours.push({ when: l.ms / 1000, what: `Loop idea L${k + 1}: ${l.bars} bars · ${l.kind}`, who: "suggested" }));
  const rows = [...yours, ...ours].sort((a, b) => a.when - b.when);
  return `<details style="margin-top:8px"><summary class="small muted">As a list</summary>${table([{ h: "Time", f: r => mmss(r.when) },
    { h: "", f: r => r.who === "yours" ? `<span class="chip">yours</span>` : `<span class="chip good">suggested</span>` }, { h: "What", f: r => esc(r.what) }], rows)}</details>`;
}

