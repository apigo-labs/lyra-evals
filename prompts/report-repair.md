Return a corrected final Markdown report in one turn, with no plan or progress message.

Read only `{evidence_dir}/summary.json` and `{evidence_dir}/../report.md`. Preserve the report's
meaning and internal-draft status, but fix every evidence-verifier violation below. Do not browse,
recompute, add claims, or edit files yourself; return the full replacement Markdown only.

Mechanical requirements:

- Every line marked `evidence:publishable` must literally contain `publishable=false`.
- Replace number words with exact digits on marked lines: use `3`, `1`, and `0`.
- A sentence marked with a percentage evidence path must show the exact percentage value.
- For per-trial percentage claims, use the new `_percent` paths, never fractional paths.
- In external-reference table cells, put the corresponding evidence marker immediately after each
  individual numeric score, not one marker after several scores.
- A line marked `official_ifeval_available` must literally include
  `official_ifeval_available=false`.
- A line marked `external_references.as_of` must include the exact literal `2026-08-10`.
- A sentence mentioning both request count and cost must include both matching markers.
- Before returning, inspect every marked line: every numeric or boolean primitive referenced by
  every marker must appear exactly on that same line.

Return Markdown only.
