import pytest
from pydantic import ValidationError

from mineru_like.config import RuntimeSettings, Settings, load_settings


def test_no_lower_precision_kv():
    with pytest.raises(ValidationError):
        RuntimeSettings(kv_cache="q4_0")


def test_toml_and_unknown_option(tmp_path):
    config = tmp_path / "settings.toml"
    config.write_text('[runtime]\nmode="external"\nbase_url="http://127.0.0.1:9000"\n')
    assert load_settings(config).runtime.mode == "external"
    config.write_text("imaginary_option=true")
    with pytest.raises(ValidationError):
        load_settings(config)


def test_default_data_layout_and_preserved_token_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert Settings().data_dir.name == "data"
    assert settings.data_dir == tmp_path / "data"
    assert settings.runtime.binary == tmp_path / "data/b12110/llama-server"
    assert settings.runtime.model == tmp_path / "data/models/OvisOCR3.Q8_0.gguf"
    assert settings.runtime.mmproj == tmp_path / "data/models/OvisOCR3.mmproj-bf16.gguf"
    assert settings.runtime.kv_cache == "bf16"
    assert settings.runtime.context_size == 32768
    assert settings.runtime.max_tokens == 16384
    assert settings.runtime.image_min_tokens == 1024
    assert settings.runtime.parallel_slots == 1


def test_paths_follow_config_location_and_custom_data_directory(tmp_path, monkeypatch):
    folder = tmp_path / "config"
    folder.mkdir()
    path = folder / "local.toml"
    path.write_text('data_dir="assets"\n[compression]\ncwebp_binary="tools/cwebp"\n')
    monkeypatch.chdir(tmp_path)
    settings = load_settings(path)
    assert settings.data_dir == folder / "assets"
    assert settings.runtime.model == folder / "assets/models/OvisOCR3.Q8_0.gguf"
    assert settings.compression.cwebp_binary == folder / "tools/cwebp"


@pytest.mark.parametrize("slots", [0, -1])
def test_invalid_parallel_slots(slots):
    with pytest.raises(ValidationError):
        RuntimeSettings(parallel_slots=slots)
