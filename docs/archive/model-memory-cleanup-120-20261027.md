# 120: 모델 관찰 계약 통합과 ERP 비교

2026-10-27. 역사·검증 기록이며 현행 계약은 ../contracts.md를 따른다.

## 결과

정리와 콜드 검증은 완료했다. 기억 인출의 전체 업무 완료는 실패했다. 따라서 전체 경로가
검증 완료됐다고 보지 않는다. 실행 안전 검증을 풀거나 실패 결과를 성공으로 교체하지 않았다.

| 조건 | ERP / 작업자 | 작업자 시간 | 원격 호출 | 화면 실행 비용 | 전달 행동 |
|---|---|---:|---:|---:|---:|
| 기존 코드, 시드 1 콜드 | 10/10 / exit 0 | 137.196초 | 15 | $0.0321007 | 24 |
| 수정 코드, 시드 1 콜드 | 10/10 / exit 0 | 122.090초 | 14 | $0.0285760 | 24 |
| 수정 콜드 편찬물 인출, 시드 1 | 0/10 / exit 1 | 67.672초 | 9 | $0.0178764 | 19 |

별도 편찬은 1호출, 첫 시도 성공, 12그룹, $0.0032878. 실패 실행을 포함한 총비용은
$0.0818409이며 미확정 비용은 없다. 시드 1 한 쌍이므로 시간·호출 차이를 일반적 개선 효과로
확정하지 않는다. 중도 종료된 인출 시간은 성공 실행보다 빠르다는 근거가 아니다.

## 실제로 제거·통합한 것

- ScreenField.source에 입력 키를 넣어 FieldAssignment와 별도 assignments를 제거했다.
  행동의 source는 실행 계약이므로 유지했다. batch 로그의 assignments는 별도 배치 입력 목록으로 유지한다.
- observed_bindings의 행동·증거·동일 값 역추론과 scanner.observed_input_bindings를 삭제했다.
- 마지막 화면 모델 응답의 source/value를 화면 노드와 verified/원장의 공통 입력으로 사용한다.
  실행용 OCR 투영값으로 다시 읽거나 무효화하지 않는다. 읽은 값이 요구 입력과 다른 경우의
  기존 무효화, entry/saved_detail 구분, 입력 전달 이력은 유지했다.
- 빈 행동 보완에 current_observation을 전달하고, 마지막 응답 전체를 사용한다.
  앞 응답의 필드·연결·증거를 합쳐 보완에서 제외한 오탐을 되살리는 코드를 제거했다.
- 항상 빈 튜플을 반환하던 EvidenceLedger.record와 호출자의 superseded 누적·순회를 제거했다.
- 현재 매핑이 없는 메서드/화면 캐시는 기존 grounded_screen_plan 호출로 관찰과 계획을 받는다.
  별도 기억 전용 모델, 과거 메모 갱신 함수 delegate, OCR 기반 메모 fallback은 없다.
- 기존 캐시 응답을 새 스키마로 잘못 읽지 않도록 키에 screen-field-source-v1 계약 접두부를 넣었다.
  과거 파일을 삭제·변환하지 않는다. 매핑 직렬화는 exclude_unset으로 생략된 값과 빈 문자열을 구별한다.

verified, post_save_verified, evidence_ledger 자체, relay, 좌표 검증과 저장 완료 조건은 유지했다.
새 상태 클래스·정책 계층은 만들지 않았다. 공급자 nullable 변환은 기존 codec에 source 경로만 연결했다.

## 코드량

이번 작업 시작 직전 미커밋 상태를 baseline으로 보존했다. 아래는 helper를 제외하지 않고
변경한 제품 파일 전체의 물리적 줄 수/AST를 비교한 값이다. 새 helper 파일은 없다.

| 지표 | 변경 전 | 변경 후 | 차이 |
|---|---:|---:|---:|
| 핵심 6개 파일 전체 줄 수 | 7,740 | 7,665 | -75 |
| if 문 | 533 | 521 | -12 |
| for/while 문 | 75 | 70 | -5 |
| 조건식 | 143 | 139 | -4 |
| comprehension | 222 | 218 | -4 |
| _remember_model_task_evidence 줄 수 | 93 | 62 | -31 |
| 같은 함수의 if 문 | 11 | 6 | -5 |

6개는 engine, screen_decision, screen_mapping, screen_memory, transitions, scanning이다.
추가 변경한 pattern_matching은 기존 해시 반환 한 줄을 교체하므로 순감량에 영향이 없다.
프로젝트 전체가 대폭 작아진 것은 아니며, 이번 중복 해석 경로에서 감소한 수치다.
HEAD 대비 diff에는 이전 승인 작업의 미커밋 변경도 포함되므로 위 측정과 구별한다.

## 테스트와 반증

- 수정 전 합성 118 재생에서 보완이 제외한 Mistaken header가 남아 실패함을 확인했다.
- 기존 메모/OCR 불일치 테스트에 verified·원장 소유권 단언을 보강했다. 수정 전에는
  화면 메모는 맞아도 verified가 비어 실패했다. 수정 후 두 회귀 모두 통과한다.
- 스크롤·다른 탭의 누적 기억, 명시적 빈 값/생략 값, 부분 전달, 저장 후 증거 범위 테스트를 유지했다.
- 실제 편찬/선택/전달을 사용하는 합성 테스트는 매핑 없는 인출에 현재 판단 1회가 필요함을 검사한다.
  모델 좌표가 executor로 전달되는 것과 부분 실패 기록을 계속 검사한다.
- 옛 캐시 키가 새 실행에 적중하지 않으며 원본 파일이 바뀌지 않는 단언을 추가했다.
- Anthropic 스키마 변환, source/null 파싱, ScreenPlan/ScreenMatch 원문 fixture·해시를 갱신했다.
- 최종 `python -m pytest -q --tb=short`: **733 passed, 3 xfailed, 41.96초**.
  실제 명령은 core .venv/Scripts/python.exe를 사용했다. ruff check, format --check,
  git diff --check도 통과했다.
- 기존 xfail 3개(미선언 클릭 대상, 미사용 0크기 영역, recovery notebook 시점)는 그대로 남는다.
  이번 변경으로 해결했다고 보고하지 않는다. recovery에는 현재 관찰을 별도 데이터로 전달한다.

현재 읽은 값과 verified가 달라지거나, 다른 노드의 사실이 덮어써지거나, 인출이 삭제한
OCR 메모 fallback에 의존하면 이번 통합 가설의 반증이다. 실제 프로파일에서 baseline
13회/45개 현재 읽기, candidate 13회/46개, 인출 8회/28개를 대조했고 해당 시점의 값 일치와
verified 사이 불일치는 0건이었다. 이것은 화면 원문을 전부 정확히 읽었다는 증명은 아니다.

## 기억 인출 실패: 화면과 원문

실제 method_bound가 5회 발생했고 각각 grounded_screen_plan을 거쳐 mapping이 있는 관찰로
기억을 갱신했다. 나머지 호출은 screen_decision 3회와 input_review 1회다. 메모 갱신 전용
추가 모델 호출은 0회이며 과거 관찰 투영 fallback 실행도 0회다.

실패 화면은 등록 화면 하단이다. 수량·단위·중량·컨테이너·항구 값은 입력되어 있었고
저장 상태는 아직 new였다. 0/10은 저장 전 종료 결과이며 19개 행동이 모두 실패했다는 뜻이 아니다.

| 이벤트 | 실제 의미 |
|---|---|
| seq 129 / 134 | 상단 입력 5개와 아래 스크롤을 모델이 한 배치로 선택. 상단 입력은 전달됐지만 후속 읽기는 아직 없음 |
| seq 229 / 235 | 하단 입력을 읽은 뒤 상단 확인을 위해 pageup을 의도했으나 응답이 action=click, keys=pageup, point=[600,400]. click은 스크롤하지 않음 |
| seq 257 / 261 | 오류 피드백 뒤 click=[600,60]과 key/pageup=[600,400]을 함께 반환 |
| seq 267–270 | 첫 click 대상 bbox=[49,96,1058,16] 밖의 점. 배치는 전달되지 않고 동일 좌표 계약 오류 2회로 중단 |

seq 229의 요청에는 상단 필드들의 delivery와 이전 빈칸 관찰이 모두 있었고, 아직 확인되지 않은
입력으로 표시됐다. 다음 응답에서 하단 관찰 7개는 verified로 반영됐다. 상단이 미확인인 것은
메모 유실이 아니라 입력 직후 스크롤하여 아직 다시 읽지 않았기 때문이다.

이 비교가 드러낸 등가교환은 명확하다. 코드의 입력 연결 추론과 OCR 역매핑은 줄었지만,
현재 관찰이 없는 인출은 화면 모델에 행동 판단까지 다시 맡긴다. 따라서 메모 통합이 정상이어도
모델이 불필요한 클릭이나 잘못된 action/keys 조합을 새로 만들 수 있다. 인출 행동을 보존한 채
관찰만 갱신하도록 더 분리할지는 후속 설계 결정이다. 이번에는 그 경계나 좌표 검증을 추가로
바꾸지 않았고, 실패 실행을 재시도로 대체하지 않았다.

## 재현 자료와 조건

- Haiku claude-haiku-5-5 / low / 8192, visual_only, ID mode none, 시드 1, 각 1회.
- education/development/company-001.json SHA256:
  9cfeeeb5cc0715cfd85fadb911887712280b9e88f3ce4dfea0a9c748c32b4263.
- 건별 $3, timeout 900초. 비교 양쪽 새 case/cache/library. 인출은 새 화면 캐시와 성공한 candidate 편찬물.
- core 시작 HEAD 6533af443dc12757b1c2ec4dbd1fbfd3c15bdf6c + 승인된 미커밋 변경.
  frozen source SHA256과 현재 candidate 제품 코드의 바이트 일치를 검사했다.
- 비공개 protocol·source·분석: `.tools/memory-cleanup-120/`.
- core 실행: `cases/memory-cleanup-120-{baseline,candidate,retrieval}/`.
- bench 결과: `../agent-sawon-bench/evidence/runs/memory-cleanup-120-{baseline,candidate,retrieval}/`.
- 편찬: `../agent-sawon-bench/evidence/runs/memory-cleanup-120-compilation/compile-attempts.json`.
- 인출 실패 캡처: case-bc24f292b62744299ddff5d9fdd24ff2,
  visual/ac815718-02bc-47d2-a21f-08ffb4f4321d/capture.png.
- 위 private capture, provider 원문과 실행 산출물은 커밋하지 않는다.
