# Worker runtime

`python -m brain.run` loads the record and user task, then runs the four-node
`Observation → Decision → Review → Execution` graph. Screen matching requires
`--task`/`task_path`; `TaskInstruction.procedure` preserves user-taught steps.
There is no separate legacy Procedure loader.

## Runtime responsibilities

Scanning (`ScreenScanner.scan`) observes the screen. Pattern Matching searches stored screen/task
patterns. Memory Retrieval (`MemoryRetrieval.retrieve`) supplies bound methods. Context Recall
(`recall_context`) combines observations, memory, education and current task progress. These names
are responsibilities within the existing worker graph; callback order and eligibility are unchanged.
See [the terminology guide](../../docs/runtime-terminology.md) for the remaining coupling.

## Ownership and flow

- `WorkerState` owns observations, proposals, pending execution, review flags,
  lifecycle, progress/evidence histories and method-learning state. The four nodes
  update the same run-local state; clients, credentials, configuration and file stores remain outside.
  The notebook and model context are projections of these facts.
- Observation obtains evidence through relay HTTP and joins pending delivery to
  its subsequent screen. Local OCR/control detection supplies the marker catalog.
- Decision obtains a proposal from a learned method, visual-match cache or measured
  model. All three pass through the same task/input normalization, current-marker
  grounding and plan validation. Action and evidence targets must both be bound.
  Current evidence is recorded before the execution review gate. Missing semantic
  definitions are repaired without silently deleting current evidence.
- Review owns the configured evidence policy and the one-use execution permission.
  `visual_model_only_review` delegates semantic business evidence to the model;
  other profiles retain deterministic field/save/query checks. No mode treats a
  successful relay response alone as business completion.
- Execution consumes that permission, prepares policy-checked actions, then calls `execute_plan` with an
  `ExecutionRequest`, relay and log. The transport returns an `ExecutionResult`;
  it never receives Brain. The worker applies the acknowledged prefix once.
  Sequential actions renew captures lazily; direct fill batches share one capture.
  Delivery is not proof of input correctness, authentication or persistence.

`MemoryRetrieval` receives explicit task/input context, `WorkerState.methods`, and
library/output services. It proposes a `ScreenMatch`; it cannot dispatch actions.
Methods stay within the education hash and rebind their targets to the current
screen. Only successful runs promote learned candidates. An unchanged reuse keeps
its reuse provenance; an adapted plan enters the normal learning path.

## Contracts and retained behavior

`ScreenMatch` and `ScreenPlan` share their action/evidence fields; only ScreenMatch
adds semantic controls and the image-request flag. Serialized field names remain
compatible. Generic task mode exposes exactly one `ActionCapability` in the model
state as `capability`; it contains the current input scope, allowed actions, goal and
completion condition. `TaskInstruction.procedure` remains user education, while the
serialized `procedure_step` field remains only for response/cache compatibility.

The graph still supports non-screen-matching policy phases. Those paths were not
removed by the task-only visual refactor. Same-image catalog reuse and separately
configured observation/decision calls also remain; this refactor does not prove a
model's UI interpretation correct or establish live ERP success.

All environment access uses authenticated relay HTTP. Model calls pass through the
measured ModelClient and existing cost budgets. Secrets resolve only when wiring a
relay action and are redacted from action logs. Logs remain append-only, status
writes atomic, and success leaves the case awaiting human approval.

The current ownership map is [the contract guide](../../docs/contracts.md). Historical validation
is preserved in the [2026-10-08 runtime refactor](../../docs/archive/reports/refactor-runtime-stages-20261008.md)
and [residual contract cleanup](../../docs/archive/reports/runtime-residual-fixes-20261008.md) reports.
