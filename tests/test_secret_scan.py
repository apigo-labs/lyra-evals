from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_secret_scan_ignores_gitignored_env_but_scans_publishable_files(tmp_path: Path) -> None:
    scanner = Path(__file__).parents[1] / "scripts" / "secret_scan.py"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "API_KEY=" + "sk-" + "ignoredsecret1234567890\n", encoding="utf-8"
    )

    ignored = subprocess.run(
        [sys.executable, str(scanner), str(tmp_path)], check=False, capture_output=True, text=True
    )
    assert ignored.returncode == 0
    assert ignored.stdout == "secret scan passed\n"

    (tmp_path / ".env.example").write_text(
        "API_KEY=" + "sk-" + "publishablesecret1234567890\n", encoding="utf-8"
    )

    publishable = subprocess.run(
        [sys.executable, str(scanner), str(tmp_path)], check=False, capture_output=True, text=True
    )
    assert publishable.returncode == 1
    assert publishable.stdout == ".env.example: possible openai-style-key\n"
