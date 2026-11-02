//! FFTW 3.3.10's real FFTs of any power-of-two size, as Essentia's macOS build plans them with FFTW_ESTIMATE
//! (fftwf_plan_dft_r2c_1d / fftwf_plan_dft_c2r_1d; fftwf_sprint_plan shows each plan, dsp/check.py compares every size
//! against the wheel's fftwf_execute). The BPM's autocorrelations need them: one FFT of the next power of two above
//! twice the onset function's length, for every band.
//!
//! The plans, by n = 2^p:
//!
//!   p <= 7     rdft2-r2hc-direct-n "r2cf_n" / rdft2-hc2r-direct-n "r2cb_n"
//!   p >= 8     rdft2-ct-dit/32 (hc2c-direct-32 "hc2cf_32" "r2cf_32" "r2cfII_32") over 32 transforms of m = n/32 (r2c);
//!              rdft2-ct-dif/r (hc2c-direct-r "hc2cb_r" "r2cb_r" "r2cbIII_r") over r transforms of m = n/r (c2r),
//!              r = 8 for p <= 10, then 16, 32, 16, 32, 16, 16, 32, 16, 32 (C2R_RADIX)
//!
//! where each m-point child (an rdft problem, halfcomplex order) is a direct codelet up to 128 points, else
//! rdft-ct-dit/r (hc2hc-direct-r "hf2_r" "r2cf_r" "r2cfII_r") or rdft-ct-dif/r (hc2hc-direct-r "hb2_r" "r2cb_r"
//! "r2cbIII_r") over transforms of m/r points, r = 16 for 256 and 512 forward and 512 inverse, else 32. The vector loops
//! FFTW puts around them (rdft-vrank>=1) only run independent transforms, so their order doesn't matter.
//!
//! From p = 20 the plans go through one complex DFT of n/2 points (p = 20, 21; radix 2) or two of n/4 (p = 22; radix 4):
//!
//!   rdft2-ct-dit/r (hc2c-direct-r "hc2cfdft_r" "r2cf_r" "r2cfII_r") (dft ...), the even samples as real parts and the
//!   odd ones as imaginary; c2r: rdft2-ct-dif/r (hc2c-direct-r "hc2cbdft_2" / "hc2cbdft2_4" ...) and the DFT with real
//!   and imaginary swapped (FFTW's inverse)
//!
//! and the DFTs (dft/ct.c, dftw-direct.c, dftw-genericbuf.c, direct.c) are dft-ct-dit/r with radix 16 for 512 points,
//! 32 for 1024 (twiddles by "t2_16" / "t2_32" over 32-point "n1_32" codelets), 512 for 2^19 and 1024 for 2^20: those
//! twiddle step is dftw-genericbuf, which multiplies batches of 4 columns by twiddles computed on the spot (rotate:
//! FFTW's trig table in double, fused, then rounded) into a buffer, runs a 512- or 1024-point DFT on them there, and
//! copies them back. That DFT runs in place, where FFTW buffers its codelet's inputs first (dft-directbuf, one batch);
//! here it reads from a copy, the same values.
//!
//! The strides are FFTW's own (rdft/ct-hc2c.c, hc2hc.c, hc2hc-direct.c, direct-r2c.c, direct2.c), so each codelet sees
//! the same values in the same order. The twiddles are FFTW's: its sqrt(n) table (kernel/trig.c) has entries in double
//! from real_cexp, here a table made on macOS (bpm_tables::TRIG_*), and each twiddle is W1 * W0 in double, fused as
//! gcc fuses it, rounded to float.

use crate::bpm_tables::{TRIG_C, TRIG_K, TRIG_P, TRIG_S};
use crate::fftw_codelets as c;
use std::sync::OnceLock;

/// The largest supported size, 2^MAX_P: a mix of up to about 3 h 20 min (onset frames 86 per second, FFT of twice that).
pub const MAX_P: u32 = 22;

type R2c = unsafe fn(*mut f32, *mut f32, *mut f32, *mut f32, isize, isize, isize, isize, isize, isize);
type Hc2c = unsafe fn(*mut f32, *mut f32, *mut f32, *mut f32, *const f32, isize, isize, isize, isize);
type Hc2hc = unsafe fn(*mut f32, *mut f32, *const f32, isize, isize, isize, isize);
type N1 = unsafe fn(*const f32, *const f32, *mut f32, *mut f32, isize, isize, isize, isize, isize);
type T2 = unsafe fn(*mut f32, *mut f32, *const f32, isize, isize, isize, isize);

/// The c2r plans' top radix for p = 8 ..= 22.
const C2R_RADIX: [isize; 15] = [8, 8, 8, 16, 32, 16, 32, 16, 16, 32, 16, 32, 2, 2, 4];

fn r2cf(n: isize) -> R2c {
    match n {
        2 => c::r2cf_2,
        4 => c::r2cf_4,
        8 => c::r2cf_8,
        16 => c::r2cf_16,
        32 => c::r2cf_32,
        64 => c::r2cf_64,
        128 => c::r2cf_128,
        _ => unreachable!("no r2cf_{n}"),
    }
}

fn r2cb(n: isize) -> R2c {
    match n {
        2 => c::r2cb_2,
        4 => c::r2cb_4,
        8 => c::r2cb_8,
        16 => c::r2cb_16,
        32 => c::r2cb_32,
        64 => c::r2cb_64,
        128 => c::r2cb_128,
        _ => unreachable!("no r2cb_{n}"),
    }
}

/// real_cexp(k, 2^TRIG_P) from the table: (cos, sin) of 2 pi k / 2^TRIG_P, as FFTW computes them.
fn trig(k: u32) -> (f64, f64) {
    let i = TRIG_K.binary_search(&k).unwrap_or_else(|_| panic!("no trig entry for {k}"));
    (f64::from_bits(TRIG_C[i]), f64::from_bits(TRIG_S[i]))
}

/// e^(2 pi i m / n) in double from FFTW's n-point sqrt(n) table (cexpl_sqrtn_table: W1[m >> shift] * W0[m & mask]).
fn cexp_d(m: usize, n: usize) -> (f64, f64) {
    let p = n.trailing_zeros();
    let shift = p / 2 + 1; // choose_twshft
    let scale = TRIG_P - p;
    let m0 = m & ((1 << shift) - 1);
    let m1 = m >> shift;
    let (wr0, wi0) = trig((m0 << scale) as u32);
    let (wr1, wi1) = trig(((m1 << shift) << scale) as u32);
    (wr1.mul_add(wr0, -(wi1 * wi0)), wi1.mul_add(wr0, wr1 * wi0))
}

/// FFTW's cexp(m) for an n-point twiddle table, rounded to float.
fn cexp(m: usize, n: usize) -> [f32; 2] {
    let (wr, wi) = cexp_d(m, n);
    [wr as f32, wi as f32]
}

/// rotate_sqrtn_table (FFT_SIGN -1): (xr + i xi) times the conjugate of cexp(m), in double, fused as gcc fuses it.
fn rotate(m: usize, n: usize, xr: f32, xi: f32) -> (f32, f32) {
    let (wr, wi) = cexp_d(m, n);
    let (xr, xi) = (xr as f64, xi as f64);
    (xr.mul_add(wr, xi * wi) as f32, xi.mul_add(wr, -(xr * wi)) as f32)
}

/// Which twiddle table: rdft2's hc2c step forward / inverse, rdft's hc2hc step forward / inverse. The radix follows
/// from the kind and n.
#[derive(Clone, Copy)]
enum Tw {
    Hc2cF = 0,
    Hc2cB = 1,
    Hc2hcF = 2,
    Hc2hcB = 3,
    T2 = 4,
}

/// The twiddles of an n-point step of radix r (m = n / r): for each m index 1 .. (m-1)/2 (the DFT's t2: 0 .. m-1),
/// cexp(j * i) for every i in `instr` (TW_FULL: 1 .. r-1; the "2" codelets' TW_CEXP list). Made on first use and kept
/// (lock-free afterwards).
fn twiddles(kind: Tw, n: usize, r: usize, instr: &[usize]) -> &'static [f32] {
    static CACHE: [[OnceLock<Vec<f32>>; 23]; 5] = [const { [const { OnceLock::new() }; 23] }; 5];
    CACHE[kind as usize][n.trailing_zeros() as usize].get_or_init(|| {
        let m = n / r;
        let js = if let Tw::T2 = kind { 0..m } else { 1..(m - 1) / 2 + 1 };
        let mut w = Vec::with_capacity(js.len() * instr.len() * 2);
        for j in js {
            for &i in instr {
                w.extend(cexp(j * i, n));
            }
        }
        w
    })
}

const FULL2: [usize; 1] = [1];
const FULL4: [usize; 3] = [1, 2, 3];
const FULL8: [usize; 7] = [1, 2, 3, 4, 5, 6, 7];
const FULL16: [usize; 15] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15];
const FULL32: [usize; 31] = [
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
];
const CEXP16: [usize; 4] = [1, 3, 9, 15]; // hf2_16 / hb2_16's twinstr
const CEXP32: [usize; 4] = [1, 3, 9, 27]; // hf2_32 / hb2_32's, t2_32's

/// rdft R2HC of n points, input stride is, halfcomplex output stride os, over the vector loops `vecs` (vl, ivs, ovs);
/// the last one goes to the codelet.
unsafe fn r2hc(n: isize, i: *mut f32, o: *mut f32, is: isize, os: isize, vecs: &[(isize, isize, isize)]) {
    unsafe {
        let (&(vl, ivs, ovs), outer) = vecs.split_last().unwrap();
        if let Some((&(ol, oi, oo), rest)) = outer.split_first() {
            let mut inner = rest.to_vec();
            inner.push((vl, ivs, ovs));
            for k in 0..ol {
                r2hc(n, i.offset(k * oi), o.offset(k * oo), is, os, &inner);
            }
        } else if n <= 128 {
            r2cf(n)(i, i.offset(is), o, o.offset(n * os), 2 * is, os, -os, vl, ivs, ovs);
        } else {
            for k in 0..vl {
                r2hc_ct(n, i.offset(k * ivs), o.offset(k * ovs), is, os);
            }
        }
    }
}

/// rdft-ct-dit/r: m-point transforms of every r-th input, then hc2hc-direct-r in place on the output.
unsafe fn r2hc_ct(n: isize, i: *mut f32, o: *mut f32, is: isize, os: isize) {
    unsafe {
        let (r, kw, cexps): (isize, Hc2hc, &[usize]) =
            if n <= 512 { (16, c::hf2_16, &CEXP16) } else { (32, c::hf2_32, &CEXP32) };
        let m = n / r;
        r2hc(m, i, o, r * is, os, &[(r, is, m * os)]);
        let rs = m * os;
        r2cf(r)(o, o.offset(rs), o, o.offset(r * rs), 2 * rs, rs, -rs, 1, 0, 0);
        let w = twiddles(Tw::Hc2hcF, n as usize, r as usize, cexps);
        kw(o.offset(os), o.offset((m - 1) * os), w.as_ptr(), rs, 1, m / 2, os);
        let mid = o.offset(m / 2 * os);
        let k2: R2c = if r == 16 { c::r2cfII_16 } else { c::r2cfII_32 };
        k2(mid, mid.offset(rs), mid, mid.offset((r - 1) * rs), 2 * rs, rs, -rs, 1, 0, 0);
    }
}

/// rdft HC2R of n points, halfcomplex input stride is (overwritten), output stride os, over the vector loops `vecs`.
unsafe fn hc2r(n: isize, i: *mut f32, o: *mut f32, is: isize, os: isize, vecs: &[(isize, isize, isize)]) {
    unsafe {
        let (&(vl, ivs, ovs), outer) = vecs.split_last().unwrap();
        if let Some((&(ol, oi, oo), rest)) = outer.split_first() {
            let mut inner = rest.to_vec();
            inner.push((vl, ivs, ovs));
            for k in 0..ol {
                hc2r(n, i.offset(k * oi), o.offset(k * oo), is, os, &inner);
            }
        } else if n <= 128 {
            r2cb(n)(o, o.offset(os), i, i.offset(n * is), 2 * os, is, -is, vl, ivs, ovs);
        } else {
            for k in 0..vl {
                hc2r_ct(n, i.offset(k * ivs), o.offset(k * ovs), is, os);
            }
        }
    }
}

/// rdft-ct-dif/r: hc2hc-direct-r in place on the input, then m-point transforms into every r-th output.
unsafe fn hc2r_ct(n: isize, i: *mut f32, o: *mut f32, is: isize, os: isize) {
    unsafe {
        let (r, kw, cexps): (isize, Hc2hc, &[usize]) =
            if n <= 512 { (16, c::hb2_16, &CEXP16) } else { (32, c::hb2_32, &CEXP32) };
        let m = n / r;
        let rs = m * is;
        r2cb(r)(i, i.offset(rs), i, i.offset(r * rs), 2 * rs, rs, -rs, 1, 0, 0);
        let w = twiddles(Tw::Hc2hcB, n as usize, r as usize, cexps);
        kw(i.offset(is), i.offset((m - 1) * is), w.as_ptr(), rs, 1, m / 2, is);
        let mid = i.offset(m / 2 * is);
        let k2: R2c = if r == 16 { c::r2cbIII_16 } else { c::r2cbIII_32 };
        k2(mid, mid.offset(rs), mid, mid.offset((r - 1) * rs), 2 * rs, rs, -rs, 1, 0, 0);
        hc2r(m, i, o, is, r * os, &[(r, m * is, os)]);
    }
}

/// A complex DFT of n points (FFTW's split pointers: real and imaginary parts, here interleaved), input stride is, output
/// stride os, over the vector loops `vecs`; the last one goes to the codelet.
#[allow(clippy::too_many_arguments)]
unsafe fn dft(n: isize, ri: *mut f32, ii: *mut f32, ro: *mut f32, io: *mut f32, is: isize, os: isize, vecs: &[(isize, isize, isize)]) {
    unsafe {
        let (&(vl, ivs, ovs), outer) = vecs.split_last().unwrap();
        if let Some((&(ol, oi, oo), rest)) = outer.split_first() {
            let mut inner = rest.to_vec();
            inner.push((vl, ivs, ovs));
            for k in 0..ol {
                dft(n, ri.offset(k * oi), ii.offset(k * oi), ro.offset(k * oo), io.offset(k * oo), is, os, &inner);
            }
        } else if n == 32 {
            (c::n1_32 as N1)(ri, ii, ro, io, is, os, vl, ivs, ovs);
        } else {
            for k in 0..vl {
                dft_ct(n, ri.offset(k * ivs), ii.offset(k * ivs), ro.offset(k * ovs), io.offset(k * ovs), is, os);
            }
        }
    }
}

/// dft-ct-dit/r: m-point DFTs of every r-th input, then the twiddle step in place on the output.
unsafe fn dft_ct(n: isize, ri: *mut f32, ii: *mut f32, ro: *mut f32, io: *mut f32, is: isize, os: isize) {
    unsafe {
        let r = match n {
            512 => 16,
            1024 => 32,
            524288 => 512,
            1048576 => 1024,
            _ => unreachable!("no DFT plan for {n} points"),
        };
        let m = n / r;
        dft(m, ri, ii, ro, io, r * is, os, &[(r, is, m * os)]);
        let (rs, ms) = (m * os, os);
        if r <= 32 {
            let (k, cexps): (T2, &[usize]) = if r == 16 { (c::t2_16, &CEXP16) } else { (c::t2_32, &CEXP32) };
            let w = twiddles(Tw::T2, n as usize, r as usize, cexps);
            k(ro, io, w.as_ptr(), rs, 0, m, ms);
        } else {
            genericbuf(n as usize, r, m, rs, ms, ro, io);
        }
    }
}

/// dftw-genericbuf/4-r-m: for each batch of 4 columns k, x[j rs + k ms] times the conjugate of cexp(j k) into a buffer
/// (rows BATCHDIST = r + 16 complex values apart), r-point DFTs there, and back.
unsafe fn genericbuf(n: usize, r: isize, m: isize, rs: isize, ms: isize, rio: *mut f32, iio: *mut f32) {
    const BATCH: isize = 4;
    let bd = 2 * (r + 16);
    let mut buf = vec![0f32; (bd * BATCH) as usize];
    let mut src = buf.clone();
    unsafe {
        let mut mb = 0;
        while mb < m {
            for j in 0..r {
                for k in mb..mb + BATCH {
                    let at = j * rs + k * ms;
                    let (yr, yi) = rotate((j * k) as usize, n, *rio.offset(at), *iio.offset(at));
                    let b = (2 * j + bd * (k - mb)) as usize;
                    buf[b] = yr;
                    buf[b + 1] = yi;
                }
            }
            src.copy_from_slice(&buf);
            let (s, b) = (src.as_mut_ptr(), buf.as_mut_ptr());
            dft(r, s, s.offset(1), b, b.offset(1), 2, 2, &[(BATCH, bd, bd)]);
            for j in 0..r {
                for k in 0..BATCH {
                    let at = ms * (mb + k) + j * rs;
                    let b = (bd * k + 2 * j) as usize;
                    *rio.offset(at) = buf[b];
                    *iio.offset(at) = buf[b + 1];
                }
            }
            mb += BATCH;
        }
    }
}

fn check_size(n: usize) {
    assert!(n.is_power_of_two() && n >= 2 && n.trailing_zeros() <= MAX_P, "no FFTW plan ported for {n} points");
}

/// x: n samples (n a power of two, 2 ..= 2^MAX_P); out: n/2 + 1 complex values, interleaved, as
/// fftwf_plan_dft_r2c_1d(n, FFTW_ESTIMATE).
pub fn r2c(x: &[f32], out: &mut [f32]) {
    let n = x.len();
    check_size(n);
    assert_eq!(out.len(), n + 2);
    let n = n as isize;
    // SAFETY: every offset stays inside x or out, as FFTW's own plan does; the codelets only read through x.
    unsafe {
        let x = x.as_ptr() as *mut f32;
        let (cr, ci) = (out.as_mut_ptr(), out.as_mut_ptr().offset(1));
        if n <= 128 {
            r2cf(n)(x, x.offset(1), cr, ci, 2, 2, 2, 1, 0, 0);
            *ci = 0.0; // rdft2-r2hc-direct sets the DC and Nyquist imaginary parts
            *ci.offset(n) = 0.0;
            return;
        }
        let p = n.trailing_zeros();
        let r = if p <= 19 { 32 } else if p <= 21 { 2 } else { 4 };
        let m = n / r;
        let (k0, kw, km, full): (R2c, Hc2c, R2c, &[usize]) = match r {
            32 => {
                r2hc(m, x, cr, 32, 2, &[(16, 2, 2 * m), (2, 1, 1)]);
                (c::r2cf_32, c::hc2cf_32, c::r2cfII_32, &FULL32)
            }
            2 => {
                dft(m, x, x.offset(1), cr, ci, 2, 2, &[(1, 0, 0)]);
                (c::r2cf_2, c::hc2cfdft_2, c::r2cfII_2, &FULL2)
            }
            _ => {
                dft(m, x, x.offset(1), cr, ci, 4, 2, &[(2, 2, 2 * m)]);
                (c::r2cf_4, c::hc2cfdft_4, c::r2cfII_4, &FULL4)
            }
        };
        k0(cr, ci, cr, ci, 2 * m, 2 * m, 2 * m, 1, 0, 0);
        *ci = 0.0;
        *ci.offset(r / 2 * 2 * m) = 0.0;
        let w = twiddles(Tw::Hc2cF, n as usize, r as usize, full);
        kw(cr.offset(2), ci.offset(2), cr.offset(2 * m - 2), ci.offset(2 * m - 2), w.as_ptr(), 2 * m, 1, (m + 1) / 2, 2);
        km(cr.offset(m), ci.offset(m), cr.offset(m), ci.offset(m), 2 * m, 2 * m, 2 * m, 1, 0, 0);
    }
}

/// The inverse of n/2 + 1 complex values (cplx, overwritten) into n samples, unnormalised, as
/// fftwf_plan_dft_c2r_1d(n, FFTW_ESTIMATE).
pub fn c2r(cplx: &mut [f32], out: &mut [f32]) {
    let n = out.len();
    check_size(n);
    assert_eq!(cplx.len(), n + 2);
    let p = n.trailing_zeros();
    let n = n as isize;
    // SAFETY: every offset stays inside cplx or out, as FFTW's own plan does.
    unsafe {
        let (cr, ci) = (cplx.as_mut_ptr(), cplx.as_mut_ptr().offset(1));
        let o = out.as_mut_ptr();
        if n <= 128 {
            r2cb(n)(o, o.offset(1), cr, ci, 2, 2, 2, 1, 0, 0);
            return;
        }
        let r = C2R_RADIX[p as usize - 8];
        let m = n / r;
        let (k0, kw, km, full): (R2c, Hc2c, R2c, &[usize]) = match r {
            2 => (c::r2cb_2, c::hc2cbdft_2, c::r2cbIII_2, &FULL2),
            4 => (c::r2cb_4, c::hc2cbdft2_4, c::r2cbIII_4, &FULL4),
            8 => (c::r2cb_8, c::hc2cb_8, c::r2cbIII_8, &FULL8),
            16 => (c::r2cb_16, c::hc2cb_16, c::r2cbIII_16, &FULL16),
            _ => (c::r2cb_32, c::hc2cb_32, c::r2cbIII_32, &FULL32),
        };
        k0(cr, ci, cr, ci, 2 * m, 2 * m, 2 * m, 1, 0, 0);
        let w = twiddles(Tw::Hc2cB, n as usize, r as usize, full);
        kw(cr.offset(2), ci.offset(2), cr.offset(2 * m - 2), ci.offset(2 * m - 2), w.as_ptr(), 2 * m, 1, (m + 1) / 2, 2);
        km(cr.offset(m), ci.offset(m), cr.offset(m), ci.offset(m), 2 * m, 2 * m, 2 * m, 1, 0, 0);
        if r >= 8 {
            hc2r(m, cr, o, 2, r, &[(r / 2, 2 * m, 2), (2, 1, 1)]);
        } else {
            // apply_dif_dft: the DFT with real and imaginary parts swapped, into r1 (= out + 1) and r0
            dft(m, ci, cr, o.offset(1), o, 2, r, &[(r / 2, 2 * m, 2)]);
        }
    }
}
