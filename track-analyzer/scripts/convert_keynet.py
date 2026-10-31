"""Turn MusicalKeyCNN's PyTorch checkpoint into the numpy weights `keycnn.py` runs, so the analyzer needs no torch.

Each conv layer keeps its weights as they are; its BatchNorm (eval mode) becomes a per-channel scale and shift:
scale = gamma / sqrt(running_var + eps), shift = beta - running_mean * scale. Needs torch, once:

    pip install torch numpy
    python scripts/convert_keynet.py <MusicalKeyCNN-CKA checkout> [commit]

Writes src/dj_track_analyzer/starter/keynet.npz.
"""

import sys
from pathlib import Path

import numpy as np
import torch

LAYERS = [f"conv{i}" for i in range(1, 10)]
OUT = Path(__file__).resolve().parent.parent / "src" / "dj_track_analyzer" / "starter" / "keynet.npz"


def main() -> None:
    repo = Path(sys.argv[1])
    commit = sys.argv[2] if len(sys.argv) > 2 else ""
    ck = torch.load(repo / "checkpoints" / "keynet.pt", map_location="cpu", weights_only=False)
    state = ck.state_dict() if hasattr(ck, "state_dict") else ck.get("model_state_dict", ck.get("state_dict", ck))
    out = {}
    for name in LAYERS:
        w = state[f"{name}.conv.weight"].numpy().astype(np.float32)
        bn = f"{name}.bn"
        eps = 1e-5  # nn.BatchNorm2d's default; the model doesn't change it
        scale = state[f"{bn}.weight"].numpy() / np.sqrt(state[f"{bn}.running_var"].numpy() + eps)
        shift = state[f"{bn}.bias"].numpy() - state[f"{bn}.running_mean"].numpy() * scale
        out[f"{name}.w"] = w
        out[f"{name}.scale"] = scale.astype(np.float32)
        out[f"{name}.shift"] = shift.astype(np.float32)
    left = sorted(k for k in state if not any(k.startswith(n + ".") for n in LAYERS))
    if any(not k.endswith("num_batches_tracked") for k in left):
        raise SystemExit(f"checkpoint has weights the numpy model doesn't use: {left}")
    out["source"] = np.array(f"github.com/puppi3-music/MusicalKeyCNN-CKA {commit} checkpoints/keynet.pt "
                             "(MIT; model from github.com/a1ex90/MusicalKeyCNN, MIT)")
    np.savez_compressed(OUT, **out)
    print(f"{OUT}  ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
