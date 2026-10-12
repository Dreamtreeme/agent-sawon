# 현재 경로 기준 테스트 정리 — 2026-10-12

기준: 72c7f6f, 작업 브랜치 cleanup/tests-20261012. 프로덕션 코드와 실행 설정은 변경하지 않았다.
사용자가 이번 작업 중 BrainSettings 기본값으로 구형 경로를 자동 실행하는 테스트도 삭제하도록 범위를 넓혔다.

## 측정

| 지표 | 전 | 후 |
|---|---:|---:|
| 수집된 테스트 | 657 | 605 |
| 통과 | 657 | 604 |
| 알려진 실패(strict xfail) | 0 | 1 |
| coverage 계측 포함 실행 시간 | 62.21초 | 36.11초 |
| Statement | 90.21% (8250/9145) | 87.49% (8001/9145) |
| Branch | 79.22% (2451/3094) | 74.60% (2308/3094) |

시간은 각각 1회 측정으로 약 42% 감소했으며, 반복 측정 중앙값이 아니다.
coverage는 brain, models, pipeline, sawon_runtime만 집계했다. 첫 JSON 생성은 Python 압축 표준
라이브러리 소스 누락으로 실패하여 --include로 양쪽 모두 동일한 프로젝트 범위를 지정했다.

기존의 0.5%p 감소 제한은 충족하지 않는다. 이번에 명시적으로 삭제하도록 요청한 구형 경로의
검증 범위를 제거했으므로 수치를 숨기거나 전체 결함 탐지 능력을 유지했다고 주장하지 않는다.
특히 graph의 비용 제한, 재선택, 로그인 정책에 대한 구형 전체 실행 검증도 줄었다.
공급자 비용 계측 단위 테스트가 그 그래프 통합 검증을 완전히 대체하는 것은 아니다.

## 삭제·이전 내역

- 구형 자동 그래프/정책 시나리오 48개 케이스(43개 함수) 삭제.
  전체 node ID와 이전 계측은 deleted-legacy-cases-20261012.csv에 기록했다.
- test_policy_workflow.py(6개), test_credential_progress.py(4개)는 파일과 전용 helper까지 삭제.
- test_input_target_recovery.py 14개, test_navigation_progress.py 7개,
  test_brain.py 12개, test_visual_workflow.py 3개, test_prepared_brain.py 1개,
  test_screen_batch.py 1개를 삭제했다.
- 남은 단위 테스트에서 쓰는 FakeRelay, ScriptedModel, NavigationRelay는 유지했다.
  더 이상 소비자가 없는 success_steps/query_steps/run_brain과 탐색·재선택 전용 helper는 제거했다.
- 과거 OCR 상대좌표/앵커 테스트 4개를 현재 저장 좌표 테스트 2개로 통합·이전했다.
  새 입력 source 연결, 정확한 좌표, OCR 없이 재사용, pHash 경계 6/7, 화면 크기 불일치를 검증한다.
- runtime_trace × 분리 호출 옵션의 4개 케이스를 compiled_only 저장 좌표의 첫 관찰/재관찰 2개로 이전했다.
- warm compiler의 좌표 비례 확대 기대를 같은 화면 저장 좌표 기대와 순서 보존으로 교체했다.
  입력 source 누락 오류 검증은 유지하고 이전 context_anchor 강제 오류는 제거했다.

새 좌표 테스트는 예전 상대좌표 포맷 지원을 동일하게 검증하는 대체물이 아니다.
그 이전 포맷에 대한 일부 검증은 삭제되었으며, 프로덕션 지원 코드는 이번에 제거하지 않았다.

## 정리 중 발견한 실제 실패

`test_compiled_coordinates_reach_executor_without_model[True]`:
첫 관찰에서는 저장 좌표 실행이 되지만, 행동 없이 두 번째 `_observe()`를 호출한 뒤
`_select_node()`가 review 대신 observation을 반환한다. 로그의 구체적 이유는
`selection_error:visual_proposal_unknown_marker`이다. 저장 메서드는 재사용되었지만 선택 단계에서
marker 연결이 끊어진다. 프로덕션 수정은 이번 범위 밖이므로 strict xfail로 남겼다.
고쳐져 통과하면 XPASS로 테스트 실행이 실패하므로 이 표시를 제거해야 한다.
이 결과는 실제 ERP 파일럿 성공을 의미하지 않는다.

## 보존한 범위

- 전경 차단과 준비 문서 handoff 거절: 그래프 업무 절차에 진입하기 전에 중단한다.
- 로컬 워커 종료 테스트: 그래프 자체가 대체된 생명주기 검증이다.
- 직접 호출하는 공용 실행기/입력 검증/탐색 상태 단위 테스트: 기본 설정으로 Brain을 생성해도
  자동으로 구형 전체 업무를 수행하는 테스트와 구분했다.
- 분리 탐지 옵션 테스트 1개와 별도 학습 CLI 2개: 아직 설정·명령으로 사용 가능한 별도 기능이다.
  단지 현재 프로필에서 꺼져 있다는 이유로 모두 미사용이라고 판단하지 않았다.

## 검증 명령

```powershell
.\.venv\Scripts\python.exe -m coverage run --branch --data-file=.tools/cleanup-final.coverage -m pytest -q
.\.venv\Scripts\python.exe -m coverage json --data-file=.tools/cleanup-final.coverage --include="*/brain/*,*/models/*,*/pipeline/*,*/sawon_runtime/*" -o .tools/cleanup-final.json
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
git diff --check
```

전체 pytest는 604 passed, 1 xfailed. 이후 미사용 run_brain helper를 인라인한 변경은
`pytest tests/test_brain.py -q`로 26 passed 확인했다. Ruff check/format과 diff 검사는 통과했다.
유료 API·GUI·CI 실행은 하지 않았다. 변이 점수는 측정하지 않았다.

## 삭제 후 새로 미실행이 된 프로덕션 코드

아래는 이전에 실행된 라인/분기가 이후 실행되지 않은 수이다. 새로 실행된 라인과 상쇄하지 않은
감소 목록이므로 순감소보다 클 수 있다. 이 감소를 다른 테스트가 대체했다고 주장하지 않는다.

| 파일 | 라인 | 분기 |
|---|---:|---:|
| brain\graph\engine.py | 157 | 77 |
| brain\graph\completion.py | 29 | 19 |
| brain\cache\methods.py | 29 | 18 |
| brain\demo_learning\warm_methods.py | 12 | 5 |
| brain\graph\input_verification.py | 9 | 6 |
| brain\graph\context.py | 5 | 4 |
| brain\graph\selection.py | 4 | 4 |
| brain\graph\action_preparation.py | 3 | 4 |
| pipeline\handoff.py | 3 | 4 |
| brain\run.py | 4 | 1 |
| brain\graph\policy.py | 2 | 2 |
| brain\graph\click_refinement.py | 1 | 1 |
| brain\observe\evidence.py | 1 | 1 |
| brain\graph\scratchpad.py | 0 | 1 |
