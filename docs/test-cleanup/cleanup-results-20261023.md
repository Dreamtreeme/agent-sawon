# 코드·테스트 정리 1차 실행 결과

[판정 기준](cleanup-criteria-20261023.md)에 따라 회귀 민감도를 먼저 확인하고,
도달 불가 코드 → 변환 왕복 → 중복 테스트 순서로 정리했다.
전체 저장소의 불필요한 코드가 모두 없어졌다는 보고는 아니다.
상태·프롬프트의 광범위한 통합은 아래 검증 공백 때문에 보류했다.
파일럿, 실제 ERP, 유료 호출은 실행하지 않았다.

## 기준선과 측정 환경

- 작업 브랜치: `cleanup/tests-20261023`.
- core 기준 HEAD: `e2c99207dbdb23ea329162592cbb562df372bd42`.
- bench 기준 HEAD: `0bc6adb21c4f9453a2003862e4e609f6f2ee3183`.
- core의 기존 미커밋 diff SHA256: `c22e3f00106ff0c800bd7263ded7f52ea83eeafe06d2c38ca25f1077fedad21f`.
- bench의 기존 미커밋 diff SHA256: `a15f035be7353db683217c86df32421a769a4af1f626980c1df24684f9a93ad9`.
- Python 3.13.14, pytest 9.1.1, pytest-cov 7.1.0. 두 저장소의 baseline은
  core `.venv` Python으로 측정했다. bench `.venv`에는 pytest-cov가 없어 첫 명령이
  옵션 해석 단계에서 실패했으며, 설치나 설정 변경 없이 core 환경으로 재실행했다.
- 기존 미커밋 수정까지 포함한 소스를 격리 복사했다. 결함 주입은 복사본에서만 수행했다.
  기존 engine, 클릭 회귀, current-runtime 및 bench 수정 파일은 작업 시작 해시와 동일하다.

| 고정 입력 | SHA256 |
|---|---|
| core config/brain.visual.toml | c1a350d5914a1655cea954c9aa71f4802b77766339cc930001c1cfcbab45d998 |
| core config/models.toml | 1b35630ecbba79715e15ec4884486c389e346bd727e62f002a71e4b79712f167 |
| core config/pipeline.toml | aff5df766d262b859d19486c37829aad399023c3a1fc67f5c5b1ded1d6295c67 |
| bench education/development/company-000.json | 68bbb09474a25496fd225e61266deef71e3698c97da3b6cf0475bbb141d0388b |
| bench education/development/company-003.json | 125c304b1e7746ae90ddef3c639b92c8957518d89d6d3d4d7238d0ef1e5154b1 |

원시 측정, 시작 diff/파일 해시, 격리 소스, 변이 실행 출력, 재생 전후 JSON은 로컬
`.tools/cleanup-audit-20261023/`에 보존한다. 비공개 실행 자료는 커밋하지 않는다.

## 0단계: 결함을 실제로 잡는지 확인

각 항목에서 원본 통과 → 변이 실패 → 원복 통과를 확인했다. 실패는 문법·import 오류가
아니라 아래 계약 위반이었다. 원본 작업 파일에는 변이를 적용하지 않았다.

| 결함 주입 | 실패한 회귀 테스트 | 확인한 실패 |
|---|---|---|
| 최신 계획 보존 분기 무력화 | test_synthetic_no_progress_preserves_current_alternative_plan | 기대 review 대신 observation 반환 |
| 완료창 intent 정규화 무력화 | test_synthetic_save_acknowledgement_normalizes_only_completed_click | ready_for_query/click만 SaveReviewResponse 계약 거절, 나머지 3조건 통과 |
| NOACTIVATE 필터 제거 | test_synthetic_window_enumeration_excludes_noactivate_tooltip | 정상 창 [10] 대신 보조 창 포함 [9, 10] |
| 활성 허용 창 우선 선택 제거 | test_synthetic_focus_restore_keeps_active_window_before_other_same_pid_window | blocked_reason이 None 대신 window_changed |

세 결함 유형에 대한 네 변이는 정리 전후 모두 탐지했다. 창 시험은 합성 재현이다.
086의 작은 캡처가 NOACTIVATE 창 때문이었다는 역사적 인과관계는 여전히 미확인이다.

## 실제 기록의 부분 재생

저장된 `screen_plan_proposed.response`와 해당 `SceneObservation`으로
`compile_screen_action_plan`/`compile_match`를 실행했다. schema는 저장 결과·입력 종류 등
응답 경계를 기준으로 선택했다. 그래프의 모든 phase 전이, 새 화면 탐색, 모델·relay와
상태 전체를 재생한 것이 아니다. 관찰과 계획 사이 이후의 보충 동작도 이 재생 범위 밖이다.

| 기록 | 계획 수 | 컴파일 | 동일 거절 | 대응 장면 없음 |
|---|---:|---:|---:|---:|
| 089 | 18 | 18 | 0 | 0 |
| 088-r3 | 14 | 14 | 0 | 0 |
| 086-r3 | 23 | 20 | 1 | 2 |
| 합계 | 55 | 52 | 1 | 2 |

- 088-r3 seq 355/375는 정리 전 기준 코드와 정리 후 코드 모두 정상 컴파일됐다.
  정규화는 이번 정리 이전 수정이며 이번 성과로 중복 계산하지 않는다.
- 086-r3 seq 485는 이 계약 재생에서 `An action requires an executable intent and one action`
  거절을 전후 동일하게 유지했다. 이번에 새로 생긴 결함도, 전체 그래프 실패 재현도 아니다.
- 086-r3 seq 124/520은 대응 장면을 찾지 못했다. 성공으로 계산하지 않았다.
- 출력·거절·미재생 상태를 담은 전체 JSON은 전후 바이트 단위 동일했다. 입력 응답 불변도 확인했다.
- 089의 ERP 성공·정상 종료와 088-r3의 ERP 성공·작업자 실패는 기존 실행의 사실이다.
  이번에 실제 ERP 성공을 다시 검증한 것은 아니다. 089는 완료창 정규화 분기를 거치지 않았다.
- 086은 company-000, 088/089는 company-003 조건이다. 교육 변경의 효과를 코드 개선으로 계산하지 않는다.

## 후보별 판단과 조치

| 후보·위치 | 유형·실패 영향 | 조사 근거 및 조치 | 정리 전 → 후 |
|---|---|---|---|
| D01 screen_decision.ActionCompletion | 과거 응답 계약. 현행 업무 영향 없음; 죽은 계약을 회귀 기준으로 유지하는 비용 | 제품 호출 0. 등록·동적 import/클래스 탐색·설정·bench 참조 없음. 실제 missing_actions_recovery는 ScreenMatch 사용. 클래스·전용 검증·fixture 사례·fixture 파일 함께 삭제 | 제품 미사용 타입 1→0, 전용 테스트 사례 2→0 |
| C01 compile_match의 내부 Payload | 의미 없는 전달·변환. 모든 컴파일 호출의 부가 처리 | extra=allow 외에 필드/validator 없는 임시 모델. 이미 조립된 dict를 bind_proposal에 직접 전달. 최종 스키마 검증과 현재 참조 검증 유지 | 호출당 dict→Payload→dict 2→0, 임시 클래스 정의 1→0 |
| T01 test_vocabulary.py | 같은 스키마의 반복 검증 | MatchAction 해시 단언 중복과 ScreenMatch fixture 중복 제거. 두 fixture 검증 함수를 공통 parameterized test로 합침 | MatchAction 검사 위치 2→1, ScreenMatch 사례 2→1, fixture 검증 본문 2→1 |
| S01 state_property/progress_property | 중복 상태 의심 | 실제 저장은 WorkerState 한 곳이고 접근자가 그 주소를 읽고 쓴다. 복사나 독립 상태 아님. 유지 | 독립 소유 위치 1→1 |
| S02 verified/post_save_verified/evidence_ledger | 같은 값의 중복 메모 의심; 잘못 합치면 저장 이전 값을 최종 증거로 인정 | 현재 입력 확인, 저장 후 확인, 증거 이력은 시점·책임이 다름. 통합하지 않음 | 변경 없음 |
| M01 ScreenMatch.anthropic_nulls | 반복 정의 의심; 삭제 시 provider 계약 변경 | SemanticDetection이 상속 순서에서 먼저 와 해당 부모의 다른 null 규칙을 선택하게 됨. ScreenPlan 규칙을 명시적으로 선택하는 재정의 유지 | 변경 없음 |
| M02 grounded plan과 replan의 해석 병합 | 반복 merge/model_copy 후보; 문맥 유실 위험 | 두 경로의 전체 요청·mapping 보존 동등성 검증이 이번 부분 재생에는 없음. 광범위 상태 통합과 함께 보류 | 미측정·변경 없음 |
| V01 ground_screen_response의 sanitize 2회 | 중복 검증 의심; 중간 대상 보완의 영향 | 사이에 retain_action_targets/retain_verification이 응답을 변경함. 같은 입력의 동일 검사라고 입증되지 않아 보류 | 변경 없음 |
| L01 정적 분석의 validator/HTTP callback/worker 후보 | 미사용 오탐; 삭제하면 실제 경로 손상 | decorator·프레임워크·동적 워커 호출을 고려해 유지. 커버리지 0만으로 삭제하지 않음 | 변경 없음 |
| L02 anthropic_provider_schema | 테스트 전용 참조 후보 | 이번 이전의 명시적 제외 항목이며 기존 소비 테스트가 있음. 이번 삭제 범위에서 제외 | 변경 없음 |

정적 분석은 core의 brain/models/pipeline/sawon_runtime/scripts/tests를 검색했고,
호출·문자열 검색은 config와 bench Python 소비자까지 확인했다. 제품 함수 본문의 동일 AST
검색에서 나온 동일 블록은 Protocol의 `...` 선언뿐이어서 중복 구현으로 삭제하지 않았다.
이는 의미가 유사한 모든 구현을 전수 증명했다는 뜻이 아니다.

## 변경한 테스트와 탐지 능력

- 폐기 타입의 `test_missing_action_completion_cannot_repeat_empty_act`와
  ActionCompletion 스키마 사례를 삭제했다. 빈 행동 복구의 실제 경로는
  `test_empty_action_is_completed_once_or_explicitly_stopped`의 act/stop 두 사례와
  교육·관찰값 보존 시험이 계속 검증한다. 삭제 타입의 독자 제약을 현행 계약에 옮기지 않았다.
- KeyChoice 검사에서 MatchAction 중복 단언만 제거했다. KeyChoice 자체 검사는 유지한다.
- ScreenMatch는 role/intent 두 fixture 사례 중 하나만 남겼다. 서로 다른 ScreenPlan과
  ScreenMatch는 현재 schema 출력이 같아도 별도 모델이므로 각각 남겼다.
- 동일 스키마 비교 구현은 `test_vocabulary_schemas_match_recorded_json_and_order`로 합쳤다.
  pytest 실패 ID에 모델 이름이 포함되며 원문 fixture와 해시를 모두 비교한다.
- MatchAction의 target 최소값 0→1, ScreenField bbox 최소길이 4→3 변이를 각각 주입했다.
  삭제 전 중복 양쪽과 정리 후 잔존 시험이 모두 해당 오류를 탐지했다(2/2).
- `test_current_policy_rejects_wrong_phase_action_and_unknown_marker`는 dict 경계로 갱신하고
  결합된 ref 및 입력 불변을 단언한다. 참조 허용 검사 제거 변이가 예상 raises 단언에서
  실패하는 것도 확인했다. 반환 스키마 검증이나 거절 분기를 제거한 작업은 아니다.

세 결함의 네 변이와 중복 검증용 두 변이, 총 6/6의 탐지를 전후 유지했다.
추가로 정리 후 현재 참조 검사 변이 1/1을 탐지했다. 프로젝트 전체 변이 점수는 측정하지 않았다.

## 정량 결과

| 시점 | core 테스트 | 시간(coverage 포함) | Statement | Branch |
|---|---:|---:|---:|---:|
| 시작 기준선 | 693 passed | 75.89초 | 87.5439% | 76.5064% |
| 미사용 타입 제거 후 | 691 passed | 87.54초 | 87.5426% | 76.5225% |
| Payload 왕복 제거 후 | 691 passed | 76.66초 | 87.5400% | 76.5225% |
| 중복 테스트 정리 후 | 690 passed | 70.70초 | 87.5400% | 76.5225% |

Statement 변화 -0.0038%p, Branch 변화 +0.0161%p로 보류 기준 0.5%p 미만이다.
유지된 동일 소스 라인의 이전 실행 커버리지 손실 0건, 양끝이 유지된 양수 라인 번호인
branch의 실행 커버리지 손실 0건을 확인했다. 삭제·변경 라인과 함수 출입 음수 arc는 이
보조 비교에서 제외했으며 전체 branch 수치는 coverage 전체 집계다.

55건 부분 재생은 내부적으로 bind_proposal을 63회 호출한다. 정리 전 Payload 검증 63회와
직렬화 63회가 있었고, 정리 후 둘 다 0회다. 참조 결합 63회와 최종 계약 결과는 유지했다.
모델 호출 감소나 ERP 실행 시간 개선을 이번 측정으로 주장하지 않는다. 테스트 시간도
반복 중앙값이 아닌 각 단계 1회 값이며 실행 간 편차가 있으므로 속도 개선의 증거가 아니다.

bench는 제품·테스트 변경 없이 baseline **621 passed, 6 deselected, 25.46초**를 확인했다.
bench 통계의 coverage 범위는 relay/harness이며 core의 네 패키지 통계와 합산하지 않는다.

## 실행 명령과 결과

core 루트에서 실행:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=brain --cov=models --cov=pipeline --cov=sawon_runtime --cov-branch --cov-report=json:tmp/cleanup-final-core-20261023.json --cov-report=term:skip-covered
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m vulture brain models pipeline sawon_runtime scripts tests --min-confidence 80
git diff --check
```

전체 테스트 통과, Ruff check 통과, 224개 파일 format 통과, vulture 80% 이상 경고 0건,
diff 공백 검사 통과. 중간 단계 전체 테스트도 각각 통과했다. 60% 탐지에서는 validator 등
오탐 후보를 조사했으며 도구 결과만으로 삭제하지 않았다. 생성된 coverage 파일은 측정
보관 위치로 이동했고, 원래 깨끗했던 추적 `.coverage` 파일은 원래 내용으로 되돌렸다.

## 남은 작업과 반증 조건

- 전체 상태·해석 병합을 진행하려면 089의 저장→재조회→종료 및 086의 대안 계획 인계를
  실제 그래프 함수로 연결한 재생을 더 확보해야 한다. 이번 계약 경계 비교만으로 대체하지 않는다.
- 086의 누락된 두 장면과 당시 잘못 선택한 창의 클래스/스타일은 미확인이다.
- 현행 설정 외의 지원 설정 조합 전체와 전체 테스트의 중복성은 전수 검증하지 않았다.
  이번 확정 후보 밖을 일괄 삭제하지 않는다.
- 같은 입력에서 결합된 ref·입력값·행동·거절 결과가 달라지거나, 입력 dict가 변하거나,
  남긴 테스트가 지정 변이를 놓치면 이번 정리의 동등성 주장이 반증된다.
- 제품 코드의 설정·프롬프트·화면 행동 정책은 변경하지 않았다. 신규 클래스나 정책 계층은 없다.
