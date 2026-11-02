# 문서 인덱스

[화면 처리 용어와 코드 대응](runtime-terminology.md)은 Scanning, Pattern Matching,
Memory Retrieval, Context Recall과 Memory Compilation의 현재 구현 위치를 정리합니다.

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

- [날짜별 로컬 아카이브와 Git 이력 복원 안내](archive/README.md)
- sibling bench의 `docs/progress.md`와 `evidence/`

일회성 보고서·테스트 정리 측정·과거 지침·첨부 화면은 `archive/YYYY-MM-DD/`에 원래 경로와
원문을 보존합니다. 날짜별 폴더는 Git에서 제외되므로 새 체크아웃에는 포함되지 않습니다.
현재 문서에서 역사 결과를 인용할 때는 날짜와 당시 모델·설정·seed를 함께 적습니다.
