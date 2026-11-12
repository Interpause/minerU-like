"""Validated server and inference configuration."""

import tomllib
from pathlib import Path
from typing import Literal, overload

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
    parallel_slots: int = Field(4, ge=1)
    image_min_tokens: int = Field(196, ge=1)
    image_max_tokens: int | None = Field(None, ge=-1)
    batch_size: int = Field(1024, ge=1)
    ubatch_size: int = Field(1024, ge=1)
    startup_timeout: float = Field(300, gt=0)
    request_timeout: float | None = Field(None, gt=0)
    kv_cache: Literal["bf16", "f16", "q8_0"] | None = "bf16"
    kv_cache_k: Literal["bf16", "f16", "q8_0"] | None = None
    kv_cache_v: Literal["bf16", "f16", "q8_0"] | None = None

    @field_validator("image_max_tokens")
    @classmethod
    def valid_image_max_tokens(cls, value: int | None) -> int | None:
        if value == 0:
            raise ValueError("image_max_tokens must be positive, -1, or omitted")
        return value


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
    def valid_qualities(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if not values or len(set(values)) != len(values) or any(not 1 <= q <= 100 for q in values):
            raise ValueError("qualities must be distinct integers between 1 and 100")
        return values

    @field_validator("preset_overrides")
    @classmethod
    def valid_presets(cls, values: dict[str, str]) -> dict[str, str]:
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
    data_dir: Path = Path("data")
    api_key: str | None = None
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    max_upload_bytes: int = Field(512 * 1024 * 1024, ge=1)
    max_storage_bytes: int = Field(8 * 1024 * 1024 * 1024, ge=1)
    queue_size: int = Field(8, ge=1, deprecated=True)
    retention_hours: float = Field(24, gt=0)
    upload_retention_hours: float = Field(1, gt=0)
    render_dpi: int = Field(300, ge=72, le=600)
    advanced_render_dpi: int = Field(600, ge=72, le=600)
    advanced_max_image_side: int = Field(8192, ge=512)
    max_image_side: int = Field(4096, ge=512)
    preview_side: int = Field(1280, ge=256)
    captioning: bool = True
    cross_page_tables: bool = True
    max_pages: int = Field(1000, ge=1)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    libreoffice: LibreOfficeSettings = Field(default_factory=LibreOfficeSettings)
    compression: CompressionSettings = Field(default_factory=CompressionSettings)
    svg: SVGSettings = Field(default_factory=SVGSettings)


def load_settings(path: Path | None = None) -> Settings:
    base = Path.cwd() if path is None else path.resolve().parent
    if path is None:
        settings = Settings()
    else:
        with path.open("rb") as stream:
            settings = Settings.model_validate(tomllib.load(stream))

    @overload
    def resolve(value: Path) -> Path: ...

    @overload
    def resolve(value: None) -> None: ...

    def resolve(value: Path | None) -> Path | None:
        return (base / value).resolve() if value is not None else None

    settings.data_dir = resolve(settings.data_dir)
    defaults = {
        "model": settings.data_dir / "models" / "OvisOCR3.Q8_0.gguf",
        "mmproj": settings.data_dir / "models" / "OvisOCR3.mmproj-bf16.gguf",
    }
    if settings.runtime.binary is not None:
        settings.runtime.binary = resolve(settings.runtime.binary)
    elif settings.runtime.mode == "managed":
        candidates = sorted(
            {
                p
                for pattern in ("b*/llama-server", "llama-b*/llama-server")
                for p in settings.data_dir.glob(pattern)
                if p.is_file()
            }
        )
        if len(candidates) > 1:
            raise ValueError("Multiple llama-server binaries found; set runtime.binary explicitly")
        settings.runtime.binary = candidates[0] if candidates else None
    for field, default in defaults.items():
        value = getattr(settings.runtime, field)
        if value is not None:
            setattr(settings.runtime, field, resolve(value))
        elif settings.runtime.mode == "managed":
            setattr(settings.runtime, field, default)
    settings.compression.cwebp_binary = resolve(settings.compression.cwebp_binary)
    settings.libreoffice.binary = resolve(settings.libreoffice.binary)
    if "/" in settings.libreoffice.flatpak_binary:
        settings.libreoffice.flatpak_binary = str(
            resolve(Path(settings.libreoffice.flatpak_binary))
        )
    return settings
