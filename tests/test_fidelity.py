from types import SimpleNamespace

from PIL import Image, ImageDraw

from mineru_like.fidelity import compare

SETTINGS = SimpleNamespace(global_ssim=0.995, local_ssim=0.98, edge_recall=0.995)


def test_identical_image_passes() -> None:
    image = Image.new("RGBA", (64, 64), "white")
    assert compare(image, image.copy(), SETTINGS).accepted


def test_small_colored_stroke_and_punctuation_loss_rejected() -> None:
    image = Image.new("RGBA", (256, 256), "white")
    draw = ImageDraw.Draw(image)
    draw.line((80, 80, 86, 80), fill=(255, 0, 0), width=1)
    draw.point((100, 100), fill="black")
    candidate = image.copy()
    ImageDraw.Draw(candidate).rectangle((79, 79, 101, 101), fill="white")
    result = compare(image, candidate, SETTINGS)
    assert not result.accepted
    assert result.edge_recall < SETTINGS.edge_recall


def test_alpha_and_hidden_rgb_changes_rejected() -> None:
    image = Image.new("RGBA", (4, 4), (100, 20, 50, 0))
    candidate = image.copy()
    candidate.putpixel((0, 0), (0, 0, 0, 0))
    assert not compare(image, candidate, SETTINGS).accepted
    candidate.putpixel((0, 0), (100, 20, 50, 1))
    assert not compare(image, candidate, SETTINGS).accepted


def test_decimal_minus_and_thin_colored_line_individually_rejected() -> None:
    marks = [
        lambda d: d.point((32, 32), fill="black"),
        lambda d: d.line((30, 32, 34, 32), fill="black"),
        lambda d: d.line((30, 32, 34, 32), fill=(0, 160, 255)),
    ]
    for draw_mark in marks:
        original = Image.new("RGB", (128, 128), "white")
        draw_mark(ImageDraw.Draw(original))
        assert not compare(original, Image.new("RGB", original.size, "white"), SETTINGS).accepted


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
