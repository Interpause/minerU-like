import json

import pytest

from mineru_like.assets import register_assets, verify_assets
from mineru_like.config import RuntimeSettings, Settings


def test_setup_checksums_and_changes(tmp_path):
    paths = {}
    for name in ("binary", "model", "mmproj"):
        path = tmp_path / name
        path.write_bytes(b"GGUFexample" if name != "binary" else b"ELFexample")
        paths[name] = path
    settings = Settings(data_dir=tmp_path / "data", runtime=RuntimeSettings(**paths))
    manifest = register_assets(settings)
    assert set(json.loads(manifest.read_text())) == set(paths)
    verify_assets(settings)
    paths["model"].write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum changed"):
        verify_assets(settings)


def test_setup_rejects_lfs_pointer(tmp_path):
    path = tmp_path / "pointer"
    path.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
    settings = Settings(
        data_dir=tmp_path, runtime=RuntimeSettings(binary=path, model=path, mmproj=path)
    )
    with pytest.raises(ValueError, match="LFS pointer"):
        register_assets(settings)
