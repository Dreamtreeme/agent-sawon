# 121: 명시적 스크롤 계약 실제 ERP 검증 중단

## 실행 조건

- core `16c377363e46d22489d1ebfac2166d43147d9be7`, bench `badbb5f6aa900aed57be742ff58d2acec9c12cfa`.
- 개발 시드 1, ID none, visual_only, 새 탐색 캐시. 탐색 성공 시 편찬 1회와 기억 인출 1회를 계획했다.
- 교육 `education/development/company-001.json`, SHA256
  `9cfeeeb5cc0715cfd85fadb911887712280b9e88f3ce4dfea0a9c748c32b4263`.
- Anthropic `claude-haiku-5-5`, low, 8192, 실행·편찬 각각 $3 상한.
- 기존 120의 원문 요청 및 메모 전후 기록 계측을 사용했다. 제품 동작 패치는 없다.
- 설정과 해시는 비공개 `.tools/explicit-scroll-121/protocol.json`에 보존했다.

## 결과

| 단계 | 결과 | 시간 | 원격 호출 | 행동 전달 | 비용 |
|---|---|---:|---:|---:|---|
| 탐색 | ERP 0/10, 작업자 exit 1 | 하네스 37.084초 / 작업자 33.171초 | 1회, HTTP 400 | 0 | 미확정 1회 |
| 편찬 | 탐색 실패로 미실행 | — | 0 | — | — |
| 기억 인출 | 편찬 미실행으로 미실행 | — | 0 | 0 | — |

하네스의 model_calls=2는 로컬 OmniParser 1회와 원격 1회의 합이다. 로컬 탐지는
15.595초, 원격 거절은 0.883초였다. 알려진 비용 $0은 전체 비용 $0을 뜻하지 않는다.
API가 사용량을 반환하지 않아 unknown_cost_calls=1, estimated_usd=null을 보존했다.
재시도하지 않았다. 실행 후 데스크톱 잠금이 해제되었고 두 저장소 제품 코드 변경은 없다.

## 화면·로그 대조

첫 capture.png를 직접 확인했다. 로그인 창의 계정·비밀번호 입력칸과 로그인 버튼이 보이며
입력칸은 비어 있다. 로컬 탐지는 완료되었지만 첫 screen_decision(seq 8)이 거절되어
클릭·키보드·스크롤은 하나도 전달되지 않았다. seq 12에서
`observation_error:BadRequestError`로 종료했다. 화면 기억 갱신과 재사용은 이 실행으로
평가할 수 없다.

서버 원문:

> The compiled grammar is too large, which would cause performance issues. Simplify your tool schemas or reduce the number of strict tools.

## 직전 성공 요청과 비교

120 candidate의 첫 screen_decision(seq 8, 성공)과 121의 첫 요청(seq 8, 실패)의
저장된 provider-request.json을 읽기 전용으로 비교했다.

| 항목 | 120 성공 요청 | 121 실패 요청 |
|---|---|---|
| 전송 스키마 UTF-8 크기 | 5,968 bytes | 7,485 bytes |
| steps.items | NamedAction 단일 참조 | NamedAction / NamedKeyAction / NamedScrollAction anyOf |
| 정의 변화 | 기존 NamedAction | NamedAction 수정, 키·스크롤 정의 추가 |

확인된 실패는 모델의 잘못된 행동 선택이 아니라 공급자의 응답 문법 컴파일 거절이다.
새 행동 분기가 가장 직접적인 변경 후보지만, 공급자의 내부 문법 크기나 임계값은
확인하지 못했다. 바이트 수 증가만으로 임계값을 단정하지 않는다. 다른 스키마를 전송하는
유료 대조 실험은 하지 않았다.

일반 테스트의 통과는 로컬 파싱·변환을 검증한 결과이며 공급자 문법 컴파일 수용까지
보장하지 않았다. 다음 수정 후보는 클릭·스크롤의 실행 의미를 유지하면서 공급자에 보내는
형식의 분기 복잡도를 낮추는 것이다. 이 실행에서는 제품·프롬프트·설정을 수정하지 않았다.
그 후보의 인과 가설은 축소된 스키마도 같은 오류로 거절되거나 원래 스키마가 같은 조건에서
수용되면 재검토해야 한다.

## 증거 위치

- bench `evidence/runs/explicit-scroll-121-cold/`: trial_result.json, results.jsonl, summary.json.
- core `cases/explicit-scroll-121-cold/64530220fcee257e38d5/case-b33f8cc24ef3463fa2e4e2332bdfd38e/case/`.
- 첫 프레임 `visual/a4a7cf4f-e5bc-49ca-a4c0-3ee0d18a0378/capture.png`.
- 요청 `model-requests/46e97899386f4a40b6302029ed869a9b/provider-request.json`.
- 서버 오류 `diagnostics/43494cc6e46043049ef0f0fb0bf153c7.json`.
- 비공개 실행 산출물은 커밋하지 않는다. 신규 보고서만 작성했고 파일럿 이후 테스트를
  다시 실행하지 않았다(제품 코드 변경 없음). 문서 검사는 `git diff --check`로 수행했다.
