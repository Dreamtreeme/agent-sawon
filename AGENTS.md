# Agent Sawon private repository

The user authorized an opt-in Jev screen-matching pilot on 2026-10-01.
Use the shared measured ModelClient, ScreenMatch compiler and executor; retain Gemini
for image-based recovery. This does not authorize broad Jev sweeps or milestone exit.

Current scope: through M2 / stages 3–4 (authorized by the user on 2026-09-29).
Follow CODEX_BRIEF.md and track progress in sibling agent-sawon-bench/docs/progress.md.
Maintain visual-match cache, executed-screen method learning, document extraction and validation,
and integrate bounded evidence review/recovery before measured development runs.
Unfinished M1 measurements remain open; scope expansion does not approve milestone exit.
The 2026-09-30 brief revision replaces the next 1,200-run development sweep with a
0–29 practice study and D4 scale proposal. Use ../agent-sawon-bench/docs/revised-development-plan-20260930.md;
preserve old plans/results and do not treat notebook/human/failover tools as implemented.

The user approved a visual-first plan on 2026-09-30. Follow ../agent-sawon-bench/docs/visual-first-plan-20260930.md before the old 300-run practice.
Implement screenshot/OCR/control detection and visual verification first; UIA is optional assistance.
Visual-only mode must exclude UIA observations and direct control patterns while retaining OS safety guards.
The user authorized L2C-style automatic foreground activation on 2026-09-30.
The relay may restore an allowlisted ERP before visual capture/input; foreground loss alone
does not require a new confirmation. Observation-only recording stays passive.
The user removed duplicate screenshot password overlays on 2026-09-30. Preserve the app's
rendered password bullets; do not reinstate white/black control paint-over in the relay.
V1 isolation and V2/V3 visual perception/brain integration are implemented with fake tests; real visual completion is unverified.
Visual cache/demo provenance and notebook/human/scoreboard tools are implemented. The user confirmed HTTP400 billing review and authorized paid resumption within the existing total $50 cap on 2026-09-30. Preserve historical unknown-cost records; run bounded visual pilots before scaling.
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

The user selected Luna as the primary screen-worker path on 2026-10-02.
Follow ../agent-sawon-bench/docs/luna-primary-plan-20261002.md.
Preserve Gemini and Jev as comparison profiles; prioritize actual save/final-query completion.

On 2026-10-08 the user removed legacy UIA semantic-cache and observation-delta demonstration learning. Do not restore their modules, CLI flags or cache formats. Keep visual_match, learned methods and visual recording; prepared document handoff lives in pipeline.handoff.

On 2026-10-08 the user consolidated runtime profiles into config/brain.visual.toml, config/models.toml (Luna), and config/pipeline.toml (Korean GPU OCR). Do not restore milestone/harness/provider-comparison presets. Provider adapters may remain without committed comparison profiles. Cost-rate data lives in models/pricing.toml; historical experiment configurations remain in Git and run artifacts.

On 2026-10-08 the user authorized removing the legacy screen-matching Procedure entry path. Screen matching now requires TaskInstruction through --task/task_path. Do not restore --procedure, procedure_path, the old Procedure loader, or compiled fixed-step candidate filters. Preserve TaskInstruction.procedure (the user's taught steps), the shared capability contract and historical run evidence. Legacy non-screen-matching policy phases remain outside this removal stage.

On 2026-10-08 runtime refactor stages 2–6 consolidated progress/evidence and method state into WorkerState. Keep model, visual-cache and learned-method proposals on the shared matching pipeline. Execution transport accepts ExecutionRequest/returns ExecutionResult and must not receive Brain. MethodRuntime accepts explicit context/state/services; do not restore whole-Brain ownership. Keep current evidence markers until semantic grounding, without treating a binding or relay delivery as verified business success. See docs/refactor-runtime-stages-20261008.md.
