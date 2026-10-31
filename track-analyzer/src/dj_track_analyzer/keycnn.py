"""
Key from MusicalKeyCNN (github.com/a1ex90/MusicalKeyCNN, via the fork github.com/puppi3-music/MusicalKeyCNN-CKA):
KeyNet of Korzeniowski & Widmer (2018) on a log-magnitude CQT of the whole track. The forward pass is numpy, with
weights converted from the PyTorch checkpoint by scripts/convert_keynet.py (starter/keynet.npz), so no torch is needed.

Model, weights and the input pipeline reimplemented here: Copyright (c) 2025 Alexander Sommer, MIT License (full text
in starter/keynet.LICENSE).

`cnn_camelot`, `cnn_key`, `cnn_confidence`: our key (collection.OUR_CAMELOT_SQL); Essentia's stays stored next to it.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

WEIGHTS = Path(__file__).resolve().parent / "starter" / "keynet.npz"
# The repo's preprocess_audio(): what the checkpoint was trained on.
SR, HOP, N_BINS, BINS_PER_OCTAVE, FMIN = 44100, 8820, 105, 24, 65
# Blocks: conv layers, then 2x2 max pooling after conv2, conv4 and conv6.
POOL_AFTER = {"conv2", "conv4", "conv6"}
LAYERS = [f"conv{i}" for i in range(1, 10)]
# Pitch class names as Essentia's KeyExtractor writes them, so cnn_key reads like `key`.
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

_weights: dict | None = None
_lock = threading.Lock()


def weights() -> dict:
    global _weights
    with _lock:
        if _weights is None:
            with np.load(WEIGHTS) as z:
                _weights = {k: z[k] for k in z.files if k != "source"}
    return _weights


def spectrogram(mono44: np.ndarray) -> np.ndarray:
    """(105, frames) log1p |CQT|, the repo's input; its last two frames are dropped as in training."""
    import librosa

    cqt = librosa.cqt(np.asarray(mono44, dtype=np.float32), sr=SR, hop_length=HOP, n_bins=N_BINS,
                      bins_per_octave=BINS_PER_OCTAVE, fmin=FMIN)
    return np.log1p(np.abs(cqt))[:, :-2].astype(np.float32)


def _conv(x: np.ndarray, w: np.ndarray) -> np.ndarray:
    """'same' 2-D convolution (cross-correlation, as torch), x (C, H, W), w (O, C, k, k), no bias. One matrix
    product over every k x k patch (im2col): 2.2x faster than a product per kernel position, same key and
    3-decimal confidence on 443/443 tracks (probabilities within 2.3e-7)."""
    o, c, k, _ = w.shape
    p = k // 2
    _, h, wd = x.shape
    xp = np.pad(x, ((0, 0), (p, p), (p, p))) if p else x
    cols = np.lib.stride_tricks.sliding_window_view(xp, (k, k), axis=(1, 2))  # (C, H, W, k, k)
    cols = cols.transpose(0, 3, 4, 1, 2).reshape(c * k * k, h * wd)
    return (w.reshape(o, c * k * k) @ cols).reshape(o, h, wd)


def _elu(x: np.ndarray) -> np.ndarray:
    return np.where(x > 0, x, np.expm1(np.minimum(x, 0))).astype(np.float32)


def _pool(x: np.ndarray) -> np.ndarray:
    c, h, w = x.shape
    h2, w2 = h // 2, w // 2  # nn.MaxPool2d(2) drops an odd last row/column
    return x[:, :h2 * 2, :w2 * 2].reshape(c, h2, 2, w2, 2).max(axis=(2, 4))


def forward(spec: np.ndarray) -> np.ndarray:
    """Logits for the 24 keys (index 0-11 = 1A-12A, 12-23 = 1B-12B) from a (105, frames) spectrogram."""
    wt = weights()
    x = spec[None].astype(np.float32)
    for name in LAYERS:
        x = _conv(x, wt[f"{name}.w"])
        x = _elu(x * wt[f"{name}.scale"][:, None, None] + wt[f"{name}.shift"][:, None, None])
        if name in POOL_AFTER:
            x = _pool(x)
    return x.mean(axis=(1, 2))


def camelot_of(index: int) -> str:
    return f"{index % 12 + 1}{'A' if index < 12 else 'B'}"


def key_name(index: int) -> str:
    from .analysis import CAMELOT_MAJOR, CAMELOT_MINOR

    table = CAMELOT_MINOR if index < 12 else CAMELOT_MAJOR
    pc = next(p for p, n in table.items() if n == index % 12 + 1)
    return f"{NAMES[pc]} {'minor' if index < 12 else 'major'}"


def key_cnn(mono44: np.ndarray) -> dict:
    """{"index", "camelot", "key", "confidence" (softmax of the winner), "probs"} for mono 44.1 kHz audio."""
    logits = forward(spectrogram(mono44)).astype(np.float64)
    probs = np.exp(logits - logits.max())
    probs /= probs.sum()
    i = int(np.argmax(logits))
    return {"index": i, "camelot": camelot_of(i), "key": key_name(i), "confidence": float(probs[i]),
            "probs": probs}
