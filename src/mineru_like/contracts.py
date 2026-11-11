"""Small structural contracts for inference, jobs and serialized metadata."""

from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Protocol, TypeAlias

# Model records and diagnostics preserve upstream extension fields and raw output.
Payload: TypeAlias = dict[str, Any]
Diagnostic: TypeAlias = Payload
# Queries select different subsets of the upload/job schema; values cross SQLite's
# dynamic boundary here rather than pretending every projection is a complete job.
SQLiteRecord: TypeAlias = dict[str, Any]


class ModelOutputError(ValueError):
    """A completed inference response failed content validation."""


class Inference(Protocol):
    async def infer(
        self,
        images: list[Path],
        prompt: str,
        *,
        max_tokens: int | None = None,
        response_schema: Payload | None = None,
    ) -> str: ...


class Engine(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...


class ResultWriter(Protocol):
    def write_zip(self, path: Path) -> None: ...


class Parser(Protocol):
    def __call__(
        self, *, input_path: Path, filename: str, ocr_mode: str, tier: str, workdir: Path
    ) -> Awaitable[ResultWriter]: ...
