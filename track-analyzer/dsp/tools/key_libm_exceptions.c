/* The inputs where key.rs can't just round a double result to float and get what Essentia's macOS build gets:
 * where macOS's function differs from that, or where the double lies within 8 double ulps of a float rounding
 * boundary (another libm, off by an ulp or two, could round those the other way). Prints "x_bits result_bits".
 *
 *   logf:   logf(x)                   vs (float)log((double)x),       every positive finite float
 *   log10f: log10f(x)                 vs (float)log10((double)x),     every float >= 1e-10 (lin2db's cutoff)
 *   exp10f: __exp10f(x)               vs (float)pow(10, (double)x),   every float in [-12, 0] (db2lin of whitened dB)
 *   cospi:  (float)cos(M_PI * (double)d) itself,                      every float d in [0, 0.5] (HPCP's weights)
 *
 * Run on macOS (its libm is the one Essentia calls): clang -O2 -ffp-contract=off key_libm_exceptions.c && ./a.out logf */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern float __exp10f(float);

static float f_of(uint32_t u) { float x; memcpy(&x, &u, 4); return x; }
static uint32_t u_of(float x) { uint32_t u; memcpy(&u, &x, 4); return u; }

/* Is double y within 8 ulps of the midpoint between float r = (float)y and its neighbour on y's side? */
static int near_boundary(double y, float r) {
    if (y == (double)r) return 0;
    float nb = nextafterf(r, y > (double)r ? INFINITY : -INFINITY);
    double mid = ((double)r + (double)nb) / 2;
    return fabs(y - mid) <= 8 * (nextafter(fabs(y), INFINITY) - fabs(y));
}

static void scan(uint32_t lo, uint32_t hi, int which) {  /* bit patterns lo..hi inclusive */
    for (uint64_t k = lo; k <= hi; k++) {
        uint32_t u = (uint32_t)k;
        float x = f_of(u);
        volatile float apple;
        double y;
        switch (which) {
        case 0: apple = logf(x); y = log((double)x); break;
        case 1: apple = log10f(x); y = log10((double)x); break;
        case 2: apple = __exp10f(x); y = pow(10.0, (double)x); break;
        default: y = cos(M_PI * (double)x); apple = (float)y; break;
        }
        float r = (float)y;
        if (apple != r || near_boundary(y, r)) printf("%08x %08x\n", u, u_of(apple));
    }
}

int main(int argc, char **argv) {
    const char *f = argc > 1 ? argv[1] : "";
    if (!strcmp(f, "logf")) scan(0x00000001u, 0x7f7fffffu, 0);
    else if (!strcmp(f, "log10f")) scan(u_of(1e-10f), 0x7f7fffffu, 1);
    else if (!strcmp(f, "exp10f")) { scan(0x80000000u, u_of(-12.0f), 2); scan(0x00000000u, 0x00000000u, 2); }
    else if (!strcmp(f, "cospi")) scan(0x00000000u, u_of(0.5f), 3);
    else { fprintf(stderr, "usage: %s logf|log10f|exp10f|cospi\n", argv[0]); return 2; }
    return 0;
}
