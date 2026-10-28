# 클릭·키보드·스크롤 모델 응답 분리

## 문제와 변경

120 인출 실행의 seq 229는 위로 스크롤한다고 설명하면서 click + keys=pageup을 반환했다.
seq 257은 key/pageup 앞에 대상 밖 클릭을 남겼다. 좌표 변환 오류는 아니었고 두 배치는
실제 전달 전에 중단됐다. 이 변경은 응답에서 클릭과 스크롤을 독립적인 행동 형식으로 만든다.

| 모델 형식 | 허용 내용 | 실행 변환 |
|---|---|---|
| NamedAction | fill/click/double_click, target, intent, point, 선택적 source | 기존 행동 그대로 |
| NamedKeyAction | key, target, intent, point, 실제 키보드 keys | 기존 키 입력 |
| NamedScrollAction | scroll, target(문맥), intent, point, direction(up/down) | key/pageup 또는 key/pagedown → relay scroll |

steps의 action discriminator로 형식을 선택한다. 클릭에는 keys/direction 속성이 없고
스크롤에는 keys/source 속성이 없다. 모델용 키보드 값은 공용 KEY_INPUT_VALUES의 부분집합으로
pageup/pagedown을 제외한다. 실행기·기존 편찬물의 키 어휘와 relay 공개 계약은 바꾸지 않았다.
스크롤 point는 현재 화면의 스크롤 영역이며 문맥 필드 bbox 내부일 필요가 없다.
별도 포커스 클릭을 생성하거나 click+pageup을 임의로 scroll로 고치는 로직은 없다.

## 경계 변경

- screen_mapping: 세 응답 형식, 모델 프롬프트, 실행 표현 변환. 회상된 기존 절차의
  key/pageup·pagedown도 모델에 보여줄 때 scroll/up·down으로 투영한다. 저장 원본은 보존한다.
- vocabulary: 모델용 KeyboardInput, ScrollDirection과 기존 실행 키 대응을 정의한다.
- screen_decision: 삭제된 NamedAction.keys의 Anthropic null 경로를 제거한다.
- models/schema: discriminator로 해당 응답 형식을 찾아 source의 null sentinel을 복원한다.
  SDK 변환 뒤에는 steps.items의 세 행동 대안인 anyOf 하나가 생긴다. 실제 전송 계측은 union=1이다.
  선택적 property의 nullable 문법 분기를 다시 늘리지 않는다.
- pattern_matching: 새 모델 응답 형식의 캐시 키 namespace로 구분한다. 기존 파일을 삭제하지 않는다.
- memory_compilation: 과거 mapping에서는 기하만 읽고 실제 전달 이력의 행동을 사용한다.
  이미 뒤에서 전달 steps로 대체하던 경로이며, 과거 모델 행동 형식의 재파싱을 피한다.
- contracts/current-runtime: 모델의 scroll과 내부 key/pageup·pagedown의 경계를 명시한다.

ScreenPlan/ScreenMatch 스키마 원문 fixture와 해시는 의도적으로 갱신했다.
새 SHA256은 `2c6785ab78ffaeba0907d82815b31a4bdf4b7d28d150fa7b26a0fbe3d41368fd`다.
다른 기존 어휘 스키마 fixture는 변경하지 않았다.

## 검증

- 기존 fake 모델과 실제 관찰→판단→좌표 결합→wire 변환을 연결한 합성 테스트에서
  up/down 모두 추가 클릭 없이 scroll 하나를 만들며, 필드 bbox 밖·화면 안의 point를 보존한다.
  wheel_delta는 각각 +600/-600이다. 실제 데스크톱 동작은 실행하지 않았다.
- click+pageup, click+direction, scroll+keys, key+pageup의 혼합 형식은 파싱 실패한다.
- 모델에 회상된 절차는 새 형식이고 저장된 key/pageup 원본은 그대로인 것을 확인했다.
- Anthropic SDK fake 시험에서 fill/click/key/scroll, null 복원, 원래 point/source 보존,
  전송 스키마의 속성 및 대안 수를 확인했다. 유료 API 수용 시험은 하지 않았다.
- 이전 테스트의 provider 단계 스크롤을 새 형식으로 변경했다. 내부 실행기 시험은 이전
  key/pageup·pagedown 표현을 유지한다. 일반 key의 불필요한 source=null과 click의 keys=null은
  새로운 분리 형식에 맞춰 fixture에서 제거했다.
- 120 성공 candidate의 기존 case/cache를 read_memory_candidates로 읽기 전용 재생했다.
  후보 12개와 실제 전달된 pagedown 2개가 보존됐다. 편찬 모델 호출이나 산출물 저장은 하지 않았다.
- `.\.venv\Scripts\python.exe -m pytest -q --tb=short`: **744 passed, 3 xfailed, 50.45초**.
- `.\.venv\Scripts\python.exe -m ruff check .`: 통과.
- `.\.venv\Scripts\python.exe -m ruff format --check .`: 통과.
- `git diff --check`: 통과.

## 범위와 한계

이 작업은 필드를 나눈 응답 계약 변경이다. 모델이 click과 scroll을 별개로 반환하면서
불필요한 click을 여전히 넣을 가능성까지 제거하지는 않는다. 그런 클릭을 purpose로 추측해
자동 삭제하지 않으며 기존 좌표 계약이 적용된다. 관찰 보완이 메서드 행동까지 교체하는
경로 역시 변경하지 않았다. 실제 ERP 성공률·시간·호출 수 개선은 아직 측정하지 않았다.
새 유효 scroll이 bbox 내부 클릭으로 취급되거나, 저장된 이전 메서드의 휠 의미가 바뀌거나,
SDK 변환에서 action별 속성이 혼합되면 이번 계약의 반증이다.
