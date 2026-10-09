# 에이전트 사원 · 비공개 구현

**현재 기준(2026-10-08):** 화면 작업자는 `TaskInstruction`의 목표·요구사항·완료 조건과
사용자 교육 절차를 현재 OCR/OmniParser 근거에 연결합니다. 구형 `--procedure`와
`procedure_path` 진입 경로는 삭제됐고, `--task`/`task_path`만 사용합니다. 런타임은 한 개의
`ActionCapability`와 `Observation → Decision → Review → Execution` 상태 계약을 사용합니다.
[현재 코드 흐름](docs/code-flow.md), [일반 업무 계약](docs/generic-task-runtime-20261003.md),
[최신 잔여 정리 결과](docs/runtime-residual-fixes-20261008.md)를 기준으로 삼습니다.
현재 문서와 역사 기록의 구분은 [문서 인덱스](docs/README.md)에 있습니다.

로컬 시각 경로는 GPU PaddleOCR와 OmniParser v2/Florence-2를 사용하고 Luna를 주 화면 모델로
사용합니다. 가장 최근 v2/640 파일럿은 ERP 값 10/10 저장과 재조회 상세 화면까지 도달했지만,
이미 열린 탭을 반복 선택해 작업자가 `stopped`로 끝났습니다. 따라서 현재 코드는 실제 전체 업무
완료나 다른 ERP 호환성을 입증한 상태가 아닙니다. 측정 근거는
[OmniParser v2 파일럿 기록](docs/omniparser-v2-pilot-20261008.md)에 보존합니다.

실제 실행 기록에서 입력 묶음의 목적과 입력 역할을 학습해 새 값과 현재 화면에 다시 결합하는
방법 재사용 경로가 있습니다. 재사용 후보는 동일한 사용자 교육 해시에 묶이며 전체 성공 후에만
승격합니다. 화면 탐색·조회·저장·완료 판단은 필요할 때 모델 경로를 유지합니다.
[방법 학습](brain/demo_learning/README.md)과 [캐시·방법 설정](brain/cache/README.md)을 참고하세요.

화면을 관찰하고 선적 서류를 검증해 ERP에 입력하는 에이전트입니다.
현재 구현 범위는 **M2 3·4단계**이며 새 M1 연습 시험과 사람의 종료 확인은 미완료입니다.
화면 전환 후 후속 M1·M2 작업은 [개정 실행 계획](../agent-sawon-bench/docs/revised-development-plan-20260930.md)입니다.
새 [준비 파일럿18건](../agent-sawon-bench/evidence/m1-practice-pilot-20260930/README.md)은 실행했고
에이전트6/6·60/60필드 성공을 확인했습니다. 경량160회·상위6회의 혼합 조건이며 연습300건은 미실행입니다.

- 두뇌: 상태별 행동 제한, 중복 조회, 입력 검증, 최종 조회와 제한된 복구.
- 재사용: 현재 화면·업무 상태 기반 캐시와 실제 화면 실행 기록에서 학습한 업무 방법.
- 서류: 합성 PDF·OCR·측정 비전 추출·교차 검증·ERP 연계.
- 경계: 환경 접근은 relay HTTP, 모든 모델 호출은 비용 기록, 승인 없는 전송 금지.

[현재 코드 흐름](docs/code-flow.md), [설정 선택](config/README.md),
[파이프라인 안내](pipeline/README.md), [최신 진행 기록](../agent-sawon-bench/docs/progress.md)을 참고합니다.

이전 전체 비교는 일반 서류 42/42·팩스 3/6 기대 판정, 통합 8건 정상 종료·1건 저장 후 중단입니다.
화면 활성화 거부로 1,215건은 미시작이며 [실행 보고서](../agent-sawon-bench/evidence/m1-m2-final-20260930/README.md)에 남겼습니다.
이후 [전경 인계 보완](../agent-sawon-bench/evidence/desktop-handoff-20260930/README.md)은
실제 재시작 12/12·기준선 업무 2/2 통과로 검증했습니다. 전체 비교를 완료했다는 뜻은 아닙니다.

기존 실측 결과와 중단 이력은 `CODEX_BRIEF.md`의 날짜별 개정 기록과 sibling bench의
`docs/progress.md`, Git 이력에 보존합니다.

## 실행 준비

Python 3.13을 설치하고 두 저장소를 아래처럼 나란히 둡니다.

```text
workspace/
  agent-sawon-bench/
  agent-sawon/
```

PowerShell에서 이 저장소를 기준으로 실행합니다.

```powershell
./scripts/setup.ps1
./scripts/check.ps1
```

설정이 스크립트 실행을 제한하면 같은 명령을 수동으로 실행할 수 있습니다.

```powershell
py -3.13 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ../agent-sawon-bench/interfaces -e '../agent-sawon-bench[dev]' -e '.[dev,pipeline]'
./.venv/Scripts/python.exe -m pytest
```

코어가 import하는 벤치 코드의 범위는 `sawon_interfaces` 계약뿐입니다. 설치 스크립트는
통합 테스트에 필요한 벤치 패키지도 editable로 설치합니다. 벤치마크는 비공개 구현을
import하지 않습니다. 두 저장소는 로컬 Git과 GitHub 비공개 저장소로 관리합니다.
구현 저장소는 [Dreamtreeme/agent-sawon](https://github.com/Dreamtreeme/agent-sawon),
벤치마크는 [Dreamtreeme/agent-sawon-bench](https://github.com/Dreamtreeme/agent-sawon-bench)입니다.
공개 배포는 별도 결정이며, API 키·원본 데이터·실행 캐시는 Git에 포함하지 않습니다.

## 구현된 구성

- `models/`: ModelClient, OpenAI/Luna, Gemini, Jev, replay, 성공/실패 호출 측정,
  토큰/추정 비용/지연 기록.
- `sawon_runtime/`: 덧붙이기 전용 실행 로그, 원자적 상태 저장, OCR·화면 공용 계약과
  지속 로컬 워커.
- `brain/graph/`: `Observation → Decision → Review → Execution` 상태 계약, 현재 근거 검토와 실행 후 재관찰.
- `brain/observe/`: `flat_fields` / `label_grouped` 관찰 요약.
- `brain/relay.py`, `brain/run.py`: 인증 HTTP 중계 클라이언트와 건별 실행 CLI.
- `models/budget.py`: 호출 전 비용 예약, 비용 미상·예산 초과 시 중단.
- `config/brain.visual.toml`, `config/models.toml`, `config/pipeline.toml`: 화면·모델·OCR 설정.
- `models/pricing.toml`: 호출 비용 계산용 단가 데이터. [설정 안내](config/README.md).
- `tests/`: 실제 API 호출 없는 모델 테스트, 손상 로그/동시 쓰기/상태 저장 테스트.

현재 주 모델은 Luna이며 비공개 `.env`의 `OPENAI_API_KEY`를 명시적으로 읽습니다.
실행 자료는 무시되는 `cases/`에 저장합니다. 비용은 `models/pricing.toml`의 확인 날짜와
출처에 따른 추정치이며, 측정 불가 비용은 0이 아닌 null입니다.
[모델 계층 설명](models/README.md)과 [실행 설정](config/README.md)을 참고하세요.

## 화면 실행과 검증

두뇌는 `sawon_interfaces` 계약과 HTTP 관찰만 사용합니다. 전산 DB·변형 시드·배치
파일은 받지 않으며, ERP 라벨·AutomationId·좌표·탭 순서를 코드에 고정하지 않습니다.
공개 하네스와의 경계는 CLI 설계상의 경계이며 운영체제 파일 접근 격리는 아닙니다.

스키마 기본값은 건당 선택 단계 80회, 같은 단계에서 진전 없는 실패 2회, 추정 비용 $0.50이며
체크인된 `brain.visual.toml`은 건당 $1.00을 설정합니다.
하네스가 보고하는 단계 수는 실제 행동 요청의 고유 action_id 수입니다.
비용을 알 수 없는 모델 호출은 중단합니다. 성공 시 `awaiting_approval`에 멈추며
자동 전송·승인은 수행하지 않습니다. 입력 후 읽기와 최종 조회 행의 값 대조를 검증합니다.

현재 시각 프로필은 사용자 완료 조건과 Luna가 제출한 현재 마커 evidence를 연결하고,
코드는 대상·입력 출처·좌표·허용 창·비용·반복 경계를 검사합니다. relay의 전달 성공만으로
업무 완료를 인정하지 않습니다. non-screen-matching 호환 경로의 중복 조회와 최종 행 검증은
결정론적 행 규칙을 계속 사용합니다. 두 모드 모두 전경 이탈·조회값 변경·근거 충돌을
성공으로 처리하지 않습니다.

현재 화면 실행 명령은 [설정 안내](config/README.md)에 있습니다.
과거 하네스 비교 프리셋은 삭제했으며 기존 결과와 Git 이력은 보존합니다.

벤치 저장소에는 수치·상태·해시만 내보내며, 비공개 모델 로그·프롬프트·캐시는
선택한 에이전트 설정의 `case_root` 아래에 남깁니다. API 키는 무시되는 `.env`에만 저장합니다.
모델 선택과 과거 비교 설정의 구분은 [설정 안내](config/README.md)를 따릅니다.
요약 전략의 우열은 미완료인 전체 비교를 대신해 판단하지 않습니다.

## 기록 보장과 한계

동일 디스크에서 이 유틸리티를 사용하는 프로세스끼리 sidecar 파일 잠금으로
순번과 상태 전환을 직렬화합니다. 로그가 손상되면 파일을 고치거나 잘라내지 않고
예외를 발생시킵니다. 관리자가 손상 원인을 조사해야 합니다. 잠금 방식은
[Portalocker 문서](https://portalocker.readthedocs.io/en/latest/)에 따릅니다.
M3의 분산 작업 점유/서버 권한은 아직 구현하지 않았습니다.

## UIA 비교와 진행 기록

GUI 비교에는 `./scripts/setup.ps1 -Gui`와 .NET 8 SDK가 필요합니다.
실행 절차와 측정 결과는 공개 저장소의 `mock_erp/probes/README.md`,
`docs/decisions/0001-ui-framework.md`, `docs/progress.md`에 있습니다.
D1 기술 선택은 제안 상태로 유지합니다.

가상 전산을 실행하려면 공개 저장소에서 `./scripts/start_mock_erp.ps1`을 실행합니다.
로그인·등록·조회·수정·권한 검사, SQLite, 시드 변형, 채점기와 VM 준비 스크립트의
재현 방법은 공개 저장소 README에 정리했습니다.

## CI

두 저장소의 GitHub Actions는 GUI를 제외하고 린트/포맷/테스트를 실행합니다.
Actions 변수 `BENCH_REPOSITORY`는 `Dreamtreeme/agent-sawon-bench`로 설정합니다.
현재 벤치마크도 비공개이므로 해당 저장소에만 접근하는 읽기 전용 deploy key를 등록하고,
개인키는 구현 저장소의 Actions secret `BENCH_READ_KEY`에 저장합니다.
개인 계정 토큰은 CI에 복사하지 않으며 checkout 이후 인증 정보도 유지하지 않습니다.
로컬 검증에는 원격이 필요 없습니다.
