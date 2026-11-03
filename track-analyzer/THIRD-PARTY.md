# Third-party software

Puppi3's Track Analyzer is free software under the GNU AGPL, version 3 ([`LICENSE`](../LICENSE)). It builds on the
open-source work below, used under each project's own licence. All of these licences can be combined with the AGPL v3.

## Code and data shipped with this project

| What | Where | By | Licence |
|---|---|---|---|
| Ports of [Essentia](https://essentia.upf.edu) (loudness, resampling, mel bands, key, BPM) | `dsp/src/` | Music Technology Group, Universitat Pompeu Fabra | AGPL-3.0-or-later |
| Port of [FFTW](https://www.fftw.org) 3.3.10's codelets and plans | `dsp/src/` | Matteo Frigo and MIT | GPL-2.0-or-later, used as GPL-3.0 ([`dsp/licenses/FFTW-COPYING`](dsp/licenses/FFTW-COPYING)) |
| Port of [libsamplerate](https://github.com/libsndfile/libsamplerate) 0.2.2 | `dsp/src/resample.rs`, `dsp/src/mid_qual_coeffs.rs` | Erik de Castro Lopo | BSD-2-Clause |
| Rust crates compiled into the DSP module (pyo3, rayon, crossbeam, libm and others) | `dsp/` wheel | their authors | MIT, Apache-2.0, Unicode-3.0 ([`dsp/licenses/THIRD-PARTY-RUST.md`](dsp/licenses/THIRD-PARTY-RUST.md)) |
| [MusicalKeyCNN](https://github.com/a1ex90/MusicalKeyCNN) key model, code and weights (design: KeyNet, Korzeniowski & Widmer, ISMIR 2018) | `keycnn.py`, `starter/keynet.npz` | © 2025 Alexander Sommer | MIT ([`starter/keynet.LICENSE`](src/dj_track_analyzer/starter/keynet.LICENSE)) |

Details of the ports, with exact versions and files: [`dsp/NOTICE.md`](dsp/NOTICE.md).

## Models downloaded on first use

| What | By | Licence |
|---|---|---|
| [Essentia's models](https://essentia.upf.edu/models/): EffNet-Discogs, MAEST and the style, mood and instrument heads on them | Music Technology Group, Universitat Pompeu Fabra | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/): **non-commercial use only** (MTG licenses them for other uses) |
| [Demucs](https://github.com/facebookresearch/demucs) htdemucs weights (only with the `stems` extra) | Meta Platforms, Inc. (Alexandre Défossez) | MIT |

## Python packages installed with it

These are installed by `uv`/`pip` from PyPI, not shipped in this repository; each carries its own licence.

| Package | Used for | Licence |
|---|---|---|
| [TensorFlow](https://www.tensorflow.org) | running the models | Apache-2.0 |
| [librosa](https://librosa.org) | CQT and chroma for the key model | ISC |
| [NumPy](https://numpy.org) | arrays | BSD-3-Clause |
| [scikit-learn](https://scikit-learn.org), [joblib](https://joblib.readthedocs.io) | colour and cue models | BSD-3-Clause |
| [PyAV](https://github.com/PyAV-Org/PyAV) (bundles [FFmpeg](https://ffmpeg.org)) | decoding audio | BSD-3-Clause; FFmpeg LGPL-2.1-or-later |
| [mutagen](https://github.com/quodlibet/mutagen) | reading and writing tags | GPL-2.0-or-later |
| [pyrekordbox](https://github.com/dylanljones/pyrekordbox) (with [sqlcipher3-wheels](https://github.com/laggykiller/sqlcipher3)) | Rekordbox's library | MIT (sqlcipher3: zlib) |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | the MCP server | MIT |
| [Demucs](https://github.com/facebookresearch/demucs), [PyTorch](https://pytorch.org) (`stems` extra) | vocals and synths in loops | MIT; BSD-3-Clause and others |
| [Essentia](https://essentia.upf.edu) (`check` group) | reference the Rust port is tested against | AGPL-3.0 |
| [pytest](https://pytest.org) (`test` group) | tests | MIT |

## Fonts

The web page loads [Archivo](https://fonts.google.com/specimen/Archivo) and
[JetBrains Mono](https://www.jetbrains.com/lp/mono/) from Google Fonts, both under the
[SIL Open Font License 1.1](https://openfontlicense.org).
