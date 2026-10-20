# 수정 후 시드 재검사 062 — 2026-10-17

## 실행 조건

- Anthropic `claude-haiku-5-5`, low. 시드별 `education/development/company-NNN.json` 사용.
- 각 시드 콜드는 빈 캐시·빈 메서드로 시작. 콜드 성공 → 해당 실행으로 유료 편찬 → 새 ERP에서 웜 실행.
- 개발 시드 0–29 순서, 최초 실패 다음 시드까지 완료하고 중지. 편찬 실패도 실패에 포함.
- 실행 중 소스/모델 설정 변경 및 실패 재시도 없음. protocol.json의 소스 해시와 종료 시 일치.
- 총비용 제한 없음. 기존 개별 실행/편찬 $3 설정 유지. 평가 시드 미사용.
- 시간은 agent_elapsed_ms(작업자 실행), 호출은 local 제외 유료 호출. 편찬 시간/호출은 실행 행과 별도.

## 결과

| 시드 | 단계 | 결과 | ERP | 시간(초) | 유료 호출 | 추정 비용($) |
|---|---|---|---|---:|---:|---:|
| 0 | cold | success | 10/10 | 179.69 | 18 | 0.0350846 |
| 0 | compilation | compiled | — | — | 1 | 0.0039174 |
| 0 | warm | success | 10/10 | 58.08 | 5 | 0.0086347 |
| 1 | cold | success | 10/10 | 152.24 | 16 | 0.0299046 |
| 1 | compilation | compiled | — | — | 1 | 0.0024347 |
| 1 | warm | success | 10/10 | 54.79 | 5 | 0.0079862 |
| 2 | cold | success | 10/10 | 116.91 | 13 | 0.0238705 |
| 2 | compilation | compiled | — | — | 1 | 0.0025303 |
| 2 | warm | success | 10/10 | 47.75 | 3 | 0.0060225 |
| 3 | cold | success | 10/10 | 279.48 | 30 | 0.0584387 |
| 3 | compilation | stopped | — | — | 1 | 0.0049751 |
| 4 | cold | stopped | 0/10 | 40.99 | 5 | 0.0071404 |

전체 추정 비용 **$0.1909397**, 유료 호출 **99회**(편찬 4회 포함), 비용 미확정 0건.

0·1·2 콜드/웜 모두 통과. 시드 3 편찬에서 첫 실패, 시드 4 콜드 중단 후 규칙대로 종료. 5–29 미실행.

## 직접 확인한 문제

### 시드 3: 관찰 프레임 순번과 행동 배치 순번 충돌

콜드는 10/10 성공했다. 편찬의 로컬 compile_warm_method를 저장된 응답으로 재현했으며 추가 모델 호출은 없었다. 실제 예외는 `LearnedMethod: method_trace_indexes_must_be_unique`다.

- 선택된 c010: 품명(description) 입력, 좌표 [400,612].
- 선택된 c011: 추가 정보 탭 클릭, 좌표 [184,117].
- 둘 다 관찰 `c285e80b-1ea3-4058-88e0-c98a72c649b3`, trace_index=11.
- 같은 관찰에서 서로 다른 행동 배치가 나왔지만, 후보는 관찰 번호를 실행 순번으로 사용한다. 모델은 두 후보를 선택했고, LearnedMethod는 순번 중복을 금지해 편찬이 거절됐다.
- 로그에 남은 `ValidationError`를 모델 API 실패로 해석하면 안 된다. API 응답 후 로컬 메서드 생성에서 발생했다. 오류 원문은 compilation/local-replay-error.txt에 저장.

### 시드 4: task 경로가 구형 중복조회 단계 검사로 유입

- seq73/74 및 95/96: search 클릭 요청/성공 반환. 마지막 캡처는 DEMO-HBL-001, 조회 결과 0건, 신규 버튼을 보여준다.
- seq110/113: 모델은 `operation=duplicate_clear`, steps=[]로 중복 없음과 신규 진행을 보고했다.
- seq114: `duplicate_query_not_executed`로 중단.
- `_check_duplicate`는 phase==duplicate_query, query_submitted, query_value_verified를 요구한다. 현재 실행은 phase=task이며 search 전달에서도 구형 query_submitted는 해당 단계에만 설정된다.
- 실제 조회를 하지 않은 실패가 아니라, 공용 task 흐름의 보고를 구형 단계 전용 코드로 전달한 계약 불일치다. 신규 입력 전 중단돼 ERP 0/10, 웜 미실행.

## 수정 효과와 한계

- 이전 실패했던 시드 1은 콜드/웜 성공. 재조회 상세 시작과 누적 증거 완료 기록을 확인했다.
- 시드 2 웜: 이전 80.65초·7회 → 이번 47.75초·3회. 단, 각 1회이고 콜드 모델 계획도 다르므로 모든 개선을 단일 변경의 인과 효과로 단정하지 않는다.
- 시드 0 웜: 이전 48.87초·3회 → 이번 58.08초·5회. 모든 시드의 호출 수가 줄어든 것은 아니다.
- 시드 3은 저장 뒤 조회/행 선택 반복으로 콜드 279.48초·30회. 최종 상세에 도달해 누적 증거 완료로 성공했지만 효율 문제는 남는다.
- 이번 실행 중 추가 수정 없음. 신규 결함은 후속 작업 대상으로 남긴다.

## 산출물

- bench `evidence/seed-audit-20261017-062/status.json`, protocol.json, seed별 compilation/ 및 실행 로그.
- core `cases/seed-audit-062-NNN-{cold,warm}`의 run_log.jsonl, visual/*/capture.png.
- 실행 드라이버는 정상 종료했고 남은 Python 실행 프로세스 없음.

## 추가 원인 추적: 시드 3과 4

### 시드 3 — 앞선 설명 정정: 두 후보가 모두 실제 실행된 것은 아니다

- seq252/253: 이전 프레임에서 품명 입력 성공. 후보 c009가 이 실제 전달에 해당한다.
- seq256 이후 프레임 c285e80b...에는 이미 품명이 채워져 있다(캡처 직접 확인).
- screen-match.json(c010)은 품명 재입력을 제안했지만 seq273에서 already_satisfied로 생략, dispatched=false. seq275 method_execution도 interrupted다.
- 같은 프레임의 screen-plan.json(c011)은 추가 정보 탭 클릭을 제안했고 seq288/289 실제 전달, seq303 전이에서 changed다.
- read_warm_candidates는 deliveries/satisfied를 obs_id로 묶는다. c010에는 직접 전달된 행동이 없지만, 같은 obs_id의 c011 탭 클릭으로 `not delivered` 조건을 통과한다. c010의 fill 요구는 already_satisfied로 충족된다. 따라서 실행하지 않은 독립 계획이 성공 후보로 편입된다.
- 두 후보에 같은 observation order(11)를 trace_index로 준다. 컴파일러의 sorted 비교는 같은 순번을 허용하지만 LearnedMethod는 유일성을 요구해 최종 생성에서 실패한다.
- 편찬 모델은 c009 대신 c010을 선택했다. c010에도 description 입력이 있고, 탭 클릭 transition 및 그 뒤 화면이 함께 달려 있어 후보의 실제 효과가 혼합된 상태다.
- 이는 직전 수정에서 already_satisfied 허용을 배치가 아닌 화면 단위로 적용한 범위 오류다. 원래 의도는 한 배치 안에서 생략된 입력과 실제 전달된 후속 행동을 함께 보존하는 것이었다.

유료 호출 없는 로컬 반증 실험:

1. c010만 삭제 → `warm_method_missing_sources:description` (선택에서 실제 입력 c009도 빠져 있었음).
2. c010을 실제 입력 c009로 교체, c011 유지 → 18개 그룹으로 compile_warm_method 성공. 파일 저장/화면 실행은 하지 않았다.

따라서 순번을 새로 붙이거나 중복 검증만 없애면 미실행 재입력을 실행 경로로 남긴다. 후보는 결정/실행 배치별 전달과 생략 기록을 연결해야 하고, 화면 ID는 그 배치가 참조한 관찰로 따로 유지해야 한다. 실제 실행 없는 독립 계획은 관찰 증거로 보존할 수 있어도 재생 행동 후보로 취급하면 안 된다.

### 시드 4 — 모델에 노출한 응답과 실행 계약이 다름

- matching.py TASK_RULES에는 generic task에서 duplicate_clear/duplicate_found를 쓰지 말고 다음 행동을 고르라는 문장이 있다.
- 하지만 실제 호출은 screen_mapping.mapping_prompt를 거친다. 이 함수는 원래 프롬프트에서 JSON state만 추출하고 MAPPING_RULES로 앞 문장을 교체한다. 해당 제한은 MAPPING_RULES에 없고 ScreenMapping.operation에는 두 응답이 그대로 존재한다.
- 모델은 실제 조회 결과 0건을 읽어 duplicate_clear와 빈 steps를 반환한다. 결과에 대한 관찰 자체는 캡처와 일치한다.
- prepare_match_plan의 task 전용 정규화는 duplicate_clear AND steps가 있을 때만 act로 바꾼다. 빈 steps는 그대로 통과한다.
- _review_node는 phase 구분 없이 duplicate_clear를 _check_duplicate로 보낸다.
- _check_duplicate는 phase!=duplicate_query이면 즉시 duplicate_query_not_executed를 던진다. 실제 search 클릭은 seq73/74, 95/96에서 이미 전달 성공했다.
- task 경로에서 구형 query_submitted 플래그가 설정되지 않는 것까지 겹친다. 이 플래그를 억지로 켜도 phase 검사에 걸린다.

따라서 단순히 모델이 조회를 안 했거나 클릭 좌표가 틀린 문제는 아니다. 공용 task 경로에서 중복 없음은 관찰 메모로 기록하고 후속 행동 선택으로 이어야 하며, 구형 단계 전용 검토기로 보내지 않아야 한다. 실제 요청의 스키마/지침과 라우팅도 같은 계약을 사용해야 한다.

이번 추가 분석은 코드 수정/유료 호출/화면 재실행 없이 기존 기록과 순수 편찬 함수 재현으로 진행했다.
