# 066 교육 재호출 및 상태 문맥 감사

분석만 수행했다. 코드 수정/추가 유료 호출 없음. 실제 27개 SDK 요청 보존본과 코드 경로를 대조했다. 수치와 상태 발췌는 cases/seed3-context-audit-066.json에 있다.

## 1. 교육 조회: 새 정보가 있는지 검사하지 않는 재계획

matching.py:1734는 requested_instruction_ids가 비어 있지 않으면 무조건 실행된다. 유효한 교육 ID인지만 검사하고, 현재 prepared.state에 해당 단계의 상세가 이미 제공됐는지는 확인하지 않는다. 현재 실행 가능한 steps도 provisional로 취급해 새 응답으로 대체한다.

- 호출 17→18: save-record, requery-saved-record 상세가 원래 요청에 모두 있었다. 두 단계 객체를 비교하면 각각 완전히 같다. 재호출 비용 3.13초. 요청의 다른 기존 최상위 데이터도 동일하다.
- 호출 21→22: requery-saved-record 상세가 원래 요청에 있었다. 재전송한 단계 객체도 같다. 오히려 기존 verify-requeried-record 상세가 제거됐다. 재호출 비용 7.24초.
- steps를 기존 교육과 합치지 않고 요청한 ID만으로 대체한다. retrieved_step_ids는 해당 호출의 expanded 변수에만 존재한다. 다음 턴 _taught_procedure_progress는 active_taught_step 기준 2개를 새로 구성하므로 읽은 상세의 지속 기록은 없다.
- 첫 응답이 교육 ID와 행동을 함께 반환하면 재호출하지만, 두 번째 응답이 같은 조합이면 행동을 수용하고 교육 ID를 제거한다. 같은 상태에 서로 다른 정책을 적용한다.
- test_education_retrieval_accepts_actions_when_model_repeats_resolved_request는 시작 시 이미 상세가 있는 login을 요청해도 호출이 2개여야 한다고 단언한다. 중복 호출을 방지하는 테스트가 아니라 현재 중복을 고정한다. 멀리 떨어져 아직 주지 않은 save-record를 조회하는 별도 테스트는 유효하다.

작은 수정 범위: 현재 제공한 상세 ID와 requested ID의 차집합만 조회한다. 새 상세가 없고 실행 계획이 있으면 재호출 없이 기존 계획을 진행한다. 새 상세가 필요하면 기존 상세와 합쳐 한 번 재계획한다. 새 상세도 행동도 없는 응답은 교육 조회 성공으로 위장하지 말고 행동 누락 복구로 구분한다. 추가 교육 영구 저장 구조는 즉시 중복 제거에 필수적이지 않다.

## 2. 상태의 조건부 예상 계산이 덮어써진다

context.execution_context는 실제 확인값 + 이번 전달값을 합쳐 조건부 proposal을 만들고, 그에 맞춰 remaining_fields와 allowed_intents를 계산한다. matching.build_match_prompt는 execution.remaining_inputs를 다시 실제 verified_fields의 차집합으로 덮어쓴다. anticipated_verified_inputs도 실제 verified_fields를 그대로 복사한다.

호출 14: 실제 확인 5개, 방금 입력해 확인 대기 중인 추가 정보 6개. execution.allowed_intents에는 save가 있지만 remaining_inputs에는 그 6개가 모두 남는다. anticipated_verified_inputs는 기존 5개뿐이다. 조건부 예상이라는 이름과 실제 값이 맞지 않는다. 모델은 설명에서 저장을 선택하지만 steps에서는 6개 fill을 반환했다. 문맥 불일치는 확인됐으나 그 모순 응답의 단일 원인임은 입증하지 않았다.

실제 confirmed 상태를 섣불리 변경하면 안 된다. 입력 전/전달됨/관찰 확인됨은 유지하되, 프롬프트의 현재 증거 상태와 조건부 다음 행동 문맥을 하나의 계약에서 파생해야 한다. 단순히 pending을 verified로 승격하거나 남은 목록에서 숨기는 처방은 부적절하다.

## 3. 재조회 첫 판단의 증거 범위가 한 호출 늦게 바뀐다

engine._observe는 visual_perception.observe 안에서 모델을 먼저 호출한 뒤 _begin_saved_detail_review를 실행한다.

- 호출 24 / seq491: 실제 화면은 열린 저장 상세. notebook.verified_inputs는 저장 전 전체 11개, remaining_inputs=[], post_save_detail_review=false, saved_result_open_pending=true.
- seq498: 모델 응답 뒤에 새 상세 검토 시작/목록 초기화.
- 호출 25: verified_inputs는 첫 상세 화면에서 확인한 4개, remaining_inputs는 7개, post_save_detail_review=true.

문구로 pending을 설명하고 있으나 같은 키의 의미가 호출 사이에 저장 전 증거에서 저장 후 증거로 바뀐다. 실제 전이가 실패할 수 있으므로 화면 확인 전에 전역 메모를 무조건 비우면 안 된다. 다음 상세 검토 후보의 증거 범위를 요청에 명확히 분리하고, 화면 확인 후 확정해야 한다.

## 4. 현재 task 경로에서 갱신되지 않는 구형 플래그가 전달된다

query_submitted 갱신은 duplicate_query/final_query phase에서만 발생한다. 현재 phase=task 호출 5와 21에서는 search를 방금 전달했는데 workflow_state.query_submitted=false다. duplicate_clear/query_value_verified도 false로 남는다. pending_actions에는 search가 있어 같은 요청 안에서 서로 다른 의미가 섞인다. false는 조회 안 함을 뜻하지 않지만 모델에게 적용되지 않는 값임을 밝히지 않는다. task 경로에는 실제 사용하는 상태만 내보내거나 명시적으로 적용 범위를 구분해야 한다.

## 5. 메모 계층별 갱신 경로가 다르다

순수 필드 배치는 _verify_node에서 _verify_screen_batch 후 조기 반환하므로 일반 _record_action_verification의 rolling_context.record_turn/mark_milestone 경로를 거치지 않는다. verified_inputs와 action_transition은 정상 기록되지만 recent_turns에는 입력보다 화면 이동만 남는다. 호출 14의 recent_turns는 신규 화면 이동, 스크롤, 탭 이동이고 6개 입력 완료는 없다. completed_milestones도 save_issued 전까지 비어 있다.

이는 실제 입력 증거 자체의 소실은 아니다. 서로 다른 요약 채널의 정보량/시점이 다르다는 문제다. 기존 검증 완료 이벤트에서 같은 메모 갱신 경로를 사용하거나, 모델용 상태에서 중복 요약을 줄여야 한다.

## 6. 교육 완료 목록과 현재 단계의 의미가 다르다

active_taught_step은 모델 제안 시점에 바뀌지만 completed_taught_steps는 현재 코드상 저장 상세 검토 시작 경로에서만 추가된다. 따라서 호출 1~24에서는 completed_step_ids=[]이고 호출25 이후에는 requery-saved-record 하나뿐이다. 이를 모든 이전 단계가 미완료라는 의미로 사용하면 안 된다. 모든 클릭 성공을 교육 단계 완료로 자동 승격하는 것도 틀리다. 확정된 완료만 기록하는 희소 목록임을 계약으로 분명히 하거나 현재 모델 문맥에서 제거하는 범위가 적절하다.

## 우선순위

1. 교육 상세 중복 검사, 기존 상세 합집합 유지, 중복 재호출을 고정한 테스트 수정 (직접 관측 비용 10.37초).
2. 조건부 예상 상태 덮어쓰기 제거, task에서 의미 없는 플래그 정리.
3. 저장 전/재조회 증거 범위를 명확히 분리하여 첫 상세 판단의 낡은 상태 제거.
4. 필드 배치와 단일 행동의 메모 갱신 일관성, 이전 purpose를 현재 사실과 구분.

이 범위는 상태 계약/문맥 조립 수정이다. 신규 FSM, 전체 행동 거절 규칙, 모델 출력 토큰 확대는 필요 근거가 없다. 실제 절감 시간과 오판 감소는 수정 후 원문 보존 비교로 측정해야 한다.
