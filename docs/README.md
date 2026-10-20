# 문서 인덱스

이 디렉터리는 **현행 기준**과 **역사 기록**을 분리합니다. 현재 구현을 바꾸기 전에 아래
현행 문서를 먼저 읽습니다. 역사 보고서의 모델명, 비용, 경로, 미해결 목록은 당시 측정값이며
현재 동작을 복원하라는 요구사항이 아닙니다.

## 현행 기준

| 문서 | 역할 |
|---|---|
| [현행 런타임 기준](current-runtime.md) | 모델, 비용, 실행 경로, 화면 계약, 제거된 동작 |
| [계약 지도](contracts.md) | `WorkerState`, 실행 계약, `sawon_interfaces` 경계와 변경 절차 |
| [코드 실행 흐름](code-flow.md) | 네 책임 노드의 실제 라우팅과 주요 구현 파일 |
| [테스트 유지 기준](test-suite-policy.md) | 현재 회귀 테스트를 유지·삭제·통합하는 기준 |
| [설정 안내](../config/README.md) | 체크인된 실행 설정과 CLI |
| [시각 경로](../brain/visual/README.md) | OCR, OmniParser, 의미 매핑과 좌표 수명 |

우선순위는 실행 중인 사용자 지시와 `AGENTS.md`, 이 문서가 가리키는 현행 기준, 체크인된
설정·코드 순입니다. 루트 `CODEX_BRIEF.md`는 이 문서들의 짧은 진입점입니다. archive의 과거
brief와 보고서는 배경·측정 이력이며 현행 설정의 권위가 아닙니다. 서로 다르면 현행 기준과
실제 코드·설정을 함께 고칩니다.

## 역사 기록

- [날짜별 설계·분석·파일럿 보고서](archive/reports/README.md)
- [2026-10-18 이전 누적 CODEX_BRIEF](archive/CODEX_BRIEF-through-20261018.md)
- [2026-10-12 이전 AGENTS.md](archive/AGENTS-20261012-before-rewrite.md)
- [테스트 정리 측정](test-cleanup/)
- sibling bench의 `docs/progress.md`와 `evidence/`

역사 파일은 내용을 소급 수정하지 않습니다. 현재 문서에서 역사 결과를 인용할 때는 날짜와
당시 모델·설정·seed를 함께 적습니다.
