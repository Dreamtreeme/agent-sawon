# 탐색·기억 주변 코드 단계별 정리

## 범위와 마킹

후보 정의: `../archive/memory-runtime-cleanup-candidates-20261028.md`의 C1–C7.
테스트의 `# Cleanup audit C…` 주석으로 관련 계약을 표시했다. 마킹은 삭제 승인이 아니라
각 후보를 제거할 때 함께 확인할 테스트의 식별자다. 제품 동작 하나를 정리하고 무료 검증과
실제 ERP를 확인한 후 다음 후보를 처리한다. 첫 단위는 영향이 작은 C7이다.

| 후보 | 테스트 파일과 함수 | 처리 |
|---|---|---|
| C1 입력 증거 중복 | test_screen_decision.py: test_semantic_mapping_field_reading_supplies_evidence_without_value_target_guessing, test_semantic_mapping_omits_unassigned_descriptive_evidence | 마킹, 미변경 |
| C2 요청 문자열 왕복 | test_screen_decision.py: test_mapping_preserves_review_and_retrieval_purpose_and_ignores_unused_grid, test_synthetic_education_expansion_survives_empty_action_recovery, test_synthetic_fallback_education_survives_empty_action_recovery | 마킹, 미변경 |
| C3 응답 이중 변환 | test_screen_decision.py: test_semantic_model_mapping_becomes_observation_and_coordinate_method | 마킹, 미변경 |
| C4 중간 타깃 문자열 | test_screen_decision.py: test_login_and_record_arrays_compile_to_existing_batches, test_same_screen_navigation_clicks_compile_to_one_action_plan | 마킹, 미변경 |
| C5 구형 증거 재결합 | test_screen_decision.py: test_semantic_mapping_field_reading_supplies_evidence_without_value_target_guessing | 마킹, 미변경 |
| C6 반복 정리 | test_screen_decision.py: test_empty_action_recovery_preserves_education_and_current_observed_values | 마킹, 미변경 |
| C7 동일 분기·필터 | test_memory_retrieval.py: test_taught_method_selects_the_only_branch_that_binds_current_screen, test_generic_success_trace_becomes_model_free_execution_graph, test_bound_edge_replaced_without_dispatch_unblocks_only_after_effect | 마킹, 실행 통과, 유지 |

12개 테스트 함수에 주석을 추가했다. 이번 단위에서 테스트 삭제·단언 완화는 0건이다.
C7의 세 테스트는 각각 account 화면 선택과 pruned 그룹 순서, consumed 그룹 제외와 선행 순서,
미전달 대체 행동의 효과 확인 여부를 검증한다. 같은 분기 두 줄을 지운다는 이유로 이 검증을
지우면 이후 후보 선택 회귀를 놓치므로 유지한다.

## 첫 단위 C7

`brain/graph/memory_retrieval.py`에서 동일한 `groups = remaining` 분기 2줄과,
remaining 생성 시 이미 제외된 used 그룹을 다시 제외하는 2줄을 삭제했다.
trace 선행 조건 필터와 pending_predecessor, 후보 순서 및 pruned_group_ids 계산은 유지했다.
상태 계약·프롬프트·메모 내용·OCR 처리·모델 응답 계약은 바꾸지 않았다.

무료 검증:

- `.venv/Scripts/python.exe -m pytest tests/test_memory_retrieval.py -q`: 32 passed, 5.59초.
- `.venv/Scripts/python.exe -m pytest`: 728 passed, 기존 3 xfailed, 42.53초.
- `.venv/Scripts/python.exe -m ruff check .`: 통과.
- `.venv/Scripts/python.exe -m ruff format --check .`: 266 files, 통과.
- `git diff --check`: 통과.

## 실제 ERP 125

시드 1, 회사 001 교육, ID none, visual_only, Haiku 5.5 low 8192 설정.
건별 한도 $3. 124의 편찬 산출물을 그대로 사용하고, 캐시·승격·case 경로만 새로 만들었다.
재편찬 또는 새로운 탐색은 하지 않았다. C7은 기억 인출의 그룹 선택 경로이므로 동일 산출물로
실행 순서 보존을 비교했다. 원본 소스 HEAD c0463b1에 이번 4줄 삭제를 적용한 실행이며,
실행 당시 diff와 설정 해시는 비공개 `.tools/cleanup-125-c7/`에 보존했다.

| 지표 | 기준 124 인출 | C7 삭제 후 125 인출 |
|---|---:|---:|
| ERP 채점 | 10/10 | 10/10 |
| 작업자 exit | 0 | 0 |
| 작업자 시간 | 26.917초 | 19.250초 |
| 행동 수 | 24 | 24 |
| 관찰 수 | 31 | 31 |
| 원격 모델 호출 | 0 | 0 |
| 전달된 원본 그룹 | 11 | 11 |
| 완료 결합 | 1 | 1 |
| method_fallback | 0 | 0 |
| 이번 실행 비용 | $0 | $0 |

11개 그룹 ID와 순서가 동일하다. 행동 구성도 set_text 14, click 7, scroll 2,
double_click 1로 같다. 메모 스냅샷 12개, 마지막 verified 키 11개가 유지되었다.
한 번씩의 실행이므로 시간 차이를 4줄 삭제의 성능 효과로 주장하지 않는다.
하네스 combined 비용 $0.0034822는 124 편찬 이력의 재표시이며 신규 청구가 아니다.

저장 후 상세 상단/하단 캡처를 직접 보았다. 상단에 저장 번호 1, 품명 Synthetic sample parts,
하단에 수량 12, 단위 CT, 중량 1250.5, 컨테이너 DEMO-CNTR-001, CNSHA/KRPUS가 보인다.
화면 확인과 독립 채점, 정상 종료가 일치한다.

비공개 증거 위치:

- bench `evidence/runs/cleanup-125-c7-retrieval/`
- core `cases/cleanup-125-c7-retrieval/1b252045ad03fd5f9200/case-63a0425fa4894e2ba97863d05a7a2870/case/`
- 상세 상단 프레임 `0060084a-1392-4c76-84a1-ffb38dc6d8da`
- 상세 하단 프레임 `48baa6b8-972b-459d-afff-21966b64b709`

반증 조건: 삭제 후 consumed 그룹이 재선택되거나, trace 선행 조건 또는 조건부 대체 경로가
달라지거나, 같은 산출물의 선택 순서가 달라지면 동등한 정리가 아니다. 무료 테스트와 이번
실행에서는 관찰되지 않았다. 다른 시드와 모델 예외 경로 전체를 실측한 결과는 아니다.

C1–C6은 아직 삭제하지 않았다. 다음 단위는 C2 요청 문자열 왕복 제거이며 실제 전달되는
교육·메모·복구 요청을 보존하는 테스트부터 대조한다.


## C2 — 문자열 왕복 제거

상태 dict를 최종 mapping_prompt에 직접 전달한다. 제공되지 않던 구형 지시문과
DecisionPromptContext의 prompt/rules 필드를 제거했다. 캐시 상태는 유지한다.
진단 prompt_chars는 조립 후 버린 중간 프롬프트 길이 대신 실제 호출 문자열 길이 합계이며,
모델을 호출하지 않은 인출에서는 0이다. 테스트의 구형 프롬프트 분기는 삭제하고 실제 최종
프롬프트와 교육·복구 문맥 단언으로 바꿨다. 테스트 함수 삭제는 없다.

152개 관련 테스트 통과. 전체 728 passed, 3 existing xfailed (43.61초), Ruff 통과.
124의 16개와 126의 20개 저장 요청 문맥을 구형/신형 생성기로 전달했을 때 최종 문자열 동일.
이는 동일 문맥에서의 요청 생성 동등성으로, 전체 그래프 상태의 동등성 증명은 아니다.

126 신규 탐색은 실패했다. 163.596초, 원격 20회, $0.0410566, 저장 0/10, exit 1.
입력 후 위아래 재확인을 반복했고 repeated_subgoal_failure:task:explore:key:pageup으로 종료.
seq 538의 요청에는 누적 screen_tree가 존재하고 seq 543은 현재 보이지 않는 하단 값을
다시 확인하겠다며 교육 enter-record-fields를 completed=false로 반환했다.
캡처의 상단 입력값은 실제 채워져 있었다. 이 실패를 삭제 변경의 무관함 또는 신규 결함으로
확정하지 않는다. 실패 증거는 cases/cleanup-126-c2-cold 및 bench 동명 evidence에 보존했다.

127 동일 코드·124 편찬 산출물 인출은 ERP 10/10, exit 0, 신규 비용 $0으로 성공했다.
신규 탐색의 신뢰성까지 통과했다고 해석하지 않는다. 나머지 후보는 이 실패를 고치기 위한
프롬프트/상태 변경과 섞지 않고 각 단위로 검증한다.


## C3 — 프레임 없는 임시 변환 제거

ScreenPlan.semantic_mapping은 모델 JSON의 ScreenMapping 검증과 보관만 한다.
번호·controls·evidence는 traced_call이 실제 screen/inputs에서 한 번 만든다.
기존 의미 매핑 통합 테스트에 변환 횟수 1회와 현재 markers 존재 검증을 추가했다.
post_action_review의 기대값은 제품 변환 결과가 아니라 원본 응답의 action/source/point로 비교한다.
공급자 테스트의 임시 controls 단언을 의미 fields 보존 단언으로 수정했고,
계획 합성 fake는 후처리 경계에 맞게 이미 연결된 계획을 반환한다. 함수 삭제/단언 포기 없음.
최초 전체 실행에서 이 구형 전제에 의존한 5건이 실패했으며 수정 후 전체 728 passed,
3 existing xfailed (40.16초), Ruff 통과. 제품 동작은 ERP 실행 중 변경하지 않았다.
128 새 탐색 ERP 10/10, exit 0, 비용 $0.0306696. 126 실패가 C3 때문에 해결됐다고
주장하지 않는다. 비결정적인 모델 실행이며 C3은 같은 실제 프레임 변환을 유지한다.
