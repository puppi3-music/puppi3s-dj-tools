# Windows: what a port would take

Track Analyzer runs on macOS and Linux (x86_64). It does not run on Windows yet. These notes list what stands
in the way, found by reading the code (October 2026); nothing here has been tried on a Windows machine. Items
marked **verify** are guesses about Traktor, Lexicon or a library on Windows that need a real install to confirm.

The plan is a **native Windows** port. WSL2 is described at the end as a stopgap, with its downsides.

## The blocker: Essentia

`essentia-tensorflow` has pip wheels for macOS (arm64, x86_64) and Linux x86_64 only, so `uv sync` fails on
Windows and nothing runs. Essentia doesn't support native Windows builds with TensorFlow. Building it ourselves
(MinGW/MSYS2 against TensorFlow's Windows C library, then a wheel per Python and TF version) would keep results
identical but means owning a fragile build forever. Not planned.

The plan is to **replace Essentia on every platform**, not only on Windows: two engines would give the same file
a different BPM or key on different machines. The replacement can be built and checked on a Mac against
Essentia before any Windows machine is involved.

### The plan: replace Essentia one piece at a time, without changing any result

Loudness and resampling are already done this way (below): a Rust copy of Essentia's own code, bit-identical on
the whole test collection. That changed the picture. A float32 algorithm ported operation for operation,
including where Essentia's arm64 build fuses multiply-adds, gives Essentia's exact bits, so BPM and key can be
ported **exactly**, not "close". Libraries that compute something similar (librosa, madmom, `samplerate`,
`rubato`, `symphonia`) are ruled out: they change results.

What Essentia bundles, and what each piece needs (versions read from the macOS wheel, `essentia/.dylibs`):

| Job | Essentia now | Replacement | Accuracy target |
|---|---|---|---|
| Loudness | `LoudnessEBUR128` | Rust (`dsp/`) | **done**, bit-identical |
| Resample to 44.1 kHz | `Resample(quality=1)`, libsamplerate 0.2.2 | Rust | **done**, bit-identical |
| Mono mix | `MonoMixer` | Rust | bit-identical |
| Resample to 16 kHz | `Resample(quality=4)` (libsamplerate linear) | Rust | bit-identical |
| Decode | `AudioLoader`: FFmpeg 2.8 (libavcodec 56, 2015) + libswresample 1.2 | PyAV (current FFmpeg, wheels for every platform) | bit-identical on MP3/WAV/AIFF/FLAC/ALAC (stage 0); else measured |
| Mel front end | `TensorflowInputMusiCNN`, `FrameGenerator`; FFTW 3.3.10 float, `FFTW_ESTIMATE` | Rust, linking the same FFTW 3.3.10 | bit-identical |
| Neural nets | `TensorflowPredict*` on TensorFlow 2.15.0 | plain `tensorflow==2.15.0` (onnxruntime tried: not identical) | bit-identical with TF 2.15 (stage 0) |
| Key | `KeyExtractor(profileType="edma")` | Rust port, same FFTW | bit-identical |
| BPM | `RhythmExtractor2013(method="multifeature")` | Rust port, same FFTW | bit-identical; gate below |

Essentia is AGPL-3.0, and so is this repo, so porting its code line by line is fine. FFTW (GPL-2.0+) can be
linked into an AGPL program too.

**The rule for every stage.** Each replacement goes in behind the same switch as loudness and resampling: the
Rust or Python copy is used when it's installed, Essentia otherwise. Essentia stays installed as the reference
until the last stage. `dsp/check.py` grows a check per stage and runs on test signals, every track of the test
collection and the mix recordings, comparing per track, never averages. A stage ships only when it passes. If
it passes only within a tolerance, `CACHE_VERSION` is bumped and the collection re-analysed. Otherwise nothing
changes for anyone. Compare on a Mac: the Rust code follows the Mac build's maths (see loudness below).
Every stage also times the old and the new step on the same files (on mains power, warm file cache, first
track dropped as warm-up) and adds a row to the speed table below. A stage that is slower says so there.

**Speed, step by step** (M1 Pro, mains power; per step, not the whole analysis):

| Step | Essentia | Replacement | Change | Measured on |
|---|---|---|---|---|
| Loudness | 1.4 s per track, 18.4 s per 65-min mix | Rust: 0.2 s, 2.5 s | 6.5–7.5x faster | 469 tracks, 3 mixes |
| Resample to 44.1 kHz (48 kHz files) | 19.7 s per 65-min mix | Rust: 3.8 s | 5.1x faster | 34 files |
| Decode | 78 ms per minute of audio (0.47 s per 6-min track, 4.6–5.4 s per mix) | PyAV: 88 ms | 12% slower (+0.04 s per track, +0.7 s per mix) | 10 tracks, 1 mix as FLAC and MP3 |
| EffNet | 0.74 s per track (median) | TF 2.15: 0.76 s; onnxruntime: 0.72 s | same | 58 tracks |
| Heads (10) | 0.01 s | TF 2.15: 0.01 s; onnxruntime: 0.02 s | same | 58 tracks |
| MAEST | 2.64 s per track | TF 2.15: 2.64 s; onnxruntime: 3.08 s | same; onnxruntime 17% slower | 58 tracks |
| Model loading | 0.1 s | TF 2.15: 0.3 s; onnxruntime: 0.3 s | same | |

The decode gap is probably the Python side of `av_decode` (one numpy array per frame, then a join and an int to
float pass); stage 2 does that conversion in one pass (numpy into a preallocated array, or Rust) and measures again.

**Stage 0: two probes before any code. Done (October 2026), both passed.**
- *Decoding*: PyAV 18.1 (FFmpeg 8) against `AudioLoader`, sample for sample, on the whole test collection, 38
  mix recordings and 39 made-up files in other formats. What `AudioLoader` really does, found on the way:
  - **MP3 goes through FFmpeg's fixed-point decoder** (`mp3`, not `mp3float`): 16-bit samples, clipped at full
    scale. Current FFmpeg's fixed-point decoder gives the same bits; its float decoder does not (not on the
    16-bit grid, peaks above 1.0).
  - **Mono files come out as left = signal, right = silence**, not copied to both channels. `MonoMixer` then
    takes the left channel, but loudness measures a silent right channel: a mono file reads about 3 dB quieter
    than the same audio as stereo. Keep it (results must not change); fixing it is a separate decision.
  - AAC and Opus keep the encoder's priming samples (FFmpeg 2.8 didn't trim them): decode with
    `flags2=+skip_manual`; Opus then also needs its end trimmed to Essentia's length (trimmed length + 312).
  - A damaged packet is skipped, as FFmpeg does, and decoding goes on.

  Results: **468 of the 469 collection tracks, all 38 mixes, and every WAV, AIFF, FLAC, ALAC and MP3 test file
  (16/24/32-bit float, 48 kHz, mono) bit-identical**, with the same length and start. The exception is one MP3
  with about 1 KB of junk after its cover art: FFmpeg 2.8 decodes one extra 1152-sample frame (26 ms) from it,
  current FFmpeg skips it; every sample after that frame is identical. AAC, Vorbis and Opus: same length,
  about half the values differ in the last bit (at most 3.6e-7, about -130 dB), from the rewritten decoders.
  The 5.1 FLAC was left out (`AudioLoader` refuses it; it goes through the ffmpeg CLI already).
- *Neural nets*: Python `tensorflow==2.15.0`, fed the exact tensors Essentia's TensorFlow got, on 59 tracks
  (every 8th of the collection): **EffNet, all 10 heads (genre, 5 moods, approachability, engagement,
  instruments, voice) and MAEST (predictions and embeddings) bit-identical**, about 267 million values. One
  graph run per tensor, as the analyzer does. TF 2.15 has wheels for macOS arm64 (macOS 12+) and x86_64,
  Linux x86_64 and aarch64, and Windows x86_64, for Python 3.9-3.11 only.

**Stage 1: mono mix and the 16 kHz resample** (Rust, small). `MonoMixer` is
`0.5*(L+R)`, rounded from double (mono files: the left channel), and libsamplerate's linear converter is a few
lines. After this, `load_audio` only calls Essentia to decode.

**Stage 2: decoding** with PyAV. Copy `AudioLoader`'s behaviour as stage 0 found it: float32 stereo at the
file's own rate, MP3 through the fixed-point decoder, mono in the left channel only, AAC/Opus untrimmed,
damaged packets skipped. The >2-channel files already go through the ffmpeg CLI (`_load_downmixed`);
PyAV's libswresample can do the same downmix, checked against the CLI. Also covers the Rubber Band output that
`stretch()` reads back.

Current FFmpeg is the target even where stage 0 found differences (one MP3; AAC, Vorbis and Opus). A newer decoder isn't more accurate to the
music: MP3 decoding is fixed by the standard, and versions differ by about one part in 10 million, far below
hearing. But FFmpeg 2.8 has years of known decoder vulnerabilities and nobody maintains it. So:
- Identical files (the whole test collection but one, all mixes): nothing to decide.
- Different files: run the full analysis with both decoders on the whole collection and the mixes, and compare per
  track: BPM, key, genre, colour, hype, and cue/loop/waveform positions. If nothing a user would see changes,
  switch, bump `CACHE_VERSION` and re-analyse once. If something does, list those tracks and decide before
  anything ships.
- Watch the MP3 start and end above all. Newer FFmpeg trims the encoder delay and padding more exactly, which
  moves the audio by up to about a thousand samples. The reference there is Traktor, not FFmpeg: the cue, loop
  and waveform code lines MP3s up with Traktor's timeline (`traktor.audio_offset`), measured with Essentia's
  decoding. If the start moves, re-measure that alignment against the Remix Deck captures, to the sample.
Building FFmpeg 2.8's decoders ourselves is the last resort, not the fallback.

**Stage 3: the mel front end** (Rust). `TensorflowInputMusiCNN` (512-sample frames, hop 256 at 16 kHz, 96 mel
bands, log compression) for EffNet and MAEST, plus EffNet's 128-frame patches and 64-patch batches
(`TensorflowPredictEffnetDiscogs`). The FFT is the hard part: Essentia uses FFTW with `FFTW_ESTIMATE` plans,
and only the same FFTW code gives the same bits. The `dsp/` crate builds FFTW 3.3.10 from source in its
build script (CMake, single precision, the same SIMD options as Essentia's build) on all four CI platforms.
The key and BPM ports reuse it.

**Stage 4: neural nets.** With stages 1–3, the nets get bit-identical input, so any difference comes from the
runtime alone.
- First choice: Python `tensorflow` 2.15, the same version Essentia bundles: bit-identical in stage 0. Costs:
  Python stays at 3.10-3.11 (no 3.12), and Apple Silicon needs macOS 12 (Essentia's pinned build runs on 11).
- onnxruntime 1.31 (graphs converted with `tf2onnx` 1.17, opset 17, CPU only) was tried on the same 59 tracks:
  **not identical** (with or without its graph optimisations). EffNet differs by up to 5e-6, the heads by up to
  4e-7, MAEST's predictions by up to 3e-6 and its embeddings by up to 5e-4 (fails the 1e-5 gate). Not faster
  either: MAEST 17% slower. Not used. Revisit only if TF 2.15 has to go (Python 3.12+, macOS 11), and then
  through the gate below with a re-analysis.
- Gate: every embedding and score within 1e-5 of Essentia's, and every result built from them identical on every
  track: top genre, top-5 styles in order, moods and instruments as stored (3 decimals), colour prediction, hype.
  Bit-identical needs no rescan; within the gate means bumping `CACHE_VERSION` and re-analysing (models
  retrained on the new numbers if the colour or genre models change).
- On a different CPU (Windows and Linux on x86_64) the nets' last bits differ from the Mac's whatever runtime
  is used. That's already true of Essentia on Linux today. The DSP stages don't have this problem.

**Stage 5: key** (Rust; about 2,500 lines of C++, much of it profile tables, parameters and the frame cutter
already ported for loudness: Blackman-Harris window, spectrum, spectral peaks, spectral whitening, HPCP, EDMA
profile correlation). Simpler than BPM and shares its spectral code, so it goes first.
Gate (decided): key and scale identical on every track and every mix timeline segment, and strength within
0.01, matching the BPM rule; the aim is bit-identical.

**Stage 6: BPM** (Rust; about 2,700 lines of C++ of its own on top of the shared spectral code: five onset
functions (RMS, complex, mel flux, beat emphasis, information gain), five Degara trackers, TempoTapMaxAgreement,
BPM from the beat intervals). Gate (decided): the whole-number BPM equals Essentia's on every track and every
mix segment, and confidence is within 0.01; the aim is bit-identical beats. Once it matches, run the five onset
functions on separate threads, kept only if the output stays identical.

**Stage 7: drop Essentia.** `track-analyzer-dsp` (prebuilt wheels) becomes a required dependency;
`essentia-tensorflow` moves to an optional dependency group used only by `dsp/check.py` on macOS and Linux;
`es()` and every fallback go. Before merging, run the end-to-end check from the loudness work: `analyze_file`'s
full output byte-identical with and without Essentia, on the whole collection and the mixes. Then `uv sync`
works on Windows, and the rest of this file (Traktor, paths, launcher) is the Windows work.

**What changes for users.** On macOS: nothing in the results; analysis gets faster (below). On Linux: the DSP
numbers become the Mac's. Essentia's Linux build doesn't fuse multiply-adds, so its loudness already differs
from the Mac's by up to ~0.1 LU (and BPM/key may differ in rare cases); the Rust code gives the same numbers on
every platform. The install swaps Essentia (with its own TensorFlow and FFmpeg 2.8) for Python TensorFlow
2.15 and PyAV: about the same size. Apple Silicon then needs macOS 12, and Python stays at 3.10-3.11.

**Risks, biggest first.**
1. *Decoder differences*: small after stage 0 (one MP3 in the test collection, plus AAC, Vorbis and Opus).
   Measured per track in stage 2.
2. *TF runtime bits*: none with TF 2.15 (stage 0). onnxruntime differs (stage 4) and isn't used.
3. *FFTW build in CI* on four platforms with the same options as Essentia's. Fiddly, but stages 3, 5 and 6
   check it to the bit.
4. *Size of the BPM port.* Large but mechanical; the check finds every slip.

### Rust for the parts that get rewritten

Where the time goes now, on an M1 Pro on mains power (each step timed alone, models loaded and warmed up;
7 tracks of 3–8.5 min from the test collection, and one 65-minute 48 kHz FLAC mix):

| Step | 3-min track | 5.5-min track | 8.5-min track | 65-min mix | What it is |
|---|---|---|---|---|---|
| BPM (`RhythmExtractor2013`) | 1.7 s | 3.1 s | 4.8 s | (per segment) | DSP, single-threaded C++ |
| MAEST | 2.7 s | 2.7 s | 2.7 s | 2.7 s | neural net, always 3 patches of 30 s; cached, so rescans skip it |
| Loudness (`LoudnessEBUR128`) | 0.8 s | 1.4 s | 2.2 s | 18.4 s | DSP, two IIR filters and windowed means |
| EffNet + styles | 0.8 s | 1.2 s | 1.5 s | 9.5 s | neural net |
| Decode + resample | 0.3 s | 0.6 s | 0.9 s | 26.0 s | FFmpeg, libsamplerate (a 48 kHz file is resampled twice) |
| Key | 0.07 s | 0.13 s | 0.20 s | (per segment) | DSP |
| Mood, instrument and extra heads | 0.07 s | 0.08 s | 0.09 s | 0.34 s | small neural nets on the embeddings |
| Timeline: BPM + key per 2-min segment | | | | 9.7 s | 33 segments over 5 processes |
| Cue bars + waveform (`cues._spectra`, `cues scan` only) | 0.6 s | 1.0 s | 1.6 s | | its own decode + numpy FFTs |

BPM grows with the track's length (about 0.55 s per minute of audio), loudness about 0.27 s per minute,
EffNet about 0.2 s per minute; MAEST is fixed. On a typical track the DSP (BPM, loudness, key) is about half of
the analysis and the neural nets a little less. On a mix, decoding, resampling and loudness are two thirds.

Rust doesn't make the neural nets faster: TensorFlow is already optimised C++ (and onnxruntime, which Rust's
`ort` crate wraps, was no faster here; stage 4). The DSP is where Rust helps, and the rewrite needs new code for most of it anyway. Rust is
the natural language for that code: it runs as fast as C++, and PyO3 + maturin build one wheel per platform
(Windows, macOS, Linux) from one source, in CI, without the build pains that rule out Essentia on Windows.

Candidates, best first:
- **BPM port** (3.1 s on a typical track, the biggest single step). Must be ported for Windows anyway. In Rust it runs at least as fast as Essentia; the five
  onset functions are independent and can run on separate threads (Essentia runs them one after another).
  Mixes gain the most: the rhythm runs again for every 2-minute timeline segment.
- **Loudness: done** (`dsp/`, `track_analyzer_dsp.loudness_ebur128`, used by `analysis.loudness()`). A line-by-line
  copy of Essentia's LoudnessEBUR128: the same float32 4th-order IIR, frame cutting and in-order sums, with the
  3 s / 400 ms windows computed in parallel. Bit-identical to Essentia on all 469 tracks of the test collection
  and 3 mixes at 48 kHz (every short-term value, integrated and range); 6.5–7.5x faster (1.4 s → 0.2 s per track,
  18 s → 2.5 s per mix). Without the module, Essentia does it.
  The match needs fused multiply-adds where Essentia's arm64 build has them (the filter coefficients and
  `b*x - a*y` in the filter loop); the plain-maths version is off by up to 0.12 LU, because the float32
  4th-order filter is that sensitive. So Essentia's own Linux x86_64 build (no FMA) likely differs from the Mac
  by that much already; the Rust module gives the Mac's numbers on every platform (`mul_add` is exact
  everywhere; on x86_64 it may call the C library's `fmaf`, check its speed in CI).
- **Key port** (0.17 s). Already fast; Rust only so it sits with the BPM code (shared spectral peaks).
- **The mel front end** for EffNet and MAEST. Small today (0.06 s for MAEST's); numpy is fine. Rust only if it
  has to share code with the rest.
- **Resampling: done** (`track_analyzer_dsp.resample_sinc_medium`, used by `analysis.resample_medium()` for the
  44.1 kHz copy in `load_audio` and the cue code). A port of libsamplerate 0.2.2's medium sinc as Essentia calls
  it (`src_simple`, the whole signal at once). libsamplerate walks the input once through a ring buffer; that
  walk is replayed step for step (positions only) on one thread while the output samples, each a dot product
  that depends only on its position, run on the other cores. Bit-identical to Essentia on every 48 kHz file
  (a track and the mix recordings) and on test signals at 8-192 kHz, up and down, 1 sample to 37 s. A 65-min mix:
  19.7 s → 3.8 s. Fused or plain multiply-adds give the same output here (the sums are double, rounded to float
  at the end), so the plain form is used. The 16 kHz copy for the models (linear, 0.6 s on a mix) stays Essentia's.
- **Cue spectra** (1.0 s). Mostly the second decode and FFTs; decoding once and sharing the audio with the
  analysis saves more than Rust would (plain Python change). `realfft` in Rust would only make the FFTs faster.

Don't use Rust for these:
- **Decoding** with `symphonia`: its MP3 decoder and gapless handling aren't ffmpeg's, so samples and the
  Traktor timeline offsets would change. Keep ffmpeg.
- **Resampling** with `rubato`: different filters from libsamplerate, so the models get different input.
  libsamplerate's own algorithm is copied instead (above).

Wins that need no Rust, with Python TensorFlow (stage 4); each checked for identical results before use:
- **One model copy per scan.** Each `scan` worker is a process holding its own models (1.5–2.5 GB with MAEST),
  which is why the worker count is capped at 5. A TensorFlow session can be shared by threads in one process
  (`Session.run` releases the GIL), and the Rust DSP code runs outside the GIL too. Then the models load once
  and more tracks run at a time on the same RAM. **Verify** the outputs stay bit-identical with runs in parallel.
- **MAEST in one batch** isn't possible with this graph (batch size fixed at 1); the 3 patches stay one after
  another. onnxruntime could re-export it with a free batch size, but it isn't identical (stage 4).
- **Not** GPU / Neural Engine back ends (Metal, Core ML, DirectML): they may compute in float16 and change the
  scores.

Rules, as for every speed-up here: results identical to before, or within float rounding and checked over the
whole test collection (BPM and key exactly equal per track; LUFS and scores within float tolerance). A
different summation order can tip a peak pick in rare cases, so compare per track, not averages. Nobody needs
Rust to install: `.github/workflows/dsp-wheels.yml` builds wheels for macOS (arm64, x86_64), Linux x86_64 and
Windows x86_64 and attaches them to a release on a `dsp-v*` tag; `pyproject.toml` installs those (the `dsp` index).

## Traktor

Traktor runs on Windows (not on Linux), so the Traktor half of the tool is what a port really adds.

- **Collection location.** `traktor.find_collection()` globs `~/Documents/Native Instruments/Traktor*/collection.nml`.
  On Windows that's the same relative to the user folder, so `expanduser` finds it (**verify**: OneDrive can move
  Documents to `~/OneDrive/Documents`, and may keep files there as online-only placeholders). History files
  (`tracklist.HISTORY_GLOB`) and recordings (`traktor.recording_dirs()`) use the same base.
- **Track paths.** `traktor.location_paths()` turns an NML `LOCATION` (`VOLUME`, `DIR="/:Users/:me/:Music/:"`, `FILE`)
  into `/Users/…` or `/Volumes/<VOLUME>/…`. On Windows `VOLUME` is a drive letter (`VOLUME="C:"`, **verify** the
  exact form and `VOLUMEID`), so the path is `C:\Users\me\Music\x.mp3`. Every lookup between Traktor's entries
  and `collection.db` paths goes through this, so get it right first; a wrong mapping means no track matches.
  Network drives (`\\server\share`) need a rule too.
- **Case.** Windows paths ignore case; the code compares paths as exact strings. If Traktor stores
  `Music\House` and the folder is `music\house`, the track doesn't match. Compare with `os.path.normcase`
  (or a casefolded key column) on Windows.
- **Settings paths.** `traktor.mac_path()` converts the `.tsi`'s colon paths (`Macintosh HD:Users:Shared:…`). Windows
  Traktor's form is unknown (**verify**: probably `C:\Users\…` or a colon form with a drive letter). Used for
  the sample folder (`web.sample_root()`) and the folders `scan --traktor` skips.
- **Is Traktor running?** `traktor.traktor_running()` uses `pgrep`. On Windows that command doesn't exist, the
  call fails, and the function returns False: every writer would then edit `collection.nml` while Traktor is
  open, and Traktor overwrites it on quit. Use `tasklist /FI "IMAGENAME eq Traktor.exe"` (**verify** the process
  name), and make a failed check refuse to write rather than assume Traktor is closed. Fix this first.
- **NML text.** The NML is read and written with `Path.read_text()` / `write_text()` without an encoding in many
  places (18 at the time of writing). Python on Windows defaults to the ANSI code page, so accented
  names would be garbled or the write would fail. Pass `encoding="utf-8"` everywhere, or run Python in UTF-8
  mode (`PYTHONUTF8=1`, set in the launcher).
- **Line endings.** `read_text()` turns `\r\n` into `\n` and writing puts `\n` back. If Windows Traktor writes CRLF
  (**verify**), every write would change every line ending and the backup/undo SHA-1 check (`.written-sha1`) would
  report every file as changed. Read and write with `newline=""` so the edits stay surgical.
- **Replacing files.** `os.replace` (the `.tsi` write, `traktor.py`) fails on Windows if another program holds the
  file open without delete sharing: antivirus, OneDrive, maybe Traktor itself. Test with Traktor closed and open;
  a failed replace must leave the original untouched and say why.
- **MODIFIED stamps.** `traktor.file_stamp()` uses the file's mtime; NTFS mtimes are fine (**verify** that the
  seconds match what Traktor writes).

## Paths inside the code

Paths are stored as `str(Path)` strings and compared as strings. On Windows that means backslashes, and
several places assume `/`:

- `collection.py` folder matching: `path.startswith(r.rstrip("/") + "/")` (in `_under`, the scan's exclude list,
  the "gone" check), `jobs.py` skip check (`d + "/"`).
- `collection._missing_for_good()` knows macOS `/Volumes/<disk>` and Linux `/media`, `/run/media`, `/mnt` for
  unplugged drives. Add drive letters: a missing `E:\` drive means unplugged, not deleted. Network shares too.
- `collection.nfc()` composes accented names only where a file has that name (NTFS doesn't normalise),
  so it already behaves on Windows.
- `clips.py` puts previews in `/tmp` when it exists, else the system temp folder: fine.
- Paths over 260 characters need long-path support turned on in Windows.

## The web page's Setup tab

- **Stopping a job.** `jobs.py` starts each job with `start_new_session=True` and stops it with `os.killpg(…, SIGINT)`,
  then SIGTERM. Windows has neither. Start with `creationflags=CREATE_NEW_PROCESS_GROUP` and send
  `CTRL_BREAK_EVENT`; the scans' safe stop listens for SIGINT, so they'd need a SIGBREAK handler too.
- **Installing tools.** The Setup tab installs ffmpeg and rubberband with Homebrew; without it, it shows
  `analysis.install_hint()`. Add a Windows hint (`winget install ffmpeg`; Rubber Band has Windows builds at
  breakfastquay.com/rubberband, needs 3.0+ for the R3 engine) or run winget from `_tool_install`.
  `analysis.find_tool()` looks in Homebrew folders; add the winget install location.
- **Show in Explorer** (`web._show`) already uses `explorer /select,` on Windows.

## Launcher

`Start Track Analyzer.command` is bash. Add a `Start Track Analyzer.bat` (or `.ps1`) that installs uv
(`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`), runs `uv sync` and `uv run dj-track-analyzer web`,
and sets `PYTHONUTF8=1`.

## Lexicon

Lexicon has a Windows version (**verify**). `lexicon.py` needs:
- its database path for the backup before each load (now `~/Library/Application Support/lexicon/main.db`;
  on Windows probably under `%APPDATA%\lexicon`, **verify**);
- the location prefix it strips (now `/Volumes/<disk>`; Windows Lexicon likely sends `C:\…`, **verify**) so
  Lexicon's tracks match `collection.db` paths.
The Local API itself (localhost:48624) is the same.

## Order of work

1. **Replace Essentia, on the Mac**, in the stages above: probes, mono mix and 16 kHz resample, decoding, mel
   front end, neural nets, key, BPM, then drop Essentia. Each checked against Essentia over the whole test
   collection and the mixes before it ships. Ship on macOS and Linux before Windows, so there is one engine
   everywhere.
2. **Traktor-running check that fails closed** (`tasklist`), before anything writes on Windows.
3. On a Windows machine with Traktor: copy `collection.nml` and `Traktor Settings.tsi` and note the `LOCATION`,
   `Browser.Dir.*` forms and line endings. Settle every **verify** above from real files.
4. Path mapping (`location_paths`, `mac_path`, collection search), case-insensitive matching, the `/` assumptions,
   drive letters for unplugged drives, UTF-8 and `newline=""` on every NML read and write.
5. Run `traktor --dry-run`, then a real write + `traktor --undo` on a copy of the collection, with Traktor closed
   and then open (it must refuse).
6. Setup tab (job stop, tool installs), launcher, Lexicon.

## Stopgap: WSL2

Ubuntu under WSL2 runs the Linux build with Essentia unchanged, so the analysis results are identical and
nothing in "The blocker" is needed. It is not harder for Traktor than native: the same path mapping, with
`/mnt/c/…` in place of `C:\…`. But it has costs:

- **Reaching the Windows side.** The collection is under `/mnt/c/Users/<windows user>/Documents/Native Instruments/`
  (user name from `cmd.exe /c echo %USERNAME%` or `wslvar USERNAME`), not `~`. `pgrep` sees only Linux processes,
  so the Traktor check must call `tasklist.exe`; Show in Explorer needs `explorer.exe` with `wslpath -w`. The case,
  line-ending and `os.replace` points above apply too.
- **Slow file access.** `/mnt/c` is a network-style share into the VM: many times slower than the Linux disk for
  listing and checking files, and Windows antivirus scans each read. `scan` (stat on every file) gets clearly
  slower on a big library; analysis barely notices (one read against 10–15 s of compute).
- **Drives.** Fixed drives appear as `/mnt/<letter>`; a USB drive plugged in after WSL started may need
  `sudo mount -t drvfs E: /mnt/e`, and network shares always need mounting. An unmounted drive looks unplugged,
  so its tracks are kept (safe, but confusing). A helper that mounts every drive letter before a scan would hide this.
- **Setup.** Turning on WSL needs admin rights and a reboot, sometimes virtualization in the BIOS; some work PCs
  block it. Ubuntu 24.04 or newer (22.04's Rubber Band is older than 3.0). The launcher is a `.bat` calling
  `wsl -- ./"Start Track Analyzer.command"`, and errors show in a Linux terminal.
- **Resources.** WSL2 gets half the RAM by default (`.wslconfig` changes it): tight on 8 GB with Traktor open.
  The distro's disk image grows to several GB and doesn't shrink on its own.
- **MCP.** Claude Desktop on Windows would start the server as `wsl.exe -- uv run …`; works, one more layer to fail.
- **Clock.** After sleep, WSL's clock can lag Windows' for a while; the NML-changed checks and MODIFIED stamps use it.
- The web page on 127.0.0.1:8766 opens in the Windows browser (WSL2 forwards localhost), and the Lexicon API is
  reachable on localhost.
