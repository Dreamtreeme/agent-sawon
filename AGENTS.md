# Repository Guidelines

## Project overview

Agent Sawon은 문서에서 업무 데이터를 추출하고, 사용자 교육과 화면 관찰을 바탕으로
Windows ERP를 조작하는 Python 프로젝트다. 이 저장소는 판단·모델·문서 처리 코어이며,
옆의 `../agent-sawon-bench`가 공개 인터페이스, relay, Mock ERP, 실행 하네스와 채점을 제공한다.

현재 모델·비용·실행 경계는 `docs/current-runtime.md`를 기준으로 한다. 상태 계약과 저장소
인터페이스는 `docs/contracts.md`, 실제 그래프 흐름은 `docs/code-flow.md`를 따른다.
화면 처리 단계의 이름과 코드 대응은 `docs/runtime-terminology.md`를 따른다.
`CODEX_BRIEF.md`는 이 현행 문서들의 짧은 진입점이다. `docs/archive/`의 과거 brief와 날짜별
보고서는 역사·측정 기록이며 현재 설정의 근거로 사용하지 않는다.

## Repository layout

- `brain/run.py`: 업무 실행 CLI. 입력 record, task, relay와 설정을 연결한다.
- `brain/graph/`: LangGraph 작업자, `WorkerState`, 판단·실행·검토와 문맥 구성.
  네 책임 노드의 다음 경로는 상태의 `lifecycle.route`로 결정된다.
- `brain/visual/`, `brain/observe/`: 화면 탐지·OCR 기하, 대상 연결, 관찰과 증거 처리.
- `brain/cache/`, `brain/demo_learning/`: 화면 캐시, 실행 기록 학습, 기억 편찬.
- `models/`: 공급자 어댑터, 공용 호출 계측, 스키마 변환, 단가와 비용 예약.
- `pipeline/`: 문서 렌더링·OCR·추출·검증 및 실행 전 데이터 인계.
- `sawon_runtime/`: 공용 저장소, 시각 계약, 지속 워커와 프로세스 관리.
- `config/`, `examples/`: 실행 설정과 업무 입력 예제.
- `tests/`: pytest 테스트. `cases/`, `data/`, `.tools/`는 로컬 실행 산출물·모델·환경이다.

## Development setup

Python 3.13과 PowerShell을 사용한다. 두 저장소를 같은 부모 폴더에 둔다.
저장소 루트에서 다음 명령으로 가상환경과 editable 의존성을 설치한다.

```powershell
.\scripts\setup.ps1
```

GUI 의존성까지 필요하면 `-Gui`를 전달한다. 이 스크립트는 GPU OCR·탐지 환경이나
모델 가중치를 설치하지 않는다. 실제 화면 실행 전 `config/pipeline.toml`의 `ocr_python`과
`config/brain.visual.toml`의 탐지기 Python·가중치 경로를 확인한다.
시각 환경의 세부 설치는 `brain/visual/README.md`를 참고한다.

## Build and test commands

루트의 `.venv` Python을 명시해 다른 저장소나 시스템 Python과 혼동하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest
```

- 변경 영역을 먼저 확인할 때: `.\.venv\Scripts\python.exe -m pytest tests/test_context_recall.py -q`
- 선택적 병렬 실행: `.\scripts\test-parallel.ps1` (`-n 4`, 기본은 순차 실행).
- core와 bench를 함께 검사할 때: `.\scripts\check.ps1`.
- 기본 pytest는 `gui` 테스트를 제외한다. 실제 Windows 데스크톱이 필요한 테스트에는
  `@pytest.mark.gui`를 붙이고, 실행할 때 `-m gui`를 명시한다.
- CI는 `.github/workflows/check.yml`에 정의되어 있다. Windows/Linux 순차 검사와
  Linux 병렬 검사를 수행하며, bench 체크아웃에 `BENCH_REPOSITORY`와 `BENCH_READ_KEY`가 필요하다.
- 코드 변경 후 lint·format·전체 테스트를 확인한다. 문서만 바꿨다면 링크·경로·명령의 정확성과
  `git diff --check`를 확인한다. 실행하지 못한 검사는 사유를 보고한다.

## Runtime configuration

- `config/brain.visual.toml`: 화면 작업 설정. 현재 visual-only, 모델 기반 의미 판단·검토,
  `guard_mode = "observe_only"`, 편찬된 메서드 사용을 지정한다. 설정을 바꿀 때
  `brain/settings.py`의 기본값과 실제 프로필 값을 구분한다.
- `config/models.toml`: 사용할 공급자·모델·출력 한도. 현재 Anthropic Haiku가 설정되어 있다.
  과거 보고서의 모델명을 근거로 이 설정을 교체하지 않는다.
- `config/pipeline.toml`: 문서 검증과 한국어 GPU OCR 설정.
- `models/pricing.toml`: 비용 계산에 사용하는 단가.

명령 옵션은 아래 진입점에서 확인한다. `--help`는 모델 호출이나 화면 조작을 하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m brain.run --help
.\.venv\Scripts\python.exe -m pipeline.run --help
.\.venv\Scripts\python.exe -m brain.demo_learning.memory_compilation --help
```

화면 작업에는 업무별 `--task`, 입력 record와 실행 중인 relay가 필요하다. 새 실행은 새 case
디렉터리를 사용한다. 문서 추출 결과를 이어 실행할 때는 `--prepared-case`와
`pipeline/handoff.py`의 인계 검증을 사용한다. 실제 ERP 파일럿은 bench 하네스에서 실행한다.

## Code conventions and boundaries

- 타입 힌트와 Pydantic v2를 사용한다. Ruff 기준은 Python 3.13, 줄 길이 100,
  `E`, `F`, `I`, `UP`, `B` 규칙이며 `pyproject.toml`을 기준으로 한다.
- Core가 bench에서 가져오는 계약은 `sawon_interfaces`로 제한한다. 작업자의 환경 접근은
  relay를 사용하고 ERP 내부 DB를 읽어 판단하지 않는다.
- 업무별 버튼명·좌표·탭 순서는 교육 파일과 관찰 데이터로 전달한다. 프로덕션 코드에
  특정 회사나 시드의 경로를 하드코딩하지 않는다.
- 상태는 `WorkerState`, 실행 인계는 `ExecutionRequest`/`ExecutionResult`를 사용한다.
  기존 공용 기능을 재사용하고 서비스에 전체 `Brain`을 넘기는 결합을 추가하지 않는다.
- 계약을 바꿀 때는 `docs/contracts.md`의 소유 파일과 생산자·소비자 목록을 먼저 확인하고,
  계약 코드와 문서를 같은 변경에서 갱신한다.
- 모델 호출은 `models`의 공용 계측 경계를 사용한다. SDK 직접 호출로 비용·지연·실패 기록을
  우회하지 않는다. 공급자별 요청 변환은 어댑터에 둔다.
- 로그는 `RunLog`로 추가하고 상태 파일은 공용 atomic write를 사용한다.
  전달 성공과 업무 완료를 구분하고 실패 실행의 증거를 덮어쓰지 않는다.
- 행동 intent, 키 입력, 컨트롤 role 어휘는 `brain/vocabulary.py`에서만 정의한다.
- 새 어휘 값이 필요하면 `brain/vocabulary.py`에 추가하고, 다른 파일에서 같은 값을 다시
  정의하지 않는다.
- 어휘를 사용하는 파일은 `brain/vocabulary.py`의 정의를 import하며, 이 모듈은 다른
  `brain` 모듈을 import하지 않는다.
- 어휘를 변경하면 `tests/fixtures/vocabulary`의 스키마 fixture와 비교 테스트가 실패하므로,
  변경이 의도된 경우에만 fixture를 갱신한다.

## Testing and evidence

- 일반 테스트는 fake/replay, 임시 경로와 격리된 상태를 사용한다. 실제 API나 데스크톱에 의존하지 않는다.
- 동작 변경에는 구체적인 결함을 잡는 테스트를 추가하거나 기존 테스트를 수정한다.
  같은 검증을 중복 추가하지 말고, 반복 케이스는 실패 입력을 식별할 수 있게 표시한다.
- 화면 동작을 분석할 때 로그와 해당 프레임을 함께 확인한다. 실제 파일럿 보고에는
  모델·교육 파일·콜드/웜 조건·시간·호출·비용·ERP 채점 결과를 남긴다.
- 평가용 seed 5000–5999는 개발이나 테스트에 사용하지 않는다.

## Secrets and change delivery

- 유료 클라이언트는 명시적으로 지정한 `.env`에서 키를 읽는다.
  키 이름은 `CLAUDE_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`다.
- 키, 실제 업무 데이터, 원본 문서, 비공개 화면·로그·캐시는 커밋하지 않는다.
  `.gitignore`에 포함된 실행 산출물을 강제로 추가하지 않는다.
- 커밋에는 해당 작업의 변경만 포함한다. 설명에는 변경 이유, 검증 결과와 남은 한계를 적는다.
- 실행 이력과 장기 계획은 `docs/archive/`, `docs/test-cleanup/` 및
  `../agent-sawon-bench/docs/progress.md`에서 관리한다.
  이 파일에는 날짜별 실험 결과나 일시적인 오류 해결 계획을 누적하지 않는다.
