# 2-3 후속 확인 및 3-1 조사 종료

기준: core `71d732a`(제품 코드 기준 `eeac16d`), bench `c33c360`.
이번 변경은 두 저장소의 `.gitignore`와 이 조사 보고뿐이다. 제품·테스트·설정·저장 경로는
수정하지 않았다. 3-2, 4단계, 실제 파일럿·유료 호출은 시작하지 않았다.

## A. 2-3 변경 이력

- `eeac16de9edb1586efd7725b4ca849c1504fa9d9`
  `fix: warn on excess warm compilation anchors`
- **7개 파일, +364/-13**. 제품 1, 테스트 2, 문서 4개다.
- 제품: `brain/demo_learning/warm_methods.py`.
- 테스트: `tests/test_rejection_evidence.py`, `tests/test_warm_method_compiler.py`.
- 문서: `docs/contracts.md`, `docs/current-runtime.md`,
  `docs/archive/reports/rejection-policy-audit-20261020.md`,
  `docs/archive/reports/rejection-policy-stage23-20261020.md`.
- 초과 앵커만 앞 8개를 보존하고 경고한다. 확신도·stop·대상·좌표·입력값·행동 검증은 그대로다.

## B. 절단과 필터의 순서

현재 순서는 다음과 같다.

1. `assemble_warm_method`가 `WarmMethodAssembly`로 모델 응답을 검증한다.
2. `WarmGroupSelection.bound_context_anchors`
   (`brain/demo_learning/warm_methods.py:43`)가 9개 이상이면 `[:8]`로 자른다.
3. `compile_warm_method:390`은 이미 검증된 객체를 받거나 같은 모델로 검증한다.
4. `:436`에서 남은 앵커에 OCR 포함 검사와 업무 인스턴스 값 제외 검사를 적용한다.

따라서 **절단이 두 필터보다 먼저**다. OCR 검사는 대소문자를 무시한 부분 문자열 비교이고,
인스턴스 검사는 `source['values']`의 비어 있지 않은 값이 앵커 문자열에 포함되는지 검사한다.
두 필터 모두 원래 순서를 보존한다.

### 072 seed002 원문

bench 상대 경로:

- `evidence/seed-audit-20261017-072/seed-002/compilation/model-requests/1250bf39ca7842b2a3215ed0bf4ea082/response.txt`
- 같은 `compilation/assembly-input.json`의 후보 `c006`.

원문 `groups[4].context_anchors`를 수정하지 않고 현재 필터 식으로 대조했다.
아래는 업무 값이 아닌 화면 라벨이다.

| 순번 | 원문 앵커 | OCR 포함 | 업무 값 포함 | 필터 통과 |
|---:|---|---|---|---|
| 1 | House B/L 등록 | 예 | 아니오 | 예 |
| 2 | Consignee | 예 | 아니오 | 예 |
| 3 | 총중량 | 예 | 아니오 | 예 |
| 4 | HBL No. | 예 | 아니오 | 예 |
| 5 | 도착항 | 예 | 아니오 | 예 |
| 6 | 컨테이너 식별번호 | 예 | 아니오 | 예 |
| 7 | Shipper | 예 | 아니오 | 예 |
| 8 | 출발항 | 예 | 아니오 | 예 |
| 9 | **Master B/L 번호** | 예 | 아니오 | **예** |

9번째는 유효한 앵커다. 그러나 앞 8개도 모두 유효하므로, **이 사례에서 필터 후 절단으로
순서만 바꾸어도 결과는 같은 8개**다. 9번째 손실은 승인된 개수 제한의 결과이며, 필터
순서 때문에 추가로 잃은 앵커는 없다. 과거 072는 절단이 아니라 응답 전체 거절이었다.

일반적인 반례는 앞 8개 중 하나가 OCR/인스턴스 필터에서 탈락하고 9번째는 통과하는
경우다. 현재는 7개, 필터 후 절단하면 8개가 남는다. 이는 가능한 반례이며 072의 실측은 아니다.

대안은 원문 앵커 목록을 편찬기까지 유지하고 **필터 후 앞 8개를 선택**하는 것이다.
이 경우 경고의 발생 위치와 개수 의미(원문 초과/유효 앵커 초과)를 정해야 하며,
검증된 draft의 내용도 달라진다. `brain/cache/methods.py`의 그룹 해시는 목록 순서를
보존하므로 일부 편찬 산출물의 그룹 ID도 달라질 수 있다. 앵커 순서를 좌표 실행 순서로
사용하지는 않지만 카탈로그·프롬프트·그룹 해시에 나타나므로 무의미한 순서가 아니다.
이번에는 순서와 제한을 변경하지 않았다.

## C. 진단 디렉터리 Git 제외

변경 전 두 저장소에 대해 `git ls-files -z`를 읽고, 경로의 디렉터리 구성요소가 정확히
`diagnostics`인 파일을 조사했다. **각각 0개**였다. 이름에 diagnostics가 들어가는
bench `tests/test_startup_diagnostics.py`는 진단 산출물 디렉터리가 아니며 제외 대상도 아니다.

| 저장소 | 커밋 | 변경 |
|---|---|---|
| core | `71d732aafc3810fd4312efd10e3d4c5ac5a1b9ca` | `.gitignore` 1개 파일, `diagnostics/` 1줄 추가 |
| bench | `c33c360f5cf537f298c9d141edcfb0ffe4441fb4` | `.gitignore` 1개 파일, `diagnostics/` 1줄 추가 |

두 커밋 메시지는 `chore: ignore private diagnostics directories`다.
삭제·untrack·강제 add는 하지 않았다. 원래 untracked인 core `tmp/`와 bench evidence는 그대로다.

두 저장소에서 각각 다음 경로를 `git check-ignore -v`로 확인했다.

```text
evidence/seed-audit-20261017-072/seed-002/compilation/diagnostics/model-example.json
core: .gitignore:14:diagnostics/
bench: .gitignore:17:diagnostics/
```

072는 진단 계측 도입 전 실행으로, 원문이 `model-requests/`에 있고 이 진단 파일은
실제로 존재하지 않는다. 위 명령은 **072 evidence 아래에 생성될 진단 경로**에 대한 규칙
매칭 검사다. 검사용 파일을 생성하거나 기존 원문을 이동하지 않았다. 다른 evidence
전체를 새로 제외하는 변경도 하지 않았다. 두 커밋의 `git diff --check`는 통과했다.

## D-1. 현재 병합 대상과 상태 소유권

경로 수는 같은 업무 사실이 들어오는 진입 종류와 실제 기록 함수를 구분하여 센다.
함수 수가 많다는 이유만으로 서로 다른 의미의 상태까지 합치는 것은 적절하지 않다.

### (a) 상태 갱신

| 사실 | 현재 진입/갱신 경로 | 현재 합류점 | 필요한 병합 여부 |
|---|---|---|---|
| 관찰값·입력 키 대응 | 일반 모델 해석, 빈 행동 보완, 동일 캡처 재사용, 화면 캐시, 편찬 메서드의 5가지 관찰 진입. 필드 배치는 이 관찰을 검증 소비 | 의미 응답은 `ScreenMapping.observed_bindings/local_payload`; Brain은 `_remember_model_task_evidence` → `_record_model_evidence` | 일반·보완·캐시를 새 기록기로 갈아엎을 필요 없음. 편찬 메서드의 좌표/증거 결합만 별도 생산 경로로 남음 |
| 개별 입력·배치·이미 충족된 입력 | `_verify_text_input`, `_verify_selected_option`, `_collect_editor_evidence`, `_verify_screen_batch`, `_reconcile_action_plan` 등의 호출 | `_record_verified_field` → task/model-review에서 `_record_model_evidence` | 이미 공용 기록 함수 사용. 진입별 대상 선택과 검사까지 한 함수로 밀어 넣을 근거 없음 |
| 최근 행동·이정표 | 일반 행동 검토와 필드 배치 검토의 2가지 진입 | `_record_action_verification:2728`, `_verify_screen_batch:2851` 모두 `_remember_action_effect:2758` 사용 | 과거 배치 조기 반환 누락은 해소. 추가 통합 불필요 |
| 상세 검토 대기 | 저장 후 query의 click/double_click/key를 후보로 설정; editor 관찰로 확정. 새 저장/거절 시 정리 | `ProgressState.saved_detail_transition_pending/step`, `_begin_saved_detail_review:2781` | 행동보다 오래 유지되는 위치로 이미 이동됨. 새 FSM/저장소 불필요 |

공통 기록의 의미도 이미 분리되어 있다. `_record_model_evidence:1907`는 ledger와 verified,
재조회 증거 범위를 함께 갱신한다. `_remember_model_task_evidence:1958`은 현재 보이는
불일치만 무효화하고, 가려진 필드의 이전 증거는 보존한다. `screen_batch.py:282`는
주입받은 같은 `_record_verified_field`를 호출한다. 재사용 카탈로그는
`brain/visual/perception.py:181,711,763`에서 선택 필드 값과 pHash를 복원한다.

편찬 경로는 `MethodLibrary.bind_with_context`와 `bind_visible_value_evidence`가 같은
`_coordinate_bindings`를 서로 다른 시점에 호출한다. 이 문제를 수정하게 된다면 기존
결합 결과의 재사용/marker 등록을 함께 다루는 함수 병합이 우선 후보다. 새 전역 ID 관리자나
정책 계층이 먼저 필요한 것은 아니다. 다만 실제 후보 생성 경로 확인은 D-3처럼 제한된다.

### (b) 문맥 조립

대상은 **3가지 요청**이다: 최초 판단, 교육 상세 조회 뒤 판단, 빈 행동 보완.
최초는 `build_match_prompt:1522`, 교육은 `select_match_response:1678`의 expanded,
빈 행동은 `ground_match_response:2034`의 prepared.state를 바탕으로 만든다.
`context.execution_context:154`가 조건부 예상 상태를 계산하고,
`Brain._screen_match_request:338`이 현재 증거 범위를 선택한다.

이미 해소된 부분: 제공된 교육 ID 차집합 검사, 기존 단계와 새 단계 합집합,
빈 행동 보완에 기본 task/교육/필드 문맥 전달, 실제 확인과 조건부 예상의 분리다.

**새로 확인한 조합상 누락(합성 재현):**

| 호출 | 목적 | 실제 전달된 상세 단계 |
|---:|---|---|
| 1 | screen_decision | login, open-query |
| 2 | education_retrieval | login, open-query, **save-record** |
| 3 | missing_actions_recovery | login, open-query — **save-record가 빠짐** |

기존 `setup_brain/named_response` fake를 사용해 `save-record` 상세 요청 → 빈 act 응답 →
stop 응답을 순서대로 공급했다. 제품 monkeypatch나 테스트 파일 수정 없이 실제
`Brain._observe()`를 호출한 결과다. 원인은 `select_match_response:1806`이 로컬 변수
`prepared`만 교체하고, `MatchSelection:327`은 response/cache/method/used_image만
반환하기 때문이다. 호출자 `match_screen:2181`은 이전 prepared로 후속 검증·보완을 수행한다.

063~072의 해당 패턴 case 로그 15개에서 같은 decision_id의 교육 조회 다음 빈 행동
보완을 검색했으나 일치 사례는 없었다. 065의 해당 보고/실행 로그는 찾지 못했다.
따라서 **현재 코드의 누락은 합성 재현으로 확인했으며, 과거 파일럿 실패 원인으로 확정하지 않는다.**

최소 병합 후보는 이 3가지 요청이 **갱신된 동일 prepared 문맥을 이어 사용**하게 하는 것이다.
기존 조립 함수와 반환 계약을 연결하면 가능하며 새 클래스/FSM/정책 계층은 필요 없다.
교육 문구 변경이나 전 턴 교육 영구 캐시 도입까지 확대할 근거도 아직 없다. 구현하지 않았다.

### (c) 작업 전체 상태 보관 위치

| 위치 | 보관 내용/역할 | 중복 상태인가 |
|---|---|---|
| `WorkerState`의 7개 section (`state.py:131`) | observation, decision, execution, review, lifecycle, progress, methods | 작업 중 변경 상태의 소유 구조 |
| `ProgressState:69` | verified, ledger, rolling history, 저장/상세 검토, 교육 focus/completed, 실패 기록 | Brain property는 이 저장소로 접근하며 별도 복사본 아님 |
| `MethodState` (`method_runtime.py:38`) | 메서드 bindings, used, pending, staged 등 | WorkerState.methods에 들어 있음 |
| Brain의 task_instruction/values와 MethodContext | 교육·입력·업무 계약 및 그 실행 문맥 | 실행 사실과 구별되는 입력 데이터 |
| VisualPerception catalog.selected_fields | 해당 프레임의 값·geometry 재사용 | 작업 전체 verified의 대체 저장소 아님 |
| `task-notebook.json` (`notebook.remember:15`) | 입력 경로, verified/remaining, 최근 행동 등의 진단용 스냅샷 | 실행 중 권위 있는 메모를 다시 읽는 경로는 저장소 검색에서 없음 |
| RunLog, 관찰 JSON, method/cache 파일 | 이력, 프레임, 성공 경로의 영속 산출물 | 서로 다른 수명/목적. 모두 하나의 현재 메모로 합치면 안 됨 |

따라서 작업 전체 상태가 없어서 새 TaskRuntimeState가 필요한 상황은 아니다.
`task-notebook.json`은 overwrite되지만 실제 증거 누적은 `ProgressState`와 ledger가 소유한다.
스냅샷과 현재 메모리 장면의 시점 차이는 로그 분석 시 명시해야 한다. 이 둘을 동일 obs_id만으로
동일 객체라고 간주한 것이 071/072 증거 ID 7건의 오진 원인이었다.

## D-2. 063~072 지적사항의 현재 분류

분류: **실제·현재** = 실제 기록이 있고 같은 코드 결함이 현재도 남음;
**합성만** = 합성 재현으로만 현재 결함 확인; **해소** = 해당 코드 원인이 현재 제거됨.
해소는 재파일럿 일반 성공을 보장한다는 뜻이 아니다. 아래 표는 구체적인 코드 원인을
분류하며, 원인이 확인되지 않은 모델 행동/성능 관찰은 뒤에 따로 둔다.

core `7cfd5b4`는 064~071 사이 수정들을 묶어 커밋한 기록이다. 각 실행 직전 별도 커밋이
있었다고 가정하지 않는다. 아래 seq는 각 case의 `run_log.jsonl` 기준이다.

| 지적사항/출처 | 분류 | 현재 코드 및 기록 근거 |
|---|---|---|
| 063 seed004 warm: Consignee 라벨이 입력 키로 들어가 ExecutionFacts 실패 | 해소 | 063 seq356/358/361/364. `ScreenMapping.local_payload/resolve_input_key`, `7cfd5b4`; 064 보고의 원문 재생에서 정규 키 5개 수용 |
| 063/064 description: 보이는 값의 별도 evidence 누락, 동일 캡처 경로에서 증거 회복 못함 | 해소(수신한 값 처리 부분) | 063 seq242–246, 064 seq204–222. `observed_bindings/local_payload`, perception 값 복원, `7cfd5b4`. 모델이 값 자체를 오독한 경우까지 해결한 것은 아님 |
| 066: 이미 제공된 교육 재조회, 기존 상세 교체, 첫/후속 응답 처리 차이, 이를 고정한 2회 호출 테스트 | 해소 | 066 호출17→18/21→22; `select_match_response:1762` 이후 supplied/missing/merge, `7cfd5b4`. 067 seq483/484에서 행동 보존, 교육 재호출 0 |
| 066: 조건부 예상 remaining을 실제 verified로 덮어씀 | 해소 | 066 호출14; `context.execution_context`, `build_match_prompt`, `7cfd5b4`. 067 호출16 실제5/예상11/조건부 remaining0 |
| 066: 첫 저장 상세 판단에 저장 전 증거가 남음 | 해소 | 066 seq491→498; `_screen_match_request`가 상세 후보 증거 범위를 미리 선택, `7cfd5b4`; 071 호출17 후보→18~20 saved_detail |
| 066: task에서 갱신 안 되는 query/duplicate 플래그가 프롬프트에 노출 | 해소 | 066 호출5/21; task용 workflow_state에서 해당 3개 제거, `7cfd5b4` |
| 066: 필드 배치 recent_turns/milestone 갱신 누락 | 해소 | 066 호출14의 입력 이력 부재; `_verify_screen_batch`→`_remember_action_effect`, `7cfd5b4`; 067 호출9에서 입력 성공 이력 확인 |
| 066: 이전 메서드 purpose가 현재 목적처럼 보임 | 해소 | `MethodRuntime.prompt_context`의 `previous_action_purpose`, `7cfd5b4` |
| 067: 수정 버튼으로 상세를 열면 식별 문자열 조건 때문에 검토 시작 안 됨 | 해소 | 067 호출22–26, 시작 이벤트 없음. query의 click/double_click/key 후보와 실제 editor로 연결, `7cfd5b4`; 071 seq391 시작 |
| 067: 일치값 fill 생략이 증거 경로와 분리 | 해소 | 067 seq302–309; `_reconcile_action_plan`→`_record_verified_field`, `7cfd5b4`; 동등성 테스트 기존 통과 경로 |
| 068~071 통합 보고: 현재 빈칸/다른 값인데 과거 증거만 남음 | 해소 | `_remember_model_task_evidence`의 observed_bindings 불일치 무효화, `7cfd5b4`; 보이지 않는 필드는 보존 |
| 같은 보고: 실제 click/key/double_click인데 intent=fill로 검증 반려 | 해소 | `ScreenMapping` 구체 행동 정규화, `7cfd5b4`; 클릭 종류/좌표 보존 테스트 |
| 같은 보고: 빈 행동 보완이 기본 task/교육/관찰값을 버림 | 해소(기본 문맥 경로) | `ground_match_response` prepared.state 전달 및 동일 프레임 필드 병합, `7cfd5b4`; 070 호출20, 071 호출10. 새 교육 직후 조합은 별도 아래 항목 |
| 069: 동일 캡처인데 pHash null로 바뀌어 변화로 판정 | 해소 | seq182 identical_form_reused /184 navigation_observed_change, 관찰0007→0008. perception pHash/selected_fields 복원, `7cfd5b4` |
| 069: action=fill/intent=search의 source를 먼저 지워 재계획 | 해소 | 호출18→19; intent 정규화를 source 정규화보다 앞에 둠, `7cfd5b4` |
| 070: 중간 dialog에서 상세 대기가 다음 행동에 유실 | 해소 | 호출17→19–21 evidence_scope=entry; ProgressState 대기 소유, `7cfd5b4`; 071 seq391 및 후속 saved_detail |
| 072 seed000/001 warm: core pHash 허용 뒤 relay 정확한 해시 거절 | 해소 | seed001 seq24–26/50–52; bench `0554bf4`가 내용 동일성 게이트 제거. 2-4 종료 보고의 현행 코드 대조. 새 화면 실행은 안 함 |
| 072 seed002: context_anchors 9개로 전체 편찬 파싱 실패 | 해소 | 원문 groups[4], core `eeac16d` 앞8+경고. B의 원문 재검사 |
| 072 기록 실행기 runpy/Pydantic 참조 오류 | 해소(로컬 실행기) | `seed-000/compilation` 오류와 `compilation-trace-fixed` 성공, status.compilation_attempts. 제품 수정 대상 아님 |
| 기존 repeated-observation visual_proposal_unknown_marker xfail | 합성만 | `tests/test_method_runtime.py::test_compiled_coordinates_reach_executor_without_model[True]`, 현재도 xfail. 반복 결합 ID가 카탈로그 범위를 벗어남. 운영의 동일 원인 발생 미확인 |
| 새 compiled-warm 메모 유실 xfail | 합성만 | `test_observation_path_equivalence.py:125–213`: action3/evidence4. read_warm_candidates 우회. D-3 참조 |
| control_cleanup: 보조 evidence 투영 누락이 행동까지 반려 | 합성만 / 정책 분류 보류 | `test_screen_matching.py:2657`, marker31/controls34. 의미 객체 누락 → bind_proposal 거절. compiled-warm의 geometry 누락과 동일 원인 아님. 변경 금지 유지 |
| 이번 발견: 교육 확장 후 빈 행동 보완에서 확장 상세 유실 | 합성만 | D-1의 실제 함수 fake 재생. select의 로컬 replace가 호출자로 반환되지 않음. 063~072 실제 발생 없음 |

이 범위에서 **실제 기록으로 확인되고 그 동일 코드 원인이 현재도 남았다고 확정한 항목은 0건**이다.
이는 현재 제품에 결함이 없다는 뜻이 아니다. 합성 재현 4종과 아래 미확인 관찰을 구분한 결과다.

### 결함으로 확정하면 안 되는 관찰/판정 정정

요청한 세 분류는 결함의 확인 수준이다. 아래 항목은 결함 자체/단일 원인이 확인되지 않아
억지로 '해소'나 '실제·현재'로 넣지 않는다.

| 항목 | 확인된 사실 / 미확인 부분 |
|---|---|
| 065 | 해당 seed-audit 보고와 실행 로그가 없다. 다른 날짜의 번호065를 대체 자료로 사용하지 않음 |
| 064 실패 후 동일 업무 로직의 066 성공 | 기록된 결과 변동. 개선 효과나 수정을 증명하지 않음 |
| 066/067 로그인 재입력, 반복 조회, 스크롤 탐색, 탭 왕복 | 실제 모델 행동은 기록됨. 067 seq27은 relay 거절이지만 바뀐 픽셀/정확한 원인은 미확인. 072의 확인된 게이트 문제를 모든 이전 반복의 원인으로 소급하지 않음 |
| 066 호출8의 bbox 밖 point, 호출9의 빈 steps, 호출14의 저장 설명+fill 행동 | 실제 응답 불일치. 유효 저장 행동을 코드가 삭제한 증거 아님. 현재 실행 정확성 검증을 없앨 근거 아님 |
| 교육 상세의 턴 간 비지속 | `_taught_procedure_progress`가 focus 주변2개를 다시 구성하는 사실은 남음. 매 턴 모든 상세를 영구 저장해야 한다는 계약/필요성은 미확인. 이번 발견인 같은 판단 내 유실과 구분 |
| completed_taught_steps 희소성 | `_complete_taught_procedure_step`의 확정 기록과 active focus는 다른 의미. 빈 목록을 모든 이전 단계 미완료로 읽으면 안 되지만, 실제 오동작 원인이었다는 증거는 없음 |
| 071/072에서 증거 ID가 저장 관찰에 없던 7건 | 거절0/재계획0, 즉시 hbl_no 메모 기록 및 행동 전달 성공. 현재 bind_mapping 이후 메모리 장면과 앞서 저장한 JSON의 시점 차이. 이전 증거 유실 추정은 철회 |
| 072 seed000 warm 성공 | 화면 캐시8회·모델6회, 최초 메서드 interrupted. 완전한 편찬 재현 성공이 아님. Stage4 집계는 아직 구현하지 않음 |
| 편찬 순번 충돌/중복 query 제거(063) | 063 seed003 실패로 해당 경로 재검증 못함; seed004는 후보13→12 편찬. 실패를 새로 재현한 항목이 아니며, 현재 trace_index/전달 순서 테스트 통과 |

7건의 구체적인 seq 연결은 [2-3 보고](rejection-policy-stage23-20261020.md)의 표를 따른다.
071 seq113/440, 072 seed000 cold115/warm144, seed001 cold114, seed002 cold117/139에서
각각 다음 seq에 메모가 기록되고, 이어 컴파일과 ok=true act 반환이 있었다.

## D-3. 편찬 웜 xfail 입력이 실제 선별 경로를 통과하는가

현재 xfail은 처음부터 `Reference.value`를 목표값으로 둔 채 fill 응답을 주고,
`cold._observe()`만 수행한다. 그 뒤 cache.finish(success=True)와 직접 만든
`WarmCandidate`를 사용한다. 실제 전달 행동/성공 실행 로그를 읽지 않는다.

정상 경로는 다음과 다르다.

1. `Brain._reconcile_action_plan:2092`가 현재 활성 입력의 값이 목표와 같고 관찰 증거로
   수용되면 fill을 제외한다. `observed_actions_already_satisfied`를 dispatched=false로 기록한다.
2. `read_warm_candidates:181`는 delivered transition을 실제 requested 시점/obs_id/계획 구간으로
   결합하고, 같은 구간의 satisfied 목록을 별도로 읽는다.
3. 전달된 행동만 retained에 넣는다. 생략된 fill은 satisfied_actions로 분리한다.
4. 전달 행동이 하나도 없으면 nonexecuted_plans에 기록하고 후보를 만들지 않는다.
5. 다른 click만 전달됐다면 fill은 빼고 click만 후보에 남는다. 매핑 정규화 이후에도
   `response['steps'] = retained`를 사용하므로 과거 모델 fill이 복원되지 않는다.

**따라서 동일 값이 정상 수신·수용되어 생략된 fill이 실제 편찬 fill 후보로 남는다는
현재 xfail의 입력 경로는 성립하지 않는다.** 생성기가 값 자체를 독립적으로 검사하는 것은
아니다. 값 오독/미수신/수용 실패로 실제 fill이 전달됐다면 delivered 쪽이 우선이므로 후보가
될 수 있다. 또한 콜드에서는 빈칸이어서 유효하게 전달된 fill을 웜에서 이미 채워진 화면에
다시 결합하는 다른 상황은 가능하다. 이 둘의 운영 발생을 현재 xfail이 증명하지는 않는다.

072의 저장된 assembly-input 4개를 대조했다.

| 편찬 입력 | 후보 수 | 업무 값 fill | 현재 값 일치 발견 | 분리된 satisfied fill |
|---|---:|---:|---:|---:|
| seed000 compilation | 13 | 12 | 0 | 0 |
| seed000 compilation-trace-fixed | 13 | 12 | 0 | 0 |
| seed001 compilation | 13 | 12 | 0 | 2 |
| seed002 compilation | 12 | 12 | 0 | 1 |

같은 seed000 재개본을 독립 실행 표본으로 세지 않는다. 일치 검사는 target의
selected_fields.value와 control.text_ids가 가리키는 OCR 텍스트/연결 텍스트에 기존
field_matches를 적용했다. 화면 전체의 다른 위치에서 같은 값이 보이는 것과 혼동하지 않았다.
이것은 저장된 입력 검사이며 재결합 이후 ID 유실의 운영 증명은 아니다.

071/072의 실제 `method_bound`는 072 seed000/001 warm 로그인 각1건이다. 둘 다 행동
ID14/15/16을 새로 만들었지만 evidence=[]다. 업무 입력 수집 뒤 proof ID가 분리되는 운영
사례는 아직 없다. 새 xfail은 유지할 합성 신호지만 이를 근거로 read_warm_candidates의
필터를 제거하거나 유효한 성공 편찬을 고장이라고 확정하면 안 된다.

## D-4. 3-2 후보와 불필요한 재구현

| 판단 | 항목 | 승인 시 필요한 범위/선행 근거 |
|---|---|---|
| 수정 가치 있음, 현재는 합성 근거 | 같은 판단 안의 교육 확장 문맥을 후속 빈 행동 보완까지 전달 | 기존 prepared 조립·반환 연결. 새 클래스 없이 가능. 현재3호출의 상세 목록 차이를 회귀 기준으로 사용 |
| 선행 재현 보강 후 판단 | 편찬 행동/증거의 geometry 결합, 반복 관찰의 marker 갱신 | 실제 reader를 거친 유효 cold 후보→warm 결합으로 재현할 것. 두 xfail을 동일 원인으로 합치지 말 것 |
| 이번 승인에서 제외/보류 | control_cleanup의 보조 오류가 행동에 미치는 영향 | 사용자가 분류를 보류한 항목. 계약·허용 동작 결정 전 변경하지 않음 |
| 재구현 불필요 | 공통 메모 기록, 배치 최근 행동, 값/키로 증거 생성, 동일 캡처 값/pHash 복원 | `7cfd5b4`와 현행 동등성 검사로 이미 존재 |
| 재구현 불필요 | 제공된 교육 재조회 제거/기존 목록 합집합 | 이미 구현. 위의 후속 prepared 전달 누락만 별개 |
| 재구현 불필요 | 상세 대기를 작업 상태에 보관하고 첫 상세 판단의 증거 범위를 분리 | 이미 ProgressState 및 request view에 구현 |
| 불필요/근거 부족 | 새 중앙 상태 클래스·FSM·정책 계층, 모든 snapshot의 단일 저장소화, 전 턴 교육 영구 캐시 | 현행 소유 구조와 좁은 누락으로 설명 가능. 새 구조 필요성 미확인 |
| 별도 승인 필요 | 필터링 후 앵커 절단, Stage4 결과 집계 | B의 정책/해시 영향 또는 원래 Stage4 범위. 이번 구현 없음 |

## 검증, 한계, 반증 조건

제품 파일을 수정하지 않고 기존 테스트만 실행했다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_warm_method_compiler.py::test_warm_candidates_keep_navigation_after_satisfied_input tests/test_warm_method_compiler.py::test_critic_graph_keeps_execution_order_and_excludes_wholly_skipped_plan tests/test_observation_path_equivalence.py tests/test_method_runtime.py::test_compiled_coordinates_reach_executor_without_model -q
```

결과: **17 passed, 2 xfailed, 3.89초**. 기존 두 xfail이며 새 테스트·단언 변경은 없다.
교육→빈 행동 보완의 fake 재생은 별도 임시 디렉터리에서 수행했고 자동 정리했다.
모델 프롬프트/제품 파일을 수정하지 않았다. 문서·ignore만 변경하므로 전체 pytest/lint/format은
반복하지 않았다. 문서 경로 및 최종 `git diff --check`를 확인한다.

반증 조건:

- 현재 코드의 새 교육 내용이 빈 행동 보완에서도 유지되는 원문이 동일 조건에서 나오면,
  로컬 prepared 유실 진단의 조건을 다시 검토해야 한다.
- 실제 reader 출력에서 정상적으로 skipped 처리된 fill이 retained에 다시 들어간다면,
  후보 제외에 대한 결론은 틀린 것이며 구간 결합을 재조사해야 한다.
- 071/072의 해당7건에서 메모 기록이 없는 다른 재결합 경로를 발견하면,
  현재7건 성공 결론을 그 경로로 일반화할 수 없다.
- 새 필터 순서의 효과는 첫8개 중 필터 탈락이 있는 원문으로 검증해야 한다.
  현재072의9개 사례는 그 효과를 증명하지 못한다.

실행 시간·웜 성공률·호출 비용의 개선은 이번 조사로 주장하지 않는다.
3-2와 4단계의 순서 및 구현 범위는 다음 사용자 승인 전까지 보류한다.
