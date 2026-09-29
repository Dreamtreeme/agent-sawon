# Model decision diagnosis — 2026-09-28

Latest follow-up: [2026-09-29 root-cause diagnosis](model-root-cause-20260929.md).
The saved latest request reproduced a wire-schema/local-validator mismatch;
current-step-only guidance produced valid search choices in three measured probes.
These are request-level diagnostics, not a new completed GUI workflow.

Private diagnostic record. This file describes the failed Flash-Lite decision,
the offline guard check, stronger-model request failures through 10:01 UTC,
the successful exact-request retry at 10:02 UTC, and subsequent GUI runs after
the user reported correcting the billing account. Following the integration
fixes described below, the 10:26 run completed saving and final verification
with `gemini-3.8-flash` and ended awaiting human approval. Historical failures
are retained for attribution. All times below are UTC.

## Full Lite retry and post-save phase repair: 2026-09-29 KST

The 08:04 KST one-call connectivity check returned a valid JSON response in
849.78 ms, with 12 input and 6 output tokens (USD 0.0000186). Following the
user's request for a full workflow rerun, run `20260928T233108Z-a557cbb5`
completed DB entry with all ten fields correct. All 26 calls returned normally;
there were no provider errors. It took 57.956 seconds excluding the inspection
hold and cost USD 0.0491139. The workflow still stopped before final query.

The last sequence was save, confirmation, editor tab navigation, then another
record field write selected in `final_query`. The write was rejected by
`write_before_duplicate_check`; the misleading reason also covers writes
outside the entry phase. The model treated entry as unfinished despite the
runtime having already verified every field. This is a phase-contract mismatch,
not a provider availability failure. Its private case is
`cases/stage2/dfd51be47bad5878d142/case-011e5527be4a431285571e620eccce83/case`.
The public evidence is `agent-sawon-bench/evidence/stage2/lite-rerun-20260929/`.

The fix adds current phase guidance for post-save query verification. A narrowly
scoped handler allows bounded `final_query_required` feedback only for a rejected
record text fill in editor/partner after save and duplicate clearance. Existing
target, role, credential and write guards run first. No rejected write, automatic
navigation, repeated save, invented query proof, or phase reset is introduced.
Synthetic regressions keep the editor open after saving, then test model-chosen
query navigation and full verification, repeated rejected fills, and missing or
disabled targets. They establish exactly one save and zero post-save field writes.
This correction changes the prompt/source and must be measured in a new GUI run.

The corrected run `20260928T233744Z-d7f0e670` reached final query after saving all
ten fields, but its last two responses failed `Decision` validation. Its 31
measured calls comprise 29 validated responses and two local ValidationErrors,
with no HTTP/provider errors. Elapsed time was 63.616 seconds and cost was
USD 0.0578098. The query window still hid several right-hand columns, so no
complete final proof was accepted. The original invalid responses were not
retained; their exact contents cannot be recovered from these logs.

Offline reconstruction reproduced all 31 original request fingerprints using
saved observations and successful decisions. The final request hash was
`e8060281a2cb71b6d3c420455205e5bbe310ef97042246591db960af5b960fb2`.
A single new measured Lite call of that exact request reproduced the known
action-contract validator failure (`action_requires_executable_intent`), in
1856.14 ms with 5144 input/347 output tokens and USD 0.0024107. This proves a new
failure of the same request; it is not recovery of either original response.
Private probe evidence is under
`cases/stage2-diagnostics/final-reconstruction-20260928T234210Z-09563862/single-probe-2163d966/`.

The policy, active final-phase guidance and operation schema description now
explicitly name `complete`/`verify`/null action for final evidence and separate
this from `act`/`navigate` when observed window controls must reveal columns.
ValidationError feedback repeats this phase-specific contract. The response
validator and same-row completeness checks are unchanged: no invalid response
is coerced into completion and no missing evidence is inferred.

Run `20260928T234641Z-5b66468a` then produced schema-valid decisions on all 25
calls and saved ten matching fields, but tried `complete` before a fresh final
query input/search. It stopped at `completion_without_required_workflow`
(52.557 seconds, USD 0.0456837). The supplied final evidence covered eight of
eleven components; three offscreen components were absent. The completion guard
correctly rejected this; displayed query rows were not substituted for the
required fresh query sequence.

To make these prerequisites visible at the response boundary, a pending-final-
query schema now permits only act/stop until both query verification and search
flags are true. A mandatory next_required_step distinguishes query_input,
search and final_row_evidence. A separate final_required_fields list prevents
entry proofs from being mistaken for current query evidence. Incomplete final
evidence receives an explicit missing-field list and observed-navigation guidance.
No runtime action, query flag, row proof, or completion is supplied automatically.

All 152 private tests, ruff check and format check passed before the next two
same-code GUI attempts. `20260928T235301Z-cced547f` saved all fields but stopped
at blocked_foreground (ChatGPT.exe observed as foreground, cause unknown).
It made 31 valid calls, cost USD 0.0578516 and took 66.022 seconds. Navigation
had also repeated before foreground changed; that remains a reliability concern.

After the user explicitly confirmed foreground readiness, the unchanged-code
retry `20260928T235735Z-30baad6d` saved ten matching fields, entered the final
query value and expanded the query window. It did not execute the required
search before two responses failed the pending-final-query schema. It stopped
at repeated_subgoal_failure:final_query:decision. Exact invalid response fields
are unavailable, so they are not inferred from the exception class alone.
This run had 28 calls (26 validated and two local ValidationErrors), no HTTP
errors or foreground rejection, cost USD 0.052237 and took 56.240 seconds.
The latest full workflow remains incomplete despite DB success. No further
runtime change was made after the user requested the same-code retry.

## Earlier continuation: 2026-09-29 KST (2026-09-28 UTC runs)

The user resumed verification and requested the next implementation step. We
reran Lite, repaired two concrete decision-contract issues, and attempted the
48-cell development pilot: two summaries and two baselines, seeds 0/17,
stable/none, three repeats. No evaluation seeds were used.

| UTC run | Outcome | Calls | Known USD subtotal |
| --- | --- | ---: | ---: |
| `20260928T144713Z-af958226` | Credential typing used login intent; command guard stopped it | 3 | 0.0024091 |
| `20260928T145137Z-5129fcc0` | Save selected with pol unverified; save guard stopped it | 21 | 0.0358358 |
| `20260928T145650Z-61b49008` | Third call returned 503 UNAVAILABLE | 3 | 0.0014928 |
| `20260928T145749Z-237ad498` | Fifth call returned 503 UNAVAILABLE | 5 | 0.0037061 |
| `20260928T145910Z-0d1cf985` | Pilot: first Lite call returned 503 UNAVAILABLE | 1 | 0 |

The first fix describes action intent at the schema boundary and in policy:
typing credentials uses `credential`, while `login` activates the login
command. Invalid command/action combinations receive bounded feedback without
executing the rejected action. The second fix exposes `remaining_fields` in
trusted task state. An attempted save with unverified fields receives the
explicit missing list; the model must fill them or supply current editor
evidence. No value, reference, or verification flag is silently supplied.

Both fixes were present in the final three runs above and passed 142 private
tests. Their latest full GUI success remains unconfirmed because the three
runs ended with measured provider errors. All three error calls returned no
usage; their costs and total costs remain null. The five runs have a combined
known subtotal of USD 0.0434438, not a known total. The errors establish HTTP
503/server_error, not a billing diagnosis or a model reasoning failure.

The pilot recorded all 48 planned rows, but only 25 trials actually ran:
24 baselines and one Lite trial. The remaining 23 model rows have
`matrix_cost_limit_or_unknown_cost`; they were not executed. Baselines saved
the complete ten fields in 12/24 trials and finished the on-screen workflow
in 9/24. No duplicate extra rows or unauthorized transmission attempts were
recorded. The two Lite summary strategies cannot be compared from this batch.
The harness iterates agents within each condition; listing baseline agents
first did not run all 24 baselines before the first model trial.

The run was on the local 3840×2160 desktop at system DPI 144, not the evaluation
reference resolution/DPI. Source hashes and original failed/blocked rows are
preserved in `agent-sawon-bench/evidence/stage2/lite-pilot-20260928/`.
Older upper-model success and intermediate Lite DB success used earlier code;
neither proves this implementation's full success.

After the pilot, independent review found that the bounded intent repair also
retried credential typing outside the login screen. The action was already
blocked, but the required immediate-stop behavior was not preserved. An early
credential-screen check now stops it before considering intent repair; two
additional set_text/type_text regression cases cover zero actions, zero repair
attempts, and no secret leakage. This final guard change was made after the
frozen pilot and has not been verified in a new GUI run.
Final local validation passed all 144 private tests, ruff check, and ruff
format --check. Tests use replay/fakes and made no external model calls.

The next measurement remains a complete latest-code Lite smoke run followed
by a fresh frozen comparison after service recovery. M1 stage 2 comparison,
development seeds 0–49, and human M1 exit decisions remain open.

## Initial finding (historical)

The immediate cause of the stopped development case was a model response that
omitted required grounding evidence. The observation, summary, and duplicate
guard did not lose or reject a valid grid reference.

The case used `gemini-3.5-flash-lite` and stopped at
`missing_query_grid_evidence`. Its eight measured model calls returned
successfully at the API/JSON boundary. This is distinct from successful task
execution: the final decision was semantically invalid, and the task did not
reach entry, save, or transmission.

Private case evidence:
`cases/stage2/d700becbc52622d133f8/case-ac38ab4b5dcd4983a44356d48b93dd43/case/`.

## Exact-request evidence

- The final observation was `observations/0013.json`. The generated
  `label_grouped` summary included an enabled `DataGrid` with reference `e1`.
  The summary was not truncated.
- `brain/graph/engine.py` explicitly instructs the model to submit the search
  and then provide the result `grid_ref` before claiming `duplicate_clear`.
- The final recorded decision, at sequence 51, had
  `operation="duplicate_clear"`, `grid_ref=null`, and an additional click action
  referring to the new-entry button. It mixed a duplicate-check decision with
  the next navigation action instead of supplying the grid evidence.
- The final prompt and `Decision` JSON schema were reconstructed from the saved
  observation, record, plan, goal, and feedback. Their request fingerprint
  exactly matched sequence 50 of the original measured log:
  `92a66444d5481dbef3bc113cb103e1c0023b7b1e4cba39da73d58629e5a9484a`.
- The reconstructed prompt contained 9,417 characters. The request fingerprint
  binds the prompt, complete response schema, and ordered image hashes; this
  request had no images.

The reconstruction used the six prior successful action decisions to rebuild
feedback. Its phase was `duplicate_query`, both query flags were true,
`verified_fields` was empty, and both credential-availability flags were true.
Credential values were neither needed nor included in the model prompt.

## Offline guard check

Evidence: `cases/stage2-diagnostics/guard-check-20260928T100159Z-73d1dc5e/result.json`.

With the recorded observation and state, the original null reference produced
`missing_query_grid_evidence`. Replacing the missing grid reference with the
observed `e1` passed the duplicate guard: the next node was `observe`, the phase
became `enter`, and `duplicate_clear` became true. This check made zero API calls
and zero GUI actions. It tested the guard only; it is not an end-to-end task
success or evidence that a model would independently produce the correction.

## Historical stronger-model request failures

Each request below passed through the measured `ModelClient` wrapper exactly
once, with SDK retries disabled. The four decision probes used the exact
request fingerprint above, keeping the observation, prompt, and response
schema unchanged. None returned a decision that could be assessed for model
quality.

| Completed at | Model | Request | HTTP / provider status | Error type / category | Latency |
| --- | --- | --- | --- | --- | --- |
| 09:12:31.971 | `gemini-3.8-flash` | Plan schema probe | 503 / `UNAVAILABLE` | `ServerError` / `server_error` | 5,784.247 ms |
| 09:43:02.782 | `gemini-3.8-flash` | Short JSON connectivity check | 503 / `UNAVAILABLE` | `ServerError` / `server_error` | 5,396.210 ms |
| 09:58:09.482 | `gemini-3.8-flash` | Exact decision probe | 503 / `UNAVAILABLE` | `ServerError` / `server_error` | 3,647.500 ms |
| 09:59:36.325 | `gemini-3.7-flash` | Exact decision probe | 504 / `DEADLINE_EXCEEDED` | `ServerError` / `timeout` | 59,661.693 ms |
| 10:00:01.021 | `gemini-3.5-flash` | Exact decision probe | 503 / `UNAVAILABLE` | `ServerError` / `server_error` | 2,450.156 ms |
| 10:01:06.187 | `gemini-3.1-pro-preview` | Exact decision probe | 429 / `RESOURCE_EXHAUSTED` | `ClientError` / `rate_limited` | 427.310 ms |

For all six requests, provider usage was unavailable. Input tokens, output
tokens, and USD cost remain `null`, not zero. Latencies above come from the
measured model-call event rather than total diagnostic process duration.

Private evidence directories under `cases/stage2-diagnostics/`:

- `plan-probe-20260928T091225Z-98b2cc50`
- `503-check-20260928T094257Z-398bf0d5`
- `decision-probe-20260928T095805Z-145e5807`
- `decision-probe-20260928T095836Z-de587d50`
- `decision-probe-20260928T095958Z-dc7428a6`
- `decision-probe-20260928T100105Z-0dc344cd`

The returned status metadata does not establish the underlying cause of these
service failures. The user subsequently reported correcting the billing
account and requested another stronger-model attempt. That report authorizes
the retry; it does not prove billing caused any historical 503, 504, or 429.
The later retry and any GUI task outcome require separate measured evidence.

## Successful stronger-model decision retry

At 10:02:48.554, `gemini-3.8-flash` successfully answered the same exact decision
request. It returned `operation="duplicate_clear"` and `grid_ref="e1"`; the
diagnostic confirmed the grid reference was valid. The prompt, complete
response schema, and request fingerprint were unchanged from the failed
Flash-Lite decision. This is direct evidence that the stronger model supplied
the missing evidence in this comparison.

The single measured call took 10,117.244 ms and used 2,954 input tokens and 2,092
output tokens, comprising 87 response tokens and 2,005 thinking tokens. Its
configured-list-price estimate was USD 0.0100605. Evidence:
`cases/stage2-diagnostics/decision-probe-20260928T100238Z-efc4882c/`.

This retry followed the user's report of correcting billing. Its success does
not retroactively establish the cause of earlier provider failures. It
validates one model decision against a saved observation, not the full GUI
workflow. The actual GUI run is evaluated separately below.

## Stronger-model GUI run: save succeeded, workflow incomplete

Public harness evidence:
`agent-sawon-bench/evidence/runs/20260928T100322Z-ea1bb95a/`.
Private brain evidence:
`cases/stage2/021070b2006fbf18e822/case-a96827d73eee4ea6aec2c74ab15245c9/case/`.

This was one development run with `gemini-3.8-flash`, seed 0, stable mode, and
the new-record scenario. It cleared the original duplicate-grid obstacle,
entered and verified the shipment fields, saved, dismissed the save dialog,
and returned to the query screen. The database grader matched all 10 of 10
fields and marked the single case successful. There were zero duplicate HBL
cases, zero extra duplicate rows, and zero unauthorized transmission attempts.

The agent workflow nevertheless stopped at `search_without_verified_query`
before its final search and complete field comparison. The harness result is
`status="stopped"`, while its database task-success flag is true. These measure
different outcomes; the database result must not be described as full workflow
success.

| Measurement | Recorded value |
| --- | --- |
| Harness elapsed time | 140.935572 seconds |
| Brain elapsed time | 136.324010 seconds |
| Model calls | 25 |
| Input tokens | 99,571 |
| Output tokens, including thinking | 17,264 |
| Configured-list-price estimate | USD 0.13941825 |
| Database field matches | 10 / 10 |
| Database task successes | 1 / 1 |

### New stop: final-query protocol mismatch

The final observation, `observations/0046.json`, contained an enabled query
input with the expected HBL value, the enabled search button, and the saved
row. The model summary was not truncated. The model selected the search button
to refresh and verify the saved record; it did not choose the wrong HBL.

On save, the runtime deliberately resets `query_value_verified` and
`query_submitted` and clears the query descriptor and window identity. Only a
new `query_input` text action followed by an exact reread sets verification
true. The search guard therefore rejected the final click before execution.
The original policy required searching again after save, but did not explain
that the unchanged query value must be entered again to restore this state.
Its instruction not to repeat successful actions also made that required
transition less clear. This is a prompt/runtime protocol mismatch, separate
from Flash-Lite's earlier missing grid evidence.

The policy was subsequently clarified: before either duplicate or final
search, if `query_value_verified` is false, issue `operation=act`,
`intent=query_input`, `action.type=set_text`, and `action.source=hbl_no` on the
observed query input, even when its visible value already matches. The policy
now explains that saving resets verification and this required re-entry is
exempt from the no-repeat instruction. The guard itself was not relaxed or
changed.

### Verification attempts after the policy clarification

| Harness run | Result | Model calls | Estimated cost |
| --- | --- | --- | --- |
| `20260928T100755Z-a421d4db` | Setup failed with `PermissionError`; no workflow actions | 0 | USD 0 |
| `20260928T100849Z-24e2ef8d` | `blocked_foreground`; zero workflow steps | 1 | USD 0.002598 |
| `20260928T101009Z-28ef8a95` | `blocked_foreground` during entry, before save | 15 | USD 0.08247375 |

The 10:08 attempt performed the planning call, then stopped at the foreground
allowlist check. The user subsequently authorized a retry while keeping the
foreground quiet. That 10:10 attempt progressed through 14 brain selection
steps and 13 recorded workflow actions before `blocked_foreground` stopped it
during entry, before save. Its 15 model calls consumed 53,585 input tokens and
11,276 output tokens; harness elapsed time was 77.411616 seconds. No database
task success, duplicate row, or unauthorized transmission was recorded.

Private evidence for the 10:10 attempt:
`cases/stage2/efe69c7bd68f457996a7/case-bd58d32ef5e5427eb014ff2c7ad7f897/case/`.

None of these three attempts reached the corrected final-query transition.
At that point the patched full workflow remained unverified, and the 10:10
execution had ended at the foreground guard. The successful 10:26 run below
supersedes that pending verification status without changing these historical
outcomes.

## Additional final-evidence integration limitations

The following findings came from source inspection and the saved final-query
observation `0046.json` of the earlier 10:03 run. They were not the reached stop
condition of the 10:10 retry, which stopped at `blocked_foreground` before
save. The row issue was subsequently fixed and the viewport obstacle was
resolved by maximizing the query window in the successful 10:26 run.

### Row representation

In that observation, the result grid `e1` contains a `Custom` row container
`e8`, whose children are `DataItem` cells. The original observation summary excluded
`Custom` from both selected elements and contextual containers, so the row
itself was absent from the model's summary. The original completion guard accepted
only `DataItem`, `Row`, or `ListItem` as a row and rejected the actual
container. Selecting the HBL cell as the row could not solve this: its subtree
does not contain the sibling cells needed for the other fields.

The implemented fix is the shared `brain/observe/rows.py` helper, used by both
summary strategies, row counting, and completion validation. It recognizes
`Custom`, `Group`, or `Pane` only when directly under an observed grid and
containing a nonempty set of direct data cells without nested data-container
ambiguity. Header-only, empty, mixed, and unrelated containers are excluded.
Cells within an established row are not treated as separate rows. Both summary
strategies include the identified row and current `row_ref` context; nested
grids use their nearest row. No application-specific names or selectors are
used. The existing all-fields-in-the-same-row evidence check remains in place.

Meaningful synthetic tests cover the supported generic container roles, both
summary strategies, duplicate matching rows, cross-row evidence rejection,
cell-as-row rejection, excluded ambiguous shapes, and nested-grid context.
UIA `GridItem`/`TableItem` metadata remains a possible future enhancement and
was not required or added for this fix.

### Columns outside the viewport

The relay currently walks visible UIA elements and does not expose general
grid row/column pattern metadata. In the saved observation, a horizontal
scrollbar is present and visible cells extend only through gross weight;
container number and the two port fields are outside the viewport. The
completion guard requires all field evidence in one observation and one row.
If horizontal scrolling hides previously visible fields, the current contract
cannot combine the observations into a complete proof.

The policy now permits using an observed maximize control to check whether all
columns fit after enlarging the window. The model used that control in the
successful run, and all required columns became visible together. Where they
still do not fit, a proposed robust
extension is bounded final-evidence collection across scroll observations,
anchored to the verified query, the same grid, and an established logical row
identity. Each observation must use its own current refs, with explicit
invalidation when the query, window, grid, or row changes. A changed sort order
or ambiguous row identity must not silently reuse earlier evidence. Missing
fields cannot be accepted, and earlier editor-entry evidence cannot substitute
for rereading the saved query result. This extension remains proposed and
unimplemented.

## Final measured GUI success

The development run `20260928T102626Z-4077ad53` used `gemini-3.8-flash`, seed 0,
stable mode, and the new-record scenario. The brain returned
`status="success"`, reason `saved_and_verified_awaiting_approval`, and durable
state `awaiting_approval`. The public harness also returned success and matched
all 10 of 10 database fields. Human approval was not performed.

After maximizing the query window, the model supplied all 11 logical field
components, including the two package components, from the same current
observation, grid `e1`, and structural row `e7`. The final verification event
confirms all components matched. The package count and unit legitimately share
one cell. No multi-observation evidence collection or relaxation of the
same-observation, same-row requirement was introduced.

| Measurement | Recorded value |
| --- | --- |
| Harness elapsed time, excluding inspection hold | 145,242.369 ms |
| Brain elapsed time | 141,361.145 ms |
| Model calls | 28 |
| Recorded workflow actions | 25 |
| Observations | 52 |
| Input tokens | 122,044 |
| Output tokens, including thinking | 23,530 |
| Total measured model latency | 128,916.122 ms |
| Configured-list-price estimate | USD 0.1797705 |
| Database field matches | 10 / 10 |
| Final UI field components matched | 11 / 11 |
| Duplicate cases / extra duplicate rows | 0 / 0 |
| Unauthorized transmission attempts / foreground rejections | 0 / 0 |

The end-state screenshot was captured during a requested 15-second inspection
hold. Its measured duration was 15,766.895 ms, recorded separately from task
elapsed time. Validation after the integration changes passed all 107 private
tests and 384 public tests.

Public evidence:
`agent-sawon-bench/evidence/runs/20260928T102626Z-4077ad53/`.
Private evidence:
`cases/stage2/8f42cda75c83dc09e5b4/case-6bdf381377cf4735938de13f05e23b0f/case/`.

This establishes one successful development-seed workflow with the final
implementation. It does not establish success across the full development
matrix or reserved evaluation seeds.

## Lower-model comparison with the successful implementation

Run `20260928T103825Z-e1cd7c7a` used `gemini-3.5-flash-lite` with the same
runtime code and development conditions as the successful 10:26 upper-model
run: seed 0, stable, new record, label_grouped, one repeat, 600 seconds, 80
selection steps, USD 0.50, and a 15-second end-state inspection. Both plans
record identical source, record, variants, ERP/relay configuration, ERP binary,
brain configuration, pricing, and desktop conditions. Only the model and its
external configuration name/path differ. Main defaults remain 3.8 Flash.

Private case:
`cases/stage2/ffbd6c34c31dea36349e/case-aa278f41b3fc40529a5edc2a30b99d9c/case/`.

All nine measured model calls succeeded at the API boundary. At select event
51, the model recognized zero query results but chose navigation to the editor
without first returning `duplicate_clear` and a grid reference. At select 58,
it attempted the first field write while the runtime remained in
`duplicate_query`. Stop event 59 and result.json record
`write_before_duplicate_check`. The write was rejected before execution.
The public harness records the more general `external_stopped` reason.
There was no foreground rejection, saved record, extra duplicate row, or
unauthorized transmission attempt.

| Measurement | Flash-Lite |
| --- | --- |
| Full workflow / database success | 0 / 1 for each |
| Database field matches | 0 / 10 |
| Harness elapsed time, excluding inspection | 21,466.985 ms |
| Brain elapsed time | 17,667.033 ms |
| Model calls / actual actions / observations | 9 / 7 / 15 |
| Brain selection steps | 8 |
| Input / output tokens | 23,424 / 1,363 |
| Model latency total | 14,730.698 ms |
| Configured-list-price estimate | USD 0.0104347 |
| Inspection elapsed time | 15,402.903 ms |

The observed trigger is an omitted model protocol step, separate from API
availability or foreground loss. The controller also permits leaving the
query screen before accepting duplicate evidence and provides no recovery for
this omission; the design audit below qualifies attribution. The prior upper run completed in
145,242.369 ms at USD 0.1797705, with 28 calls and all ten saved fields matching.
The lower run's earlier stop means its shorter time and lower cost do not
establish better completion efficiency. One run per model cannot establish
general reliability. No prompt, summary, schema, or guard was changed for this
comparison; operation-specific schemas and bounded repair remain proposed.

Public numeric evidence and masked end-state screenshot:
`agent-sawon-bench/evidence/stage2/lite-comparison-20260928/`.
Private checks passed 107 tests, lint, and formatting before this execution.

## Controller design audit after the comparison

The user's question about application logic prompted a separate offline audit.
The measured GUI results above are unchanged. Attributing the failure only to
model capability was too strong: the omitted protocol step is the immediate
trigger, while the controller's sequencing and recovery design make it terminal.

- `_select_node` invokes `_check_duplicate` only when the model explicitly
  chooses a duplicate operation. The deterministic checker owns the actual
  state transition, but there is no required evidence checkpoint after search.
- `_guard_action` allows `navigate` in `duplicate_query`, including after a
  successful search. The agent can leave the only current evidence screen.
  Once it reports an editor screen, the same guard stops every action before
  execution; it does not offer a bounded route back to evidence collection.
- `Decision` makes `grid_ref` optional for all operations and accepts mixed
  evidence operations and UI actions. All `GuardStop` exceptions from selection
  become final stops, so missing evidence bypasses the configured two-failure
  feedback mechanism. Foreground and authorization failures should still stop;
  a repairable response omission needs separate classification.

Offline evidence:
`cases/stage2-diagnostics/protocol-audit-20260928T104520Z-bbeffefb/audit.json`.
It uses synthetic observations and scripted model replies, with zero live API
calls and zero actual desktop actions. Three scenarios reproduced:

1. Verified exact query, search, then navigation without `duplicate_clear`:
   navigation is accepted; the first field write stops with
   `write_before_duplicate_check`, and no record fields are written.
2. The scripted workflow including valid duplicate evidence completes.
3. A duplicate decision missing `grid_ref` stops immediately, even when the
   scripted next response would provide valid evidence. No repair feedback is
   sent. Schema validation also accepts a missing grid combined with an action.

These checks demonstrate controller behavior; they do not predict whether
Flash-Lite will succeed after a fix. Existing tests exercised successful
protocol execution and forbidden writes, but not recovery from navigation
selected after search and before evidence acceptance.

The proposed correction is an explicit post-search duplicate-evidence stage.
Keep navigation pending until a narrow response identifies the current result
grid and the existing checker validates the exact query value, query window,
and visible grid contents. Missing or malformed evidence may receive bounded
feedback within existing step and cost limits. The controller owns the phase
transition; it must not infer clearance from model prose, an arbitrary empty
grid, or historical evidence after leaving the query screen. This audit changes
diagnostic attribution only, not runtime code, guards, or previous measurements.

## Implemented checkpoint and bounded response repair

Following the user's explicit request to fix the logic and rerun Flash-Lite,
the runtime now switches to an evidence-only `DuplicateDecision` schema after
duplicate search. The common checkpoint predicate also blocks all UI actions
until current query/window/grid evidence is accepted. Missing or malformed
evidence receives bounded feedback; query identity changes remain fatal.

General decisions now reject inconsistent operation/intent/action combinations.
Targeted choices require a nonempty current ref, and text actions require a
record or credential source. Invalid responses are never sent to the relay;
the model may correct them within the existing limits. A search attempted
before query-value verification receives an explicit query_input instruction,
without executing the search or setting either verification flag.

Verified navigation to a new window counts as progress once per destination
within the phase. Same-window actions and waits do not reset the phase failure
streak. Per-subgoal failures and total step/cost caps are retained. Existing
same-observation, same-row final evidence requirements are unchanged.

Development attempts and outcomes are preserved in
`agent-sawon-bench/evidence/stage2/lite-checkpoint-fix-20260928/README.md`.
The intermediate 10:58 run saved all ten fields, recovered two malformed
responses, then stopped before final search. Later fixes address missing query
revalidation and a null field target seen in the 11:05 run. Two attempts with
the latest code at 11:09 and 11:10 stopped in desktop startup before model
execution because foreground changed. The user then deferred further screen
verification, so no additional GUI run was started. The latest full GUI result is pending;
no completed workflow is claimed from the intermediate database success.
Final local validation passed 138 private tests, including 31 new regression
cases, plus lint and formatting. All six attempted GUI run artifacts were
preserved, including startup failures and the partial database success.

## Historical hardening proposals before this implementation

`Decision` currently makes `grid_ref` and `action` optional for every operation.
Consequently, a structurally valid JSON response can still omit the evidence
required by a particular operation. Operation-specific constraints could
require grid evidence for duplicate decisions and separate evidence decisions
from actions. A bounded repair attempt could also provide precise missing-
evidence feedback before stopping, subject to the existing step, failure, and
cost limits. The guard must continue to reject unsupported duplicate claims;
an absent reference must not be silently inferred or filled in at runtime.

These are proposed reliability improvements and were not applied. No runtime
prompt, schema, summary, or guard change was applied for the initial
exact-request model comparison, preserving attribution. The later policy
clarification and structural-row fix address the distinct final-query
integration issues described above.
A single failed decision identifies the immediate cause in this case; it does
not establish a general failure rate for Flash-Lite.
