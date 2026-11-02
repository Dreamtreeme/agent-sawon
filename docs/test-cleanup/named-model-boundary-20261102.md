# 화면 모델 경계 통합 — 2026-11-02

기준 커밋: `eb585c1`. 두 번째 문제인 모델 입력의 연결 정보 보존을 최소 패치가 아닌
중복 계약 제거 방식으로 수정했다. 이번 검증은 로컬 fake/replay 테스트이며 API·ERP 실행은 없다.

## 변경된 흐름

```text
현재 캡처 + OCR + 탐지 영역
  → frame_input: 텍스트 / 영역 / 영역에 포함된 텍스트를 한 번 투영
  → 기존 실행 메모·교육과 함께 모델에 전달
  → ScreenMapping: 의미 이름 / bbox / source / 현재 읽은 값 / 행동
  → 실행용 현재 참조로 한 번 투영 → 수용 계획 검증
  → accepted_mapping: 수용된 행동을 의미 매핑에 동기화
  → 기억 저장·편찬은 의미 매핑 직접 소비
```

현재 프레임을 만드는 책임은 `brain/visual/frame.py`에 모았다. `mapping_prompt`는 받은
프레임을 다시 만들지 않는다. OCR 내부 번호와 탐지 번호를 모델 번호로 바꾸던
`brain/visual/marker_contract.py`는 삭제했다. 외부 OCR도 `text_regions`에 유지하고,
영역 내부 OCR은 `control_regions[].contained_text`로 함께 전달한다. 이는 기하 연결이며
컨트롤의 의미를 코드가 정하는 규칙은 아니다.

## 제거·통합 범위

| 기존 중복 | 변경 후 |
|---|---|
| `model_screen` → `mapping_prompt`에서 화면 다시 작성 | `frame_input` 결과를 그대로 사용 |
| `model_screen`, `model_controls`, `local_response`, `MarkerReferenceError` | 모델 번호 codec 및 전용 재시도 삭제 |
| 숫자 의미 응답용 `SCANNING_RULES`, `SemanticRequest.prompt` | 단독 관찰·녹화기도 `ScreenMappingWire` 사용 |
| `retain_verification` / `retain_action_targets`의 서로 다른 보완 응답 | `retain_screen_observation`에서 전체 named 응답 수용 |
| 편찬 2곳·인출 1곳의 `local_payload` 재호출 | 세 호출 제거, 의미 필드 직접 소비 / 결합된 실행 참조 재사용 |
| 수용한 실행 계획과 원본 mapping의 행동이 다를 수 있음 | 저장 직전 `accepted_mapping`으로 행동·주석 동기화 |

실행·SceneObservation·relay에서 사용하는 현재 참조는 유지한다. 같은 매핑을 재투영하면
기존 model_selected 영역을 교체하므로 ID와 마커가 누적되지 않는다. 과거 숫자 형식 실행
기록을 읽는 편찬 경계도 유지한다. 별도 선택 경로의 `Choice.ref`와 상세 클릭 보완 계약은
이번에 제거한 모델 화면 번호 codec과 다른 계약이며 이번 변경 대상이 아니다.

과거 값은 인출된 현재 관찰로 복사하지 않는다. 메서드에는 필드 구조와 source를 저장하고,
새 실행의 실제 값과 현재 관찰을 사용한다. 현재 프레임과 누적 기억은 다른 항목으로 전달한다.

## 통합 중 확인한 회귀와 보완

- 보완 요청이 계획을 바꾼 뒤 다시 다른 관찰 보완을 수행하면 마지막 계획이 검증을
  건너뛸 수 있었다. 모든 보완 뒤 최종 계획을 검증하도록 순서를 맞췄다. disabled 버튼으로
  바뀌는 합성 두 번째 보완이 실제 검증기에서 거절됨을 확인했다.
- 모델이 현재 source·값을 명시했는데 창 크기 변경으로 이전 영역이 무효화되면 보완을
  요청하는 회귀를 확인했다. 기존의 명시적 현재 evidence 인정 경로를 보존했다. 합성 테스트는
  수정 전 불필요한 모델 호출로 실패했고 수정 후 추가 호출 없이 통과한다. 실제 값 비교는 유지한다.
- 단독 관찰의 표 상태·부모·포커스가 소실되지 않도록 기존 관찰 속성을 named 필드의
  `table_state`, `parent`, `focused`로 보존했다. 부모는 이름으로 전달하고 내부에서 ID로
  투영한다. 미선언 보조 부모 때문에 전체 응답을 거절하지 않는다. `uncertain` 표의 기존
  실행 제한은 유지한다.

## 검증 근거

| 검사 | 구체적으로 잡는 결함 |
|---|---|
| `tests/test_visual_frame.py` | 140의 두 버튼 기하로 OCR 연결 유실, ID·순서에 의존한 연결, 원본 프레임 변경 검출 |
| `tests/test_screen_decision.py` | 최종 프롬프트의 연결 보존, 재투영 시 ID 누적, 수용 후 제외한 행동·주석의 캐시/기억 재등장 검출 |
| `tests/test_replan_pipeline.py` | 마지막 보완 계획의 검증 누락, 창 크기 변경 뒤 명시 입력 evidence 유실 검출 |
| `tests/test_visual_record.py` | 실제 recorder와 scanner를 fake 모델로 연결하여 named 계약·비용 상한·빈 표·부모·포커스 손실 검출 |
| `tests/test_memory_compilation.py` 및 기존 기억 테스트 | 실제 전달 행동·필드 이름·source·좌표 보존, 과거 값의 현재 관찰 승격 방지 |
| `tests/test_vocabulary.py` | 의도된 두 모델 스키마 외 변경 검출; 두 fixture는 세 관찰 속성 추가를 반영 |

기존 숫자 codec 전용 테스트를 삭제하고 현재 프레임 연결 테스트로 대체했다. 수용 mapping
검증은 기존 교육 입력 표면 테스트 한 건을 확장했다. source/value의 생략·빈 문자열·명시 null,
스크롤 방향과 소수점 좌표를 실제 `decide_screen` → 캐시·기억 전달 → 실행 투영으로 확인한다.

실행 명령과 최종 결과:

```powershell
.\.venv\Scripts\python.exe -m pytest
# 777 passed, 2 xfailed in 46.86s
.\.venv\Scripts\python.exe -m ruff check .
# All checks passed!
.\.venv\Scripts\python.exe -m ruff format --check .
# 279 files already formatted
git diff --check
# 통과
```

두 xfail은 기존 106 합성 관찰의 zero-size 필드와 이전 메모 보완 사례이며 이번에 새로
xfail 처리하거나 완화하지 않았다. 스키마 fixture는 `ScreenPlan`, `ScreenMatch`의
NamedScreenField 세 속성만 변경했으며 의도된 변경을 테스트 상단에 기록했다.

## 크기와 주장 범위

제품 Python 기준 443줄 추가·517줄 삭제, **순수 74줄 감소**다. 문서·테스트·fixture는 제외하고
새 frame.py를 포함했다. 삭제된 모델 번호 codec은 112줄이다. 기억 편찬의 직접 named 처리와
최종 수용 매핑 동기화, 기존 관찰 동작 보존 때문에 모든 파일이 작아지지는 않았다.

현재 테스트는 연결의 보존과 실행 계약을 검증한다. 140의 입력 연결 누락이 잘못된 모델 선택의
유일한 원인이었다거나, API 호출 수·ERP 실행 시간이 줄었다고 확정하지 않는다. 실제 모델이
동일한 기하·텍스트를 받아도 의미를 바꿔 선택할 수 있으며 이는 후속 화면 검증에서 확인해야 한다.

반증 조건은 다음과 같다: 최종 프롬프트에서 연결이 다시 유실됨, 생략한 현재값이 과거 값으로
복원됨, 수용에서 제외한 행동이 기억으로 재등장함, 보완의 최종 행동이 실행 검증을 건너뜀,
단독 관찰에서 이전에 표현 가능했던 표·부모·포커스가 소실됨. 이번 로컬 검사에서는 재현되지 않았다.
