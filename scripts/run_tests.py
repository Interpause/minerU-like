#!/usr/bin/env python3
"""Run the test suite with explicit opt-in integration prerequisites."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from pytest import CollectReport, TestReport

ROOT = Path(__file__).resolve().parents[1]


def executable(value: str | None) -> str | None:
    if not value:
        return None
    found = shutil.which(value)
    return str(Path(found).resolve()) if found else None


def configure(args: argparse.Namespace) -> dict[str, str]:
    env = {}
    if args.all or args.plugin:
        plugin = Path(args.plugin_root).expanduser().resolve() if args.plugin_root else None
        if not plugin or not (plugin / "src/utils/mineruLocalClient.ts").is_file():
            raise ValueError("--plugin-root must point to an llm-for-zotero checkout")
        node = executable("node")
        if not node:
            raise ValueError("Node.js 26 or later is required on PATH")
        try:
            version = subprocess.run(
                [node, "--version"], capture_output=True, text=True, check=True
            )
            major = int(version.stdout.strip().lstrip("v").split(".")[0])
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            raise ValueError("Could not determine Node.js version") from exc
        if major < 26:
            raise ValueError("Node.js 26 or later is required on PATH")
        modules = Path(args.node_modules).expanduser().resolve()
        ts = modules / "typescript/lib/typescript.js"
        fflate = modules / "fflate/esm/browser.js"
        if not ts.is_file() or not fflate.is_file():
            raise ValueError(f"TypeScript and fflate are required under {modules}")
        env.update(
            TEST_PLUGIN_ROOT=str(plugin), TYPESCRIPT_MODULE=str(ts), FFLATE_MODULE=str(fflate)
        )
    if args.all or args.cwebp_tests:
        candidate = args.cwebp
        config = Path(args.config).expanduser().resolve()
        if not candidate and config.is_file():
            from mineru_like.config import load_settings

            configured = load_settings(config).compression.cwebp_binary
            candidate = str(configured) if configured is not None else None
        candidate = candidate or shutil.which("cwebp")
        if not candidate:
            matches = sorted((ROOT / "data/tools").glob("**/cwebp"))
            candidate = str(matches[0]) if matches else None
        binary = executable(candidate)
        if not binary:
            raise ValueError(
                "cwebp is required; set compression.cwebp_binary in --config, supply --cwebp, or put it on PATH"
            )
        env["MINERU_TEST_CWEBP"] = binary
    if args.all or args.flatpak:
        flatpak = executable("flatpak")
        if not flatpak:
            raise ValueError("Flatpak and org.libreoffice.LibreOffice are required")
        result = subprocess.run(
            [flatpak, "info", "org.libreoffice.LibreOffice"], capture_output=True, text=True
        )
        if result.returncode:
            raise ValueError("Flatpak app org.libreoffice.LibreOffice is not installed")
        env["MINERU_LIKE_TEST_FLATPAK"] = "1"
    if args.all or args.live:
        config = Path(args.config).expanduser().resolve() if args.config else None
        if not config or not config.is_file():
            raise ValueError("--config must name a runtime configuration for live inference")
        if not (ROOT / "tests/test_live_runtime.py").is_file():
            raise ValueError(
                "tests/test_live_runtime.py is missing; cannot run live inference tests"
            )
        env["MINERU_TEST_RUNTIME_CONFIG"] = str(config)
    return env


class Tee:
    """Keep pytest output visible while retaining the complete result transcript."""

    def __init__(self, terminal: TextIO, log: TextIO) -> None:
        self.terminal = terminal
        self.log = log

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        self.terminal.write(text)
        self.log.write(text)
        self.flush()
        return len(text)

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()


class NoSkips:
    def __init__(self) -> None:
        self.skips: list[str] = []

    def pytest_runtest_logreport(self, report: TestReport) -> None:
        if report.skipped:
            self.skips.append(report.nodeid)

    def pytest_collectreport(self, report: CollectReport) -> None:
        if report.skipped:
            self.skips.append(report.nodeid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all", action="store_true", help="enable every integration and reject skips"
    )
    parser.add_argument("--plugin", action="store_true")
    parser.add_argument(
        "--plugin-root",
        default="data/llm-for-zotero",
        help="plugin checkout (default: data/llm-for-zotero)",
    )
    parser.add_argument("--node-modules", default="data/test-tools/node_modules")
    parser.add_argument("--cwebp-tests", action="store_true")
    parser.add_argument("--cwebp", help="path to cwebp; used with --all or --cwebp-tests")
    parser.add_argument("--flatpak", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--config",
        default="config.local.toml",
        help="inference and encoder config (default: config.local.toml)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path("data/test-results.log"),
        help="console transcript, overwritten per run (default: data/test-results.log)",
    )
    args, pytest_args = parser.parse_known_args(argv)
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    print(f"Test log: {args.log_file.resolve()}", flush=True)
    with args.log_file.open("w", encoding="utf-8") as log:
        with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
            return run(parser, args, pytest_args)


def run(parser: argparse.ArgumentParser, args: argparse.Namespace, pytest_args: list[str]) -> int:
    try:
        os.environ.update(configure(args))
    except ValueError as exc:
        parser.error(str(exc))
    import pytest

    no_skips = NoSkips()
    result = pytest.main(pytest_args, plugins=[no_skips] if args.all else [])
    if args.all and no_skips.skips:
        print("Full suite rejected skipped tests:", *no_skips.skips, sep="\n  ", file=sys.stderr)
        return int(result) or 1
    return int(result)


if __name__ == "__main__":
    sys.exit(main())
