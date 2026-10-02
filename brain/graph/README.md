# Reasoning graph (M1 stage 2)

`python -m brain.run` runs a new private case. The LangGraph nodes are plan,
observe, select, act, and reread/verify. Phase gates enforce duplicate query before
entry, verified field values before save, and an observed final query row before
success. Success changes case status from `entering` to `awaiting_approval`;
the brain never approves or transmits a record.

All environment access uses authenticated relay HTTP. No benchmark implementation,
database, exported layout, app label, AutomationId, or fixed coordinate is used.
The model maps observed semantics to the public shipment field contract. Ref
targets are valid for the current observation only; rereads rematch descriptors
containing role, observed name/label and ancestor context.

The default limits in `config/brain.toml` are 80 selection steps, two failures per
deterministic phase/field subgoal, and USD 0.50 per case. Before each measured call,
the remaining budget must cover at least USD 0.025 or a greater configured-price
reservation. The latter uses prompt/schema UTF-8 byte counts, an extra 1024-token
envelope allowance and the configured output-token maximum. This conservative
text-token estimate is logged as a budgeting assumption, not provider billing.
Any unmeasured or unknown-cost failed call stops the case. Provider retries remain
disabled in the model layer.

Renaming a model subgoal does not reset failures. A second failed action without
new grounded field/query progress also stops the phase when the model rotates
intent or field names. Successful waits/navigation are not proof of progress.
The query identifier is reread before search and final evidence checks; final
success requires exactly one matching result row with all expected field values.

The model receives a short trusted task object and a separately delimited
untrusted UI summary. Raw password values never enter prompts, snapshots or action
logs; an opaque credential source resolves only into the relay request. Password
ValuePattern rereads are deliberately opaque; successful subsequent navigation
is the credential check. Ordinary text input must exactly match a fresh reread.

The private case contains append-only `run_log.jsonl`, redacted observations,
the record, atomic `status.json`, and `result.json`. A case directory cannot be
reused by the CLI. A stopped execution remains distinct from a failed execution
in `result.json`; both transition the existing case-state contract to `failed`.

Required arguments: `--record`, `--case-dir`, `--relay-url`, `--goal`, `--config`,
`--model-config`. Gemini also requires explicit `--env-path`; optional
`--summary-strategy` selects either supported strategy. Environment variables
`SAWON_RELAY_TOKEN`, `SAWON_APP_USERNAME`, `SAWON_APP_PASSWORD` carry session
credentials. Database/layout/export paths are intentionally absent.

Model reasoning can still misinterpret a UI control. The deterministic checks
prevent ungrounded completion, stale target reuse, unchecked text, duplicate entry
and missing-field saves; they are not a semantic proof of arbitrary button intent.
The application's agent role remains the enforcement boundary for transmission.

Current execution architecture: [2026-09-30 review](../../docs/architecture-review-20260930.md).
Selection prepares an `ActionPlan`; single model/cache actions, login and field batches
all use `execution.execute_plan` through the `act` node. The `verify` node owns the
subsequent observation. Domain-specific verifiers share field comparison, semantic
rebinding and transition logging. A partial batch retains delivery records without
claiming unobserved values. Group plans are not promoted as single-field cache entries.

The Gemini013 visual pilot completed save and final verification on seed0.
The latest Luna004 reached the query screen but failed on an invalid parent relation.
See the bench progress log; a single successful pilot does not establish general success rates.

Save delivery now enters `save_result`. The next measured selection jointly returns
current evidence, rejected/confirmation_required/ready_for_query/unknown, and an
eligible next action. Rejection returns to entry and invalidates only implicated
record fields; confirmation remains pending. Neither delivery nor readiness for
query proves persistence. Final row verification remains mandatory. Flat record
inputs use the shared form-scope predicate, including cache and evidence paths.

Current visual profiles use `screen_matching`: the supplied procedure, runtime
notebook and current OCR/marker JSON produce one `ScreenMatch` response. Local code
binds visible marker IDs to the current scene and supplies record values to the
shared executor. Post-verification policy checks remain mandatory. The superseded
number-only selector and `VisualTurn` model path have been removed; shared proposal
binding and adaptation remain. Translation-only capture renewal preserves
image-relative coordinates while binding the new capture origin. Scale/content
changes do not.

For a novel screenshot, `ScreenMatch` combines grounded screen semantics and the
conditional next action in one image-model call. The proposal is consumed only for
that observation and only after the pending action is verified. Exact-image catalog
reuse keeps only grounded semantics and requests a fresh `ScreenPlan` against the
latest notebook. A valid interpretation paired with an invalid procedure action gets
one bounded action-only correction; marker and semantic errors stay in the visual
grounding correction path.
