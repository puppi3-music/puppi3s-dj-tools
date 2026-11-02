//! The float maths functions key.rs and bpm.rs call where Essentia calls libm: each computed in double by the `libm`
//! crate (a Rust port of musl's, within an ulp of the exact double) and rounded to float, which gives the nearest float
//! but on a rare input within a double ulp of halfway. The same bits on every platform, and as close to the exact
//! result as float allows: Essentia's own float libm calls are a float step off on some inputs (macOS's atan2f on
//! about 13% of them; docs/WINDOWS.md stage 6).

pub fn logf(x: f32) -> f32 {
    ::libm::log(x as f64) as f32
}

/// essentia::log2 on float (logf(x) / M_LN2 in C++), here log2 itself.
pub fn log2f(x: f32) -> f32 {
    ::libm::log2(x as f64) as f32
}

pub fn log10f(x: f32) -> f32 {
    ::libm::log10(x as f64) as f32
}

pub fn expf(x: f32) -> f32 {
    ::libm::exp(x as f64) as f32
}

/// powf(10, x).
pub fn exp10f(x: f32) -> f32 {
    ::libm::pow(10.0, x as f64) as f32
}

/// (sin x, cos x).
pub fn sincosf(x: f32) -> (f32, f32) {
    let (s, c) = ::libm::sincos(x as f64);
    (s as f32, c as f32)
}

/// cos(x) of a double, rounded (HPCP's weight cos(M_PI * d) is computed in double).
pub fn cos(x: f64) -> f32 {
    ::libm::cos(x) as f32
}

pub fn atan2f(y: f32, x: f32) -> f32 {
    ::libm::atan2(y as f64, x as f64) as f32
}
