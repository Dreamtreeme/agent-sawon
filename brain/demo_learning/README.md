# Demonstration conversion (M2, schema 0.1)

## Learn a method from an executed screen batch

```powershell
python -m brain.demo_learning.methods --source-case cases/source/case --case-dir cases/learning/run-001 --method-id visible-inputs --model-config config/models.luna.toml --env-path .env --max-cost-usd 0.1 --output cases/methods/learned.json
```

This opt-in path joins recorded ScreenMatch actions to delivered action IDs and actual before/
after observations. It selects a fully delivered business-input batch and asks the measured
model to extract purpose and slot meanings once. Code retains the original target/label
relationships and parameterizes values; it does not ask the model to invent a successful
execution. Partial delivery and credential batches are excluded.

The learning case records the trace, draft and model cost. `method_from_draft` can rebuild
the same extraction after a compiler change without another paid request. Inspect source
screens, execution outcome and the generated method before configuring it for reuse. This
learner currently extracts one visible input batch, not every branch of the source workflow.
See [method runtime configuration](../cache/README.md).

## Earlier observation-only converter

The public relay recorder writes one bare `Observation` JSON value per line,
with a unique ID and strictly increasing timestamp. Conversion reads only that
public contract and a `ShipmentRecord`; it never reads ERP internals or sends
actions. It matches focused changes to exactly one source field and requires a
third observation to corroborate the new value. Missing/ambiguous, protected,
unfocused, stale and transient changes are omitted. Reordered fields and fresh
refs are matched through observed semantic label/context.

```powershell
python -m brain.demo_learning --observations cases/demo/observations.jsonl --record cases/demo/record.json --output cases/demo/proposals.json
```

The output is explicitly proposed and contains semantic input entries and a
recording fingerprint; no input values, refs, handles or coordinates. Adding
`--install-cache cases/cache/semantic.json` explicitly seeds only the validated
inputs after rechecking the recording. Arbitrary proposal JSON is not executable
and cannot be imported directly. Future cache uses require the normal policy,
fresh lookup and value verification; this neither approves a shipment nor D6.

Only text-input intent can be proved by value agreement. This converter does not
infer or replay login, navigation, search, save or approval actions. Keep raw
recordings and generated cache/proposals in ignored private storage.
