# D8: confidence calculation

Status: **proposed**, 2026-09-29. No human decision has been recorded.

Default: deterministic cross-validation. Alternatives are model self-report or
a calibrated estimator based on a labeled validation set; neither is treated as
approved. The model's self-reported confidence is retained in `extracted.json`
only, alongside OCR confidence, and never overrides an inconsistency.

Configurable default rule scores: corroborated documents 0.95; supported single
document 0.80; weights differing within tolerance 0.85. OCR evidence must have
minimum confidence 0.80. Missing fields, invalid units, ungrounded transcription,
container check-digit errors and conflicting documents produce blocking checks.
Field-level blocking checks set the final score to zero. A final score below
0.80 also blocks entry. These scores are not calibrated correctness probabilities.

Weight differences use the larger of absolute tolerance (1 kg) and relative
tolerance (0.5% of the larger weight). In-tolerance differences remain visible at
low priority; candidate source priority does not remove or rewrite them. The
configuration can change tolerances and scores without changing the extraction
prompt or consulting truth files.

Validation includes a high-confidence model with bad OCR being blocked, low
model self-confidence with consistently corroborated evidence using the rule
score, and retention of both sides of every disagreement. Model confidence has
not been statistically calibrated; no comparison superiority is claimed yet.
