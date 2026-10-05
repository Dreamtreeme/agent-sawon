# 테스트 세밀 분류 및 실행경로 감사

측정일: 2026-10-05

이 보고서는 정리 직후 846개 case를 기준으로 각 test context가 실제 실행한 `brain/`,
`models/`, `pipeline/` 줄과 JUnit 시간을 결합한 결과다. 이후 중복 전체 업무 2개를 통합해
현재 수집 수는 844개다. 실행 줄이 같다는 사실만으로 테스트를 삭제하지 않고, assertion이
보호하는 출력·예외·감사 계약을 함께 확인했다.

## 계층별 분포

| 계층 | case | 시간 | 비율 | 판단 |
|---|---:|---:|---:|---|
| L0 순수 계약 | 155 | 1.623초 | 1.0% | 많은 case가 있어도 매우 싸며 schema·fail-closed 행렬을 보호한다. |
| L1 컴포넌트 | 373 | 15.036초 | 9.3% | 주된 결함 위치를 직접 검증한다. |
| L2 전체 그래프 | 224 | 118.574초 | 73.0% | 정리의 최우선 대상이다. |
| L3 경계/프로세스 | 94 | 27.127초 | 16.7% | public CLI·HTTP·child-process 대표 경로를 남긴다. |

15개 case가 전체 162.36초 중 79.67초(49.1%)를 사용했다. 반대로 0.1초 미만
655개 case는 12.16초(7.5%)뿐이다. 따라서 case 수가 많은 빠른 계약 테스트를 줄이는 것보다
전체 그래프의 중복 준비를 제거하는 편이 효과와 안전성 모두 높다.

## 업무 영역별 분포

| 영역 | case | 시간 | 비율 |
|---|---:|---:|---:|
| 캐시 | 92 | 59.478초 | 36.6% |
| 업무 실행 | 196 | 50.051초 | 30.8% |
| 준비 인계 | 43 | 16.957초 | 10.4% |
| 시각/OCR | 101 | 13.792초 | 8.5% |
| 문서 파이프라인 | 84 | 7.095초 | 4.4% |
| 런타임 경계 | 39 | 6.677초 | 4.1% |
| 학습 방법 | 45 | 4.065초 | 2.5% |
| 상태 계약 | 119 | 2.269초 | 1.4% |
| 모델/wire | 127 | 1.976초 | 1.2% |

## 파일별 인벤토리

`고유 줄`은 해당 테스트 파일만 실행한 프로덕션 줄 수다. 0이라고 해서 가치가 없다는 뜻은
아니다. 같은 코드 경로에서 서로 다른 반환값·예외·schema 거부를 확인할 수 있기 때문이다.

| 파일 | 영역 | 주 계층 | 함수 | case | 시간(초) | 고유 줄 | 선별 판단 |
|---|---|---|---:|---:|---:|---:|---|
| `tests/test_graph_cache_rebinding.py` | 캐시 | L2 전체 그래프 | 3 | 3 | 25.856 | 2 | P1 준비 공유 |
| `tests/test_graph_cache_failure_recovery.py` | 캐시 | L2 전체 그래프 | 2 | 2 | 17.551 | 16 | P1 준비 공유 |
| `tests/test_visual_workflow.py` | 시각/OCR | L2 전체 그래프 | 3 | 6 | 11.949 | 32 | P2 하위 이동 |
| `tests/test_input_target_recovery.py` | 업무 실행 | L2 전체 그래프 | 13 | 19 | 11.261 | 34 | 통합 완료 |
| `tests/test_prepared_cache_workflow.py` | 준비 인계 | L3 경계/프로세스 | 11 | 31 | 10.185 | 30 | 보류: 계약 대조 |
| `tests/test_policy_workflow.py` | 업무 실행 | L2 전체 그래프 | 7 | 8 | 9.883 | 10 | P3 경계 분해 |
| `tests/test_shared_graph_cache_integration.py` | 캐시 | L2 전체 그래프 | 2 | 2 | 9.575 | 3 | P1 준비 공유 |
| `tests/test_navigation_progress.py` | 업무 실행 | L2 전체 그래프 | 10 | 11 | 8.042 | 12 | 대표 E2E 유지 |
| `tests/test_screen_matching.py` | 업무 실행 | L2 전체 그래프 | 60 | 85 | 7.618 | 630 | 유지: 고유 계약 |
| `tests/test_screen_batch.py` | 업무 실행 | L2 전체 그래프 | 12 | 19 | 7.213 | 231 | 유지: 고유 계약 |
| `tests/test_prepared_brain.py` | 준비 인계 | L3 경계/프로세스 | 2 | 12 | 6.772 | 2 | 통합 완료 |
| `tests/test_cache_experiment.py` | 캐시 | L3 경계/프로세스 | 18 | 31 | 4.593 | 15 | 보류: 계약 대조 |
| `tests/test_method_runtime.py` | 학습 방법 | L2 전체 그래프 | 9 | 15 | 3.592 | 414 | 유지: 고유 계약 |
| `tests/test_persistent_worker.py` | 런타임 경계 | L3 경계/프로세스 | 9 | 11 | 3.506 | 256 | 유지: 프로세스 |
| `tests/test_pipeline.py` | 문서 파이프라인 | L1 컴포넌트 | 17 | 32 | 3.363 | 84 | 보류: 계약 대조 |
| `tests/test_pipeline_measure.py` | 문서 파이프라인 | L1 컴포넌트 | 19 | 29 | 3.233 | 263 | 유지: 측정 계약 |
| `tests/test_brain.py` | 업무 실행 | L2 전체 그래프 | 23 | 37 | 2.656 | 18 | 보류: 계약 대조 |
| `tests/test_credential_progress.py` | 업무 실행 | L2 전체 그래프 | 2 | 4 | 2.210 | 1 | 보류: 계약 대조 |
| `tests/test_brain_http.py` | 런타임 경계 | L3 경계/프로세스 | 4 | 5 | 1.738 | 25 | 보류: 계약 대조 |
| `tests/test_storage.py` | 런타임 경계 | L1 컴포넌트 | 10 | 12 | 1.280 | 7 | 보류: 계약 대조 |
| `tests/test_click_refinement.py` | 업무 실행 | L2 전체 그래프 | 9 | 13 | 1.168 | 151 | 보류: 계약 대조 |
| `tests/test_semantic_cache.py` | 캐시 | L1 컴포넌트 | 13 | 26 | 1.094 | 9 | 보류: 계약 대조 |
| `tests/test_decision_policy.py` | 상태 계약 | L0 계약 | 14 | 40 | 1.066 | 4 | 보류: 계약 대조 |
| `tests/test_visual_markers.py` | 시각/OCR | L1 컴포넌트 | 30 | 40 | 0.856 | 163 | 유지: 저비용 |
| `tests/test_jev_matching.py` | 모델/wire | L1 컴포넌트 | 24 | 31 | 0.828 | 278 | 유지: 저비용 |
| `tests/test_ocr_preprocessing.py` | 시각/OCR | L1 컴포넌트 | 9 | 20 | 0.711 | 14 | 유지: 저비용 |
| `tests/test_models.py` | 모델/wire | L1 컴포넌트 | 23 | 33 | 0.645 | 101 | 유지: 저비용 |
| `tests/test_cache_rebinding.py` | 캐시 | L1 컴포넌트 | 9 | 21 | 0.483 | 5 | 유지: 저비용 |
| `tests/test_worker_context.py` | 상태 계약 | L1 컴포넌트 | 8 | 11 | 0.411 | 16 | 유지: 저비용 |
| `tests/test_pipeline_workflow.py` | 문서 파이프라인 | L3 경계/프로세스 | 4 | 4 | 0.333 | 4 | 유지: 저비용 |
| `tests/test_action_rebind.py` | 캐시 | L1 컴포넌트 | 5 | 5 | 0.300 | 103 | 유지: 저비용 |
| `tests/test_marker_contract.py` | 상태 계약 | L1 컴포넌트 | 3 | 5 | 0.299 | 23 | 유지: 저비용 |
| `tests/test_wire_contract.py` | 모델/wire | L0 계약 | 5 | 33 | 0.239 | 0 | 유지: 저비용 |
| `tests/test_saved_evidence.py` | 상태 계약 | L1 컴포넌트 | 19 | 26 | 0.239 | 358 | 유지: 저비용 |
| `tests/test_task_instruction.py` | 상태 계약 | L1 컴포넌트 | 15 | 15 | 0.215 | 57 | 유지: 저비용 |
| `tests/test_method_learning.py` | 학습 방법 | L1 컴포넌트 | 2 | 2 | 0.210 | 134 | 유지: 저비용 |
| `tests/test_openai_client.py` | 모델/wire | L1 컴포넌트 | 5 | 8 | 0.179 | 83 | 유지: 저비용 |
| `tests/test_atomic_replace_retry.py` | 런타임 경계 | L1 컴포넌트 | 5 | 11 | 0.153 | 5 | 유지: 저비용 |
| `tests/test_demo_learning.py` | 학습 방법 | L1 컴포넌트 | 7 | 18 | 0.143 | 50 | 유지: 저비용 |
| `tests/test_visual_proposal.py` | 시각/OCR | L1 컴포넌트 | 2 | 5 | 0.091 | 0 | 유지: 저비용 |
| `tests/test_pipeline_adapters.py` | 문서 파이프라인 | L1 컴포넌트 | 9 | 9 | 0.086 | 79 | 유지: 저비용 |
| `tests/test_validation_metadata.py` | 모델/wire | L0 계약 | 18 | 22 | 0.085 | 3 | 유지: 저비용 |
| `tests/test_visual_scene.py` | 시각/OCR | L0 계약 | 14 | 20 | 0.080 | 43 | 유지: 저비용 |
| `tests/test_methods.py` | 학습 방법 | L1 컴포넌트 | 7 | 7 | 0.079 | 12 | 유지: 저비용 |
| `tests/test_visual_relay_boundary.py` | 시각/OCR | L1 컴포넌트 | 2 | 2 | 0.071 | 2 | 유지: 저비용 |
| `tests/test_reconciliation.py` | 문서 파이프라인 | L0 계약 | 3 | 3 | 0.058 | 18 | 유지: 저비용 |
| `tests/test_visual_cache_demo.py` | 학습 방법 | L1 컴포넌트 | 3 | 3 | 0.041 | 18 | 유지: 저비용 |
| `tests/test_visual_rectangles.py` | 시각/OCR | L0 계약 | 3 | 4 | 0.030 | 29 | 유지: 저비용 |
| `tests/test_visual_match_cache.py` | 캐시 | L1 컴포넌트 | 2 | 2 | 0.026 | 0 | 유지: 저비용 |
| `tests/test_pipeline_evaluate.py` | 문서 파이프라인 | L0 계약 | 6 | 7 | 0.022 | 5 | 유지: 저비용 |
| `tests/test_transition_contract.py` | 상태 계약 | L0 계약 | 14 | 21 | 0.021 | 6 | 유지: 저비용 |
| `tests/test_worker_state_contract.py` | 상태 계약 | L0 계약 | 1 | 1 | 0.018 | 0 | 유지: 저비용 |
| `tests/test_visual_points.py` | 시각/OCR | L0 계약 | 3 | 4 | 0.004 | 2 | 유지: 저비용 |

## 실행경로가 같은 파라미터의 판정

- `test_wire_contract.py`의 invalid 19개는 한 실행경로를 사용하지만 19개의 서로 다른 wire
  위반을 거부한다. 총 0.122초라 유지한다.
- Windows replace 오류 코드, 예산 경계, Pydantic 설정 경계도 실행 줄은 같지만 서로 다른
  입력 class를 고정하며 모두 합쳐도 0.2초 미만이라 유지한다.
- 프로덕션 측정 대상 줄을 실행하지 않은 11개는 문서 예제 내용, Pydantic 생성 시점 거부,
  외부 schema validation을 검사한다. `coverage` 범위 밖이라는 이유로 삭제하지 않는다.

## 고비용 후보별 판정

| 후보 | 기존 비용 | 실행경로 증거 | 조치 |
|---|---:|---|---|
| query/editor 입력 재선택 성공 | 6.343초 | 두 case 모두 같은 1,819줄 | 한 업무에서 두 재선택을 모두 실행하도록 통합 완료 |
| prepared engine/CLI 성공 | 6.111초 | CLI가 동일 engine을 경유하고 engine 성공은 다수 전체 그래프가 검증 | public CLI 대표 성공만 유지하도록 통합 완료 |
| graph cache rebinding 3개 | 25.856초 | 파일 고유 줄 2개지만 모델 호출수·캐시 불변·비용 중단 assertion은 서로 다름 | 삭제 보류, cold cache 준비 공유가 다음 조치 |
| graph cache failure 2개 | 17.551초 | 실패 회복과 최종 불일치는 서로 다른 최종 계약 | 삭제 보류, cold cache 준비 공유가 다음 조치 |
| visual caret 4개 | 11.628초 | 네 case가 모두 다른 실행경로 | 성공 E2E 1개와 실패 컴포넌트 3개로 이동 가능한지 P2에서 검토 |
| prepared cache 4개 주 경로 | 7.880초 | cold/snapshot과 비용 허용/차단 네 조합이 모두 다른 경로 | 유지 |
| screen matching 85개 | 7.618초 | 파일 고유 프로덕션 줄 630개, case당 평균 0.09초 | 유지 |
| wire 33개 | 0.239초 | 파일 고유 줄 0개지만 외부 schema 조합을 독립 검증 | 유지 |

## 이번 조치 검증

- 수집 수: 846개에서 844개로 변경했다.
- query와 editor 재선택은 한 전체 업무에서 모두 발생하며 두 번 모두 relay 입력 전에 거부됨을
  확인한다.
- prepared 성공은 CLI 진입점, factory, 준비 검증, engine, 상태 및 append-only 로그를 한 번에
  검증한다.
- 전체 결과: 844 passed.
- 전체 커버리지: 87.63%, 문장 90.24%, 분기 80.14%로 정리 전과 정확히 같다.
- `brain/graph/engine.py`는 Cosmic Ray에서 3,763개 변이를 생성한다. 현재 Windows local
  distributor는 순차 실행만 하므로 이 두 테스트 단위에 대한 전체 변이 비교는 실용적인 범위를
  넘는다. 커버리지 불변과 남은 대체 경로를 조치 근거로 사용했다.

## 다음 우선순위

1. 세 graph-cache 파일이 반복하는 cold 성공 준비를 읽기 전용 fixture 결과로 공유한다.
2. visual caret 실패 3종을 전체 업무가 아닌 입력 검증 컴포넌트 경계에서 재작성할 수 있는지
   먼저 실험하고, 동일한 오류·로그 계약이 확인될 때만 E2E 파라미터를 줄인다.
3. policy/navigation 전체 그래프는 전이·review 경계에서 같은 결함을 잡는 하위 테스트가
   만들어진 경우에만 대표 E2E 외 반복을 제거한다.
4. 0.1초 미만 계약 행렬은 개수가 많다는 이유로 손대지 않는다.
