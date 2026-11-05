"""Image dimensions and coordinate transforms for OvisOCR3 inference."""

from dataclasses import dataclass
from math import ceil, floor, sqrt
from struct import pack, unpack

Size = tuple[int, int]
Box = tuple[float, float, float, float]


def _float32(value: float) -> float:
    return unpack("f", pack("f", value))[0]


def ovis_size(size: Size, min_tokens: int, max_tokens: int) -> Size:
    """Predict Qwen3VL smart_resize with explicit 32-pixel-grid limits."""
    width, height = size
    if min(width, height, min_tokens) <= 0 or max_tokens < min_tokens:
        raise ValueError("Invalid image size or token bounds")
    factor = 32
    w = max(factor, round(_float32(width / factor)) * factor)
    h = max(factor, round(_float32(height / factor)) * factor)
    if w * h > max_tokens * factor**2:
        beta = _float32(sqrt(_float32(_float32(float(height) * width) / (max_tokens * factor**2))))
        w = max(factor, floor(_float32(_float32(width / beta) / factor)) * factor)
        h = max(factor, floor(_float32(_float32(height / beta) / factor)) * factor)
    elif w * h < min_tokens * factor**2:
        beta = _float32(sqrt(_float32((min_tokens * factor**2) / _float32(float(height) * width))))
        w = ceil(_float32(_float32(width * beta) / factor)) * factor
        h = ceil(_float32(_float32(height * beta) / factor)) * factor
    return max(factor, w), max(factor, h)


@dataclass(frozen=True)
class ImageGeometry:
    source_size: Size
    rendered_size: Size
    submitted_size: Size
    effective_size: Size | None = None
    page_roi: Box | None = None

    @property
    def image_tokens(self) -> int | None:
        if self.effective_size is None:
            return None
        return self.effective_size[0] * self.effective_size[1] // 1024

    def normalized_to_source(self, box: Box) -> Box:
        """Map 0..1000 coordinates through the submitted image to the source ROI."""
        left, top, right, bottom = self.page_roi or (0, 0, *self.source_size)
        return (
            left + box[0] * (right - left) / 1000,
            top + box[1] * (bottom - top) / 1000,
            left + box[2] * (right - left) / 1000,
            top + box[3] * (bottom - top) / 1000,
        )

    def normalized_to_effective(self, box: Box) -> Box | None:
        if self.effective_size is None:
            return None
        width, height = self.effective_size
        return (
            box[0] * width / 1000,
            box[1] * height / 1000,
            box[2] * width / 1000,
            box[3] * height / 1000,
        )
