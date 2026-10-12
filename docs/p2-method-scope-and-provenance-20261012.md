# P2 회사별 메서드 범위와 재사용 근거

## 결론

`MethodDocument`는 이미 별도 DSL을 추가하지 않아도 사용자 교육에서 모델 판단, 실행,
현재 화면 재결합, 성공 후 승격까지 같은 slot/target/action/group 표현을 사용한다. 런타임은
메서드의 `task_objective`와 사용자 작업지시서 원본의 SHA-256이 모두 일치할 때만 자동
재생한다. 입력값은 이 범위 키에 포함하지 않으므로 같은 교육에서 다른 업무 값은 재사용할 수
있고, 화면 좌표는 현재 OCR/OmniParser 증거로 다시 결합한다.

이번 단계에서는 새 상태 머신이나 생성 코드를 만들지 않았다. 확인된 결함 하나와 재사용
설명 가능성만 보완했다.

## 확인된 결함과 수정

`MethodLibrary.upsert()`는 같은 `method.id`가 있으면 기존 메서드와 새 action group을
병합했다. 자동 생성 런타임 ID는 교육 해시를 포함하므로 일반 실행에서는 충돌 가능성이
낮지만, 웜 컴파일러의 명시 ID나 수동 반입 자료가 다른 교육 계약에서 같은 ID를 쓰면 서로
다른 회사·업무의 메서드가 조용히 섞일 수 있었다.

이제 같은 ID의 기존 메서드와 새 메서드가 `task_contract_sha256` 또는
`task_objective`에서 다르면 `method_id_task_scope_collision`으로 거절한다. 기존 라이브러리는
변경하지 않으며, 충돌 없는 같은 범위의 성공 리비전 병합만 계속 허용한다.

메서드 적중 로그에는 다음을 추가했다.

- 작업지시서 계약 SHA-256
- `runtime_trace`/`warm_compiled` 출처 종류
- 리비전
- 성공 행동을 만든 원 실행 참조(`source_transitions`)

`method-learning-result.json`에도 현재 교육 계약과 설치 후보별 같은 provenance를 기록한다.
따라서 어떤 교육의 어떤 성공 기록을 왜 재사용했는지 실행 로그만으로 추적할 수 있다.

## 기존 재사용 계약 확인

| 구분 | 현재 동작 | 검증 |
|---|---|---|
| 같은 교육, 다른 입력값 | slot을 새 record key/value에 연결 | `test_learned_method_uses_current_scene_and_executor_without_model` |
| 교육 파일 변경 | 원본 바이트 SHA-256 불일치로 후보 제외 | `test_learned_method_is_scoped_to_exact_user_education_file` |
| 화면 위치 변경 | 현재 OCR/마커의 라벨 관계로 target 재결합 | `test_learned_method_rebinds_new_values_keys_and_current_geometry` |
| 재결합 실패 | 해당 실행에서 메서드를 막고 모델 판단으로 폴백 | `test_rejected_method_edge_is_blocked_before_delivery` 계열 |
| 승격 | 전체 업무 성공일 때만 라이브러리에 설치 | `test_successful_candidate_installs_versioned_live_library` |
| 다른 교육의 동일 ID | 조용한 병합을 거절하고 기존 자료 보존 | `test_library_rejects_same_method_id_from_another_task_contract` |

로컬 과거 `methods-working.json` 45개 파일의 메서드 48개를 점검했을 때 동일 ID가 서로 다른
교육 해시에 걸친 사례는 0개였다. 교육 범위 필드가 없는 구형 메서드 24개는 현재의 exact
scope 검사에서 자동 재생되지 않는다. 이를 새 형식으로 추정 변환하지 않는다.

## 검증 결과

- 변경 영역: 31 passed, 3.46초
- 전체: 640 passed, 64.74초
- Ruff lint: 통과
- Ruff format check: 185 files already formatted
- `git diff --check`: 통과

이 단계는 저장 형식과 로그의 경계를 검증한다. 실제 ERP 성공률이나 실행시간 개선을 주장하지
않는다. 다음 실제 화면 검증은 같은 교육·새 업무 값에서 메서드 적중 로그의 scope/provenance와
폴백 여부를 대조하고, 다른 회사 교육에서는 같은 라이브러리를 주어도 후보가 노출되지 않는지
확인하는 두 조건으로 나눈다.

## 실제 화면 검증 051–053

첫 051은 `compiled_only=true`였지만 저장소의 메서드가 `runtime_trace`라 메서드 적중이
0회였다. visual cache 15회와 모델 경로로 ERP 10/10에 성공했지만 메서드 범위 검증에서
제외한다.

격리한 052/053은 seed23 메서드 원본을 복사해 고정하고 visual cache를 끈 뒤
`compiled_only=false`로 실행했다.

| 실행 | 교육 | seed23 method bound | 결과 | Haiku/local | 비용 |
|---|---|---:|---|---:|---:|
| 052 | company-023, 같은 SHA | 9 | stopped, 저장 전 0/10 | 3/10 | $0.0085978 |
| 053 | company-019, 다른 SHA | 0 | success, 10/10 | 18/16 | $0.0473155 |

052의 9개 적중에는 계약 SHA, `runtime_trace`, revision 3, 원 실행 참조가 모두 기록됐다.
로그인부터 기본 필드 입력과 추가정보 탭 이동까지 현재 화면에 재결합했다. 저장까지 가지 못한
직접 원인은 추가정보 입력 폴백 모델 계획이 `selection_error:action_target_semantics_missing`로
거절된 것이다. 이는 범위 혼합이 아니라 불완전한 메서드 이후 모델 폴백 계약의 별도 결함이다.

053은 같은 seed23 메서드 파일을 읽었지만 company-019 교육 SHA가 달라 `method_bound`와
`selection_source=method`가 모두 0이었다. 모델 프롬프트는 기존 `prompt_context()`의 exact
scope 필터로 seed23 메서드를 받지 않았고 콜드 모델 경로로 ERP 10/10을 완료했다.

실행 후 중앙 진단 파일 `task-notebook.json`만 전체 라이브러리 catalog를 기록하는 projection
불일치를 발견했다. 모델 입력은 아니었지만 사람의 사후 분석에서 다른 회사 메서드로 오해할 수
있어 `context()`도 같은 교육 목표와 SHA 필터를 사용하도록 통일했다. 관련 146개 및 전체
640개 테스트, Ruff lint/format이 통과했다.
