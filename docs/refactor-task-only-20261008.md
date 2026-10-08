# 리팩토링 1단계: 화면 작업자 task 진입 경로 통합

2026-10-08 사용자 승인 계획의 1단계 완료 기록. 실행 기준선은 로컬 커밋 `fcc78dd`이며, 이전에 검증한 미커밋 정리 작업을 이번 삭제와 분리해 보존했다.

## 제거한 코드

- `brain.run --procedure`, `BrainSettings.procedure_path`, 경로 해석 및 Procedure JSON 로더.
- 구형 `Procedure` 컨테이너와 별도 plan 생성 분기.
- 구형 `RULES`/`PLAN_RULES` 프롬프트와 phase/checkpoint/field_scope에 따른 고정 단계 후보 필터.
- `exclude_current_navigation_step`, 구형 후속 selection 단계의 범위를 미리 요구하던 `require_deferred_field_evidence`.
- 클릭 상세 좌표 요청과 notebook에 전달하던 구형 Procedure 데이터.
- 더 이상 소비자가 없는 `tests/fixtures/shipment-procedure.json`.

화면 매칭은 이제 `--task` 또는 `task_path`를 요구한다. 구형 설정 키는 extra_forbidden으로, 구형 CLI 옵션은 argparse에서 거절된다. 자동 변환이나 무시하는 호환 분기는 추가하지 않았다.

## 유지한 계약

- `TaskInstruction.procedure`: 사용자가 가르친 화면별 순서·행동·완료 조건.
- 현재 capability 생성에 쓰는 `ProcedureStep`, 응답/캐시의 `procedure_step` 직렬화 필드. 구형 로더와 다른 공유 계약이며 이번에 이름 변경·캐시 변환을 수행하지 않았다.
- task 교육 해시, 현재 화면과 대상 연결, 캐시·방법 실행, 증거 기록, 비용·relay 계약.
- 비 화면 매칭 작업자의 기존 policy phase. 이 경로까지 제거한 것은 아니다.

## 테스트 이관과 삭제 근거

공유 `test_screen_matching.setup_brain`은 실제 TaskInstruction 파일을 생성해 task_path로 Brain을 초기화한다. 이 준비 경로에 구형 파일 로더를 남기거나 내부 task 상태를 덮어써서 로딩을 우회하지 않는다. 별도 단위 테스트에서 사용하는 상태 주입과 저장/조회 정책의 명시적 phase 설정은 유지한다.

| 제거한 테스트 함수 | 삭제한 전용 동작 | 남아 있는 공통 검증 |
|---|---|---|
| test_field_bound_lookup_navigation_requires_current_input_evidence | 구형 절차의 뒤쪽 selection step에서 필드 범위를 미리 추론하는 검사 | 선택 효과의 입력 출처 연결, 선택 뒤 편집 화면에서 값 확인, pending evidence 검증 |
| test_multi_action_verification_step_remains_eligible_after_navigation | 구형 후보 필터에서 복수 행동 step을 제외하지 않는 예외 | 현재 capability의 행동 허용 범위, 같은 화면의 복수 행동 컴파일·실행 |
| test_pending_tab_navigation_offers_its_successor_not_the_same_step | 구형 고정 단계 ID의 자동 후속 후보 선택 | 사용자 교육 진행/다중 행동 단계 완료 조건(method_runtime), pending 행동과 검증된 사실 분리(worker_context) |

삭제한 세 알고리즘 자체의 동작은 의도적으로 폐기했다. 이를 다른 테스트가 그대로 보장한다고 주장하지 않는다. 같은 파일에 섞여 있던 좌표·입력·증거·모델 재계획 검증은 현재 task 계약으로 옮겼다.

- 잘못된 구형 단계 이름을 거절하던 보정 테스트는 실제 잘못된 혼합 행동을 보정하는 사례로 변경했다. 자유로운 업무 목적 이름을 다시 거절하지 않는다.
- 구형 단계 목록/필드 제한에 대한 assertion은 현재 capability와 available/pending/verified 입력 분리 검증으로 변경했다.
- 잘못된 target, 입력 출처 범위, 부분 행동, 같은 화면 OCR 재사용, 모델 재호출 횟수, 저장 증거 검증은 유지했다.
- 중간에 발생한 이관 실패는 기존 장애나 flaky로 분류하지 않는다. 최종 결과를 아래에 기록한다.

## 측정

동일 환경에서 `coverage run --branch --source=brain,models,pipeline -m pytest -q`를 변경 전후 각 1회 실행했다. GUI 테스트는 저장소 기본 설정에 따라 제외된다. 시간은 계측 오버헤드를 포함하므로 파일럿 성능 개선의 근거가 아니다.

| 지표 | 변경 전 | 변경 후 | 차이 |
|---|---:|---:|---:|
| pytest | 683 통과 | 680 통과 | 구형 전용 3개 삭제 |
| 실행 시간 | 113.02초 | 107.83초 | -5.19초, 각 1회 |
| 종합 statement+branch coverage | 86.2324% | 86.2356% | +0.0033%p |
| statement coverage | 88.9272% | 88.9461% | +0.0190%p |
| branch coverage | 78.4717% | 78.3794% | -0.0923%p |
| Python assert 문 수 | 1,830 | 1,828 | -2 |
| 프로덕션 Python 물리 줄 수 | 16,713 | 16,465 | -248 |

assert 문 수는 AST 기준이며 실제 실행 횟수나 결함 탐지 점수가 아니다. 종합 커버리지에는 코드 삭제로 인한 분모 변경도 포함된다. 모든 전체 지표의 하락은 0.5%p 미만이다. 실행기와 saved_evidence의 종합 커버리지는 각각 91.84%, 97.19%로 동일했다. 변이 테스트는 이번 단계에서 측정하지 않았다.

주요 크기 변화: engine.py 2,858→2,835줄, matching.py 1,898→1,694줄. 테스트 함수의 이동·수정과 별도로 구형 JSON fixture 267줄을 제거했다.

Ruff lint/format, CRLF를 허용한 diff 공백 검사, 기본 운영 설정 로드와 구형 설정 거절, CLI 도움말에서 구형 옵션 제거를 확인했다. 측정 요약은 `test-cleanup/task-only-stage1-20261008.json`에 함께 보관한다.

## 다음 단계와 한계

이 단계는 구형 화면 매칭 진입 경로 제거다. WorkerState 밖에 남은 상태 통합, 실행기의 Brain 사설 멤버 의존 제거, 모델 증거 누락·오류 해석 재사용 수정은 아직 하지 않았다. 유료 화면 파일럿을 재실행하지 않았으며, 이번 테스트 통과를 실제 업무 전체 성공으로 보고하지 않는다.
