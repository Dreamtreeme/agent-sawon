# 문서 상태

기준일: 2026-10-08. 날짜가 붙은 측정 보고서는 당시 입력·코드·설정에서 얻은 기록이므로
수치와 실패 원인을 소급해서 고치지 않는다. 현재 실행 방법과 계약은 아래 “현재 기준” 문서를
우선한다. 역사 기록의 명령·파일명·미해결 목록은 현재 기능을 복원하라는 요구사항이 아니다.

## 현재 기준

| 문서 | 역할 |
|---|---|
| [코드 실행 흐름](code-flow.md) | 현재 visual-only 작업자의 네 책임 노드와 호출 순서 |
| [일반 ERP 업무 계약](generic-task-runtime-20261003.md) | TaskInstruction, 단일 ActionCapability, 재사용 경계 |
| [런타임 잔여 문제 수정](runtime-residual-fixes-20261008.md) | 최신 계약 정리와 677개 회귀 기준 |
| [OmniParser v2 파일럿](omniparser-v2-pilot-20261008.md) | 최신 실제 화면 결과와 미해결 탭 반복 |
| [테스트 스위트 정책](test-suite-policy-20261004.md) | 테스트 유지·삭제 판단 기준 |
| [설정 안내](../config/README.md) | 현재 세 실행 설정과 CLI |
| [시각 경로](../brain/visual/README.md) | OCR, OmniParser v2, marker와 좌표 보완 |
| [작업자 런타임](../brain/graph/README.md) | WorkerState와 Observation/Decision/Review/Execution 계약 |
| [화면 캐시와 업무 방법](../brain/cache/README.md) | 현재 지원하는 재사용 범위 |

## 완료 단계와 역사 기록

| 문서 | 읽는 방법 |
|---|---|
| [작업자 리팩토링 단계](refactor-runtime-stages-20261008.md) | 단계 2–6의 완료 기록 |
| [task 진입 경로 통합](refactor-task-only-20261008.md) | 1단계 당시 기록; capability 후속 변경은 최신 수정 문서 우선 |
| [삭제 후보 조사](pilot-path-deletion-candidates-20261008.md) | 삭제 전 조사와 이관 과정 |
| [런타임 잔여 조사](runtime-residual-audit-20261008.md) | 최신 수정 전 원인 조사 |
| [추상화 경계 1차 점검](abstraction-boundaries-20261008.md) | 후속 리팩토링 전 결합 목록 |
| [M2 구현 기록](m2-implementation.md) | 삭제된 UIA 캐시·시연 경로를 포함한 2026-09-29 기록 |
| [사용자 교육 파일럿](taught-procedure-runtime-20261003.md) | 2026-10-03 콜드·웜 실측 |
| [방법 복구 계획](experience-runtime-repair-plan-20261003.md) | 최초 방법 학습 설계와 당시 결과 |
| [파일럿 리팩토링](pilot-refactor-20261008.md) | 특정 실행의 영상·로그 검토 |

`CODEX_BRIEF.md`는 범위·불변 규칙과 날짜별 이력을 함께 보존한다. 0.1절의 최신 개정이
아래 과거 절의 충돌하는 설명보다 우선한다. 외부 벤치의 진행률과 증거는 sibling
`agent-sawon-bench/docs/progress.md`에서 관리한다.

`AGENTS.md`는 현재 실행 지침만 담는다. 2026-10-12 전면 재작성 직전의
원문은 [AGENTS 아카이브](archive/AGENTS-20261012-before-rewrite.md)에 보존했다.
