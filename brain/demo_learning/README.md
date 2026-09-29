# Demonstration conversion (M2, schema 0.1)

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
