# Evidence key repair and seed 3 cold retry (064)

## Scope

Fix the seed 4 evidence conversion failure first; retry seed 3 without changing its identical-frame/review behavior. No seed 4 paid rerun or warm run was performed.

## Change and local verification

- ScreenMapping.local_payload accepts task inputs on both fresh model and cached mapping paths.
- Explicit assignments remain authoritative. Existing resolve_input_key resolves evidence keys or a uniquely matching model-reported field value when assignments are omitted. This only binds evidence already reported by the model; it does not infer new evidence from fields.
- Unresolved or ambiguous evidence is logged and omitted, without rejecting the action plan. Noncanonical display names cannot enter the runtime through these mapping paths.
- The prompt asks for assignments for reviewed fields, including read-only completion responses.
- Replaying seed 4 run 063's final semantic mapping with its five visible input values produced consignee, hbl_no, mbl_no, pod, shipper; ExecutionFacts accepted them. This is a local response replay, not proof of a new successful desktop run.
- Regression tests cover act/complete, Korean/spaced/English names, ambiguous values, unknown values and explicit assignment precedence.
- pytest: 632 passed, 1 pre-existing xfailed, 28.12 s. Ruff check and format check passed (199 files).

## Actual seed 3 retry

- Run: seed-audit-064-003-cold, claude-haiku-5-5, low.
- Education: bench/education/development/company-003.json.
- Fresh cache and method files; no production source changed during execution (protocol hashes checked).
- Worker: stopped, 81.633 s; harness: 84.496 s. Eight paid model calls, estimated $0.0145849, unknown cost calls 0.
- ERP: 0/10 saved fields; save was never reached, not proof that visible input failed.
- seq 204 records four basic fields but not description. seq 206–207 count description evidence missing once.
- The screenshot a4a753c5-b3e8-4fec-8fec-cfe74de0e6d7 visibly contains Synthetic sample parts. The model nevertheless proposes filling the supposedly empty description again.
- seq 218 reuses identical form without new model review; seq 220–222 count missing description evidence again and stop with repeated_subgoal_failure:task:description.
- This reproduces the prior seed 3 failure independently of the evidence-key conversion repair. No additional retry or code repair for seed 3 was made.

Artifacts: ../agent-sawon-bench/evidence/seed-audit-20261017-064/status.json and protocol.json; cases/seed-audit-064-003-cold/613ff6fe7769c6bfc586/case-1afd724b585b4efe917190aeb77e7d2c/case.
