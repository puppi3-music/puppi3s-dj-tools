//! Fast DSP for the track analyzer, written to give the same numbers as the Essentia code it replaces.
//!
//! Each function follows Essentia's own arithmetic: the same 32-bit float operations in the same order,
//! so the results match Essentia to the bit (checked over the test collection; see docs/WINDOWS.md).
//! The speed comes from running independent pieces (one loudness window each) on all cores, which
//! doesn't change any single result.

use pyo3::buffer::PyBuffer;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;

mod bpm;
mod bpm_tables;
mod fft4096;
mod fft512;
mod fftw;
mod fftw_codelets;
mod key;
mod key_tables;
mod math;
mod mid_qual_coeffs;
mod musicnn;
mod musicnn_tables;
mod resample;

// ---------------------------------------------------------------------------------------------------
// EBU R128 loudness: Essentia's LoudnessEBUR128 (streaming/loudnessebur128.cpp, loudnessebur128filter.cpp)
// with its defaults (hopSize 0.1 s, startAtZero false = frames start at sample 0).
// ---------------------------------------------------------------------------------------------------

/// How the float maths is contracted into fused multiply-adds. A C++ compiler may fuse `a*b + c` into
/// one FMA (one rounding instead of two) depending on the platform and flags, which changes the last bit.
/// `Plain` is two roundings; the `Fma*` variants are the fusions a compiler can choose.
#[derive(Clone, Copy, PartialEq)]
enum Contract {
    Plain,
    FmaBx,  // b*x - a*y  ->  fma(b, x, -(a*y))
    FmaAy,  // b*x - a*y  ->  fma(-a, y, b*x)
}

/// K-weighting: Essentia's two biquads (shelf, then RLB high-pass) multiplied into one 4th-order IIR,
/// in float, as LoudnessEBUR128Filter::configure does.
fn k_weighting(sr: f64, fma_coeffs: bool) -> ([f32; 5], [f32; 5]) {
    use std::f64::consts::PI;
    let f0 = 1681.974450955533;
    let g = 3.999843853973347;
    let q = 0.7071752369554196;
    let k = (PI * f0 / sr).tan();
    let vh = 10f64.powf(g / 20.0);
    let vb = vh.powf(0.4996667741545416);
    let a0 = 1.0 + k / q + k * k;
    let b1 = [((vh + vb * k / q + k * k) / a0) as f32, (2.0 * (k * k - vh) / a0) as f32, ((vh - vb * k / q + k * k) / a0) as f32];
    let a1 = [1f32, (2.0 * (k * k - 1.0) / a0) as f32, ((1.0 - k / q + k * k) / a0) as f32];
    let f0 = 38.13547087602444;
    let q = 0.5003270373238773;
    let k = (PI * f0 / sr).tan();
    let b2 = [1f32, -2.0, 1.0];
    let a2 = [1f32, (2.0 * (k * k - 1.0) / (1.0 + k / q + k * k)) as f32, ((1.0 - k / q + k * k) / (1.0 + k / q + k * k)) as f32];

    // sum of two / three float products, left to right; with fma_coeffs the later product is fused
    let s2 = |p: f32, x: f32, y: f32| if fma_coeffs { x.mul_add(y, p) } else { p + x * y };
    let mul2 = |a: [f32; 3], b: [f32; 3]| -> [f32; 5] {
        [
            a[0] * b[0],
            s2(a[0] * b[1], a[1], b[0]),
            s2(s2(a[0] * b[2], a[1], b[1]), a[2], b[0]),
            s2(a[1] * b[2], a[2], b[1]),
            a[2] * b[2],
        ]
    };
    let b = mul2(b1, b2);
    let a = mul2(a1, a2);
    // IIR::configure normalises by a[0] (= 1, so exact)
    let n = a[0];
    (b.map(|v| v / n), a.map(|v| v / n))
}

#[inline(always)]
fn flush_subnormal(x: f32) -> f32 {
    if x.is_subnormal() { 0.0 } else { x }
}

/// Essentia's IIR (Direct Form II transposed, state line of 5, subnormals flushed), one channel.
fn iir(x: impl Iterator<Item = f32>, b: &[f32; 5], a: &[f32; 5], c: Contract, out: &mut Vec<f32>) {
    let mut s = [0f32; 5];
    for xn in x {
        let y = match c {
            Contract::Plain => b[0] * xn + s[0],
            _ => b[0].mul_add(xn, s[0]),
        };
        for k in 1..5 {
            let t = match c {
                Contract::Plain => b[k] * xn - a[k] * y,
                Contract::FmaBx => b[k].mul_add(xn, -(a[k] * y)),
                Contract::FmaAy => (-a[k]).mul_add(y, b[k] * xn),
            };
            s[k - 1] = t + s[k];
        }
        for k in 1..5 {
            s[k - 1] = flush_subnormal(s[k - 1]);
        }
        out.push(y);
    }
}

/// Start positions of FrameCutter's frames (startFromZero = true, lastFrameToEndOfFile = false): full
/// frames while one fits with room to spare, then one last zero-padded frame.
fn frame_starts(n: usize, frame: usize, hop: usize) -> Vec<usize> {
    let mut starts = Vec::new();
    let mut s = 0usize;
    loop {
        starts.push(s);
        if frame >= n - s {
            break; // acquireSize >= available: this is the last frame
        }
        s += hop;
        if s >= n {
            break;
        }
    }
    starts
}

/// Essentia's mean(): a float sum in order (the 8-way unroll still adds one value at a time), divided
/// by the full frame size; zero padding adds nothing.
fn frame_means(p: &[f32], frame: usize, hop: usize) -> Vec<f32> {
    frame_starts(p.len(), frame, hop)
        .into_par_iter()
        .map(|s| {
            let end = (s + frame).min(p.len());
            let mut sum = 0f32;
            for &v in &p[s..end] {
                sum += v;
            }
            sum / frame as f32
        })
        .collect()
}

fn power_to_lufs(p: f32) -> f32 {
    // Real power2loudness(Real power) { return 10 * log10(power) - 0.691; }  (the subtraction is double)
    ((10.0f32 * p.log10()) as f64 - 0.691) as f32
}

fn gated_mean(p: &[f32], threshold: f32) -> (f32, usize) {
    let (mut sum, mut n) = (0f32, 0usize);
    for &v in p {
        if v >= threshold {
            sum += v;
            n += 1;
        }
    }
    (sum, n)
}

struct Loudness {
    short_term: Vec<f32>,
    integrated: f32,
    range: f32,
}

fn loudness_impl(left: &[f32], right: &[f32], sr: f64, c: Contract, fma_coeffs: bool) -> Loudness {
    let (b, a) = k_weighting(sr, fma_coeffs);
    let (mut yl, mut yr) = (Vec::with_capacity(left.len()), Vec::with_capacity(right.len()));
    rayon::join(
        || iir(left.iter().copied(), &b, &a, c, &mut yl),
        || iir(right.iter().copied(), &b, &a, c, &mut yr),
    );
    // UnaryOperatorStream "square" on each channel, then BinaryOperatorStream "add"
    let power: Vec<f32> = yl.par_iter().zip(yr.par_iter()).map(|(&l, &r)| l * l + r * r).collect();
    drop((yl, yr));

    let hop = (0.1 * sr).round() as usize;
    let short_frame = (3.0 * sr) as usize; // int(3 * sampleRate): truncated, not rounded
    let block = (0.4 * sr).round() as usize;
    let (short_p, block_p) = rayon::join(|| frame_means(&power, short_frame, hop), || frame_means(&power, block, hop));

    // loudness2power(-70.): pow in double, returned as float
    let abs_thr = 10f64.powf((-70.0f32 as f64 + 0.691) / 10.0) as f32;

    // integrated: absolute gate, then relative gate 10 LU below
    let (sum, n) = gated_mean(&block_p, abs_thr);
    let thr = if n > 0 { (sum / n as f32 / 10.0).max(abs_thr) } else { abs_thr };
    let (sum, n) = gated_mean(&block_p, thr);
    let integrated = power_to_lufs(if n > 0 { sum / n as f32 } else { abs_thr });

    // loudness range from the short-term powers: absolute gate, relative gate 20 LU below, 10th-95th pct
    let (sum, n) = gated_mean(&short_p, abs_thr);
    let thr = if n > 0 { (sum / n as f32 / 100.0).max(abs_thr) } else { abs_thr };
    let mut gated: Vec<f32> = short_p.iter().copied().filter(|&v| v >= thr).collect();
    let range = if gated.is_empty() {
        0.0
    } else {
        gated.sort_by(|x, y| x.partial_cmp(y).unwrap());
        let hi = (0.95 * (gated.len() - 1) as f64).round() as usize;
        let lo = (0.1 * (gated.len() - 1) as f64).round() as usize;
        power_to_lufs(gated[hi]) - power_to_lufs(gated[lo])
    };

    // short-term loudness: log10 with Essentia's 1e-30 cutoff, *10, then + (-0.691) in float
    let cutoff = 1e-30f32;
    let short_term = short_p
        .iter()
        .map(|&p| {
            let l = if p < cutoff { cutoff.log10() } else { p.log10() };
            l * 10.0 + (-0.691f32)
        })
        .collect();
    Loudness { short_term, integrated, range }
}

fn read_f32(obj: &Bound<'_, PyAny>) -> PyResult<Vec<f32>> {
    let buf = PyBuffer::<f32>::get(obj)?;
    if !buf.is_c_contiguous() {
        return Err(PyValueError::new_err("expected a C-contiguous float32 array"));
    }
    buf.to_vec(obj.py())
}

/// (short-term LUFS as float32 bytes, integrated LUFS, loudness range LU) for a stereo signal.
/// `stereo`: float32 array of shape (n, 2), as Essentia's AudioLoader returns. `contract` / `fma_coeffs`
/// pick the float contraction (see Contract); the defaults are the ones that match Essentia here.
#[pyfunction]
#[pyo3(signature = (stereo, sample_rate, contract = 1, fma_coeffs = true))]
fn loudness_ebur128(py: Python<'_>, stereo: &Bound<'_, PyAny>, sample_rate: f64, contract: u8, fma_coeffs: bool)
    -> PyResult<(Py<PyAny>, f32, f32)> {
    let x = read_f32(stereo)?;
    if x.is_empty() || x.len() % 2 != 0 {
        return Err(PyValueError::new_err("expected a non-empty (n, 2) float32 array"));
    }
    let c = match contract {
        0 => Contract::Plain,
        1 => Contract::FmaBx,
        2 => Contract::FmaAy,
        _ => return Err(PyValueError::new_err("contract must be 0, 1 or 2")),
    };
    let r = py.detach(|| {
        let left: Vec<f32> = x.iter().step_by(2).copied().collect();
        let right: Vec<f32> = x.iter().skip(1).step_by(2).copied().collect();
        loudness_impl(&left, &right, sample_rate, c, fma_coeffs)
    });
    let bytes: Vec<u8> = r.short_term.iter().flat_map(|v| v.to_le_bytes()).collect();
    Ok((pyo3::types::PyBytes::new(py, &bytes).into_any().unbind(), r.integrated, r.range))
}

// ---------------------------------------------------------------------------------------------------
// Resampling: Essentia's standard Resample (libsamplerate 0.2.2 src_simple), quality 1 = sinc medium.
// ---------------------------------------------------------------------------------------------------

/// Output length to allocate for resample_sinc_medium (Essentia's own allowance).
#[pyfunction]
fn resample_max_len(n: usize, in_rate: f64, out_rate: f64) -> usize {
    resample::max_output_len(n, in_rate, out_rate)
}

/// Run `f` on `signal` and `out` (float32, C-contiguous, `out` writable, not overlapping) without copying
/// either, with the GIL released.
fn with_buffers<F>(py: Python<'_>, signal: &Bound<'_, PyAny>, out: &Bound<'_, PyAny>, f: F) -> PyResult<usize>
where
    F: FnOnce(&[f32], &mut [f32]) -> Result<usize, String> + Send,
{
    let ib = PyBuffer::<f32>::get(signal)?;
    let ob = PyBuffer::<f32>::get(out)?;
    if !ib.is_c_contiguous() || !ob.is_c_contiguous() || ob.readonly() {
        return Err(PyValueError::new_err("expected C-contiguous float32 arrays, `out` writable"));
    }
    let (ip, il) = (ib.buf_ptr() as usize, ib.item_count());
    let (op, ol) = (ob.buf_ptr() as usize, ob.item_count());
    if ip < op + ol * 4 && op < ip + il * 4 && il > 0 && ol > 0 {
        return Err(PyValueError::new_err("signal and out overlap"));
    }
    // SAFETY: both buffers stay alive (we hold the PyBuffers) and don't overlap; nothing else touches them
    // while the GIL is released for the duration of this call.
    let r = py.detach(|| {
        let x = unsafe { std::slice::from_raw_parts(ip as *const f32, il) };
        let o = unsafe { std::slice::from_raw_parts_mut(op as *mut f32, ol) };
        f(x, o)
    });
    drop((ib, ob));
    r.map_err(PyValueError::new_err)
}

/// Resample mono float32 `signal` into the float32 array `out` (at least resample_max_len long, C-contiguous,
/// writable); returns how many samples were written. Neither array is copied.
#[pyfunction]
#[pyo3(signature = (signal, out, in_rate, out_rate, icoeff_fma = false, acc_fma = false))]
fn resample_sinc_medium(py: Python<'_>, signal: &Bound<'_, PyAny>, out: &Bound<'_, PyAny>, in_rate: f64,
                        out_rate: f64, icoeff_fma: bool, acc_fma: bool) -> PyResult<usize> {
    let f = resample::Fused { icoeff: icoeff_fma, acc: acc_fma };
    with_buffers(py, signal, out, |x, o| resample::sinc_medium(x, o, in_rate, out_rate, f))
}

/// Essentia's Resample(quality=4) (libsamplerate's linear converter), like resample_sinc_medium. The arm64 build
/// fuses `x0 + t*(x1 - x0)`: the plain form was 1 sample off in 62.9 million on a 65-minute 48 kHz mix.
#[pyfunction]
#[pyo3(signature = (signal, out, in_rate, out_rate, fma = true))]
fn resample_linear(py: Python<'_>, signal: &Bound<'_, PyAny>, out: &Bound<'_, PyAny>, in_rate: f64,
                   out_rate: f64, fma: bool) -> PyResult<usize> {
    with_buffers(py, signal, out, |x, o| resample::linear(x, o, in_rate, out_rate, fma))
}

/// Number of mel frames musicnn_mels gives for a signal of n samples (FrameCutter 512/256, startFromZero=False).
#[pyfunction]
fn musicnn_frame_count(n: usize) -> usize {
    musicnn::frame_starts(n).len()
}

/// Essentia's TensorflowInputMusiCNN on every frame of a 16 kHz float32 `signal` (frames as FrameGenerator /
/// FrameCutter with frameSize 512, hopSize 256, startFromZero False), within a float step (more exact log10: math.rs),
/// into the float32 array `out` (at least musicnn_frame_count(len) * 96 long, C-contiguous, writable; row = frame);
/// returns the frame count.
/// streaming=True: frames as the streaming FrameCutter makes them (TensorflowPredictEffnetDiscogs; see musicnn::mels).
#[pyfunction]
#[pyo3(signature = (signal, out, streaming = false))]
fn musicnn_mels(py: Python<'_>, signal: &Bound<'_, PyAny>, out: &Bound<'_, PyAny>, streaming: bool) -> PyResult<usize> {
    with_buffers(py, signal, out, |x, o| {
        let n = musicnn::frame_starts(x.len()).len();
        if o.len() < n * musicnn::BANDS {
            return Err(format!("out holds {} values, {} needed", o.len(), n * musicnn::BANDS));
        }
        musicnn::mels(x, o, streaming);
        Ok(n)
    })
}

/// The 4096-point FFT of `signal` (4096 float32) into `out` (4098 float32: 2049 interleaved complex values), for
/// dsp/check.py to compare against FFTW itself.
#[pyfunction]
fn _rfft4096(py: Python<'_>, signal: &Bound<'_, PyAny>, out: &Bound<'_, PyAny>) -> PyResult<usize> {
    with_buffers(py, signal, out, |x, o| {
        let x: &[f32; 4096] = x.try_into().map_err(|_| "signal must hold 4096 values".to_string())?;
        let o: &mut [f32; 4098] = o.try_into().map_err(|_| "out must hold 4098 values".to_string())?;
        fft4096::rfft4096(x, o);
        Ok(4098)
    })
}

/// Essentia's KeyExtractor(profileType="edma", sampleRate=44100) on a 44.1 kHz mono float32 `signal` (more exact
/// libm: math.rs):
/// (key, "major" or "minor", strength). See key.rs.
#[pyfunction]
fn key_edma(py: Python<'_>, signal: &Bound<'_, PyAny>) -> PyResult<(&'static str, &'static str, f32)> {
    let buf = PyBuffer::<f32>::get(signal)?;
    if !buf.is_c_contiguous() {
        return Err(PyValueError::new_err("expected a C-contiguous float32 array"));
    }
    if buf.item_count() == 0 {
        return Err(PyValueError::new_err("KeyExtractor: empty input signal"));
    }
    let (p, n) = (buf.buf_ptr() as usize, buf.item_count());
    // SAFETY: the buffer stays alive (we hold it) and is only read while the GIL is released.
    let (k, minor, strength) = py.detach(|| {
        let x = unsafe { std::slice::from_raw_parts(p as *const f32, n) };
        key::key_of(&key::hpcps(x))
    });
    drop(buf);
    Ok((key::KEYS[k], if minor { "minor" } else { "major" }, strength))
}

/// Every frame's HPCP of key_edma into `out` (at least frames * 12 float32), for dsp/check.py; returns the frame count.
#[pyfunction]
fn _key_hpcps(py: Python<'_>, signal: &Bound<'_, PyAny>, out: &Bound<'_, PyAny>) -> PyResult<usize> {
    with_buffers(py, signal, out, |x, o| {
        let h = key::hpcps(x);
        if o.len() < h.len() * 12 {
            return Err(format!("out holds {} values, {} needed", o.len(), h.len() * 12));
        }
        for (k, f) in h.iter().enumerate() {
            o[12 * k..12 * k + 12].copy_from_slice(f);
        }
        Ok(h.len())
    })
}

/// key_edma's steps on one 4096-sample frame: (spectrum, peak frequencies, peak magnitudes, whitened, HPCP).
#[pyfunction]
fn _key_frame_steps(frame: &Bound<'_, PyAny>) -> PyResult<(Vec<f32>, Vec<f32>, Vec<f32>, Vec<f32>, Vec<f32>)> {
    let x = read_f32(frame)?;
    if x.len() != 4096 {
        return Err(PyValueError::new_err("expected 4096 samples"));
    }
    Ok(key::frame_steps(&x))
}

/// fftw.rs's FFTs for dsp/check.py to compare against FFTW: `kind` "r2c" (n samples -> n + 2 floats) or "c2r"
/// (n + 2 floats -> n samples), n a power of two up to 2^fftw::MAX_P. Returns the output length.
#[pyfunction]
fn _rfft(py: Python<'_>, kind: String, signal: &Bound<'_, PyAny>, out: &Bound<'_, PyAny>) -> PyResult<usize> {
    with_buffers(py, signal, out, |x, o| {
        let n = if kind == "r2c" { x.len() } else { o.len() };
        let fits = if kind == "r2c" { o.len() == n + 2 } else { x.len() == n + 2 };
        if !(fits && n.is_power_of_two() && n >= 2 && n.trailing_zeros() <= fftw::MAX_P) {
            return Err(format!("wrong sizes for {kind}"));
        }
        match kind.as_str() {
            "r2c" => fftw::r2c(x, o),
            "c2r" => fftw::c2r(&mut x.to_vec(), o),
            _ => return Err(format!("unknown kind {kind}")),
        }
        Ok(o.len())
    })
}

/// fftw::MAX_P, for dsp/check.py.
#[pyfunction]
fn _rfft_max_p() -> u32 {
    fftw::MAX_P
}

/// BeatTrackerMultiFeature's five onset detection functions (rms, complex, melflux, beat_emphasis, infogain), for
/// dsp/check.py.
#[pyfunction]
fn _bpm_odfs(py: Python<'_>, signal: &Bound<'_, PyAny>) -> PyResult<Vec<Vec<f32>>> {
    let x = read_f32(signal)?;
    let o = py.detach(|| bpm::onsets(&x));
    Ok(vec![o.rms, o.complex, o.melflux, o.beat_emphasis, o.infogain])
}

/// Essentia's RhythmExtractor2013(method="multifeature", minTempo, maxTempo) on a 44.1 kHz mono float32 signal:
/// (bpm, ticks in seconds, confidence). See bpm.rs; `order`: the order the five beat trackers draw rand() numbers in
/// (Essentia's: infogain, beat_emphasis, melflux, rms, complex).
#[pyfunction]
#[pyo3(signature = (signal, min_tempo = 40, max_tempo = 208, order = [4, 3, 2, 0, 1]))]
fn rhythm_multifeature(py: Python<'_>, signal: &Bound<'_, PyAny>, min_tempo: i32, max_tempo: i32, order: [usize; 5])
    -> PyResult<(f32, Vec<f32>, f32)> {
    let x = read_f32(signal)?;
    Ok(py.detach(|| bpm::rhythm(&x, min_tempo, max_tempo, order)))
}

/// One TempoTapDegara (resample 1 or 2) on an onset detection function, rand() from `seed` (as after srand(seed)),
/// for dsp/check.py: (ticks, the rand state after).
#[pyfunction]
fn _degara(odf: &Bound<'_, PyAny>, resample: usize, min_tempo: i32, max_tempo: i32, seed: u32) -> PyResult<(Vec<f32>, u32)> {
    let x = read_f32(odf)?;
    let mut r = bpm::Rand::seeded(seed);
    let t = bpm::Degara::new(resample, min_tempo, max_tempo).ticks(&x, &mut r);
    Ok((t, r.state()))
}

/// TempoTapMaxAgreement on candidate tick lists, for dsp/check.py: (ticks, confidence).
#[pyfunction]
fn _max_agreement(candidates: Vec<Vec<f32>>) -> (Vec<f32>, f32) {
    bpm::max_agreement(&candidates)
}

#[pymodule]
fn track_analyzer_dsp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(loudness_ebur128, m)?)?;
    m.add_function(wrap_pyfunction!(resample_max_len, m)?)?;
    m.add_function(wrap_pyfunction!(resample_sinc_medium, m)?)?;
    m.add_function(wrap_pyfunction!(resample_linear, m)?)?;
    m.add_function(wrap_pyfunction!(musicnn_frame_count, m)?)?;
    m.add_function(wrap_pyfunction!(musicnn_mels, m)?)?;
    m.add_function(wrap_pyfunction!(_rfft4096, m)?)?;
    m.add_function(wrap_pyfunction!(key_edma, m)?)?;
    m.add_function(wrap_pyfunction!(_rfft, m)?)?;
    m.add_function(wrap_pyfunction!(_rfft_max_p, m)?)?;
    m.add_function(wrap_pyfunction!(_bpm_odfs, m)?)?;
    m.add_function(wrap_pyfunction!(rhythm_multifeature, m)?)?;
    m.add_function(wrap_pyfunction!(_degara, m)?)?;
    m.add_function(wrap_pyfunction!(_max_agreement, m)?)?;
    m.add_function(wrap_pyfunction!(_key_hpcps, m)?)?;
    m.add_function(wrap_pyfunction!(_key_frame_steps, m)?)?;
    Ok(())
}
