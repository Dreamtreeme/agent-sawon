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
