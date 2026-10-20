# Agent Sawon

Agent Sawon은 문서에서 업무 데이터를 추출하고, 사용자 교육과 현재 화면 관찰을 바탕으로
Windows ERP를 조작하는 Python 프로젝트입니다. 이 저장소는 판단·모델·문서 처리 코어이고,
옆의 `../agent-sawon-bench`는 공개 인터페이스, relay, Mock ERP, 실행 하네스와 채점을
제공합니다.

현재 운영 기준은 [현행 런타임 기준](docs/current-runtime.md) 한 곳에서 관리합니다.
내부 상태와 저장소 사이의 경계는 [계약 지도](docs/contracts.md), 실제 노드 흐름은
[코드 실행 흐름](docs/code-flow.md)을 따릅니다. 날짜가 붙은 분석·파일럿 보고서는
[문서 인덱스](docs/README.md)에서 역사 기록으로 분리합니다.

## 현재 구성

- 주 화면 모델: `config/models.toml`의 Anthropic Claude Haiku 5.5
- 실행 한도: `config/brain.visual.toml`의 건별 `$3`
- 화면 관찰: GPU PaddleOCR + OmniParser v2
- 작업자: `Observation → Decision → Review → Execution` 책임 그래프
- 재사용: 화면 매칭 캐시와 성공한 콜드 실행에서 편찬한 웜 메서드
- 환경 경계: `sawon_interfaces` 계약과 relay HTTP

과거 Luna·Gemini·Jev 어댑터와 가격 정보는 비교 및 재생을 위해 남아 있을 수 있지만,
현재 기본 모델을 뜻하지 않습니다. 프로젝트 전체 누적 비용 상한은 현재 운영 설정에 없으며,
각 실행은 건별 한도를 독립적으로 적용합니다.

## 설치

Python 3.13과 PowerShell을 사용합니다. 두 저장소를 같은 부모 폴더에 둡니다.

```text
workspace/
  agent-sawon/
  agent-sawon-bench/
```

```powershell
.\scripts\setup.ps1
```

GUI 의존성도 설치하려면 `-Gui`를 전달합니다. GPU OCR·탐지 환경과 모델 가중치는
별도로 준비하고, 실행 전에 `config/pipeline.toml`과 `config/brain.visual.toml`의 로컬
경로를 확인합니다.

## 검사

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest
```

두 저장소를 함께 검사하려면 `.\scripts\check.ps1`을 사용합니다. 기본 pytest는 실제
Windows 데스크톱이 필요한 `gui` 테스트를 제외합니다. 테스트 개수는 고정 계약이 아니며,
현재 기준은 명령의 실제 결과입니다.

## 실행

```powershell
.\.venv\Scripts\python.exe -m brain.run --help
.\.venv\Scripts\python.exe -m pipeline.run --help
.\.venv\Scripts\python.exe -m brain.demo_learning.warm_methods --help
```

화면 작업에는 업무별 `--task`, 입력 record, 실행 중인 relay가 필요합니다. 문서 추출 결과를
이어 실행할 때는 `--prepared-case`와 `pipeline/handoff.py`의 인계 검증을 사용합니다.
실제 ERP 파일럿은 bench 하네스에서 실행합니다.

API 키와 원본 업무 데이터, 비공개 화면·로그·캐시는 커밋하지 않습니다. 키는 명시한 `.env`에
보관하며 현재 Anthropic 설정은 `CLAUDE_API_KEY`를 사용합니다.
