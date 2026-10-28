# 현행 경로의 중복·명목상 테스트 정리

2026-10-28. 기준 커밋 `2af46c8`, 작업 브랜치 `cleanup/tests-20261028`.
제품 코드, 프롬프트, 설정, 스키마, bench는 변경하지 않았다. 테스트 11개 파일과 보고서·파일별 시간 CSV만
대상이다. 실제 ERP, 네트워크 모델 호출은 실행하지 않았다.

## 측정 결과

| 지표 | 정리 전 | 정리 후 |
|---|---:|---:|
| pytest 수집 사례 | 747 | 727 |
| 통과 / 기존 xfail | 744 / 3 | 724 / 3 |
| 테스트 함수 | 527 | 517 |
| 정적 `assert` 구문 | 2,401 | 2,378 |
| 정적 `pytest.raises` 호출 | 180 | 176 |
| `tests/test_*.py` 줄 수 | 18,421 | 18,194 |
| Statement coverage | 8,641 / 9,792 (88.2455%) | 동일 |
| Branch coverage | 2,562 / 3,314 (77.3084%) | 동일 |
| 일반 실행 2회 | 52.27초, 52.33초 | 46.06초, 44.23초 |
| 일반 실행 2회 중앙값 | 52.30초 | 45.15초 |
| coverage 포함 실행 | 68.73초 | 55.55초 |

통과 사례 20개를 제거·통합했다. 이와 별도로 반복문 안의 중복 입력 10개를 줄였다
(원자적 파일 교체 3개, 진단 메타데이터 4개, 준비된 문서 인계 3개).
반복문 입력은 pytest 사례 수에 중복해서 더하지 않았다. 테스트 파일 수는 51개로 동일하다.
정적 assertion 수는 헬퍼도 포함한 AST 구문 수이며, 실행된 단언 횟수나 결함 탐지율이 아니다.

같은 제품 파일의 실행 라인 집합과 실행 분기 집합을 비교했다. 삭제로 사라진 라인·분기는
각각 0개다. 전체 변이 도구를 돌린 점수는 측정하지 않았으며, 아래에 명시한 결함 8개를
런타임 monkeypatch로 주입해 모두 잔존 테스트가 잡는 것을 확인했다.

기준선과 최종본은 각각 전체 테스트를 3회 확인했다(coverage 1회, 일반 2회).
같은 사례의 통과/실패가 바뀌는 flaky 사례는 없었다. 기준선의 실패는 없고 xfail 3개는
유지했다. 통합 도중 이동한 설명문 단언이 이미 소비된 계획을 참조하는 테스트 오류를 발견해,
관찰 직후·선택 이전으로 옮겼다. 최종 세 실행은 모두 통과했다.

일반 실행 중앙값은 약 7.16초(13.7%) 줄었다. 표본 2회의 로컬 측정이며, CI나 ERP의
실행 시간 개선을 뜻하지 않는다. coverage 실행은 계측 비용이 있으므로 이 중앙값에서 제외했다.
[파일별 실행 시간](runtime-test-pruning-20261028-files.csv)은 JUnit의 setup/call/teardown
시간 합계다. pytest 수집·프로세스 시작 비용은 파일별 합계에 포함되지 않는다.

## 삭제와 단언 이동

경로는 모두 `tests/` 아래다. 삭제 이유는 나이·모델 이름만으로 판단하지 않았다.

| 삭제 대상 | 현재 중복 또는 명목상인 이유 | 남긴 검증 / 옮긴 단언 |
|---|---|---|
| `test_observation_path_equivalence.py::test_baseline_detects_disabled_memory_update`의 `single-action`, `field-batch`, `empty-action-recovery`, `identical-capture`, `model-to-pattern-cache` | 갱신 함수를 고의로 끈 뒤 자기 assertion의 실패를 성공으로 세는 과거 감사용 테스트. 정규 회귀와 같은 경로를 다시 만든다. | 같은 파일의 `test_synthetic_same_facts_reach_same_memory` 6경로 유지. 갱신 누락 변이 5개를 별도 실행하여 검출 확인. 전용 `mutate` 인자·우회 반환·콜백도 삭제. |
| `test_screen_decision.py::test_similar_rematch_uses_fresh_ocr_and_reused_detector_geometry` | 동일 캡처에서 새 OCR/탐지기 재사용/새 계획 선택을 중복 수행한다. | `test_similar_capture_reruns_ocr_but_reuses_detector_geometry`가 새 좌표 실행과 화면 변경 시 재탐지까지 확인. 메모와 재사용 이벤트는 위 동등성 시험, navigation hash는 `test_visual_navigation_ignores_model_catalog_edits_on_identical_pixels`가 검증. |
| `test_screen_decision.py::test_gemini_interprets_and_plans_in_one_image_call` | 이름과 달리 `provider=fake`; Gemini 어댑터를 시험하지 않는다. 관찰→선택→검토 1회 흐름은 이미 검사된다. | 이미지가 실제로 전달됐다는 `images_seen == [True]`를 `test_task_is_loaded_and_new_screen_match_is_one_combined_call`로 이동. source/ref 연결은 `test_named_current_evidence_is_bound_without_a_second_grounding_call` 유지. |
| `test_screen_decision.py::test_save_result_and_proof_use_the_existing_contract` | 저장 확인 행동·증거 참조는 더 구체적인 저장 완료/승인 구분 테스트에 포함된다. | `test_synthetic_save_acknowledgement_normalizes_only_completed_click` 유지. `duplicate_clear`의 grid ref 단언은 `test_final_proof_preserves_grid_row_and_field_evidence`에 이동. |
| `test_screen_decision.py::test_long_semantic_explanation_keeps_plan_and_original_mapping` | 응답을 파싱하고 그대로 돌려주는지만 검사한다. 실제 관찰·wire·메모·원문 artifact를 대조하는 변형 테스트가 있다. | `test_synthetic_semantic_response_variants_preserve_executable_action[long-description]` 유지. 내부 설명 160자 보존 단언을 관찰 직후로 이동; 모델 원문 보존 단언 유지. |
| `test_worker_context.py::test_pending_proposal_does_not_mutate_actual_facts_or_runtime_policy` | query_input 예상 실행에서 실제 메모가 바뀌지 않는 사실을 다른 테스트가 검사한다. | `test_pending_query_input_proposes_search_without_claiming_verification`에 실제 context 전후 동일, policy 불변, pending/required 단언을 이동. 두 phase에서 확인. |
| `test_visual_markers.py::test_semantic_schema_cannot_generate_coordinates` | `SemanticDetection` 좌표 비노출과 bbox 거절이 중복된다. | `test_visual_scene.py::test_provider_schema_is_compact_but_local_limits_remain` 유지. bbox 허용 변이 검출 확인. |
| `test_vocabulary.py::test_key_choice_schema_matches_preconsolidation_json_and_order` | 독립 KeyChoice JSON은 `WireActDecision`의 `$defs.KeyChoice`와 직렬화 순서까지 동일하다. | `test_vocabulary_schemas_match_recorded_json_and_order[WireActDecision-…]`의 원문 fixture+hash 비교 유지. `tab` 삭제 변이 검출 확인. 독립 digest 헬퍼·import 제거; fixture는 삭제하지 않음. |
| `test_models.py::test_replay_config_loads_explicit_fixture_without_credentials` | 같은 config/상대 fixture 경로/factory 호출이 반복된다. | `test_factory_replay_does_not_read_env`가 경로 및 환경 접근 금지, `test_replay_determinism_and_zero_actual_usage`가 비용 0과 응답을 검증. |
| `test_decision_policy.py::test_assert_contract_fails_for_a_contradictory_oracle_expectation` | 올바른 허용 결과에 반대 기대값을 주고 헬퍼 assert가 실패하는지만 시험한다. | 실제 정책의 허용·거절 사례를 검사하는 credential/query/save 테스트와 헬퍼 내부 schema/Pydantic/runtime 비교를 유지. 헬퍼의 양쪽 경로는 실제 사례가 계속 실행. |

`test_screen_decision.py`에서 결과를 받지도 사용하지도 않는 `fixture()[-1]` 호출 5곳도
제거했다. 이 fixture는 파일·공유 상태를 변경하지 않고 OCR/marker/Pydantic 값을 만들기만 한다.

## 매개변수와 반복 입력 축소

| 대상 | 전 → 후 | 이유와 남는 경계 |
|---|---|---|
| `test_action_owned_control_normalizes_once_and_keeps_cache_contract` | 메타데이터 5사례 → 충돌 1사례 | role/button, confidence/0.1, 긴 설명을 한 supporting control에 넣는다. 실행 대상의 id/role/confidence/description/point 및 원문 불변·직렬화 왕복 단언 유지. 기본값 전체와 충돌값 전체를 비교한다. provider 필드 순서는 별도 변형 재생이 검증. |
| `test_complete_perception_uses_marked_image_and_preserves_raw_capture` | valid/corrected/invalid/provider_error → corrected 제거 | corrected와 invalid 모두 첫 응답의 같은 잘못된 text_ids를 반환하고 호출 1회로 끝났다. corrected라는 이름과 달리 두 번째 응답은 실행되지 않았다. invalid의 경고·산출물·단일 호출을 유지. |
| `test_screen_schema_removes_anthropic_optional_grammar_branches` | ScreenMatch/ScreenPlan/SemanticDetection → ScreenMatch/SemanticDetection | 앞 두 타입은 동일 ScreenMappingWire와 null mapping을 사용한다. 클래스별 fixture 비교는 둘 다 유지하며, 서로 다른 SemanticDetection은 유지. |
| 원자적 파일 교체의 exhausted loop | winerror 5/32/33 → 32 | 세 오류의 허용 여부는 transient 테스트가 계속 검사. 시도 6회·동일 예외·원본 보존·임시 파일 제거는 공통 소진 분기이므로 한 대표값으로 검사. |
| 원자적 파일 교체의 즉시 실패 loop | POSIX 권한/disk-full/Windows87 → POSIX 권한/Windows87 | 앞 두 사례는 동일 winerror=None 분기. Windows의 비허용 코드와 POSIX 실패는 구분해 유지. |
| `test_extra_field_names_do_not_enter_locations` | 비공개 키 문자열 3개 → URL 키 1개 | 문자열별 조건이 없는 schema 미등록 키 제거 분기. `loc=[]` 및 키·값 비노출 유지. |
| `test_schema_annotation_values_do_not_authorize_union_branches` | title/examples/default → examples | annotation 안의 `$defs`는 schema의 허용 이름으로 쓰지 않는다. 고정 키만 걷는 구현의 동일 분기이며, annotation 안의 `properties`를 다루는 별도 테스트는 유지. |
| `test_prepared_validator_rejects_each_invalid_handoff_without_mutation` | value/source/missing_hash/blocking/not_ready → blocking/not_ready | wrapper의 readiness/can_enter 분기를 유지. fingerprint의 값·출처 차이와 hash 누락은 `test_pipeline_evaluate.py::test_ready_gate_binds_actual_record_values_and_sources`, `test_ready_gate_rejects_mismatched_confidences_and_checks`에서 계속 검증. |

## 명목상 검사였던 인계 테스트 보강

`test_brain_rechecks_each_independent_prepared_handoff_gate`의 기존 `value` 사례는 값을 바꾼
record를 그대로 파일에도 저장했다. 따라서 `saved_record != supplied_record`는 거짓이고
`can_enter`의 fingerprint 불일치가 실패 원인이었다. 주석이 주장한 저장 record 일치 검증은
실제로 수행하지 않았다.

이 사례를 `saved_record_mismatch`로 교체했다. 원본 record 파일은 유지하고 공급 record만
바꾸되, 검증 보고서 hash는 공급 record에 맞춰 `can_enter=True`를 확인한다. 이제 equality
조건만 해당 입력을 막는다. 정확한 오류 문구·환경 미접근·record/report/state/log 불변을
검사한다. 제품의 조건이나 거절 동작은 바꾸지 않았다.

## 결함 주입 확인

제품 파일은 수정하지 않고 격리된 pytest 프로세스에서 런타임 함수를 monkeypatch했다.
오류는 모두 테스트 본문의 assertion에서 발생했으며 import/setup 실패로 탐지 성공을 세지 않았다.

| 주입한 결함 | 잔존 테스트에서 관찰한 실패 |
|---|---|
| single-action의 `_record_action_verification` 무효화 | 실제 메모의 최근 행동/완료 기록과 기준 메모의 불일치 |
| field-batch의 `_remember_action_effect` 무효화 | 같은 실제 메모 비교에서 행동 기록 유실 |
| empty-action-recovery의 `_remember_model_task_evidence` 무효화 | 관찰한 hbl_no 값이 기억에 없음 |
| model-to-pattern-cache의 같은 기억 갱신 무효화 | 캐시로 받은 관찰의 hbl_no 값이 기억에 없음 |
| identical-capture의 두 번째 `_apply_field_values` 무효화 | 현재 값+bbox 연결 대신 빈 visible_values |
| KeyChoice 타입에서 tab 삭제 | WireActDecision의 JSON fixture 원문 비교 실패 |
| ControlMeaning에서 extra 허용으로 미정의 bbox 수용 | SemanticDetection 검증의 DID NOT RAISE ValidationError |
| 저장 record equality를 강제로 True 처리 | `saved_record_mismatch`에서 DID NOT RAISE ValueError; can_enter는 True |

메모 변이는 `reference`나 학습용 `cold` worker가 아니라 해당 경로의 `current` worker에만
적용했고 각각 실제 무효 함수 호출도 확인했다. 이는 지정한 결함 8개에 대한 탐지 확인이며
가능한 모든 결함의 동등성 증명은 아니다.

로컬 증거는 `.tools/test-cleanup-20261028/`의 `memory-mutation-summary.json`,
`vocabulary-probe-summary.json`, `prepared-mutation-summary.json` 및 각 원문 실행 로그다.
감사용 변이 스크립트는 일반 pytest 수집에 추가하지 않았다.

## 유지·보류

- `synthetic_106` xfail 3개는 유효 관찰이 잘못된 행동/기하 때문에 유실되거나 복구 요청에서
  빠지는 미해결 문제를 나타낸다. 해당 기대 동작을 폐기하라는 지시는 없으므로 유지했다.
- Gemini/OpenAI 어댑터, 내부 `key/pageup/pagedown`, 직접 주입 관찰·편찬 기억 경로는
  여전히 코드가 지원한다. 기본 모델이나 공개 scroll 표현과 다르다는 이유로 삭제하지 않았다.
- `test_worker_graph_and_state_have_four_explicit_responsibilities`와 task instruction의 속성
  부재 검사는 구조 위주지만 대체되는 동일 계약 검증을 확인하지 못했다. 삭제 보류다.
- 모든 남은 테스트가 최소 집합이라는 결론은 내리지 않는다. 이번 삭제는 위에 적은 구체적
  중복·무효 재시도·감사용 자기 검증에 한정한다.

## 실행 명령

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
.\.venv\Scripts\python.exe -m pytest -q --tb=short --cov=brain --cov=models --cov=pipeline --cov=sawon_runtime --cov-branch --cov-report=json:.tools/test-cleanup-20261028/after-coverage.json --cov-report=
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
git diff --check
```

JUnit와 텍스트 로그는 `before-1..3`, `after-1..3`으로 보관했다. coverage 결과는 비공개
로컬 측정 경로에 두었고, 기존에 추적 중이던 루트 `.coverage`는 측정 전 상태로 복원했다.
제품·스키마 fixture 파일에는 변경이 없다.
