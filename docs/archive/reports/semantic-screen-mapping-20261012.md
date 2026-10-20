# Semantic screen mapping and coordinate replay

## Contract

Cold task responses identify fields by semantic names and choose original-image geometry.
The provider wire contract uses named field/assignment entries so strict schemas need no
arbitrary object keys. ScreenMapping stores these as name-keyed dictionaries. OCR/SoM IDs
are not model task targets. Numeric references are allocated locally for the existing executor.
The original screenshot, OCR text/geometry and detected geometry are supplied to the model.

Warm action groups retain reference pHash, image dimensions, per-field rectangles and
per-action points. Matching reference dimensions and pHash distance <= 6 allow replay without
OCR target lookup; current input values are still resolved through source keys. A mismatch
returns no method binding so the shared selection path can consult the model. pHash similarity
is a reuse criterion, not proof of business completion. Historical anchor-based methods remain
readable; they are not silently converted to screenshot-bound methods.

Successful cold cache entries join to recorded responses independently of their mapping metadata.
The warm assembler restores semantic geometry and compiles screenshot-bound groups without
requiring OCR anchors. Whole-run success and the existing separate assembly stage remain.

## Tests changed

Task-worker fakes in test_screen_matching, test_method_runtime and test_worker_context now
return semantic fields, assignments and exact points. The screen-matching fake rejects numeric
provider response fixtures. Anthropic/OpenAI tests exercise the strict named-entry wire schema.
Obsolete numbered-target correction scenarios were consolidated into named-field binding,
missing-field rejection, mixed actions, reused observations and point-preserving execution.
Low-level compiler/marker tests retain numeric handles because those are still internal contracts.
ScreenMatch and ScreenPlan schema fixtures were deliberately updated for the new response contract.

Regression coverage includes cold mapping -> scene -> learned geometry, replay with empty OCR
and markers, mismatch fallback, distinct points on one field, successful cache -> warm assembler
-> compiled library, independent evidence ownership, unchanged-image navigation and attribution
of observation failure separately from prior action delivery.

## Verification

- Full pytest: 652 passed in 48.52 seconds.
- After the final cache-rebinding correction: test_screen_matching, 104 passed in 6.71 seconds.
- Ruff check, Ruff format --check, git diff --check: passed.
- No GUI interaction or paid API call was performed.

Offline run-056 captures: basic/additional-tab pHash distances were 10 and 12; additional empty/
filled distance was 4; save-dialog distances were 16–20. The configurable warm threshold of 6
separates those examples. This is not calibration across all layouts or proof of GUI success.
The existing legacy screenshot-free methods and other run-056 exploration/pruning behavior
have not been established as fixed by a new live pilot.


## Follow-up: ordered action compilation

Pilot 057 failed before query input: its valid named fill/search response was rejected by
an intent-combination whitelist even with observe_only enabled. Bypassing that check exposed
a second failure in the old field-only compiler. The selection call also stored multi-action
plans as a side effect that select_candidate's result could overwrite.

Removed the mixed-boundary whitelist/error and consolidated selection/navigation compilation
into compile_screen_action_plan. Nonhomogeneous current-screen actions now retain order in an
ActionPlan returned explicitly through SelectionResult. Login and homogeneous field batches
continue to use their existing verification and transport paths. Sequential delivery still
stops at the first transport failure and observes afterward; delivery alone is not value proof.
No new ERP-specific combination allowlist was introduced.

Removed the obsolete mixed-field rejection expectation and intent-specific audit expectations;
updated selection-effect tests to cover the common compiler. Added three end-to-end fake cases
(input then search/save/navigation) and two partial-failure cases. They verify exact points,
current source values, complete plan handoff, one initial decision call, subsequent observation,
and absence of fabricated input verification. Replayed the saved 057 response locally: it now
compiles to set_text(hbl_no) then click(search).

Validation: 657 passed in 52.41 seconds; after the final logging change, 109 matching tests passed
in 7.42 seconds. Ruff check, format check and diff check passed. No paid or GUI rerun performed.
