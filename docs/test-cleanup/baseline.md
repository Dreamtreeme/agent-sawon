# 테스트 정리 기준선

측정일: 2026-10-05

브랜치: `cleanup/tests-20261005`

대상: `tests/` 전체

목적: 실행 시간과 유지보수 비용 절감. 테스트 수 자체는 목표가 아니다.

## 실행 환경과 명령

프로젝트는 Python 3.13과 pytest 9.1.1을 사용한다. 저장소의 가상환경 실행기는 한글 경로를 `??`로 변환해 직접 시작할 수 없으므로, 동일 가상환경의 site-packages, 형제 bench의 `sawon_interfaces`, pywin32 경로를 Python 3.13에 명시해 실행했다. 실제 측정 명령은 다음과 동등하다.

```powershell
python -m pytest -q --junitxml=<temporary-path>
```

첫 환경 점검은 `sawon_interfaces` 누락으로 수집 오류 49건, 두 번째 점검은 pywin32 DLL 경로 누락으로 419건이 실패했다. 두 실행은 코드 기준선이 아니며 아래 통계와 flaky 판정에서 제외했다. 완성된 동일 환경에서 세 번 다시 실행했다.

## 전체 결과

| 실행 | 결과 | pytest 시간(초) | JUnit suite 시간(초) |
|---|---:|---:|---:|
| 1 | 855 passed | 197.69 | 197.254 |
| 2 | 855 passed | 196.22 | 193.987 |
| 3 | 855 passed | 177.42 | 176.550 |

- 테스트 수: **855개**
- 테스트 파일 수: **53개**
- pytest 평균 실행 시간: **190.44초**
- 기준선 실패: 없음
- flaky 테스트: 없음. 동일 코드의 전체 테스트를 3회 실행했고 세 번 모두 같은 855개가 통과했다.
- 커버리지: 전체 87.63%, 문장 90.24%, 분기 80.14%. 8,741문장 중 7,888줄과
  3,042개 분기 중 2,438개를 실행했다.
- 변이 점수: 정리 대상 중 검증 계층을 바꾼 `brain/observe/progress.py`를
  `tests/test_navigation_progress.py`로 검사한 결과 0%(20개 중 탐지 0개)였다.

최초 기준선 실행 당시에는 측정 도구가 없었다. 사용자가 이후 로컬 설치를 승인해 프로젝트
가상환경에 `coverage` 7.16.2, `pytest-cov` 7.1.0, `mutmut` 3.8.0,
`cosmic-ray` 8.7.0을 설치한 뒤 정리 전 커밋 `b713c70`을 임시 worktree에서 다시 측정했다.
`mutmut`는 네이티브 Windows 실행을 지원하지 않아 동일한 변이 연산을 Windows에서 실행할
수 있는 Cosmic Ray를 사용했다.

## 테스트 파일별 실행 시간

시간은 JUnit testcase 시간을 파일별로 합산했다. 평균 내림차순이다.

| 파일 | 테스트 수 | 1회(초) | 2회(초) | 3회(초) | 평균(초) |
|---|---:|---:|---:|---:|---:|
| `tests/test_graph_cache_rebinding.py` | 5 | 34.06 | 33.133 | 30.052 | 32.415 |
| `tests/test_graph_cache_failure_recovery.py` | 3 | 21.543 | 22.271 | 21.824 | 21.879 |
| `tests/test_shared_graph_cache_integration.py` | 3 | 18.218 | 17.381 | 18.623 | 18.074 |
| `tests/test_visual_workflow.py` | 7 | 13.263 | 12.611 | 12.566 | 12.813 |
| `tests/test_navigation_progress.py` | 15 | 12.59 | 12.458 | 10.599 | 11.882 |
| `tests/test_input_target_recovery.py` | 19 | 12.656 | 11.957 | 9.536 | 11.383 |
| `tests/test_policy_workflow.py` | 8 | 10.797 | 10.768 | 8.474 | 10.013 |
| `tests/test_prepared_cache_workflow.py` | 31 | 10.456 | 10.514 | 8.804 | 9.925 |
| `tests/test_screen_matching.py` | 85 | 7.866 | 7.316 | 6.837 | 7.34 |
| `tests/test_prepared_brain.py` | 12 | 7.529 | 7.614 | 6.479 | 7.207 |
| `tests/test_screen_batch.py` | 19 | 7 | 6.937 | 6.098 | 6.678 |
| `tests/test_cache_experiment.py` | 31 | 5.053 | 5.081 | 4.724 | 4.953 |
| `tests/test_persistent_worker.py` | 11 | 3.499 | 3.631 | 3.567 | 3.566 |
| `tests/test_pipeline.py` | 32 | 3.678 | 3.791 | 2.835 | 3.435 |
| `tests/test_method_runtime.py` | 15 | 3.667 | 3.476 | 2.664 | 3.269 |
| `tests/test_brain.py` | 37 | 2.929 | 2.935 | 2.757 | 2.874 |
| `tests/test_pipeline_measure.py` | 29 | 3.212 | 3.013 | 2.393 | 2.873 |
| `tests/test_credential_progress.py` | 4 | 2.59 | 2.42 | 2.553 | 2.521 |
| `tests/test_brain_http.py` | 5 | 1.736 | 1.728 | 1.701 | 1.722 |
| `tests/test_storage.py` | 12 | 1.326 | 1.405 | 1.372 | 1.368 |
| `tests/test_click_refinement.py` | 13 | 1.38 | 1.547 | 1.061 | 1.329 |
| `tests/test_semantic_cache.py` | 26 | 1.166 | 1.165 | 1.123 | 1.151 |
| `tests/test_decision_policy.py` | 40 | 1.03 | 1.01 | 1.019 | 1.02 |
| `tests/test_visual_markers.py` | 40 | 0.938 | 0.899 | 0.854 | 0.897 |
| `tests/test_jev_matching.py` | 31 | 1.064 | 0.881 | 0.628 | 0.858 |
| `tests/test_models.py` | 33 | 0.784 | 0.673 | 0.589 | 0.682 |
| `tests/test_ocr_preprocessing.py` | 20 | 0.726 | 0.777 | 0.531 | 0.678 |
| `tests/test_cache_rebinding.py` | 21 | 0.439 | 0.504 | 0.394 | 0.446 |
| `tests/test_worker_context.py` | 11 | 0.449 | 0.321 | 0.385 | 0.385 |
| `tests/test_pipeline_workflow.py` | 4 | 0.403 | 0.407 | 0.275 | 0.362 |
| `tests/test_marker_contract.py` | 5 | 0.35 | 0.358 | 0.346 | 0.351 |
| `tests/test_saved_evidence.py` | 26 | 0.285 | 0.294 | 0.171 | 0.25 |
| `tests/test_wire_contract.py` | 33 | 0.281 | 0.232 | 0.233 | 0.249 |
| `tests/test_task_instruction.py` | 15 | 0.171 | 0.215 | 0.193 | 0.193 |
| `tests/test_atomic_replace_retry.py` | 11 | 0.117 | 0.215 | 0.24 | 0.191 |
| `tests/test_action_rebind.py` | 5 | 0.165 | 0.214 | 0.185 | 0.188 |
| `tests/test_openai_client.py` | 8 | 0.195 | 0.132 | 0.165 | 0.164 |
| `tests/test_method_learning.py` | 2 | 0.131 | 0.141 | 0.101 | 0.124 |
| `tests/test_demo_learning.py` | 18 | 0.095 | 0.115 | 0.157 | 0.122 |
| `tests/test_visual_proposal.py` | 5 | 0.075 | 0.115 | 0.091 | 0.094 |
| `tests/test_visual_scene.py` | 20 | 0.078 | 0.089 | 0.098 | 0.088 |
| `tests/test_pipeline_adapters.py` | 9 | 0.071 | 0.106 | 0.082 | 0.086 |
| `tests/test_validation_metadata.py` | 22 | 0.082 | 0.074 | 0.086 | 0.081 |
| `tests/test_visual_relay_boundary.py` | 2 | 0.076 | 0.076 | 0.083 | 0.078 |
| `tests/test_methods.py` | 7 | 0.077 | 0.083 | 0.067 | 0.076 |
| `tests/test_reconciliation.py` | 3 | 0.055 | 0.065 | 0.035 | 0.052 |
| `tests/test_visual_cache_demo.py` | 3 | 0.067 | 0.024 | 0.023 | 0.038 |
| `tests/test_visual_rectangles.py` | 4 | 0.028 | 0.034 | 0.03 | 0.031 |
| `tests/test_visual_match_cache.py` | 2 | 0.03 | 0.023 | 0.037 | 0.03 |
| `tests/test_transition_contract.py` | 21 | 0.021 | 0.023 | 0.021 | 0.022 |
| `tests/test_pipeline_evaluate.py` | 7 | 0.024 | 0.017 | 0.016 | 0.019 |
| `tests/test_worker_state_contract.py` | 1 | 0.008 | 0.004 | 0.005 | 0.006 |
| `tests/test_visual_points.py` | 4 | 0.004 | 0.003 | 0.007 | 0.005 |
