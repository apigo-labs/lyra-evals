You are writing an external English product news report about a verified VOHU benchmark run.

Complete this in one turn. Do not emit a plan or progress update: read the evidence immediately and
return the final Markdown as your only response.

Read only the frozen evidence files under `{evidence_dir}`. Do not browse the web, call models,
edit the evidence, recompute scores, or invent missing values. Write a concise, credible report
with: headline, executive summary, methodology, VOHU IFEval results, quality/cost/latency
observations, an external-reference table, limitations, and source notes. The external-reference
table must include Claude Opus 5, Claude Fable 5, GPT-5.6 Sol, and OpenRouter Fusion. Keep VOHU's
IFEval metrics separate from vendor HLE/GPQA/BrowseComp and Fusion DRACO scores.

If `publishable` is false, do not present the output as released product news. Title it as an
internal validation draft, state clearly that it is not approved for external publication, retain
all system failures in the denominator, and explain which gate failed.

Every sentence containing a number or comparative claim must end with an evidence marker in this
exact form: `<!-- evidence:path.to.value -->`. Use "beats", "outperforms", or equivalent language
only when the evidence explicitly marks the external result as `comparable`. For `reference_only`,
state that protocols differ. If evidence is missing, omit the claim.

The verifier requires the exact JSON literal for every marked primitive to appear on the same
line: write `3`, `1`, and `false`, not words such as "three", "one", or "False". For IFEval scores,
use evidence paths ending in `_percent`; do not mark a percentage sentence with a fractional path.
Apply these rules mechanically:

- Write `publishable=false` on every line marked with `evidence:publishable`.
- Write digits for trial, failure, completed, invalid-output, prompt, instruction, and task counts.
- Every percentage claim must use a matching `_percent` marker, including per-trial claims.
- If one table cell contains multiple scores, place the matching evidence marker immediately after
  every individual score; one marker cannot source several numbers.
- If a line states that official IFEval is unavailable, include the exact literal
  `official_ifeval_available=false`.
- Source dates must use the exact `YYYY-MM-DD` evidence literal.
- Before returning, simulate the verifier line by line and remove or fix any unsupported claim.

Return Markdown only.
