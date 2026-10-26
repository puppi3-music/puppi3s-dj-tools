//! Essentia's TensorflowInputMusiCNN (the mel input of the EffNet and MAEST models) over a whole 16 kHz signal,
//! cut as FrameCutter(frameSize=512, hopSize=256, startFromZero=False) cuts it. Per frame, as the C++ does it:
//! Windowing (Hann, zero-phase), Spectrum (FFTW, then sqrt(re*re + im*im)), MelBands (96 bands, power, summed bin
//! by bin), x*10000 then +1, log10f. Essentia's arm64 build fuses `re*re + im*im` and the band sum
//! `acc + (s*s)*w` (clang's default contraction, within one expression) but not the separate `*=`/`+=` of the
//! scale and shift; the same here. Frames are independent, so they run in parallel.

use crate::fft512::rfft512;
use crate::musicnn_tables::{LOG10F_FIX, MEL_LEN, MEL_START, MEL_WEIGHTS, WINDOW};
use rayon::prelude::*;

pub const FRAME: usize = 512;
pub const HOP: usize = 256;
pub const BANDS: usize = 96;

/// Start of each frame (may be negative: the first frame is centred on sample 0), as the standard FrameCutter
/// steps: a frame while its start is inside the signal; after a zero-padded frame whose centre is at or past the
/// end, no more.
pub fn frame_starts(n: usize) -> Vec<isize> {
    let n = n as isize;
    let mut out = Vec::new();
    let mut start = -((FRAME as isize + 1) / 2);
    while n > 0 && start < n {
        out.push(start);
        if start + FRAME as isize > n && start + (FRAME / 2) as isize >= n {
            break;
        }
        start += HOP as isize;
    }
    out
}

/// macOS's log10f, which isn't correctly rounded: (float)log10((double)x), except at the inputs listed in
/// LOG10F_FIX (where it differs, or where another libm's double log10 could round the other way).
#[inline]
fn log10f(x: f32) -> f32 {
    let b = x.to_bits();
    match LOG10F_FIX.binary_search_by_key(&b, |&(k, _)| k) {
        Ok(i) => f32::from_bits(LOG10F_FIX[i].1),
        Err(_) => (x as f64).log10() as f32,
    }
}

fn mel_frame(frame: &[f32; FRAME], out: &mut [f32]) {
    let win = |i: usize| f32::from_bits(WINDOW[i]);
    let mut x = [0f32; FRAME];
    for i in 0..FRAME / 2 {
        x[i] = frame[FRAME / 2 + i] * win(i); // zero-phase: the second half first
        x[FRAME / 2 + i] = frame[i] * win(FRAME / 2 + i);
    }
    let mut c = [0f32; 514];
    rfft512(&x, &mut c);
    let mut s = [0f32; 257];
    for k in 0..257 {
        let (re, im) = (c[2 * k], c[2 * k + 1]);
        s[k] = re.mul_add(re, im * im).sqrt();
    }
    let mut w = 0usize;
    for b in 0..BANDS {
        let (lo, len) = (MEL_START[b] as usize, MEL_LEN[b] as usize);
        let mut acc = 0f32;
        for j in lo..lo + len {
            acc = (s[j] * s[j]).mul_add(f32::from_bits(MEL_WEIGHTS[w]), acc);
            w += 1;
        }
        let y = acc * 10000.0 + 1.0;
        out[b] = log10f(if y < 1e-30 { 1e-30 } else { y });
    }
}

/// essentia::isSilent: instantPower (inner_product, which clang fuses: fma(x, x, acc); / size in float) < 1e-10.
fn is_silent(fr: &[f32; FRAME]) -> bool {
    let e = fr.iter().fold(0f32, |acc, &v| v.mul_add(v, acc));
    ((e / FRAME as f32) as f64) < 1e-10
}

/// Mel frames of the whole signal into out (frame_starts(len).len() * 96 values, row by row).
///
/// streaming: frames as the streaming FrameCutter gives them (what TensorflowPredictEffnetDiscogs uses), not the
/// standard one (FrameGenerator). Its silentFrames="noise" default, on a silent frame (isSilent), copies the frame
/// into a zeroed buffer shifted right by the left zero-padding, `fastcopy(&inputFrame[0] + zeropadSize, &frame[0],
/// acquireSize)`, and adds noise. Only the first frame is left-padded, so a silent first frame loses its few
/// samples and becomes silence. The noise (NoiseAdder, -100 dB, unseeded) is about 1e-10 in amplitude and
/// disappears in log10(1 + 10000 * band): Essentia's EffNet output is the same on every run, and matches without it.
pub fn mels(x: &[f32], out: &mut [f32], streaming: bool) {
    let starts = frame_starts(x.len());
    out[..starts.len() * BANDS].par_chunks_mut(BANDS).zip(starts.par_iter()).for_each(|(o, &st)| {
        let mut fr = [0f32; FRAME];
        for (k, v) in fr.iter_mut().enumerate() {
            let i = st + k as isize;
            if i >= 0 && (i as usize) < x.len() {
                *v = x[i as usize];
            }
        }
        if streaming && st < 0 && is_silent(&fr) {
            let zeropad = (-st) as usize;
            let acquire = (FRAME - zeropad).min(x.len());
            let mut shifted = [0f32; FRAME];
            for i in 0..acquire.min(FRAME - zeropad) {
                shifted[zeropad + i] = fr[i];
            }
            fr = shifted;
        }
        mel_frame(&fr, o);
    });
}
