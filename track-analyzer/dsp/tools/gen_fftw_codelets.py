"""Translate the FFTW 3.3.10 scalar codelets of the 512-point real FFT into Rust (src/fftw_codelets.rs).

    curl -LO https://www.fftw.org/fftw-3.3.10.tar.gz   # md5 8ccbf6a5ea78a16dbc3e1306e234cc5c
    tar xzf fftw-3.3.10.tar.gz
    python3 dsp/tools/gen_fftw_codelets.py fftw-3.3.10 dsp/src/fftw_codelets.rs

Essentia's macOS FFTW is built with gcc -O3 and without ARCH_PREFERS_FMA, so it runs each codelet's plain variant
(#else branch), and gcc then contracts products into fused multiply-adds by itself (-ffp-contract=fast): every
multiply whose results only feed adds/subtracts, across statements, multiplies taken in program order. That is
modelled here with explicit mul_add, which is exact on every platform. Checked against the wheel's own
compiled codelets (called at their addresses) on random inputs, and the whole FFT against fftwf_execute.
"""
import re
import sys
from pathlib import Path

CODELETS = [("r2cf", "r2cf_16"), ("r2cf", "r2cf_32"), ("r2cf", "r2cfII_32"), ("hc2c", "hc2cf_32")]


def fma_section(src: str, variant: str = "plain") -> str:
    """The codelet's FMA variant (#if ARCH_PREFERS_FMA) or its plain one (#else). Essentia's macOS FFTW has the plain
    one: its plan's operation count (fftwf_flops, 838 FMAs for 512 points) adds up to the plain codelets' counts."""
    a = src.index("#if defined(ARCH_PREFERS_FMA)")
    b = src.index("\n#else", a)
    return src[a:b] if variant == "fma" else src[b:src.index("\n#endif", b)]


def body_of(sec: str, name: str) -> tuple[str, str]:
    i = sec.index(f"static void {name}(")
    j = sec.index("{", i)
    depth, k = 0, j
    while True:
        if sec[k] == "{":
            depth += 1
        elif sec[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return sec[i:j], sec[j + 1:k]


# --- a tiny expression parser for the codelet statements ---------------------------------------------------------
TOK = re.compile(r"\s*(?:(?P<num>\d+)|(?P<id>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>[-+*(),\[\]]))")


def tokenize(s):
    out, pos = [], 0
    s = s.strip()
    while pos < len(s):
        m = TOK.match(s, pos)
        if not m or m.end() == pos:
            raise ValueError(f"can't tokenize: {s[pos:]!r} in {s!r}")
        pos = m.end()
        out.append(m.group("num") or m.group("id") or m.group("op"))
    return out


class P:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def eat(self, x=None):
        v = self.t[self.i]
        if x is not None and v != x:
            raise ValueError(f"expected {x}, got {v} in {' '.join(self.t)}")
        self.i += 1
        return v

    def expr(self):  # additive
        n = self.term()
        while self.peek() in ("+", "-"):
            op = self.eat()
            n = ("add" if op == "+" else "sub", n, self.term())
        return n

    def term(self):
        n = self.unary()
        while self.peek() == "*":
            self.eat()
            n = ("mul", n, self.unary())
        return n

    def unary(self):
        if self.peek() == "-":
            self.eat()
            return ("neg", self.unary())
        return self.atom()

    def atom(self):
        t = self.eat()
        if t == "(":
            n = self.expr()
            self.eat(")")
            return n
        if t in ("FMA", "FMS", "FNMS", "FNMA"):
            self.eat("(")
            a = self.expr(); self.eat(",")
            b = self.expr(); self.eat(",")
            c = self.expr(); self.eat(")")
            ab = ("mul", a, b)
            return {"FMA": ("add", ab, c), "FMS": ("sub", ab, c), "FNMS": ("sub", c, ab),
                    "FNMA": ("neg", ("add", ab, c))}[t]
        if t == "WS":
            self.eat("(")
            s = self.eat(); self.eat(",")
            k = self.eat(); self.eat(")")
            return ("ws", s, k)
        if t.isdigit():
            return ("int", t)
        if self.peek() == "[":
            self.eat("[")
            idx = self.expr()
            self.eat("]")
            return ("load", t, idx)
        return ("var", t)


def idx_rs(n):
    k = n[0]
    if k == "int":
        return n[1]
    if k == "ws":
        return f"{n[1].lower()} * {n[2]}"
    if k == "var":
        return n[1].lower()
    raise ValueError(n)


# --- gimple-like lowering + gcc's FMA formation ---------------------------------------------------------------------
class Lower:
    """Lower statements to three-address code in evaluation order, then form FMAs like gcc's widening_mul pass."""

    def __init__(self):
        self.code = []  # [dst, op, args]   op: load, mul, add, sub, neg, copy, store, const
        self.n = 0

    def tmp(self):
        self.n += 1
        return f"t_{self.n}"

    def emit(self, op, args, dst=None):
        dst = dst or self.tmp()
        self.code.append([dst, op, list(args)])
        return dst

    def val(self, node):
        k = node[0]
        if k == "var":
            return node[1]
        if k == "load":
            return self.emit("load", [node[1], idx_rs(node[2])])
        if k in ("mul", "add", "sub"):
            a = self.val(node[1])
            b = self.val(node[2])
            return self.emit(k, [a, b])
        if k == "neg":
            return self.emit("neg", [self.val(node[1])])
        raise ValueError(node)

    def assign(self, var, node):
        v = self.val(node)
        self.code.append([var, "copy", [v]])

    def store(self, arr, idx, node):
        v = self.val(node)
        self.code.append([None, "store", [arr, idx_rs(idx), v]])


def resolve_copies(code):
    """Variables are assigned once; replace copies so uses point straight at the defining op (as gimple's SSA does)."""
    alias = {}
    out = []
    for dst, op, args in code:
        args = [alias.get(a, a) for a in args] if op != "load" else args
        if op == "store":
            args = [args[0], args[1], alias.get(args[2], args[2])]
        if op == "copy":
            alias[dst] = args[0]
            continue
        out.append([dst, op, args])
    return out, alias


def form_fma(code):
    """gcc's widening_mul pass: each multiply in program order; if every use of its result is an add or a subtract,
    each of those uses becomes a fused multiply-add (a use already turned into an FMA no longer counts)."""
    defs = {c[0]: c for c in code if c[0]}
    uses = {}
    for c in code:
        for a in (c[2] if c[1] not in ("load", "store") else ([c[2][2]] if c[1] == "store" else [])):
            uses.setdefault(a, []).append(c)
    for c in code:
        dst, op, args = c
        if op != "mul":
            continue
        us = uses.get(dst, [])
        if not us:
            continue
        if not all(u[1] in ("add", "sub") for u in us):
            continue
        for u in us:
            a, b = u[2]
            m1, m2 = args
            if u[1] == "add":
                other = b if a == dst else a
                u[1], u[2] = "fma", [m1, m2, other, "+", "+"]
            elif a == dst:  # dst - other
                u[1], u[2] = "fma", [m1, m2, b, "+", "-"]
            else:  # other - dst
                u[1], u[2] = "fma", [m1, m2, a, "-", "+"]
            if a == dst and b == dst:
                raise ValueError("x*x+x*x")
    return code


def translate(kind, name, header, body):
    consts = re.findall(r"DK\((KP\w+), ([+-][0-9.]+)\);", body)
    body = re.sub(r"DK\(.*?\);", "", body)
    # the loop header
    m = re.search(r"for \((.*?)\) \{", body, re.S)
    loop = m.group(1)
    inner = body[m.end():]
    inner = inner[: inner.rindex("}")]  # end of the block holding the for
    inner = inner[: inner.rindex("}")]  # end of the for body
    inner = re.sub(r"\bE [^;]*;", "", inner)
    stmts = [s.strip() for s in re.split(r";", inner.replace("{", ";").replace("}", ";")) if s.strip()]
    L = Lower()
    for s in stmts:
        lhs, rhs = [x.strip() for x in s.split("=", 1)]
        node = P(tokenize(rhs)).expr()
        if "[" in lhs:
            arr, idx = lhs.split("[", 1)
            idxn = P(tokenize(idx.rstrip("]"))).expr()
            L.store(arr.strip(), idxn, node)
        else:
            L.assign(lhs, node)
    code, alias = resolve_copies(L.code)
    code = form_fma(code)
    rs = []
    def v(x):
        return x if x.startswith("KP") else f"v_{x}"
    for dst, op, args in code:
        if op == "load":
            arr, idx = args
            rs.append(f"let {v(dst)} = *{arr.lower()}.offset({idx});")
        elif op == "mul":
            rs.append(f"let {v(dst)} = {v(args[0])} * {v(args[1])};")
        elif op == "add":
            rs.append(f"let {v(dst)} = {v(args[0])} + {v(args[1])};")
        elif op == "sub":
            rs.append(f"let {v(dst)} = {v(args[0])} - {v(args[1])};")
        elif op == "neg":
            rs.append(f"let {v(dst)} = -{v(args[0])};")
        elif op == "fma":
            m1, m2, c, sm, sc = args
            a = f"(-{v(m1)})" if sm == "-" else v(m1)
            cc = f"-{v(c)}" if sc == "-" else v(c)
            rs.append(f"let {v(dst)} = {a}.mul_add({v(m2)}, {cc});")
        elif op == "store":
            arr, idx, val = args
            rs.append(f"*{arr.lower()}.offset({idx}) = {v(val)};")
    kps = "\n".join(f"    const {k}: f32 = {val.lstrip('+')}_f64 as f32;" for k, val in consts)
    stmts_rs = "\n".join("            " + s for s in rs)
    if kind == "r2cf":
        sig = (f"pub unsafe fn {name}(mut r0: *mut f32, mut r1: *mut f32, mut cr: *mut f32, mut ci: *mut f32,\n"
               f"        rs: isize, csr: isize, csi: isize, v: isize, ivs: isize, ovs: isize)")
        lp = ("    let mut i = v;\n    while i > 0 {\n" + stmts_rs.replace("            ", "        ") +
              "\n        i -= 1; r0 = r0.offset(ivs); r1 = r1.offset(ivs); cr = cr.offset(ovs); ci = ci.offset(ovs);\n    }")
    else:
        step = int(re.search(r"W = W \+ (\d+)", loop.split(";")[-1]).group(1))
        sig = (f"pub unsafe fn {name}(mut rp: *mut f32, mut ip: *mut f32, mut rm: *mut f32, mut im: *mut f32, w: *const f32,\n"
               f"        rs: isize, mb: isize, me: isize, ms: isize)")
        lp = (f"    let mut w = w.offset((mb - 1) * {step});\n    let mut m = mb;\n    while m < me {{\n" +
              stmts_rs.replace("            ", "        ") +
              f"\n        m += 1; rp = rp.offset(ms); ip = ip.offset(ms); rm = rm.offset(-ms); im = im.offset(-ms); w = w.offset({step});\n    }}")
    return f"/// FFTW 3.3.10 `{name}` (plain variant), with the FMAs gcc -O3 forms in it.\n{sig} {{\n{kps}\n{lp}\n}}\n"


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    parts = ["// Generated by dsp/tools/gen_fftw_codelets.py from FFTW 3.3.10 (rdft/scalar/r2cf), Copyright (c) 2003, 2007-14",
             "// Matteo Frigo and Massachusetts Institute of Technology, GPL-2.0-or-later. Do not edit by hand.",
             "#![allow(clippy::all, non_snake_case, unused_mut, unused_variables, unsafe_op_in_unsafe_fn)]", ""]
    for kind, name in CODELETS:
        sec = fma_section((src / "rdft/scalar/r2cf" / f"{name}.c").read_text())
        header, body = body_of(sec, name)
        parts.append(translate(kind, name, header, body))
    out.write_text("\n".join(parts))


if __name__ == "__main__":
    main()
