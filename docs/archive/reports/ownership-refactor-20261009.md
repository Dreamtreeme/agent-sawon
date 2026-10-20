# 작업자 소유권 리팩토링 결과

기준 커밋은 `7be7710`이다. 이번 작업은 실제 ERP나 유료 모델을 호출하지 않고 fake/replay
회귀만 실행했다. 복잡도는 이전 보고서와 동일하게 함수 AST의 분기 노드를 세는 근사값이다.

## 결과

이전 변경은 큰 함수의 판단을 같은 클래스 안의 헬퍼로 옮긴 부분이 있었다. 이번에는 판정과
부수효과의 소유자를 실제 모듈 계약으로 이동했다.

- `context`, `click_refinement`, `screen_batch`는 `Brain`을 받지 않는다. 세 모듈의 직접
  `Brain` 속성 접근은 각각 6개, 12개, 10개에서 모두 0개가 됐다.
- `matching.validate_match_plan`은 정규화, 완료 근거, 로그인 경계, 복수 행동 경계, 입력
  범위 검증을 독립 규칙으로 나눴다. 기존 공개 호출 계약은 유지했다.
- 저장 증거의 editor 후보 계산은 `evidence_projection`의 순수 요청/결과로 이동했다.
  `SavedEvidence`는 세션 연속성과 증거 저장만 적용한다.
- 텍스트 입력 검증과 완료 검증은 각각 `input_verification`, `completion`이 판단하고,
  `Brain`은 검증 결과를 중앙 상태와 로그에 반영한다.
- 선택 모드는 `selection`이 결정하며, 로그인/화면 입력/저장 검토/일반 판단이 동일한
  `SelectionResult` 경계로 돌아온다.
- 행동 정책과 릴레이 payload 작성은 `action_preparation`이 소유한다. pHash 재결합,
  캡처 갱신, 좌표 출처와 물리 스크롤 변환을 포함하며 실행 전 상태 반영은 `Brain`에 남겼다.
- OCR 블록, 렌더링 페이지, OCR 프로토콜, append-only 저장소, 지속 로컬 워커를
  `sawon_runtime`으로 이동했다. 이 패키지는 `brain`이나 `pipeline`을 import하지 않는다.
  내부 사용자를 공용 경로로 옮긴 뒤 사용되지 않는 과거 재수출은 후속 정리에서 제거했다.

## 정적 지표

| 지표 | 기준 `7be7710` | 완료 | 변화 |
|---|---:|---:|---:|
| 100줄 이상 함수 | 20 | 17 | -3 |
| 근사 복잡도 40 이상 함수 | 7 | 4 | -3 |
| `Brain` 클래스 | 2,778줄 / 75메서드 | 2,631줄 / 73메서드 | -147줄 / -2 |
| `SavedEvidence` 클래스 | 539줄 / 14메서드 | 508줄 / 14메서드 | -31줄 / 동일 |
| 프로덕션 Python | 83파일 / 16,807줄 | 90파일 / 17,520줄 | +7파일 / +713줄 |

전체 줄 수는 줄지 않았다. 묵시적인 객체 접근을 요청·서비스·결과 계약으로
바꾼 비용이다. 따라서 이번 결과를 코드 총량 감소로 보고하지 않는다. 대신 중심 클래스의
크기, 고복잡도 함수 수, 패키지 역방향 의존은 실제로 감소했다.

| 함수 | 기준 줄/복잡도 | 완료 줄/복잡도 |
|---|---:|---:|
| `validate_match_plan` | 139 / 56 | 16 / 1 |
| `_select_node` | 142 / 52 | 130 / 37 |
| `_check_complete` | 112 / 48 | 41 / 13 |
| `_observe_editor` | 79 / 31 | 49 / 7 |
| `_verify_text_input` | 94 / 22 | 83 / 14 |
| `_guard_action` | 62 / 38 | 66 / 22 |
| `_wire_action` | 161 / 39 | 41 / 7 |

옮겨진 함수도 다시 큰 단일 함수로 만들지 않았다. 새 판정 함수의 최대 근사 복잡도는
행동 가드 31, 최종 조회 완료 23, editor projection 15, 입력값 판정 10이다.

현재 복잡도 40 이상은 `bind_scene` 53, `prepare_case` 44, `SavedEvidence.after_action` 41,
`compile_match` 40의 네 함수다. 화면 의미 결합, 서류 준비, 저장 세션 수명, 응답 컴파일이라는
서로 다른 책임이므로 이번 화면 작업자 소유권 변경에 섞지 않았다. `_select_node`는 37로
내려갔지만 130줄이므로 예외 분류와 선택 결과 적용 경계를 다음 후보로 남긴다.

## 검증

- Ruff lint 통과, format 170파일 통과
- 전체 677개 테스트 통과
- branch coverage 포함 실행 105.28초
- 종합 coverage 87.6093% (기준 기록 87.253% 대비 +0.3563%p)
- statement coverage 90.2239%, branch coverage 79.6654%

실행 시간은 한 번의 로컬 실행이며 성능 개선 근거로 사용하지 않는다. 실제 ERP 화면 성공,
OmniParser 의미 정확도 개선, 마일스톤 종료도 이 결과로 주장하지 않는다.

## 누락 항목 후속 점검

1. `examples/shipment-task.json`에는 `identity_input_key`가 `hbl_no`로 선언되어 있다.
   예제 로더가 이 값을 보존하는지 `test_shipment_task_declares_duplicate_identity_input`에서
   직접 단언한다.
2. `brain/visual/markers.py`는 현재 10,310바이트의 유효한 UTF-8 Python 텍스트다. NUL과
   기타 비텍스트 제어 바이트가 없고 Git 속성도 이 파일을 binary로 지정하지 않으며,
   `git grep -I`도 텍스트로 읽는다. 업로드 덤프의 binary 표시는 저장소 파일이나 과거
   `.gitattributes`에서 재현되지 않아 덤프 생성기의 문자 판별 오분류로 판단한다. 원본 덤프
   생성기가 보존되지 않아 그 도구 내부의 정확한 판정식까지는 확정하지 않는다.
3. `MatchServices.call: Callable[..., BaseModel]`은 요청한 Pydantic schema와 반환 타입의
   관계를 지워 `ScreenMatch`, `ScreenPlan`, `SemanticDetection`의 멤버 접근을 mypy가
   거부하게 했다. 이를 schema 타입을 그대로 반환하는 제네릭 `ScreenModelCall` Protocol로
   교체하고 `Brain._call`도 같은 타입 관계를 선언했다. 보고된 `BaseModel` 반환 오류군은
   사라졌다. 현재 저장소에는 mypy 설정이 없으며, 일회성 mypy 1.20.2 점검에서 이 오류와
   무관한 기존 오류가 `matching.py` 19건, `engine.py` 25건 남아 있으므로 전체 type-check
   통과로 보고하지 않는다.
4. `matching.py`가 `MethodRuntime`을 import하고 `method_runtime.py`가 `ScreenMatch`를
   지연 import하던 양방향 의존을 제거했다. matching은 이제 필요한 `enabled`, context,
   `apply`, `remember` 계약만 `MatchMethodRuntime` Protocol로 받는다. AST import 점검 결과
   `matching -> method_runtime`은 0건이고 `method_runtime -> matching`만 남아 단방향이다.

## 상태·호환 계층 후속 정리

- 그래프 노드 입력, 반환 및 최초 invoke에서 반복하던 `WorkerState` 전체 `deepcopy`를
  제거했다. 이 그래프는 한 실행 상태를 네 책임 노드가 순차 인계하며 복사본을 병렬 분기나
  재시작 체크포인트로 소비하지 않는다.
- 복사를 위한 `copy_worker_state`, 상태 채택/완료 래퍼 두 메서드와 복사 격리만 확인하던
  테스트를 삭제했다. 네 노드는 전달받은 상태에 route를 기록해 그대로 반환한다.
- 일회성 `SelectionMode`, 사용하지 않는 입력 검증·저장 증거 인자, `MatchResult`의 중복
  procedure 값과 prompt context의 중복 notebook/contract 참조를 제거했다. 선택 schema는
  안전한 validation metadata에 필요하므로 `selection_schema()`로 유지했다.
- 프로덕션 호출이 없고 테스트만 과거 경로를 사용하던 `brain.runtime`과 공용 워커 이동 후
  남은 `pipeline.persistent_worker`, `pipeline.worker_process` 재수출을 삭제했다.
- 직전 core `e59957b` 대비 프로덕션은 94파일/17,610줄에서 90파일/17,520줄로 줄었다.
  전체677개 테스트가 67.12초에 통과했고 Ruff lint/format도 통과했다. branch 계측은
  당시 누적 coverage data에서 종합89.0493%로 보고했지만, 아래 `coverage erase` 재측정으로
  대체한다. 실제 ERP와 유료 모델은 다시 실행하지 않았다.

## 잔여 중복 2차 감사

- production 90개 Python 파일을 고신뢰도 미사용 심볼과 함수 본문 중복 기준으로 다시
  검사했다. 미사용 심볼은 0건이었고, 동일 구현은 navigation key와 visual rebinding에서
  각각 정의한 공백 제거·casefold 정규화 한 쌍만 확인됐다.
- 두 구현을 `brain.observe.values.compact_normalize`로 합쳐 화면 의미 키의 정규화 규칙을
  한 곳에서 소유하게 했다. HTTP worker의 로그 억제 override도 실제로 사용하지 않는 인자임을
  이름에 표시했다.
- 남은 `model_copy()`는 불변 설정·응답·학습 메서드 갱신이고, `RunLog`의 비용 객체 복사는
  append 뒤 호출자가 원본을 바꿔 로그 이벤트가 변하는 것을 막는다. 이들을 상태 스냅샷
  중복으로 분류하지 않았다.
- 프로덕션은 90파일/17,522줄이다. `coverage erase` 뒤 전체 677개 테스트가 80.32초에
  통과했고 Ruff lint/format도 통과했다. 전체 production 종합 coverage는 87.6118%,
  statement 90.2367%, branch 79.6503%다. 이 clean run이 앞서 누적 data에서 계산한
  89.0493%를 대체한다. 시간은 단일 실행값이며 성능 개선 근거로 사용하지 않는다.
