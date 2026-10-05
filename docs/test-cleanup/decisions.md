# 테스트 정리 판단 기록

이 문서는 테스트 후보의 분류, 대체 검증, 조치와 측정 결과를 기록한다. 프로덕션 코드는
변경하지 않는다. 사용자가 로컬 도구 설치를 승인한 뒤 정리 전후 커버리지와 변경 모듈의
변이 점수를 같은 명령으로 측정했다. 삭제는 코드상 대체 검증을 특정할 수 있는 중복에만
적용했다.

## 단위 1: 의미 캐시 전체 그래프

### 삭제

- `tests/test_shared_graph_cache_integration.py::test_cache_invalidates_before_falling_back_to_model`
  - 분류: 중복 테스트.
  - 기존 검증: 캐시 입력 불일치 두 번, 두 번째 실패에서 무효화, 모델 fallback 후 전체 성공.
  - 남은 대체 검증:
    `tests/test_graph_cache_failure_recovery.py::test_temporary_failure_invalidates_locally_then_recovers_on_fresh_model_target`가
    같은 전체 그래프에서 실패 횟수별 무효화 순서, label-anchor와 model-rebind 양쪽,
    새 observation/ref 사용, 모델 fallback, 원본 캐시 불변, 최종 전체 성공까지 더 강하게 검증한다.

### 통합

- `tests/test_graph_cache_rebinding.py::test_empty_or_invalid_proposal_falls_back_without_unmeasured_retry`
  - 분류: 입력값만 다른 반복 준비.
  - 조치: empty, unknown ref, invalid schema 세 변형은 모두 유지하고 cold 캐시 준비 한 번을
    공유한다. 각 변형 전에 cold bytes를 복원하고 서로 다른 case 디렉터리를 사용한다.
- `tests/test_graph_cache_failure_recovery.py::test_temporary_failure_invalidates_locally_then_recovers_on_fresh_model_target`
  - 분류: 입력값만 다른 반복 준비.
  - 조치: label-anchor 즉시 무효화와 model-rebind 1회 재시도 시나리오를 모두 유지하고 cold
    캐시 준비 한 번을 공유한다. 각 시나리오 전에 원본 캐시 bytes를 복원한다.

### 검증과 효과

- 관련 테스트: 변경 전 11개, 변경 후 7개. 실제 제안 변형과 실패 모드는 모두 유지한다.
- 관련 파일 평균: 기준선 72.368초, 변경 후 56.740초로 15.628초 감소했다.
- 전체: 851 passed, JUnit 178.242초, pytest 180.41초.
- 기준선 전체 평균 190.44초보다 10.03초 빠르다. 기준선 최저 177.42초와 겹치므로 전체
  시간만으로 효과를 과장하지 않고, 관련 파일의 합산 시간 감소를 직접 근거로 사용한다.
- 기준선 대비 커버리지 하락 여부: 정리 전후 전체 커버리지가 모두 87.63%로 동일했다.
  삭제된 동작은 위의 더 강한 전체 그래프 테스트가 계속 검증하며, 통합한 모든 입력 변형도
  그대로 실행된다.

## 단위 2: 화면 오류 기록과 탐색 진행

### 하위 계층으로 이동

- `tests/test_visual_workflow.py::test_relay_diagnostic_records_only_known_codes`
  - 분류: E2E 계층에서 순수 allowlist 세부 규칙을 세 번 반복 검증.
  - 조치: 전체 알려진 오류 코드 14개와 비신뢰 문자열 거부를 빠른 단위 테스트에서 검증한다.
    전체 그래프에는 비신뢰 relay 오류가 원문을 로그에 남기지 않는 대표 경로 한 개를 유지한다.
  - 남은 검증: `test_relay_error_code_allowlist_rejects_untrusted_text`와
    `test_relay_diagnostic_redacts_untrusted_error_text`.
- `tests/test_navigation_progress.py::test_semantic_change_in_same_window_accepts_navigation`
  - 분류: `navigation_state`의 여섯 입력 변형을 전체 업무 그래프에서 반복 검증.
  - 조치: 값, 이름, 라벨, enabled, 계층, 새 콘텐츠 여섯 변형은 `navigation_state` 단위에서
    모두 검증하고, 새 콘텐츠 대표 변형 하나만 전체 그래프에서 진행으로 받아들이는지 확인한다.
  - 남은 검증: `test_navigation_state_tracks_each_same_window_semantic_change`와 대표 E2E
    `test_semantic_change_in_same_window_accepts_navigation`. 새 창 변화는 별도 E2E를 그대로 유지한다.

### 검증과 효과

- 관련 테스트: 변경 전 22개, 변경 후 17개. 검증하던 오류 코드와 의미 변경 종류는 줄지 않았다.
- 관련 파일 평균: 기준선 24.695초, 변경 후 18.310초로 6.385초 감소했다.
- 전체: 846 passed, JUnit 147.504초, pytest 149.75초.
- 기준선 평균보다 40.69초, 단위 1 실행보다 30.66초 빠르다. 전체 시간에는 실행 간 변동이
  있으므로 관련 파일의 직접 감소를 주 근거로 삼는다.
- 기준선 대비 커버리지 하락 여부: 정리 전후 전체·문장·분기 커버리지와 실행/누락 개수가
  모두 동일해 하락은 0.00%p였다. 모든 분기 입력은 단위 계층에 남고 각 경계의 대표 전체
  그래프 경로도 유지된다.
- 변경 모듈 변이 점수: `brain/observe/progress.py`의 동일한 20개 변이에 대해 정리 전
  `tests/test_navigation_progress.py`는 0개를 탐지했고, 정리 후는 13개를 탐지했다. 변이 점수는
  0%에서 65%로 상승했다. 여섯 의미 변화 검증을 전체 그래프의 반복 입력에서 순수 상태 계약
  테스트로 옮긴 조치가 실제 조건식 결함을 탐지한다.

## 단위 3: 고비용 전체 업무 중복

### 통합

- `tests/test_input_target_recovery.py::test_rejected_query_or_editor_target_can_recover_to_full_success`
  - 분류: query와 editor 두 case가 각각 전체 업무를 실행하지만 같은 1,819개 프로덕션 줄과
    같은 assertion 구조를 사용했다.
  - 조치: 한 전체 업무 안에서 query와 editor의 잘못된 입력 대상을 차례로 제시하고, 두 번
    모두 relay dispatch 전에 거부된 뒤 최종 성공하는지 검증한다.
- `tests/test_prepared_brain.py::test_prepared_case_finishes_and_appends_to_existing_log`
  - 분류: engine과 CLI 성공 변형 중 CLI가 동일 engine을 통과하고 factory, argument parsing,
    상태 파일까지 더 넓게 검증한다. 직접 engine 성공은 다른 전체 그래프 테스트에도 남아 있다.
  - 조치: public CLI 성공 변형을 대표 경로로 유지하고 직접 engine 성공 변형을 제거했다.
    prepared 거부 경계는 engine/CLI 10개 조합을 그대로 유지한다.

### 검증과 효과

- 관련 case: 31개에서 29개로 감소했지만 query/editor 재선택과 engine/CLI 거부 계약은 모두
  남아 있다.
- 관련 파일 직접 시간: 18.033초에서 12.506초로 5.527초(30.65%) 감소했다.
- 전체: 844 passed, JUnit 160.947초, pytest 163.48초.
- 커버리지: 전체 87.63%, 문장 90.24%, 분기 80.14%로 이전 결과와 동일하다.
- 변이 실행 가능성: 대상 engine 파일은 Cosmic Ray에서 3,763개 변이를 생성한다. Windows
  local distributor가 순차 실행만 지원해 이 단위에서 전체 변이 비교는 실행하지 않았다.
