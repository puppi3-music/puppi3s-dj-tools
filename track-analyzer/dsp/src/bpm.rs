//! Essentia's RhythmExtractor2013(method="multifeature") over a 44.1 kHz mono signal (docs/WINDOWS.md stage 6), step by
//! step as the C++ (Essentia 2.1b6.dev1177) computes it: float where it is float, double where C++ promotes, clang's
//! contractions and the loops it vectorised, read off the macOS build's machine code where the source leaves it open.
//! libm calls are computed in double and rounded (math.rs): the nearest float, where macOS's are a float step off on
//! some inputs, so the bits differ from Essentia's there.
//!
//! BeatTrackerMultiFeature: frames of 2048 every 1024 (startFromZero) -> Hann -> FFT -> magnitude and phase -> three onset
//! detection functions (rms, complex, melflux); OnsetDetectionGlobal's beat_emphasis and infogain on the signal; each of
//! the five into TempoTapDegara; TempoTapMaxAgreement picks the ticks; RhythmExtractor2013 the BPM from their intervals.

use crate::bpm_tables::{
    ERB40, GAUSS_IBI1, GAUSS_IBI2, GAUSS_VITERBI, HANN2048, MEL40_LEN, MEL40_START, MEL40_WEIGHTS, RAYLEIGH512,
};
use crate::fftw;
use crate::math::{atan2f, expf, log2f, log10f, logf, sincosf};
use rayon::prelude::*;
use std::f64::consts::PI;

pub const FRAME: usize = 2048;
pub const BINS: usize = FRAME / 2 + 1;

/// The FrameCutter rule with startFromZero: frames every `hop` from 0, the last one the first that reaches the end.
pub fn frame_starts(n: usize, frame: usize, hop: usize) -> Vec<usize> {
    let mut out = Vec::new();
    let mut start = 0;
    while start < n {
        out.push(start);
        if start + frame >= n {
            break;
        }
        start += hop;
    }
    out
}

/// hypotf: sqrt(x*x + y*y) in double (the squares and so the sum exact or rounded once), rounded.
fn hypotf(x: f32, y: f32) -> f32 {
    if x.is_infinite() || y.is_infinite() {
        return f32::INFINITY;
    }
    let (x, y) = (x as f64, y as f64);
    (x * x + y * y).sqrt() as f32
}

/// libc++'s std::polar(rho, theta) for rho >= 0 (a magnitude): rho*cos, rho*sin, a NaN product becoming 0.
fn polar(rho: f32, theta: f32) -> (f32, f32) {
    if rho.is_nan() || rho.is_sign_negative() {
        return (f32::NAN, f32::NAN);
    }
    if theta.is_nan() {
        return if rho.is_infinite() { (rho, theta) } else { (theta, theta) };
    }
    if theta.is_infinite() {
        return if rho.is_infinite() { (rho, f32::NAN) } else { (f32::NAN, f32::NAN) };
    }
    let (s, c) = sincosf(theta);
    let x = rho * c;
    let y = rho * s;
    (if x.is_nan() { 0.0 } else { x }, if y.is_nan() { 0.0 } else { y })
}

/// `fmod(tp + M_PI, -2 * M_PI) + M_PI` as clang compiled it: no fmod call, x - floor(x / y) * y with the product fused.
fn wrap_phase(tp: f32) -> f32 {
    let x = tp as f64 + PI;
    let y = -2.0 * PI;
    let q = (x / y).floor();
    ((-q).mul_add(y, x) + PI) as f32
}

/// essentiamath's lin2db: 10 * log10f(x), -100 below 1e-10.
fn lin2db(x: f32) -> f32 {
    if (x as f64) < 1e-10 { -100.0 } else { 10.0 * log10f(x) }
}

/// One frame: Windowing (Hann, normalized, zero-phase) -> FFT -> CartesianToPolar (magnitude fused, phase atan2f).
#[inline(never)]
fn polar_frame(x: &[f32], start: usize, mag: &mut [f32], phase: &mut [f32]) {
    let mut fr = Box::new([0f32; FRAME]);
    let end = (start + FRAME).min(x.len());
    fr[..end - start].copy_from_slice(&x[start..end]);
    let win = |i: usize| f32::from_bits(HANN2048[i]);
    let mut w = Box::new([0f32; FRAME]);
    for k in 0..FRAME / 2 {
        w[k] = fr[FRAME / 2 + k] * win(FRAME / 2 + k);
        w[FRAME / 2 + k] = fr[k] * win(k);
    }
    let mut c = Box::new([0f32; 2 * BINS]);
    fftw::r2c(&w[..], &mut c[..]);
    for k in 0..BINS {
        let (re, im) = (c[2 * k], c[2 * k + 1]);
        mag[k] = re.mul_add(re, im * im).sqrt();
        phase[k] = atan2f(im, re);
    }
}

/// Sum of a[i] * b[i] in order, as the macOS build's vectorised loops (ERBBands, SpectralWhitening) do it: in whole
/// blocks of 16 each product is rounded before it is added, the tail fuses `acc += a*b`.
fn dot16(a: &[f32], b: &[f32]) -> f32 {
    let block = a.len() / 16 * 16;
    let mut acc = 0f32;
    for i in 0..a.len() {
        acc = if i < block { acc + a[i] * b[i] } else { a[i].mul_add(b[i], acc) };
    }
    acc
}

/// OnsetDetection rms before the difference: sqrt(sum of squares) / size, the sum unrolled by 4 without fusing and the
/// leftover iterations fused (the macOS build's loop).
fn rms_level(mag: &[f32]) -> f32 {
    let block = mag.len() / 4 * 4;
    let mut acc = 0f32;
    for (i, &v) in mag.iter().enumerate() {
        acc = if i < block { acc + v * v } else { v.mul_add(v, acc) };
    }
    acc.sqrt() / mag.len() as f32
}

/// OnsetDetection complex of one frame, from the frame and the two before it (zeros before the first).
fn complex_odf(mag: &[f32], phase: &[f32], mag_1: &[f32], phase_1: &[f32], phase_2: &[f32]) -> f32 {
    let mut acc = 0f32;
    for i in 0..BINS {
        let tp = wrap_phase(2.0f32.mul_add(phase_1[i], -phase_2[i]));
        let (x, y) = polar(mag[i], phase[i] - tp);
        acc += hypotf(mag_1[i] - x, -y);
    }
    acc
}

/// OnsetDetection melflux before the difference: MelBands (40, power, sums fused) in dB (amp2db).
fn mel_db(mag: &[f32], out: &mut [f32]) {
    let mut w = 0usize;
    for b in 0..40 {
        let (lo, len) = (MEL40_START[b] as usize, MEL40_LEN[b] as usize);
        let mut band = 0f32;
        for j in lo..lo + len {
            band = (mag[j] * mag[j]).mul_add(f32::from_bits(MEL40_WEIGHTS[w]), band);
            w += 1;
        }
        out[b] = 2.0 * lin2db(band);
    }
}

/// libc++'s std::norm: re*re + im*im (fused), |re| or |im| when one is infinite.
fn norm(re: f32, im: f32) -> f32 {
    if re.is_infinite() {
        re.abs()
    } else if im.is_infinite() {
        im.abs()
    } else {
        re.mul_add(re, im * im)
    }
}

/// OnsetDetectionGlobal beat_emphasis, per frame: the complex-domain difference in each bin (its target phase is
/// 2*phase_1 + phase_2, as the C++ has it) through ERBBands (40 bands, magnitude).
fn beat_erb(mag: &[f32], phase: &[f32], mag_1: &[f32], phase_1: &[f32], phase_2: &[f32], out: &mut [f32]) {
    let mut temp = [0f32; BINS];
    for i in 0..BINS {
        let tp = wrap_phase(2.0f32.mul_add(phase_1[i], phase_2[i]));
        let (x, y) = polar(mag[i], phase[i] - tp);
        temp[i] = norm(mag_1[i] - x, -y);
    }
    for (b, o) in out.iter_mut().enumerate() {
        // SAFETY: ERB40 holds f32 bit patterns, 40 rows of BINS.
        let row = unsafe { std::slice::from_raw_parts(ERB40.as_ptr().add(b * BINS) as *const f32, BINS) };
        *o = dot16(&temp, row);
    }
}

const IG_LO: usize = 2; // infogain bins: round(40 * 2048 / 44100) ..
const IG_HI: usize = 233; // .. round(5000 * 2048 / 44100) + 1
const IG_HIST: usize = 5;

/// infogain's weights: 1 - i * 0.9 / 5, in double, for i = 0..5.
fn ig_weights() -> [f32; IG_HIST] {
    let mut w = [0f32; IG_HIST];
    for (i, v) in w.iter_mut().enumerate() {
        *v = (1.0 - i as f64 * 0.9 / IG_HIST as f64) as f32;
    }
    w
}

/// OnsetDetectionGlobal infogain of one frame, from the spectra of the 11 frames ending with it (None before the first).
fn infogain(frames: &[Option<&[f32]>; 11], w: &[f32; IG_HIST]) -> f32 {
    let mut detection = 0f32;
    for b in IG_LO..IG_HI {
        let (mut old, mut new) = (0f32, 0f32);
        for i in 0..IG_HIST {
            let a = frames[i].map_or(0.0, |f| f[b]);
            let c = frames[IG_HIST + 1 + i].map_or(0.0, |f| f[b]);
            old = a.mul_add(w[IG_HIST - 1 - i], old);
            new = c.mul_add(w[i], new);
        }
        if old == 0.0 {
            old = 1.0;
        }
        if new == 0.0 {
            new = f32::EPSILON;
        }
        let l = log2f(new / old);
        detection += l.max(0.0);
    }
    detection
}

/// The five onset detection functions BeatTrackerMultiFeature feeds TempoTapDegara.
pub struct Onsets {
    pub rms: Vec<f32>,
    pub complex: Vec<f32>,
    pub melflux: Vec<f32>,
    pub beat_emphasis: Vec<f32>,
    pub infogain: Vec<f32>,
}

/// All five, from one pass over the 2048-sample frames every 512 samples (OnsetDetectionGlobal's frames; the main chain's
/// frames every 1024 are every other one of them, with the same spectra). Spectra are computed in parallel a chunk at a
/// time with the 10 frames before kept, then each frame's values in parallel from its frame and the ones before.
pub fn onsets(x: &[f32]) -> Onsets {
    if x.is_empty() {
        return Onsets { rms: vec![], complex: vec![], melflux: vec![], beat_emphasis: vec![], infogain: vec![] };
    }
    let nf = frame_starts(x.len(), FRAME, 512).len();
    let main = frame_starts(x.len(), FRAME, 1024).len();
    // the main chain's last frame can come after OnsetDetectionGlobal's last (which stops one hop earlier when that
    // already reaches the end): spectra for both
    let total = nf.max(2 * main - 1);
    let hop512: Vec<usize> = (0..total).map(|j| 512 * j).collect();
    const CHUNK: usize = 2048;
    const BACK: usize = 10;
    let mut mags = vec![0f32; (CHUNK + BACK) * BINS];
    let mut phases = vec![0f32; (CHUNK + BACK) * BINS];
    let mut erb = vec![0f32; nf * 40];
    let mut ig = vec![0f32; nf];
    let mut rms = vec![0f32; main];
    let mut cplx = vec![0f32; main];
    let mut mel = vec![0f32; main * 40];
    let w = ig_weights();
    let zeros = vec![0f32; BINS];
    for c0 in (0..total).step_by(CHUNK) {
        let c1 = (c0 + CHUNK).min(total);
        if c0 > 0 {
            // keep the last BACK frames of the previous chunk in front
            mags.copy_within(CHUNK * BINS..(CHUNK + BACK) * BINS, 0);
            phases.copy_within(CHUNK * BINS..(CHUNK + BACK) * BINS, 0);
        }
        mags[BACK * BINS..(BACK + c1 - c0) * BINS].par_chunks_mut(BINS)
            .zip(phases[BACK * BINS..(BACK + c1 - c0) * BINS].par_chunks_mut(BINS))
            .zip(hop512[c0..c1].par_iter())
            .for_each(|((m, p), &st)| polar_frame(x, st, m, p));
        let (mags, phases) = (&mags, &phases);
        // frame j's spectra, or None before the first frame
        let spec = |j: isize| -> Option<(&[f32], &[f32])> {
            if j < 0 {
                return None;
            }
            let k = (j as usize + BACK - c0) * BINS;
            Some((&mags[k..k + BINS], &phases[k..k + BINS]))
        };
        let zero = (&zeros[..], &zeros[..]);
        let e1 = c1.min(nf);
        erb[c0.min(nf) * 40..e1 * 40].par_chunks_mut(40).zip(ig[c0.min(nf)..e1].par_iter_mut()).enumerate().for_each(|(i, (e, g))| {
            let j = (c0 + i) as isize;
            let (m, p) = spec(j).unwrap();
            let (m1, p1) = spec(j - 1).unwrap_or(zero);
            let p2 = spec(j - 2).unwrap_or(zero).1;
            beat_erb(m, p, m1, p1, p2, e);
            let frames: [Option<&[f32]>; 11] = std::array::from_fn(|k| spec(j - 10 + k as isize).map(|s| s.0));
            *g = infogain(&frames, &w);
        });
        // the main chain's frames among these: k at 512-frame 2k
        let (k0, k1) = (c0.div_ceil(2), c1.div_ceil(2).min(main));
        if k0 < k1 {
            rms[k0..k1].par_iter_mut().zip(cplx[k0..k1].par_iter_mut()).zip(mel[k0 * 40..k1 * 40].par_chunks_mut(40))
                .enumerate().for_each(|(i, ((r, c), md))| {
                    let j = 2 * (k0 + i) as isize;
                    let (m, p) = spec(j).unwrap();
                    let (m1, p1) = spec(j - 2).unwrap_or(zero);
                    let p2 = spec(j - 4).unwrap_or(zero).1;
                    *r = rms_level(m);
                    *c = complex_odf(m, p, m1, p1, p2);
                    mel_db(m, md);
                });
        }
    }
    // rms and melflux: the differences with the frame before, half-rectified; 0 on the first frame
    let mut rms_odf = vec![0f32; main];
    let mut melflux = vec![0f32; main];
    for k in 1..main {
        let d = rms[k] - rms[k - 1];
        rms_odf[k] = if d < 0.0 { 0.0 } else { d };
        let mut flux = 0f32;
        for b in 0..40 {
            let diff = mel[k * 40 + b] - mel[(k - 1) * 40 + b];
            if !(diff < 0.0) {
                flux += diff; // C++: if (diff < 0) continue;
            }
        }
        melflux[k] = flux;
    }
    Onsets { rms: rms_odf, complex: cplx, melflux, beat_emphasis: beat_emphasis(&erb, nf), infogain: ig }
}

/// Essentia's MovingAverage (IIR with 17 taps of 1/17, a = [1]): the transposed form, fused, subnormal states flushed;
/// the state carries on from one call to the next, as OnsetDetectionGlobal never resets it between bands.
struct MovingAverage {
    b: f32,
    state: [f32; 17],
}

impl MovingAverage {
    fn run(&mut self, x: &[f32]) -> Vec<f32> {
        let b = self.b;
        x.iter()
            .map(|&v| {
                let y = b.mul_add(v, self.state[0]);
                for k in 1..17 {
                    let s = b.mul_add(v, self.state[k]);
                    self.state[k - 1] = if s.is_subnormal() { 0.0 } else { s };
                }
                y
            })
            .collect()
    }
}

/// AutoCorrelation(normalization "unbiased"), its first `lags` lags (zero past the signal, as tempACF.resize): FFTW at
/// nextPowerTwo(2n) points, |X|^2 (fused), the inverse, / size, then / (n - lag). Beyond fftw::MAX_P (beat_emphasis on
/// over 3 h 20 min of audio), the lags directly in double: close, not bit for bit.
fn acf_unbiased(x: &[f32], lags: usize) -> Vec<f32> {
    let n = x.len();
    let size = (2 * n).next_power_of_two();
    if size.trailing_zeros() > fftw::MAX_P {
        return acf_direct(x, lags);
    }
    let mut pad = vec![0f32; size];
    pad[..n].copy_from_slice(x);
    let mut c = vec![0f32; size + 2];
    fftw::r2c(&pad, &mut c);
    for k in 0..=size / 2 {
        let (re, im) = (c[2 * k], c[2 * k + 1]);
        c[2 * k] = re.mul_add(re, im * im);
        c[2 * k + 1] = 0.0;
    }
    fftw::c2r(&mut c, &mut pad);
    (0..lags).map(|i| if i < n { (pad[i] / size as f32) / (n - i) as f32 } else { 0.0 }).collect()
}

fn acf_direct(x: &[f32], lags: usize) -> Vec<f32> {
    let n = x.len();
    (0..lags)
        .into_par_iter()
        .map(|k| {
            if k >= n {
                return 0.0;
            }
            let mut s = 0f64;
            for i in 0..n - k {
                s += x[i] as f64 * x[i + k] as f64;
            }
            (s as f32) / (n - k) as f32
        })
        .collect()
}

/// beat_emphasis after the frames: each band normalised by its stddev, smoothed, half-rectified, weighted by its
/// periodicity (autocorrelation through 4 comb filters and a Rayleigh tempo weighting); the bands weighing at least the
/// threshold summed. The threshold is weightsERB[24]: the C++ copies the weights into a reserved but empty vector,
/// sorts that empty range, and reads element 24 of its storage, the unsorted copy.
fn beat_emphasis(erb: &[f32], nf: usize) -> Vec<f32> {
    if nf == 0 {
        return Vec::new();
    }
    const MAX_PERIOD: usize = 512; // round(5.944308390022676 * 44100 / 512)
    // the Rayleigh weighting, rayparam2 = pow(round(43 * 512.0 / 512), 2)
    let weights = floats(&RAYLEIGH512[..MAX_PERIOD]);
    let mut avg = MovingAverage { b: (1.0f64 / 17.0) as f32, state: [0.0; 17] };
    let mut bands: Vec<Vec<f32>> = Vec::with_capacity(40);
    let mut weights_erb = [0f32; 40];
    for b in 0..40 {
        let mut v: Vec<f32> = (0..nf).map(|j| erb[j * 40 + b]).collect();
        let mut sum = 0f32;
        for &x in &v {
            sum += x;
        }
        let mean = sum / nf as f32;
        let mut var = 0f32;
        for &x in &v {
            var = (x - mean).mul_add(x - mean, var);
        }
        let sd = (var / nf as f32).sqrt();
        if sd > 0.0 {
            for x in v.iter_mut() {
                *x /= sd;
            }
        }
        let last = *v.last().unwrap();
        v.extend(std::iter::repeat(last).take(16)); // the C++ appends 8 twice, both at the end
        let smoothed = avg.run(&v);
        for i in 0..nf {
            v[i] -= smoothed[16 + i];
            if v[i] < 0.0 {
                v[i] = 0.0;
            }
        }
        let acf = acf_unbiased(&v, MAX_PERIOD);
        let mut weighted = [0f32; MAX_PERIOD];
        let (pmin, pmax) = (3usize, (MAX_PERIOD - 3) / 4 - 1);
        for comb in 1..=4i64 {
            let width = (2 * comb - 1) as f32;
            for region in 1 - comb..=comb - 1 {
                for period in pmin..pmax {
                    let k = (period as i64 * comb + region) as usize;
                    weighted[period] += weights[period] * acf[k] / width;
                }
            }
        }
        let mut best = 0;
        for i in 1..MAX_PERIOD {
            if weighted[i] > weighted[best] {
                best = i;
            }
        }
        weights_erb[b] = weighted[best];
        bands.push(v);
    }
    let mut m = weights_erb[0];
    for &x in &weights_erb {
        if m < x {
            m = x;
        }
    }
    if m != 0.0 {
        for x in weights_erb.iter_mut() {
            *x /= m;
        }
    }
    let threshold = weights_erb[24];
    let mut out = vec![0f32; nf];
    for (b, band) in bands.iter().enumerate() {
        if weights_erb[b] >= threshold {
            for i in 0..nf {
                out[i] = band[i].mul_add(weights_erb[b], out[i]);
            }
        }
    }
    out
}

// ---------------------------------------------------------------------------------------------------------------------
// TempoTapDegara
// ---------------------------------------------------------------------------------------------------------------------

/// macOS's rand(): the Park-Miller "minimal standard" generator, seed 1 unless srand is called. TempoTapDegara adds
/// rand() noise to its tempo observations; Essentia never seeds it, so its result depends on how many numbers the
/// process drew before. This port starts every RhythmExtractor2013 from seed 1, as a fresh process does.
pub struct Rand(u32);

impl Rand {
    pub fn new() -> Self {
        Rand(1)
    }

    /// As after srand(seed) (macOS: a seed of 0 is used as is by do_rand's caller only through srand; 1 is the default).
    pub fn seeded(seed: u32) -> Self {
        Rand(seed)
    }

    pub fn state(&self) -> u32 {
        self.0
    }

    fn next(&mut self) -> i32 {
        let x = self.0 as i64;
        let (hi, lo) = (x / 127773, x % 127773);
        let mut v = 16807 * lo - 2836 * hi;
        if v < 0 {
            v += 0x7fff_ffff;
        }
        self.0 = v as u32;
        v as i32
    }
}

const RAND_MAX: f64 = 2147483647.0;

/// The first index of the largest value (std::max_element).
fn argmax(v: &[f32]) -> usize {
    let mut best = 0;
    for i in 1..v.len() {
        if v[i] > v[best] {
            best = i;
        }
    }
    best
}

/// The first index of the smallest value (std::min_element).
fn argmin(v: &[f32]) -> usize {
    let mut best = 0;
    for i in 1..v.len() {
        if v[i] < v[best] {
            best = i;
        }
    }
    best
}

/// essentiamath's normalize: divide by the largest value unless it is 0.
fn normalize_max(v: &mut [f32]) {
    if v.is_empty() {
        return;
    }
    let m = v[argmax(v)];
    if m != 0.0 {
        for x in v.iter_mut() {
            *x /= m;
        }
    }
}

/// essentiamath's normalizeSum: nothing if a value is negative or the sum is 0, else divide by the sum.
fn normalize_sum(v: &mut [f32]) {
    let mut s = 0f32;
    for &x in v.iter() {
        if x < 0.0 {
            return;
        }
        s += x;
    }
    if s != 0.0 {
        for x in v.iter_mut() {
            *x /= s;
        }
    }
}

/// essentia::round: floor(x + 0.5).
fn round_es(x: f32) -> f32 {
    (x + 0.5).floor()
}

/// A weight table of bpm_tables.rs (computed with libm's exp, as macOS gives it: gen_bpm_tables.py) as floats.
fn floats(t: &[u32]) -> Vec<f32> {
    t.iter().map(|&b| f32::from_bits(b)).collect()
}

/// AutoCorrelation(normalization "unbiased") of a 512-value ODF frame (FFTW at 1024 points).
fn acf512(x: &[f32]) -> [f32; 512] {
    let mut out = [0f32; 512];
    out[..x.len()].copy_from_slice(&acf_unbiased(x, x.len()));
    out
}

/// TempoTapDegara (sampleRateODF 44100/1024 with resample x2, or 44100/512: 86.1328125 either way) on one onset detection
/// function: beat times in seconds. `rand` carries on from one call to the next, as the process-wide rand() does.
pub struct Degara {
    resample: usize,
    sr: f32,
    resolution: f32,
    hop_duration: f32,
    tempo_weights: Vec<f32>,
    viterbi: Vec<Vec<f32>>,
    pmin_user: usize,
    pmax_user: usize,
    avg: MovingAverage,
}

const COMBS: usize = 4;
const ODF_FRAME: usize = 512; // int(round(5.944308390022676 * 86.1328125))
const ODF_HOP: usize = ODF_FRAME / COMBS;

impl Degara {
    pub fn new(resample: usize, min_tempo: i32, max_tempo: i32) -> Self {
        let base = if resample == 2 { (44100.0f32 / 1024.0) * 2.0 } else { 44100.0f32 / 512.0 };
        let sr = base;
        let frame_duration = 5.944308390022676f64 as f32;
        let hop_duration = frame_duration / COMBS as f32;
        let resolution = (1.0 / sr as f64) as f32;
        // createTempoPreferenceCurve: the Rayleigh weighting with rayparam2 = pow(round(60 * sr / 120), 2) = 43^2, as
        // beat_emphasis'
        let mut tempo_weights = floats(&RAYLEIGH512[..ODF_HOP]);
        normalize_sum(&mut tempo_weights);
        // user tempo range
        let pmax_user = ((60.0 / min_tempo as f32 as f64 * sr as f64).ceil() as i64 - 1).min(ODF_HOP as i64 - 1) as usize;
        let pmin_user = ((60.0 / max_tempo as f32 as f64 * sr as f64).floor() as i64 - 1).min(ODF_HOP as i64 - 1) as usize;
        // createViterbiTransitionMatrix
        let scale = (sr as f64 / (44100.0 / 512.0)) as f32;
        let g = floats(&GAUSS_VITERBI); // gaussianPDF(8 * scale, 1, 1), scale 1
        let gm = (g.len() / 2) as i64;
        let (lo, hi) = ((28.0 * scale).floor() as i64 - 1, (108.0 * scale).ceil() as i64 - 1);
        let mut viterbi = vec![vec![0f32; ODF_HOP]; ODF_HOP];
        for i in lo..=hi {
            for j in i - gm..=i + gm {
                if j >= lo && j <= hi {
                    viterbi[i as usize][j as usize] = g[(j - (i - gm)) as usize];
                }
            }
        }
        Degara { resample, sr, resolution, hop_duration, tempo_weights, viterbi, pmin_user, pmax_user,
                 avg: MovingAverage { b: (1.0f64 / 17.0) as f32, state: [0.0; 17] } }
    }

    /// TempoTapDegara::adaptiveThreshold (the moving average's state carries on between calls).
    fn adaptive_threshold(&mut self, a: &mut Vec<f32>) {
        const H: usize = 8; // floor(0.1 * 86.1328125)
        let (front, back) = (a[0], *a.last().unwrap());
        let mut padded = Vec::with_capacity(a.len() + 2 * H);
        padded.extend(std::iter::repeat(front).take(H));
        padded.extend_from_slice(a);
        padded.extend(std::iter::repeat(back).take(H));
        let smoothed = self.avg.run(&padded);
        for i in 0..a.len() {
            a[i] -= smoothed[2 * H + i];
            if a[i] < 0.0 {
                a[i] = 0.0;
            }
        }
    }

    pub fn ticks(&mut self, odf: &[f32], rand: &mut Rand) -> Vec<f32> {
        if odf.is_empty() {
            return Vec::new();
        }
        let mut det = odf.to_vec();
        normalize_max(&mut det);
        if self.resample > 1 && det.len() > 1 {
            let r = self.resample;
            let mut temp = vec![0f32; (det.len() - 1) * r + 1];
            for i in 0..det.len() - 1 {
                let delta = (det[i + 1] - det[i]) / r as f32;
                for j in 0..r {
                    temp[i * r + j] = delta.mul_add(j as f32, det[i]);
                }
            }
            *temp.last_mut().unwrap() = *det.last().unwrap();
            det = temp;
        }
        let (periods, ends) = self.beat_periods(det.clone(), rand);
        self.beats(&mut det, &periods, &ends)
    }

    /// computeBeatPeriodsDavies: the beat period of each 6 s frame (hop 1.5 s), by Viterbi over the frames' weighted
    /// autocorrelations; (periods, frame end times).
    fn beat_periods(&mut self, mut det: Vec<f32>, rand: &mut Rand) -> (Vec<f32>, Vec<f32>) {
        self.adaptive_threshold(&mut det);
        let mut obs: Vec<Vec<f32>> = Vec::new();
        let mut obs_max = 0f32;
        for st in frame_starts(det.len(), ODF_FRAME, ODF_HOP) {
            let mut frame = [0f32; ODF_FRAME];
            let end = (st + ODF_FRAME).min(det.len());
            frame[..end - st].copy_from_slice(&det[st..end]);
            let acf = acf512(&frame);
            let mut norm = vec![0f32; ODF_HOP];
            for comb in 1..=COMBS as i64 {
                let width = (2 * comb - 1) as f32;
                for region in 1 - comb..=comb - 1 {
                    for period in 3..=((ODF_FRAME - 3) / COMBS - 1) {
                        let k = ((period as i64 + 1) * comb - 1 + region) as usize;
                        norm[period] += self.tempo_weights[period] * acf[k] / width;
                    }
                }
            }
            self.adaptive_threshold(&mut norm);
            for v in norm[..=self.pmin_user].iter_mut() {
                *v = 0.0;
            }
            for v in norm[self.pmax_user + 1..].iter_mut() {
                *v = 0.0;
            }
            normalize_sum(&mut norm);
            let t_max = norm[argmax(&norm)];
            if t_max > obs_max {
                obs_max = t_max;
            }
            obs.push(norm);
        }
        for o in obs.iter_mut() {
            for v in o.iter_mut() {
                let r = rand.next() as f32;
                *v = (*v as f64 + 0.0001 * obs_max as f64 * r as f64 / RAND_MAX) as f32;
            }
        }
        let path = self.viterbi_path(&obs);
        let periods = path.iter().map(|&p| (p as f32 + 1.0) / self.sr).collect();
        let ends = (0..obs.len()).map(|t| (t + 1) as f32 * self.hop_duration).collect();
        (periods, ends)
    }

    /// findViterbiPath with the tempo weights as the prior.
    fn viterbi_path(&self, obs: &[Vec<f32>]) -> Vec<usize> {
        let n = ODF_HOP;
        let t_len = obs.len();
        let mut delta: Vec<f32> = (0..n).map(|i| self.tempo_weights[i] * obs[0][i]).collect();
        normalize_sum(&mut delta);
        let mut psi = vec![vec![0usize; n]; t_len];
        let mut tmp = vec![0f32; n];
        for t in 1..t_len {
            let mut new = vec![0f32; n];
            for j in 0..n {
                for i in 0..n {
                    tmp[i] = delta[i] * self.viterbi[j][i];
                }
                let i_max = argmax(&tmp);
                new[j] = tmp[i_max] * obs[t][j];
                psi[t][j] = i_max;
            }
            normalize_sum(&mut new);
            delta = new;
        }
        let mut path = vec![0usize; t_len];
        path[t_len - 1] = argmax(&delta);
        for t in (0..t_len - 1).rev() {
            path[t] = psi[t + 1][path[t + 1]];
        }
        path
    }

    /// computeHMMTransitionMatrix for one beat period's inter-beat-interval pdf: log probabilities * alpha.
    fn hmm_transitions(ibi_pdf: &[f32]) -> Vec<Vec<f32>> {
        let n = ibi_pdf.len();
        let mut t = vec![vec![0f32; n]; n];
        t[0][0] = ibi_pdf[0];
        t[0][1] = 1.0 - t[0][0];
        for i in 1..n {
            let mut s = 0f32;
            for k in 0..i {
                s += logf(t[k][k + 1]);
            }
            let mut v = expf(logf(ibi_pdf[i]) - s);
            if v < 0.0 || v > 1.0 {
                v = if v < 0.0 { 0.0 } else { 1.0 };
            }
            t[i][0] = v;
            if i + 1 < n {
                t[i][i + 1] = 1.0 - t[i][0];
            }
        }
        for row in t.iter_mut() {
            for v in row.iter_mut() {
                *v = logf(*v) * 0.5;
            }
        }
        t
    }

    /// computeBeatsDegara + decodeBeats: the probabilistic beat tracker over the (resampled) detections.
    fn beats(&self, det: &mut [f32], periods: &[f32], ends: &[f32]) -> Vec<f32> {
        for v in det.iter_mut() {
            if *v == 0.0 {
                *v = f32::EPSILON;
            }
        }
        let sigma = 0.025f32;
        let period_max = periods[argmax(periods)];
        let ibi_max = period_max + 3.0 * sigma;
        let mut n_states = 0usize;
        let mut t = self.resolution;
        while t <= ibi_max {
            n_states += 1;
            t += self.resolution;
        }
        // gaussianPDF(sigma, resolution, 0.01 / resample)
        let g = floats(if self.resample == 1 { &GAUSS_IBI1 } else { &GAUSS_IBI2 });
        let mut matrices: Vec<(u32, Vec<Vec<f32>>)> = Vec::new(); // std::map<Real, ...> keyed by the period's bits
        let key = |p: f32| p.to_bits();
        let mut ibi_pdf = vec![0f32; n_states];
        for &p in periods {
            if matrices.iter().any(|(k, _)| *k == key(p)) {
                continue;
            }
            let shift = ((g.len() / 2) as f32 - round_es(p / self.resolution - 1.0)) as i64;
            for j in 0..n_states {
                let jn = j as i64 + shift;
                ibi_pdf[j] = if jn < 0 || jn >= g.len() as i64 { 0.0 } else { g[jn as usize] };
            }
            matrices.push((key(p), Self::hmm_transitions(&ibi_pdf)));
        }
        let matrix = |p: f32| &matrices.iter().find(|(k, _)| *k == key(p)).unwrap().1;
        let frames = det.len();
        // beat / no-beat log probabilities, (1 - alpha) * log
        let (beat, no_beat): (Vec<f32>, Vec<f32>) = det
            .iter()
            .map(|&d| {
                let b = (0.99 * d as f64) as f32;
                let nb = (1.0 - b as f64) as f32;
                ((1.0 - 0.5f32) * logf(b), (1.0 - 0.5f32) * logf(nb))
            })
            .unzip();
        // decodeBeats
        let mut back = vec![0i32; n_states * frames]; // [state][t]
        let mut cost = vec![f32::MAX; n_states];
        cost[0] = 0.0;
        let mut cost_old = cost.clone();
        let mut diff = vec![0f32; n_states];
        let mut cur = 0usize;
        for t in 0..frames {
            let m = matrix(periods[cur]);
            for i in 0..n_states {
                diff[i] = cost_old[i] - m[i][0];
            }
            let mut best = argmin(&diff) as i32;
            let best_path = diff[best as usize];
            if best_path == f32::MAX {
                best = -1;
            }
            back[t] = best;
            cost[0] = -beat[t] + best_path;
            for s in 1..n_states {
                cost[s] = cost_old[s - 1] - m[s - 1][s] - no_beat[t];
                back[s * frames + t] = (s - 1) as i32;
            }
            cost_old.copy_from_slice(&cost);
            if t + 1 < frames {
                let now = (t + 1) as f32 * self.resolution;
                let mut i = cur + 1;
                while i < ends.len() && ends[i] <= now {
                    cur = i;
                    i += 1;
                }
            }
        }
        let mut seq = vec![0i32; frames];
        seq[frames - 1] = argmin(&cost) as i32;
        for t in (0..frames - 1).rev() {
            seq[t] = back[seq[t + 1] as usize * frames + t + 1];
        }
        (0..frames).filter(|&i| seq[i] == 0).map(|i| i as f32 * self.resolution).collect()
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// TempoTapMaxAgreement and RhythmExtractor2013
// ---------------------------------------------------------------------------------------------------------------------

const AGREEMENT_BINS: usize = 40;

/// TempoTapMaxAgreement's histogram bin edges (configure).
fn agreement_bins() -> Vec<f32> {
    let mut bins = vec![-0.5f32];
    let delta = (1.0 / (AGREEMENT_BINS - 1) as f64) as f32;
    let mut bin = (-0.5 + 1.5 * delta as f64) as f32;
    while bin as f64 <= 0.5 - 1.5 * delta as f64 {
        bins.push(bin);
        bin += delta;
    }
    bins.push(0.5);
    for i in 0..bins.len() - 1 {
        bins[i] = (bins[i] + bins[i + 1]) / 2.0;
    }
    bins.pop();
    bins
}

fn closest_tick(ticks: &[f32], x: f32) -> usize {
    let mut min = -1f32;
    let mut j = 0;
    while j < ticks.len() {
        let d = (ticks[j] - x).abs();
        if min < 0.0 || d < min {
            min = d;
        } else {
            break;
        }
        j += 1;
    }
    j - 1
}

fn beat_error(ticks1: &[f32], ticks2: &[f32]) -> Vec<f32> {
    ticks2
        .iter()
        .map(|&t| {
            let j = closest_tick(ticks1, t);
            let error = t - ticks1[j];
            let interval = if j == 0 {
                (0.5 * (ticks1[j + 1] - ticks1[j]) as f64) as f32
            } else if j == ticks1.len() - 1 || error < 0.0 {
                (0.5 * (ticks1[j] - ticks1[j - 1]) as f64) as f32
            } else {
                (0.5 * (ticks1[j + 1] - ticks1[j]) as f64) as f32
            };
            (0.5 * error as f64 / interval as f64) as f32
        })
        .collect()
}

fn entropy(err: &mut [f32], bins: &[f32]) -> f32 {
    for e in err.iter_mut() {
        let a = *e as f64 + 0.5;
        *e = ((a - a.floor()) - 0.5) as f32; // essentia::fmod(a, 1.) - 0.5
    }
    let mut counts = vec![0f32; bins.len() + 1];
    for &e in err.iter() {
        if e >= *bins.last().unwrap() {
            *counts.last_mut().unwrap() += 1.0;
        } else if let Some(b) = bins.iter().position(|&edge| e < edge) {
            counts[b] += 1.0;
        }
    }
    let last = counts.pop().unwrap();
    counts[0] += last;
    normalize_sum(&mut counts);
    let mut h = 0f32;
    for c in counts.iter_mut() {
        if *c == 0.0 {
            *c = 1.0;
        }
        h = (-log2f(*c)).mul_add(*c, h); // entropy -= log2(v) * v: clang divides logf by -M_LN2 and fuses
    }
    h
}

fn beat_infogain(t1: &[f32], t2: &[f32], bins: &[f32]) -> f32 {
    if t1.len() < 2 || t2.len() < 2 {
        return 0.0;
    }
    let fwd = entropy(&mut beat_error(t2, t1), bins);
    let bwd = entropy(&mut beat_error(t1, t2), bins);
    // log2(_numberBins): essentia::log2<int>, log(40) / M_LN2 truncated to the int 5
    5.0 - if fwd < bwd { bwd } else { fwd }
}

/// TempoTapMaxAgreement: the candidate agreeing most with the others (ticks before 5 s ignored for that) and the
/// confidence, the mean agreement. (ticks, confidence)
pub fn max_agreement(candidates: &[Vec<f32>]) -> (Vec<f32>, f32) {
    let bins = agreement_bins();
    let trimmed: Vec<&[f32]> = candidates.iter().map(|c| &c[c.iter().position(|&t| t >= 5.0).unwrap_or(c.len())..]).collect();
    let n = candidates.len();
    let mut gain = vec![vec![0f32; n]; n];
    for i in 0..n {
        for j in i + 1..n {
            gain[i][j] = beat_infogain(trimmed[i], trimmed[j], &bins);
        }
    }
    let mean = |v: &[f32]| {
        let mut s = 0f32;
        for &x in v {
            s += x;
        }
        s / v.len() as f32
    };
    let dist: Vec<f32> = (0..n)
        .map(|i| {
            let mut t: Vec<f32> = (i + 1..n).map(|j| gain[i][j]).collect();
            t.extend((0..i).map(|j| gain[j][i]));
            mean(&t)
        })
        .collect();
    (candidates[argmax(&dist)].clone(), mean(&dist))
}

/// RhythmExtractor2013's BPM from the ticks: the most common BPM of the beat intervals in 2-BPM bins (bincount of
/// bpm / 2), then the mean of the intervals' BPMs within 5 of it.
pub fn bpm_of(ticks: &[f32]) -> f32 {
    if ticks.len() < 2 {
        return 0.0;
    }
    let mut est: Vec<f32> = ticks.windows(2).map(|w| (60.0 / (w[1] - w[0]) as f64) as f32).collect();
    for v in est.iter_mut() {
        *v = (*v as f64 / 2.0) as f32;
    }
    // essentiamath bincount: counts of each value rounded (max(v, 0) + 0.5, truncated)
    let size = ((est[argmax(&est)].max(0.0) + 0.5) as i64 + 1) as usize;
    let mut counts = vec![0f32; size];
    for &v in &est {
        let i = (v.max(0.0) + 0.5) as i64 as usize;
        if i < size {
            counts[i] += 1.0;
        }
    }
    let closest = (argmax(&counts) * 2) as f32;
    let mut kept = Vec::new();
    for v in est.iter_mut() {
        *v = (*v as f64 * 2.0) as f32;
        if (closest - *v).abs() < 5.0 {
            kept.push(*v);
        }
    }
    if kept.is_empty() {
        return closest;
    }
    let mut s = 0f32;
    for &x in &kept {
        s += x;
    }
    s / kept.len() as f32
}

/// RhythmExtractor2013(method="multifeature", minTempo, maxTempo): (bpm, ticks, confidence). `order` is the order the
/// five TempoTapDegara instances draw their rand() numbers in (rms, complex, melflux, beat_emphasis, infogain = 0..4).
/// Essentia's streaming scheduler runs them as [4, 3, 2, 0, 1]: on test signals every order gives the same result, but on
/// two short recordings exactly this one of the 120 matches.
pub fn rhythm(x: &[f32], min_tempo: i32, max_tempo: i32, order: [usize; 5]) -> (f32, Vec<f32>, f32) {
    let o = onsets(x);
    let odfs = [&o.rms, &o.complex, &o.melflux, &o.beat_emphasis, &o.infogain];
    let mut rand = Rand::new();
    let mut ticks: Vec<Vec<f32>> = vec![Vec::new(); 5];
    for &k in &order {
        let resample = if k < 3 { 2 } else { 1 };
        ticks[k] = Degara::new(resample, min_tempo, max_tempo).ticks(odfs[k], &mut rand);
    }
    // A tracker that finds a single beat reaches BeatTrackerMultiFeature as none: its pool stores a stream of one value
    // as a scalar, and `_pool.contains<vector<Real> >(...)` is then false.
    for t in ticks.iter_mut() {
        if t.len() == 1 {
            t.clear();
        }
    }
    // BeatTrackerMultiFeature's candidate order: complex, rms, melflux, beat_emphasis, infogain
    let candidates = vec![ticks[1].clone(), ticks[0].clone(), ticks[2].clone(), ticks[3].clone(), ticks[4].clone()];
    let (t, conf) = max_agreement(&candidates);
    (bpm_of(&t), t, conf)
}
