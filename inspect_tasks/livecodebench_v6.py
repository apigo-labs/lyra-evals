"""LiveCodeBench release_v6 as an Inspect AI task (direct-api profile).

inspect_evals ships `ifeval` and `gpqa_diamond` but no LiveCodeBench release_v6, so this
module is a thin wrapper only: the dataset comes from this repo's frozen pack
(`.local/suites/livecodebench`), the prompt is the one the ACP path already sends
(`vohu_evals.suites.packs.livecodebench_prompt`), and grading is the existing offline
Docker grader (`vohu_evals.suites.adapters.grade_livecodebench`, image built by
`make benchmark-lcb-image`). No agent loop, no retries, no new grader, no Inspect sandbox.

Import path: Inspect runs in an isolated `uvx` environment, so `scripts/inspect_eval.sh`
exports `PYTHONPATH=src:.` for `inspect_tasks/...` tasks instead of installing this project
into that environment. Installing it would put `src/instruction_following_eval` (the frozen
Google original) on the path and shadow the josejg fork that inspect_evals' IFEval scorer
needs; PYTHONPATH is scoped to this task's invocation only.
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer, stderr
from inspect_ai.solver import TaskState, generate

from vohu_evals.suites.adapters import grade_livecodebench, lcb_code
from vohu_evals.suites.packs import livecodebench_prompt, load_pack

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE_ROOT = ROOT / ".local/suites"
DEFAULT_GRADER_IMAGE = "lyra-evals-lcb-grader:local"

# Stratification fields, if a future pack projection carries them. The current frozen pack
# only keeps the public projection (question, starter code, public tests); difficulty,
# platform and contest date live in the multi-GB hidden grader artifacts and are not read
# here so building the dataset stays cheap and hidden tests stay grader-side.
METADATA_FIELDS = ("difficulty", "platform", "contest_date", "contest_id", "question_title")

# Resolved once by the task and reused by the scorer, so no filesystem path or pack content
# is written into sample metadata / the .eval log.
_state: dict[str, object] = {"suite_root": DEFAULT_SUITE_ROOT, "image": DEFAULT_GRADER_IMAGE}
_rows_cache: dict[str, dict[str, dict]] = {}


def rows_by_id(suite_root: Path) -> dict[str, dict]:
    key = str(suite_root)
    if key not in _rows_cache:
        _, rows = load_pack(suite_root, "livecodebench")
        _rows_cache[key] = {row["case_id"]: row for row in rows}
    return _rows_cache[key]


def build_dataset(suite_root: Path) -> tuple[dict, MemoryDataset]:
    manifest, rows = load_pack(suite_root, "livecodebench")
    samples = [
        Sample(
            id=row["case_id"],
            input=livecodebench_prompt(row),
            metadata={
                "suite": "livecodebench",
                "has_starter_code": bool(row.get("starter_code")),
                **{field: row[field] for field in METADATA_FIELDS if field in row},
            },
        )
        for row in rows
    ]
    return manifest, MemoryDataset(samples=samples, name="livecodebench_v6")


@scorer(metrics=[accuracy(), stderr()])
def livecodebench_official():
    """pass@1 from the offline official grader.

    A grader result of `correct: False` is a model miss (INCORRECT). Infrastructure faults —
    docker missing, image absent, grader timeout, malformed grader output — raise out of
    `grade_livecodebench` (OSError / RuntimeError / ValueError / TimeoutError) and are left
    to propagate so Inspect records a sample error instead of a zero, mirroring the
    `system_failed` status that `scripts/score_benchmark.py` keeps out of the numerator.
    """

    async def score(state: TaskState, target: Target) -> Score:
        row = rows_by_id(Path(str(_state["suite_root"])))[str(state.sample_id)]
        result = await grade_livecodebench(
            row, lcb_code(state.output.completion), image=str(_state["image"])
        )
        correct = bool(result["correct"])
        return Score(
            value=CORRECT if correct else INCORRECT,
            explanation=(
                f"official {result.get('metric', 'pass@1')} grader: correct={correct}, "
                f"tests_run={result.get('tests', '')}"
            )[:200],
            metadata={"metric": result.get("metric"), "tests_run": result.get("tests")},
        )

    return score


@task
def livecodebench_v6(
    suite_root: str | None = None, grader_image: str = DEFAULT_GRADER_IMAGE
) -> Task:
    """Single-turn generation on the frozen LiveCodeBench release_v6 pack.

    Subsetting is Inspect's job: use `--limit` / `--sample-id`.
    """
    root = Path(suite_root) if suite_root else DEFAULT_SUITE_ROOT
    _state["suite_root"] = root
    _state["image"] = grader_image
    manifest, dataset = build_dataset(root)
    return Task(
        dataset=dataset,
        solver=generate(),
        scorer=livecodebench_official(),
        metadata={
            "pack_sha256": manifest["sha256"],
            "pack_count": manifest["count"],
            "release": (manifest.get("source") or {}).get("release", ""),
            "source_revision": (manifest.get("source") or {}).get("revision", ""),
            "grader_image": grader_image,
        },
    )
