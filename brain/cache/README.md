# 화면 캐시와 업무 방법 재사용

## Memory Retrieval

`methods.MethodLibrary` stores learned purposes, input slots and observed action groups,
using current visual evidence. Enable it explicitly in a brain profile:

```toml
[methods]
enabled = true
path = "../cases/methods/reviewed-methods.json"
```

Paths resolve relative to the configuration file. The source is copied into each case's
`methods-working.json`; it is never changed in place. Reuse is scoped to the exact user-education
digest and task objective. Each fresh observation binds learned labels and label-relative geometry
to current OCR/control candidates, then generates ScreenMatch without a semantic-model call.
Existing scene binding, ActionPlan and execution handle it. Detected targets retain semantic label
geometry. A successful model-created temporary point may be compiled separately into OCR
anchor-relative geometry; raw historical pixels, input values and marker IDs are not replayed.

When current targets do not bind, the screen model plans the current action. A successful run
promotes only its delivered trace, including login, navigation, lookup, input, save and review
actions. A no-progress click is consumed for the current run. A delivered fill whose immediate
OCR reread is incomplete remains provisional and is promoted only if the whole task succeeds.
Sequential actions may share one taught step; they are not treated as interchangeable branches.

Learned fill targets also rebind visible current values as deterministic evidence. This lets a
memory-based execution accumulate proof across multiple tabs before applying its learned completion condition.
The save-result review contract remains separate from ordinary action replay. Delivery alone is
never business success, and failed runs promote neither methods nor visual decisions.

This does not establish compatibility with every ERP workflow. Generated methods and their source
traces belong in ignored private storage. See the learning CLI in
[demo_learning](../demo_learning/README.md).

`compiled_only=true` loads only methods produced by the Memory Compilation process.
Runtime trace fragments remain useful compiler evidence but cannot preempt visual-cache/model
fallback during memory reuse. A compiled method that does not bind to the current screen is suspended
for that run and the ordinary visual cache/model path continues.

## Pattern Matching

`pattern_matching.ScreenPatternCache`는 화면과 업무 상태에 연결된 ScreenMatch를 재사용합니다.
`[visual_match_cache]`의 `enabled`와 `path`로 설정합니다. 캐시 결과도 현재 화면의
대상 연결과 공통 실행 검증을 거칩니다. 전체 업무 성공 시 새 후보를 승격합니다.

구형 UIA 필드 캐시와 관찰값 변화 기반 시연 변환기는 제거됐습니다.
`[cache]`, `--cache-path`, `--cache-isolation`, `--cache-snapshot`은 지원하지 않습니다.
현재 방법 학습은 실행된 화면 입력 묶음과 그 실행 기록을 사용합니다.

단계 이름과 구현 범위는 [화면 처리 용어](../../docs/runtime-terminology.md)를 따릅니다.
