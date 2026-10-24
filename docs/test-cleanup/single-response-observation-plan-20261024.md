# 한 응답의 관찰 반영과 다음 행동 전달

2026-10-24. 제품 커밋 `62f5b70`. 앞선 `saved-detail-context-20261024.md`의
강제 재판단 보정은 이번 변경으로 대체했다. 이 문서는 측정 기록이며 현행 계약은
`docs/contracts.md`, `docs/current-runtime.md`, `docs/code-flow.md`를 따른다.

## 변경과 진단 정정

같은 관찰 ID나 메모 갱신 자체가 기존 계획을 무효로 만드는 것은 아니다. 모델이 현재 화면의
결과를 읽고 그 결과에 근거해 다음 행동을 함께 만들었다면, 코드가 같은 확인 내용을 메모에
반영한 뒤 그 계획을 실행할 수 있다. 앞선 보정은 이 경우까지 계획을 폐기해 재호출했다.

- `engine._observe`: 검토 범위 확정 → 응답의 현재 증거 기록 → 같은 계획의 컴파일·전달.
  첫 상세 관찰에서 계획·후보·오류를 지우는 분기와 `screen_plan_context_changed` 기록 제거.
- `_begin_saved_detail_review`: 재호출 판단용 반환 bool 제거. 재진입의 pending 해소와 누적
  검토 보존, 완료된 교육 단계로 초점이 회귀하지 않는 수정은 유지.
- `screen_mapping.CONTEXT_MAPPING_RULES`: notebook과 교육 초점은 현재 스크린샷 해석 전의
  상태이며 pending_actions는 이미 전달한 행동임을 명시. 기존 screen_kind, fields.value,
  assignments/evidence로 현재 결과를 표현하고 그 결과를 바탕으로 같은 응답의 steps를 선택.
  taught_step_id는 다음 행동의 교육 단계. 새 응답 필드·상태 클래스·실행 거절은 추가하지 않음.
- 잘못된 행동의 검증 오류를 메모 반영 과정에서 없애지 않는다. 증거 보존과 행동 승인 구분.

이 변경은 모델이 지시를 반드시 따른다고 보장하지 않는다. 잘못된 닫기 응답을 코드로 차단하는
방법도 아니다. 이전에는 pending 문맥에 intent/source만 있어 어떤 출발 화면에서 무슨 행동을
했는지 빈약했다. 선행 수정의 screen_kind/action/subgoal 전달을 함께 유지한다.

## 무료 검증

기존 합성 093 재판단 테스트를 한 응답 테스트로 교체했다. fake의 올바른 다음 선택은 합성
입력이며 모델 정확도를 증명하지 않는다. 변경 전에는 관찰 후 계획이 지워져 실패했고,
변경 후에는 다음을 검증한다.

- 모델 호출 1회, 탐지·OCR 각 1회.
- 상세 검토 시작, 이동 대기 해소, 재조회 교육 완료, 현재 hbl_no 증거 반영.
- 위 메모가 이미 반영된 상태에서 fake relay로 같은 응답의 추가 탭 좌표만 전달.
- 잘못된 계획을 주입한 기존 테스트는 증거는 남지만 선택 시 기존 검증 오류가 발생함을 확인.

| 명령 | 결과 |
|---|---|
| core `.venv/Scripts/python.exe -m pytest` | 710 passed, 39.95초 |
| 관련 3파일 pytest | 159 passed, 10.79초 |
| core `.venv/Scripts/python.exe -m ruff check .` | 통과 |
| core `.venv/Scripts/python.exe -m ruff format --check .` | 242 files already formatted |
| bench relay/visual_relay/relay_windows pytest | 96 passed, 1.45초; 기존 Starlette 경고 1건 |
| `git diff --check` | 통과 |

최초 lint의 테스트 줄 길이 위반 1건은 formatter로 수정한 뒤 위 검사를 통과했다.

## 실제 ERP 095: 목표 구간 미도달

- core `62f5b705a8d45f7fba9fae7a5f8634ef759491ab`, bench
  `5ae931f4edce3be4afa55c3023a6ee97a1b8cdc0`.
- 시드 3, 반복 1, cold/fresh cache, ID none, visual_only.
- company-003 교육 SHA256:
  `85d66f168ba3932587f28913a1f6962253628c3c878e9aba7b05578b45ade7dd`.
- Haiku 5.5, low, 8192, 건별·총 $0.30, timeout 600초. 실행 전 깨끗한 두 작업 트리와
  교육·설정 해시를 검사하고 bench의 비공개 `evidence/local/seed3-recheck-095/protocol.json`에 고정.
- 실행 시간 32.14초, 작업자 27.61초. ERP 0/10, 작업자 stopped/exit 1.
- 원격 screen_decision 2회 + 로컬 탐지 계측 1회, 비용 $0.0022959, 미확정 비용 없음.

로그 seq 22, 47에서 로그인 사용자명 입력을 relay가 point_outside_allowed_window로 거절.
seq 50에서 repeated_subgoal_failure:task:credential.username으로 중단했다. 첫 캡처와 다음
캡처 모두 계정·비밀번호가 비어 있는 로그인 화면이었다. 모델 점 [260,149], [290,147]은
캡처상 계정 입력칸 내부이다. 실제 데스크톱의 허용 창 판정이 거절된 상세 원인은 이번 범위에서
확정하지 않았다. 저장 상세 처리에는 도달하지 않았으므로 이번 제품 변경의 E2E 효과는 미확인.
로그인 쪽 제품 코드를 변경하거나 파일럿을 재시도하지 않았다. 원본은 bench의
`evidence/runs/seed3-recheck-095-r1-cold`와 core의 동명 cases 아래 보존한다.

## 기록 화면으로 실제 모델 1회 재생

로그인과 독립적으로 목표 장면을 검사했다. 093의 원격 호출 seq 546에 보존된 이미지와 JSON
문맥을 사용하고, 현재 제품 프롬프트와 현재 pending_actions의 추가 3개 필드만 반영했다.
3개 필드는 추정한 정답이 아니라 실제 전달된 seq 534 Decision의 screen_kind=query,
action=double_click, subgoal 원문에서 복원했다. notebook/execution 양쪽에 동일하게 반영했다.
이것은 과거 상태 일부를 현재 계약으로 복원한 부분 재생이지 전체 그래프 재실행이 아니다.

- 이미지 SHA256 `88ac4793622ec5ce20ed4298f2b3e323d68b31510e39ebd4e33934b9b51339fa`.
- 보존된 스키마와 현행 ScreenMatch 모델 스키마 동일 확인.
- Haiku 5.5/low/8192, 공용 create_model_client/complete_json 계측 사용, SDK 재시도 0.
- 1회만 호출, 사전 예약 $0.0135262 ≤ 상한 $0.03. 실제 $0.0024044, 11.16초.
- 호출 결과: editor, taught_step_id=verify-requeried-record.
- 보이는 HBL·MBL·송하인·수하인 4개 값을 assignments로 대응하고, 추가 정보 탭을
  select_tab/click [188,117]로 선택했다. 실제 이미지에서 해당 점은 추가 정보 탭 내부이다.
- 과거 결과의 닫기 [205,899]와 달리 닫기·재조회·중복 입력·완료 선언을 선택하지 않았다.

원문과 계측은 비공개 `cases/single-response-detail-095`에 보존한다. 화면 행동은 실행하지
않았으며 API의 선택과 무료 테스트의 실제 함수 전달 검증을 구분한다. 095 및 부분 재생의
합산 비용은 $0.0047003이다.

## 해석과 반증 조건

확인: 추가 호출 없이 관찰 내용을 기록하고 같은 응답의 다음 행동을 전달하는 코드 경로가
동작한다. 이번 실제 모델 부분 재생도 기대한 다음 행동을 1회에 선택했다.

미확인: 전체 ERP 성공, 후속 모든 화면의 루프 해소, 일반적인 속도·호출 감소. 샘플 1건과
프롬프트 및 pending 문맥을 함께 바꾼 비교이므로 어느 변경의 독립 효과인지 단정하지 않는다.

반증: 같은 응답의 현재 값이 전달 전에 메모에 반영되지 않거나 메모 반영만으로 추가 호출이
생기면 구현이 틀린 것이다. 올바른 pending 문맥에서도 닫기·재조회를 반복한다면 이번 문맥
개선이 모델 판단에 충분하다는 가설은 틀린 것이며, 실패 응답을 그대로 보존해 조사해야 한다.
