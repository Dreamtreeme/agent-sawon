# 에이전트 사원 · 비공개 구현

화면을 관찰하고 선적 서류를 검증해 ERP에 입력하는 에이전트입니다.
현재 구현 범위는 **M2 3·4단계**이며 M1 전체 비교와 사람의 종료 확인은 미완료입니다.

- 두뇌: 상태별 행동 제한, 중복 조회, 입력 검증, 최종 조회와 제한된 복구.
- 캐시: 의미 입력 캐시, 관찰 기반 시연 학습, 화면 변화에 따른 재연결과 독립 실행.
- 서류: 합성 PDF·OCR·측정 비전 추출·교차 검증·ERP 연계.
- 경계: 환경 접근은 relay HTTP, 모든 모델 호출은 비용 기록, 승인 없는 전송 금지.

[현재 코드 흐름](docs/code-flow.md), [설정 선택](config/README.md),
[파이프라인 안내](pipeline/README.md), [최신 진행 기록](../agent-sawon-bench/docs/progress.md)을 참고합니다.

최근 전체 비교는 일반 서류 42/42·팩스 3/6 기대 판정, 통합 8건 정상 종료·1건 저장 후 중단입니다.
화면 활성화 거부로 1,215건은 미시작이며 [실행 보고서](../agent-sawon-bench/evidence/m1-m2-final-20260930/README.md)에 남겼습니다.
이후 [전경 인계 보완](../agent-sawon-bench/evidence/desktop-handoff-20260930/README.md)은
실제 재시작 12/12·기준선 업무 2/2 통과로 검증했습니다. 전체 비교를 완료했다는 뜻은 아닙니다.

기존 실측 결과와 중단 이력은 [이전 진행 서술](docs/readme-history-20260930.md)에 보존합니다.

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

- `models/`: ModelClient, Gemini, replay, 성공/실패 호출 측정, 토큰/추정 비용/지연 기록.
- `brain/runtime/`: 덧붙이기 전용 실행 로그와 명시적 상태 전환, 원자적 상태 저장.
- `brain/graph/`: LangGraph 계획→관찰→선택→실행→검증, 중복 조회와 저장 후 대조.
- `brain/observe/`: `flat_fields` / `label_grouped` 관찰 요약.
- `brain/relay.py`, `brain/run.py`: 인증 HTTP 중계 클라이언트와 건별 실행 CLI.
- `models/budget.py`: 호출 전 비용 예약, 비용 미상·예산 초과 시 중단.
- `config/brain.toml`, `config/models.toml`, `config/pricing.toml`: 제한·요약·모델·가격 설정.
- `config/harness_agents*.json`: 비교 조건별 외부 CLI 설정. [설정 안내](config/README.md)에서 선택.
- `tests/`: 실제 API 호출 없는 모델 테스트, 손상 로그/동시 쓰기/상태 저장 테스트.

```powershell
# API 키 없이 저장된 합성 응답 재생
./.venv/Scripts/python.exe -m examples.replay_demo
```

실행 결과는 무시되는 `cases/`에 기록합니다. 같은 폴더에서 재실행하면 기존 로그에
덧붙이며 순번을 이어 갑니다. replay의 실제 모델 비용은 0이며 원래 응답의 사용량은
별도 메타데이터입니다. Gemini 비용은 가격표 기반 추정치로 청구서 금액을 보장하지 않습니다.

Gemini는 비공개 `.env` 파일에 `GEMINI_API_KEY`를 설정한 뒤
`create_model_client(Path('config/models.toml'), run_log=log, env_path=Path('.env'))`로
명시적으로 로드합니다. 테스트와 기본 재생 예제는 실제 API를 호출하지 않습니다.
가격은 호출 전에 확인하고 `config/pricing.toml`을 갱신합니다. 측정할 수 없는 실패 비용은
null이며 0으로 취급하지 않습니다. 자세한 예제는 [모델 계층 설명](models/README.md)을 봅니다.

## 화면 실행과 검증

두뇌는 `sawon_interfaces` 계약과 HTTP 관찰만 사용합니다. 전산 DB·변형 시드·배치
파일은 받지 않으며, ERP 라벨·AutomationId·좌표·탭 순서를 코드에 고정하지 않습니다.
공개 하네스와의 경계는 CLI 설계상의 경계이며 운영체제 파일 접근 격리는 아닙니다.

기본값은 건당 선택 단계 80회, 같은 단계에서 진전 없는 실패 2회, 추정 비용 $0.50입니다.
하네스가 보고하는 단계 수는 실제 행동 요청의 고유 action_id 수입니다.
비용을 알 수 없는 모델 호출은 중단합니다. 성공 시 `awaiting_approval`에 멈추며
자동 전송·승인은 수행하지 않습니다. 입력 후 읽기와 최종 조회 행의 값 대조를 검증합니다.

중복 검색 실행 후에는 현재 결과 그리드를 제출하는 필수 검증 단계로 들어갑니다.
이 단계의 응답 스키마는 화면 이동·입력 행동을 허용하지 않으며, 실행기도 같은 조건으로
행동을 차단합니다. 누락되거나 잘못된 근거에는 현재 화면을 다시 관찰해 보완을 요청합니다.
보완 요청도 같은 실패 횟수·단계·비용 한도에 포함됩니다. 정확한 조회값과 같은 조회 창의
현재 결과를 검증한 뒤에만 입력 단계로 전환하며, 전경 이탈·조회값 변경은 즉시 중단합니다.

화면 검증은 벤치 저장소에서 새 출력 경로로 실행합니다. 전체 비교용 경량 모델의 두
요약 전략은 `config/harness_agents.m1-final.json`의 `core_lite`와 `core_lite_flat`입니다.
두 설정은 같은 캐시 비활성·상위 복구 조건에서 요약 전략만 다릅니다. 초기 비교용
`harness_agents.json`과 `harness_agents.lite.json`은 이전 조건의 재현에 사용합니다.

```powershell
# 공개 저장소 루트에서: 로그인한 Windows 데스크톱과 빌드된 ERP가 필요
./.venv/Scripts/python.exe -m pip install -e '.[dev,relay]'
$RunStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$ReferencePath = "evidence/references/seed0-$RunStamp.json"
./.venv/Scripts/python.exe -m harness.record_reference --output $ReferencePath
if ($LASTEXITCODE -ne 0) { throw 'Reference recording failed.' }
./.venv/Scripts/python.exe -m harness.run --agent core_lite,core_lite_flat,selector_script,coord_replay --agent-config ../agent-sawon/config/harness_agents.m1-final.json --reference $ReferencePath --seeds 0,17 --modes stable,none --repeats 3 --max-cost-usd 5 --desktop-handoff --output "evidence/runs/dev-pilot-$RunStamp"
```

참조 녹화는 모델을 호출하지 않습니다. 비교 명령은 실제 모델을 호출하며, 예약 예산이
부족하거나 비용 미상 호출이 있으면 남은 모델 실행을 중단하고 미실행 행도 집계합니다.
GUI 명령을 실행하는 동안 전산 창을 유지해야 합니다. 허용 목록 밖의 창이 전경에
오면 중계가 중단합니다. 실패 자료는 보존하며 기존 출력 경로에 덮어쓰지 않습니다.
위 예제는 전경 인계 옵션을 명시한 별도 파일럿이며, 기존 전체 비교의 재개 계획을 대신하지 않습니다.
자세한 절차는 공개 저장소의 `harness/README.md`에 있습니다.

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
