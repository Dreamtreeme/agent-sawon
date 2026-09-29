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
