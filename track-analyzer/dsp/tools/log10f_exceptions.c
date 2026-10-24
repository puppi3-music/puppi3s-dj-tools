/* Every float x >= 1 where `(float)log10((double)x)` may not give macOS's log10f(x): where they differ on this Mac,
 * and where the double result lies within 8 double ulps of a float rounding boundary (another libm's log10, which
 * may be off by an ulp or two, could round those the other way). Prints "x_bits log10f_bits" per line.
 * Run on macOS (its libm is the one Essentia calls): clang -O2 -ffp-contract=off log10f_exceptions.c && ./a.out */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    float one = 1.0f;
    uint32_t start;
    memcpy(&start, &one, 4);
    for (uint32_t u = start; u < 0x7f800000u; u++) {
        float x;
        memcpy(&x, &u, 4);
        volatile float apple = log10f(x);
        double y = log10((double)x);
        float r = (float)y;
        int risky = apple != r;
        if (!risky && y != (double)r) {
            float nb = nextafterf(r, y > (double)r ? INFINITY : -INFINITY);
            double mid = ((double)r + (double)nb) / 2;
            risky = fabs(y - mid) <= 8 * (nextafter(y, INFINITY) - y);
        }
        if (risky) {
            float a = apple;
            uint32_t ua;
            memcpy(&ua, &a, 4);
            printf("%08x %08x\n", u, ua);
        }
    }
    return 0;
}
