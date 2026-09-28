# 에이전트 사원 · 비공개 구현

화면을 관찰하여 선적 레코드를 사내 전산에 입력하는 사무 에이전트입니다.
현재 구현 범위는 **M1 / 0단계 준비**입니다. 가상 전산 전체, 신체 중계, 두뇌의
GUI 실행 루프는 다음 단계에서 구현합니다. 진행 기준은 [CODEX_BRIEF.md](CODEX_BRIEF.md)입니다.

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
- `config/models.toml`, `config/pricing.toml`: 제공자, 모델, 가격 설정.
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

## CI

두 저장소의 GitHub Actions는 GUI를 제외하고 린트/포맷/테스트를 실행합니다.
원격 저장소를 만든 뒤 이 저장소의 Actions 변수 `BENCH_REPOSITORY`를 공개 저장소의
`owner/name`으로 설정해야 계약을 checkout할 수 있습니다. 로컬 검증에는 원격이 필요 없습니다.
