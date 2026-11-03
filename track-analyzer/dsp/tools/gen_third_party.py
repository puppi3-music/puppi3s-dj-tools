"""Write licenses/THIRD-PARTY-RUST.md: the licence of every Rust crate in Cargo.lock, which ships inside the wheel.

Run from dsp/ after changing dependencies: `python3 tools/gen_third_party.py`. Needs cargo (reads the crates'
sources from cargo's registry). For crates offered under "MIT OR Apache-2.0" we use them under MIT.
"""
import glob
import json
import os
import subprocess

meta = json.loads(subprocess.check_output(["cargo", "metadata", "--format-version", "1", "--locked"]))
out = [
    "# Rust crates in track-analyzer-dsp",
    "",
    "The compiled module includes code from these crates (some are only used while building it). Their licences",
    "follow. Crates offered under a choice of MIT or Apache-2.0 are used here under MIT.",
    "",
    "| Crate | Version | Licence |",
    "|---|---|---|",
]
texts = []
for p in sorted(meta["packages"], key=lambda p: p["name"]):
    if p["source"] is None:  # our own crate
        continue
    out.append(f"| [{p['name']}](https://crates.io/crates/{p['name']}) | {p['version']} | {p['license']} |")
    d = os.path.dirname(p["manifest_path"])
    files = [f for f in ("LICENSE-MIT", "LICENSE.txt", "LICENSE", "LICENSE-UNICODE") if os.path.exists(os.path.join(d, f))]
    if not files:
        files = [os.path.basename(f) for f in glob.glob(os.path.join(d, "LICENSE*"))]
    if not files:
        raise SystemExit(f"no licence file in {p['name']} {p['version']}")
    for f in files:
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            texts.append(f"## {p['name']} {p['version']}: {f}\n\n```text\n{fh.read().strip()}\n```\n")
out += ["", *texts]
with open("licenses/THIRD-PARTY-RUST.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out))
print(len(texts), "licence texts written")
