# Strict test-suite audit — 2026-10-10

## Scope and evidence

This report replaces the earlier per-test template inventory. That inventory repeated test names
without proving defect-detection value and is not audit evidence.

The audit covered the original 669 collected cases. One redundant `extra=forbid` case had already
been removed in commit `70d5724`; the strict pass measured 668 cases before the remaining changes.
Production code was not changed.

Measurements used:

- sequential coverage run before: `668 passed in 64.98s`
- sequential coverage run after: `575 passed in 55.16s`
- plain sequential run after: `575 passed in 36.19s`
- `pytest-xdist -n 4` after: `575 passed in 14.96s`
- per-test duration: JUnit `time` values from `.audit-before.xml` and `.audit-final.xml`
- per-test production-line ownership: coverage dynamic contexts from `.coverage`
- mutation evidence: isolated detached worktree, one production mutation at a time

Coverage did not pay for the reduction:

| Metric | Before (668) | After (575) | Change |
|---|---:|---:|---:|
| Statement | 90.3594% | 90.3716% | +0.0122%p |
| Branch | 79.7619% | 79.7991% | +0.0372%p |
| Combined | 87.7318% | 87.7502% | +0.0184%p |

## Mutation evidence: what actually turns red

These mutations were applied to production code only in a temporary worktree and restored after
each run.

| Injected defect | Test that failed | Failure observed |
|---|---|---|
| Remove the `bool` exclusion from the repeat-count validator | `test_invalid_repeat_count_rejected_before_output_creation` | `True` stopped raising before any output was created |
| Make every policy rule behave as a wait rule | `test_broad_action_types_cannot_bypass_intent_or_source_contract` | the `navigate-as-wait` case was accepted by the exported schema oracle |
| Relax `NonemptyRef` from `\S` to `.*` | `test_wire_schema_and_local_validation_reject_each_invalid_contract_shape` | the blank-reference payload passed JSON Schema validation |
| Remove WinError 32 from the atomic-replace retry allowlist | both consolidated atomic-replace retry tests | the transient case aborted after one attempt and the exhausted case no longer made six attempts |
| Replace `business_revision += 1` with `+= 0` | both reconciliation tests | observed query and visible-field reconciliation left revision at zero |
| Log rejected input selection as `action_dispatched=True` | `test_rejected_query_and_editor_targets_use_the_same_bounded_rediscovery` | the exact audit payload differed at `action_dispatched` |
| Always invalidate editor evidence in model-owned review | `test_model_owned_generic_evidence_is_not_invalidated_by_later_screens` | the `pytest.fail` sentinel fired before evidence could be retained |
| Remove the prepared-state `READY` gate | `test_prepared_validator_rejects_each_invalid_handoff_without_mutation` | the `not_ready` handoff stopped raising |

One mutation survived: removing `saved != record` from `validate_prepared_case`. The same changed
record also makes `can_enter(record, report)` false, so the handoff still fails. Adding another test
would not isolate this predicate under the current public contract. This is a redundant production
condition, not an excuse to retain another test. It is left unchanged because this cleanup forbids
production edits.

## 1. Immediate deletion targets (executed)

### Validation inputs that exercised an already-covered validator branch

- `test_transition_rejects_unverified_effects_and_arbitrary_data[control_label]` was removed.
  `password` already proves Pydantic's unknown-field rejection on the same model and error path.
- `test_invalid_repeat_count_rejected_before_output_creation[-1]` was removed. `0` reaches the same
  lower-bound guard; `True`, `1.5`, and `101` remain because they target bool exclusion, integer type,
  and upper bound respectively.
- `test_invalid_cost_budget_rejected_before_provider_call[-1]` was removed because `0` reaches the
  same non-positive branch. `nan` was removed because `inf` already reaches Pydantic's finite-number
  guard. `0`, `11`, and `inf` retain lower-bound, upper-bound, and finiteness coverage.
- `test_corrupt_log_is_not_modified[not-json]` was removed. The partial JSON tail already trips the
  JSON decoder and proves bytes remain unchanged. The blank-line case remains because it reaches the
  separate empty-record guard.

### Heavy graph scenarios whose observable contract is fully covered elsewhere

- The same-window semantic-change graph replay was deleted. The direct `navigation_state` matrix
  still checks value, name, label, enabled state, hierarchy, and content changes, while the retained
  graph replay checks that actual semantic progress resets the phase counter.
- The two-case business-evidence panel-revisit replay was deleted. Reconciliation tests now assert
  both the verified field and `business_revision == 1`; the exploration-ledger tests assert that a
  changed revision permits revisiting the target.

No deleted behavior was left without an assertion. The final coverage run gained one covered line
and one covered branch relative to the 668-case baseline.

## 2. Consolidate / parameter reduction (executed)

The rows below show collected cases, not merely source functions. The loop bodies preserve a case
label in assertion failures, so consolidation does not hide which contract member failed.

| Test module | Before | After | Reduction | Preserved defect boundaries |
|---|---:|---:|---:|---|
| `test_decision_policy` | 40 | 15 | 25 | credentials by screen, query phase state, six final-completion prerequisites, intent/action/source compatibility |
| `test_wire_contract` | 33 | 5 | 28 | every valid union member and all 19 invalid shapes still pass through both JSON Schema and Pydantic |
| `test_pipeline_measure` | 29 | 21 | 8 | bool/type/lower/upper/finiteness guards; duplicate representatives removed |
| `test_prepared_brain` | 11 | 4 | 7 | five direct handoff mutations, one CLI ordering sentinel, three independent Brain gates |
| `test_atomic_replace_retry` | 11 | 5 | 6 | WinError 5/32/33 membership, retry exhaustion, and immediate propagation of unrelated errors |
| `test_transition_contract` | 20 | 15 | 5 | ordering, secret source/fact vocabulary, and unknown-field rejection remain separate loop entries |
| `test_validation_metadata` | 22 | 18 | 4 | private extra names and schema metadata keys are checked in one model-building loop each |
| `test_brain_http` | 6 | 4 | 2 | one server lifecycle checks both 403 and redirect; both unsafe token forms remain |
| `test_storage` | 20 | 18 | 2 | malformed JSON and blank record retain separate parser paths |
| Other graph de-escalations | 37 | 31 | 6 | input-target recovery, navigation progress, and policy workflow; described below |
| **Strict pass total** | **668** | **575** | **93** | coverage increased |

Including the earlier redundant transition case, the original 669-case suite is now 575 cases: a
94-case reduction (14.1%).

The remaining parametrizations with three or more rows were inspected rather than flattened merely
to lower the count. They represent distinct branches or state combinations: numeric/text/package
field matching, URL origin components, grid topology, retry-limit states, provider error category
mapping, OCR punctuation classes, five discrepancy rules, normalization families, ambiguous unit
rules, incomplete evidence shapes, batch geometry failure timing, taught input surfaces, mapping
failure stages, verification-omission positions, invalid visual roles/parents, storage cache
replacement modes, perception image modes, and visual evidence geometry. Collapsing these into one
collected test would reduce the displayed count but would not reduce work or remove duplication.

## 3. De-escalate to unit test (executed)

| Former graph/integration work | Replacement | Why the replacement catches the same bug |
|---|---|---|
| Full safe-wait workflow before save | direct `_collect_editor_evidence` followed by `DecisionPolicy` assertions | deleting evidence collection leaves fields unverified; permitting fill or denying save makes the policy assertions fail |
| Full query/editor retry success workflow (previously about 2.2s) | direct `_retry_input_target_selection` for both phases | asserts retry count, phase failure, zero relay actions, and the complete non-dispatch evidence payload; a separate retained replay verifies fresh refs on re-observation |
| Three full missing/disabled target scenarios | one missing-target replay plus direct `validate_action_guard` checks for disabled query and editor controls | the replay owns hard-stop behavior; the unit checks own the two disabled-control branches |
| New-window navigation graph replay | direct before/after `navigation_state` comparison | a handle change must alter semantic state; no model, log, or relay loop is needed to prove that function |
| Two panel-revisit graph replays | reconciliation revision assertions plus ledger checkpoint tests | a missing revision increment or a ledger that ignores revision now fails at its owning layer |

Measured JUnit case time fell by 3.024s in input-target recovery, 2.243s in policy workflow, and
1.268s in navigation progress. These are direct savings from removing repeated graph setup/model
replay rather than from renaming or hiding cases.

## 4. The remaining >=500ms tests with zero unique production lines

Dynamic coverage found nine final cases above 500ms with zero exclusively owned production lines.
Each is classified C because its value is a sequence/state combination; where that claim could not
be made, the case was deleted or de-escalated above.

| Test | Time | Required state combination and distinct failure |
|---|---:|---|
| `test_action_delivery_and_final_proof_are_separate_auditable_events` | 1.883s | joins every dispatched action to a returned transition, then proves save acknowledgement is not final business proof; no unit test covers that event ordering across a completed run |
| `test_concurrent_processes_keep_all_events_and_unique_sequences` | 0.947s | two OS processes contend for the same append-only log and must produce 24 unique contiguous sequences; mocking the lock removes the race being tested |
| `test_failed_target_is_allowed_again_after_a_real_state_change` | 0.712s | failed target is blocked in revision N and becomes eligible only in N+1; neither the static ledger unit nor no-progress replay spans both revisions |
| `test_real_same_window_progress_resets_phase_failures` | 0.702s | failure, semantic content change, and a later failure must produce counter sequence 1 then 1 rather than 1 then 2 |
| `test_failed_target_prompt_allows_recovery_via_another_observed_control` | 0.598s | the prompt must exclude the failed tab while still exposing and dispatching an alternate observed control |
| `test_unchanged_navigation_blocks_second_click_and_stops_after_two_failures` | 0.565s | first click is dispatched, the identical second target is withheld, and both failures reach the configured graph stop reason |
| `test_alternating_navigation_intents_cannot_evade_the_phase_cap` | 0.549s | `navigate` then `select_tab` use different subgoal keys but must share the phase cap; removing the phase counter changes the stop reason |
| `test_broad_action_types_cannot_bypass_intent_or_source_contract` | 0.542s | same executed lines, different union/state values; the `if rule.wait -> if True` mutation was killed specifically by `navigate-as-wait` |
| `test_ref_focus_geometry_hash_and_model_prose_churn_do_not_count_as_progress` | 0.504s | refs, focus, bbox, hash, and prose all change while semantic target identity remains; a second click must still be withheld |

“Zero unique lines” is therefore not used as a shield. It triggered individual A/B/C review. The
four cases removed or de-escalated by that review are listed above; these nine remain because their
return values, stop reasons, dispatched-action counts, or event sequences differ from the tests that
share their lines.

## Assertion validity audit

`assert_contract` is not treated as an opaque assertion. It checks three independent boundaries:

1. the exported JSON Schema with the independent `schema_accepts` oracle,
2. `DecisionPolicy.allows` on a validated internal decision,
3. Pydantic response parsing and conversion for allowed payloads, or a required `ValidationError`
   for denied payloads.

`test_assert_contract_fails_for_a_contradictory_oracle_expectation` deliberately reverses known
valid and invalid expectations and requires `AssertionError` both times. The policy mutation above
also proves the helper turns red on a real production defect.

The `pytest.fail` callback in model-owned evidence handling was strengthened. The test now supplies
actual evidence, records `_record_model_evidence` calls, and asserts the field entered verified
state. If invalidation occurs, the callback fires; if neither callback nor recording occurs, the
new call/verified assertions fail. The forced-invalidation mutation demonstrated the first path.

### CI failure-label probe

Consolidated loops use `labeled_case`, which retains the original exception and adds
`consolidated-case=<label>` to its traceback. A one-time probe branch deliberately failed six
representative loop members: `navigate-as-wait`, `blank-ref`, `winerror=32`, `blocking`, `inf`, and
`/redirect:302`. GitHub Actions run `38001600048` printed every label in both Ubuntu and Windows
pytest logs. The probe branch and its injected failures were deleted afterward.

The permanent implementation then passed GitHub Actions run `38001588557`: Ubuntu unit, Windows
unit, and Ubuntu `pytest-xdist -n 4` jobs all completed successfully.

## 5. Quantitative result

| Item | Original | Final | Result |
|---|---:|---:|---:|
| Collected tests | 669 | 575 | -94 (-14.1%) |
| Plain sequential wall time | historical 99.9s target baseline | 36.19s | target of <=40s met |
| Instrumented coverage wall time | 64.98s (668-case strict baseline) | 55.16s | -9.82s (-15.1%) |
| `xdist -n 4` wall time | 16.25s (668 cases) | 14.96s | -1.29s (-7.9%) |
| Statement coverage | 90.3594% | 90.3716% | defended; +0.0122%p |
| Branch coverage | 79.7619% | 79.7991% | defended; +0.0372%p |

The plain sequential target is met without changing the default runner, production code, or test
isolation model. The remaining large time difference between JUnit case totals and wall time is
collection, worker startup, coverage instrumentation, and fixture/process overhead rather than
unaccounted test bodies.
