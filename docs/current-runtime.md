# 현행 런타임 기준

기준일: 2026-10-18

이 문서는 Agent Sawon의 현재 운영 기준입니다. `CODEX_BRIEF.md`, 날짜별 보고서, 과거 실행
기록과 내용이 다르면 이 문서와 체크인된 설정·코드를 우선합니다. 모델, 비용, 실행 경계가
바뀌면 구현과 함께 이 문서를 수정합니다.

## 모델과 비용

| 항목 | 현재 값 | 근거 |
|---|---|---|
| 공급자 | Anthropic | `config/models.toml` |
| 화면 모델 | `claude-haiku-5-5` | `config/models.toml` |
| thinking | `low` | `config/models.toml` |
| 최대 출력 | 8192 tokens | `config/models.toml` |
| 요청 제한 | 120초 | `config/models.toml` |
| 실행 비용 상한 | 건별 `$3.00` | `config/brain.visual.toml`, `BrainSettings` |
| 프로젝트 누적 상한 | 없음 | 현재 운영 결정 |

Luna, Gemini, Jev와 replay 어댑터는 비교·재생 코드일 수 있으며 현재 기본 모델이 아닙니다.
과거 보고서의 `$0.50`, `$1.00`, 합산 `$50` 한도는 현재 운영 기준으로 사용하지 않습니다.
사용량을 반환하지 않은 호출은 비용을 0으로 간주하지 않습니다.

## 실행 경로

1. `pipeline`이 문서를 렌더링하고 GPU OCR·추출·교차 검증을 수행합니다.
2. `brain.run`이 `TaskInstruction`, 입력 record, 설정과 relay를 연결합니다.
3. 작업자 그래프가 `WorkerState` 하나를 `Observation`, `Decision`, `Review`, `Execution`
   책임 노드 사이에 전달합니다. 다음 노드는 `lifecycle.route`가 결정합니다.
4. 환경 관찰과 입력은 `sawon_interfaces` 계약을 사용하는 relay HTTP로만 수행합니다.
5. 전체 성공한 콜드 실행만 화면 캐시와 웜 메서드 편찬의 승격 근거가 됩니다.

업무별 버튼명, 좌표, 탭 순서는 프로덕션 코드에 넣지 않습니다. 사용자가 가르친 절차는
`TaskInstruction.procedure`에 참고 지식으로 보존하고, 현재 관찰과 함께 모델이 해석합니다.
구형 `--procedure`/`procedure_path` 진입점은 사용하지 않습니다.

## 화면 관찰과 의미 매핑

- PaddleOCR은 보이는 문자열과 bbox를 만듭니다.
- OmniParser v2는 조작 가능한 후보 bbox를 만듭니다. Florence-2 기능 캡션은 사용하지 않습니다.
- 로컬 의미 탐지 단계는 제공된 후보를 분류하기 위해 실행 중에만 유효한 marker ID를 사용할 수
  있습니다. 이 번호는 화면 요소의 영구 식별자가 아닙니다.
- 업무 판단 단계의 `ScreenMapping`은 스크린샷, OCR 텍스트+bbox, 컨트롤 bbox와 작업 상태를
  받아 `수량`, `포장정보/단위` 같은 보이는 의미 이름, 입력 key, 행동 point를 반환합니다.
  지속 상태와 웜 메서드는 detector 번호를 업무 key로 저장하지 않습니다.
- 성공한 좌표는 같은 viewport와 허용 pHash 거리에서만 웜 메서드로 재결합합니다. 결합에
  실패하면 현재 화면 모델 경로로 돌아갑니다.

전체 화면 픽셀 해시와 캡처 해시는 관찰 메타데이터, 캐시, 안정화 측정에 사용할 수 있습니다.
relay는 같은 허용 창·캡처 크기 안에서 픽셀이 달라졌다는 이유만으로 행동을 거절하지 않습니다.
관찰 만료, 창·DPI·캡처 기하 변화, 허용 창 이탈과 좌표 경계 검사는 유지합니다.

## 현재 사용하지 않는 동작

- UIA 필드 캐시와 UIA 기반 시연 학습
- 고정 선적 상태 기계와 구형 Procedure 로더
- Florence-2 기능 설명을 입력칸 의미로 사용하는 경로
- detector/Set-of-Mark 번호를 지속 업무 key로 저장하는 경로
- 입력 직전 전체 화면 픽셀의 정확한 일치만으로 행동을 거절하는 경로
- 날짜별 실험 프리셋을 운영 설정으로 자동 선택하는 경로

이 목록의 기능을 되살리려면 새 요구사항, 현재 코드 근거, 회귀 검증이 필요합니다. 과거 문서에
남은 설명만으로 복원하지 않습니다.

## 검증 기준

테스트 총개수는 계약이 아닙니다. `677 passed` 같은 과거 수치는 당시 기록으로만 취급합니다.
현재 기준은 저장소 루트에서 실행한 아래 명령의 실제 결과입니다.

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest
```

실제 화면 동작을 바꿨다면 core 테스트만으로 완료를 선언하지 않고 bench의 relay 테스트와
파일럿 화면·로그를 함께 확인합니다.
