# 화면 캐시와 업무 방법 재사용

## Purpose-based learned methods (2026-10-03)

`methods.MethodLibrary` stores learned purposes, input slots and observed action groups,
using current visual evidence. Enable it explicitly in a brain profile:

```toml
[methods]
enabled = true
path = "../cases/methods/reviewed-methods.json"
```

Paths resolve relative to the configuration file. The source is copied into each case's
`methods-working.json`; it is never changed in place. A preceding model decision selects a
method and binds its slots to the task's current input keys. The next observation binds
learned labels and label-relative geometry to current OCR/control candidates and generates
ScreenMatch without a model call. Existing scene binding, ActionPlan and execution handle it.
No absolute historical coordinates, input values or marker IDs are execution targets.

When current targets do not bind, the normal screen model plans the current action. Delivered
business fill batches become local method variants and appear in `method-progress.json`.
Delivery is not proof of business completion. Inspect the run and its video before selecting
that working file as the next run's source. Interrupted batches are not retained as variants.

The first supported runtime family is a visible business-input batch. Navigation, lookup,
login, save and final result decisions still use the model. This does not implement a general
branching recipe or establish compatibility with every ERP workflow. Generated methods and
their source traces belong in ignored private storage. See the learning CLI in
[demo_learning](../demo_learning/README.md).

## 화면 매칭 캐시

`visual_match.VisualMatchCache`는 화면과 업무 상태에 연결된 ScreenMatch를 재사용합니다.
`[visual_match_cache]`의 `enabled`와 `path`로 설정합니다. 캐시 결과도 현재 화면의
대상 연결과 공통 실행 검증을 거칩니다. 전체 업무 성공 시 새 후보를 승격합니다.

구형 UIA 필드 캐시와 관찰값 변화 기반 시연 변환기는 제거됐습니다.
`[cache]`, `--cache-path`, `--cache-isolation`, `--cache-snapshot`은 지원하지 않습니다.
현재 방법 학습은 실행된 화면 입력 묶음과 그 실행 기록을 사용합니다.
