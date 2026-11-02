//! Essentia's KeyExtractor(profileType="edma", sampleRate=44100), its other parameters at their defaults, over a whole
//! 44.1 kHz mono signal: the streaming network FrameCutter (4096/4096) -> Windowing (Hann, normalized, zero-phase) ->
//! Spectrum (FFTW) -> SpectralPeaks -> SpectralWhitening -> HPCP (12 bins) -> Key (streaming: the mean HPCP).
//! Each step follows the C++ (Essentia 2.1b6.dev1177) operation for operation, in float where it is float and in
//! double where C++ promotes to double, with clang's contractions (`a*b + c` within one expression becomes one
//! fused multiply-add); its libm calls (pow(10, x), log, log10, cos) are computed in double and rounded (math.rs), the
//! nearest float, where Essentia's macOS build is a float step off on some inputs. Frames are independent, so they run in parallel; their HPCPs are then summed in order, as meanFrames does.
//!
//! Silent frames: the streaming FrameCutter adds unseeded noise (-100 dB) to a frame whose mean power is below
//! 1e-10. It can't matter here: such a frame's spectrum stays below 5e-5 (|X| <= ||x|| * ||w|| < 6.4e-4 * 0.038,
//! noise included), under SpectralPeaks' 1e-4 threshold, so the frame has no peaks and a zero HPCP either way.

use crate::fft4096::rfft4096;
use crate::key_tables::{HANN4096, HARMONICS};
use crate::math::{cos, exp10f, log10f, log2f};
use rayon::prelude::*;
use std::f64::consts::PI;

const FRAME: usize = 4096;
const HOP: usize = 4096;
const BINS: usize = FRAME / 2 + 1;
const RANGE: f32 = 22050.0; // SpectralPeaks: sampleRate / 2
const MIN_FREQ: f32 = 25.0;
const MAX_FREQ: f32 = 3500.0;
const PEAK_THRESHOLD: f32 = 0.0001;
const MAX_PEAKS: usize = 60;
const PCP_THRESHOLD: f32 = 0.2;

pub const KEYS: [&str; 12] = ["A", "Bb", "B", "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab"];
// Key's edma profiles (profileTypesWithOther[6], [7]): major, minor
const EDMA_MAJOR: [f32; 12] = [1.00, 0.29, 0.50, 0.40, 0.60, 0.56, 0.32, 0.80, 0.31, 0.45, 0.42, 0.39];
const EDMA_MINOR: [f32; 12] = [1.00, 0.31, 0.44, 0.58, 0.33, 0.49, 0.29, 0.78, 0.43, 0.29, 0.53, 0.32];

/// HPCP's weight: (float)cos(M_PI * d), d in [0, 0.5].
fn cospi(d: f32) -> f32 {
    cos(PI * d as f64)
}

/// essentiamath's lin2db: 10 * log10f(x), -100 below 1e-10.
fn lin2db(x: f32) -> f32 {
    if (x as f64) < 1e-10 { -100.0 } else { 10.0 * log10f(x) }
}

/// Start of each frame: the first is centred on sample 0, then every HOP while the start is inside the signal (the
/// streaming FrameCutter with startFromZero false; it also cuts the frame whose centre is past the end).
pub fn frame_starts(n: usize) -> Vec<isize> {
    let mut out = Vec::new();
    let mut start = -((FRAME as isize + 1) / 2);
    while (start < n as isize) && n > 0 {
        out.push(start);
        start += HOP as isize;
    }
    out
}

/// The frame's buffers live on the heap and this isn't inlined: inlined into rayon's recursive splitting, ~60 KB of
/// arrays per level overflowed a worker thread's 2 MB stack on long files.
#[inline(never)]
fn spectrum(x: &[f32], start: isize) -> Box<[f32; BINS]> {
    let mut fr = Box::new([0f32; FRAME]);
    for (k, v) in fr.iter_mut().enumerate() {
        let i = start + k as isize;
        if i >= 0 && (i as usize) < x.len() {
            *v = x[i as usize];
        }
    }
    let win = |i: usize| f32::from_bits(HANN4096[i]);
    let mut w = Box::new([0f32; FRAME]);
    for k in 0..FRAME / 2 {
        w[k] = fr[FRAME / 2 + k] * win(FRAME / 2 + k); // zero-phase: the second half first
        w[FRAME / 2 + k] = fr[k] * win(k);
    }
    let mut c = Box::new([0f32; 2 * BINS]);
    rfft4096(&w, &mut c);
    let mut s = Box::new([0f32; BINS]);
    for k in 0..BINS {
        let (re, im) = (c[2 * k], c[2 * k + 1]);
        s[k] = re.mul_add(re, im * im).sqrt();
    }
    s
}

/// PeakDetection::interpolate: (value, bin).
fn interpolate(l: f32, m: f32, r: f32, bin: i32) -> (f32, f32) {
    let dx = (0.5 * ((l - r) / (l - 2.0 * m + r)) as f64) as f32;
    let val = (-(0.25 * (l - r) as f64)).mul_add(dx as f64, m as f64) as f32;
    (val, bin as f32 + dx)
}

/// SpectralPeaks (PeakDetection with interpolation, range 22050, 25-3500 Hz, threshold 1e-4), ordered by magnitude
/// (ties: lower frequency first), at most 60: (frequency, magnitude).
fn peaks(a: &[f32; BINS]) -> Vec<(f32, f32)> {
    let size = BINS as i32;
    let scale = RANGE / (size - 1) as f32;
    let at = |i: i32| a[i as usize];
    let mut p: Vec<(f32, f32)> = Vec::new();
    let mut i = 0.max((MIN_FREQ / scale).ceil() as i32);
    if i + 1 < size && at(i) > at(i + 1) && at(i) > PEAK_THRESHOLD {
        p.push((i as f32 * scale, at(i)));
    }
    loop {
        while i + 1 < size - 1 && at(i) >= at(i + 1) {
            i += 1;
        }
        while i + 1 < size - 1 && at(i) < at(i + 1) {
            i += 1;
        }
        let mut j = i;
        while j + 1 < size - 1 && at(j) == at(j + 1) {
            j += 1;
        }
        if j + 1 < size - 1 && at(j + 1) < at(j) && at(j) > PEAK_THRESHOLD {
            let (val, bin) = if j != i {
                (at(i), ((i + j) as f64 * 0.5) as f32)
            } else {
                interpolate(at(j - 1), at(j), at(j + 1), j)
            };
            let pos = bin * scale;
            if pos > MAX_FREQ {
                break;
            }
            p.push((pos, val));
        }
        i = j;
        if i + 1 >= size - 1 {
            if i == size - 2 && at(i - 1) < at(i) && at(i + 1) < at(i) && at(i) > PEAK_THRESHOLD {
                let (val, bin) = interpolate(at(i - 1), at(i), at(i + 1), j);
                p.push((bin * scale, val));
            }
            break;
        }
    }
    let pos = MAX_FREQ / scale;
    if ((size - 2) as f32) < pos && pos <= (size - 1) as f32 && at(size - 1) > at(size - 2) && at(size - 1) > PEAK_THRESHOLD {
        p.push(((size - 1) as f32 * scale, at(size - 1)));
    }
    p.sort_by(|x, y| y.1.partial_cmp(&x.1).unwrap().then(x.0.partial_cmp(&y.0).unwrap()));
    p.truncate(MAX_PEAKS);
    p
}

/// SpectralWhitening (maxFrequency 3500, so 4200 inside): the peaks' magnitudes, whitened.
fn whiten(s: &[f32; BINS], p: &[(f32, f32)]) -> Vec<f32> {
    if p.is_empty() {
        return Vec::new();
    }
    let max_freq = (3500.0f64 * 1.2) as f32;
    let range = (44100.0f64 / 2.0) as f32;
    let incr = 100.0f32;
    let db: Vec<f32> = p.iter().map(|&(_, m)| 2.0 * lin2db(m)).collect();
    let mut max_amp = -f32::MAX;
    for (k, &(f, _)) in p.iter().enumerate() {
        if f <= max_freq && max_amp < db[k] {
            max_amp = db[k];
        }
    }
    let spec = BINS as f64;
    let (mut xs, mut ys) = (Vec::new(), Vec::new());
    let mut freq = 0f32;
    while freq <= max_freq && freq <= range {
        let fd = freq as f64;
        let bf = (fd - if 50.0 < fd * 0.34 { fd * 0.34 } else { 50.0 }) as f32;
        let ef = (fd + if 50.0 < fd * 0.58 { fd * 0.58 } else { 50.0 }) as f32;
        let mut b = ((bf / range) as f64).mul_add(spec - 1.0, 0.5) as i32;
        let mut e = ((ef / range) as f64).mul_add(spec - 1.0, 0.5) as i32;
        b = b.max(0).min(BINS as i32 - 1);
        e = e.max(b + 1).min(BINS as i32);
        let c = (b as f64 / 2.0 + e as f64 / 2.0) as f32;
        let half = e as f32 - c;
        // clang vectorised this loop (4 floats x 4) with in-order sums: in whole blocks of 16 the product
        // energy*weight is rounded before it is added; the scalar tail fuses `wavg += energy*weight` (found against
        // SpectralWhitening on 550 frames).
        let vectorised = b + (e - b) / 16 * 16;
        let (mut n, mut wavg) = (0f32, 0f32);
        for i in b..e {
            let mut weight = (1.0 - ((i as f32 - c).abs() / half) as f64) as f32;
            weight *= weight;
            weight *= weight;
            let energy = s[i as usize] * s[i as usize];
            weight *= energy;
            wavg = if i < vectorised { wavg + energy * weight } else { energy.mul_add(weight, wavg) };
            n += weight;
        }
        if n != 0.0 {
            wavg /= n;
        }
        xs.push(freq);
        ys.push(wavg);
        freq += incr;
    }
    let k = ys.len();
    ys[k - 1] = ys[k - 2];
    for y in ys.iter_mut() {
        *y = 2.0 * lin2db(y.sqrt());
    }
    let slopes: Vec<f32> = (1..xs.len()).map(|j| (ys[j] - ys[j - 1]) / (xs[j] - xs[j - 1])).collect();
    let mut out = vec![0f32; p.len()];
    for (k, &(f, _)) in p.iter().enumerate() {
        let amp = db[k];
        if f > max_freq - incr {
            out[k] = amp;
        } else {
            let mut j = 0;
            while f > xs[j + 1] {
                j += 1;
            }
            let env = (f - xs[j]).mul_add(slopes[j], ys[j]);
            let w = if amp > env {
                0.0
            } else if amp as f64 > env as f64 - 30.0 {
                amp - env
            } else {
                -200.0
            };
            out[k] = (w as f64 - 20.0 * f as f64 / 4000.0) as f32;
        }
        out[k] = exp10f(((out[k] as f64 / 2.0) as f32) / 10.0);
    }
    out
}

/// HPCP's table of harmonics (initHarmonicContributionTable, 4 harmonics): (semitone, strength, exp2(-semitone/12)),
/// in its order; made on macOS (gen_key_tables.py), as its log and exp2 give them.
fn harmonic_peaks() -> Vec<(f32, f32, f64)> {
    HARMONICS.iter().map(|&(s, w, x)| (f32::from_bits(s), f32::from_bits(w), f64::from_bits(x))).collect()
}

/// HPCP (size 12, reference 440 Hz, cosine weights, window 1 semitone, 25-3500 Hz, no band preset, not normalized).
fn hpcp(p: &[(f32, f32)], mags: &[f32], harm: &[(f32, f32, f64)]) -> [f32; 12] {
    let mut h = [0f32; 12];
    let resolution = 1f32; // pcpSize / 12, an int division
    for (k, &(freq, _)) in p.iter().enumerate() {
        if !(freq >= MIN_FREQ && freq <= MAX_FREQ) {
            continue;
        }
        let mag = mags[k];
        for &(_semitone, hw, factor) in harm {
            let f = (freq as f64 * factor) as f32; // freq * pow(2., -semitone / 12.0)
            let bin = log2f(f / 440.0) * 12.0;
            let left = (bin as f64 - (resolution * 1.0) as f64 / 2.0).ceil() as i32;
            let right = (bin as f64 + (resolution * 1.0) as f64 / 2.0).floor() as i32;
            for i in left..=right {
                let distance = (bin - i as f32).abs() / resolution;
                let weight = cospi(distance / 1.0);
                let iw = i.rem_euclid(12) as usize;
                h[iw] = (weight * (mag * mag) * hw).mul_add(hw, h[iw]);
            }
        }
    }
    h
}

/// One frame's steps (spectrum, peak frequencies, peak magnitudes, whitened magnitudes, HPCP), for dsp/check.py.
pub fn frame_steps(frame: &[f32]) -> (Vec<f32>, Vec<f32>, Vec<f32>, Vec<f32>, Vec<f32>) {
    let s = spectrum(frame, 0);
    let p = peaks(&s);
    let w = whiten(&s, &p);
    let h = hpcp(&p, &w, &harmonic_peaks());
    (s.to_vec(), p.iter().map(|x| x.0).collect(), p.iter().map(|x| x.1).collect(), w, h.to_vec())
}

/// The HPCP of every frame (frame_starts(len) rows of 12).
pub fn hpcps(x: &[f32]) -> Vec<[f32; 12]> {
    let harm = harmonic_peaks();
    frame_starts(x.len())
        .par_iter()
        .with_min_len(32)
        .map(|&st| {
            let s = spectrum(x, st);
            let p = peaks(&s);
            let w = whiten(&s, &p);
            hpcp(&p, &w, &harm)
        })
        .collect()
}

fn normalize(v: &mut [f32; 12]) {
    let mut m = v[0];
    for &x in v.iter() {
        if m < x {
            m = x;
        }
    }
    if m != 0.0 {
        for x in v.iter_mut() {
            *x /= m;
        }
    }
}

fn mean(v: &[f32; 12]) -> f32 {
    let mut s = 0f32;
    for &x in v {
        s += x;
    }
    s / 12.0
}

/// Key's std: sqrt of the summed squares, NOT fused (found against Key on 3,000 random PCPs, while the correlation's
/// `r += a*b` is fused).
fn std_sum(v: &[f32; 12], mean: f32) -> f32 {
    let mut s = 0f32;
    for &x in v {
        s += (x - mean) * (x - mean);
    }
    s.sqrt()
}

fn correlation(v1: &[f32; 12], m1: f32, s1: f32, v2: &[f32; 12], m2: f32, s2: f32, shift: i32) -> f32 {
    if s1 == 0.0 || s2 == 0.0 {
        return 0.0;
    }
    let mut r = 0f32;
    for i in 0..12i32 {
        let index = (i - shift).rem_euclid(12) as usize;
        r = (v1[i as usize] - m1).mul_add(v2[index] - m2, r);
    }
    r / (s1 * s2)
}

/// Key from the frames' HPCPs, as the streaming Key: the mean HPCP (summed in frame order), peak-normalized, bins
/// under 0.2 zeroed, normalized again (averageDetuningCorrection, which shifts nothing at 12 bins), then the best
/// correlation with the edma profiles. (key index into KEYS, minor?, strength)
pub fn key_of(frames: &[[f32; 12]]) -> (usize, bool, f32) {
    let mut avg = [0f32; 12];
    for f in frames {
        for j in 0..12 {
            avg[j] += f[j];
        }
    }
    for v in avg.iter_mut() {
        *v /= frames.len() as f32;
    }
    normalize(&mut avg);
    for v in avg.iter_mut() {
        if *v < PCP_THRESHOLD {
            *v = 0.0;
        }
    }
    normalize(&mut avg);
    let mp = mean(&avg);
    let sp = std_sum(&avg, mp);
    let (mmaj, mmin) = (mean(&EDMA_MAJOR), mean(&EDMA_MINOR));
    let (smaj, smin) = (std_sum(&EDMA_MAJOR, mmaj), std_sum(&EDMA_MINOR, mmin));
    let (mut max_maj, mut i_maj, mut max_min, mut i_min) = (-1f32, 0usize, -1f32, 0usize);
    for shift in 0..12 {
        let c = correlation(&avg, mp, sp, &EDMA_MAJOR, mmaj, smaj, shift);
        if c > max_maj {
            max_maj = c;
            i_maj = shift as usize;
        }
        let c = correlation(&avg, mp, sp, &EDMA_MINOR, mmin, smin, shift);
        if c > max_min {
            max_min = c;
            i_min = shift as usize;
        }
    }
    // maxOther stays -1 without useMajMin
    if max_maj > max_min && max_maj > -1.0 { (i_maj, false, max_maj) } else { (i_min, true, max_min) }
}
