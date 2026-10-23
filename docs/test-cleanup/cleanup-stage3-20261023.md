# 3차 정리: 화면 해석과 계획의 결합

## 수정된 계획과 조사

[계획](cleanup-criteria-20261023.md)에 후보 확정 전 차이 조사, 실제 함수 연결 검증,
정보 유실 변이 탐지, 전후 요청·출력 비교를 추가했다. 089 정상 재생을 오류 경로의
검증으로 대체하지 않으며, 086 누락 구간에 의존하는 상태 통합은 보류한다.

| 항목 | grounded_screen_plan | replan_after_rejection |
|---|---|---|
| 기존 해석 생산자 | request.grounded_interpretation | 거절된 응답의 interpretation() |
| 새 계획 생산자 | 모델 ScreenPlan 요청 | 거절 사유를 포함한 모델 ScreenPlan 요청 |
| 실제 반환 타입 | ScreenPlan 또는 traced_call이 만든 ScreenMatch | 동일 |
| 결합 우선순위 | 기존 해석 뒤에 새 계획 dump | 동일 |
| mapping 전달 | dump 제외 필드이므로 plan.mapping을 별도 부착 | 동일 |
| 후속 처리 | 선택 경로의 증거 결합 후 호출자가 검증 | prepare_screen_plan으로 즉시 검증 |
| 기록 | cached-plan, grounded_interpretation_planned | action-correction, combined_action_corrected |

`decide_screen.traced_call`은 의미 이름 기반 응답을 local_payload로 변환한 뒤,
ScreenPlan 요청에도 실제 ScreenMatch를 반환할 수 있다. 따라서 새 계획이 항상
행동 필드만 가진다는 가정은 틀리다. 새 해석이 있으면 새 controls/screen_kind/uncertainties가
우선해야 한다. 타입 이름만 보고 기존 해석을 무조건 유지하도록 변경하지 않았다.

| 필드 | 결합 규칙 |
|---|---|
| controls, screen_kind, uncertainties | 순수 ScreenPlan에는 없으므로 기존 해석 유지. ScreenMatch이면 새 값 우선 |
| needs_refinement | 두 타입에 모두 존재하며 새 계획 값 우선 |
| steps, evidence, purpose, expected_result 등 계획 필드 | 새 계획에서 전달 |
| mapping | 직렬화에서 제외되므로 새 계획의 객체를 그대로 연결; 입력 객체는 변경하지 않음 |

유사 결합 함수가 있는지 brain/tests를 검색했으며 재사용할 정의는 없었다.
결합 자체는 동일하지만 호출 문맥과 후속 검증이 다르므로 두 경로 전체는 합치지 않았다.

## 변경

- `ScreenMatch.from_plan`에 결합과 mapping 연결을 한 번 정의하고 두 경로에서 호출한다.
- 기존 인라인 결합 두 곳을 삭제했다. 새로운 서비스·상태·정책 계층은 없다.
- 독립 결합 정의 **2→1**, 호출당 dump/검증/copy 횟수는 동일하다.
  줄 수 감소나 모델 호출 감소를 성과로 주장하지 않는다. 작은 classmethod의 유지 비용은
  추가되지만 mapping 전달과 덮어쓰기 규칙을 수정할 위치가 한 곳으로 줄었다.
- 프롬프트·스키마·거절 조건·artifact/이벤트 이름·경로별 검증은 유지했다.
- 기존 미커밋 완료창 정규화와 계획 보존 수정은 별도 변경으로 남겼다.

## 검증과 한계

`tests/test_plan_composition.py`는 두 경로 × 두 응답 형태의 합성 4조건을 검사한다.
모델 호출만 fake로 대체하며 build_decision_prompt, select_screen_response,
validate_or_replan, prepare_screen_plan 및 내부 결합·검증은 실제 함수를 실행한다.
재계획은 실제로 좌표가 없는 click을 주어 실제 검증 오류로 진입한다.
전체 ERP나 실제 모델 공급자의 응답 능력을 검증하는 테스트는 아니다.

1. 제품 수정 전 4조건 통과를 확인했다.
2. 두 기존 결합 지점에서 mapping 연결을 제거한 테스트 프로세스 변이는
   4조건 모두 mapping이 None이 되는 단언 실패로 검출됐다.
3. 통합 후 4조건의 프롬프트 원문, 응답 dump, mapping dump가 수정 전과 완전히 같았다.
   원본 해석·계획·mapping 불변과 새 mapping 객체 연결도 확인했다.
4. 통합된 함수의 덮어쓰기 순서를 뒤집은 합성 변이는 4조건 모두 검출됐다.
5. 089 전체 오프라인 재생은 요청 19회·행동 29건·노드 88회 및 정상 종료가 유지됐다.
   verified/post_save_verified는 각각 기존 11개 키와 같았다.

두 결함 종류의 유한한 변이 탐지이지 전체 결함 탐지율이 아니다. 089가 두 오류 경로를
모두 실행했다고 주장하지 않는다. full-match 사례는 traced_call의 반환 형태를 합성한
것이며 해당 변환 전체의 별도 신규 검증으로 세지 않는다.
비공개 자료와 전후 비교 결과는 `.tools/cleanup-stage3/`에 보존한다.
실제 ERP·유료 모델 호출은 없었다. 테스트 삭제와 coverage 측정은 하지 않았다.

실행 결과: `.venv/Scripts/python.exe -m pytest` **702 passed, 72.81초**.
`-m ruff check .`, `-m ruff format --check .`(231개 파일),
`-m vulture brain models pipeline sawon_runtime scripts tests --min-confidence 80` 통과,
`git diff --check` 통과. 단일 테스트 실행 시간을 성능 개선 근거로 사용하지 않는다.

반증 조건: 요청 문맥, 새 계획 우선순위, mapping 연결, 후속 검증 결과 중 하나라도
이전과 달라지거나 입력 객체가 수정되면 동작 보존 주장이 반증된다.
