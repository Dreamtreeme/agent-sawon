# Agent Sawon private repository

Current scope: through M2 / stages 3–4 (authorized by the user on 2026-09-29).
Follow CODEX_BRIEF.md and track progress in sibling agent-sawon-bench/docs/progress.md.
Implement semantic cache, demonstration learning, document extraction and validation,
and integrate bounded evidence review/recovery before measured development runs.
Unfinished M1 measurements remain open; scope expansion does not approve milestone exit.
The 2026-09-30 brief revision replaces the next 1,200-run development sweep with a
0–29 practice study and D4 scale proposal. Use ../agent-sawon-bench/docs/revised-development-plan-20260930.md;
preserve old plans/results and do not treat notebook/human/failover tools as implemented.

The user approved a visual-first plan on 2026-09-30. Follow ../agent-sawon-bench/docs/visual-first-plan-20260930.md before the old 300-run practice.
Implement screenshot/OCR/control detection and visual verification first; UIA is optional assistance.
Visual-only mode must exclude UIA observations and direct control patterns while retaining OS safety guards.
V1 relay contracts/isolation are implemented; OCR/control detection and visual brain (V2/V3) are next.
See ../agent-sawon-bench/docs/visual-v1-contract-20260930.md. Preserve previous UIA evidence; do not auto-resume old runs.

- Core may import only the public `sawon_interfaces` contract from bench.
- The future brain accesses the environment only through relay HTTP; never read ERP internals.
- Do not hardcode ERP labels, coordinates, AutomationIds, navigation paths, or tab order here.
- Evaluation seeds 5000–5999 are reserved. Never use them for development or tests.
- All model calls pass through the measured ModelClient wrapper; tests use replay/fakes.
- Keep run_log.jsonl append-only; replace status files atomically.
- Never commit API keys, original AI Hub data, or real business information.
- Keep decisions proposed, defaults configurable. Do not silently approve a decision.
- Python 3.13, typed code, pydantic v2, ruff, pytest. Mark desktop tests `gui`.
- Run `python -m ruff check .`, `python -m ruff format --check .`, `python -m pytest`.
- Do not publish this repository or its prompts/cache as part of routine implementation.
