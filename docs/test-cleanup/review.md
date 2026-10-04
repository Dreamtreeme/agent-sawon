# 테스트 전수 검토

검토일: 2026-10-05

## 방법

- `tests/test_*.py` 53개에서 모든 `test_*` 함수, parametrization, assertion과
  `pytest.raises` 사용을 AST로 목록화했다.
- 기준선 JUnit의 855개 case와 정리 후 JUnit의 846개 case를 파일·테스트 이름으로 대조했다.
- 느린 테스트는 전체 본문과 호출하는 production 경계를 읽고, 같은 동작을 남겨 두는 대체
  테스트가 특정되는 경우에만 삭제 또는 하위 계층 이동 대상으로 정했다.
- production import와 factory를 검색해 비교 프로필의 실제 도달 가능성을 확인했다.

## 전수 범위와 처분

아래 파일의 모든 테스트를 검토했다. 별도 표시가 없는 파일은 서로 다른 실패 경계, 보안 계약,
영속성 규칙 또는 외부 관찰 결과를 검증하므로 유지한다.

### 실행 그래프와 업무 상태

- `test_brain.py`, `test_brain_http.py`, `test_credential_progress.py`
- `test_decision_policy.py`, `test_input_target_recovery.py`
- `test_navigation_progress.py` — 의미 변경 세부 검증을 단위 계층으로 이동
- `test_policy_workflow.py`, `test_prepared_brain.py`, `test_reconciliation.py`
- `test_saved_evidence.py`, `test_screen_batch.py`, `test_screen_matching.py`
- `test_task_instruction.py`, `test_transition_contract.py`
- `test_worker_context.py`, `test_worker_state_contract.py`

### 캐시, 시연과 학습 방법

- `test_action_rebind.py`, `test_cache_experiment.py`, `test_cache_rebinding.py`
- `test_demo_learning.py`
- `test_graph_cache_failure_recovery.py` — cold 준비를 공유하도록 통합
- `test_graph_cache_rebinding.py` — cold 준비를 공유하도록 통합
- `test_method_learning.py`, `test_method_runtime.py`, `test_methods.py`
- `test_semantic_cache.py`
- `test_shared_graph_cache_integration.py` — 더 강한 중복 1개 삭제
- `test_visual_cache_demo.py`, `test_visual_match_cache.py`

### 시각 관찰과 좌표 실행

- `test_click_refinement.py`, `test_marker_contract.py`, `test_ocr_preprocessing.py`
- `test_visual_markers.py`, `test_visual_points.py`, `test_visual_proposal.py`
- `test_visual_rectangles.py`, `test_visual_relay_boundary.py`, `test_visual_scene.py`
- `test_visual_workflow.py` — 오류 allowlist 세부 검증을 단위 계층으로 이동

### 모델, wire, 저장과 작업자

- `test_atomic_replace_retry.py`, `test_models.py`, `test_openai_client.py`
- `test_persistent_worker.py`, `test_storage.py`, `test_validation_metadata.py`
- `test_wire_contract.py`
- `test_jev_matching.py` — Jev는 `models.factory`와 비교 설정에서 여전히 도달 가능하므로 유지

### 문서 추출 파이프라인

- `test_pipeline.py`, `test_pipeline_adapters.py`, `test_pipeline_evaluate.py`
- `test_pipeline_measure.py`, `test_pipeline_workflow.py`

## 유형별 결과

1. **중복 테스트**: 한 개를 삭제했다. 경로와 대체 테스트는 `decisions.md` 단위 1에 기록했다.
2. **구현 세부 사항에 결합된 테스트**: 후보는 있었지만 외부 감사 계약을 함께 검증해 보류했다.
3. **약한 검증 테스트**: 없음. AST상 assertion 0개로 나온 아래 6개는 모두
   `assert_contract`가 JSON schema, Pydantic validation, runtime policy를 assertion한다.
   - `test_credential_input_is_never_offered_on_other_claimed_screens`
   - `test_query_input_then_search_have_disjoint_preconditions`
   - `test_field_readback_removes_verified_sources_and_prevents_early_save`
   - `test_partner_selection_and_search_remain_available_during_unfinished_entry`
   - `test_save_confirmation_requires_a_prior_save_and_a_dialog_activation`
   - `test_broad_action_types_cannot_bypass_intent_or_source_contract`
4. **flaky 테스트**: 없음. 기준선 전체 실행 3회가 모두 855 passed였다.
5. **폐기된 기능의 테스트**: 없음. Jev와 Gemini 비교 경로, UIA 보조 경로, legacy wire 호환은
   production factory·설정·호환 계약에서 여전히 도달 가능하다.
6. **가치가 낮은 테스트**: 확실한 삭제 후보 없음. 짧은 schema/bounds 테스트는 좌표, 비밀,
   비용, 상태 전이의 fail-closed 경계를 검증하므로 유지했다.

## 보류 후보

- `test_visual_workflow.py::test_caret_recheck_uses_fresh_evidence_without_cleaning_values`
  - 네 변형이 같은 전체 업무 준비를 반복하지만 blur 활성화, 잘못된 값, focus 미이동, 기능 비활성화가
    `_verify_node`의 서로 다른 상태 결합을 통과한다. 같은 결함 탐지력을 가진 독립 단위 경계를
    만들지 않고 삭제하면 입력 검증 회귀를 놓칠 수 있어 보류한다.
- `test_input_target_recovery.py::test_rejected_query_or_editor_target_can_recover_to_full_success`
  - assertion은 같지만 query와 editor가 서로 다른 phase·policy·field state를 사용한다. query의
    재선택 세부 테스트가 별도로 있어도 양쪽의 최종 성공을 대체하지 못하므로 보류한다.
- `test_prepared_brain.py::test_prepared_case_finishes_and_appends_to_existing_log`
  - engine/CLI 두 변형은 결과가 비슷하지만 CLI가 argument parsing, factory, 상태 파일을 추가로
    검증한다. 두 public entry point가 남아 있어 보류한다.
- `test_semantic_cache.py::test_corrupt_cache_preserved`와
  `test_shared_graph_cache_integration.py::test_corrupt_cache_preserved_while_model_finishes_task`
  - 전자는 저장소 fail-closed, 후자는 손상 캐시 뒤 전체 업무 fallback 성공을 검증한다. 계층이
    달라 중복으로 삭제하지 않는다.
- prompt/event 순서를 직접 보는 `test_screen_matching.py`, `test_worker_context.py`,
  `test_graph_cache_*` 일부 테스트
  - 일반 구현 순서라면 재작성 후보지만, 이 저장소에서는 모델에 전달되는 최소 정보, 측정 호출 수,
    append-only 감사 이벤트가 외부 계약이다. 동등한 공개 관찰 기준 없이 삭제하지 않는다.

## 자동 감사 결과

- 정확히 같은 AST 본문: 0개
- 정리 후 테스트 함수: 532개
- 정리 후 수집 case: 846개
- assertion 없는 실제 테스트: 0개
