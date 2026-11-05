"""Checks for the suite runner's prerequisite and skip handling."""

import subprocess
import sys
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "scripts/run_tests.py"


def test_full_run_reports_missing_plugin(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--all",
            "--plugin-root",
            str(tmp_path / "missing"),
            "--log-file",
            str(tmp_path / "missing.log"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "--plugin-root" in result.stderr
    assert "--plugin-root" in (tmp_path / "missing.log").read_text()


def test_standard_run_passes_pytest_arguments(tmp_path: Path) -> None:
    fixture = tmp_path / "test_fixture.py"
    fixture.write_text("def test_example():\n    assert True\n")
    result = subprocess.run(
        [sys.executable, str(RUNNER), "-q", str(fixture)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "1 passed" in (tmp_path / "data/test-results.log").read_text()


def test_full_run_rejects_skips(tmp_path: Path) -> None:
    # Exercise the pytest hook without requiring live services or external software.
    fixture = tmp_path / "test_fixture.py"
    fixture.write_text('import pytest\n\ndef test_example():\n    pytest.skip("reason")\n')
    script = (
        "import importlib.util, pytest, sys; "
        f"spec=importlib.util.spec_from_file_location('runner', {str(RUNNER)!r}); "
        "runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner); "
        "hook=runner.NoSkips(); "
        f"code=pytest.main(['-q', {str(fixture)!r}], plugins=[hook]); "
        "sys.exit(1 if hook.skips else int(code))"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 1
    assert "1 skipped" in result.stdout


def test_failed_run_is_logged_and_keeps_failure_status(tmp_path: Path) -> None:
    fixture = tmp_path / "test_failure.py"
    fixture.write_text("def test_failure():\n    assert False, 'logged failure'\n")
    log = tmp_path / "results.log"
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--log-file", str(log), "-q", str(fixture)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "logged failure" in log.read_text()
    assert "1 failed" in log.read_text()


def test_cwebp_uses_config_relative_path(tmp_path: Path) -> None:
    import argparse
    import importlib.util

    spec = importlib.util.spec_from_file_location("runner", RUNNER)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    binary = tmp_path / "tools/cwebp"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    config = tmp_path / "settings.toml"
    config.write_text('[compression]\ncwebp_binary = "tools/cwebp"\n')
    args = argparse.Namespace(
        all=False,
        plugin=False,
        cwebp_tests=True,
        cwebp=None,
        config=str(config),
        flatpak=False,
        live=False,
    )
    assert runner.configure(args)["MINERU_TEST_CWEBP"] == str(binary)


def test_full_run_uses_repo_root_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse
    import importlib.util

    spec = importlib.util.spec_from_file_location("runner", RUNNER)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.chdir(tmp_path)

    def configure(args: argparse.Namespace) -> dict[str, str]:
        assert args.all
        assert args.plugin_root == "data/llm-for-zotero"
        assert args.config == "config.local.toml"
        assert args.node_modules == "data/test-tools/node_modules"
        assert args.cwebp is None
        return {}

    monkeypatch.setattr(runner, "configure", configure)
    monkeypatch.setattr(pytest, "main", lambda *args, **kwargs: 0)
    assert runner.main(["--all"]) == 0
    assert (tmp_path / "data/test-results.log").is_file()
