"""Validated server and inference configuration."""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["managed", "external"] = "managed"
    binary: Path | None = None
    model: Path | None = None
    mmproj: Path | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_alias: str | None = None
    device: Literal["auto", "cuda", "cpu"] = "auto"
    context_size: int = Field(32768, ge=4096)
    max_tokens: int = Field(16384, ge=256)
    image_min_tokens: int = Field(1024, ge=1)
    image_max_tokens: int = Field(4096, ge=1)
    batch_size: int = Field(512, ge=1)
    ubatch_size: int = Field(128, ge=1)
    startup_timeout: float = Field(300, gt=0)
    request_timeout: float = Field(600, gt=0)
    kv_cache: Literal["f16", "q8_0"] = "f16"


class LibreOfficeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["off", "auto", "system", "flatpak"] = "auto"
    binary: Path | None = None
    flatpak_binary: str = "flatpak"
    flatpak_app_id: str = "org.libreoffice.LibreOffice"
    timeout: float = Field(120, gt=0)
    max_output_bytes: int = Field(512 * 1024 * 1024, ge=1)


class CompressionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    cwebp_binary: Path | None = None
    min_savings: float = Field(0.10, ge=0, lt=1)
    qualities: tuple[int, ...] = (95, 90, 85, 80)
    method: int = Field(6, ge=0, le=6)
    preset_overrides: dict[str, str] = Field(default_factory=dict)
    global_ssim: float = Field(0.995, ge=0, le=1)
    local_ssim: float = Field(0.98, ge=0, le=1)
    edge_recall: float = Field(0.995, ge=0, le=1)

    @field_validator("qualities")
    @classmethod
    def valid_qualities(cls, values):
        if not values or len(set(values)) != len(values) or any(not 1 <= q <= 100 for q in values):
            raise ValueError("qualities must be distinct integers between 1 and 100")
        return values

    @field_validator("preset_overrides")
    @classmethod
    def valid_presets(cls, values):
        kinds = {"photo", "drawing", "text", "icon", "mixed", "unknown"}
        presets = {"default", "photo", "picture", "drawing", "icon", "text"}
        if not set(values).issubset(kinds) or not set(values.values()).issubset(presets):
            raise ValueError("preset overrides must map known asset types to WebP presets")
        return values


class SVGSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    comparison_dpi: int = Field(144, ge=72, le=300)
    max_pixels: int = Field(16_000_000, ge=1)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_dir: Path = Path(".local/mineru-like")
    api_key: str | None = None
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    max_upload_bytes: int = Field(512 * 1024 * 1024, ge=1)
    max_storage_bytes: int = Field(8 * 1024 * 1024 * 1024, ge=1)
    queue_size: int = Field(8, ge=1)
    retention_hours: float = Field(24, gt=0)
    upload_retention_hours: float = Field(1, gt=0)
    render_dpi: int = Field(200, ge=72, le=600)
    advanced_render_dpi: int = Field(300, ge=72, le=600)
    advanced_max_image_side: int = Field(4096, ge=512)
    max_image_side: int = Field(2880, ge=512)
    preview_side: int = Field(1280, ge=256)
    captioning: bool = True
    cross_page_tables: bool = True
    max_pages: int = Field(1000, ge=1)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    libreoffice: LibreOfficeSettings = Field(default_factory=LibreOfficeSettings)
    compression: CompressionSettings = Field(default_factory=CompressionSettings)
    svg: SVGSettings = Field(default_factory=SVGSettings)


def load_settings(path: Path | None = None) -> Settings:
    if path is None:
        return Settings()
    with path.open("rb") as stream:
        return Settings.model_validate(tomllib.load(stream))
