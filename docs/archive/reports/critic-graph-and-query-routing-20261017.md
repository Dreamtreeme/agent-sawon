# 실행 그래프 Critic 및 구형 조회 검토기 제거 — 2026-10-17

## 변경

- 웜 편찬의 기존 모델 호출을 Critic 역할로 변경했다. 실행 순서, 앞선 후보, 실제 전달 행동, 결과 전이와 후속 화면 OCR을 검토해 유지/제거 이유를 반환한다. 추가 모델 호출 단계를 만들지 않았다.
- 유지 항목은 review_reason, 제거 항목은 removed_groups(candidate_id, reason)에 기록한다. 원본 assembly 입력/요청/응답 산출물에 판단 근거를 보존한다.
- 새로운 실행은 각 판단을 screen_plan_proposed 이벤트에 저장한다. 같은 화면에서 여러 번 판단해도 파일 두 개만으로 재구성하지 않고, 각 계획과 그 계획 이후의 실제 액션 요청 순번을 연결한다.
- 전이 검토 로그가 다음 판단보다 늦게 기록될 수 있어, action_id로 실제 요청 시점을 찾아 계획에 연결한다.
- 기존 실행 기록은 screen-match/screen-plan과 screen_mapping 이벤트를 연결해 읽는다. 모든 행동이 생략된 계획은 nonexecuted_plans에 남기며 재생 후보로 포함하지 않는다.
- 일부 입력만 생략된 배치도 실제 전달 행동만 재생 후보에 남긴다. 생략된 입력은 already_satisfied 증거로 Critic에 제공한다.
- 관찰 번호 대신 실제 액션 요청 순번을 trace_index로 사용한다. 동일 프레임의 서로 다른 실행 배치도 순서가 분리된다.
- `_check_duplicate()`를 삭제하고 Review 노드의 해당 호출도 제거했다. `duplicate_query_not_executed`, `query_not_empty`, `ungrounded_duplicate_claim` 중단 경로는 이 검토기와 함께 삭제됐다.
- 조회 결과만 보고한 응답은 query_result_observed로 메모와 로그에 남기고 다음 Decision으로 진행한다. 다음 행동이 함께 있으면 기존 공용 실행으로 연결한다. 조회 보고를 작업 완료로 취급하지 않는다.
- 실제 모델 요청의 MAPPING_RULES에도 조회 결과 다음에 교육에 맞는 행동을 선택하도록 명시했다.
- 공통 최종 완료 검토와 그 검토가 사용하는 `_grid`는 유지했다. 삭제된 중복조회 전용 분기에 결합된 테스트는 최종 그리드 검증 기준으로 갱신했다.

## 검증

- 시드 3 기존 기록의 로컬 후보 재구성: 24개 후보 / 24개 고유 실행 순번. 실제 품명 입력은 유지되고 c285e80b 프레임의 생략된 재입력은 제외된다. 같은 프레임의 추가 정보 탭 클릭은 유지된다.
- 로컬에서 모든 실제 후보를 선택한 편찬: LearnedMethod 생성 성공. 모델의 경로 정제 효과나 실제 웜 성공을 검증한 것은 아니다.
- 회귀 테스트: 동일 프레임의 실제 입력 → 전체 생략 계획 → 클릭을 구별하며, 뒤늦게 기록된 전이도 원래 실행 배치와 연결된다.
- 회귀 테스트: task의 duplicate_clear 및 duplicate_found가 메모에 기록되고 다음 모델 판단/실행으로 진행된다. 구형 단계 플래그를 설정하거나 검토기를 우회하는 별칭은 없다.
- `.venv/Scripts/python.exe -m pytest`: 630 passed, 1 xfailed, 24.23초. xfail은 기존 반복 관찰의 visual_proposal_unknown_marker.
- ruff check 통과, ruff format --check 통과(197 files), git diff --check 통과.

## 남은 검증

실제 Critic 유료 호출, 선택 경로의 웜 ERP 실행 및 호출/시간 측정은 수행하지 않았다. 기존 미커밋 변경은 보존했으며 이번 작업도 커밋하지 않았다.
