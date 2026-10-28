# 탐색·기억 활용 흐름을 유지하는 주변 코드 정리 후보

## 조사 범위

기준 제품 코드 `3d5831d`, 조사 시작 HEAD `c57f69a`.
모델이 화면에서 정보를 누적 결합하고, 기억 인출이 그 결과를 사용하는 현재 흐름을 유지한다.
이번 조사는 그 흐름 주변의 중복 계약·변환·준비 작업과 별도 설정 경로를 대상으로 했다.
실패를 새로 정의하거나 기억 사용 방식을 바꾸는 작업이 아니다. 제품 코드와 테스트 파일은
수정하지 않았고, ERP·유료 모델도 호출하지 않았다.

검토 기준은 ① 반환값을 실제 소비하는지, ② 같은 정보가 다시 만들어지는지,
③ 현재 경로와 별도 설정 경로 중 어디서 필요한지, ④ 제거 후 유지할 계약이 무엇인지다.
현재 실행에서 호출되지 않았다는 사실만으로 함수를 삭제 대상으로 정하지 않았다.

## 우선 후보

| ID | 위치 | 현재 처리 | 제거·통합할 부분 | 유지할 부분 |
|---|---|---|---|---|
| C1 | `screen_mapping.py:68,115,147` | 모델이 fields와 별도 evidence 목록을 반환하지만 실행 증거는 fields의 source/value에서 생성 | `NamedEvidence`와 모델에게 중복 목록을 요구하는 계약 | 내부 `ScreenMatch.evidence`, 완료 조건의 `completion_evidence` |
| C2 | `context_recall.py:97`, `screen_decision.py:1473,2033`, `screen_mapping.py:280` | 상태를 문자열로 만든 뒤 JSON을 다시 찾아 읽고 화면 부분·지시문을 교체 | 문자열→JSON 왕복과 최종 요청에 남지 않는 구형 지시문 조립 | 업무·교육·메모·복구 사유 및 최종 실제 프롬프트 |
| C3 | `screen_decision.py:169,2050` | 응답 파싱 때 빈 화면으로 임시 실행 payload를 만들고 실제 화면으로 다시 생성 | 빈 화면의 임시 번호·controls·evidence 생성 | 모델 JSON 구조 검증과 실제 프레임에서의 한 번의 실행 변환 |
| C4 | `screen_decision.py:2212,2307`, `visual_turn.py:8` | 정수 타깃을 `m번호` 문자열로 만들고 중첩 객체를 순회해 현재 ref로 다시 치환 | 내부 중간 문자열과 재귀 변환 | 현재 프레임 ref 생성, 대상 존재 검사, 실행 스키마 검증 |
| C5 | `screen_decision.py:895,1781` | 의미 이름 기반 응답에도 구형 OCR 타깃→컨트롤 증거 재연결을 수행 | 새 ScreenMapping 경로에서 결과를 바꾸지 않는 재연결 호출 | fields에서 생성한 증거, 아직 지원하는 숫자 응답의 처리 |
| C6 | `screen_decision.py:1934` | 정리·타깃 보완·검증을 같은 응답에 반복 적용 | 응답·대상이 바뀌지 않은 구간의 중복 통과 | 새 보완 응답이나 타깃 변경 후 필요한 검증 |
| C7 | `memory_retrieval.py:390` | 두 분기가 모두 `groups = remaining`; 이미 필터한 used를 반복 검사 | 동일 분기·중복 필터 | 후보 집합·순서·대체 행동 처리 의미 |

이 표의 C2–C6은 함수 전체를 바로 지우는 목록이 아니다. 없앨 중간 단계를 식별한 목록이다.
특히 계약의 생산자·소비자를 함께 바꾸지 않고 한쪽만 삭제하면 현재 흐름을 깨뜨린다.

## C1 — 모델이 별도로 적는 입력 증거 목록

`ScreenMapping.local_payload()`는 `self.evidence`를 읽어 증거를 만들지 않는다.
`fields`의 명시적 source와 value를 읽고, 입력과 일치하는 필드에서 내부 evidence를 만든다.
모델의 별도 목록은 원문·mapping 직렬화에는 남지만 실행 증거의 원천은 아니다.

124의 탐색 응답 16개와 기억 인출 응답 12개를 실제 화면 카탈로그·동일 업무 입력으로 재생했다.

| 비교 | 탐색 | 인출 | 합계 |
|---|---:|---:|---:|
| 비교한 mapping | 16 | 12 | 28 |
| 모델의 별도 evidence가 비어 있지 않았던 응답 | 14 | 0 | 14 |
| 별도 evidence를 비워도 실행 payload 동일 | 16 | 12 | 28 |
| 변환 후 screen 데이터도 동일 | 16 | 12 | 28 |

따라서 중복 응답 항목의 제거 후보로 근거가 강하다. 다만 `extra=forbid`인 계약이므로
필드 삭제만 하면 과거 mapping을 읽는 캐시·편찬기가 거절할 수 있다.
`memory_compilation.py:316,350`, 화면 캐시의 저장 mapping, provider 스키마 fixture까지
같은 변경 범위로 잡아야 한다. 기존 기록의 보존·재생 방식을 정하지 않고 즉시 삭제하지 않는다.
필드 삭제가 실제 모델의 응답 양상에 미치는 영향은 이번 읽기 전용 재생으로 측정하지 않았다.

## C2 — 요청 문자열을 만들었다가 다시 해체하는 어댑터

현재 경로:

```text
Context Recall의 dict
 → model_screen으로 번호 기반 화면 표현 생성
 → 기존 SCREEN_DECISION_RULES/SCREEN_PLAN_RULES + JSON 문자열
 → traced_call → mapping_prompt
 → 문자열에서 JSON을 찾아 파싱
 → grounded_interpretation 제거, 화면을 text_regions/control_regions로 교체
 → CONTEXT_MAPPING_RULES + JSON 문자열
 → 실제 모델 호출
```

현재 모델에 전달되는 화면·규칙을 구조화된 상태에서 직접 만들면 중간 문자열과 파싱을 줄일 수 있다.
`TARGET_GROUNDING_RULES`처럼 SemanticDetection 보완에 실제 사용되는 프롬프트는 구분해서 유지한다.
정상·교육 확장·빈 행동 보완·거절 복구 등 각 호출의 최종 요청 내용과 순서가 같아야 한다.
이 과정에서 현재 모델 지시를 개선하거나 바꾸는 작업을 섞지 않는다.

인출에서 프롬프트를 미리 만들 필요도 분리할 수 있다. 124 인출은 모델 0회인데
12번, 391,111자의 문자열을 조립했다. 인출·캐시 선택에 필요한 상태는 유지하고
원격 호출용 문자열만 필요한 지점에서 만드는 후보이다. 시간 절감량은 아직 측정하지 않았다.

## C3 — 빈 화면에서 하는 임시 실행 변환

`ScreenPlan.semantic_mapping()`은 provider 응답을 `ScreenMapping`으로 읽은 뒤
`local_payload({})`로 임시 번호를 생성한다. 이후 `traced_call()`이 같은 mapping에
현재 screen과 inputs를 넣어 다시 `local_payload()`를 수행한다.

기존 테스트 11건을 함수 교체 없이 프로파일링했을 때 해당 경계에서 빈 화면 변환 11회,
현재 화면 변환 11회가 관찰됐다. 모델 응답 구조 검증은 필요하지만 프레임 없는 실행 번호 생성은
줄일 후보다. 공급자 공용 계측, 원문 저장, 비용 기록을 건너뛰지 않고 모델 응답과 실행 계약을
한 번 연결하도록 바꾸는 범위이다. 공용 `local_payload()` 자체를 제거하는 제안이 아니다.

## C4 — `m번호`를 거치는 내부 참조 변환

`compile_match()`가 정수 대상에서 `m12` 같은 중간 ref를 만들고,
`bind_proposal()`이 중첩 dict/list를 순회하면서 다시 `e{obs_id}0{번호}`로 바꾼다.
프로덕션의 `bind_proposal()` 호출자는 `compile_match()` 한 곳이다.
현재 관찰은 이미 그 함수에 있으므로 처음부터 최종 ref를 생성할 수 있다.

원천 정수 대상→현재 관찰 ref라는 변환과 허용 ref 검사는 유지한다. 지울 후보는 중간 표기와
그 표기를 해체하는 재귀 순회다. `adapt_proposal()`은 로그인·입력 배치·저장 검토의 실제
스키마 형태를 맞추므로 이 후보에 포함하지 않는다.

## C5 — 의미 이름 응답에 적용하는 OCR 증거 재연결

`bind_value_evidence_to_controls()`는 evidence 타깃이 source=ocr인 경우에만 바꾼다.
`ScreenMapping.local_payload()`의 증거 타깃은 각 field에 배정한 source=model_selected
마커다. 124 응답 28개에서 이 함수를 적용한 결과는 모두 동일 객체였고 변경은 0건이었다.

그러므로 새 의미 매핑 경로에서는 이 단계의 제거 근거가 있다. 함수 전체를 제거하려면
숫자형 ScreenMatch를 직접 받는 테스트·replay·호환 경로를 함께 확인해야 한다.
테스트에 호출이 있다는 이유만으로 제품 기능이 필요하다고 판단하지도, 반대로 해당 테스트를
전부 삭제하지도 않는다. 어떤 입력 계약을 유지할지에 따라 기존 검증을 새 경로로 옮겨야 한다.

## C6 — 같은 응답을 반복 정리하는 구간

`ground_screen_response()`에는 정상 경로 기준으로 sanitize 3곳, 타깃 보완 2곳,
별도 검증 유지 1곳이 있다. 그 앞의 `validate_or_replan()`과 뒤의
`ground_screen_response()`가 각각 `prepare_screen_plan()`도 호출한다.

기존 11개 테스트 프로파일에서 ground 경로 6회에 대해:

- sanitize 18회: 반환 응답이 모두 동일했다.
- retain_action_targets 12회: 모두 동일했다.
- retain_verification 6회: 모두 동일했다.
- 뒤쪽 prepare_screen_plan 6회: **3회는 응답을 변경했다.**

마지막 항목 때문에 두 번째 계획 검증을 무조건 지우는 것은 후보에서 제외한다.
보완 모델 응답이나 대상 보완으로 내용이 바뀐 뒤에는 다시 검증할 필요가 있다.
동일 응답의 반복 처리만 한 번으로 모으는 후보이며, 실제 오류 복구 기능을 없애는 후보가 아니다.
위 숫자는 선택한 기존 테스트의 프로파일이며 실제 ERP 전체 호출 횟수가 아니다.

## 별도 운영 결정이 필요한 큰 후보

| ID | 별도 경로 | 현재 프로필에서 쓰지 않는 근거 | 바로 삭제하지 않는 이유 |
|---|---|---|---|
| R1 | `screen_matching=False`의 별도 계획·단일 판단 경로 | `_plan_node()`의 Plan 호출, `_decision_prompt()`, `_choose_decision()`의 구형 분기가 현재 true 설정으로 건너뛰어짐 | CLI가 여전히 false 설정을 받으며 테스트도 사용. 지원 종료 여부를 먼저 정해야 함 |
| R2 | `visual_model_only_review=False`의 값 재판독·저장 증거 경로 | `_invalidate_editor_evidence`, `verify_screen_fields` 후반부, `_begin_saved_query_proof`의 진입 조건 | 해당 옵션을 유지하면 실제 기능. 현재 누적 메모 기반 경로를 유일한 운영 경로로 정할 때 함께 정리 가능 |
| R3 | 별도 recovery 모델 계층 | 현재 `recovery_enabled=False`, screen_matching 경로는 `_choose_decision()`에서 먼저 분기 | core CLI와 모델 예산 설정에 공개된 옵션이며, 주 경로의 일반 모델 예외 복구와 다름 |

이 셋은 현재 설정에서 사용하지 않는 경로이지 저장소 전체에서 도달 불가능한 코드는 아니다.
삭제 범위를 확정하려면 옵션·문서·생성자·fixture까지 함께 정리해야 한다.
로그인·입력용 `ScreenFillPlan`/`LoginScreenPlan`은 현행 경로에서도 실행 어댑터로 쓰인다.
따라서 `screen_batch.py`, `selection.py`, SavedEvidence 계열 파일을 통째로 구형이라고 지우지 않는다.

## 후보에서 제외한 것

- `screen_nodes` 누적, source/value 결합, 기억 편찬, 입력 전달 기록, 정상 인출과 모델 예외 복구.
- pHash·ORB·OCR·현재 창/좌표 갱신: 서로 다른 역할이며 호출 0건만으로 불필요하다고 보지 않았다.
- `guard_mode` 전체 삭제: observe_only에서도 계속 적용되는 실행 계약과 비용 계측 등이 섞여 있다.
- 메모와 최근 행동 버퍼의 일괄 통합: 필드 사실과 행동 이력은 다른 정보를 담는다.
- 파일 반복 파싱에 새 캐시 계층 추가: 앞선 측정에서 작았으며 이번 코드 축소의 우선순위가 아니다.

## 확인 방법과 실행 결과

- `python -m vulture brain models pipeline sawon_runtime --min-confidence 80`: 종료 0, 지적 없음.
- `python -m ruff check brain models pipeline sawon_runtime --select F401,F841`: 모두 통과.
- `.tools/cleanup-candidates-124/inspect_candidates.py`: 원본 28개 매핑의 격리된 변환 비교,
  동일 분기 AST 검색. 원본·제품 파일은 변경하지 않음.
- `.tools/cleanup-candidates-124/profile_existing_tests.py`: sys.setprofile로 기존 함수 실행을
  관찰. 내부 mapping까지 포함해 반환 응답을 비교한 최종 실행은 기존 fake/replay 테스트
  11 passed, 139 deselected, pytest 표시 시간 5.89초. 프로파일 비용이 포함된 시간이다.
  함수 교체·제품 패치·네트워크·데스크톱 실행 없음.

실행 Python은 모두 루트 `.venv/Scripts/python.exe`였다. 전체 테스트는 코드 변경이 없고
이번 목적이 후보 조사여서 반복하지 않았다. 위 비교는 제거 후 전체 시스템 검증이 아니다.

## 권장 순서

1. C2·C3·C4: 최종 모델 요청과 실행 요청을 보존하며 불필요한 중간 표현을 줄인다.
2. C5·C6: 새 의미 매핑 경로에서 결과를 바꾸지 않는 처리만 줄인다.
3. C1: 저장 mapping·편찬 재생 호환 범위를 정한 뒤 중복 응답 계약을 제거한다.
4. C7: 같은 행동을 유지하는 작은 중복으로 함께 정리할 수 있다.
5. R1–R3은 운영 옵션 폐기 결정 후 별도 범위로 다룬다.

제거 줄 수와 속도 개선량은 아직 확정하지 않는다. 같은 코드를 여러 후보가 공유하므로
함수 길이를 합산해 예상 삭제량으로 제시하지 않았다. 이번 산출물은 조사 보고서뿐이다.
