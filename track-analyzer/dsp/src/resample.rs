//! libsamplerate 0.2.2's SRC_SINC_MEDIUM_QUALITY, mono, as Essentia's standard Resample calls it
//! (`src_simple` on the whole signal, end_of_input = 1). Ported from src_sinc.c / samplerate.c
//! (2-clause BSD, Copyright (c) 2002-2021 Erik de Castro Lopo; see mid_qual_coeffs.rs for the licence).
//!
//! libsamplerate walks the input once, in order: a ring buffer it refills and shifts, and a fractional read
//! position that advances by 1/ratio per output sample. Where each output sample sits depends on that walk,
//! so the walk is replayed here step for step (positions only, no audio copied). Each output sample is then a
//! dot product that depends only on its position, so those run in parallel. Same float/double operations in
//! the same order as the C code, so the output is the same to the bit.

use crate::mid_qual_coeffs::{COEFFS_F64, INCREMENT};
use rayon::prelude::*;
use std::sync::OnceLock;

const SHIFT_BITS: u32 = 12;
const FP_ONE: f64 = (1i32 << SHIFT_BITS) as f64;
const INV_FP_ONE: f64 = 1.0 / FP_ONE;
const FP_MASK: i32 = (1 << SHIFT_BITS) - 1;
const SRC_MAX_RATIO: f64 = 256.0;

fn coeffs() -> &'static [f32] {
    static C: OnceLock<Vec<f32>> = OnceLock::new();
    C.get_or_init(|| COEFFS_F64.iter().map(|&v| v as f32).collect())
}

/// C's lrint in the default rounding mode: round half to even.
#[inline(always)]
fn lrint(x: f64) -> i64 {
    x.round_ties_even() as i64
}

#[inline(always)]
fn double_to_fp(x: f64) -> i32 {
    lrint(x * FP_ONE) as i32
}

#[inline(always)]
fn fmod_one(x: f64) -> f64 {
    let res = x - lrint(x) as f64;
    if res < 0.0 { res + 1.0 } else { res }
}

/// How the C compiler fused `a + b*c` into FMAs (see lib.rs Contract): in the coefficient interpolation
/// `icoeff = c[i] + fraction * (c[i+1] - c[i])` and in the accumulation `left += icoeff * x`.
#[derive(Clone, Copy)]
pub struct Fused {
    pub icoeff: bool,
    pub acc: bool,
}

/// calc_output_single over a virtual input `v(i)` (zeros before / after the signal, as libsamplerate's buffer).
#[inline(always)]
fn calc_output_single(v: &impl Fn(i64) -> f32, cur: i64, increment: i32, start: i32, half_len: i32, f: Fused) -> f64 {
    let c = coeffs();
    let tap = |fi: i32| -> f64 {
        let fraction = (fi & FP_MASK) as f64 * INV_FP_ONE;
        let i = (fi >> SHIFT_BITS) as usize;
        let d = (c[i + 1] - c[i]) as f64; // float subtraction, then widened
        if f.icoeff { fraction.mul_add(d, c[i] as f64) } else { c[i] as f64 + fraction * d }
    };
    let acc = |sum: f64, k: f64, x: f32| if f.acc { k.mul_add(x as f64, sum) } else { sum + k * x as f64 };
    let max_fi = half_len << SHIFT_BITS;

    // left half
    let mut fi = start;
    let count = (max_fi - fi) / increment;
    fi += count * increment;
    let mut di = cur - count as i64;
    let mut left = 0.0f64;
    while fi >= 0 {
        left = acc(left, tap(fi), v(di));
        fi -= increment;
        di += 1;
    }

    // right half
    let mut fi = increment - start;
    let count = (max_fi - fi) / increment;
    fi += count * increment;
    let mut di = cur + 1 + count as i64;
    let mut right = 0.0f64;
    loop {
        right = acc(right, tap(fi), v(di));
        fi -= increment;
        di -= 1;
        if fi <= 0 {
            break;
        }
    }
    left + right
}

/// The output length Essentia allows: (long)((double)size * factor + 100.0).
pub fn max_output_len(n: usize, in_sr: f64, out_sr: f64) -> usize {
    let factor = (out_sr as f32 / in_sr as f32) as f64; // Real / Real in Essentia, kept as double
    (n as f64 * factor + 100.0) as usize
}

/// Resample `x` into `out` (at least max_output_len long); returns the number of samples written.
pub fn sinc_medium(x: &[f32], out: &mut [f32], in_sr: f64, out_sr: f64, f: Fused) -> Result<usize, String> {
    let ratio = (out_sr as f32 / in_sr as f32) as f64;
    if ratio == 1.0 {
        out[..x.len()].copy_from_slice(x);
        return Ok(x.len());
    }
    if x.is_empty() {
        return Ok(0);
    }
    if ratio < 1.0 / SRC_MAX_RATIO || ratio > SRC_MAX_RATIO {
        return Err("bad sample rate ratio".into());
    }
    let out_count = max_output_len(x.len(), in_sr, out_sr) as i64;
    if out.len() < out_count as usize {
        return Err("output buffer too small".into());
    }

    let n = x.len() as i64;
    let half_len = (COEFFS_F64.len() - 2) as i32; // coeff_half_len
    let index_inc = INCREMENT;
    let b_len = {
        let b = 3 * lrint((half_len as f64 + 2.0) / index_inc as f64 * SRC_MAX_RATIO + 1.0);
        b.max(4096) + 1
    };
    let mut count = (half_len as f64 + 2.0) / index_inc as f64;
    if ratio < 1.0 {
        count /= ratio;
    }
    let half = lrint(count) + 1; // half_filter_chan_len (1 channel)

    let float_increment = index_inc as f64 * if ratio < 1.0 { ratio } else { 1.0 };
    let increment = double_to_fp(float_increment);
    let scale = float_increment / index_inc as f64;
    let terminate = 1.0 / ratio + 1e-20;

    // Furthest a tap reaches from the centre: (half_len << SHIFT_BITS) / increment samples to the left,
    // one more to the right. Away from the edges the signal is read directly; near them through `v`, which
    // supplies libsamplerate's zeros before and after the signal.
    let reach = ((half_len as i64) << SHIFT_BITS) / increment as i64 + 2;
    let v = |i: i64| -> f32 {
        let j = i - half;
        if j >= 0 && j < n { x[j as usize] } else { 0.0 }
    };
    let direct = |i: i64| -> f32 { x[(i - half) as usize] };
    let compute = |cur: i64, start: i32| -> f32 {
        let y = if cur - reach >= half && cur + reach < half + n {
            calc_output_single(&direct, cur, increment, start, half_len, f)
        } else {
            calc_output_single(&v, cur, increment, start, half_len, f)
        };
        (scale * y) as f32
    };

    // The replay below (positions only) runs on its own thread and hands over chunks of positions; the
    // dot products for each chunk run on the other cores meanwhile.
    const CHUNK: usize = 1 << 17;
    let (tx, rx) = std::sync::mpsc::sync_channel::<Vec<(i64, i32)>>(4);
    let replay = move || -> Result<(), String> {
        // sinc_mono_vari_process + prepare_data. `offset` = virtual index of buffer[0].
        let (mut b_current, mut b_end, mut b_real_end, mut in_used, mut offset) = (0i64, 0i64, -1i64, 0i64, 0i64);
        let mut input_index = 0.0f64;
        let mut out_gen = 0i64;
        let mut jobs: Vec<(i64, i32)> = Vec::with_capacity(CHUNK);
        while out_gen < out_count {
            let mut in_hand = (b_end - b_current + b_len) % b_len;
            if in_hand <= half {
                // prepare_data
                if b_real_end < 0 {
                    let mut len;
                    if b_current == 0 {
                        if in_used > 0 {
                            return Err("unexpected buffer state".into()); // libsamplerate would read stale data
                        }
                        len = b_len - 2 * half;
                        b_current = half;
                        b_end = half;
                    } else if b_end + half + 1 < b_len {
                        len = (b_len - b_current - half).max(0);
                    } else {
                        len = b_end - b_current;
                        offset += b_current - half;
                        b_current = half;
                        b_end = b_current + len;
                        len = (b_len - b_current - half).max(0);
                    }
                    len = len.min(n - in_used);
                    if len < 0 || b_end + len > b_len {
                        return Err("bad buffer length".into());
                    }
                    b_end += len;
                    in_used += len;
                    if in_used == n && b_end - b_current < 2 * half {
                        if b_len - b_end < half + 5 {
                            let len = b_end - b_current;
                            offset += b_current - half;
                            b_current = half;
                            b_end = b_current + len;
                        }
                        b_real_end = b_end;
                        let mut len = half + 5;
                        if b_end + len > b_len {
                            len = b_len - b_end;
                        }
                        b_end += len;
                    }
                }
                in_hand = (b_end - b_current + b_len) % b_len;
                if in_hand <= half {
                    break;
                }
            }
            if b_real_end >= 0 && b_current as f64 + input_index + terminate > b_real_end as f64 {
                break;
            }
            jobs.push((offset + b_current, double_to_fp(input_index * float_increment)));
            out_gen += 1;
            if jobs.len() == CHUNK {
                if tx.send(std::mem::replace(&mut jobs, Vec::with_capacity(CHUNK))).is_err() {
                    return Ok(());
                }
            }
            input_index += 1.0 / ratio;
            let rem = fmod_one(input_index);
            // `% b_len` in C; b_current stays below b_end <= b_len, so this is the same
            b_current += lrint(input_index - rem);
            if b_current >= b_len {
                b_current -= b_len;
            }
            input_index = rem;
        }
        if !jobs.is_empty() {
            let _ = tx.send(jobs);
        }
        Ok(())
    };

    let mut written = 0usize;
    let result = std::thread::scope(|scope| {
        let producer = scope.spawn(replay);
        for jobs in rx {
            out[written..written + jobs.len()]
                .par_iter_mut()
                .zip(jobs.par_iter())
                .for_each(|(o, &(cur, start))| *o = compute(cur, start));
            written += jobs.len();
        }
        producer.join().unwrap_or_else(|_| Err("resampler thread panicked".into()))
    });
    result.map(|_| written)
}
