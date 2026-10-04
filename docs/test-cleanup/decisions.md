# 테스트 정리 판단 기록

이 문서는 테스트 후보의 분류, 대체 검증, 조치와 측정 결과를 기록한다. 프로덕션 코드는
변경하지 않는다. 커버리지와 변이 점수는 로컬 도구가 없고 외부 네트워크 설치가 금지되어
있어 측정하지 못한다. 따라서 삭제는 코드상 대체 검증을 특정할 수 있는 중복에만 적용한다.

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
- 기준선 대비 커버리지 하락 여부: 측정 불가. 삭제된 동작은 위의 더 강한 전체 그래프 테스트가
  계속 검증하며, 통합한 모든 입력 변형도 그대로 실행된다.

## 보류

후보 전수 검토가 끝날 때 삭제 근거가 충분하지 않은 항목을 여기에 추가한다.
