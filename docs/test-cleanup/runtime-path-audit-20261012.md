# 657-case runtime path audit

Revision: 72c7f6f. Scope: every collected test case, including parameter expansions,
in 46 test files. No production or test behavior was changed.

## Method and limits

Ran `.venv/Scripts/python.exe -m pytest -p path_audit -q` with a temporary local profiler.
The plugin records production Python calls during each test protocol (including fixtures),
Brain construction settings, and source locations. Results: 657 passed in 114.74 seconds.
Instrumentation adds overhead: this is not comparable to the previous 52.41-second baseline.
The profiler observes the pytest thread, not separate processes/threads, C code or actual GUI.
Constructor settings can subsequently be changed by a test; actual called entrypoints take
precedence. Static review checked the fixtures, full-run callers, compiler tests and substitutions.
This audits path coverage, not an assertion-by-assertion mutation score or real GUI correctness.

The companion CSV contains one row for each of 657 node IDs, route category, stage calls,
key boundary flags, constructor settings, production files and entrypoint locations.
Raw traces and the profiler remain in ignored `.tools/path-audit`.

## Mutually exclusive inventory

| Category | Cases | What it demonstrates |
|---|---:|---|
| Semantic model path, partial node execution | 51 | mapping_prompt was called; no Brain.run/_run |
| Other Brain.run/_run entry | 50 | All instantiate screen_matching=False; some test startup/failure or mock _run |
| Other partial Brain paths | 134 | 82 instantiate screen_matching=True; 52 use other settings; no mapping_prompt or full run |
| Component paths without Brain execution | 422 | Models, storage, extraction, schemas, geometry, caches and other units |
| Total | 657 | Not 657 instances of the pilot workflow |

These are route categories, not keep/delete decisions. A component test does not need to
execute the whole application to be valuable. The 134 partial cases also include legitimate
model-free warm paths and validation tests.

Overlapping entrypoint counts: mapping_prompt 51; compile_screen_action_plan 38;
select_candidate 92; execute_plan 73; MethodLibrary.bind_with_context 19;
compile_warm_method 3. Exactly 9 cases call both mapping_prompt and execute_plan.
Only 3 semantic cases call all five nodes _observe/_select_node/_review_node/_act_node/
_verify_node: the new input-then-search/save/navigate tests. They manually drive one cycle,
not a graph-managed complete business workflow.

## Findings and actions

### P1: No complete current cold/warm workflow regression

`tests/test_screen_matching.py` drives individual nodes. Its three new mixed-action cases
cover proposal -> review -> delivery -> observation, but stop after one batch. No test combines
current semantic wire responses, graph scheduling, login, query, entry, save, final evidence,
success promotion, assembly, reopening a compiled library and a second full run.
The last transition/compiler defect could therefore coexist with hundreds of passing tests.
Action: build one synthetic full current-flow fixture and a cold/warm success pair through
Brain.run; use external fakes for model/relay/OCR/detector, retain real internal compilation,
selection, execution, progress, learning and review.

### P1: Full-workflow tests exercise another configuration

`tests/test_screen_batch.py:101` reports a complete successful workflow and asserts 10 calls,
but make_brain uses screen_matching=False (the default) and replaces VisualPerception with
CountPerception. `tests/test_policy_workflow.py:50` and `tests/test_brain.py:325` similarly use
default settings. These protect shared or legacy paths, not the semantic pilot contract.
The 50 run-entry cases occur in test_brain (13), credential_progress (4), input_target_recovery
(13), navigation_progress (7), persistent_worker (2), policy_workflow (5), prepared_brain (2),
screen_batch (1), visual_workflow (3).
Action: distinguish alternate-path coverage in names/markers and port the representative
business-success scenario to the active profile. Do not delete unrelated budgets/storage/transport
regressions merely because they use cheap alternate fixtures.

### P1: Warm tests mix anchor-based history with screenshot-bound reuse

`test_warm_compiler_preserves_successful_model_points_and_trace_order` uses screens without
screen_phash, requires anchor_relative_point, changes image size and expects rescaled coordinates.
`test_warm_compiler_rejects_missing_input_and_unscoped_point` likewise requires OCR anchors.
`test_learned_method_uses_current_scene_and_executor_without_model` explicitly expects
artifact_kind=runtime_trace, whereas the production profile uses compiled_only=True.
These are real historical code paths, not proof of the new pHash/unchanged-coordinate strategy.
Current coordinate cases exist in test_methods, test_screen_matching and the promoted-cache
join case in test_warm_method_compiler, but do not finish a warm workflow.
Action: label old-format compatibility tests separately; use the compiled-only screenshot-bound
fixture for main warm integration, including mismatch fallback and current values.

### P2: Some integration-looking tests intentionally bypass internal boundaries

`test_method_grounding_failure_retries_visual_cache_or_model_once` replaces select_match_response,
validate_or_replan and ground_match_response. It verifies fallback routing only; it cannot catch
real contract disagreements. test_click_refinement and a screen-matching evidence test replace
_observe. Visual workflow/batch fixtures replace perception with synthetic scene construction.
Action: retain genuinely isolated tests with explicit scope, but never count these as intact
pilot-path integration; pair their important boundary with an unpatched internal path.

### P2: Numeric internal handles are not necessarily stale provider assumptions

`test_decision_schema_has_no_freeform_text_or_coordinate_action` checks internal Choice,
whose coordinates are supplied separately by visual_points. Compiler tests may legitimately use
numeric executor handles after semantic mapping. They should not be mechanically deleted.
Provider-facing screen fixtures explicitly require named fields; Anthropic/OpenAI tests cover
named-entry wire schemas. The problem is missing end-to-end connections, not every numeric value.

## Recommended order

1. Add/port one graph-managed current-profile cold success and compiled warm replay scenario.
2. Split active-profile, historical compatibility and isolated component reporting.
3. Replace historical warm fixtures in main integration; preserve only justified format compatibility.
4. Audit assertions/deletion candidates separately using behavior/mutation evidence.

No paid model requests or desktop interactions were performed. No tests were deleted in this audit.
