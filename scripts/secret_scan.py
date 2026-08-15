from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "github-token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "openai-style-key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
SKIP_DIRS = {".git", ".venv", ".cache", ".pytest_cache", ".ruff_cache", "runs"}
TEXT_SUFFIXES = {"", ".md", ".py", ".toml", ".yaml", ".yml", ".json", ".txt", ".env"}


def candidate_paths(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return [root / item.decode() for item in result.stdout.split(b"\0") if item]
    return list(root.rglob("*"))


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in candidate_paths(root):
        if (
            not path.is_file()
            or SKIP_DIRS.intersection(path.parts)
            or (path.suffix not in TEXT_SUFFIXES and not path.name.startswith(".env"))
        ):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append(f"{path.relative_to(root)}: possible {name}")
    return findings


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    findings = scan(root)
    if findings:
        print("\n".join(findings))
        raise SystemExit(1)
    print("secret scan passed")


if __name__ == "__main__":
    main()
