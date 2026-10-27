# 모델 판단 주변의 중복 재해석 제거

## 범위와 확인한 결함

기준 커밋은 `1f943da`다. 이번 작업은 의미 매핑 변환과 모델의 보완 요청을 코드가
재판단하는 부분에 한정했다. ERP 실행·유료 호출은 하지 않았다.

| 기존 처리 | 확인한 문제 | 변경 |
|---|---|---|
| 대상의 이름 일부·값·유일한 role로 필드 검색 | 선언되지 않은 완료 대상 `12`, `Quantity`, `first row`가 실제 row로 바뀜 | 추측 검색 삭제. 선언된 이름 또는 유일한 명시적 source 연결만 유지 |
| evidence 목록을 별도로 해석하고 입력값으로 source 역검색 | fields.source=unknown인데 값이 12라는 이유로 packages.count 증거가 생김 | fields.source/value에서 실행용 evidence를 한 번 파생 |
| 현재 행동의 대상이 있으면 needs_refinement/needs_image 해제 | 모델의 불확실성 요청이 실행 가능한 행동의 존재만으로 취소됨 | 해제 함수 두 개와 전용 role 판별 함수 삭제 |
| 이미 model_dump에 있는 source 재대입 | 같은 사실을 바로 다시 씀 | 중복 대입 삭제 |

입력 키 오연결, 완료 대상 추측 세 경우, 재탐지 취소의 합성 테스트 5건은 제품 변경 전
실패를 확인했다. 입력 키 테스트는 초기 fixture에 packages.unit이 빠져 있었으므로 보완한 뒤
다시 실행했고, 예외가 아닌 잘못된 packages.count 증거 생성 단언으로 실패하는 것을 확인했다.

## 계약과 테스트 변경

- provider 응답 스키마와 프롬프트는 변경하지 않았다. 선택적 evidence 목록은 읽을 수 있지만
  더 이상 source를 보충하거나 덮어쓰는 별도의 판단 근거로 쓰지 않는다.
- record 없는 변환 단계는 source와 명시적으로 읽은 값의 연결을 만든다. record가 있는
  실행 경계에서 알려진 source와 field_matches 비교를 거친다. 변환 자체는 성공 판정이 아니다.
- 미해결 completion/message/grid/row는 unresolved_annotations에 남기고 증거에서 제외한다.
  행동 대상의 미해결 오류와 좌표·실행·값 비교 계약은 유지한다.
- test_named_current_evidence_is_bound_without_a_second_grounding_call:
  모델 입력 fixture의 Master/Shipper에 명시적 source를 넣었다. 1호출·대상 연결 단언은 유지했다.
- test_review_evidence_uses_only_declared_field_sources:
  값 역검색 대신 명시적 source를 사용한다. 미지정/동일값 필드 미연결과 명시적 right 연결을 확인한다.
- test_semantic_mapping_field_reading_supplies_evidence_without_value_target_guessing:
  잘못된 별도 evidence target과 무관하게 필드의 source/value가 증거가 되는 의미로 이름을 수정했다.
- test_semantic_mapping_uses_explicit_result_names_for_completion:
  role 추측을 기대하던 입력을 정확한 grid/row 이름으로 수정했다. 완료 조건 두 개의 연결을 유지한다.
- test_semantic_mapping_never_retargets_actions_from_a_descriptive_fragment:
  부분 이름은 기존 KeyError, 정확한 이름은 실행 계획으로 연결됨을 함께 검증한다.
- 삭제 함수 직접 호출 테스트를 실제 select_screen_response 경로의 합성 이미지 보완 테스트로 교체했다.
  행동이 있어도 보완 호출이 수행되고, 반환 행동이 유지되는지를 확인한다.
- 새로운 합성 테스트는 source 오연결 1건, 완료 대상 추측 3건, 실제 decide_screen을 통한
  재탐지 요청 유지 1건이다. 실제 모델의 오독 빈도나 ERP 성공률 측정은 아니다.

## 정량과 검증

| 제품 파일 | 줄 수 전 → 후 | if 전 → 후 | 함수 수 전 → 후 |
|---|---:|---:|---:|
| brain/graph/screen_mapping.py | 432 → 383 | 23 → 12 | 5 → 5 |
| brain/graph/screen_decision.py | 2,378 → 2,314 | 145 → 139 | 52 → 49 |
| 합계 감소 | 113 | 17 | 3 |

- `.\.venv\Scripts\python.exe -m pytest tests/test_screen_decision.py -q --tb=short`:
  152 passed, 12.40초.
- `.\.venv\Scripts\python.exe -m pytest -q --tb=short`: 738 passed, 3 xfailed, 49.02초.
  기존 106 계열 xfail 세 건은 변경하지 않았다. 스키마 fixture 비교도 전체 테스트에 포함된다.
- `.\.venv\Scripts\python.exe -m ruff check .`: 통과.
- `.\.venv\Scripts\python.exe -m ruff format --check .`: 256 files already formatted.
- `git diff --check`: 통과.

## 남은 문제와 반증 조건

기억 인출 계획에 현재 mapping이 없을 때 모델에 관찰과 행동을 함께 요청하고 기존 계획을
교체하는 경로는 이번에 변경하지 않았다. 관찰 보완과 행동 재선택의 계약을 분리해야 하는
별도 작업이며, 이번 삭제로 120 인출 실패가 해결됐다고 주장하지 않는다.
관찰을 새로 얻지 않고 이전 값을 현재 값으로 사용하거나, 새 관찰을 얻으면서 기존 실행
대상의 ID를 그대로 붙이는 방식으로 이 경계를 임시 우회하지 않았다.

제거한 추측에 의존하던 부정확한 행동 대상 응답은 이제 자동 복구되지 않는다. 보조 주석은
미해결로 기록되고, 행동은 기존 대상 조회 오류로 드러난다. 모델 요청을 취소하지 않으므로
재탐지·보완 호출이 늘 수 있으며 기존 한도는 유지한다. 실제 실행 시간·성공률 효과는 미측정이다.
정확한 이름/source/좌표를 가진 정상 계획이 새로 거절되거나, 무관한 동일값 입력이 증거로
생기거나, 요청한 보완이 코드에 의해 취소되면 이번 변경의 계약을 위반한 것이다.
