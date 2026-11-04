"""Validated server and inference configuration."""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    max_image_side: int = Field(2880, ge=512)
    preview_side: int = Field(1280, ge=256)
    captioning: bool = True
    cross_page_tables: bool = True
    max_pages: int = Field(1000, ge=1)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)


def load_settings(path: Path | None = None) -> Settings:
    if path is None:
        return Settings()
    with path.open("rb") as stream:
        return Settings.model_validate(tomllib.load(stream))
