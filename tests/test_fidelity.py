from types import SimpleNamespace
from typing import cast

import pytest
from PIL import Image, ImageDraw

from mineru_like.config import CompressionSettings
from mineru_like.fidelity import compare

# The comparator only reads these three settings; keep this fixture intentionally partial.
SETTINGS = cast(
    CompressionSettings, SimpleNamespace(global_ssim=0.995, local_ssim=0.98, edge_recall=0.995)
)


def test_identical_image_passes() -> None:
    image = Image.new("RGBA", (64, 64), "white")
    assert compare(image, image.copy(), SETTINGS).accepted


def test_alpha_and_hidden_rgb_changes_rejected() -> None:
    image = Image.new("RGBA", (4, 4), (100, 20, 50, 0))
    candidate = image.copy()
    candidate.putpixel((0, 0), (0, 0, 0, 0))
    assert not compare(image, candidate, SETTINGS).accepted
    candidate.putpixel((0, 0), (100, 20, 50, 1))
    assert not compare(image, candidate, SETTINGS).accepted


@pytest.mark.parametrize("mark", ["decimal", "minus", "colored-line", "stroke-and-punctuation"])
def test_small_mark_loss_is_rejected(mark: str) -> None:
    combined = mark == "stroke-and-punctuation"
    original = Image.new(
        "RGBA" if combined else "RGB", (256, 256) if combined else (128, 128), "white"
    )
    draw = ImageDraw.Draw(original)
    if mark == "decimal":
        draw.point((32, 32), fill="black")
    elif mark == "minus":
        draw.line((30, 32, 34, 32), fill="black")
    elif mark == "colored-line":
        draw.line((30, 32, 34, 32), fill=(0, 160, 255))
    else:
        draw.line((80, 80, 86, 80), fill=(255, 0, 0), width=1)
        draw.point((100, 100), fill="black")
    result = compare(original, Image.new(original.mode, original.size, "white"), SETTINGS)
    assert not result.accepted
    assert result.edge_recall < SETTINGS.edge_recall


def test_tiled_global_ssim_matches_direct_moments() -> None:
    import numpy as np

    from mineru_like.fidelity import _global_ssim, _ssim

    rng = np.random.default_rng(304)
    a = rng.integers(0, 256, (291, 531, 3), dtype=np.uint8)
    b = rng.integers(0, 256, (291, 531, 3), dtype=np.uint8)
    assert abs(_global_ssim(a, b) - _ssim(a, b)) < 1e-8
    # Small variance exposes cancellation in the whole-image moment formula.
    a.fill(253)
    b.fill(253)
    a[256, 256, 0] = 252
    b[256, 256, 1] = 252
    assert abs(_global_ssim(a, b) - _ssim(a, b)) < 1e-8


def test_tiled_edges_preserve_marks_across_both_tile_boundaries() -> None:
    import numpy as np

    from mineru_like.fidelity import _edge_counts

    a = np.full((260, 260, 3), 255, dtype=np.uint8)
    b = a.copy()
    a[255:257, 255:257] = 0
    b[255:257, 255:257] = 0
    b[256, 256] = 255
    expected_total = expected_preserved = 0
    for axis in (0, 1):
        da = np.diff(a.astype(np.int16), axis=axis)
        db = np.diff(b.astype(np.int16), axis=axis)
        mask = np.max(np.abs(da), axis=2) >= 24
        expected_total += int(mask.sum())
        expected_preserved += int((mask & (np.max(np.abs(da - db), axis=2) <= 18)).sum())
    assert expected_total > expected_preserved
    assert _edge_counts(a, b) == (expected_total, expected_preserved)
