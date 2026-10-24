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

### What Essentia does here, and what replaces it

| Job | Essentia now | Replacement | Results |
|---|---|---|---|
| Decode, mono, resample | `AudioLoader`, `MonoMixer`, `Resample` (analysis.py `load_audio`, cues.py, `cues.WaveSource`) | ffmpeg to f32 + `samplerate` (libsamplerate) | identical (check) |
| Neural models | `TensorflowPredictEffnetDiscogs`, `TensorflowPredict2D`, `TensorflowPredict` (MAEST), `TensorflowInputMusiCNN` | onnxruntime + a numpy mel front end | identical (check) |
| Loudness | `LoudnessEBUR128` | numpy EBU R128 or ffmpeg `ebur128` | within ~0.1 LU |
| BPM | `RhythmExtractor2013(method="multifeature")` | port, or a different tracker | port: close; other: different |
| Key | `KeyExtractor(profileType="edma")` | port, or a different detector | port: close; other: different |

**Decoding and resampling.** `AudioLoader` is FFmpeg underneath (the decoded samples were checked equal to the
ffmpeg CLI's, see `traktor.audio_offset`), so ffmpeg to `pcm_f32le` gives the same audio. `Resample`'s `quality`
is libsamplerate's converter type (1 = sinc medium, used for 44.1 kHz; 4 = linear, used for the models' 16 kHz,
as Essentia's model docs do); the `samplerate` package wraps the same library. `MonoMixer` is (L+R)/2.
Watch the MP3 start: the cue, waveform and loop code shift MP3s onto Traktor's timeline assuming Essentia's
handling of the encoder delay. Check the replacement against the existing Remix Deck captures, to the sample.
Speed: the same.

**Neural models.** The `.pb` files are frozen TensorFlow graphs and don't depend on Essentia. Convert them with
`tf2onnx` (Essentia's model site has `.onnx` versions of some, **verify** which) and run them with onnxruntime,
which has wheels for Windows, macOS and Linux. Plain `tensorflow` has Windows CPU wheels too (**verify** for the
pinned Python), but it is the heavier choice. What must be rewritten:
- `TensorflowInputMusiCNN` (512/256 frames at 16 kHz, 96 mel bands, log compression): port its exact settings
  from Essentia's source to numpy. EffNet and MAEST both read it.
- `TensorflowPredictEffnetDiscogs`'s patching (128-frame patches; the `bs64` graph takes fixed batches of 64,
  padded) and the output it returns. `_maest_patches` already does the same for MAEST and was checked
  identical to Essentia's wrapper (max diff 2e-7): do the same check here, over the whole test collection.
- The heads (`TensorflowPredict2D`) are plain matrix runs on the embeddings.

If the embeddings match, nothing downstream changes. If they drift, the drift reaches genre, moods, instruments,
the colour model, `my_genres.joblib`, hype and every cached embedding: retrain and rescan, so get the match first.
Speed: onnxruntime is usually as fast as or faster than TF on CPU, loads in well under a second (no TF warm-up
on the first track) and is a far smaller install. MAEST (a fixed 2.7 s, a third of a short track's analysis)
may get faster. Measure.

**Loudness.** EBU R128 is a standard; integrated loudness and LRA from any correct implementation agree within
about 0.1 LU. Match Essentia's short-term window (3 s) and hop (0.1 s), since `energy_curve`, `energy_arc`, hype
and the mix highlights read the short-term values. Speed: the same.

**BPM and key.** No library gives Essentia's numbers. Two ways:
- **Port Essentia's algorithms** (numpy, or a small Rust/C extension for speed). `KeyExtractor` is moderate
  (spectral peaks → HPCP → EDMA profile correlation); `RhythmExtractor2013` multifeature is large (five onset
  functions, Degara tracking, TempoTapMaxAgreement). Expect close, not bit-exact. Pure numpy is several times
  slower than Essentia's C++, and the rhythm runs again per timeline segment on mixes. Essentia is AGPL-3.0, so a
  line-by-line port carries that licence.
- **Use another library.** Results change; re-measure against Traktor on the test collection (today 96% of BPMs
  within 1 BPM, key agreement 57%).
  - librosa (`beat_track`, chroma key): fast, installs anywhere; expect more half/double-tempo errors and
    weaker keys. Not good enough.
  - madmom (RNN/DBN beats, CNN key): often as good as or better on electronic music, but several times slower
    and barely maintained; awkward to install on Windows with current Python.
  - beat_this (2024): strong beats, slow on CPU, needs PyTorch.
  - libKeyFinder (key only, C++): built for DJ music; needs its own Windows build.
  A new key detector changes the Camelot keys written to tags and Traktor, and `tracklist`'s key matching.

Whatever changes: bump `CACHE_VERSION` when the BPM, key or embeddings change, and re-analyse (10–15 min for the
test collection, about 30 with `--rescan`). Dropping essentia-tensorflow and TensorFlow also removes most of
the install size, and may lift the `numpy<2` and Python `<3.13` pins (**verify** the other dependencies).

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

Rust doesn't make the neural nets faster: onnxruntime is already optimised C++, and Rust's `ort` crate only
wraps it. The DSP is where Rust helps, and the rewrite needs new code for most of it anyway. Rust is
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

Wins that need no Rust, from moving to onnxruntime:
- **MAEST in one batch.** The graph has batch size 1, so the 3 patches run one after another. A dynamic-batch
  ONNX export runs them together, with the same maths.
- **One model copy per scan.** Each `scan` worker is a process holding its own models (1.5–2.5 GB with MAEST),
  which is why the worker count is capped at 5. onnxruntime sessions can be shared by threads in one process
  (inference runs outside the GIL), and Rust DSP code can run outside the GIL too. Then the models load once and
  more tracks run at a time on the same RAM.
- **Not** the Core ML or DirectML (GPU / Neural Engine) providers: they may compute in float16 and change the
  scores.

Rules, as for every speed-up here: results identical to before, or within float rounding and checked over the
whole test collection (BPM and key exactly equal per track; LUFS and scores within float tolerance). A
different summation order can tip a peak pick in rare cases, so compare per track, not averages. The cost: anyone
who runs from the repo needs the Rust toolchain unless prebuilt wheels are published. `pyproject.toml` builds
`dsp/` from source for now; `.github/workflows/dsp-wheels.yml` builds wheels for macOS (arm64, x86_64), Linux
x86_64 and Windows x86_64, and attaches them to a release on a `dsp-v*` tag. Point `uv` at those before
anyone else needs to install it.

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

1. **Replace Essentia, on the Mac.** Decoding, resampling, loudness and the neural models first, each checked
   against Essentia over the whole test collection (embeddings to float precision, MP3 offsets to the sample,
   LUFS within 0.1). Then decide BPM and key: port (in Rust, see above), or compare a library against today's
   96% / 57%. Ship it on macOS and Linux before Windows, so there is one engine everywhere.
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
