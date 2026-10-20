# 2단계: 거절 증거 보존과 승인 전 분류

2026-10-20. 현행 운영 지침이 아닌 감사 기록이다. 2-1 계측과 2-2 분류까지만 수행한다.
2-3 경고 전환, 2-4 화면 판정 통일, 3단계 메모 병합은 적용하지 않았다.
실제 ERP·유료 API·새 파일럿은 실행하지 않았다.

## 시작 전 네 항목 재검증

### 경로 동등성 테스트의 민감도

`tests/test_observation_path_equivalence.py::test_baseline_detects_disabled_memory_update`에서
아래 함수만 monkeypatch했다. 제품 파일을 바꾸지 않은 상태에서 먼저 5건 통과를 확인했다.
이후 비교가 다른 사전 단언에 가려지지 않도록, 변이 실행에서는 메모를 반환한 뒤
`semantic memory differs` 비교 자체가 실패해야 통과하도록 강화했다.
무력화 함수가 실제 호출됐다는 단언도 있다.

| 경로 | 무력화한 갱신점 | 확인 결과 |
|---|---|---|
| 일반 행동 | `Brain._record_action_verification` | 메모 비교 실패 감지 |
| 필드 배치 | `Brain._remember_action_effect` | 메모 비교 실패 감지 |
| 빈 행동 보완 | `Brain._remember_model_task_evidence` | 메모 비교 실패 감지 |
| 동일 캡처 | 두 번째 관찰의 `VisualPerception._apply_field_values` | 메모 비교 실패 감지 |
| 콜드→화면 캐시 웜 | `Brain._remember_model_task_evidence` | 메모 비교 실패 감지 |

5개 모두 1차 무력화 검사에서 실패를 감지했다. 테스트가 검증하는 것은 이 합성 입력의 메모
동등성이며, 모든 상태 조합이나 실제 화면 성공을 증명하지는 않는다.

### 편찬 웜 xfail의 출처와 실제 기록

실제 경로는 `brain/demo_learning/warm_methods.py`의
`main → read_warm_candidates → assemble_warm_method → compile_warm_method`다.
`read_warm_candidates`는 성공 결과·교육 digest·승격 캐시를 읽고,
`screen_plan_proposed`와 **실제로 delivered인 전이**를 관찰 ID/순서로 연결한다.
이미 충족된 입력은 `already_satisfied`로 분리하며, 전달된 행동이 없으면 후보로 만들지 않는다.

새 xfail은 `WarmCandidate`를 직접 만들고 `compile_warm_method`를 호출한다. 입력 칸에
이미 같은 값이 보이는데도 fill 후보를 만들며, 실제 전달 이력 선별을 우회한다.
따라서 **현재 확인 수준은 합성 재현이며, 실제 입력으로 발생한다고 확정할 수 없다.**
xfail 설명도 이를 명시하도록 정정했다.

071·072의 보존된 `run_log.jsonl`과 `observations/*.json`을 obs_id로 대조했다.

| 실행 | 대조한 제안 | method_bound | 장면에서 evidence 대상이 안 보인 제안 |
|---|---:|---:|---:|
| 071 seed003 cold | 19 | 0 | 2 |
| 072 seed000 cold | 15 | 0 | 1 |
| 072 seed000 warm | 15 | 1 | 1 |
| 072 seed001 cold | 14 | 0 | 1 |
| 072 seed001 warm | 2 | 1 | 0 |
| 072 seed002 cold | 13 | 0 | 2 |
| 합계 | 78 | 2 | 7 |

7건은 모두 `method_id` 없는 제안이다. 저장된 관찰이 선택 전후 어느 시점을 반영하는지도
영향을 주므로 이 숫자를 결함 건수로 해석하지 않는다. method_bound 2건은 로그인 구간이다.
따라서 **같은 편찬 웜 입력값/증거 ID 분리로 메모가 사라졌다는 운영 사례는 미확인**이다.

bench `evidence/seed-audit-20261017-072/seed-*/compilation*/assembly-input.json`도 확인했다.
seed000의 compilation/compilation-trace-fixed 후보는 각각 13개, seed001은 13개,
seed002는 12개다. 행동과 증거의 ID가 다른 후보는 있지만, 같은 `source`/`field`에 대해
다른 target ID를 갖는 후보는 이 네 입력에서 0개였다. 버튼 행동과 입력값 증거의 ID가
다른 것은 정상일 수 있다. 입력 수집 이후 웜 재결합에서 새 ID가 생기는지는 별도 문제다.

### 확신도 변형의 범위

1단계 변형은 보조 객체의 `1.0 → 0.95`였다. 기존 supporting-role 변형에는 0.1도 있으나
행동 소유 객체의 확신도는 1.0이다. **행동 대상 0.6을 거절하는 구간을 피한 테스트였다.**
`test_low_confidence_action_target_is_rejected_before_execution`의 기존 0.6 거절 단언은
그대로 유지하고, 이번에는 거절 응답·이유가 저장되는지만 추가 확인했다.

### 두 xfail은 별개로 유지

| 항목 | 실제 로컬 재현의 차이 | 판단 |
|---|---|---|
| 기존 `test_compiled_coordinates_reach_executor_without_model[True]` | 재관찰 후 선택에서 행동 ID 4·5가 6·7로 다시 생성되지만 카탈로그는 0..5. `visual_proposal_unknown_marker` | 실행 대상 재결합/카탈로그 불일치 |
| 새 `cold-to-warm-compiled` | 최초 재결합의 행동 3, 같은 입력값 증거 4. 장면에는 3만 남아 메모가 비어 있음 | 합성 입력의 증거 투영 유실; 실제 발생 미확인 |

둘 다 `brain/cache/methods.py::_coordinate_bindings`의 max(marker ID)+1 할당을 거치지만,
기존 사례는 재관찰/선택 시 재할당이고 새 사례는 `bind_visible_value_evidence`의 별도 할당이다.
공통 할당 함수를 사용한다는 사실만으로 동일 원인·동일 수정이라고 결론내리지 않는다.

## 2-1 구현 경계와 보존 내용

| 소유 파일 | 변경 이유와 보존 내용 |
|---|---|
| `sawon_runtime/storage.py` | 기존 RunLog/atomic JSON을 사용한 best-effort 진단 함수와 예외 관찰 scope. 예외를 그대로 재발생시키며 분기·재시도 규칙은 만들지 않음 |
| `models/base.py` | 모든 ProviderReply.text 원문을 private JSON에 저장. 파싱 거절 시 모델 스키마 식별자와 기존 validation_errors 연결. 성공 후 로컬 거절도 추적할 수 있도록 성공 응답도 저장 |
| `brain/graph/matching.py` | 계획 검증·보완·전체 매칭 실패의 응답 객체/문맥/trace 보존. 확신도 0.6 및 충분한 증거의 stop 포함 |
| `brain/graph/engine.py` | 선택·관찰·검토·실행 예외와 예외 없이 retry/blocked로 가는 경계 보존 |
| `brain/demo_learning/warm_methods.py`, `methods.py` | 편찬/학습의 로컬 변환 거절 보존. 앵커 8개 초과는 공용 모델 파싱에서 원문 보존 |
| `brain/visual/perception.py` | 복구되는 grounding 거절도 기록. pHash 또는 로컬 재결합 거절 시 실제 비교 캡처 두 장, 해시·거리·기준 보존 |
| `pipeline/runner.py` | 문서 추출 후 로컬 검증 실패의 trace 보존. 원문은 공용 모델 파일, 문서 규칙 결과는 기존 checks.json에 남음 |
| bench `relay/service.py`, `relay/api.py` | 단일/배치 실행 거절과 HTTP 요청 스키마 오류의 원문·이유. 치수 거절 시 이미 비교에 쓴 두 캡처 보존. 추가 캡처/입력 없음 |
| `models/README.md`, `docs/contracts.md` | 새 private 진단 산출물의 소유자·형식·한계 명시 |

core: `case/diagnostics/model-<call_id>.json`의 `text`가 공급자 원문이다.
`verify/check=rejection_evidence`가 가리키는 파일의 `response`는 해당 경계의 **정규화된 객체**다.
둘을 혼동하지 않는다. 원문은 기존 model_call의 call_id/context, 매칭 decision_id/프레임과
이벤트 순서로 추적한다. 캐시/메서드 경로에는 새로운 공급자 원문이 존재하지 않는다.
한 예외가 내부 거절과 외곽 종료 경계를 통과하면 기록이 여러 개일 수 있다. 기록 개수를
고유 거절 수나 모델 호출 수로 집계하면 안 된다.

bench: relay artifact 디렉터리의 `diagnostics/<id>/rejection.json`.
HTTP 응답은 계속 일반화된 422를 반환하며, 요청 원문을 외부로 반사하지 않는다.
relay는 현재 픽셀 동일성도 pHash도 실행 게이트로 사용하지 않는다. 치수 비교 캡처의
`phash=null`, `phash_reason=relay_does_not_compare_phash`는 실제 상태를 표시한다.
core의 pHash를 relay에 도입하거나 새 해시 구현을 복제하지 않았다. 2-4 승인 전까지
relay에 **판정에 쓰인 pHash 수치**를 제공할 수 없다.

한계: SDK가 ProviderReply를 반환하기 전에 실패하면 응답 문자열 자체가 없어 원문은
미확보다. 진단 저장 실패 시 경고와 가능한 이벤트만 남는다. 디스크 실패에도 모든 증거가
반드시 남는다고 보장하지 않으며, 이를 위해 기존 결과를 바꾸지도 않는다.

## 2-2 검증 분류표 — 승인 전

정적 검색 범위는 core의 brain/models/pipeline/sawon_runtime 및 bench의 relay/공개 계약이다.
72개 파일의 raise, 제약 있는 Field, 문서 Check, 그래프 결과 호출 730개를
[위치·식·분류 CSV](rejection-sites-20261020.csv)에 수록했다. **730개가 독립 거절 조건이라는
뜻은 아니다.** 재전파·정상 종료·환경 설정도 검색된다. CSV의 nearest_condition은 가장 가까운
문법적 if이며 전체 실행 경로의 논리식은 아니다. 타입/enum/extra 계약은 아래 공용 Pydantic
경계에 포함한다. 보류 행은 승인된 완화 대상이 아니다.

| 검증 조건 | 코드 위치/함수 | 거절 동작 | 분류 | 근거 |
|---|---|---|---|---|
| 모델 JSON, 필수 타입, intent/action enum | `models/base.py::complete_json`; `graph/schemas.py`, `matching.py`, `screen_mapping.py` 등 요청 모델 | ValidationError, 기존 복구/종료 | 실행 필드는 엄격 유지 | 없는 행동·출처·좌표를 실행 가능한 값으로 만들면 안 됨 |
| purpose/subgoal/설명 160, expected_result 240, reason 등의 길이 | `matching.py` 모델·`prepare_match_plan`; `schemas.py`; `screen_batch.py`; `scene.py`; `demo_learning/{methods,warm_methods}.py` | 파싱 거절 또는 이미 존재하는 자르기 | 경고 전환 후보 | 입력값이 아닌 설명문. 현재 자르는 경로와 아직 거절하는 스키마가 공존 |
| 편찬 context_anchors 8개 | `warm_methods.py:36` | draft 저장 전 모델 파싱 실패 | 경고 전환 후보 | 보조 설명 앵커이며 `MethodActionGroup.context_anchors` 자체에는 8개 제한이 없음 |
| 행동 대상 확신도 threshold 미달 | `matching.py::_validate_action_targets_executable`; `scene.py::bind_scene` | 엄격 모드 거절 / 비활성 control 생성 | 경고 전환 후보 | 실행 좌표가 같아도 모델 자기평가만으로 바뀜. observe_only와 argmax 예외는 보존 |
| OCR 확신도·문서 최종 규칙 확신도 | `scene.py::bind_scene`, `pipeline/checks.py` | 값 비움/입력 차단 | 보류 | 모델 자기평가와 다름. 실제 OCR 근거가 사라지는지 함께 판단해야 함 |
| 충분한 완료 증거인데 stop | `matching.py::_validate_step_contract` | `screen_match_completion_decision_required`, observe_only에서도 거절 | 보류 | 새 액션 정확성보다 종료 정책. 경고로 바꾸면 모델 stop과 성공 종료 의미도 결정해야 함 |
| 047의 객체 전체 동등성 | 현재 `matching.py::retain_action_targets`, `ScreenMatch.collect_action_bindings` | 과거 전체 객체 비교는 제거됨; 현재 steps 보존 단언 | 과거 조건은 삭제 상태 유지 | 설명/확신도 동일성을 되살리지 않음. 실행 steps 자체 보존은 별개 |
| 060의 160자 거절 | `matching.py::prepare_match_plan` 및 위 스키마 | 현재 일부는 `[:160]`, 다른 타입은 파싱 거절 | 경고 전환 후보 | 과거 증상과 현행 진입 스키마를 구분. 전역 길이 제한을 풀지는 않음 |
| 072의 relay 픽셀 일치 | bench `service.py::_visual_fresh`, `capture_compare.py` | 정확한 픽셀 비교 거절은 이미 제거됨 | 삭제 상태 유지 | 비교 해시는 관찰 메타데이터에 남지만 실행 조건이 아님 |
| core 실행 직전 pHash 거리/재결합 | `perception.py::rebind_action`, `action_rebind.py`, `action_preparation.py::_fresh_visual_target` | 다른 장면/대상 불명확 시 거절 | 2-4 보류 | 현재 거리 기준 16. 메서드 캐시 기본 6과 실행 직전 16은 용도가 다름 |
| 메서드 후보 pHash·해상도 부적합 | `cache/methods.py::_coordinate_bindings` | None→캐시 미스/모델 폴백 | 거절과 구분 | 모델 응답이나 실행 요청을 거절하는 사건이 아님 |
| 좌표 범위, 필수 point, 실제 행동 target/source | `visual_points.py`, `action_preparation.py`, `screen_mapping.py`, `marker_contract.py` | GuardStop/ValueError/KeyError | 엄격 유지 | 실행 대상·입력값이 달라질 수 있음 |
| 보조 증거 target가 scene에 없음 | `engine.py::_selection_call`, `compile_match`; control_cleanup 테스트 | 정상 save 행동까지 unknown_marker로 거절 | **애매함·보류** | 증거 보존과 행동 실행 결합 문제. 행동 target가 틀린 경우와 일괄 분류하지 않음 |
| 교육 단계·행동 intent·입력 표면의 제약 | matching의 `_validate_taught_step`, `_validate_action_boundary`, `_validate_input_scope`, `enforce_taught_input_surfaces`; `action_preparation.py` | 엄격 모드는 거절, observe_only는 일부 관찰 | 보류 | 실행 입력 출처와 교육 의미의 추정이 섞임. 보조 필드 일괄 완화로 처리 불가 |
| 이미 공급된 교육의 재조회 | `matching.py::select_match_response` | 현재는 supplied 요청을 무시하고 기존 행동 유지 | 유지 | 이미 1회 호출. 누락된 교육을 요청할 때만 기존 추가 호출 |
| 화면 계획/로그인 배치/완료 계약 결합 | `screen_batch.py`, `compile_match`, `schemas.py` | 중복 assignment, 혼합 경계, proof+action 거절/정규화 | 항목별 보류 | 중복 입력 정확성과 배치 분할 정책은 같은 의미가 아님 |
| 업무 완료의 값 불일치·같은 ref 재사용 | `completion.py` | 재관찰 또는 GuardStop | 엄격 유지 | 다른 값 또는 한 칸을 여러 값의 증거로 쓰는 오류 |
| 완료 증거 부족·저장 메시지/repair/다음 행동 일관성 | `completion.py`, `save_review.py`, engine `_grid`/`_check_complete` | 재시도/중단 | 보류 | 실제 미관찰인지 메모/증거 투영 손실인지 분리 필요 |
| 메서드 후보 ID·순서·필수 입력·참조 이미지 | `warm_methods.py::compile_warm_method`, `cache/methods.py` | 편찬 중단 | 엄격 유지 | 성공 기록에 없는 실행을 생성하거나 필요한 입력을 빠뜨릴 수 있음 |
| relay 대상/창/포커스/capture/중복 요청·배치 일관성 | bench `service.py`, `interfaces/.../contracts.py` | 전달 전 실패 또는 배치 prefix만 반환 | 엄격 유지 | 실행 정확성 경계. 새 거절 조건 추가 없음 |
| 문서 값/단위/OCR 출처·상충 | `pipeline/extract.py`, `normalize.py`, `checks.py` | 준비 case 실패/검토 대기 | 엄격 유지, 확신도는 위 보류 | 실제로 ERP에 쓸 값과 출처를 바꾸는 문제 |
| 비용/환경/도구 설정·로그 무결성 | 모델·CLI·worker·storage 설정 경계 | 기존 중단 | 이번 완화 대상 외 | 2-3의 설명/보조 필드 정책과 구분 |

## 기존 테스트 검색 확대

core `tests/**/*.py` 전체를 검색했다. 테스트를 삭제하거나 기대 행동을 완화하지 않았다.

| 테스트 | 고정한 동작 | 판단 |
|---|---|---|
| `test_screen_matching.py::test_low_confidence_action_target_is_rejected_before_execution` | 실행 대상 0.6 거절 | 1단계 후보 1. 경고 전환 승인 후 기대 변경 후보 |
| `...::test_identical_rematch_reuses_grounded_interpretation` | 동일 캡처에서 관찰+선택으로 2호출 | 1단계 후보 2. 3단계 문맥/상태 병합 검토, 검증 정책 변경으로 해결하지 않음 |
| `...::test_stop_is_rejected_when_current_review_has_all_required_evidence` | 충분한 증거의 stop 거절 | 1단계 후보 3. 종료 의미 보류 |
| `...::test_control_cleanup_preserves_current_evidence_regardless_of_prior_verification` | 보조 evidence31 때문에 save34도 거절 | 1단계 후보 4. **애매함**, 임의로 엄격 유지/경고 전환에 배정하지 않음 |
| `test_visual_scene.py::test_final_grid_requires_unambiguous_structured_evidence` | 현재 grid 불확실성으로 `_grid` 거절 | 보조 warning과 결과 식별이 결합되어 보류 |
| `test_visual_scene.py::test_uncertain_ocr_is_not_value_evidence` | 낮은 OCR 확신도의 값을 증거로 쓰지 않음 | 모델 confidence와 별개, 보류 |
| `test_visual_proposal.py::test_current_policy_rejects_wrong_phase_action_and_unknown_marker` | phase 제약과 없는 실행 marker를 한 테스트에서 검사 | 없는 행동 marker는 엄격, phase 정책은 별도 검토 |
| `test_marker_contract.py::test_unknown_and_nontext_references_report_exact_field` | 잘못된 OCR 링크 필드·ID를 식별 | 대상 연결 정확성 유지; 보조 링크만 잘못됐을 때 행동 전체 거절은 별도 판단 |
| `test_warm_method_compiler.py::test_warm_compiler_rejects_missing_input` | 편찬에 필요한 입력/참조 캡처 누락 거절 | 실제 실행 정보 누락이므로 유지 |
| `test_pipeline.py`의 3 prompts 단언 | 서로 다른 문서 3개 추출 | 같은 화면/같은 교육 재호출이 아님 |

`test_screen_matching.py` 밖에서 같은 화면의 공급된 교육을 반드시 재조회해야 한다는
동일한 단언은 찾지 못했다. 파일 내에는 빈 행동 보완·새 교육 조회·다음 관찰 등 2호출
단언이 남아 있으므로 호출 수만 보고 중복으로 삭제하지 않았다.

## 검증·반증·승인 지점

- 진단 코드를 제거한 AST를 비교하면 matching/engine/warm_methods의 기존 결정 로직과
  동일했다. 프롬프트 문자열, 모델·설정·스키마·캐시 판정 기준은 변경하지 않았다.
- 메모 무력화 5경로, 저장 실패 3조건, 앵커9개 저장 정상/실패 2조건, 정상 응답+저장 실패
  1조건, pHash 거절 1조건, 기존 확신도/stop 거절 2조건을 직접 확인한다.
- bench는 치수 거절 시 캡처 1회·입력 0회, 저장 실패 뒤 다음 정상 입력 성공,
  422 원문 비공개 저장을 fake desktop으로 확인한다.
- 해제한 거절 분기 **0개**. 두 xfail 유지. 실제 파일럿 실행 **0회**, 유료 호출 **0회**.
- 반증 조건: 계측 유무에 따라 반환값·예외 객체·후속 모델/relay 호출이 달라지거나, 저장된
  캡처가 실제 비교본과 다르거나, 기존 원문이 보존되지 않으면 이 계측은 잘못된 것이다.
- 확인한 것은 합성 회귀와 로컬 코드/보존 로그의 관계다. 071/072의 실제 편찬 메모 손실,
  다음 파일럿의 성공률·속도 개선은 미확인이다. 테스트 실행 시간 차이로 성능 효과를 주장하지 않는다.
- 다음 승인: 2-3의 **경고 전환 후보만** 처리할 범위, 보류 항목의 별도 결정,
  2-4에서 공유할 실행 직전 화면 판정 함수/기준. 이 승인 전에는 계속 멈춘다.

## 실행 완료 기록

각 저장소 루트에서 자체 `.venv` Python으로 실행했다.

| 명령 | core | bench |
|---|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q --tb=short` | **663 passed, 2 xfailed, 41.96초** | **591 passed, 6 deselected, 15.83초** |
| `.\.venv\Scripts\python.exe -m ruff check .` | 통과 | 통과 |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 217파일 통과 | 158파일 통과 |
| `git diff --check` | 통과 | 통과 |

bench의 기존 Starlette/httpx TestClient deprecation warning 1건은 남아 있다.
core 임시 분석 스크립트가 최초 Ruff 검사에 포함되어 실패했으며, 이번 작업에서 만든
해당 파일만 제거한 뒤 lint/format을 다시 통과시켰다. 기존 tmp 자료는 삭제하지 않았다.
일반 테스트는 fake/replay만 사용했다. 진단 저장의 추가 I/O는 있으며, 이 측정만으로
운영 지연이 늘지 않는다고 주장하지 않는다.

민감도/합성 xfail 출처 정정 커밋: `8218bc7`. bench 거절 계측 커밋: `21157d0`.
core 거절 계측·분류는 이 보고서를 포함하는 별도 커밋으로 기록한다.
