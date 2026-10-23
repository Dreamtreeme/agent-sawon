# Agent Sawon 작업 기준

기준일: 2026-10-18

Agent Sawon은 문서의 업무 데이터를 검증하고, 사용자 교육과 현재 화면 관찰을 결합해 Windows
ERP를 조작합니다. core 저장소는 판단·모델·문서 처리를, sibling `agent-sawon-bench`는 공개
계약·relay·Mock ERP·하네스·채점을 소유합니다.

## 작업 전에 읽을 문서

1. [AGENTS.md](AGENTS.md): 저장소 작업 규칙과 검증 명령
2. [현행 런타임 기준](docs/current-runtime.md): 모델, 비용, 화면 계약, 제거된 동작
3. [계약 지도](docs/contracts.md): `WorkerState`, 실행 계약, `sawon_interfaces` 경계
4. [코드 실행 흐름](docs/code-flow.md): 실제 책임 노드와 폴백 흐름

이 네 문서와 현재 코드·설정이 운영 기준입니다. 2026-10-18 이전의 누적 원문은
[역사 CODEX_BRIEF](docs/archive/CODEX_BRIEF-through-20261018.md)에 보존합니다. 날짜별 보고서와
과거 brief의 모델명, 비용, 테스트 개수, 경로와 미완료 계획은 현재 요구사항이 아닙니다.

## 현재 운영 값

- 주 화면 모델: Anthropic `claude-haiku-5-5`
- 비용: 실행 건별 최대 `$3`; 프로젝트 누적 상한 없음
- 관찰: GPU PaddleOCR + OmniParser v2
- 화면 판단: 의미 이름, OCR text+bbox, control bbox와 모델 선택 point
- 재사용: 전체 성공한 원본 실행에서 편찬하고 현재 viewport·pHash에 재결합한 편찬된 기억
- 완료: relay 전달이 아니라 사용자 완료 조건의 후속 화면 근거

상세 값은 이 문서에 중복하지 않고 `config/models.toml`, `config/brain.visual.toml`과
`docs/current-runtime.md`에서 관리합니다.

## 구현 원칙

- ERP별 버튼명·좌표·탭 순서를 프로덕션 코드에 하드코딩하지 않습니다.
- 사용자가 가르친 절차, 현재 관찰, 누적 업무 메모와 입력 key를 모델 판단에 함께 제공합니다.
- detector 번호는 현재 관찰의 임시 handle이며 지속 업무 key나 기억 식별자로 저장하지 않습니다.
- `WorkerState`가 그래프 상태를 소유하고 `ExecutionRequest`/`ExecutionResult`가 실행 경계를
  담당합니다. 서비스에 전체 `Brain`을 넘기지 않습니다.
- core가 bench에서 직접 사용하는 계약은 `sawon_interfaces`로 제한합니다.
- 모델 호출은 공용 계측 경계를 지나 비용·지연·실패를 기록합니다.
- 실제 API 키, 업무 데이터, 화면, 모델 원문과 실행 캐시는 커밋하지 않습니다.

## 제거된 동작을 복원하지 않는 기준

고정 Procedure 상태 기계, Florence-2 기능 캡션, detector 번호 기반 지속 매핑,
입력 직전 전체 픽셀의 정확한 일치만으로 행동을 거절하는 로직은 현재 경로가 아닙니다. 역사
보고서에 남아 있다는 이유로 복원하지 않습니다. 다시 도입하려면 새 요구사항과 현재 화면 근거,
결함을 잡는 회귀 테스트가 필요합니다.

## 완료와 검증

테스트 개수는 고정 완료 기준이 아닙니다. 코드 변경은 Ruff lint/format과 현재 전체 pytest를,
화면 경계 변경은 sibling bench relay 테스트를 함께 통과해야 합니다. 실제 ERP 성공을 주장할
때는 모델·교육 파일·콜드/웜 조건·시간·호출·비용·화면과 채점 결과를 같은 실행에서 보존합니다.
