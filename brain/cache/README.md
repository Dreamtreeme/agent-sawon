# Semantic input cache (M2, schema 0.1)

`SemanticCache(path, enabled=True, max_retries=1)` creates a locked, atomic JSON
store. Keep its path under ignored private `cases/`; never publish cache contents.
Each entry stores an observed input label, role, ancestor semantics, field source,
text action template, provenance, success/failure counts and verification time.
It stores no coordinates, refs, AutomationIds, handles, input values or credentials.

Integration: `start_run()` returns a session; `lookup(observation, phase="enter",
allowed_sources=remaining_fields, record=record)` returns a current-observation
`CacheMatch` or `None`. The hit contains `entry_id`, `obs_id` and `decision`.
The caller logs `cache_hit`/`cache_miss`, then applies the same current policy and
grounded execution guards as model decisions. Fresh after-observation proof is
required even on hits. `stage_validated(before, after, decision, record)` accepts
only changed, uniquely identified record text inputs with newer observation/time.
`finish(success=True)` promotes these mappings only after the entire business
task passes its normal final verification; `finish(success=False)` discards them.

On a failed cached action/reverification call `record_failure(entry_id)`. The
default D6 proposal permits one retry; the second failure invalidates the entry.
The normal model flow can learn a replacement after a successful run. This is
configurable and does not approve D6. The default `matching_mode="exact"` treats
changed labels/context or ambiguous matches as misses.

## Opt-in current-screen rebinding

`config/brain.m2-rebind.toml` selects `matching_mode="rebind"`. Same-role, same-label
targets may tolerate intermediate container changes only with a matching named
outer scope and at least `min_anchor_fields=2` distinct unambiguous learned fields.
Conflicting field ownership, duplicate targets and invalidated descriptors are not guessed.

On a remaining miss, the graph may spend `rebind_model_max_calls=1` measured calls
per case to propose several changed-label mappings together. The prompt contains
cached field meanings and current editable descriptors, without record values or
coordinates. Unknown refs, stale observations, role conflicts, repeated sources
or targets and targets already claimed by anchors invalidate the proposal.
An empty or invalid known-cost response falls back to ordinary decisions. Unknown
cost or exhausted budget still stops the run through the shared budget guard.

Rebound entries are run-local and resolved against fresh observations before each
action. Failure counters invalidate the local proposal without rewriting the
original learned mapping. Only observed input verification and whole-task success
promote the new descriptor. `cache_hit.match_mode` distinguishes `exact`,
`label_anchor`, and `model_rebind`; the latter's mapping call remains in model costs.
This does not claim arbitrary-layout generalization: insufficient common anchors
or a changed outer scope uses the ordinary model path. No UI label aliases are hardcoded.

Version 0.1 intentionally covers editor record text inputs only. Navigation,
querying, credentials, login, save, approval and final completion remain uncached.
This is a bounded semantic cache, not a claim to implement every L2C recipe type.

## Isolated development comparisons

The ordinary M2 configuration uses a shared cache. Its measurements describe
that running system; they do not establish an isolated causal cache effect.
Failures can change counters or invalidate entries before a trial succeeds.

For independent cold starts, add `--cache-isolation cold` to `brain.run` with
cache enabled. For fixed warmed comparisons, use `--cache-isolation snapshot
--cache-snapshot <private-frozen-cache.json>`. The source snapshot is validated,
read once and never modified. A proposal bundle is not a usable cache snapshot.
The `config/harness_agents.cache-isolated.json` example defines cold, snapshot,
and cache-disabled agents with matching models, recovery and limits. Select only
the desired pair. Snapshot mode requires its explicitly configured private file.

Each new case records `cache-experiment/initial.json`, mutable `working.json`,
and atomic `provenance.json` with source/initial/final hashes and execution outcome.
Changed or missing initial evidence makes provenance incomplete. The no-cache
agent omits these flags and performs no cache I/O. Existing or `--prepared-case`
executions cannot be reset through isolation flags. Pipeline-prepared cases need
separate orchestration before using these comparison modes.

Freeze the training snapshot before measuring each replicate, record training
cost separately, and use a fresh case per attempt. Learning across repeats is a
different experiment and is not orchestrated here. Preserve cancelled runs in
success denominators; compare efficiency among comparable completed outcomes.
These controls prepare the next GUI comparison; their fake tests do not measure
live cache recovery or demonstrate an efficiency gain.
