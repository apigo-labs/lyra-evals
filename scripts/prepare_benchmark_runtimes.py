"""Install isolated official evaluator dependencies; no dataset or model inference."""

import subprocess
from pathlib import Path

from vohu_evals.suites.adapters import UPSTREAM_REVISIONS

ROOT = Path(__file__).resolve().parents[1]
REPOS = {
    "tau2": "https://github.com/sierra-research/tau2-bench.git",
    "swebench": "https://github.com/SWE-bench/SWE-bench.git",
}


def main():
    for name, url in REPOS.items():
        checkout = ROOT / ".local/upstream" / name
        if not checkout.exists():
            subprocess.run(
                ["git", "clone", "--no-checkout", "--filter=blob:none", url, str(checkout)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(checkout), "checkout", "--detach", UPSTREAM_REVISIONS[name]],
                check=True,
            )
        revision = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        if revision != UPSTREAM_REVISIONS[name]:
            raise ValueError(f"{name} revision mismatch; existing checkout left unchanged")
    environment = ROOT / ".local/benchmark-runtime"
    if not environment.exists():
        subprocess.run(["uv", "venv", "--python", "3.12", str(environment)], check=True)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(environment / "bin/python"),
            "-r",
            str(ROOT / "deploy/suites/runtime-requirements.txt"),
            str(ROOT / ".local/upstream/tau2") + "[gym]",
            str(ROOT / ".local/upstream/swebench"),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
