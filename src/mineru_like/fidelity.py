"""Conservative CPU image similarity checks for delivery encodings."""

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class Fidelity:
    accepted: bool
    global_ssim: float
    local_ssim: float
    edge_recall: float
    reason: str


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    # SSIM over all color channels; tile-level scores detect localized damage.
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    ma, mb = a.mean(axis=(0, 1)), b.mean(axis=(0, 1))
    va, vb = ((a - ma) ** 2).mean(axis=(0, 1)), ((b - mb) ** 2).mean(axis=(0, 1))
    cov = ((a - ma) * (b - mb)).mean(axis=(0, 1))
    score = ((2 * ma * mb + 6.5025) * (2 * cov + 58.5225)) / (
        (ma * ma + mb * mb + 6.5025) * (va + vb + 58.5225)
    )
    return float(np.min(score))


def _global_ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Compute whole-image channel moments without full-image float arrays."""
    sums = np.zeros((5, 3), dtype=np.float64)
    height, width = a.shape[:2]
    for y in range(0, height, 256):
        for x in range(0, width, 256):
            left = a[y : y + 256, x : x + 256].astype(np.float64)
            right = b[y : y + 256, x : x + 256].astype(np.float64)
            sums[0] += left.sum(axis=(0, 1))
            sums[1] += right.sum(axis=(0, 1))
            sums[2] += (left * left).sum(axis=(0, 1))
            sums[3] += (right * right).sum(axis=(0, 1))
            sums[4] += (left * right).sum(axis=(0, 1))
    ma, mb, aa, bb, ab = sums / (height * width)
    va, vb = np.maximum(aa - ma * ma, 0), np.maximum(bb - mb * mb, 0)
    covariance = ab - ma * mb
    score = ((2 * ma * mb + 6.5025) * (2 * covariance + 58.5225)) / (
        (ma * ma + mb * mb + 6.5025) * (va + vb + 58.5225)
    )
    return float(np.min(score))


def _edge_counts(a: np.ndarray, b: np.ndarray) -> tuple[int, int]:
    """Count each horizontal and vertical edge once, including tile boundaries."""
    height, width = a.shape[:2]
    total = preserved = 0
    for y in range(0, height, 256):
        for x in range(0, width, 256):
            for axis in (0, 1):
                bottom = min(height, y + 256 + (axis == 0))
                right = min(width, x + 256 + (axis == 1))
                left = a[y:bottom, x:right].astype(np.int16)
                right_image = b[y:bottom, x:right].astype(np.int16)
                da, db = np.diff(left, axis=axis), np.diff(right_image, axis=axis)
                mask = np.max(np.abs(da), axis=2) >= 24
                total += int(mask.sum())
                preserved += int((mask & (np.max(np.abs(da - db), axis=2) <= 18)).sum())
    return total, preserved


def compare(original: Image.Image, candidate: Image.Image, settings) -> Fidelity:
    """Check decoded RGBA, structural similarity and preservation of sharp edges.

    These metrics reduce risk of damage but cannot prove semantic equivalence.
    """
    if original.size != candidate.size:
        return Fidelity(False, 0, 0, 0, "dimensions changed")
    a = np.asarray(original.convert("RGBA"))
    b = np.asarray(candidate.convert("RGBA"))
    if not np.array_equal(a[:, :, 3], b[:, :, 3]):
        return Fidelity(False, 0, 0, 0, "alpha changed")
    # Hidden RGB is significant for exact transparency and subsequent compositing.
    for y in range(0, a.shape[0], 256):
        for x in range(0, a.shape[1], 256):
            left, right = a[y : y + 256, x : x + 256], b[y : y + 256, x : x + 256]
            if np.any((left[:, :, 3] == 0) & np.any(left[:, :, :3] != right[:, :, :3], axis=2)):
                return Fidelity(False, 0, 0, 0, "transparent RGB changed")
    if np.array_equal(a, b):
        return Fidelity(True, 1, 1, 1, "decoded pixels identical")
    rgb_a, rgb_b = a[:, :, :3], b[:, :, :3]
    global_score = _global_ssim(rgb_a, rgb_b)
    h, w = a.shape[:2]
    local = min(
        _ssim(rgb_a[y : y + 32, x : x + 32], rgb_b[y : y + 32, x : x + 32])
        for y in range(0, h, 32)
        for x in range(0, w, 32)
    )
    # Compare signed RGB gradients, so thin colored strokes and punctuation count.
    total, preserved = _edge_counts(rgb_a, rgb_b)
    edge = preserved / total if total else 1.0
    ok = (
        global_score >= settings.global_ssim
        and local >= settings.local_ssim
        and edge >= settings.edge_recall
    )
    failures = []
    if global_score < settings.global_ssim:
        failures.append("global SSIM")
    if local < settings.local_ssim:
        failures.append("local SSIM")
    if edge < settings.edge_recall:
        failures.append("edge recall")
    return Fidelity(ok, global_score, local, edge, "accepted" if ok else ", ".join(failures))
