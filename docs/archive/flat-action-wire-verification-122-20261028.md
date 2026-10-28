# 122: 단일 행동 전송 형식과 실제 ERP 검증

## 변경과 검증 범위

121은 첫 Anthropic 요청의 문법 컴파일에서 HTTP 400으로 종료했다.
이번에는 Anthropic 전송 경계만 단일 행동 객체로 바꾸었다. 내부 NamedAction,
NamedKeyAction, NamedScrollAction, 좌표 계산, 작업자 경로와 relay는 유지했다.

- `brain/graph/screen_decision.py`: 화면 계약의 steps에만 전송 평탄화를 지정한다.
- `models/schema.py`: 기존 codec에서 지정한 union을 전송할 때 펼치고, 수신 시 action으로
  내부 타입을 선택한다. 미사용 source/keys/direction의 빈 문자열만 제거한다.
  비어 있지 않은 혼합 필드는 보존하여 기존 Pydantic 검증에서 거절한다.
- `models/anthropic_client.py`: 위 계약 metadata를 기존 codec에 전달한다.
- `tests/test_anthropic_client.py`: 배열 내부까지 union을 검사하고, 왕복 보존·혼합 행동
  거절을 검증한다. SDK mock의 통과를 실제 공급자 수용으로 해석하지 않는다.
- `docs/contracts.md`: 공급자 표현과 내부 행동 계약의 경계를 기록했다.

일반 프롬프트, 모델, 출력 한도, 도메인 스키마와 실행 정책은 바꾸지 않았다. 공급자에
전달되는 스키마 및 그 필드 설명은 달라졌다. 모델 판단 품질이 동일하다고 가정하지 않는다.
전송 스키마는 7,485→6,054 bytes, union 1→0, codec은
`nullable-sentinels-flat-actions-v2`다. 실제 서버 문법 크기는 측정할 수 없다.

## 로컬 검증

| 명령 | 결과 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests/test_anthropic_client.py tests/test_screen_decision.py -q` | 158 passed, 16.60초 |
| `.venv/Scripts/python.exe -m pytest -q` | 724 passed, 기존 3 xfailed, 70.51초 |
| `.venv/Scripts/python.exe -m ruff check .` | 통과 |
| `.venv/Scripts/python.exe -m ruff format --check .` | 260 files, 통과 |
| `git diff --check` | 통과 |

기존 xfail은 관찰 경로의 합성 106 사례 세 건이며 이번 작업에서 변경하지 않았다.
관련 테스트에서는 source·좌표 0 보존과 클릭/키/스크롤 왕복을 검사한다. 잘못된 키,
key/pageup, click+pageup, click+direction, scroll+keys, scroll+source는 계속 거절된다.
API 오류 계측, 비대상 nullable 계약, 원본 스키마 불변 검사도 유지했다.

## 실제 API 대조

121의 저장된 로그인 요청과 동일한 프롬프트·이미지를 사용했다. 도메인 요청 지문은
`81ae2a530e7502a9f01bca3eac004aec30adfe9c989b2a3b89929410175cda92`로 동일하다.
전송 스키마만 달라졌고 API 및 로컬 파싱에 성공했다.

- 1회, 10.479초, $0.0012797, 미확정 비용 없음.
- 계정 입력, 비밀번호 입력, 로그인 클릭의 연속 행동이 반환됐다. 실제 입력은 하지 않았다.
- 이 결과는 전송 문법 변경이 121 실패를 해결한다는 가설을 지지한다. 공급자 내부의
  정확한 임계값이나 모든 가능한 스키마의 수용을 증명하지 않는다.

## ERP 조건과 결과

개발 seed 1, ID none, visual_only, Haiku 5.5 low/8192. 교육은
bench `education/development/company-001.json`, SHA256
`9cfeeeb5cc0715cfd85fadb911887712280b9e88f3ce4dfea0a9c748c32b4263`.
각 실행·편찬 상한 $3. 새 탐색 캐시와 성공 기록에서 편찬한 13그룹을 사용했다.
기준 core HEAD는 `16c3773`, bench는 `badbb5f`이고 core 수정본을 사용했다.
제품 파일·설정·launcher 해시 및 작업 트리 패치는 `.tools/explicit-scroll-122/protocol.json`과
`core.patch`에 고정했다. 파일럿 중 제품·설정 해시가 바뀌지 않았음을 종료 후 확인했다.

| 단계 | ERP / 작업자 | 작업자 시간 | 원격 호출 | 실제 행동 | 비용 |
|---|---|---:|---:|---:|---:|
| 탐색 | 10/10 / exit 0 | 158.818초 | 18 | 29 | $0.0359619 |
| 편찬 첫 시도 | 13그룹 성공 | 모델 25.974초 | 1 | — | $0.0042847 |
| 기억 인출 유효 실행 | 10/10 / exit 0 | 175.166초 | 21 | 33 | $0.0437254 |

하네스 시간은 탐색 162.154초, 인출 178.613초다. probe 포함 신규 유료 호출은 총 41회,
알려진 비용 합계 **$0.0852517**, 이 호출들의 미확정 비용은 0이다.
이전 121의 미확정 비용은 해소하거나 이 합계에 포함하지 않았다.

화면 판단 API 40회(probe 1 + 탐색 18 + 인출 21)는 모두 성공했다. 이 40개 실제 응답에서
provider 원문 → 내부 복원 → 전송 형식 왕복 결과가 원문 JSON과 같았다. 편찬 응답은
별도 계약이므로 이 왕복 비교의 대상이 아니다.

### 실행자의 보조 파일 오류와 재시작

인출 최초 시작은 private 로그 조회 파일 `inspect.py`가 Python 표준 모듈을 가려
`missing_agent_result`로 종료했다. process.log에는 `no case yet`가 남았고 brain.run에
진입하지 않았다. import 검색으로 실제 잘못된 해석 경로를 확인한 뒤 파일 이름을
`inspect_run.py`로 바꿨다. 원래 실패 산출물은 그대로 두고 새 case/output에 `-r2`를 붙였다.
제품 코드·모델·실행 설정 내용은 동일하다. 이 오류는 실행자의 계측 보조 파일 문제다.

첫 인출 시작의 하네스 기록은 0.862초, 모델 0회, 행동 0회, estimated_usd=null이다.
로그 파일이 없다는 이유만으로 비용 0으로 수정하지 않았다. 해당 보조 스크립트의 종료
경로에는 모델 호출이 없으며, 이 판단과 원시 하네스의 null 표시는 구분한다.
이 초기 실패를 유효 인출 결과로 덮어쓰거나 삭제하지 않았다.

## 화면·메모·행동 대조

- 탐색에서 4회, 인출에서 7회의 실제 scroll이 전달됐다. 모두 성공 응답을 받았고
  click+pageup 같은 혼합 형식이나 행동 전달 거절은 없었다. 탐색 seq 175는
  wheel_delta=-600으로 하단 필드에 이동했다. 캡처에서 해당 이동을 직접 확인했다.
- 기억 갱신은 탐색 15회, 인출 20회다. 등록 화면 노드에 상단·하단의 11개 입력 키가
  누적됐다. 탐색 seq 268에서 하단 관찰값이 채워지고 상단 필드는 보존되었다.
  이후 상단 재관찰과 저장 후 상세 검토로 전체 증거가 확보됐다.
- 인출은 로컬 탐지 호출이 1회로 줄었다(탐색 12회). 그러나 모델 판단은 늘었다.
  grounded_screen_plan 2, screen_decision 9, input_review 2,
  completion_review 7, missing_actions_recovery 1회다.
- 인출 기록은 adaptation 전달 17회, learned 전달 2회다. 편찬 원본 reuse 전달은 0이며,
  재현 v2는 두 정책 모두 false다. 메서드 인출 후 모델 재판단이 계속 발생하는 구조는
  이번 전송 수정으로 바뀌지 않았다.

### 해결되지 않은 실제 비효율

1. **이미 채워진 번호를 빈칸으로 읽었다.** 인출 seq 149에서 번호 입력이 전달됐고,
   seq 251에 전달된 이미지에도 번호가 보인다. 그런데 provider 원문 fields의 hbl_no 값은
   빈 문자열이며 모델은 fill을 다시 선택했다. 변환 후에도 동일하므로 codec이 값을
   지운 것이 아니다. 원본 캡처와 모델 이미지의 SHA256도 동일하다.
2. **상·하단 재확인을 반복했다.** seq 277은 하단 확인을 위한 down, seq 303은 상단
   재확인을 위한 up을 선택했다. 인출은 탐색보다 set_text 1회·click 1회·scroll 3회가
   많고 key는 1회 적어 총 행동이 4회 늘었다.
3. **설명과 선택 행동이 어긋났다.** seq 494는 등록 화면을 닫겠다고 설명했지만 실제
   선택은 Shipper 거래처 검색 클릭이었다. seq 518에서 그대로 전달되어 창이 열렸고,
   seq 528에서 모델이 취소로 복구했다. 실행기는 모델이 선택한 버튼 좌표를 전달했다.
4. **노드 이름 구분이 흔들린다.** 저장 후 실제 창 제목은 House B/L 수정이지만 인출의
   seq 553/578은 screen_path를 House B/L 등록으로 반환했다. 탐색은 수정 노드를 별도로
   만들었지만 인출에서는 등록 노드에 관찰이 모였다. 화면 노드 의미 선택의 안정성은
   이번 성공으로 보장되지 않는다.

최종 캡처의 수정 화면과 저장 번호, 상단·하단 입력값을 직접 확인했다. 하네스 채점과
작업자 종료도 모두 성공이다. 다만 한 시드 한 쌍에서 인출은 더 느렸으므로 속도나 호출
효율 개선을 주장하지 않는다. 위 비효율을 막는 추가 규칙이나 프롬프트 수정은 하지 않았다.

## 증거 위치와 반증 조건

- core `cases/explicit-scroll-122-probe/`.
- core `cases/explicit-scroll-122-cold/ece8af0bca0c1f1c11f4/case-97b28b51b57d45e298af071967cccb52/case/`.
- core `cases/explicit-scroll-122-retrieval-r2/2186a1d76063f58bd1aa/case-68fdeaf7c56e4fd8918f64b883e4f433/case/`.
- bench `evidence/runs/explicit-scroll-122-{cold,compilation,retrieval,retrieval-r2}/`.
- 오독 이미지: 인출 `visual/a6428c69-06b2-4de6-9bcf-243d24f87db1/capture.png`.
- 오독 원문: 인출 `model-requests/a8853025af9a4736a5c3ba33027c8ff6/`.
- 최종 인출 이미지: `visual/e93649f8-d700-4eae-b61d-5fb402fce90f/capture.png`.

동일 전송 스키마에서 문법 오류가 재발하거나, 복원 과정이 비어 있지 않은 혼합 필드를
삭제하거나, 원래 좌표·입력 키를 바꾸면 수정 가설을 재검토해야 한다. 현재 관찰된
오독·불필요한 탐색은 전송 수용과 별개인 남은 품질 문제다. 비공개 원문·이미지는 커밋하지 않는다.
