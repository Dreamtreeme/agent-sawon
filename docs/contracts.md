# 상태와 저장소 계약 지도

기준일: 2026-10-21

이 문서는 런타임 상태와 계층 간 인터페이스의 기준 위치를 한 곳에 모읍니다. 설명과 구현이
다르면 해당 계약을 소유한 코드와 이 문서를 같은 변경에서 맞춥니다.

## 계약 소유권

단계 명칭과 실제 구현 범위는 [화면 처리 용어](runtime-terminology.md)를 따른다.
Python 모듈 이름 변경은 `WorkerState` 키나 공개 실행 계약의 변경을 뜻하지 않는다.

| 계약 | 소유 위치 | 소비자 | 책임 |
|---|---|---|---|
| `WorkerState`와 하위 상태 | `brain/graph/state.py` | `brain/graph/workflow.py`, `engine.py`, graph 노드 | 한 실행 안의 관찰·판단·검토·실행·진행 상태 |
| 그래프 라우팅 | `brain/graph/workflow.py` | LangGraph | `lifecycle.route`를 다음 책임 노드 또는 `finish`로 연결 |
| `ExecutionRequest`/`ExecutionResult` | `brain/graph/execution.py` | `engine.py`, relay adapter | 준비된 행동 전달과 relay가 확인한 결과. 업무 성공을 판정하지 않음 |
| 화면 의미 매핑 | `brain/graph/screen_mapping.py` | `context_recall`, `memory_retrieval` | 의미 이름·bbox·입력 key·행동 point의 모델 경계 |
| 선택 결과의 요청 문맥 | `brain/graph/context_recall.py`의 `RecallSelection.prepared` | `recall_context` → `ground_context_response` | 같은 판단에서 확장한 교육 상세를 빈 행동 보완에 전달. 거절 후 재계획은 기존 prepared 사용. 턴 간 영구 상태가 아님 |
| 기억 편찬 응답 | `brain/demo_learning/memory_compilation.py`의 `MemoryAssembly` / `MemoryGroupSelection` | 모델 클라이언트, `compile_memory` | 후보 ID로 성공 행동을 선택. context_anchors는 타입 검증 후 첫 8개를 보존하고 초과분 경고 기록 |
| 인출된 행동과 저장 결과 | `brain/cache/methods.py`의 `MethodActionGroup` | `method_from_match`, `compile_memory`, `MemoryRetrieval`, `context_recall` | 행동 좌표와 함께 저장 결과 분류·메시지 대상 이름·수정 필드를 보존하고 현재 화면 참조로 결합 |
| 시각 공용 자료형 | `sawon_runtime/visual.py` | OCR·탐지·pipeline | 렌더 페이지와 OCR block 계약 |
| 공개 record/action/observation/event | `../agent-sawon-bench/interfaces/sawon_interfaces/contracts.py` | core와 bench | 두 저장소가 공유하는 버전 있는 Pydantic 계약 |
| 공개 스키마 export | `../agent-sawon-bench/interfaces/sawon_interfaces/export_schemas.py` | 외부 소비자·검사 | `sawon_interfaces` JSON Schema 생성 |
| 실행·웜 재현 측정 | `../agent-sawon-bench/harness/reproduction.py` | bench metrics, runner, summary | 신규 결과에 실행 상태 3값과 호출·캐시·메서드 원시 집계를 추가. 기존 결과 필드는 유지하며 과거 기록은 재판정하지 않음 |

core가 bench 구현에서 직접 import할 수 있는 것은 `sawon_interfaces`뿐입니다. 화면 캡처, 입력,
창 관리는 relay HTTP 경계 뒤에 둡니다. `WorkerState`에는 SDK client, relay, 열린 파일 같은
서비스 객체를 넣지 않습니다.

## 상태 수명

```mermaid
flowchart LR
    O[ObservationState] --> D[DecisionState]
    D --> R[ReviewState]
    R --> E[ExecutionState]
    E --> O
    P[ProgressState] --- O
    P --- D
    P --- R
    P --- E
    L[LifecycleState.route] --> O
    L --> D
    L --> R
    L --> E
```

노드는 같은 실행의 `WorkerState`를 받아 자기 책임 영역과 `lifecycle.route`를 갱신하고 같은
상태를 반환합니다. `ExecutionResult`의 전달 성공은 업무 완료가 아닙니다. 후속 관찰과 검토가
입력 효과, 저장 결과와 사용자 완료 조건을 확인합니다.

## 계약 변경 절차

1. 계약 소유 파일에서 필드, 의미와 수명을 먼저 수정합니다.
2. 모든 생산자와 소비자를 저장소 전체에서 검색해 함께 수정합니다.
3. `sawon_interfaces` 변경이면 bench의 계약, export schema, core editable 설치 소비자를 함께
   검사하고 schema version 변경 필요성을 판단합니다.
4. `WorkerState` 변경이면 `create_worker_state`, 네 그래프 노드, fake/replay fixture와 상태
   직렬화 소비자를 확인합니다.
5. 실행 계약 변경이면 relay 전달 성공과 업무 완료가 섞이지 않는지 검증합니다.
6. Pydantic 계약의 JSON Schema가 외부 응답 형식에 영향을 주면 fixture를 의도적으로 갱신하고
   차이를 검토합니다.
7. 이 문서와 [현행 런타임 기준](current-runtime.md)을 같은 변경에서 갱신합니다.

서비스에 전체 `Brain`을 넘기거나 같은 상태를 별도 객체에 반복 복사해 새 계약처럼 만들지
않습니다. 이미 존재하는 공용 계약을 확장할 수 있는지 먼저 확인합니다.

## 인출된 증거와 저장 결과

`MethodLibrary.bind_visible_value_evidence`는 현재 OCR에서 입력값이 일치한 경우에만 증거를
만듭니다. 이때 증거 대상도 행동 대상과 동일한 `screen.markers` / `selected_fields`에 등록합니다.
현재 프레임의 같은 bbox·role 대상은 같은 ID를 사용하며, 다른 그룹의 새 대상에는 새 ID를
배정합니다. 이 ID는 현재 장면 결합용이며 메서드의 영구 업무 key가 아닙니다.
이후 `ground_meanings` → `bind_scene` → `_remember_model_task_evidence`의 기존 경로가 메모를
갱신합니다. 전달 성공만으로 입력이나 저장 완료를 메모에 추가하지 않습니다.

`MethodActionGroup`의 `save_result`, `message_targets`, `repair_fields`는 모델 판단의 저장 결과를
보존합니다. `message_targets`에는 `targets`의 이름을 저장하고, 해당 대상의 bbox와
`anchor_texts`에 메시지 위치와 텍스트를 보관합니다. 입력값 부분은 입력 key 자리표시자로
바꿉니다. 생산자는 `method_from_match`이며 런타임 학습과 편찬 CLI가 이 함수를 공유합니다.

소비자는 `MethodLibrary.bind_with_context`입니다. 기존 viewport·pHash 조건을 통과한 뒤 현재
메시지를 입력값이 치환된 텍스트와 정규화 비교하여 저장 결과를 재결합합니다.
`context_recall`은 재결합된 결과와 메시지가 있는 메서드를 `save_result` 단계에서도 선택합니다.
`compile_match`와 `SaveReviewResponse`의 기존 변환·검증은 유지합니다. 이 결과는 다음 행동을
위한 저장 화면 해석이며 작업 전체 성공 선언이 아닙니다.

새 필드가 없는 이전 메서드는 기본값 `None` / 빈 목록으로 읽습니다. 메시지가 없거나 현재
내용이 다르면 저장 결과를 재사용하지 않고 기존 화면 캐시·모델 판단 경로로 넘깁니다.
이전 산출물에 없던 메시지는 자동 복원하지 않으므로 새 저장 결과 재사용에는 재편찬이 필요합니다.
이 저장 결과 전달은 공개 인터페이스와 행동 응답의 필드·제약을 바꾸지 않습니다.
편찬 응답 타입의 이름 변경은 [화면 처리 용어](runtime-terminology.md)에 별도로 기록합니다.

기억 편찬의 앵커 절단 경고는 logging과 편찬 실행의 RunLog verify 이벤트
`warm_context_anchors_truncated`에 남긴다. 이벤트에는 group_index, retained, discarded를
기록하고 앵커 원문은 넣지 않는다. 앵커 리스트의 원래 순서를 유지하며, 후보 ID·행동·좌표·
입력 출처 검증 및 다른 응답 필드의 제한은 이 경고 정책으로 완화하지 않는다.

편찬 전체 설명인 `MemoryAssembly.purpose`는 1–300자를 허용하며 `LearnedMethod.purpose`에
원문을 보존합니다. 300자를 넘으면 파싱 단계에서 거절합니다. 그룹별 설명인
`MemoryGroupSelection.purpose`의 160자 제한과 `expected_result`의 240자 제한은 별도입니다.

## 거절 진단 산출물

거절 기록은 판단 계약이 아니라 비공개 진단 산출물이다. `sawon_runtime/storage.py`의
`record_rejection`은 기존 `verify` 이벤트에 검증 경계·예외 발생 위치·산출물 경로를 추가한다.
`case/diagnostics/*.json`에는 거절 이유, 해당 경계가 받은 응답, 문맥과 예외 위치를 저장한다.
이 응답은 이미 정규화된 객체일 수 있으므로 공급자 원문과 구분한다.
공급자 원문은 `models/base.py`가 `diagnostics/model-<call_id>.json`에 보관하며,
`model_call` 이벤트의 `call_id`, `context`, `response_artifact`로 추적한다.

화면 비교 거절은 `brain/visual/scanning.py`가 실제 비교한 두 캡처와 pHash·거리·기준을
기록한다. bench의 `relay/service.py`와 `relay/api.py`는 relay artifact 디렉터리의
`diagnostics/<id>/rejection.json`에 요청과 이유를 기록한다. relay는 현재 pHash를 판정에
사용하지 않으므로 값은 null과 미사용 사유로 표시한다. HTTP 오류 응답에는 원문을 노출하지 않는다.
진단 파일·이벤트 저장 실패는 기존 거절·재시도·종료 결과를 변경하지 않는다. 다만 기존 필수
비용 로그의 저장 실패 정책은 그대로다. 이 산출물은 Git에 포함하지 않는다.

## 신규 실행 측정 계약

bench 외부 에이전트 설정의 `execution_phase`로 cold/warm/unknown을 명시한다.
신규 trial 결과의 `execution_outcomes`는 ERP 채점, 작업자 정상 성공 종료, 웜 성공,
웜 재현을 분리하며 `true`, `false`, `unknown` 문자열로 저장한다. 화면 캐시 허용·비허용
재현값을 모두 보존하고 공식 정책을 임의로 선택하지 않는다. `execution_trace`에는 purpose별
호출, 호출 분류, 캐시 선택, origin별 메서드 중단·전달 수를 남긴다.
검토 호출은 모델 개입으로 집계하고 문서 추출·로컬 탐지는 별도로 센다.
정의와 생산자·소비자는 bench `harness/README.md`, `reproduction.py`, `metrics.py`,
`runner.py`, `run.py`, 회귀 검증은 `tests/test_reproduction.py`가 소유한다.
이 측정은 종료 후 하네스 집계이며 작업자의 행동·종료 판정이나 공개 RunEvent 계약은 바꾸지 않는다.
