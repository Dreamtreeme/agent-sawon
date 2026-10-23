# 남은 정리 계획의 순차 실행 결과

사용자 지시에 따라 남은 1~5번을 순서대로 조사·조치·검증했다.
판정 기준이나 범위를 바꿀 필요는 없었으므로 계획 변경 승인을 요청하지 않았다.
제품 코드와 프롬프트·설정은 변경하지 않았다. 삭제한 것은 사용하지 않는 테스트
보조 클래스 하나이며, 테스트 사례 삭제나 저장소 전체 정리 완료를 주장하지 않는다.

## 1. 중복 변환·검증

ground_screen_response에는 sanitize_unused_controls 호출이 두 번이 아니라 세 번 있다.
첫 호출 뒤 retain_action_targets/retain_verification이 응답을 보완하고, 두 번째 호출 뒤
빈 행동 보완과 prepare_screen_plan을 거쳐 마지막 대상 보완·정리가 실행된다.

089 오프라인 재생에 계측 wrapper를 붙여 입력·출력 dump를 비교했다.
각 위치에서 18회씩, 총 **54회 모두 값 변화 0회**였다. 이 사실만으로 삭제하지 않았다.
실제 보완 함수를 연결한 별도 합성 입력에서 다음 반례를 확인했다.

- 초기 응답은 무관한 OCR marker 3을 edit으로 잘못 분류했다. 첫 정리가 이를 제거했다.
- 모델 fake가 누락된 action target 1과 parent 2를 보완했다. parent의 영역은 target을
  포함하지 않았다. retain_action_targets가 이 연결을 추가한 상태를 확인했다.
- 두 번째 정리가 parent_id를 2에서 None으로 바꿨다. 보완 전후 검증 입력은 동일하지 않았다.

이 반례는 첫째·둘째 호출의 역할 차이를 증명하며 마지막 호출의 모든 경우를 증명하지는
않는다. 마지막 호출 앞에도 응답 교체와 대상 보완이 있어 중복 삭제 근거가 부족하다.
세 호출 모두 유지한다. private 재현 스크립트와 로그는
`.tools/cleanup-final/check_sanitize.py`, `sanitize-counterexample/`, `sanitize-calls.json`이다.

## 2. 상태 소유·계획 인계

| 후보 | 확인한 생산자·소비자/수명 | 판단 |
|---|---|---|
| state_property, progress_property | WorkerState의 동일 주소를 읽고 쓰는 접근자. 별도 상태 복사 없음 | 유지 |
| verified와 post_save_verified | 전자는 현재 입력의 descriptor, 후자는 저장 후 검토된 키. 저장·재조회 경계에서 후자 초기화 | 통합하지 않음 |
| evidence_ledger | 입력 surface의 소유권과 대체 이력. _record_model_evidence가 이전 소유 필드를 verified에서 제거 | 단순 verified 복제 아님, 유지 |
| _record_verified_field와 _record_model_evidence | 입력 표면·값 비교 후, 모델 검토 설정일 때만 공통 기록 경로로 위임. 다른 경로는 ledger/저장 후 판정을 하지 않음 | 조건 제거·무조건 통합하지 않음 |
| _remember_model_task_evidence의 불일치 제거 두 곳 | mapping의 현재 관찰값과 개별 evidence의 관찰값을 별도로 처리. 뒤의 기록 순서가 상태 결과에 영향 | 같은 문자열이라는 이유로 순서 변경하지 않음 |
| 086 대안 계획 인계 | OCR 입력 누락으로 전체 그래프 기준 미확보 | 기존 보류 유지 |

근거 위치: brain/graph/state.py의 ProgressState/state_property/progress_property,
engine.py의 _record_model_evidence/_record_verified_field/_remember_model_task_evidence 및
save/requery 경계의 post_save_verified.clear 호출. 소유 위치 수는 각각 그대로다.
추가 상태 통합을 구현할 근거가 확인되지 않았으며, 새 상태 계층을 만들지 않았다.

## 3. 도달 불가 후보

vulture 60% 조사 결과는 **76건**(brain 45, models 3, pipeline 10,
sawon_runtime 2, tests 16)이다. 80% 이상 경고는 없다.
원문은 `.tools/cleanup-final/vulture60.txt`에 보존한다.

| 후보 묶음 | 확인한 경계 | 조치 |
|---|---|---|
| Pydantic validator들 | decorator를 통한 검증 호출 | 유지 |
| Execution/WorkerState TypedDict 필드 | dict key 및 state_property 경로 | 정적 미참조로 삭제하지 않음 |
| elapsed_ms, review_reason, removed_groups, pipeline 추출 메타데이터 | 반환·응답 스키마/직렬화 필드. Python 속성 읽기만으로 소비를 판단할 수 없음 | 유지 |
| HTTP handler, redirect_request, daemon_threads | 상위 라이브러리가 호출하거나 읽는 이름 | 유지 |
| ActionIntent | 현재 직접 소비 검색은 없지만 공용 기본 어휘의 Literal 타입이라는 기존 계약 | 이번 정리에서 제거하지 않음 |
| 테스트 Mock.side_effect, OtherAnswer.different, no_provider | Mock API, 스키마 구분, autouse fixture | 유지 |
| StructuralRowRelay | 정의 외 참조 없음. core/bench와 설정 검색, getattr/globals/상속 열거 및 pytest 수집 경로 검사에서도 등록 없음 | 4번에서 제거 |

검토되지 않은 동적 경로까지 도달 불가라고 확정하지 않았다. 추가로 삭제를 확정한 제품
코드는 없다. brain/models/pipeline/sawon_runtime과 tests의 함수 AST 본문을 비교한
보조 조사에서는 25개 이상 AST 노드를 가진 완전 동일 본문 묶음이 없었다.
이는 의미상 중복이 없다는 증명이 아니며, 모든 함수의 수동 전수 검토도 아니다.

## 4. 테스트 정리

`tests/test_brain.py`의 StructuralRowRelay와 두 메서드, 18줄을 제거했다.
생성자를 AssertionError로 바꾼 테스트 프로세스에서도 해당 파일의 **28개 테스트가 통과**했다.
실제 테스트는 FakeRelay와 직접 만든 nested grid 요소를 사용한다.
삭제 전후 전체 수집 사례는 702건으로 같다. 테스트 본문과 assertion은 삭제하지 않았다.

최근 추가된 검증도 다시 구분했다. replan_pipeline의 두 오류는 표면/확신도 보완 경로이며,
plan_composition은 좌표 누락 오류의 계획 재작성과 기존 해석 결합 경로다. 호출 종류가
다르므로 둘을 같은 테스트로 취급하지 않는다. control_cleanup의 두 prior verified 상태는
상태가 삽입되거나 지워지지 않는 서로 다른 사전조건을 명시하므로 이번에는 유지했다.
별도 결함 탐지 행렬 없이 정상 재생과 오류 테스트를 합치거나 삭제하지 않았다.

## 5. 최종 검증과 범위

원본 실행 산출물과 기존 미커밋 수정은 보존했다. 이번 파일럿·유료 모델·실제 ERP 실행은 없다.
089 재생은 요청 19회·행동 29건·노드 88회 및 정상 종료가 유지됐고,
verified/post_save_verified는 각각 11개 키였다. 갱신 관찰 21건의 합성 envelope라는
제한은 [재생 보고서](graph-replay-20261023.md)와 같다.

bench는 수정 없이 **621 passed, 6 deselected, 22.52초**였다.
core 전후 coverage는 brain/models/pipeline/sawon_runtime, branch 포함 범위로 비교한다.
독립 COVERAGE_FILE을 사용하여 추적 중인 기존 .coverage는 덮어쓰지 않았다.

| 측정 | 제거 전 | 제거 후 |
|---|---:|---:|
| core 사례 | 702 passed | 702 passed |
| 전체 시간(coverage 포함, 각 1회) | 82.13초 | 84.28초 |
| Statement | 87.8616% (8505/9680) | 87.8616% (8505/9680) |
| Branch | 76.7661% (2521/3284) | 76.7661% (2521/3284) |
| 테스트 보조 클래스 StructuralRowRelay | 1 | 0 |

파일별 executed_lines/executed_branches 집합도 전후 같았다(손실·추가 0).
결함 탐지 테스트와 assertion 수는 변경하지 않았다. 이번 삭제가 실행 시간을 줄였다는
주장은 하지 않는다. Ruff check/format(232개 파일), vulture 80%, diff 공백 검사 통과.
기존 다섯 미커밋 파일의 diff 바이트도 작업 시작 때와 같음을 확인했다.

```powershell
$env:COVERAGE_FILE = '.tools/cleanup-final/after.coverage'
.\.venv\Scripts\python.exe -m pytest --cov=brain --cov=models --cov=pipeline --cov=sawon_runtime --cov-branch --cov-report=json:.tools/cleanup-final/after-coverage.json --cov-report=term:skip-covered
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m vulture brain models pipeline sawon_runtime scripts tests --min-confidence 80
git diff --check
```

반증 조건: 삭제된 helper의 동적 소비자가 발견되거나, 전후 제품 커버리지가 사라지거나,
동일 재생의 요청·행동·메모·종료가 달라지면 삭제/유지 판단을 다시 검토한다.
현재 계획의 조사·판정은 마쳤지만 086에 의존하는 통합은 해결 완료가 아니라 보류다.
