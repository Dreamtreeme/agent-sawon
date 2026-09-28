# 에이전트 사원 · 비공개 구현

화면을 관찰하여 선적 레코드를 사내 전산에 입력하는 사무 에이전트입니다.
현재 **M1 / 2단계 두뇌·중계·기준선·실행 하네스 구현**을 마쳤습니다.
화면 검증을 재개하여 시드 0 참조 녹화와 개발 시드 0/17 기준선 24건을 측정했습니다.
두뇌의 실제 계획 요청과 별도 진단 요청은 서버 오류로 중단됐고, 재확인에서
Gemini `503 UNAVAILABLE`을 확인했습니다. 모델 비교와 2단계 실험 완료 기준은 남아 있습니다.
진행 기준은 [CODEX_BRIEF.md](CODEX_BRIEF.md)입니다.

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
./.venv/Scripts/python.exe -m pip install -e ../agent-sawon-bench/interfaces -e '../agent-sawon-bench[dev]' -e '.[dev]'
./.venv/Scripts/python.exe -m pytest
```

`sawon_interfaces`만 editable 의존합니다. 공개 벤치마크에서 비공개 구현을
import하는 의존성은 없습니다. 두 저장소는 로컬 Git으로 관리하며 원격 공개는 별도 작업입니다.

## 구현된 구성

- `models/`: ModelClient, Gemini, replay, 성공/실패 호출 측정, 토큰/추정 비용/지연 기록.
- `brain/runtime/`: 덧붙이기 전용 실행 로그와 명시적 상태 전환, 원자적 상태 저장.
- `brain/graph/`: LangGraph 계획→관찰→선택→실행→검증, 중복 조회와 저장 후 대조.
- `brain/observe/`: `flat_fields` / `label_grouped` 관찰 요약.
- `brain/relay.py`, `brain/run.py`: 인증 HTTP 중계 클라이언트와 건별 실행 CLI.
- `models/budget.py`: 호출 전 비용 예약, 비용 미상·예산 초과 시 중단.
- `config/brain.toml`, `config/models.toml`, `config/pricing.toml`: 제한·요약·모델·가격 설정.
- `config/harness_agents.json`: 공개 하네스가 두 요약 전략을 실행하는 외부 CLI 설정.
- `tests/`: 실제 API 호출 없는 모델 테스트, 손상 로그/동시 쓰기/상태 저장 테스트.

```powershell
# API 키 없이 저장된 합성 응답 재생
./.venv/Scripts/python.exe -m examples.replay_demo
```

실행 결과는 무시되는 `cases/`에 기록합니다. 같은 폴더에서 재실행하면 기존 로그에
덧붙이며 순번을 이어 갑니다. replay의 실제 모델 비용은 0이며 원래 응답의 사용량은
별도 메타데이터입니다. Gemini 비용은 가격표 기반 추정치로 청구서 금액을 보장하지 않습니다.

Gemini는 `.env.example`을 `.env`로 복사하여 키를 채운 뒤
`create_model_client(Path('config/models.toml'), run_log=log, env_path=Path('.env'))`로
명시적으로 로드합니다. 테스트와 기본 재생 예제는 실제 API를 호출하지 않습니다.
가격은 호출 전에 확인하고 `config/pricing.toml`을 갱신합니다. 측정할 수 없는 실패 비용은
null이며 0으로 취급하지 않습니다. 자세한 예제는 [모델 계층 설명](models/README.md)을 봅니다.

## 2단계 실행과 검증

두뇌는 `sawon_interfaces` 계약과 HTTP 관찰만 사용합니다. 전산 DB·변형 시드·배치
파일은 받지 않으며, ERP 라벨·AutomationId·좌표·탭 순서를 코드에 고정하지 않습니다.
공개 하네스와의 경계는 CLI 설계상의 경계이며 운영체제 파일 접근 격리는 아닙니다.

기본값은 건당 선택 단계 80회, 같은 단계에서 진전 없는 실패 2회, 추정 비용 $0.50입니다.
하네스가 보고하는 단계 수는 실제 행동 요청의 고유 action_id 수입니다.
비용을 알 수 없는 모델 호출은 중단합니다. 성공 시 `awaiting_approval`에 멈추며
자동 전송·승인은 수행하지 않습니다. 입력 후 읽기와 최종 조회 행의 값 대조를 검증합니다.

화면 검증을 재개할 때는 공개 저장소에서 다음 순서로 새 실행을 만듭니다.

```powershell
# 공개 저장소 루트에서: 로그인한 Windows 데스크톱과 빌드된 ERP가 필요
./.venv/Scripts/python.exe -m pip install -e '.[dev,relay]'
./.venv/Scripts/python.exe -m harness.record_reference --output evidence/references/seed0.json
./.venv/Scripts/python.exe -m harness.run --agent core,selector_script,coord_replay --agent-config ../agent-sawon/config/harness_agents.json --reference evidence/references/seed0.json --seeds 0,17 --modes stable,none --repeats 3 --max-cost-usd 5
```

참조 녹화는 모델을 호출하지 않습니다. 비교 명령은 실제 모델을 호출하며, 예약 예산이
부족하거나 비용 미상 호출이 있으면 남은 모델 실행을 중단하고 미실행 행도 집계합니다.
GUI 명령을 실행하는 동안 전산 창을 유지해야 합니다. 허용 목록 밖의 창이 전경에
오면 중계가 중단합니다. 실패 자료는 보존하며 기존 출력 경로에 덮어쓰지 않습니다.
자세한 절차는 공개 저장소의 `harness/README.md`에 있습니다.

공개 저장소에는 수치·상태·해시만 내보내며, 비공개 모델 로그·프롬프트·캐시는
`cases/stage2/`에 남깁니다. API 키는 무시되는 `.env`에만 저장합니다.
현재 설정은 `gemini-3.8-flash`입니다. 초기 사전 호출 1회는 성공했지만, 이후
실제 계획 요청과 별도 진단은 서버 오류로 실패했습니다(재확인 HTTP 503).
실패 사용량과 비용은 null로 보존하며 추가 모델 호출은 중단했습니다.
초기 연결 검사는 전산 업무 성공률을 뜻하지 않습니다. D3 기본 요약은 `label_grouped`이고
두 전략의 우열은 미측정입니다.

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
원격 저장소를 만든 뒤 이 저장소의 Actions 변수 `BENCH_REPOSITORY`를 공개 저장소의
`owner/name`으로 설정해야 계약을 checkout할 수 있습니다. 로컬 검증에는 원격이 필요 없습니다.
