# 106 관찰·행동 결합 가설: 1단계 무료 재생

기준 제품 코드: `380c0582bbd2cf8f29b6d94fb2961ba645018da0`.
제품 코드, 프롬프트, 설정은 변경하지 않았다. 실제 ERP와 유료 모델은 실행하지 않았다.

## 입력과 검증 범위

`tests/test_observation_path_equivalence.py`의 기존 `prepare_worker`,
`observed_response`, fake 모델·OCR·relay를 재사용했다. 106 응답의 실패 구조만
공개 테스트 값으로 축소한 **합성 입력**이며 원문 재생이나 실제 화면 실행이 아니다.
실제 `_observe → scan → decide_screen` 경로를 통과한다. 제품 함수 monkeypatch는 없다.

| 테스트 | 대조와 관찰 결과 | 가설 판정 |
| --- | --- | --- |
| `test_synthetic_106_click_declaration_must_not_discard_observation` | 대상 선언이 있으면 관찰값과 클릭 좌표가 유지된다. 선언만 제거하면 `field_name`의 `KeyError('Continue')`로 관찰 메모가 비어 있다. | 대상 선언 오류가 관찰 반영을 막는 것은 확인. 선언 없이 실행 대상을 연결하는 수정안의 충분성은 아직 미검증. |
| `test_synthetic_106_unused_zero_area_must_not_discard_observation` | 정상 입력은 메모에 반영된다. 참조 없는 text 필드 `[10,8,0,0]`만 추가하면 `bind_scene`의 `visual_bbox_outside_capture`로 정상 입력도 반영되지 않는다. | 사용하지 않는 요소의 오류가 전체 관찰로 전파되는 것은 확인. 실제 부모·증거 의존 요소까지 제외해도 된다는 의미는 아니다. |
| `test_synthetic_106_recovery_must_receive_current_observed_memory` | 빈 act 뒤 보완 요청의 `matched_inputs`에는 현재 확인한 입력이 있지만 notebook의 observed_inputs와 screen_nodes는 비어 있다. 보완 응답이 정상 반환된 후에는 동일 관찰값과 화면 경로가 저장된다. | 반영 순서와 요청 상태 불일치는 확인. 이 불일치가 실제 모델의 탭 재방문을 유발했다는 인과관계는 미확인. |

## 실패를 숨기지 않는 방식

세 테스트는 원하는 동작을 표현하며 현재 코드에서는 실패한다.
`--runxfail` 실행에서 **3 failed**를 확인했다. 평상시에는 `strict=True` xfail로
보관한다. 특정 결함의 예외 종류·내용·메모 유실까지 확인한 뒤에만 전용
`ObservationBlockedByPlan`을 발생시킨다. 다른 준비 오류나 단언 실패는 xfail로
숨기지 않는다. 수정 후 통과하면 XPASS로 실패하므로 해당 xfail을 제거해야 한다.

## 폐기 기준과 남은 검증

- 선언 누락 클릭을 실제 실행 계약으로 연결할 수 없다면 좌표만으로 연결하는 수정안을 폐기한다.
- 제외한 요소가 다른 필드의 부모·입력·증거에 필요하다면 불필요 요소라는 분류를 폐기한다.
- 최신 관찰이 일관되게 반영된 요청에서도 모델이 반복 행동을 선택하면 메모 갱신만으로 해결된다는 가설을 폐기한다.
- 관찰 선반영이 미실행 행동이나 미저장 상태를 완료로 만들면 구현을 중단한다.
- 이번 합성 재생은 위 수정안들의 효과를 입증하지 않는다. 원인 후보의 코드 경계만 고정했다.

## 검증 명령

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_observation_path_equivalence.py -k synthetic_106 --runxfail --tb=short -q
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
git diff --check
```

결과: 전체 **714 passed, 3 xfailed (51.68초)**. 새 3건을 제외한 기존 테스트는
모두 통과했다. Ruff check, format(247개 파일), `git diff --check`도 통과했다.
2단계 제품 수정은 시작하지 않았다.
