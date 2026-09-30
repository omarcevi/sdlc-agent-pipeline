"""Run pytest in a throwaway working copy (outside this project's config)."""

import os
import subprocess
import sys
from pathlib import Path


def run_pytest(cwd: Path, *paths: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return result.returncode == 0
