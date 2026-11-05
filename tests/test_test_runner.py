"""Checks for the suite runner's prerequisite and skip handling."""

import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "scripts/run_tests.py"


def test_full_run_reports_missing_plugin():
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--all"], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "--plugin-root" in result.stderr


def test_standard_run_passes_pytest_arguments(tmp_path):
    fixture = tmp_path / "test_fixture.py"
    fixture.write_text("def test_example():\n    assert True\n")
    result = subprocess.run(
        [sys.executable, str(RUNNER), "-q", str(fixture)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_full_run_rejects_skips(tmp_path):
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
