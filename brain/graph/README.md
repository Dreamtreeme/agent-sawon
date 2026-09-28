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

Live model GUI comparison and development-seed measurements are pending, deferred
at the user's request. Fake/replay and local HTTP tests do not establish real GUI
success rates or the relative performance of the two summary strategies.
