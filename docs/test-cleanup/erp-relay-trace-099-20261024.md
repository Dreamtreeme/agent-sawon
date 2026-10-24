# ERP 099: 전경과 클릭 대상 불일치의 실제 기록

## 조건과 결과

사용자의 ERP 재검증 요청으로 시드 3 콜드 1회를 실행했다. 실행 도중 제품 코드·프롬프트·
설정을 변경하지 않았다. 이전 098 산출물은 보존하고 099의 새 case/cache/method 경로를 사용했다.

- core `64445278a3ee0085cbceff90b32616a09884d664`, bench `26697dbaa53fc50ee787d1ab68c178a72ff3ac27`.
- Haiku 5.5, low/8192, visual_only, ID none. 시드 3 전용 company-003 교육.
- 교육 SHA256 `85d66f168ba3932587f28913a1f6962253628c3c878e9aba7b05578b45ade7dd`.
- 실행 전 두 작업 트리 clean 및 설정 검증. 단일 실행·전체 비용 한도 각각 $0.30.
- ERP **0/10**, 작업자 stopped/exit 1. 전체 **20.13초**, 작업자 **15.64초**.
- 원격 모델 **2회**, 로컬 탐지 **1회**, 비용 **$0.0023254**, 미확정 비용 0.
- 로그인 전에 중단됐으므로 조회 이후 문맥 수정의 효과는 이번 실행으로 검증하지 못했다.

## 화면·모델·입력 추적

입력 직전 ERP 원본 캡처를 직접 확인했다. 622×476 이미지의 계정 칸은 대략
x175–586, y133–165이며 두 모델 선택점 모두 내부다. 계정·비밀번호는 빈 상태였다.
모델은 두 응답 모두 로그인 미완료로 판단하고 계정·비밀번호·로그인 버튼 행동을 계획했다.

| 항목 | 첫 시도 | 둘째 시도 |
|---|---|---|
| core 모델 호출 seq | 8 | 31 |
| core 행동 요청/반환 seq | 20/21 | 45/46 |
| 이미지 좌표 | [260,151] | [260,148] |
| 캡처 origin / DPI | [1609,806] / 144 | 동일 |
| 데스크톱 좌표 | [1869,957] | [1869,954] |
| 전경 ERP HWND / PID | 395316 / 16060 | 동일 |
| hit-test 루트 HWND / PID | 132800 / 40180 | 동일 |
| hit-test 프로세스 | chrome.exe | chrome.exe |
| 실제 입력 전달 | 없음 | 없음 |

새 `desktop-trace.jsonl`은 6개 trace, 92행을 보존했다. 첫 행동의 파일 줄 25는
`native_focus_skipped: already_foreground`, 43–46은 hit-test, 허용 검사, 거절, 결과다.
둘째 행동은 71, 89–92행에 대응한다. trace 내부 seq와 JSONL 파일 줄 번호는 다르다.

두 번 모두 `inside_active_bbox=true`, `hit_present=true`였지만
`hit_identity_matches=false`, `hit_bbox_matches=false`였다. 캡처 ID·크기·DPI 변화가
거절 원인은 아니었다. `_check_visual_point`에서 `point_outside_allowed_window`를
반환했으며 physical input 시작 이벤트가 없다. 새 입력 직전 캡처 2개도 남았다.

## 확인된 코드 원인

1. `relay/native_input.py:force_foreground`는 GetForegroundWindow가 ERP 핸들이면
   즉시 반환한다. 이번 두 행동에서 실제로 이 분기를 탔다. BringWindowToTop 및 활성화
   재시도는 실행되지 않았다. `focus_window`의 후속 확인도 전경 핸들만 확인한다.
2. `relay/service.py:_check_visual_point`는 별도로 현재 지점의 창을 검사했다.
   Windows가 Chrome을 반환했으므로 거절은 현재 계약대로 이루어졌다. 이 검사를 없애면
   다른 앱에 입력할 가능성을 남긴다. 모델 좌표 변경만으로 복구할 근거도 없다.
3. `brain/graph/execution.py:execute_plan`은 상세 error_code를 로그에는 남기지만
   실행 결과는 `GuardStop("relay_action_failed")`로 전달한다. `engine.py`는 이를
   계정 입력 목표의 실패로 누적한다. 실제 seq 23→재관찰→31 모델 재호출→48의 2회 실패→
   49 `repeated_subgoal_failure:task:credential.username` 순서다.

따라서 이번에 확인된 구조적 문제는 **전경 핸들 확인을 입력 준비 완료로 취급하는 복구 경계**와
**환경 불일치가 일반적인 업무 행동 재판단으로 흘러가는 경계**다. 메모 갱신 누락이나
로그인 정보를 모르는 모델이 이번 실패 원인이라는 근거는 없다.

## 확정하지 못한 부분과 다음 확인

Chrome이 hit-test에 잡힌 사실은 확정됐지만, 물리적 가림의 생성 원인까지 확정하지 않는다.
ERP와 Chrome 모두 기록된 TOPMOST/NOACTIVATE가 false다. '항상 위 창 때문'이라는
가설은 이 실행에서 뒷받침되지 않았다. 화면은 PrintWindow로 ERP만 렌더링하므로 실제
데스크톱 겹침을 보여주지 않는다. 당시 Z-order와 enabled 상태는 기록하지 않았다.
[Microsoft WindowFromPoint 문서](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-windowfrompoint)는
숨겨지거나 disabled된 창을 건너뛴다고 설명하므로, hit 불일치만으로 물리적 가림을 단정하지 않는다.

다음 최소 확인은 유료 모델 없이 같은 창 조건에서 전경·Z-order·enabled·DPI 문맥과
hit-test를 함께 확인하고, 허용 ERP를 앞으로 올리기 전후에 hit 대상이 ERP로 바뀌는지
비교하는 것이다. 이것이 바뀌지 않으면 전경 복구 조기 반환만 제거하면 해결된다는 가설은
기각해야 한다. 이 보고에서는 포커스 복구·거절·재시도 코드를 수정하지 않았다.

## 비공개 증거 위치

- bench `evidence/local/seed3-recheck-099/protocol.json`
- bench `evidence/runs/seed3-recheck-099-r1-cold/results.jsonl`
- 같은 실행 `core_visual-seed3-none-r1/relay/diagnostics/desktop-trace.jsonl`
- 같은 diagnostics `frames/08669fbecb2b424c80d9598f97163de7.png`,
  `frames/52edcb1d391449e789e59d1103133d3a.png`
- core `cases/seed3-recheck-099-r1-cold/969331b77282b2d71aaa/` 아래 case의
  run_log.jsonl, model-requests, relay-observations.jsonl, visual 및 relay-captures.

원문·캡처는 커밋하지 않는다. 이 변경은 보고서뿐이며 링크·경로와 `git diff --check`를
확인했다. 제품 코드가 바뀌지 않아 전체 단위 테스트는 반복하지 않았다.

## 정리 전후 커밋 대조

사용자의 정리 이전과의 차이 질문에 따라, 성공 091의 bench `1bdeba4`, UIA 삭제
`a51c717`, 진단 추가 `26697db`를 비교했다. 핵심 거절 분기·조기 반환이 정리에서
새로 생겼다는 근거는 없다. 그러나 모든 변경을 동작 보존으로 묶어서도 안 된다.

| 대상 | 확인 결과 |
|---|---|
| force_foreground 조기 반환 | `90039b0`(2026-09-30)에 도입. 091과 현재 모두 존재 |
| UIA 정리 전후 네이티브 동작 | force_foreground, _point_guard, hit_test_window, _visual_point, _check_visual_point의 AST가 `1bdeba4`와 `a51c717`에서 각각 동일 |
| visual_only focus_window | 기존의 force_foreground 분기를 남기고 다른 모드의 set_focus 분기를 제거. 091도 visual_only 사용 |
| 오류 전달 | core `f6540c5`와 `6444527`의 graph/execution.py diff 없음. 일반 relay_action_failed 변환은 기존 동작 |
| 실제 ERP 버튼 처리 변경 | `a51c717`에서 UiContext.cs의 BeginInvoke 예약을 제거하고 button.Click += action으로 즉시 호출. 실행 시점 변경이며 단순 주석 삭제가 아님 |
| 시작 경로 차이 | 091/092 desktop_handoff=false, 093–099=true. false에서는 relay_managed_focus로 startup 활성화를 생략하고, true에서는 별도 harness 활성화/안정화 경로 사용 |
| 진단 추가 | 파일 쓰기·추가 상태 읽기로 타이밍에 영향 가능. 098에서 이미 동일 오류가 발생했으므로 최초 발생 원인으로 볼 근거 없음 |

099의 두 `point_authorization` 기록을 Window 객체로 복원해 위 세 커밋에서 추출한
실제 `_check_visual_point` 함수에 읽기 전용으로 넣었다. backend는 기록된 active/hit을
반환하는 fake다. 세 버전 모두 두 건을 `point_outside_allowed_window`로 거절했다.
같은 전경 핸들을 반환하는 네이티브 fake에서도 세 버전 모두 BringWindowToTop/활성화
호출 0회였다. 이는 해당 분기 비교이며 과거 Windows 상태나 전체 ERP 재현은 아니다.

반례도 있다. 정리 이후 bench `5ae931f`의 092와 093은 ERP10/10·정상 종료했다.
093은 handoff=true였으므로 이 옵션 하나만으로 실패를 확정할 수 없다. 099는 첫 입력
전 거절되어 ERP 버튼 콜백에도 도달하지 않았다. 예약→즉시 호출 변경은 별도 영향 평가
대상이지만 이번 로그인 전 hit-test 불일치의 직접 원인으로 볼 수 없다.

따라서 비교 실험에서 먼저 통제할 것은 **091과 달라진 시작 옵션**이다. 같은 현재 코드에서
handoff=false/true를 비교해야 하며, 원격 모델 없이 초기 활성화·좌표 검사만으로 확인할 수 있다.
이번 조사에서는 파일럿 재실행이나 제품 변경을 하지 않았다.
