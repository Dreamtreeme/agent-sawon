# 정리 전 성공 커밋 복구와 현재 코드 비교: 100·101

## 복구 방식과 통제 조건

사용자 요청으로 UIA 정리 전 마지막 성공 실행 091의 두 저장소 커밋을 별도 worktree에
복구했다. 현재 dev를 reset하거나 후속 수정을 삭제하지 않았다.

| 구분 | core | bench |
|---|---|---|
| 복구본 100 | f6540c51b33e5bdff32ee2ac3adbd406bbc9cb7a | 1bdeba4b664add9e521575b335a908c6ad4eac2c |
| 현재 코드 101 | 3c450ccc9139d0bf9e7c164923256996511ecd3e | 26697dbaa53fc50ee787d1ab68c178a72ff3ac27 |

복구 위치는 기존 저장소들의 부모 아래 `agent-sawon-compare-091/agent-sawon`과
`agent-sawon-compare-091/agent-sawon-bench`이며 detached HEAD로 보존한다.

- 두 실행 모두 seed 3, ID none, visual_only, cold 1회, 새 DB·case·cache·method 경로.
- 091과 동일하게 **desktop_handoff=false**. 당시 교육 파일의 CRLF 원문을 비공개
  실행 폴더에 복원했다. SHA256은
  `125c304b1e7746ae90ddef3c639b92c8957518d89d6d3d4d7238d0ef1e5154b1`.
  현재 LF 파일과 JSON 내용은 같으며 줄바꿈만 다르다.
- Haiku 5.5, low/8192, 각 실행 비용 상한 $0.30, 각 1회만 실행. 자동 재시도 없음.
- 복구본 ERP는 해당 커밋의 소스를 빌드했다. 제품 소스는 수정하지 않았다.
- 두 실행 모두 현재 core의 Python 환경을 공용으로 사용했다. PYTHONPATH는 각각의
  core/bench/interfaces 소스를 우선하도록 실행 프로세스에만 지정했다.
  brain, relay, sawon_interfaces의 실제 import 파일 경로를 출력해 분리를 확인했다.
- 기존 GPU OCR·탐지 환경 및 가중치를 재사용했다. `.env`는 기존 명시 경로를 사용했고
  복사하거나 출력하지 않았다. 원문 기록용 private launcher는 복구본 .tools에 복사했다.
- 실행 중 코드·프롬프트·설정은 변경하지 않았다. 완료 후 네 worktree 모두 clean 확인.

## 측정 결과

| 실행 | ERP | 작업자 | 전체 시간 | 작업자 시간 | 원격 호출 | 로컬 탐지 | 비용 |
|---|---|---|---:|---:|---:|---:|---:|
| 과거 091 기록 | 10/10 | success/exit 0 | 194.60초 | 190.54초 | 20 | 10 | $0.0359221 |
| 복구본 100 | 10/10 | success/exit 0 | 180.99초 | 177.25초 | 23 | 10 | $0.0405284 |
| 현재 101 | 10/10 | success/exit 0 | 176.64초 | 172.95초 | 20 | 10 | $0.0397068 |

신규 비용 합계 **$0.0802352**, 미확정 비용 0. 두 신규 실행 모두 하네스 독립 ERP 채점과
작업자 정상 종료를 달성했다. final capture에서 저장 번호 1의 상세 화면과 기본 정보·품명을
직접 확인했다. 각 실행 로그와 원문을 별도 보존했다.

100은 행동 반환 31건, 101은 28건이 모두 성공했고 `point_outside_allowed_window`는 0건.
100은 search 행동 4회, 101은 1회였으나 한 쌍만으로 호출·시간 개선 효과를 확정하지 않는다.
복구본에서도 조회 반복이 있었으므로 이전 버전이 항상 더 안정적이라는 근거도 되지 않는다.

## 099 실패와 대조한 활성화 경로

| 단계 | 099 실패 | 현재 101 성공 |
|---|---|---|
| desktop_handoff | true | false |
| harness startup | ERP 활성화 및 0.5초 확인 | relay_managed_focus, startup 활성화 생략 |
| relay 첫 전경 검사 | 이미 ERP | Chrome HWND 132800 |
| relay BringWindowToTop | 실행 안 함 | 1회 실행 |
| 입력점 hit-test | Chrome | ERP |
| 입력 결과 | 계정 입력 전 2회 거절 | 로그인부터 최종 확인까지 성공 |

101의 신규 trace는 1,965행, point_authorization 56회, 모두 failed_checks가 비어 있었다.
입력 직전 캡처 20개가 보존됐다. 시작 시 native_focus_before의 예상 ERP는 3672076,
실제 전경은 Chrome 132800이었다. native_bring_to_top_returned 이후
native_focus_after가 ERP 3672076을 확인했으며 계정 입력점도 dotnet.exe로 연결됐다.

이번 관찰은 **현재 코드의 전경 복구 기능이 사라진 것이 아니라, 앞선 활성화 경로에 따라
실제 창 올리기가 생략되는 문제**라는 가설을 강화한다. `desktop_handoff=true`에서는
harness가 SetForegroundWindow를 먼저 호출하고, relay는 이미 전경이라는 이유로
BringWindowToTop 경로를 건너뛸 수 있다. 두 활성화 구현의 책임 중첩을 우선 조사해야 한다.

다만 099와 101은 실행 시점과 다른 창의 상태까지 고정한 실험은 아니다. 099는 bench
Python으로 하네스를 실행했고 100·101은 core Python을 공용으로 사용했다. 조사한
Python 3.13.14 및 pywin32 312/PyAutoGUI 0.9.54/Pillow 12.3.0/psutil 7.2.2/Pydantic
2.13.5 버전은 양 환경에서 같지만 전체 환경 동일성을 증명한 것은 아니다.
과거 093은 handoff=true에서도 성공했다. 따라서 이 옵션이 항상 실패를 만든다는 결론이나
완전한 원인 분리는 아직 아니다. 다음 최소 검증은 같은 interpreter·현재 코드·창 배치에서
handoff 유무만 바꾸는 모델 없는 활성화/hit-test 대조다.

## 검증 범위와 결론

- 복구본 네이티브 전경/visual relay 테스트 **44 passed**, 실제 모델·GUI 없는 무료 검사.
- 복구본 ERP Release 빌드 **경고 0·오류 0**.
- 복구본과 현재 코드의 실제 ERP 각각 1회 성공. 로그인, 입력, 저장, 재조회, 상세 확인 완료.
- 제품 코드 무수정이므로 전체 단위 테스트를 다시 실행하지 않았다. 보고서 diff 공백 검사.
- 과거 Python/GPU 패키지 환경과 ERP 바이너리를 바이트 단위로 복구한 실험은 아니다.
  source commit을 고정하고 현재 설치 환경에서 다시 빌드·실행한 비교다.
- 전체 rollback의 필요성은 이번 결과로 뒷받침되지 않는다. 복구 worktree는 남겨 두고
  현재 dev는 유지한다. 창 활성화 경로 변경은 이 작업에서 구현하지 않았다.

비공개 protocol/config는 bench `evidence/local/restore-091-100`, `restore-091-101`에,
결과는 `evidence/runs/restore-091-100`, `restore-091-101`에 있다. core case는 각각의
core worktree `cases/restore-091-100`, `cases/restore-091-101`에 보존한다.
