# 검증 조건 단계별 완화 실험 — 2026-10-17

## 결론

전체 응답을 거절하는 보조 참조 변환과 설명 길이 제한을 완화했다.
웜 대체 행동의 대상 영역 비교와 저장 후 전체 필드 증거 조건은 임시 해제해 시험한 뒤 복원했다.
두 조건을 해제하면 잘못된 대상 연결/누락된 업무의 완료 수락이 가능한 반례가 재현됐다.
속도 차이를 모두 완화 효과로 볼 수는 없다. 각 조건 1회이며 실제 분기 발동 여부를 구분했다.

## 조건

- seed 23, none, visual_only, Haiku `claude-haiku-5-5`, low.
- 교육: bench `education/development/company-023.json`.
- 교육 SHA256: `631a9b1508d0ed8dcfd81eb7e9e0ce2f04de29ebaee32a34eed9aaee71354b62`.
- 합성 record, GPU OCR/OmniParser, pHash 한도 6 유지.
- 059 콜드의 immutable assembly-input/draft에서 동일 ID `ablation-060`의 19개 그룹을
  오프라인 재생성했다. 캐시는 해당 콜드 후보의 16개 키로 제한했다.
- 실행별 별도 파일 복사본을 사용해 앞 단계의 학습 결과를 다음 단계로 넘기지 않았다.
- 설정/캐시/메서드: bench `evidence/validation-ablation-20261017-060/<stage>/`.
- 시간은 worker elapsed_ms이며 하네스 준비 시간 제외. 비용은 공용 계측 추정치.

## 실제 화면 결과

| 단계 | 변경 범위 | 작업자 | ERP 채점 | 시간 | 유료 호출 | USD |
|---|---|---|---|---:|---:|---:|
| baseline | 시작 시점 코드 | success | 10/10 | 116.53초 | 10 | 0.0189234 |
| aux | 해석 불가 메시지/완료 증거 참조만 제외 | success | 10/10 | 58.94초 | 4 | 0.0077578 |
| edge | aux + 대체 행동의 좌표 영역 비교 제거 | failed | 10/10 | 35.91초 | 2 | 0.0034824 |
| purpose | aux + 긴 설명을 내부 표시용 요약으로 변환; 영역 비교 복원 | success | 10/10 | 63.10초 | 4 | 0.0079941 |
| completion | purpose + 저장 후 전체 필드 증거 목록 조건 임시 제거 | success | 10/10 | 47.45초 | 3 | 0.0053846 |

합계 $0.0435423, 미확정 비용 없음. ERP 채점과 작업자 종료 성공은 별개다.
edge는 저장 완료 팝업에서 중단되어 재조회 확인까지 마치지 못했다.

## 조건별 인과 확인

### 보조 참조

정상 클릭 계획에 선언되지 않은 message_targets 또는 completion_evidence 이름을 추가한
재현 입력은 기존 코드에서 각각 KeyError로 응답 전체가 탈락했다. 수정 후에는 클릭 1개가
유지되고 해석 불가 참조는 증거로 등록되지 않는다. 유효한 참조는 보존한다.
실행 대상 자체가 해석 불가한 경우는 그대로 예외가 난다.

실제 aux 실행에서는 `unresolved_mapping_annotations_omitted` 0건이었다.
따라서 116.53→58.94초 차이를 이 변경의 효과라고 주장할 수 없다.
변환 이후 저장 검토/최종 완료에서 요구하는 증거 조건도 여전히 존재한다.
예컨대 save_result를 제시했는데 모든 message_targets가 제외되면 후속 검토는 실패할 수 있다.
이번 변경은 응답 변환 단계의 연쇄 실패만 분리한 것이다.

### 웜 대체 행동의 좌표 영역

화면 유사도·행동 종류·입력 source·키·순서는 그대로 두고, 영역 비교만 제거했다.
같은 화면에서 Customer 입력 행동 좌표를 70px 아래의 Reference 영역으로 옮긴 재현 입력은
영역 비교가 있을 때 원래 edge로 연결되지 않았고, 제거하면 연결됐다.
이는 연결 시점의 오판 증명이며, 후속 효과 검토까지 반드시 통과한다는 주장은 아니다.

실제 edge 실행은 seq288/298의 purpose 길이 ValidationError로 먼저 중단됐다.
`experimental_edge_geometry_bypassed` 발동은 0건이다. 따라서 실제 실행 실패 원인은
좌표 완화가 아니며, 좌표 완화의 E2E 효과도 확인하지 못했다. 반례를 근거로 영역 비교는 복원했다.

### 설명 길이

baseline, aux, edge에서 모델의 purpose가 내부 MethodChoice의 160자 제한으로 거절됐다.
공급자에게 제공한 의미 매핑의 purpose에는 그 제한이 없었다. 행동 오류가 아니라 계약 간 차이다.
내부 메서드 저장 코드가 이미 사용하는 160자 요약 규칙을 변환 경계에도 적용했다.
원문은 ScreenMapping 및 semantic-mapping 산출물에 남긴다. 행동과 업무 값은 자르지 않는다.

purpose 실행에서는 160자 초과 응답이 없어 실제 완화 분기가 발동하지 않았다.
뒤의 completion 실행에서는 247자 설명이 원문 그대로 저장되고 실행은 계속됐다.
이 변경은 실제 API 응답에서도 설명 길이 거절을 피한 것이 확인됐다.

### 저장 후 전체 필드 목록

`_check_complete()`의 post_save_verified 누락 시 반려만 로그로 바꿨다.
모델의 완료 선언과 현재 완료 증거 검토는 유지했다.
실제 completion 실행은 성공했지만 `experimental_completion_ledger_bypassed`는 0건이다.
필드 증거가 이미 충족되어, 47.45초 성공을 조건 제거 효과로 볼 수 없다.

별도 재현에서는 필수 description이 빈 화면, 해당 필드의 저장 후 증거 누락,
모델의 완료 선언과 일반적인 Saved record 증거를 제공했다.
기존 조건은 재관찰, 해제 조건은 success로 종료했다. 전체 필드 조건은 복원했다.
기록상의 누락과 실제 값 누락을 구별하는 개선 여지는 있지만, 이번에는 이를 새 규칙으로 추측해
대체하지 않았다.

## 화면 직접 확인

로그뿐 아니라 baseline의 재조회 기본 정보, aux 및 completion의 재조회 추가 정보,
edge의 저장 완료 팝업 캡처를 직접 열어 확인했다.
baseline 화면에는 HBL/MBL, 수출자/수입자, 품명이 입력되어 있었다.
추가 정보에는 12/CT, 1250.5, DEMO-CNTR-001, CNSHA, KRPUS가 보였다.
edge 화면에는 저장 완료 문구와 확인 버튼이 정상적으로 보였다.
화면 확인과 독립 ERP 10/10 채점은 일치했다.

## 최종 유지 변경과 검증

- screen_mapping.py: 미해결 보조 참조 제외, 원문 설명 보존 및 내부 요약 변환.
- matching.py: 제외한 보조 참조를 RunLog에 기록.
- 회귀 테스트: 유효한 증거/행동 보존, 해석 불가 실행 대상의 실패, 긴 설명 보존,
  동일 화면의 다른 컨트롤을 원래 edge로 연결하지 않는 경우.
- method_runtime.py와 engine.py의 실험적 완화는 시작 상태로 복원했다.
  기존 작업에서 수정 중이던 내용은 보존했다.

- `.\.venv\Scripts\python.exe -m pytest -q`: 622 passed, 1 기존 xfailed, 28.34초.
- `.\.venv\Scripts\python.exe -m ruff check .`: 통과.
- `.\.venv\Scripts\python.exe -m ruff format --check .`: 193 files already formatted.
- `git diff --check`: 통과.
- 최종 유지 버전의 실제 화면 결과는 purpose 실행에 해당한다. completion 실행 뒤 임시 해제만 복원했다.

## 재현 명령과 산출물

bench 루트에서 각 stage에 대해 실행했다:

```powershell
.\.venv\Scripts\python.exe -m harness.run --agent core_visual --seeds 23 --modes none --observation-mode visual_only --repeats 1 --agent-config evidence/validation-ablation-20261017-060/<stage>/agents.json --output evidence/runs/validation-ablation-060-<stage> --timeout 600 --no-total-cost-limit
```

stage: baseline, aux, edge, purpose, completion.
코어 기록: `cases/validation-ablation-060-<stage>/`.
독립 채점: bench `evidence/runs/validation-ablation-060-<stage>/results.jsonl`.
준비/반례/집계 스크립트와 임시 백업: `.tools/validation-ablation-060/` (비공개 산출물).
