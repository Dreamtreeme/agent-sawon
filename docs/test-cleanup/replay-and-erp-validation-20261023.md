# 실패 관찰 재생 보완과 실제 ERP 검증 — 2026-10-23

## 변경 범위

제품 코드·모델 프롬프트·계약 검증은 변경하지 않았다.
`tests/recorded_graph_replay.py`는 실패하여 최종 observe를 남기지 못한 관찰의 입력 슬롯을
보존한다. 원본 wire가 없으면 해당 위치에서 명시적으로 중단한다. 후속 관찰을 대신 소비하거나
OCR을 재계산하지 않는다. `relay-observations.jsonl`이 있는 새 기록은 원문 순서를 우선한다.
원본을 사용한 경우 `observation_source=raw_relay`, 과거 로그는 legacy_events로 표시한다.

새 합성 테스트 두 건은 변경 전 각각 관찰 누락과 후속 snapshot 잘못 소비로 실패했고 변경 후
통과했다. 실패→재관찰→갱신 순서, 원문 필드 보존, 캡처 공급, 큐 소진을 검사한다.
테스트용 원문도 실제 ERP 기록인 것처럼 취급하지 않는다.

## 무료 검증

| 검증 | 결과 |
|---|---|
| core 전체 pytest | 704 passed, 59.66초 |
| core Ruff check / format | 통과, 234개 파일 |
| bench 전체 pytest | 621 passed, 6 GUI deselected, 19.39초 |
| bench Ruff check / format | 통과, 165개 파일 |
| 086 현재 코드 재생 | seq 113의 실패 관찰 원본 wire 부재에서 명시적으로 중단. 모델4회·행동7건·노드22회. 전체 재생 아님 |
| 089 현재 코드 재생 | 모델19회·행동29건·노드88회. success, 큐 모두 소진. 합성 갱신21건이라는 기존 한계 유지 |

086의 실제 실패 관찰 캡처·OCR은 있으나 원본 relay JSON은 없다. 이미지가 같다는 이유로
창 위치·메타데이터까지 동일했다고 가정하지 않았다. 조사 근거는
[입력 순서 재조사](replay-086-input-order-audit-20261023.md)에 있다.

## 실제 ERP 시도 — 실패도 포함

| 실행 | 조건 | ERP / 작업자 | 시간 | 원격 모델 | 비용 |
|---|---|---|---:|---:|---:|
| 090 | 시드3, company-003, 빈 캐시, visual_only, ID none, Haiku low/8192 | relay 초기화 실패. 작업자 미시작, 0/10 | 하네스2.39초 | 0 | $0 |
| 091 | 같은 업무·모델 조건, relay 의존성 설치 후 새 실행 | 10/10, success, exit 0 | 하네스194.60초 / 작업자190.54초 | 20 | $0.0359221 |

090은 core 가상환경에 PyAutoGUI가 없어 실패했다. 기존 bench의 relay extra를 설치했다.
setup.ps1의 Gui extra는 relay extra를 포함하지 않으므로 README 설치 명령도 정정했다.
090 원본은 유지하고 091은 새 protocol·폴더에 기록했다. 제품 코드 수정이나 동일 폴더 재실행은 없다.
각 시도의 건별/전체 한도는 $0.30이며, 두 시도 합산도 $0.0359221, 비용 미확정0이다.

091 실행 core f6540c5, bench 1bdeba4. 로컬 기록 wrapper는 기존 모델 원문 기록에
relay.observe 원문과 실제로 내려받은 캡처 복사를 추가했다. 추가 relay 요청은 없다.
무료 fake 시험에서 원래 호출1회·반환값·캡처 바이트 보존을 확인했다. wrapper·교육·설정의
SHA-256과 실행 커밋은 bench의 비공개 `evidence/local/seed3-recheck-091/protocol.json`에 있다.

## 화면·로그 대조 및 새 전체 재생

로그와 실제 기본 정보 입력 화면, 저장 후 수정 화면, 마지막 품명 확인 프레임을 직접 대조했다.
저장 번호가 표시된 수정 화면에서 기본 입력과 품명이 보이며, 최종 모델 응답 seq526은
기본 정보와 앞서 확인한 추가 정보가 일치한다고 판단했다. 독립 ERP 채점도 10/10이다.

091 원문 관찰46개를 공급한 전체 그래프 재생은 요청20회·행동31건·노드94회 후 정상 종료했다.
모델 요청 purpose·prompt·schema·image와 행동 payload 비교를 통과했고 모든 큐가 소진됐다.
verified/post_save_verified는 각각11개 입력 키다. 합성 갱신0건이며 별도 모델/ERP 호출은 없다.
비공개 출력: `.tools/graph-replay-091-raw-01/replay-summary.json`.

## 발견했지만 이번에 수정하지 않은 항목

1. 091 seq146–150에서 새 건 등록 계획을 이미 얻었지만 seq152의 semantic_target_repeated 뒤
   재관찰·재판단(seq155 이후)이 발생했다. `_verify_node`의 현재 계획 유지 분기는
   `_verify_navigation` 결과만 감싸며, 뒤의 `_verify_repeated_target` 결과는 그대로 반환한다.
   같은 계열의 추가 호출 후보이나 이번 제품 수정에는 포함하지 않았다. 새 기록이 이 경로도
   그대로 재생하므로 후속 변경의 기준으로 사용할 수 있다.
2. save_acknowledgement_intent_normalized는 0건이다. 091 성공이 confirm_save→navigate
   변환 분기까지 실제 실행으로 검증했다는 뜻은 아니다. 해당 분기는 기존 합성 회귀에 의존한다.
3. 잘못된 캡처 창은 이번 실행에서 관찰되지 않았다. 단일 정상 실행은 모든 창 상태를 보장하지 않는다.
4. bench 원격 CI(1bdeba4)는 Linux에서 schema export 불일치와 pywintypes mock 누락으로
   실패했다. 아래 확인 링크를 남기며, 테스트·스키마를 임의로 바꾸지 않는다.
   https://github.com/Dreamtreeme/agent-sawon-bench/actions/runs/39971795435

## 보류와 반증 조건

086의 원본 wire 부재가 해소된 것은 아니다. 091의 새 기준을 확보했지만 두 실행의 모든 분기가
같지는 않으므로 086 미확인 구간에 의존하는 광범위 상태 통합은 보류한다. 기존 상태는 각기
입력 확인, 저장 후 확인, 표면 소유권을 표현하므로 개수만 줄이려고 합치지 않는다.

실패 관찰이 다시 조용히 생략되거나, 원본 없는 메타데이터를 원문으로 표시하거나, 요청·행동이
달라도 complete_replay=true가 되면 이번 보완의 주장이 반증된다. 모델·화면의 새 결함을
재생기 완화로 덮지 않는다. 단일 실행의 시간·호출 차이는 개선 효과로 확정하지 않는다.
