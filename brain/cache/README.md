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
configurable and does not approve D6. Changed labels/context or ambiguous matches
are cache misses; no fuzzy guessing or coordinate replay is used.

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
