"""Bounded server diagnostics with task-local document identity."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from . import __version__
from .config import Settings
from .contracts import Payload

job_context: ContextVar[Payload | None] = ContextVar("job_context", default=None)
log = logging.getLogger(__name__)


class ServerFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = tuple(value for value in secrets if value)

    def format(self, record: logging.LogRecord) -> str:
        item: Payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "version": __version__,
            **(job_context.get() or {}),
            **getattr(record, "details", {}),
            "message": record.getMessage(),
        }
        if record.exc_info:
            item["traceback"] = self.formatException(record.exc_info)

        def redact(value: Any) -> Any:
            if isinstance(value, str):
                for secret in self.secrets:
                    value = value.replace(secret, "[redacted]")
                return value
            if isinstance(value, dict):
                return {key: redact(val) for key, val in value.items()}
            if isinstance(value, (tuple, list)):
                return [redact(val) for val in value]
            return value

        return json.dumps(redact(item), ensure_ascii=False, default=str)


def open_server_logs(settings: Settings) -> tuple[RotatingFileHandler, ...]:
    directory = settings.data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handlers = []
    logger = logging.getLogger("mineru_like")
    try:
        for filename, level in (("server.jsonl", logging.INFO), ("issues.jsonl", logging.WARNING)):
            handler = RotatingFileHandler(
                directory / filename, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            handler.setFormatter(
                ServerFormatter((settings.api_key or "", settings.runtime.api_key or ""))
            )
            handler.setLevel(level)
            handlers.append(handler)
            logger.addHandler(handler)
    except Exception:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()
        raise
    return tuple(handlers)


def runtime_profile(settings: Settings) -> Payload:
    runtime = settings.runtime
    return {
        "runtime_mode": runtime.mode,
        "device_requested": runtime.device,
        "model": runtime.model.name if runtime.model else None,
        "mmproj": runtime.mmproj.name if runtime.mmproj else None,
        "context_size": runtime.context_size,
        "parallel_slots": runtime.parallel_slots,
        "max_tokens": runtime.max_tokens,
        "image_min_tokens": runtime.image_min_tokens,
        "image_max_tokens": runtime.image_max_tokens,
        "kv_cache": runtime.kv_cache,
        "kv_cache_k": runtime.kv_cache_k,
        "kv_cache_v": runtime.kv_cache_v,
        "batch_size": runtime.batch_size,
        "ubatch_size": runtime.ubatch_size,
        "max_image_side": settings.max_image_side,
        "advanced_max_image_side": settings.advanced_max_image_side,
        "render_dpi": settings.render_dpi,
        "advanced_render_dpi": settings.advanced_render_dpi,
    }


def record_diagnostic(item: Payload) -> None:
    keys = {
        "stage",
        "page_idx",
        "region",
        "status",
        "outcome",
        "reason",
        "error",
        "warning",
        "finish_reason",
        "usage",
        "duration_seconds",
        "call_number",
        "attempt",
        "submitted_size",
        "effective_size",
        "minimum_symbol_pixels",
        "expected_gain",
        "code",
        "message",
        "model_calls",
        "regions",
        "coverage_basis",
        "ink_pixels",
        "uncovered_ink_pixels",
        "uncovered_fraction",
        "candidate_count",
        "matched_candidates",
        "best_iou",
    }
    details = {key: value for key, value in item.items() if key in keys}
    if "message" in details:
        details["diagnostic_message"] = details.pop("message")
    if "page_idx" in details:
        details["page"] = details["page_idx"] + 1
    issue = (
        item.get("status") in {"unresolved", "incomplete", "disputed"}
        or item.get("outcome") == "uncertain"
        or bool(item.get("error") or item.get("warning") or item.get("code"))
        or item.get("finish_reason") == "length"
    )
    log.log(
        logging.WARNING if issue else logging.INFO, "parse_diagnostic", extra={"details": details}
    )
