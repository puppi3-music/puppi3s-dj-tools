//! The 512-point real FFT as Essentia's macOS build computes it: FFTW 3.3.10, fftwf_plan_dft_r2c_1d(512,
//! FFTW_ESTIMATE), whose plan is (fftwf_sprint_plan)
//!
//!     (rdft2-ct-dit/32 (hc2c-direct-32/62/0 "hc2cf_32" (rdft2-r2hc-direct-32 "r2cf_32")
//!                       (rdft2-r2hc01-direct-32 "r2cfII_32")) (rdft-vrank>=1-x16/1 (rdft-r2hc-direct-r2c-16-x2 "r2cf_16")))
//!
//! The same codelets (fftw_codelets.rs) on the same memory layout, in the same order, so every value matches
//! fftwf_execute to the bit (checked on 20,000 inputs, docs/WINDOWS.md stage 3).

use crate::fftw_codelets as c;
use crate::musicnn_tables::TW512;

/// x: 512 samples; out: 257 complex values, interleaved (re, im), as FFTW's fftwf_complex output.
pub fn rfft512(x: &[f32; 512], out: &mut [f32; 514]) {
    // SAFETY: TW512 holds f32 bit patterns; u32 and f32 have the same size and alignment.
    let w: &[f32; 434] = unsafe { &*(TW512.as_ptr() as *const [f32; 434]) };
    // SAFETY: every offset stays inside x (512) or out (514), as FFTW's own plan does; the codelets only read
    // through the input pointers.
    unsafe {
        let xp = x.as_ptr() as *mut f32;
        let o = out.as_mut_ptr();
        // rdft2-ct-dit/32, first the child: 32 interleaved 16-point r2hc transforms. Sequence j = p + 2q is
        // x[j + 32t]; its halfcomplex output lands at out[p + 32q + 2k] (real parts), out[p + 32q + 32 - 2k] (imag).
        for q in 0..16isize {
            let i = xp.offset(2 * q);
            let ob = o.offset(32 * q);
            c::r2cf_16(i, i.offset(32), ob, ob.offset(32), 64, 2, -2, 2, 1, 1);
        }
        // then hc2c-direct-32 on cr = out, ci = out + 1 (rs = 32, ms = 2, m = 16)
        let (cr, ci) = (o, o.offset(1));
        c::r2cf_32(cr, ci, cr, ci, 32, 32, 32, 1, 0, 0);
        *ci = 0.0; // rdft2-r2hc-direct sets the DC and Nyquist imaginary parts
        *ci.offset(16 * 32) = 0.0;
        c::hc2cf_32(cr.offset(2), ci.offset(2), cr.offset(30), ci.offset(30), w.as_ptr(), 32, 1, 8, 2);
        c::r2cfII_32(cr.offset(16), ci.offset(16), cr.offset(16), ci.offset(16), 32, 32, 32, 1, 0, 0);
    }
}
