"""Build the isolated official tau2 runtime; never calls a model."""

import shutil
import subprocess
from pathlib import Path

from vohu_evals.suites.adapters import UPSTREAM_REVISIONS

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / ".local/upstream/tau2"
if (
    subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    != UPSTREAM_REVISIONS["tau2"]
):
    raise ValueError("tau2 source revision mismatch")
context = ROOT / ".local/build/tau-runtime"
context.mkdir(parents=True, exist_ok=True)
shutil.copytree(
    source,
    context / "tau2",
    dirs_exist_ok=True,
    ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv"),
)
for name in ["Dockerfile", "worker.py"]:
    shutil.copy(ROOT / "deploy/suites/tau-runtime" / name, context / name)
shutil.copy(ROOT / "deploy/suites/runtime-requirements.txt", context / "requirements.txt")
subprocess.run(["docker", "build", "-t", "lyra-evals-tau-runtime:local", str(context)], check=True)
