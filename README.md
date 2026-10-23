# Agent Sawon

사용자 교육과 문서의 값을 바탕으로 Windows ERP 화면을 조작하는 Python 에이전트입니다.

- 업무 문서를 추출·교차 검증하고 화면 입력에 연결합니다.
- OCR로 문자열을 읽고 OmniParser로 조작 후보 영역을 찾습니다.
- 사용자 교육과 실행 메모를 바탕으로 모델이 다음 행동을 선택합니다.
- 성공 기록을 기억으로 편찬해 유사한 화면에서 재사용합니다.
- 별도 bench에서 Mock ERP의 저장 결과와 작업자 종료를 독립적으로 측정합니다.

**core**는 이 저장소의 판단·모델·문서 처리 코드입니다. **bench**는 시험 환경입니다.
**Mock ERP**는 합성 데이터를 저장하는 시험용 Windows 앱, **relay**는 작업자와 앱 사이에서
화면 관찰·입력 명령을 전달하는 인증 HTTP 중계기입니다.
[화면 처리 용어](docs/runtime-terminology.md)와 [문서 인덱스](docs/README.md)를 참고하세요.

## 한계와 보증 범위

현재 문서 추출과 주요 ERP 실험은 선적 업무입니다. 다른 회사·업무에는 해당 교육과 별도 검증이
필요합니다. 기억 재사용 중에도 모델 판단이 개입할 수 있으며, 모델 없는 재현과 ERP 저장 성공은
별도 결과입니다. 일반 테스트·오프라인 재생은 실제 ERP 전체의 호환성이나 성공률을 보장하지 않습니다.
기록 누락 때문에 전체 재생하지 못한 사례도 있습니다. 범위와 근거는 [재생 보고](docs/test-cleanup/graph-replay-20261023.md)에 있습니다.

## 사전 요구사항

| 항목 | 요구사항 또는 확인된 환경 |
|---|---|
| 저장소 접근 | [core](https://github.com/Dreamtreeme/agent-sawon), [bench](https://github.com/Dreamtreeme/agent-sawon-bench) 접근 권한. 비공개 프로젝트이며 두 폴더를 나란히 배치 |
| OS·데스크톱 | 실제 ERP 실행은 로그인된 Windows 데스크톱. 확인 환경은 Windows 11 Pro. 화면 기준은 1920×1080·100% 배율. 무료 비GUI 테스트는 Windows/Linux CI에서 실행 |
| Python·셸 | Python 3.13 이상, 아래 명령은 PowerShell 기준. 설치 스크립트는 Python 3.13으로 가상환경 생성 |
| Mock ERP 빌드 | .NET 8 SDK, `net8.0-windows` WinForms 앱 |
| GPU·CUDA | 현재 화면 프로필은 NVIDIA GPU 사용. 확인한 탐지 환경은 PyTorch 2.13.0+cu130 / CUDA 13.0, OCR 환경은 PaddlePaddle GPU 3.3.1 / CUDA 13.0. 해당 런타임을 지원하는 드라이버 필요. 이 조합은 확인 환경이며 최소 지원 버전 표가 아님 |
| 탐지·OCR 자산 | OmniParser v2 `icon_detect/model.pt`, 한국어 PaddleOCR PP-OCRv5 모델과 별도 Python 환경. GPU 환경·가중치는 `setup.ps1`이 설치하지 않음 |
| 모델 API | 현재 모델 설정에 접근 가능한 Anthropic 계정과 core 루트 `.env`의 `CLAUDE_API_KEY`. 무료 테스트와 계획 확인에는 유료 호출 없음 |

**OmniParser**는 조작 후보 영역을 찾는 탐지기입니다. **bbox**는 영역을 감싼 사각형 좌표,
**pHash**는 비슷한 화면을 찾기 위한 지각 해시입니다. pHash 일치는 입력값이나 저장 완료의 증거가 아닙니다.

GPU와 가중치 경로는 [시각 경로 안내](brain/visual/README.md)를 참고하세요. 현재 설정에는
개발 PC의 절대 경로가 포함되어 있으므로 아래 설정 섹션의 경로를 실행 전에 맞춰야 합니다.

## 설치와 빠른 시작

### 설치

```powershell
git clone https://github.com/Dreamtreeme/agent-sawon.git
git clone https://github.com/Dreamtreeme/agent-sawon-bench.git
Set-Location agent-sawon
.\scripts\setup.ps1 -Gui
```

`setup.ps1`은 core와 bench, 공개 인터페이스를 core의 `.venv`에 editable 설치합니다.
`-Gui`는 실제 화면 중계에 필요한 bench의 `relay` extra를 설치합니다.
화면 실행이 필요 없는 개발 환경에서는 `-Gui`를 생략할 수 있습니다.

### Mock ERP 한 건 실행

아래 명령은 **core 루트**에서 실행합니다. 먼저 GPU 경로와 `.env`를 준비합니다.
시드 0의 교육 `company-000.json`과 합성 수기 record를 사용하므로 문서 추출은 생략합니다.
이 예제는 기존 로컬 기억·캐시를 사용할 수 있는 단일 실행이며 콜드/웜 비교 실험은 아닙니다.

```powershell
# Mock ERP 빌드: bench에 로컬 SDK가 있으면 사용하고, 없으면 PATH의 dotnet 사용
$dotnetExe = '..\agent-sawon-bench\.tools\dotnet\dotnet.exe'
if (!(Test-Path $dotnetExe)) { $dotnetExe = 'dotnet' }
& $dotnetExe build ..\agent-sawon-bench\mock_erp\MockErp.App\MockErp.App.csproj -c Release
if ($LASTEXITCODE -ne 0) { throw 'Mock ERP build failed' }

# 실행마다 새 결과 폴더를 지정
$runDir = "..\agent-sawon-bench\evidence\runs\quickstart-$([guid]::NewGuid().ToString('N'))"
$pilotArgs = @(
    '--agent', 'core_visual',
    '--agent-config', '../agent-sawon-bench/config/harness_agents.user-taught.json',
    '--seeds', '0', '--modes', 'none', '--observation-mode', 'visual_only',
    '--repeats', '1', '--max-cost-usd', '3', '--output', $runDir
)

# 무료 확인: 단일 실행 계획 JSON 출력. ERP·relay·모델은 시작하지 않음
.\.venv\Scripts\python.exe -m harness.run @pilotArgs --plan-only
if ($LASTEXITCODE -ne 0) { throw 'Plan validation failed' }
```

계획의 `trials`에 `core_visual / seed 0 / none / repeat 1` 한 건이 나타나야 합니다.
화면을 사용할 준비가 되면 다음 명령을 실행합니다. **실제 창을 조작하고 유료 모델을 호출합니다.**
현재 설정의 건별 한도와 이 예제의 전체 한도는 각각 $3입니다.

```powershell
.\.venv\Scripts\python.exe -m harness.run @pilotArgs
Get-Content "$runDir/results.jsonl"
Get-Content "$runDir/summary.json"
```

하네스가 Mock ERP와 relay를 시작하고 로그인 → 조회·입력 → 저장·확인을 수행한 뒤 채점합니다.
성공한 신규 입력의 기대 결과는 `task_success=true`, `erp_task_success=true`, `matched_fields=10`입니다.
개별 `trial_result.json`과 전체 `results.jsonl`, `summary.json`이 생성됩니다. 실패한 경우에도
결과의 `status`·`reason`과 case 로그를 확인하고 기존 폴더를 덮어쓰지 않습니다.
실행 중 다른 앱이 전경을 차지하지 않도록 합니다. 자세한 옵션은 [하네스 안내](../agent-sawon-bench/docs/archive/retired-tree-interface/harness/README.md)에 있습니다.

## 입력과 결과

| 입력 | 역할 | 정의 |
|---|---|---|
| 업무 값 | `ShipmentRecord` 또는 수동 `TaskInputRecord.inputs` | [입력 계약](brain/task_instruction.py) |
| 사용자 교육 `--task` | 목표·식별 키·완료 조건·단계별 설명 | [교육 예제](examples/shipment-user-taught-task.json) |
| 현재 화면 | 텍스트·컨트롤 영역·입력값·메시지 | [시각 경로](brain/visual/README.md) |
| 이번 실행의 메모 | 확인한 값·남은 입력·직전 행동·저장 진행 | [상태 계약](docs/contracts.md) |
| 재사용 기억 | 성공 기록에서 정제한 행동·기하·입력 키 | [기억과 탐색](docs/runtime-terminology.md) |

core case에는 `result.json`과 `run_log.jsonl`이 남으며 성공 시 case 상태는 `AWAITING_APPROVAL`입니다.
행동 전달, 작업자 종료, bench의 ERP 저장 채점은 구분합니다. 문서를 먼저 처리할 때는
`pipeline.run` 또는 `pipeline.workflow`를 사용합니다. [문서→화면 실행 흐름](docs/code-flow.md)을 참고하세요.

## 설정과 환경 변수

| 파일 | 담당 |
|---|---|
| [brain.visual.toml](config/brain.visual.toml) | 화면 경로·예산·기억과 탐지기 Python·가중치 경로 |
| [models.toml](config/models.toml) | 모델 공급자·ID·추론 강도·출력 한도 |
| [pipeline.toml](config/pipeline.toml) | 문서 검증, 한국어 OCR 설정과 `ocr_python` |
| [하네스 설정](../agent-sawon-bench/config/harness_agents.user-taught.json) | 시드별 교육, core CLI, 공유 워커의 `--ocr-python`·`--detector-python` |

현재 기본 모델은 Anthropic Haiku입니다. 정확한 모델 ID·비용·제거된 경로는
[현행 런타임 기준](docs/current-runtime.md)에서 관리합니다. OCR 경로를 바꾸면 하네스 공유 워커의
경로도 맞춥니다. `BrainSettings` 기본값과 위 실행 프로필 값은 구분합니다.

`.env.example`은 현재 제공하지 않습니다. **core 루트의 `.env`**를 직접 만들고 아래 항목에 실제
키를 넣습니다. 기존 파일이 있다면 덮어쓰지 말고 필요한 항목만 추가합니다.

```dotenv
CLAUDE_API_KEY=여기에_실제_키
```

위 하네스는 이 파일을 `--env-path`로 명시합니다. 다른 공급자를 선택할 때만 해당 키가 필요합니다.
`.env`는 Git 제외 대상입니다. 키·업무 문서·원본 화면·비공개 로그·캐시는 커밋하지 않습니다.

## 테스트

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest
.\scripts\check.ps1  # core와 bench 함께 검사
```

일반 테스트는 fake/replay와 격리 상태를 사용하며 `gui` 테스트를 제외합니다.
CI는 Windows/Linux 순차 검사와 Linux 병렬 검사를 수행합니다.
[CI 실행 결과](https://github.com/Dreamtreeme/agent-sawon/actions/workflows/check.yml)와
[workflow 설정](.github/workflows/check.yml)을 확인하세요. 비공개 저장소 접근 권한이 필요합니다.

상세 개수·시간·커버리지는 [정리 보고](docs/test-cleanup/cleanup-final-20261023.md),
이번 로직 대조·회귀 검사 기록은 [README 검토 기록](docs/test-cleanup/readme-runtime-review-20261023.md)에 있습니다.

## 코드 구조와 심화 문서

| 위치 | 책임 |
|---|---|
| `brain/graph/` | Observation·Decision·Review·Execution 책임과 상태 기반 라우팅 |
| `brain/visual/`, `brain/observe/` | OCR·탐지 기하·장면 결합·관찰 값 처리 |
| `brain/cache/`, `brain/demo_learning/` | 화면 캐시·기억 저장·편찬 |
| `models/` | 공급자 어댑터·호출·비용·지연 계측 |
| `pipeline/`, `sawon_runtime/` | 문서 처리·인계, 로그·상태 저장·지속 워커 |

위 책임 이름은 고정 실행 순서를 뜻하지 않습니다. 현재 모델 판단은 보통 Observation 안에서
수행되고 Decision이 실행 계약으로 변환합니다. 실제 그래프는 [코드 흐름](docs/code-flow.md),
상태 소유·결합 규칙은 [계약 지도](docs/contracts.md), 화면 처리 분기는
[용어 문서](docs/runtime-terminology.md)를 기준으로 합니다. 역사 기록은 `docs/archive/`에 있습니다.

## 기여·라이선스·문의

기여 전 [AGENTS.md](AGENTS.md)의 경계·검증·커밋 규칙과 [테스트 유지 기준](docs/test-suite-policy.md)을
읽어 주세요. 별도 브랜치에서 변경하고 관련 회귀 검사와 전체 검사 결과를 PR에 적습니다.
CI를 별도 환경에 설치할 때는 bench 접근용 `BENCH_REPOSITORY`·`BENCH_READ_KEY` 설정이 필요합니다.

현재 저장소 전체에 적용되는 루트 `LICENSE`는 없습니다. [L2C LICENSE](brain/visual/L2C-LICENSE.txt)는
해당 코드의 고지이며 저장소 전체의 라이선스 선언으로 확장하지 않습니다.

별도 내부 메일·메신저 연락처는 저장소에 기재되어 있지 않습니다. 접근 권한·사용 조건 문의는
저장소 소유자 [Dreamtreeme](https://github.com/Dreamtreeme)에게 확인하세요. 코드 변경 논의는
권한이 있는 저장소의 PR에서 진행합니다.
