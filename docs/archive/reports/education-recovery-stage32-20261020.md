# 3-2 승인 항목 A: 교육 확장 문맥의 빈 행동 보완 전달

수정 전 기준: `75e09419e5cf7c0675205ca71912051cd07b4077`.
이 해시는 직전 3-1 조사 보고 `state-context-stage31-final-20261020.md`의 커밋이다.

이번 구현은 해당 조사에서 합성 재현한 한 가지 문맥 유실만 다룬다.
실제 파일럿·유료 호출은 없으며, 4-2는 구현하지 않았다.
4-1의 과거 실행 읽기 전용 판정 결과는 파일이나 과거 실행 기록에 쓰지 않고 대화로만 보고한다.

## 실패 확인 후 수정

먼저 `tests/test_screen_matching.py`에
`test_synthetic_education_expansion_survives_empty_action_recovery`를 추가했다.
테스트 주석에 기존 fake를 사용한 합성 입력이며 파일럿 원문이 아님을 명시했다.

입력은 기존 `setup_brain/named_response`로 만든 3개 응답이다.

1. `requested_instruction_ids=['save-record']`와 임시 행동.
2. `operation='act', steps=[]`.
3. `operation='stop', steps=[]`.

제품 수정 전에 이 테스트만 실행하여 **1 failed**를 확인했다.
실패 지점은 세 번째 요청의 `taught_procedure_progress`가 두 번째 요청과 같아야 한다는
단언이다. `save-record` 상세와 `retrieved_step_ids`가 없어서 실패했다.
호출 목적 순서는 screen_decision → education_retrieval → missing_actions_recovery였으며,
세 번째 호출이 실제로 발생한 다음 내용을 비교한다. 오류 종류나 우연한 호출 중단을 성공으로 보지 않는다.

## 제품 변경 범위

제품 변경 파일은 `brain/graph/matching.py` 하나, **+4/-3줄**이다.

- 기존 `MatchSelection`에 `prepared: MatchPromptContext` 필드 1개를 추가했다.
- `select_match_response`가 자신이 확장한 prepared를 반환한다.
- `match_screen`의 일반 선택과 메서드 실패 후 fallback 두 경로에서
  `ground_match_response`에 `selection.prepared`를 전달한다.
- `validate_or_replan`에는 기존 prepared를 그대로 전달한다. 이번 승인 범위 밖인
  거절 후 재계획 요청까지 바뀌지 않게 하기 위한 구분이다.
- `ground_match_response`의 capability/active_fields는 교육 확장으로 달라지지 않는다.
  해당 함수에서 prepared.state로 만드는 모델 요청은 빈 행동 보완 요청이다.
- 기존 테스트의 MatchSelection 생성 mock 2곳에 필수 필드를 전달하도록 맞췄다.
  새 클래스, 상태 기계, 정책 계층, Pydantic 모델 응답 필드는 만들지 않았다.

**삭제·해제·완화한 거절 분기: 0개.** 프롬프트 상수와 생성 문구도 수정하지 않았다.
두 xfail, control_cleanup, 앵커 절단 순서, 교육 상세의 턴 간 영구 보관은 변경하지 않았다.

## 요청 원문 비교

기존 테스트는 호출 수와 주요 문맥을 검증하지만 전체 요청 바이트 불변을 보장하지 않는다.
따라서 제품 수정 **전**에 5가지 합성 시나리오의 요청 원문을 저장하고, 수정 후 같은
입력으로 다시 실행하여 UTF-8 바이트 전체를 비교했다. 정렬, 동적 값 제거, 부분 필드
정규화 없이 비교했다. 아래는 모델에 보낸 프롬프트 문자열 비교이며, 원격 API는 호출하지 않았다.

| 합성 경로 | 요청 수 | 바이트 비교 |
|---|---:|---|
| 교육 조회 없는 일반 행동 | 1 | 전부 동일 |
| 교육 조회 없는 빈 행동 보완 | 2 | 전부 동일 |
| 이미 제공된 login 교육 요청 | 1 | 동일, 추가 조회 없음 |
| save-record 교육 조회 후 stop | 2 | 전부 동일 |
| save-record 교육 조회 후 빈 행동 보완 | 3 | 1·2번째 동일, 3번째만 변경 |

총 **9개 요청 중 8개 동일**이다. 비교 도구와 before/after 원문은 비공개 ignored
`cases/stage32-context-20261020/`에 있으며 커밋하지 않는다. 현재 제품에서 교육 확장이 없으면
select가 받은 prepared 자체를 그대로 반환하므로, 새 경로가 다른 문맥을 구성하지도 않는다.

변경된 3번째 요청의 전체 최상위 차이는 아래 **2개 키**뿐이다.

| 키 | 수정 전 | 수정 후 |
|---|---|---|
| taught_procedure_progress | steps=[login, open-query], retrieved_step_ids 없음 | 기존 steps 뒤에 save-record의 전체 상세 객체 추가, retrieved_step_ids=[save-record] |
| education_instruction | 없음 | `Requested details are present. Return the decision, not another retrieval.` |

두 번째 행은 새로 작성한 지시가 아니다. 수정 전부터 `screen_mapping.mapping_prompt:324`
에 있던 조건부 안내문이다. 이제 retrieved_step_ids가 보완 호출까지 유지되므로 그 기존
안내문도 출력된다. 두 번째 교육 조회 요청에는 수정 전에도 같은 문구가 있었다.
이를 숨기고 '추가 상세 단계 하나만 달라졌다'고 보고하지 않는다.

새 테스트는 save-record의 이름만 확인하지 않고 교육 파일의 전체 단계 객체
(instruction, completion, actions 포함)가 전달됐는지 확인한다. 이어 확장된 진행 문맥
전체가 보완 호출에 남는지 비교하며, stop 결과·교육 완료 목록 불변·실제 행동 없음도 확인한다.

## 검증 결과

| 명령 | 결과 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest tests/test_screen_matching.py::test_synthetic_education_expansion_survives_empty_action_recovery -q` (수정 전) | 의도한 문맥 누락 단언에서 1 failed, 2.19초 |
| 위 새 테스트 + `test_method_grounding_failure_retries_visual_cache_or_model_once` (수정 후) | 2 passed, 1.84초 |
| `.\.venv\Scripts\python.exe -m pytest` | **667 passed, 2 xfailed, 36.90초** |
| `.\.venv\Scripts\python.exe -m ruff check .` | 통과 |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 통과, 219 files already formatted |
| `git diff --check` | 통과 |

기존 두 xfail은 그대로다. 테스트 수 증가는 새 합성 회귀 테스트 1개뿐이다.
계약 필드의 생산자·소비자를 `docs/contracts.md`에, 같은 판단 안에서의 교육 보존 범위를
`docs/current-runtime.md`에 기록했다. 제품 설정은 바꾸지 않았다.

반증 조건: 동일 합성 입력에서 확장 상세가 다시 누락되거나, 교육 조회가 없는 경로의
요청 바이트가 달라지거나, 거절 후 재계획 요청까지 바뀌면 이번 수정의 범위/원인 설명이
틀린 것이다. 이 검증은 합성 경로의 문맥 보존만 입증하며 파일럿 성공률·속도 개선은 입증하지 않는다.

## C. 기존 원문 파일의 Git 제외 여부

bench의 `evidence/seed-audit-20261017-072/**/model-requests/*/response.txt`를 실제
파일 기준으로 열거하고 각 경로에 `git check-ignore -v`를 실행했다.

| 파일(072 아래 상대 경로) | 매칭 |
|---|---|
| seed000: `seed-000/compilation-trace-fixed/model-requests/09a361c77362466cabd2830e0b3ccced/response.txt` | 없음 |
| seed001: `seed-001/compilation/model-requests/80892b9dc09a4a02a0cc6ec221c543b2/response.txt` | 없음 |
| seed002: `seed-002/compilation/model-requests/1250bf39ca7842b2a3215ed0bf4ea082/response.txt` | 없음 |

**3개 모두 제외 규칙에 매칭되지 않는다.** `diagnostics/` 규칙은 model-requests 디렉터리에
적용되지 않는다. 이번에는 `.gitignore`를 수정하지 않았고 원문 파일도 add/이동/삭제하지 않았다.

여기서 구현을 멈춘다. 4-1은 정의에 대한 승인 대기이며 4-2와 파일럿은 미실행이다.
