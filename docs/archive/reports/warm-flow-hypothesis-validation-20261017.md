# 관찰·웜 경로 수정 및 E2E 가설 검증 — 2026-10-17

실행 식별자는 UTC 날짜 기준 `cold-warm-pilot-20261016-059`이다.
수정 전 058 기록과 비교했고, 새 콜드 1회·웜 1회·보완 후 웜 재검증 1회를 수행했다.
세 실행 모두 작업자 success 및 독립 ERP 채점 10/10이다. 최종 실행의 실제 기본/추가 정보
화면을 직접 열어 로그의 완료 주장과 비교했다. 모든 지연 원인이 해결됐다는 의미는 아니다.

## 실행 조건과 결과

- 개발 seed 23, mode none, visual_only, 합성 manual_record.json.
- 교육: `../agent-sawon-bench/education/development/company-023.json`.
- 교육 SHA256: `631a9b1508d0ed8dcfd81eb7e9e0ce2f04de29ebaee32a34eed9aaee71354b62`.
- 모델: config/models.toml의 `claude-haiku-5-5`, thinking low.
- GPU OCR/OmniParser, 화면/모델 설정은 058 조건을 복제했다. pHash 한도 6은 바꾸지 않았다.
- 시간은 worker result.elapsed_ms 기준이며 하네스 준비·웜 편찬 시간은 제외한다.
- 유료 호출과 로컬 탐지 호출을 구분했다. 비용은 공용 계측의 설정 단가 추정치다.

| 실행 | 작업자 결과 | ERP | 시간 | 유료 호출 | 로컬 호출 | 유료 호출 지연 합계 | 비용 USD |
|---|---|---|---:|---:|---:|---:|---:|
| 이전 058 콜드 | success | 10/10 | 239.18초 | 29 | 17 | 207.58초 | 0.0510062 |
| 이전 058 웜 | stopped | 10/10 | 148.90초 | 14 | 17 | 108.86초 | 0.0264658 |
| 059 콜드 | success | 10/10 | 247.01초 | 24 | 20 | 197.43초 | 0.0435369 |
| 059 첫 웜 | success | 10/10 | 102.97초 | 8 | 19 | 68.87초 | 0.0158688 |
| 059 보완 후 웜 | success | 10/10 | 51.45초 | 3 | 18 | 23.37초 | 0.0054863 |

최종 웜은 이전 실패 웜 대비 시간 약 65.4%, 유료 호출 78.6% 감소했다.
콜드는 호출 수가 줄었지만 전체 시간은 개선되지 않았다. 각 조건 1회이며 모델 응답과
로컬 초기화 지연이 달라, 이 수치를 일반적인 성능 보장이나 변경별 독립 효과로 해석하지 않는다.

웜 편찬은 첫 시도 실패 1회($0.0022555), 보완 후 성공 1회($0.0032261)였다.
이번 API 전체 추정 비용은 $0.0703736, 미확정 비용은 없다.

## 가설별 판정

| 가설 | 판정 | 실제 근거와 수정 |
|---|---|---|
| 상세 진입 상태보다 다음 계획이 먼저 만들어져 회귀한다 | 원인 지지, 처방은 조건부 계획으로 조정 | pending 결과 열기 정보를 모델에 전달하고, 상세 관찰 확인 뒤 증거 기록 전에 검토 세션을 시작했다. 콜드 seq406 검토 시작 → 407 기본 필드 증거. 이후 재검증에서 같은 상세창을 닫고 다시 조회하는 회귀가 없어졌다. |
| 모델 대체 행동이 원래 웜 선행 그룹을 완료시키지 못한다 | 확인 | 첫 웜은 같은 확인 버튼의 284,266과 288,266을 다른 행동으로 처리했다. 동일 컨트롤 영역/행동 종류로 연결하고, 전달된 대체 행동은 효과 검증 대기 동안 조건부 후속 후보로 사용한다. 효과 성공 때만 원래 그룹을 확정한다. 최종 seq315 method_edge_replaced 후 후속 웜 그룹이 연속 실행됐다. |
| 편찬 후보에서 필요한 실행이 빠진다 | 확인, 최초 수정만으로는 부족 | screen-plan 추가로 과거 성공 기록의 후보가 13→16개. 동일 캡처의 OCR 파일 생략도 발견해 바이트가 같은 원본 자료와 연결했다. 새 콜드에서는 직후 해석 오류 때문에 미승격된 저장·조회·탭 이동도 빠져 있었다. 전체 success와 실제 전달 전이를 근거로 복구해 16→19개로 편찬했다. |
| 매핑 변환이 호출 목적 지시를 없앤다 | 코드/회귀 테스트 확인, 속도 기여는 분리 측정하지 않음 | 입력 후 검토와 교육 재조회 금지 지시를 의미 매핑 JSON에 보존했다. 과거 숫자 타깃 지시는 복구하지 않았다. |
| 미사용 grid/row만 고치면 변환 예외가 끝난다 | 불충분 | 콜드와 첫 웜에서 message_targets/completion 참조에 선언되지 않은 설명 문장이 들어가 KeyError가 발생했다. 모든 target이 fields의 선언된 이름을 참조하도록 전달 지시를 명시했다. 최종 실행 예외 0건이나 모델이 항상 준수한다는 보장은 아니다. |
| 서로 다른 예외를 같은 subgoal 실패로 합산한다 | 코드/회귀 테스트 확인, 최종 E2E에는 해당 실패 조합 없음 | 선택·검증·실행 단계와 예외 종류를 실패 키에 포함하고 업무 전체 실패 횟수와 분리했다. 동일 예외 반복 제한은 유지했다. |

## 실행 중 발견해 추가 보완한 부분

1. 의미 매핑을 다시 읽으면 정규화된 act가 원래 duplicate_clear로 복원되어 신규 등록
   행동의 편찬이 실패했다. 좌표 복원은 하되 실제 실행의 operation을 유지했다.
2. 캐시 승격 목록만으로는 실행 직후 관찰이 실패한 성공 행동을 복구할 수 없었다.
   전체 업무 성공 + 해당 관찰의 전달 전이를 모두 확인한 행동을 trace 출처로 구분해 편찬한다.
   미전달 제안은 제외하고 같은 프레임/응답의 중복 파일도 하나만 수집한다.
3. 대체 행동을 효과 검증 후에만 후보 연결에 쓰면, 관찰 내부의 다음 계획은 이미 모델로
   넘어간다. 확정 used와 검증 대기 predecessor를 구분해 조건부 계획만 먼저 허용했다.
   테스트는 효과 실패 시 후속 경로가 더 이상 연결되지 않는 것까지 확인한다.

이 작업은 네 노드를 전면 분리한 리라이트가 아니다. 한 번의 모델 응답에서 현재 결과의
증거와 조건부 다음 행동을 받는 구조를 유지하면서, 상태 반영과 의존 관계를 고쳤다.

## 최종 웜의 호출 3회

| 로그 seq | 목적 | 시간 | 실제 이유 |
|---|---|---:|---|
| 288 | completion_review | 6.50초 | 저장 완료 메시지를 현재 화면에서 검토하고 확인 버튼을 누르는 계획 |
| 431 | completion_review | 9.19초 | 재조회한 기본 정보/품명 값을 확인하고 추가 정보 확인으로 이동 |
| 456 | grounded_screen_plan | 7.67초 | 추가 정보 6개 값을 확인하고 업무 완료 선언 |

19개 메서드 바인딩 중 18개는 직접 재사용 실행됐고, 확인 버튼 1개는 모델이 같은
컨트롤에서 대신 실행해 원래 그룹으로 연결됐다. seq315의 원래 그룹은
61de2bca7f95eed7, 대체 그룹은 c75f7cde36c4d2ad이다.
최종 완료 seq464 뒤 seq465 task_completed_with_verified_evidence로 종료됐다.

## 남은 문제와 해석 한계

- 최종 웜에도 screen_fill_evidence_missing 4건이 남았다(seq147/191/242/270).
  실제 입력 실패가 아니라 재사용된 다음 행동 응답의 입력 증거가 부족한 경우다.
  실제 최종 화면과 ERP 채점은 정확한 입력을 확인했다. 이 로그를 성공으로 위장하지 않았다.
- 기본 필드/품명 입력 전후 pHash 거리는 각각 8로 한도6을 넘었다. 좌표 증거 연결이
  탈락하는 원인 하나다. 나머지 두 입력 배치는 거리가4/0이라 이것만으로 전부 설명되지 않는다.
  별도 오프라인 조회에서 일부 증거의 synthetic target이 마커 목록에 등록되지 않는 것도 관찰했다.
  따라서 남은 증거 누락을 pHash 한도 상향 하나로 해결하지 않는다.
- 과거부터 존재한 repeated-observation visual_proposal_unknown_marker xfail 1건은 그대로다.
- 새 시드의 일반화, 반복 측정의 중앙값, 무모델 완료는 이번 범위에서 입증하지 않았다.

## 재검증 조건 통제

최종 웜은 첫 웜의 학습 결과를 그대로 재실행하지 않았다.
`warm-compilation-2/assembly-input.json`과 `assembly-draft.json`에서 같은19개 그룹을
오프라인으로 재생성했고, 시각 캐시는 그 콜드 후보의 기존16개 키로 한정했다.
첫 웜이 추가한 새 키/행동 그룹은 제외했다. 기존 키의 success_count는 성능 판단에 쓰지 않는다.
최종 워커의 변경은 대체 행동의 영역 비교·조건부 선행 연결과 명시적 target 참조 지시다.
콜드 E2E는 이 마지막 두 보완 전 버전으로 수행했고, 최종 버전은 웜 E2E와 전체 테스트로 확인했다.

## 검증

- `.\.venv\Scripts\python.exe -m pytest -q`: 619 passed, 1 xfailed (29.26초).
- `.\.venv\Scripts\python.exe -m ruff check .`: 통과.
- `.\.venv\Scripts\python.exe -m ruff format --check .`: 통과.
- `git diff --check`: 통과.
- 회귀 검증은 실제 관찰에서 검토 세션→증거 순서, 입력 전/후 증거 구분, 4px 다른 동일
  컨트롤 대체, 효과 실패 시 후속 경로 차단, screen-plan 수집/중복제거, 동일캡처 카탈로그,
  미전달 제외, 미승격 성공 행동 복구, operation 보존을 확인한다.

E2E 명령(bench 루트):

```powershell
.\.venv\Scripts\python.exe -m harness.run --agent core_visual --seeds 23 --modes none --observation-mode visual_only --repeats 1 --agent-config evidence/cold-warm-pilot-20261016-059/agents-cold.json --output evidence/runs/cold-warm-pilot-20261016-059-cold-1 --timeout 600 --no-total-cost-limit
.\.venv\Scripts\python.exe -m harness.run --agent core_visual --seeds 23 --modes none --observation-mode visual_only --repeats 1 --agent-config evidence/cold-warm-pilot-20261016-059/agents-warm.json --output evidence/runs/cold-warm-pilot-20261016-059-warm-1 --timeout 600 --no-total-cost-limit
.\.venv\Scripts\python.exe -m harness.run --agent core_visual --seeds 23 --modes none --observation-mode visual_only --repeats 1 --agent-config evidence/cold-warm-pilot-20261016-059/agents-warm-recheck.json --output evidence/runs/cold-warm-pilot-20261016-059-warm-recheck --timeout 600 --no-total-cost-limit
```

실행 산출물은 로컬/비공개 경로에 유지한다.
- Cold: cases/cold-warm-pilot-20261016-059-cold/6da2bee5628fecbaca31/case-5aa01a72040c449baebed1b9f3497d09/case
- Warm: cases/cold-warm-pilot-20261016-059-warm/0c3f68d905095c8f7103/case-2aafc8b93a2d43d6b9430bc40f4bae9c/case
- Final: cases/cold-warm-pilot-20261016-059-warm-recheck/d1d51d6bf89cb21a352c/case-2870dde39ad44928a8bd2ddb9b791ef9/case
- Final 기본 화면: visual/978f74c0-caad-4b70-bb55-0d4eedf616f1/capture.png
- Final 추가 화면: visual/8292ac67-4a25-428f-b51a-fc95a75db7b4/capture.png
- ERP 채점: bench evidence/runs의 각 실행 디렉터리 results.jsonl.
