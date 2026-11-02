# Rust DSP module (dsp/): the Essentia port

Detail moved out of CLAUDE.md so it loads only when needed. Code is the source of truth.

- `dsp/`: Rust (PyO3 + maturin) module `track_analyzer_dsp`, replacing Essentia steps bit for bit, faster
  (`analysis.dsp()`, required since stage 7). Now: `loudness_ebur128` (= LoudnessEBUR128, needs FMA
  in the coefficients and `b*x - a*y` to match the arm64 build) and `resample_sinc_medium` (= Resample quality 1,
  libsamplerate 0.2.2: its ring-buffer walk replayed on one thread, dot products in parallel; via
  `analysis.resample_medium`) and `resample_linear` (= Resample quality 4, the models' 16 kHz input via
  `analysis.resample_linear`; needs FMA in `x0 + t*(x1 - x0)`: the plain form was 1 sample off in 62.9M on a mix,
  though every test signal passed). `analysis.mono_mix` is MonoMixer in numpy (same bits). `musicnn_mels` (= TensorflowInputMusiCNN per
  FrameGenerator frame; `analysis.musicnn_mels`, MAEST's patches; `streaming=True` = the streaming FrameCutter's
  frames, whose silentFrames="noise" blanks a silent first frame) and `analysis.effnet_embeddings` (EffNet's
  padding/patches/batches as TensorflowPredictEffnetDiscogs, graph via plain TensorflowPredict). Its 512-point FFT is
  FFTW 3.3.10's plan with the codelets ported (`dsp/tools/gen_fftw_codelets.py`: Essentia's FFTW runs the PLAIN
  codelet variant and gcc -O3 fuses mul+add across statements, modelled with mul_add; checked by calling the
  wheel's local `_r2cf_16` etc. via ctypes). `key_edma` (= KeyExtractor edma, `analysis.key_of`; dsp/src/key.rs, the 4096
  FFT in fft4096.rs with `r2cf_128`, tables from `dsp/tools/gen_key_tables.py`): SpectralWhitening's vectorised loop (blocks of 16 unfused, tail fused), Key's std
  unfused / correlation fused; per-frame work `#[inline(never)]` with heap buffers (inlined into rayon's splitting it
  overflowed a worker's 2 MB stack in check.py). Source read at Essentia commit 1d1cc983 (= dev1177). `rhythm_multifeature`
  (= RhythmExtractor2013 multifeature, `analysis.rhythm`; dsp/src/bpm.rs, tables from `dsp/tools/gen_bpm_tables.py`):
  the trackers' autocorrelations need FFTW's own rounding, so dsp/src/fftw.rs ports FFTW's ESTIMATE plans for every
  power of two to 2^22 (r2c + c2r; plans read with `fftwf_sprint_plan`, twiddles from FFTW's sqrt(n) trig table);
  the five Degara trackers draw rand() (seed 1 per call) in Essentia's scheduler order infogain, beat_emphasis,
  melflux, rms, complex (`order` [4, 3, 2, 0, 1]; test signals can't tell orders apart, real recordings can).
  Essentia fails on a whole long mix (output buffer full), so check.py compares only a mix's 2-min segments, as the
  app measures. Accuracy beats Essentia's bits (user, 2026-11-02): every libm call (log, log2, log10, exp, pow(10, x),
  sin/cos, atan2) is the `libm` crate's double rounded to float (dsp/src/math.rs), more exact than Essentia's macOS
  float libm and the same bits on every platform; check.py passes those steps within `TOL` (mel 1 float step, EffNet
  and key strength 1e-5, key/scale equal; BPM its gate) as "close". Mel tables (window, mel weights, twiddles) from
  `dsp/tools/gen_musicnn_tables.py` (macOS + Essentia). A step goes in only
  after a check against Essentia over the whole collection + 48 kHz mixes (test signals aren't enough):
  `uv sync --inexact --group check` (Essentia, the reference; the analyzer itself doesn't use it since stage 7), then
  `uv run python dsp/check.py --collection --files-from <list of mixes>` (all steps, ~25 min; before a
  release). While working, `--steps <changed step>` (~3 min: Essentia's own steps are most of the time).
  uv installs the wheels CI attaches to the `dsp-v<version>` release (`.github/workflows/dsp-wheels.yml`; the
  `dsp` index in pyproject.toml). After changing dsp/: `cd dsp && VIRTUAL_ENV=../.venv maturin develop --release
  --uv` (needs Rust; `uv sync` puts the release wheel back), then bump the version in dsp/Cargo.toml +
  dsp/pyproject.toml, tag `dsp-v<version>`, and update the version + index URL in pyproject.toml. Plan:
  docs/WINDOWS.md.
  Essentia's `AudioLoader` (checked 2026-10, docs/WINDOWS.md stage 0): MP3 via FFmpeg's fixed-point `mp3`
  decoder (16-bit, clipped), mono files as left = signal / right = silence (so loudness reads mono ~3 dB low),
  AAC/Opus untrimmed. `analysis.decode()` = AudioLoader's output from PyAV (`analysis.pyav()`), bit
  for bit on 507/508 files (stage 2; the other: FFmpeg 2.8 decoded one MP3's LAME Info frame as 1152 samples of audio,
  current FFmpeg doesn't, as `traktor.audio_offset` assumes); raw frame bytes converted ~1M samples at a time (per-frame
  numpy was slower than Essentia), FLAC/ALAC frame threads; >2 channels via PyAV's resampler = `ffmpeg -ac 2` bits.
  Opus length = sum of packet durations (FFmpeg 2.8's). Every model graph goes through `analysis.Graph` (= TensorflowPredict:
  run({in: x}) → {out: y}; inputs lose size-1 axes after the batch, outputs padded to 4 axes) or `Head` (= TensorflowPredict2D,
  64 rows per run, the last shorter: one batch per track is off by 2e-6): Python `tensorflow==2.15.0`
  (`analysis.tensorflow()`; pyproject.toml also names tensorflow-macos/-intel: uv's lock misses them); bit-identical
  to Essentia's, same speed (stage 4; `dsp/check.py --steps nets`). onnxruntime (tf2onnx) isn't identical
  (MAEST embeddings off by up to 5e-4) and is slower on MAEST.
