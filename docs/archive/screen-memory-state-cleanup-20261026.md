# 화면 기억과 모델 문맥의 중복 상태 정리

사용자 요청: 의미론적 변수의 실제 사용처를 더 조사하고 불필요한 항목을 제거한다.
기준 코드: `6095623`. 117 ERP 기록은 이 변경 전의 성공 기록이며 소급 수정하지 않는다.

## 제거한 항목과 근거

| 항목 | 이전 생산자·소비자 | 변경 |
|---|---|---|
| 전역 `ProgressState.observed_inputs`와 노드의 별도 `observed_inputs` | engine이 같은 관찰을 복사, notebook/context_recall이 모두 출력 | 노드 `fields[source].last_observation` 한 곳에 저장. 전역 속성·요청 인자·직렬화 삭제 |
| `delivery.status`의 awaiting/observed_match/observed_mismatch | 전달 시 대기, 관찰 시 비교 결과 저장; pending_screen_inputs가 읽음 | 상태 문자열 삭제. 실제 입력·관찰값과 범위를 남기고 관찰 순번으로 확인 대기를 계산 |
| `seen_in_last_view` | 관찰 ID 비교로 모델용 플래그 생성 | 삭제. 트리는 과거 사실이고 현재 가시성은 새 화면에서 판단 |
| 요청 `pending_verification`, 문맥 `awaiting_visual_result_review` | pending 유무를 다시 bool로 전달 | 요청 필드·플래그 삭제. 실제 pending과 전달 행동 목록은 유지 |
| `delivery_status=succeeded` | last_delivered_actions의 모든 항목에 같은 문자열 | 상수 필드 삭제. 목록 자체가 성공한 전달임을 의미 |
| notebook의 `last_delivered_actions` | execution의 같은 목록을 다시 복사 | execution 한 곳에만 전달 |
| `anticipated_verified_inputs/fields` | conditional 실행 정책의 remaining 목록을 뒤집어 재계산 | 모델용 예상 확인 목록 삭제. 조건부 정책과 실제 verified는 유지 |
| 모델용 `evidence_claims` | ledger의 field·관찰 ID·revision을 verified 목록과 함께 전달 | 요청 인자·프롬프트 복사와 사용처가 사라진 EvidenceLedger.prompt_context 삭제. 내부 ledger의 무효화·소유권 판단은 유지 |
| `available_inputs` | inputs의 키를 notebook·execution·저장 메모에 반복 | 키 목록 삭제. 입력 원문은 inputs, 저장 메모는 record.json 경로 사용 |
| scratchpad의 verified/remaining | notebook과 같은 목록을 재포장 | 필드와 메서드 인자 삭제. scratchpad는 목표·마일스톤, notebook은 실제 확인 목록 소유 |
| `post_action_review.delivered_sources` | pending_verification_inputs 복사 | 중복 목록 삭제. 검토 모드와 기존 라우팅 유지 |

`save_issued`, `model_saved_detail_review`, `saved_detail_transition_pending`,
`post_save_query_identity_verified`는 실제 분기와 증거 범위를 결정하므로 이번에 삭제하지 않았다.
`verified`, `post_save_verified`, evidence_ledger는 실행 판단·증거 소유권·무효화에 각각
소비자가 있다. 단순 이름 유사성만으로 합치지 않았다. 이는 저장소 전체의 모든 상태를
불필요하다고 판정한 작업이 아니라 화면 기억과 모델 문맥의 생산자·소비자 정리다.

## 관찰 순서와 단일 저장 위치

- 기존 `WorkerState.observation.sequence`를 입력 전달과 관찰 기록에 참조한다. 별도 카운터나
  상태 기계를 추가하지 않았다. `_observe()`는 현재 회차를 올린 뒤 그 화면의 사실을 기록한다.
- 같은 노드·범위에서 입력 당시보다 나중 관찰이 없으면 확인 대기로 계산한다. 같은 캡처 ID,
  같은 값, 입력 이전 관찰만으로 확인 대기를 해소하지 않는다. 이후 불일치를 읽으면 관찰은
  완료됐지만 verified에는 들어가지 않는다. 불일치를 대기 플래그로 표현하지 않는다.
- 노드에 전달 기록이 있으면 현재 관찰과 비교한 결과가 직전 행동 목록보다 우선한다.
  이미 읽은 값이 마지막 fill 행동 때문에 계속 pending 목록에 남는 중복 계산을 제거했다.
  필드 전달 기록이 없는 기존 경로만 전달 행동 목록을 참고한다.
- 경로 없는 편찬·관찰도 버리지 않는다. `[]` 루트 필드에만 저장하며 기존에 방문한 화면에
  자동 배정하지 않는다. 모델용 트리는 `{fields, controls, children}` 루트 아래 알려진 화면을
  children으로 투영한다. 화면명·하위 탭명은 새로 하드코딩하지 않는다.
- 외부 relay 계약, 모델 응답 스키마, 좌표 검증, 저장 성공 조건과 비용 설정은 변경하지 않았다.
  모델 요청 문맥의 모양과 설명은 변경됐다. 화면 패턴 캐시는 새 문맥으로 키가 달라질 수 있다.
  과거 실행·원문·캐시는 수정하지 않는다. task-notebook은 현재 실행에서 쓰는 산출물이며
  이 JSON을 읽어 WorkerState를 복원하는 생산 코드 경로는 없다.

제품 Python 10개 파일에서 56줄 추가, 123줄 삭제(순감소 67줄). 새 제품 파일이나 클래스는 없다.
계약 설명은 contracts/current-runtime/code-flow와 함께 갱신했다.

## 검증

- `python -m pytest -q`: **730 passed, 3 xfailed**, 44.63초. 루트 `.venv` Python 사용.
- 변경 영역 우선 검사: **180 passed, 3 xfailed**.
- `python -m ruff check .`, `python -m ruff format --check .`, `git diff --check`: 통과.
- 기존 106 예상 실패 3건(선언 안 된 행동 대상, 사용하지 않는 크기 0 필드, 빈 행동 보완 시
  이전 메모 사용)은 수정하거나 완화하지 않았다.
- 기존 합성 그래프 테스트는 상태 문자열 대신 실제 전달값, 다음 관찰값, pending 계산,
  verified와 입력 횟수를 검사하도록 갱신했다. 스크롤 후 숨겨진 입력 보존, 탭 격리,
  부분 전달 실패, 저장 후 증거 범위와 편찬 경로의 경로 없는 관찰을 검증한다.
- 순번 회귀 테스트 1개를 추가했다. 동일 캡처 ID·동일 값의 이전 관찰, 이후 관찰, 다른 증거
  범위, 다른 화면 노드의 같은 입력 키를 구분한다. 각 반복 단언은 순번·범위를 실패 메시지로 낸다.
- 테스트 프로세스에서만 pending_screen_inputs를 ‘값이 같으면 대기 종료’로 monkeypatch했다.
  새 회귀 테스트가 `(1, entry)`에서 실제 실패함을 확인했다. 제품 파일은 변이하지 않았다.
- 새 상태 표현을 요구하도록 기존 테스트와 캐시 테스트의 구형 evidence_claims 예제도 정리했다.

실제 ERP와 유료 모델은 이번 정리에서 재실행하지 않았다. 따라서 모델의 실제 선택 변화나
호출·시간·비용 개선은 아직 검증하지 않았다. 117의 성공을 변경 후 성공으로 간주하지 않는다.

반증 조건: 숨긴 필드나 경로 없는 관찰이 사라짐, 입력 이전 관찰이 확인 대기를 해소함,
입력 전달만으로 verified가 생김, 한 탭의 같은 입력 키가 다른 탭을 덮어씀,
entry 관찰이 saved_detail 완료 증거가 됨. 이 경우 단순 필드 삭제가 의미를 보존하지 못한 것이다.
