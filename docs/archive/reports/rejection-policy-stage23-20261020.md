# 2-3: 승인된 앵커 제한 완화와 추가 조사

2026-10-20. 이전 기준 커밋은 core `5a9758c`, bench `21157d0`이다.
이 문서는 감사 결과이며 운영 지침을 대체하지 않는다.
제품 변경은 `WarmGroupSelection.context_anchors` 초과 수용과 경고 기록뿐이다.
2-1 저장 정책과 3-1 메모/투영 경로는 조사만 했다. 2-4는 해당 없음으로 닫는다.
실제 ERP, 외부 API, 새 콜드/웜 실행은 하지 않았다.

## 변경 전 필드 조사와 제외 결정

길이 제한 후보는 `brain`, `models`, `pipeline`의 `max_length` 정의와 이전 분류 CSV를
대조했다. 아래 줄 번호는 수정 전 `5a9758c` 기준이다. 사용자 조건인
“매칭·동등성 비교·실행 결정에 쓰이는 필드는 제외”에는 후속 판단 모델에 다시 제공되는
문맥도 포함했다. 코드의 직접 분기와 모델 판단 문맥을 구분하여 기재한다.

| 정의 위치 / 필드 | 제한 | 이후 사용처 | 조치 |
|---|---:|---|---|
| `brain/demo_learning/warm_methods.py:35,37` `WarmGroupSelection.purpose`, `expected_result` | 160 / 240 | `compile_warm_method` → `method_from_match`; 그룹 전체 직렬화가 그룹 ID 해시에 포함됨(`brain/cache/methods.py:379–392`). 후속 그룹 선택·used 판정의 ID에 간접 영향 | 제외 |
| `brain/demo_learning/warm_methods.py:51` `WarmMethodAssembly.purpose` | 160 | `LearnedMethod.purpose` → `MethodLibrary.catalog`, `MethodRuntime.prompt_context` → 모델의 메서드 선택 문맥; 재사용 응답의 `procedure_step`에도 사용 | 제외 |
| `brain/demo_learning/methods.py:28,31` `MethodMeaning.purpose`, `expected_result` | 160 / 240 | purpose는 메서드 카탈로그, expected_result는 그룹 해시 및 재사용 응답. `method_from_draft:197–203`은 이미 수동 절단하지만 공급자 응답 파싱은 그보다 앞서 거절함 | 제외. 060과 같은 길이 거절을 이번에 모두 해소했다고 주장하지 않음 |
| `brain/graph/matching.py:144` `MethodChoice.purpose` | 160 | `ScreenPlan`/`ScreenMatch` 상속; `MethodRuntime.remember`로 전달되어 그룹 해시, 보관 상태, 후속 메서드 문맥에 사용 | 제외 |
| `brain/graph/matching.py:197` `ScreenPlan.expected_result` | 240 | 그룹 해시, `compile_match`의 `Decision.reason`, 클릭 보완 요청의 이전 기대 결과 | 제외 |
| `brain/graph/schemas.py:86,87` `Decision.subgoal`, `reason` | 160 / 1000 | subgoal은 `_record_action_verification`의 feedback에 들어가 `_current_feedback`을 통해 다음 판단에 제공. reason은 중복 조회 feedback 및 `ClickRefinement.prepare`의 `previous_expectation`에 제공. 두 문자열 자체를 직접 비교해 클릭 여부를 결정하는 것은 아님 | 모델 실행 판단의 문맥이므로 제외 |
| `brain/graph/schemas.py:205,206` `WireDecisionBase.subgoal`, `reason` | 160 / 1000 | WireAct/Stop/Duplicate/Complete/TaskCompleteDecision → 내부 Decision 변환; 위와 같은 소비자 | 제외 |
| `brain/graph/screen_batch.py:51` `ScreenFillPlan.purpose` | 160 | `prepare_screen_fill()`에서 `Decision.subgoal`로 변환 → 행동 feedback | 제외 |
| `brain/graph/matching.py:60,61` `ActionCapability.goal`, `completion` | 500 / 500 | 행동 능력/교육 목표와 완료 조건으로 모델에 전달. 실행 판단을 위한 내용이며 단순 부가 설명이 아님 | 제외 |
| `brain/visual/scene.py:37` `ControlMeaning.visual_description` | 160 | `bound_visual_description`이 이미 파싱 전 절단. 이후 장면 이름, `selected_fields` 조회 키 및 대상 이름에 사용 | 현재 장문으로 파싱 거절하지 않음. 의미 매칭에도 사용하므로 제외 |
| `brain/graph/click_refinement.py:31` `DetailedClick.explanation` | 300 | `bound_explanation`이 이미 파싱 전 절단. 응답 기록과 coordinate_review feedback에 보존 | 현재 장문으로 파싱 거절하지 않으므로 제외 |
| `brain/graph/scratchpad.py:18,22,36` `RollingTurn.target_summary`, `observed_effect`, `StructuredScratchpad.current_goal` | 120 / 120 / 200 | 모델 응답 파싱 대상이 아닌 내부 문맥. `record_turn`, `build_scratchpad`가 생성 전에 이미 절단; 후속 판단 프롬프트로 전달 | 제외 |

이 표는 문자열 제한 19개를 다룬다. 현재 장문 거절이 없거나 판단에 다시 쓰이는 필드를
제외한 결과, **이번 (b)의 변경 필드는 0개**다. `TaskInstruction`의 교육 문장/ID,
`MatchAction.source`, `NonemptyRef`, 문서의 `VisionField.value_text/unit_text`는 사용자
입력·식별자·실행값 계약이므로 제외했다. 이전 CSV가 `RollingTurn.turn_index`의 `ge=0`도
경고 후보로 표시한 것은 범위가 넓은 분류였으며, 문자열 길이 제한이 아니므로 이번 대상이 아니다.
보류/애매함, 확신도, stop, 교육 표면 및 완료 증거 관련 검증은 수정하지 않았다.

## 2-3(a): 앵커 순서와 실제 변경

`context_anchors`는 `compile_warm_method`가 원래 순서대로 순회하면서 현재 OCR에 없는
문자열과 업무 인스턴스 값을 필터링한다. `method_from_match`는 리스트를 그대로 저장하고,
그룹 전체 JSON의 SHA-256으로 그룹 ID를 만든다(`brain/cache/methods.py:383,391–392`).
`sort_keys=True`여도 **리스트 순서는 정렬되지 않으므로 앵커 순서가 그룹 ID에 영향을 준다.**
카탈로그와 `MethodRuntime.prompt_context`에도 같은 순서로 제공된다.
반면 `_coordinate_bindings`는 pHash, 이미지 크기, 저장 좌표를 사용하며 앵커 문자열의
순서로 대상을 매칭하지 않는다. 그룹 실행 순서는 `trace_index`와 `predecessor_group_ids`가 정한다.

- `WarmGroupSelection`의 `max_length=8` 파싱 거절을 제거했다. 문자열 리스트 타입 검증은 유지한다.
- 모델 검증 이후 길이가 8보다 크면 **앞의 8개만 유지**한다. 정렬·중복 제거·대체 선택을 하지 않는다.
- `warm_context_anchors_truncated retained=8 discarded=N` 경고를 Python logging에 기록한다.
  원문 앵커 문자열이나 candidate ID를 경고 메시지에 넣지 않는다.
- 버린 개수는 Pydantic `PrivateAttr`에 보관하고, 편찬기에서 `RunLog`의 verify 이벤트로
  `check`, `level=warning`, `group_index`, `retained`, `discarded`를 남긴다.
  이 속성은 응답 스키마/직렬화/그룹 해시에 들어가지 않는다.
- 원문은 기존 `models/base.py`의 비공개 응답 파일에 남는다. `assembly-draft.json`은
  정규화된 8개를 저장한다. 직접 스키마 파싱만 하는 호출은 logging 경고가 남고,
  `assemble_warm_method` 경로에서는 실행 로그에도 영구 기록된다.
- 공급자 스키마의 `context_anchors.maxItems`도 없어져, 공급자가 9개를 반환해도 로컬
  수용 경계에 도달할 수 있다. 나머지 필드 제약은 그대로다.

기존 8개 이하 응답의 순서·값·그룹 ID는 달라지지 않는다. 9개 이상은 과거에는 유효한
그룹 ID가 생성되지 않았으므로, 과거 거절 응답과의 ID 동일성을 주장하지 않는다.

### 변경한 단언과 새 경계 테스트

| 테스트 | 변경/추가 단언 | 근거 |
|---|---|---|
| `test_ninth_compiler_anchor_preserves_raw_response_and_same_validation_error` → `test_ninth_compiler_anchor_warns_and_preserves_raw_response` (저장 정상/실패 2조건) | `ValidationError(too_long)` 대신 첫 8개 동일성과 경고 확인; model_call의 validator 오류 대신 success 확인 | 사용자가 승인한 유일한 거절 완화. 원문 바이트 동일성, 호출 1회, 진단 저장 실패 시 결과 불변은 유지 |
| 위 테스트 | 공급자 스키마의 `maxItems`가 없음을 확인 | 로컬 수용 전에 공급자 스키마가 8개로 강제하지 않도록 함 |
| `test_anchor_overflow_records_warning_without_relaxing_candidate_ids[accepted]` | 앵커 9개인 응답을 편찬하고 경고 RunLog 1건, 첫 8개 순서, 좌표·입력 slot 보존 확인. 수동으로 8개를 준 결과와 action_groups 전체 동일 | 경고가 실제 기록되고 앵커 순서/그룹 ID/실행값을 임의 변경하지 않음 |
| 같은 테스트 `[bad-target-id]` | 같은 9개 응답에 존재하지 않는 candidate_id를 넣으면 `warm_method_selected_unknown_candidate`; 결과 method 파일 미생성. 경고는 남음 | 보조 제한을 완화해도 실행할 후보 ID의 정확성 검증은 유지 |
| `test_anchor_overflow_does_not_discard_invalid_trailing_item` | 9번째 앵커가 None이면 `groups.0.context_anchors.8`의 `string_type` 거절 | 절단 때문에 뒤쪽의 잘못된 타입이 무검증 수용되지 않음 |

다른 기존 단언을 삭제하거나 완화하지 않았다. 두 xfail 및 control_cleanup의 거절 단언도 유지했다.

## 2-1 보완: 원문 저장 비용·비공개 경계·대안

### 저장량과 동기 쓰기 측정

`models/base.py:207–217`은 모든 **반환된** ProviderReply의 `text`를 저장한다.
SDK 실패로 ProviderReply가 없으면 저장할 원문도 없다. 이미지와 ProviderReply 객체 전체를
이 파일에 저장하는 것은 아니다. JSON에는 `call_id`, `context`, `schema`, `text`가 들어간다.

071·072의 `model-requests/*/response.txt` 70개를 읽고, 실제 metadata의 context/schema와
UUID 형식 call_id를 넣어 현재 `diagnostic_artifact`를 로컬에서 3회씩 실행했다.
원문을 출력하거나 새 모델 호출을 하지 않았다. 같은 디스크의 `cases/` 아래 임시 디렉터리,
호출마다 새 파일명을 사용했으며, 실제 파일 바이트 수도 확인했다. 이 계측은 과거 실행 당시
지연을 복원한 것이 아니라 **현재 저장 구현을 기존 응답 크기로 측정한 마이크로벤치**다.
분위수 p95는 정렬한 표본의 ceil(0.95*N)번째 값이다.

| 항목 | 표본 수 | 최소 | 중앙값 | p95 | 최대 |
|---|---:|---:|---:|---:|---:|
| 원문 UTF-8 크기 | 70 | 1,044B | 1,721B | 3,005B | 3,428B |
| 현재 형식 진단 파일 크기 | 70 | 1,616B | 2,383.5B | 3,887B | 4,402B |
| 동기 저장 전체 지연 | 210 | 1.269ms | 3.171ms | 3.888ms | 5.225ms |

파일 크기 합계는 179,074B/70호출(평균 2,558.2B), 3회 저장 지연 합계는 556.430ms다.
각 회 중앙값은 3.196/3.155/3.177ms였다. `atomic_write_json:171–186`의 JSON 인코딩,
디렉터리 확인, 임시 파일 생성, write/flush/fsync, replace 및 임시 파일 정리까지 포함한다.
일반 model_call 이벤트 쓰기·SDK·스키마 검증은 제외했다. 파일시스템 할당 단위/메타데이터
용량은 측정하지 않았다. 공유 디스크·백신·잠금 경합 시 더 느릴 수 있으며 기존 replace
재시도는 최대 0.775초 지연을 추가할 수 있다. 이 70개는 주로 화면 응답이므로 큰 문서
추출이나 편찬 응답의 상한으로 해석하면 안 된다. 집계는 로컬
`cases/stage23-audit-20261020/storage-measurement.json`에 남겼다.

### Git 제외와 기존 정책

- core `.gitignore:13`의 `cases/` 아래 진단 파일은 `git check-ignore -v`로 제외를 확인했다.
- `AGENTS.md:120`, `models/README.md:45–46`, `docs/contracts.md`의 거절 진단 절은
  업무 값·원문 응답·화면·진단 산출물을 비공개로 취급하고 커밋하지 않도록 한다.
- **모든 진단 디렉터리가 자동 제외되는 것은 아니다.** `diagnostic_artifact`는 임의의
  `log.case_dir`를 허용하고 `.gitignore`에는 전역 `diagnostics/` 규칙이 없다.
  실제 편찬 경로인 bench `evidence/seed-audit-20261017-072/seed-000/compilation/diagnostics/`
  예시에는 `git check-ignore`가 매칭되지 않았다. bench는 `cases/`, `evidence/runs/`,
  `references/`, `local/` 등을 제외하지만 모든 날짜별 evidence 경로를 제외하지 않는다.
- 따라서 **비공개 정책의 적용 대상은 맞지만, Git 자동 제외의 보장은 경로 의존적**이다.
  이는 조사 결과로만 남겼으며 이번에 `.gitignore`나 저장 경로를 변경하지 않았다.
  이 변경에는 원본 응답·업무 값·실제 로그·화면을 추가하지 않았다.

### 거절 시에만 기록하는 대안

기술적으로 가능하다. 파싱 오류는 `complete_json` 안에 `reply.text`가 살아 있으므로
catch에서만 저장할 수 있다. 하지만 성공 파싱 이후의 매칭/편찬/실행 거절은 메서드가
반환된 뒤 발생한다. 현재 `ModelResult`에는 원문과 call_id가 없고, 거절 헬퍼는 이미
정규화된 응답만 받는다. 따라서 그 변경만으로는 원문을 잃는다.

전체 경계를 유지하려면 call_id로 연결된 실행 단위 메모리 보관소 또는 원문 인계를 두고,
후속 판단의 수용/거절 시점까지 수명을 관리해야 한다. 여러 번의 교육/매칭 호출, 중첩 거절,
캐시 응답(새 공급자 원문 없음)을 구분해야 하며, 정상 수용 후 해제와 실행 중단 시 정리도
필요하다. 메모리만 사용하면 프로세스 강제 종료 때 미기록 응답이 사라지는 트레이드오프가
있다. **불가능한 구조는 아니지만, 지금의 계약에서 저장 if문 하나로 바꿀 수는 없다.**
이번에는 `models/base.py`, ModelResult 및 진단 저장 정책을 변경하지 않았다.

### core 거절 계측 커밋 규모

`5a9758cdb549080d1b8ddf5eedf4ec05c36a5073`:
**15파일, +1,812 / -402줄** (`git show --numstat/--stat` 확인).
제품 Python 8파일은 +688/-400줄, 테스트 3파일은 +169/-0줄,
문서/CSV 4파일은 +955/-2줄이다. 전체 추가 줄 수에는 분류 CSV 731줄과 보고서 202줄,
기존 함수를 진단 scope로 감싼 들여쓰기 diff가 포함되어 있다. +1,812줄을 순수 신규
계측 실행 로직의 길이로 해석하면 안 된다.

## 2-4: 해당 없음으로 종료

072 당시 core는 `_fresh_visual_target`/`_rebind_visual_point`에서 동일 해시의 빠른 경로
외에도 pHash 기반 좌표 재결합을 허용했지만, relay `_visual_fresh`는 다시 캡처한 내용의
정확한 해시 일치를 요구하여 `screen_changed`로 거절했다. bench `0554bf4`가 그 후자의
내용 동일성 거절을 제거했다. 현재 core `brain/graph/action_preparation.py:191–266`은
새 관찰의 obs_id/capture_id와 그 관찰에 맞춘 좌표를 전달하며, relay
`service.py::_visual_point:653`, `_visual_fresh:717`은 해당 캡처 ID·좌표 범위·창·DPI·치수·
유효 시간을 확인한다. `_comparison_hash`는 관찰 메타데이터 생산용이며 현재 픽셀/pHash
내용 판정 게이트가 아니다. 단일 및 배치 진입점의 `_visual_fresh` 호출도 대조하여,
**같은 화면 내용에 대해 core는 허용하고 relay는 정확한 픽셀 일치 때문에 거절하는 추가
불일치는 발견하지 않았다.** 캐시 후보의 pHash 6과 행동 재결합의 16은 서로 다른 단계의
허용 범위이고 relay와 같은 입력을 이중 판정하는 기준 충돌은 아니다. 창/치수/DPI/시간
검사는 여전히 존재하므로 두 계층의 모든 조건이 같다는 뜻도 아니다. 확인 범위를 넘는
“모든 런타임 불일치가 없다”는 보장은 하지 않는다. relay에 pHash를 도입하지 않았다.

## 3-1 조사: 증거 ID와 투영 경로

### control_cleanup과 편찬 웜 xfail

두 사례는 최종적으로 `ground_meanings → bind_scene → SceneObservation`이라는 공통
장면 투영 계약을 거치지만, 그 앞에서 빠지는 구성요소와 최종 증상은 다르다.

| 항목 | 투영 이전 입력 | 실제 경로/결과 |
|---|---|---|
| control_cleanup 테스트 | marker 31은 있으나 controls에는 행동 34만 존재. evidence는 31을 참조 | sanitize는 evidence 31을 유지. `ground_meanings`는 controls만 투영하여 장면에 34만 남음. `compile_match → bind_proposal`에서 증거 ref 31을 찾지 못해 `visual_proposal_unknown_marker`; 행동까지 거절 |
| compiled-warm 합성 xfail | `bind_with_context`가 행동 3의 marker를 추가한 뒤 `bind_visible_value_evidence`가 별도 할당으로 증거 4의 control/evidence를 생성. 4의 marker는 screen에 추가하지 않음 | `Brain._groundable_interpretation`이 현재 marker에 없는 control 4를 observe_only 투영에서 제외. 장면에는 행동 3만 남고 `_remember_model_task_evidence`가 4를 찾지 못하여 건너뜀. 입력 값은 보이지만 메모가 비어 `MissingWarmEvidence` |

즉 “geometry와 의미 객체가 함께 있어야 장면 요소가 된다”는 경계는 공통이다. 하지만
첫 사례는 **의미 객체 누락**, 두 번째는 **새 증거 ID의 geometry 등록 누락**이다.
첫 테스트는 sanitize/ground/compile 직접 호출이고, 두 번째는 실제 observe 경로의 필터를
포함한다. 같은 원인이나 같은 수정이라고 합치지 않는다. 합성 후보가 실제 전달 행동을
선별하는 `read_warm_candidates`를 우회한다는 한계도 그대로다. 이번에 해당 코드를 수정하지 않았다.

### 071·072의 7건: 거절/재계획으로 이어졌는가

이전 보고의 7건은 저장된 `observations/*.json`에서 제안의 증거 ID가 안 보인 비교 결과다.
이번에는 제안 뒤의 메모 기록, 컴파일, act 요청/응답까지 연결했다. **7건 모두 hbl_no가
즉시 기록되고, 계획이 컴파일되며, 행동이 ok=true로 전달됐다. 이 ID 차이로 인한 거절이나
재계획은 0건이다.** 해당 구간 이후의 정상 다음 화면 판단을 거절 재계획으로 세지 않았다.

| 실행 | 제안 seq / 증거 ID → 행동 ID | 메모 기록 seq | 컴파일 seq | act 요청 → ok 반환 seq |
|---|---|---:|---:|---|
| 071 seed003 cold | 113 / 36 → 39 | 114 | 115 | 119 → 120 |
| 071 seed003 cold | 440 / 42 → 41 | 441 | 442 | 446 → 447 |
| 072 seed000 cold | 115 / 39 → 42 | 116 | 117 | 121 → 122 |
| 072 seed000 warm | 144 / 36 → 39 | 145 | 146 | 150 → 151 |
| 072 seed001 cold | 114 / 39 → 42 | 115 | 116 | 120 → 121 |
| 072 seed002 cold | 117 / 54 → 55 | 118 | 119 | 123 → 124 |
| 072 seed002 cold | 139 / 61 → 65 | 140 | 141 | 145 → 146 |

각 `model_task_evidence_recorded.fields`가 제안의 evidence.field인 hbl_no를 포함하는 것도
확인했다. 모두 controls에는 증거 ID가 들어 있으며, method_bound 응답은 아니다.
이 경로에서 `Brain._selection_call:632–635`는 현재 모델 mapping으로 `bind_mapping`을
호출하여 **메모리의 장면**을 다시 만든다. 일반 bind_mapping 직후에는 관찰 JSON을 다시
쓰지 않으며, 별도 missing-target merge 분기에서만 관찰 파일을 쓴다. 따라서 같은 obs_id라도
앞서 저장된 장면과 이후 판단 장면의 ID 집합이 같다고 전제한 조인이 잘못될 수 있다.
7건을 증거 유실의 운영 사례로 해석하면 안 된다는 점을 이전 보고에도 추가했다.

seed002의 seq117 뒤에는 `identical_form_reused`가 있었으나, 이미 seq118에서 메모가
기록되고 seq124에 전달이 성공한 뒤다. 이를 증거 ID 누락 때문에 재계획한 것으로 볼 근거는 없다.

### 입력 수집 뒤 웜 재결합에서의 새 ID: 확인 수준

`_coordinate_bindings`(`brain/cache/methods.py:529–556`)는 매번 현재 marker의 최대 ID+1을
사용한다. `MethodRuntime.apply:310`의 행동 결합이 screen에 marker를 추가한 다음,
같은 apply의 `:354`에서 `bind_visible_value_evidence`가 이 함수를 다시 호출한다.
그래서 **새 ID를 만들 수 있는 코드 경로와 합성 재현은 확인**했다.

실제 071·072 로그의 `method_bound`는 072 seed000/001 warm의 seq5, 로그인 두 건뿐이다.
두 건 모두 원래 markers.json은 0–13이고 제안 seq9의 행동은 14/15/16이다. 따라서
실제 실행에서 재결합이 새 행동 ID를 만든 것은 확인된다. 그러나 두 응답의 evidence는
빈 리스트이며, 업무 입력 수집 후 증거를 재결합한 사례가 아니다. 뒤의 모델 응답에
method_id가 존재한다는 사실만으로 자동 `MethodRuntime.apply`의 성공으로 세지도 않았다.
앞서 본 편찬 입력 4개(후보 13/13/13/12개)는 결합 전 자료여서 결합 이후 ID를 증명하지 못한다.

따라서 **“입력 수집 뒤 같은 필드의 행동/증거에 새 ID가 갈라져 실제 메모가 유실됐다”는
운영 발생은 미확인**으로 유지한다. 추가 파일럿이나 제품 계측을 임의로 실행하지 않았다.

## 검증과 종료

| 명령 | 최종 결과 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest` | 666 passed, 2 xfailed, 35.08초 |
| `.\.venv\Scripts\python.exe -m ruff check .` | 통과 |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 통과 |
| `git diff --check` | 통과 |

초기 집중 검사도 16 passed였다. 최초 lint에서 테스트 import 구분의 빈 줄 1건을 고쳤고,
최종 검사에서 모두 통과했다. 기존 baseline 663 passed/2 xfailed에서 경계 테스트 3건만
늘었다. 새 실패나 xfail은 없다. 대상 ID·좌표·입력값·행동 종류, 확신도·stop·control_cleanup의
기존 거절 조건과 단언은 유지했다. bench tracked/staged diff도 없다.

변경 파일은 제품 `brain/demo_learning/warm_methods.py`, 테스트 2개,
계약/현행 기준 문서 2개, 이 보고와 이전 보고의 후속 정정 링크다.
승인된 2-3까지만 처리하며 3단계 구현으로 넘어가지 않는다.
