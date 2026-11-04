import pytest
from pydantic import ValidationError

from mineru_like.config import RuntimeSettings, load_settings


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
