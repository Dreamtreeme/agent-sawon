# 현재 파일럿 밖 코드의 삭제 후보 검토

후속 상태: 구형 Procedure 진입 경로와 전용 필터·프롬프트 제거를 완료했다.
아래 이관 중간 기록의 보류 항목 중 해당 범위는 해소됐으며,
[1단계 완료 기록](refactor-task-only-20261008.md)을 최신 상태로 본다.

기준일: 2026-10-08. 최초 조사는 후보 등록만 수행했다. 이후 사용자의 ‘정리시작’ 지시로 적용한 내용은 아래 후속 정리에 구분한다. 기존 작업 트리의 변경/삭제는 이번 조사와 구분한다.

## 근거와 한계

- 실행: bench `evidence/core-pilot-20261008-113452/agents.json`, `brain.toml`, `task.json`.
- 기록: core `cases/core-pilot-20261008-113452/d577bc8540217ecb61a0/case-ea2656c114eb4f898932879a7c1d721d/case/run_log.jsonl`의 478개 이벤트.
- 명령 진입점은 `brain.run`, 별도 로컬 워커는 `pipeline.worker_broker`다. Luna, visual_only, screen_matching, 사용자 task, methods, visual cache를 사용했다. prepared-case와 구형 procedure는 지정하지 않았다.
- 이번 실행에 함수/라인 커버리지 계측은 없다. 따라서 아래의 ‘제외’는 설정 분기와 호출 코드로 확인한 것이며, 로그가 없다는 이유로 미실행 또는 사장 코드를 단정하지 않는다. Python import는 실행 증거가 아니다.
- core의 brain/models/pipeline Python 모듈 목록과 후보의 호출·설정·테스트 참조를 조사했다. bench는 해당 실행 명령 및 설정/심벌 참조 확인 범위다. bench 전체에 대한 삭제 전수조사 결과는 아니다.

## 사용이 확인된 경로

`brain.run → 설정/레코드/task 로딩 → ModelClient/OpenAI → Brain/worker graph → relay 캡처 → GPU OCR/OmniParser → visual perception/markers → screen matching 또는 method 재사용 → 실행 계획/좌표 실행 → 재관찰/증거 기록 → 종료`

소스와 로그를 함께 대조한 주요 기록:

| 기능 | 로그 check | 건수 | 판단 |
|---|---|---:|---|
| 회사 교육 로딩 | user_education_loaded | 1 | task_instruction 및 교육 컨텍스트 유지 |
| 화면 매핑 | screen_mapping | 25 | matching/scene/marker 계약 유지 |
| 해석 재사용 후 계획 | grounded_interpretation_planned | 6 | 분리 설정 false여도 캐시 후 계획 경로가 사용됨 |
| 동일 화면 재사용 | visual_identical_capture_renewal | 21 | perception 캐시 유지 |
| 실행 전 제안 검토 | proposed_execution_review | 19 | 검토 모듈 전체 삭제 불가 |
| 방법 실행 | method_execution | 19 | method_runtime/cache.methods 유지 |
| 입력 계획/확인 | screen_fill_plan / screen_fill_values | 4 / 4 | screen_batch 및 검증 유지 |
| 클릭 좌표 검토 | click_coordinate_review | 2 | 상세 좌표 보완 경로 유지 |
| 저장 결과 검토 | save_result_review | 1 | 저장 검토 관련 코드 전체 삭제 불가 |
| 모델 증거 기록 | model_task_evidence_recorded | 12 | saved evidence 유지 |
| 대상 미연결 증거 제거 | unbound_action_evidence_omitted | 2 | 실행된 문제 경로: 사장 코드가 아니라 수정 검토 대상 |

이벤트 개수는 함수 호출 횟수나 캐시 성공 횟수와 동일하지 않다. promotion 이벤트가 있다는 사실만으로 승격 성공도 단정하지 않는다.

## 우선 삭제 후보: 소비자가 없는 설정

core 및 bench의 Python 소스에서 이름 참조를 검색하고 core 설정 파일도 대조했다. 아래 4개는 선언 외 Python 참조가 없었다. 모델 덤프에 포함될 수는 있지만 그 값으로 동작을 변경하는 소비자는 확인되지 않는다.

| 후보 | 위치 | 근거 | 삭제 시 확인 |
|---|---|---|---|
| hybrid_assist_focus | brain/settings.py:60 | 설정 필드 선언만 존재 | 실제 relay 전경 복원 기능은 삭제하지 않는다. 외부 TOML의 키 제거/마이그레이션 필요 |
| hybrid_assist_ime | brain/settings.py:63 | 설정 필드 선언만 존재 | relay의 IME 처리와 별개임을 유지 |
| hybrid_assist_scroll | brain/settings.py:66 | 설정 필드 선언만 존재 | 실제 scroll 실행/관찰 기능 유지 |
| jev_request_max_bytes | brain/settings.py:79 | Jev 호출 분기 정리 후 요청 크기 설정 소비자 없음 | 과거 재현 설정에 키가 있으면 strict 설정 로더에서 거절될 수 있음 |

`BrainSettings`는 extra=forbid다. 코드에서 안 쓰인다는 사실과 외부 설정 파일 호환성이 깨지지 않는다는 사실은 다르다. 이 4개도 실제 삭제 단계에서 호환성 점검이 필요하다.

## 경로 폐기와 함께 정리할 조건부 후보

| 후보 묶음 | 구체 위치/범위 | 이번 파일럿에서 제외된 근거 | 남은 의존성·삭제 조건 |
|---|---|---|---|
| 구형 고정 Procedure 입력 | brain/run.py:30,69–77; brain/settings.py procedure_path; engine.py:361; matching.py:78의 Procedure | --task 사용, task_instruction 존재 | 구형 --procedure 지원 폐기를 결정한 뒤 CLI/로더/테스트를 함께 정리. tests/test_screen_matching.py, test_worker_context.py, fixtures/shipment-procedure.json 의존이 남아 있음 |
| 구형 절차 후보 필터와 프롬프트 | matching.py RULES/PLAN_RULES, exclude_current_navigation_step:1274, match_screen의 procedure 분기:1393 부근 | task 경로는 task_action_contract로 후보 생성, TASK_RULES 사용 | 구형 Procedure 폐기와 묶어서 제거. **ProcedureStep은 task_action_contract:1301도 반환하므로 삭제 대상 아님**. procedure라는 문자열을 일괄 삭제하면 현재 사용자 교육까지 손상 |
| 구형 단계별 업무 상태·지시 | graph/context.py의 duplicate_query/enter/final_query 지시; graph/policy.py의 해당 규칙; engine의 task_instruction 없는 분기 | 현재 phase=task, 사용자 교육 기반 경로 | 공통 정책·자격증명·저장 결과 전이와 섞여 있음. 파일 전체 삭제 금지. tests/test_brain.py 및 구형 matching 테스트의 보장 내용을 현재 task 테스트로 이관한 뒤 구형 분기만 제거 |
| 비 screen_matching 결정/상위 모델 복구 분기 | engine.py:1357 _choose_decision의 screen_matching 조기 반환 이후; brain.run의 recovery-model 설정 | screen_matching=true, recovery_enabled=false | 현 파일럿에서는 배제되나 공개 설정으로 도달 가능. 지원 종료 여부 보류. **이미지 좌표 보완과 다른 기능**이므로 refinement 모듈을 함께 지우면 안 됨 |
| legacy_uia 및 hybrid 모드 선택 | settings.py:59,88; relay.py visual 분기; engine 초기화 | visual_only 실행 | legacy 지원 종료 시 조건부 정리. hybrid는 is_visual에는 포함되지만 engine.py:369 perception 생성 조건은 visual_only만 허용하여 경로 불일치도 있음. 이를 ‘미사용이므로 안전’으로 처리하면 안 됨 |
| flat_fields 비교 옵션 | observe/summary.py:114–163; settings.py:29; brain.run summary CLI | label_grouped 지정 | 테스트와 이전 비교 프로토콜에서 사용. 요약 비교 기능 폐기 확정 전 보류. summarize 전체는 공통 함수라 유지 |

조건부 후보는 ‘아무도 호출하지 않는 코드’가 아니다. 현재 제품 방향과 다른 경로를 유지하고 있어 삭제 검토 대상으로 등록한 것이다. 현재 task 경로로 회귀 검증을 옮기기 전에 연관 테스트를 통째로 지우지 않는다.

## 이번 파일럿 밖이지만 삭제 후보에서 제외/보류

| 영역 | 현재 판단과 이유 |
|---|---|
| separate_observation_decision=true 경로 | 이번 false 설정과 달리 현재 config/brain.visual.toml 기본값은 true. 유지 |
| OmniParser v2/캡션 경로 | 파일럿은 use_v2=false로 캡션 비활성. 후속 해시 확인에서 탐지 가중치는 공식 v2 icon_detect와 동일하여 기존 v1 표기를 정정한다. 캡션 경로 유지 |
| models/gemini.py | 다른 공급자/비교 기능이며 명시적인 factory 경로 존재. Luna 파일럿 미사용만으로 삭제 불가 |
| models/replay.py | 네트워크 없는 회귀 테스트 경로. 유지 |
| pipeline/run, runner, extract, normalize, checks, render, evaluate, measure, workflow, handoff | 이미 만든 record를 전달했으므로 문서 추출/준비 검증은 이번 명령 경로 밖. M2 문서 기능과 독립 CLI이므로 제품 범위 폐기 전 보류 |
| pipeline/ocr*, persistent_worker, worker_broker/server/process, schemas | 화면 OCR도 사용하는 공통 인프라. pipeline 디렉터리 일괄 삭제 금지 |
| brain/visual/record.py | 별도 관찰 기록 CLI. 자동 파일럿 명령에서 호출하지 않았지만 사용자 요청에 의한 화면 관찰용 기능. 보류 |
| brain/demo_learning/methods.py | 별도 학습 CLI. 런타임 MethodRuntime과 구별해야 함. 시연 학습 기능의 존속을 결정하기 전 보류 |
| brain/cache/visual_match.py, methods.py | 캐시 및 업무방법 실행이 현재 경로에 연결되어 있음. 유지 |
| graph/review, save_review, saved_evidence, reconciliation, execution | model_only_review=true가 코드 전부를 우회한다는 뜻이 아님. 실제 검토/증거/저장 로그 존재. 유지하고 오류 부분을 별도 수정 |
| brain/observe 및 sawon_interfaces의 Observation/UIElement 형태 | 비주얼 화면도 공통 계약 형태로 변환. 이름이 UIA처럼 보인다는 이유만으로 삭제 불가 |
| 성공 종료/승격 및 실패 예외 경로 | 중단된 파일럿 한 건에서 성공 경로가 안 나왔다는 것은 삭제 근거가 아님 |

## 권장 정리 순서

1. 소비자 없는 설정 4개를 작은 변경으로 정리하되 외부 설정 호환성 확인.
2. 구형 --procedure 입력 및 해당 프롬프트/후보 필터 폐기를 하나의 범위로 다룸. 현재 task가 재사용하는 ProcedureStep 등은 보존.
3. 이후 구형 업무 phase와 non-screen_matching/legacy_uia를 각각 별도 변경으로 검토. 공통 함수까지 확대 삭제하지 않음.
4. 현재 중단 원인인 증거 제거·잘못된 화면 의미 재사용은 별도 결함 수정. 이들은 이번 실행에서 실제 사용되므로 레거시 삭제로 해결될 문제가 아님.

최초 조사에서는 코드 삭제, 테스트 삭제, 유료 재실행, 커밋을 수행하지 않았다. 전체 테스트도 문서 조사만으로는 재실행하지 않았다.

## 후속 정리: 1차 적용

사용자의 ‘정리시작’ 지시에 따라 소비자 없는 설정 4개를 제거했다. 현재 core config/examples 및 bench config에 이 키를 사용하는 설정이 없음을 확인했다. 과거 실행 폴더는 수정하지 않았다. 오래된 설정을 직접 로드할 때 제거된 키는 extra_forbidden으로 거절하며, 키 제거 안내를 config/README.md에 기록했다.

동일 동작인 중복 조건도 두 곳 정리했다.

- brain/run.py: `is_visual or perception_mode == visual_only`를 `is_visual`로 단순화. is_visual이 이미 visual_only/hybrid를 포함한다.
- brain/graph/engine.py: visual_only 분기 내부의 항상 참인 is_visual 검사를 제거. OCR 필수 검사와 기존 perception 생성 범위는 유지한다. hybrid 경로 불일치는 이번 정리에서 수정하지 않았다.

구형 Procedure 경로는 아직 제거하지 않았다. tests/test_screen_matching.py의 공통 setup_brain이 procedure_path를 사용하여 입력·좌표·저장 등 공유 동작도 검증한다. 이를 함께 지우면 현재 task 경로의 검증까지 잃을 수 있다. 먼저 공유 동작 테스트를 TaskInstruction 기반으로 이관하고 구형 계약 전용 테스트를 분리하는 후속 변경이 필요하다. ProcedureStep 등 현재 코드에서 사용 중인 계약은 그대로 둔다.

테스트 파일 삭제·추가 없이 기존 683개 테스트로 변경 전후를 비교한다. 기본 visual 설정과 실제 파일럿 설정의 로드, 제거 키 거절, 세 perception 모드에서 OCR 조건 동등성도 직접 확인했다. 유료 실행은 하지 않았다.

| 검증 | 변경 전 | 변경 후 |
|---|---:|---:|
| 전체 pytest | 683 passed / 85.99초 | 683 passed / 86.66초 |
| Ruff lint | 이번 기준선 별도 미측정 | 통과 |
| Ruff format | 이번 기준선 별도 미측정 | 151 files already formatted |

시간은 각각 1회 측정으로 성능 차이를 주장할 근거가 아니다. 커버리지·변이 점수는 이번 소규모 코드 정리에서 측정하지 않았다. Git diff 공백 검사는 Windows CRLF를 허용한 설정에서 통과했다. 기존 미커밋 변경이 있어 전체 diff의 증감량을 이번 변경량으로 보고하지 않는다.

## 후속 정리: task 경로 검증 이관

브랜치 `cleanup/tests-20261008`에서 테스트 이관을 진행했다. 이번 묶음에서는 프로덕션 코드와 구형 로더를 수정하지 않았다.

문제: 기존 task 테스트가 구형 procedure_path로 Brain을 만든 뒤 task_instruction/phase/procedure를 덮어썼다. 실제 task 파일 로딩, 교육 해시 설정, 초기화 시 교육 로딩 이벤트를 거치지 않으므로 파일럿 초기화 경로의 결함을 놓칠 수 있었다.

- screen matching의 20개, worker context의 1개, saved evidence의 2개 테스트 함수를 실제 task_path 초기화로 이관했다. 이 중 17개는 내부 상태 덮어쓰기를 제거했고, 6개는 공통 화면 동작 검증을 구형 절차에서 task 경로로 옮겼다.
- 동일 task 준비 코드 11곳을 함수 범위 fixture로 합쳤다. 각 테스트는 독립된 TaskInstruction과 tmp_path를 사용하며 Brain·모델·로그·캐시를 공유하지 않는다.
- task 초기화 테스트에 교육 파일 해시, 기록된 교육 내용, 로딩 이벤트, 구형 procedure 산출물 부재를 검증하도록 보강했다. 기존 동작 assertion을 삭제하지 않았다.
- setup_brain의 task_instruction 인자를 필수로 바꿨다. 남은 구형 테스트는 None을 명시해야 하며 새로운 task 테스트가 조용히 구형 로더로 들어가지 않는다.
- 관련 96개 테스트가 통과했다(9.65초). 테스트 수를 줄이거나 새로운 테스트 함수를 추가하지 않았다.

아직 남은 구형 초기화 사용은 screen matching 29개, worker context 7개, marker contract 1개, reconciliation 2개, saved evidence 2개 테스트 함수다. 이 개수는 parametrized 실행 case 수와 다르다. 구형 단계 후보·필드 범위·query checkpoint 전용 검증과 클릭·입력·저장 공통 검증이 섞여 있다. 이관이 완료되지 않았으므로 구형 --procedure와 fixture 파일은 아직 삭제하지 않았다. 이 문서의 조건부 삭제 목록은 완료 목록이 아니다.

전체 실행에서 공유 setup 함수의 다른 호출 파일에 필수 인자가 누락된 8개 case가 실패했다. 누락된 호출을 수정하고(saved evidence의 task 2개는 실제 로더로 이관), 해당 세 파일의 34개 case를 다시 실행하여 통과했다. 이 실패는 기존 결함이나 flaky가 아닌 이번 테스트 이관 중 발생한 인자 변경 누락이다.

최종 전체 실행은 **683 passed / 80.67초**다. Ruff lint와 format(151 files), 모든 setup_brain 호출의 명시적 task_instruction 인자 검사도 통과했다. 직전 단계 전체 실행은 683 passed / 86.66초였으며 1회씩의 시간으로 속도 개선을 주장하지 않는다. 커버리지·변이 점수는 이번 이관에서 측정하지 않았고, 테스트 삭제는 없다. 기존 미커밋 변경과 섞여 있어 이 묶음의 커밋은 아직 만들지 않았다.
