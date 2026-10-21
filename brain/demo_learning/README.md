# 화면 실행 기록 기반 방법 학습

## Learn a method from an executed screen batch

```powershell
python -m brain.demo_learning.methods --source-case cases/source/case --case-dir cases/learning/run-001 --method-id visible-inputs --model-config config/models.toml --env-path .env --max-cost-usd 0.1 --output cases/methods/learned.json
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

## Memory Compilation

Memory Compilation refines a successful source execution into retrievable task memory.
After the source case reaches business success and its screen-pattern cache is promoted, compile
its successful path with one measured model call:

```powershell
python -m brain.demo_learning.memory_compilation --source-case cases/source/case --visual-cache cases/visual-match-cache.json --case-dir cases/memory-compilation/run-001 --method-id shipment-entry --model-config config/models.toml --env-path .env --max-cost-usd 0.1 --output cases/methods/memory.json
```

`read_memory_candidates` joins source observations and delivered actions. `assemble_memory` asks
for a `MemoryAssembly`; `compile_memory` retains the selected actions, input keys and coordinates.
The model reviews the trace and selects candidates; it does not invent actions or coordinates.
Current compilation still selects action-bearing candidates. Preserving all read-only screen
mappings is follow-up work, not an effect of the terminology change.

Pattern Matching compares the recorded viewport and pHash. Memory Retrieval binds the selected
method, and Context Recall combines it with the current task and notebook. The runtime still has
combined responsibilities; see [terminology and implementation](../../docs/runtime-terminology.md).

The persisted artifact tag remains `warm_compiled`. `methods.compiled_only=true` excludes partial
runtime traces. The compilation directory records inputs, requests, draft, cost and result.
Historical cold/warm labels describe benchmark conditions, not different runtime stages.
