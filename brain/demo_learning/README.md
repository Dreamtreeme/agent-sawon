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

## Compile a successful cold run into a warm method

Cold execution and warm replay are separate stages. After a cold case reaches business success and
its visual cache is promoted, compile the complete successful path with one measured model call:

```powershell
python -m brain.demo_learning.warm_methods --source-case cases/cold/case --visual-cache cases/visual-match-cache.json --case-dir cases/warm-compilation/run-001 --method-id shipment-entry --model-config config/models.toml --env-path .env --max-cost-usd 0.1 --output cases/methods/warm.json
```

The model selects and orders immutable cached batches; local code retains their exact actions and
input keys. Compilation requires a successful source result, covers every observed input source and
login/save intent, and rejects candidates without a reference screenshot. Successful coordinates
are reused only when viewport size and screenshot pHash match the recorded screen.
The result is marked `warm_compiled`; configure `methods.compiled_only=true` so partial cold trace
fragments are not replayed as finished warm methods. The compilation directory records its input,
request, model draft, cost, and result for review.
