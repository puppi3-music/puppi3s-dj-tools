//! The 4096-point real FFT as Essentia's macOS build computes it (KeyExtractor's Spectrum): FFTW 3.3.10,
//! fftwf_plan_dft_r2c_1d(4096, FFTW_ESTIMATE), whose plan is (fftwf_sprint_plan)
//!
//!     (rdft2-ct-dit/32 (hc2c-direct-32/62/0 "hc2cf_32" (rdft2-r2hc-direct-32 "r2cf_32")
//!                       (rdft2-r2hc01-direct-32 "r2cfII_32")) (rdft-vrank>=1-x16/1 (rdft-r2hc-direct-r2c-128-x2 "r2cf_128")))
//!
//! fft512's plan with m = 128 instead of 16: the same codelets plus r2cf_128 (fftw_codelets.rs), on the same memory
//! layout, in the same order, so every value matches fftwf_execute to the bit (docs/WINDOWS.md stage 5).

use crate::fftw_codelets as c;
use crate::key_tables::TW4096;

/// x: 4096 samples; out: 2049 complex values, interleaved (re, im), as FFTW's fftwf_complex output.
#[inline(never)]
pub fn rfft4096(x: &[f32; 4096], out: &mut [f32; 4098]) {
    // SAFETY: TW4096 holds f32 bit patterns; u32 and f32 have the same size and alignment.
    let w: &[f32; 3906] = unsafe { &*(TW4096.as_ptr() as *const [f32; 3906]) };
    // SAFETY: every offset stays inside x (4096) or out (4098), as FFTW's own plan does; the codelets only read
    // through the input pointers.
    unsafe {
        let xp = x.as_ptr() as *mut f32;
        let o = out.as_mut_ptr();
        // rdft2-ct-dit/32, first the child: 32 interleaved 128-point r2hc transforms. Sequence j = p + 2q is
        // x[j + 32t]; its halfcomplex output lands at out[p + 256q + 2k] (real parts), out[p + 256q + 256 - 2k] (imag).
        for q in 0..16isize {
            let i = xp.offset(2 * q);
            let ob = o.offset(256 * q);
            c::r2cf_128(i, i.offset(32), ob, ob.offset(256), 64, 2, -2, 2, 1, 1);
        }
        // then hc2c-direct-32 on cr = out, ci = out + 1 (rs = 256, ms = 2, m = 128)
        let (cr, ci) = (o, o.offset(1));
        c::r2cf_32(cr, ci, cr, ci, 256, 256, 256, 1, 0, 0);
        *ci = 0.0; // rdft2-r2hc-direct sets the DC and Nyquist imaginary parts
        *ci.offset(16 * 256) = 0.0;
        c::hc2cf_32(cr.offset(2), ci.offset(2), cr.offset(254), ci.offset(254), w.as_ptr(), 256, 1, 64, 2);
        c::r2cfII_32(cr.offset(128), ci.offset(128), cr.offset(128), ci.offset(128), 256, 256, 256, 1, 0, 0);
    }
}
