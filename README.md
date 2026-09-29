# 에이전트 사원 · 비공개 구현

화면을 관찰하여 선적 레코드를 사내 전산에 입력하는 사무 에이전트입니다.
현재 **M2 3·4단계 기능 구현·개발 파일럿 재적용과 후속 검증**을 진행했습니다.
의미 입력 캐시·시연 변환, 별도 업무 근거 검토, 조건부 상위 모델 복구,
합성 PDF·PaddleOCR·비전 추출·교차 검증·ERP 입력 연결을 제공합니다.

[구현 상태](docs/m2-implementation.md), [현재 코드 흐름](docs/code-flow.md),
[후속 반복 검증 보고서](../agent-sawon-bench/evidence/m2-followup-20260929/README.md)를 참고합니다.
이전 [M2 파일럿](../agent-sawon-bench/evidence/m2/README.md)에서 경량 기본+상위 1회 복구로
실제 서류부터 ERP 저장·최종 재조회까지 10/10 일치했습니다.
캐시 비교 완료 표본은 28→19 모델 호출이며, 각1회여서 일반 성능 개선율로 확대하지 않습니다.
최초 정상 팩스 차단을 분석해 원본·처리 OCR 근거를 보존하고, 구두점·식별자 비교와 반복 측정을 보완했습니다.
서류 개발 시드0과 [새 시드17](../agent-sawon-bench/evidence/m2-seed17-20260929/README.md)은
각각 8조건×3회, **24/24회 기대 판정 일치**, 정상 팩스3/3 준비 통과입니다.
일반7조건은 경량, 팩스는 상위 모델입니다. 각 시드의 같은 서류를 반복했으며 서류 반복 측정에서는 ERP 입력을 검증하지 않았습니다.
시드17의 최초 중단 계획24건(실측1·미시작23)은 수정 후 새24건과 구분해 보존했습니다.
환경 오류가 연속되면 하네스가 남은 시도를 취소합니다. 빈 캐시·고정 스냅샷의 독립 비교도 준비했습니다.
최초 [격리 화면 비교](../agent-sawon-bench/evidence/m2-isolated-cache-20260929/README.md)는
18건 중1건 성공 뒤 ERP 전경 활성화 거부로1건 실패·16건 취소됐습니다. 이 기록을 보존한 뒤
[새 비교18건](../agent-sawon-bench/evidence/m2-isolated-cache-retry1-20260929/README.md)은 **18/18회 모두10/10 필드 일치**로 통과했습니다.
시드0의 평균 모델 호출은 캐시 없음28.67회→고정 캐시17회, 시드17은 두 조건 모두28회였습니다.
당시 정확 일치 캐시는 라벨과 상위 구조 차이로 시드17에서 재사용되지 않았습니다.
후속 [재연결 구현](docs/cache-rebinding-plan-20260929.md)은 공통 역할·라벨로 구조 변화를 허용하고,
달라진 라벨은 최대1회 모델 호출로 묶어서 제안받습니다. 기본은 기존 정확 일치이며 별도 설정으로 켭니다.
[실제12회 비교](../agent-sawon-bench/evidence/m2-cache-rebinding-live-20260929/README.md)는11/12회 성공, 재연결 방식6/6회 성공입니다.
시드17 세 반복 모두 직접5개·모델 제안6개로 완료하며 모델 호출28→18회, 추정 비용50.5% 감소를 확인했습니다.
시드0 기존 방식1건은 부적합 입력 대상 선택으로 중단됐습니다. 전체234호출·추정$0.411441365입니다.
Windows 파일 잠금에 대한 원자 교체 재시도와 외부 프로세스의 비정상 종료 판정도 보완했습니다.
비공개539개·벤치마크476개 일반 테스트 및 양 저장소 린트·포맷 검사를 통과했습니다.
벤치마크 수치는 변경되지 않은 실행 코드의 이전 검사입니다. M1 전체 매트릭스, 추가 시드·변형과 실제 캐시 실패 복구 측정,
사람의 마일스톤 종료 점검과 결정 승인은 남아 있습니다.

M2 CLI 예시는 [파이프라인 안내](pipeline/README.md)와 `config/harness_agents.m2.json`,
`config/harness_agents.m2-workflow.json`에 있습니다. 격리된 캐시 비교는
[캐시 안내](brain/cache/README.md)와 `config/harness_agents.cache-isolated.json`을 참고합니다.
모든 경로는 새 case/output을 사용합니다.

아래는 이전 실측 이력입니다.
화면 검증을 재개하여 시드 0 참조 녹화와 개발 시드 0/17 기준선 24건을 측정했습니다.
상위 모델 `gemini-3.8-flash`의 시드 0 업무에서 입력·저장·재조회·10개 필드 대조를
완료하고 `awaiting_approval`에 도달했습니다. 전송·승인은 수행하지 않았습니다.
초기 비교에서 경량 모델은 중복 확인 근거 제출을 건너뛰어 입력 전에 중단했습니다.
이후 필수 검증 단계와 제한된 응답 보완에 로그인 동작 분류·미검증 필드 안내를
추가했습니다. 화면 검증을 재개해 두 요약 방식과 기준선의
48건 파일럿을 실행했습니다. 기준선 24건은 DB 저장 12건·전체 흐름 9건 성공이며,
경량 모델은 첫 호출의 503 오류와 비용 미확정으로 1건 중단·23건 실행 차단됐습니다.
최신 코드의 경량 모델 전체 통과와 두 요약 방식 비교는 아직 미확인입니다.
[파일럿 결과](../agent-sawon-bench/evidence/stage2/lite-pilot-20260928/README.md)에
수정 전 실패와 서버 오류를 모두 보존했습니다. 2단계 실험 완료 기준은 남아 있습니다.
9월 29일 전체 작업을 다시 실행한 다섯 건은 모두 저장값 10/10을 확인했고 503은 없었습니다.
저장 후 단계·완료 응답·재검색 선행 조건을 보완해 최종 테스트 152개를 통과했습니다.
사용자의 전경 유지 후 같은 코드로 재실행했지만 최종 조회 단계의 응답 형식 오류
2회로 중단됐습니다. 경량 전체 통과는 아직 미확인입니다.
[최신 재실행 기록](../agent-sawon-bench/evidence/stage2/lite-same-code-20260929/README.md)을 참고합니다.
이후 응답 계약·무효 탐색·입력 전달 기억을 보완했고 전체 **237개 테스트**를 통과했습니다.
추가 GUI 세 건에서는 중복 근거 오류, 저장 후 무효 메뉴, 비밀번호 반복이 각각 발생했습니다.
경량 전체 성공은 아직 미확인입니다. 사용자 요청으로 다음 GUI 실행을 보류하고
[L2C의 구현을 비교해 상태·행동 선택·전이 검증 개선 순서](docs/l2c-lessons-20260929.md)를
정리했습니다. [수정·실측 기록](docs/contract-repair-20260929.md)에 실패와 비용을 보존했습니다.
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
./.venv/Scripts/python.exe -m pip install -e ../agent-sawon-bench/interfaces -e '../agent-sawon-bench[dev]' -e '.[dev,pipeline]'
./.venv/Scripts/python.exe -m pytest
```

`sawon_interfaces`만 editable 의존합니다. 공개 벤치마크에서 비공개 구현을
import하는 의존성은 없습니다. 두 저장소는 로컬 Git과 GitHub 비공개 저장소로 관리합니다.
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

중복 검색 실행 후에는 현재 결과 그리드를 제출하는 필수 검증 단계로 들어갑니다.
이 단계의 응답 스키마는 화면 이동·입력 행동을 허용하지 않으며, 실행기도 같은 조건으로
행동을 차단합니다. 누락되거나 잘못된 근거에는 현재 화면을 다시 관찰해 보완을 요청합니다.
보완 요청도 같은 실패 횟수·단계·비용 한도에 포함됩니다. 정확한 조회값과 같은 조회 창의
현재 결과를 검증한 뒤에만 입력 단계로 전환하며, 전경 이탈·조회값 변경은 즉시 중단합니다.

화면 검증은 공개 저장소에서 새 출력 경로로 실행합니다. 현재 경량 모델의 두
요약 전략은 `config/harness_agents.lite.json`의 `core_lite`와 `core_lite_flat`입니다.
두 설정은 요약 전략만 다르며 기본 상위 모델 설정은 유지합니다.

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
현재 설정은 `gemini-3.8-flash`입니다. 2026-09-28 사용자가 결제 계정을 수정한 뒤
경량 모델이 누락했던 그리드 근거를 동일 요청에서 올바르게 반환했습니다.
조회 재검증 지침과 일반 UIA 행 구조 처리를 보완한 최종 실행은
`20260928T102626Z-4077ad53`이며 시드 0 / stable / 1회 전체 흐름에 성공했습니다.
모델 28회, 행동 25회, 약 145.24초, 추정 비용 $0.1797705, 저장 필드 10/10입니다.
관찰된 창 최대화 컨트롤로 모든 열을 표시한 뒤 같은 조회 행의 값을 검증했습니다.
화면 확인 15초와 캡처 시간은 업무 시간에서 제외하고 별도로 기록했습니다.
여러 관찰의 근거를 누적하는 기능은 도입하지 않았으며, 작은 화면에서 모든 열을
표시할 수 없는 경우는 추가 검증이 필요합니다. 이 1회 결과는 일반 성공률을 뜻하지 않습니다.
같은 코드·시드·설정에서 경량 모델 `gemini-3.5-flash-lite`를 별도 실행한
`20260928T103825Z-e1cd7c7a`는 `write_before_duplicate_check`로 입력 전에 중단했습니다.
모델 9회는 모두 정상 응답했지만 중복 확인 완료 근거 제출 단계를 생략했습니다.
약 21.47초, 추정 비용 $0.0104347, 저장 필드 0/10이며 전경 차단은 없었습니다.
실험 설정은 `config/models.lite.toml`과 `config/harness_agents.lite.json`이고,
기본 모델은 3.8 Flash를 유지합니다. 공개 저장소에서 재현할 수 있습니다.

```powershell
./.venv/Scripts/python.exe -m harness.run --agent core_lite --agent-config ../agent-sawon/config/harness_agents.lite.json --seeds 0 --modes stable --repeats 1 --max-cost-usd 0.5 --inspect-seconds 15
```

이번 경량 실험에는 프롬프트·스키마·검증 가드 변경을 적용하지 않았습니다.
조기 중단한 실행의 시간·비용을 완료 효율과 혼동하지 않습니다.
경량 모델의 근거 누락 및 과거 상위 모델의 API 실패 기록도 보존합니다.
[원인 분석과 실측](docs/model-diagnosis-20260928.md)을 참고합니다.
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
Actions 변수 `BENCH_REPOSITORY`는 `Dreamtreeme/agent-sawon-bench`로 설정합니다.
현재 벤치마크도 비공개이므로 해당 저장소에만 접근하는 읽기 전용 deploy key를 등록하고,
개인키는 구현 저장소의 Actions secret `BENCH_READ_KEY`에 저장합니다.
개인 계정 토큰은 CI에 복사하지 않으며 checkout 이후 인증 정보도 유지하지 않습니다.
로컬 검증에는 원격이 필요 없습니다.
