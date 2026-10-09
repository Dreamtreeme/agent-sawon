# 리팩토링 후 코드·그래프 추적 파일럿 — 2026-10-09

실행 코드 `81db40f`, 성공 실행 ID `core-pilot-20261009-refactor-trace-002`.
seed19의 기존 회사 교육과 합성 입력, Luna, visual-only, OmniParser v2/640, combined
screen matching을 빈 visual/method store에서 한 번 실행했다. 총 case 상한은 기존 승인 안의
`$1`이다. Python profiler는 production 코드를 수정하지 않고 작업자 subprocess의 함수
진입·반환만 기록했다.

## 결과

| 항목 | 결과 |
|---|---:|
| Core / ERP | success / 10필드 중 10필드 일치 |
| 중복 저장 / 비인가 전송 | 0 / 0 |
| 작업자 / 하네스 | 259.615초 / 265.866초 |
| 행동 / 관찰 | 25 / 31 |
| Luna / OmniParser v2 호출 | 19 / 14 |
| 모델 지연 | 173.739초 |
| 추정 비용 / 미확정 호출 | `$0.05762435` / 0 |

최종 상세 화면의 기본 정보와 추가 정보 캡처를 직접 확인했다. 기본 5개 값과 추가 6개
입력 키가 모두 표시되며, packages의 count/unit을 한 필드로 집계하는 ERP grader에서
10/10이다. 저장 번호는 1이다.

## 실제 코드 진입 순서

첫 관찰의 profiler 순서는 다음과 같다.

```text
brain.run.main
  → Brain.run → Brain._run → worker_graph.invoke
  → Brain.observation_node → Brain._observe_node → Brain._observe
  → VisualPerception.observe
  → OmniParserDetector._generate
  → VisualPerception._semantic_request
  → Brain._detect_visual → Brain._match_visual
  → matching.match_screen → matching.select_match_response
  → Brain._call → OpenAIClient._generate
```

첫 행동 뒤부터 observation 책임은 `_observe_node` 대신 `_verify_node`로 들어간다.
`_verify_node`가 새 화면을 관찰하고 직전 행동의 transition과 현재 evidence를 연결한다.

```text
Brain.observation_node → Brain._verify_node → Brain._observe → perception/matching
Brain.review_node      → Brain._review_node
Brain.decision_node    → Brain._select_node → selection.select_candidate
                       → Brain._selection_call
Brain.review_node      → Brain._review_node
Brain.execution_node   → Brain._act_node → Brain._execute_plan
                       → execution.execute_plan
                       → action_preparation.wire_action → relay.act
```

15번의 decision 중 화면 계획 모델을 다시 호출한 것은 4개 node에서 5회다. 나머지는 현재
관찰에서 만든 combined match와 실행 중 구성한 method를 재사용했다. 반면 15번의 관찰 중
첫 관찰을 제외한 14번은 매번 local detector와 Luna 의미 판단을 모두 호출했다. Luna 19회는
관찰 14회와 decision 5회로 나뉜다.

## 실제 그래프 라우팅

전체 74개 책임 node 실행은 아래 식과 정확히 일치한다.

```text
Observation
→ (Review[관찰 증거] → Decision → Review[실행 전 검토] → Execution → Observation) × 14
→ Review[관찰 증거] → Decision[complete] → Review[완료 검토] → finish
```

| node | 실행 횟수 | 합계 | 작업자 시간 비율 |
|---|---:|---:|---:|
| Observation | 15 | 214.203초 | 82.51% |
| Decision | 15 | 29.018초 | 11.18% |
| Execution | 14 | 15.904초 | 6.13% |
| Review | 30 | 0.137초 | 0.05% |
| 그래프 기타 | - | 0.354초 | 0.14% |

따라서 현재 구현의 실제 책임 순서는 문서 제목의 네 단어보다 구체적으로
`관찰 → 관찰 근거 검토 → 판단 → 제안 행동 검토 → 실행`이다. Review가 30회인 것은
우연한 중복 호출이 아니라 서로 다른 두 검토 지점 때문이다.

그래프 step은 단일 행동 수가 아니라 node 전환 과정의 누적 업무 step이다. 로그인 batch는
한 execution에서 3개 행동, 기본 정보 batch는 5개, 추가 정보 batch는 6개를 실행하므로
그래프 실행 횟수 14와 실제 relay 행동 25는 일치하지 않아도 정상이다.

## 시간과 반복 분석

- append-only log 기준 모델 지연은 전체의 66.92%다. Luna 19회 123.771초, local v2
  14회 49.968초다.
- local v2 첫 호출은 40.519초로 cold load가 컸다. 이후 13회는 profiler 내부 측정 기준
  중앙값 0.644초다.
- 관찰 node 안의 Luna 의미 판단은 14회, profiler 내부 합계 98.911초·중앙값 6.921초다.
  decision node의 Luna는 5회, 합계 20.803초다. 현재 주 병목은 decision 선택보다 매 관찰의
  detector+의미 매칭이다.
- `combined_screen_match_reused` 11회, `method_execution` 14회가 기록됐다. 재사용은 decision
  호출을 줄였지만, 관찰 의미 모델 호출까지 생략하지는 않는다.
- relay 요청 25건은 모두 성공했다. 행동 구성은 credential 입력2, 로그인1, 일반 입력12,
  navigation click6, tab click3, save1이다. 실패 클릭이나 같은 대상 무효 반복은 없었다.
  저장 안내 닫기와 조회 화면 이동은 같은 subgoal 문구로 연속 기록됐지만 서로 다른 화면의
  두 필수 행동이다.

Python call trace는 142,614개 event를 기록했다. 이 계측 자체의 오버헤드가 있으므로
259.615초를 비계측 파일럿과 직접 성능 비교하지 않는다. provider latency, 행동 수,
그래프 순서와 ERP 결과는 append-only run log와 harness 결과로 별도 확인했다.

## 증거 위치

- Harness 결과: sibling bench
  `evidence/runs/core-pilot-20261009-refactor-trace-002/`
- 전체 그래프 전환: 위 폴더의 `trace-analysis/graph-sequence.json`
- 주요 코드 호출 순서: `trace-analysis/node-call-sequence.json`
- 함수 시간: `trace-analysis/function-timing.csv`
- 원본 함수 trace: core
  `cases/core-pilot-20261009-refactor-trace/9347e931f231a11db665/`
  아래 성공 case의 `case-code-trace.jsonl`
- 원본 화면 녹화: sibling bench
  `evidence/recordings/core-pilot-20261009-refactor-trace-002/screen.mp4`

첫 실행 ID `core-pilot-20261009-refactor-trace`는 profiler script의 import path 누락으로
ERP 행동과 모델 호출 전에 종료됐다. 로그는 모델 호출0, 토큰0, 알려진 비용0을 보여준다.
`sawon_runtime`이 설치된 editable package 목록에 아직 반영되지 않은 상태에서 외부 script
경로가 `sys.path[0]`가 된 것이 원인이다. core 경로를 profiler에 추가해 002를 실행했으며,
production import나 패키지 코드는 변경하지 않았다.

이 한 번의 성공은 seed19 현재 업무의 회귀 확인이다. 다른 회사·시드 성공이나 M1/M2
마일스톤 종료 근거로 확대하지 않는다.
