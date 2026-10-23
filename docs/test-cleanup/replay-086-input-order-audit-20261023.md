# 086 재생 누락 재조사 — 2026-10-23

## 결론

기존 보고서의 최종 오류 `missing OCR for 704e4d28-6c5d-4dd7-a717-ed25f22fe613`는
재현된다. 그러나 실제 원인을 OCR 산출물 누락만으로 설명한 진단은 불충분했다.
재생기가 장면 결합에 실패한 관찰을 입력 큐에서 빠뜨려 다음 관찰부터 순서가 밀린다.
제품 코드·재생기·과거 기록은 수정하지 않았다.

## 실제 기록과 재생 순서

원본은 `cases/repeated-warm-086-r3-s3-cold` 아래 case다.

| 원본 seq | 사건 | 재생기의 입력 큐 포함 여부 |
|---|---|---|
| 113–126 | 관찰 1a883a4a…에서 OCR·모델 판단 후 visual_bbox_outside_capture 발생 | observe 이벤트와 observations snapshot이 없어 제외됨 |
| 133–147 | 관찰 ecfe5af5…에서 다시 판단하고 장면 결합 성공 | seq 147 observe에 따라 포함됨 |
| 152 | 행동 직전 갱신 704e4d28…; same_content=true, origin_changed=false | 갱신 이벤트로 포함됨 |
| 153–154 | 새 건 등록 click 전달·반환 | 행동 큐에 포함됨 |

`tests/recorded_graph_replay.py:RecordedInputs.__init__`은 관찰 큐를
`e['kind'] == 'observe'`만으로 만든다. 따라서 실패한 첫 관찰 대신 성공한 둘째 관찰을
먼저 공급하고, 첫 모델 응답의 실제 bbox 오류를 재현한 다음에는 둘째 관찰이 아니라
seq 152의 갱신을 일반 scan에 공급한다. RecordedOcr는 이 갱신 ID의 ocr.json을 요구하며 멈춘다.

## 파일과 화면 확인

- 실패 관찰 `1a883a4a-4e82-4f4f-8b3c-cc8fc9ba8d0b`의 capture.png, ocr.json,
  local-detections.json, markers.json, 모델 응답이 원본 case에 존재한다. OCR block은 19개다.
- 성공 관찰 `ecfe5af5-46ed-4507-8181-b84b536dea73`도 OCR 19개를 보존한다.
- 두 capture.png는 1262×786이며 SHA-256이
  `d021ce9916c03d80567a6f230066cdefd2863087329919617d505c44c02c7b5e`로 같다.
- 실제 이미지는 빈 조회 결과와 새 건 등록 버튼이 보이는 화면이다. 첫 모델 응답의 입력칸
  bbox는 [180,70,1094,31]이며 x+w=1274가 캡처 너비1262를 12px 초과한다.
  이후 응답은 [179,72,917,26]으로 수정된다. 원본에서도 이 오류 후 재관찰이 수행됐다.
- 704e4d28…는 입력 전 갱신 ID다. 원본에 독립 OCR 산출물이 없는 것을 당시 OCR 실패로
  해석하면 안 된다. 첫 실패 관찰의 원본 relay Observation JSON은 확인한 snapshot 목록에 없다.
  이미지·OCR의 존재만으로 이 JSON 전체가 복구됐다고 주장하지 않는다.

## 변경 없는 재검증

현재 core e9af652에서 원본 cold.toml과 cold-agents.json의 goal을 사용했다.
`tests/recorded_graph_replay.py`는 네트워크와 subprocess를 차단한다.
새 출력 `.tools/graph-replay-086-audit-5ddd42d8/replay-summary.json`:
모델5회, 행동7건, 노드22회 후 같은 ReplayMismatch. complete_replay=false, 종료 코드1.
실제 모델·ERP·relay는 실행하지 않았다. 원본 기록을 덮어쓰지 않았다.

## 제안하는 순서 변경 — 승인 대기

1. 재생기의 입력 수집이 실패한 관찰도 보존하도록 수정 범위를 먼저 정한다.
   원본 wire가 없는 관찰은 복구 가능한 메타데이터와 합성 envelope를 구분하고, 복원 불가이면
   잘못된 후속 큐 소비보다 먼저 명시적으로 중단한다. OCR을 다시 계산해 원본인 척하지 않는다.
2. 실패 관찰→재관찰→행동 직전 갱신의 합성 회귀 테스트를 추가하고, 기존 089 재생 및 관련
   테스트가 유지되는지 확인한다. 원본 086이 더 진행되는지와 남은 누락을 별도로 보고한다.
3. 그 결과에 따라 실제 ERP 검증과 새 기록 확보를 진행한다. 새로운 제품 수정이나 검증 완화는
   이번 조사로 승인된 것으로 취급하지 않는다.

원래 계획 1번 조사에서 재생 도구의 결함이 드러났으므로 2번 실제 ERP 실행 전에 사용자에게
순서 변경 승인을 요청한다. 과거 보고서는 수정하지 않고 이 후속 조사로 진단을 정정한다.
