# Agent Sawon private repository

Current scope: M1 / stage 2 (authorized by the user). Follow CODEX_BRIEF.md and track
progress in the sibling agent-sawon-bench/docs/progress.md. Implement relay-facing
reasoning, bounded execution, observation summaries and measured development runs.

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
