# Where the code in track-analyzer-dsp comes from

This module re-implements parts of three open-source libraries in Rust, so that it gives their results (where its maths is more exact, within a float step).
The ports follow the original code closely, so they are derivative works of it. They are used under these
libraries' own licences, which allow this, and the whole module is released under the GNU Affero General Public
License, version 3 (`LICENSE`).

## Essentia

Copyright (C) 2006-2021 Music Technology Group, Universitat Pompeu Fabra. <https://essentia.upf.edu>,
<https://github.com/MTG/essentia>. GNU AGPL, version 3 or (at your option) any later version.

Ported from Essentia 2.1b6.dev1177 (commit 1d1cc983): LoudnessEBUR128, Resample, TensorflowInputMusiCNN (with
FrameGenerator, Windowing, Spectrum and MelBands), KeyExtractor (with FrameCutter, SpectralPeaks,
SpectralWhitening, HPCP and Key), and RhythmExtractor2013 in multifeature mode (with its onset detection
functions, TempoTapDegara, TempoTapMaxAgreement and AutoCorrelation). Files: `src/lib.rs` (loudness), `src/musicnn.rs`,
`src/key.rs`, `src/bpm.rs` and the tables generated from Essentia by `tools/`.

## FFTW

Copyright (c) 2003, 2007-14 Matteo Frigo and Massachusetts Institute of Technology. <https://www.fftw.org>.
GNU GPL, version 2 or (at your option) any later version (`licenses/FFTW-COPYING`). Used here under version 3,
which may be combined with the GNU AGPL version 3 (GPL-3.0, section 13).

`src/fftw_codelets.rs` is translated from FFTW 3.3.10's scalar codelets by `tools/gen_fftw_codelets.py`.
`src/fft512.rs`, `src/fft4096.rs` and `src/fftw.rs` follow FFTW 3.3.10's plans (as `FFTW_ESTIMATE` makes them)
and its twiddle-factor code.

## libsamplerate

Copyright (c) 2002-2021 Erik de Castro Lopo. <https://github.com/libsndfile/libsamplerate>. 2-clause BSD licence
(full text at the top of `src/mid_qual_coeffs.rs`).

`src/resample.rs` follows libsamplerate 0.2.2's SRC_SINC_MEDIUM_QUALITY and SRC_LINEAR converters; `src/mid_qual_coeffs.rs` is its filter table.

## Rust crates

The compiled module also contains code from the crates in `Cargo.lock` (pyo3, rayon and others), under MIT,
Apache-2.0 and Unicode-3.0. Their licence texts are in `licenses/THIRD-PARTY-RUST.md`, made by `tools/gen_third_party.py`
(run it again after changing dependencies).
