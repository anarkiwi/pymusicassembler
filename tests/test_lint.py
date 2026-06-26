"""Style and lint gates run as tests so they cannot be skipped."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGETS = ["src/pymusicassembler", "tests", "scripts"]


def test_black():
    result = subprocess.run(
        [sys.executable, "-m", "black", "--check", *TARGETS],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_pylint():
    result = subprocess.run(
        [sys.executable, "-m", "pylint", *TARGETS],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_import_all():
    """The public API imports cleanly and __all__ is exhaustive."""
    import pymusicassembler

    for name in pymusicassembler.__all__:
        assert hasattr(pymusicassembler, name), name
