from mineru_like.geometry import ImageGeometry, ovis_size


def test_grid_bounds_and_orientation() -> None:
    assert ovis_size((80, 48), 1, 8192) == (64, 64)
    assert ovis_size((200, 400), 196, 8192) == (320, 640)
    assert ovis_size((400, 200), 196, 8192) == (640, 320)
    assert ovis_size((4000, 4000), 196, 8100) == (2880, 2880)


def test_roi_and_effective_coordinates() -> None:
    geometry = ImageGeometry((1200, 800), (600, 400), (200, 100), (640, 320), (100, 200, 500, 400))
    assert geometry.normalized_to_source((0, 0, 1000, 1000)) == (100, 200, 500, 400)
    assert geometry.normalized_to_effective((250, 500, 750, 1000)) == (160, 160, 480, 320)
    assert geometry.image_tokens == 200
    assert ImageGeometry((10, 20), (10, 20), (10, 20)).normalized_to_effective((0, 0, 1, 1)) is None
