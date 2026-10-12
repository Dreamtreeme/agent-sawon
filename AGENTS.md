# Agent Sawon 현재 작업 지침

이 문서는 현재 코드에 적용할 지침만 담는다. 날짜별 과거 지시는
[`docs/archive/AGENTS-20261012-before-rewrite.md`](docs/archive/AGENTS-20261012-before-rewrite.md)에
보존한다. 과거 기록은 기능을 복원하라는 지시가 아니다.

## 1. 범위와 기준 문서

- 현재 구현 범위는 M2 / 3–4단계까지다. 남은 M1 실측과 마일스톤 종료는
  별도 검증 없이 완료 처리하지 않는다.
- 현재 실행 규칙은 `CODEX_BRIEF.md`의 최상단 개정, `docs/README.md`의
  현재 기준 문서, 이 파일 순으로 함께 적용한다.
- 진행 상태와 실측 결과는 sibling 저장소
  `../agent-sawon-bench/docs/progress.md`에 기록한다.

## 2. 현재 런타임

- 화면 작업은 screenshot, 한국어 GPU OCR, OmniParser v2를 사용하는
  visual-first 경로가 기본이다. visual-only 모드에 UIA 관찰과 직접 control pattern을
  섞지 않는다.
- OCR이 제공하는 텍스트·좌표와 OmniParser가 제공하는 시각 영역을 전달하고,
  모델이 현재 화면과 사용자 교육에서 의미와 다음 행동을 판단한다. 탐지기의
  불확실한 설명을 업무 사실로 취급하지 않는다.
- 화면 매칭은 `TaskInstruction`/교육 파일만 진입점으로 사용한다. 삭제한
  `Procedure`, `--procedure`, `procedure_path`, 고정 절차 필터를 복원하지 않는다.
- 작업자 그래프는 `Observation → Decision → Review → Execution`의 책임을
  상태 계약으로 분리한다. 한 실행의 상태는 `WorkerState`가 소유한다.
- Brain은 ERP 내부나 DB를 읽지 않고 relay HTTP로만 관찰·실행한다.
  `ExecutionRequest`/`ExecutionResult`가 전송 계약이며 실행기에 Brain 전체를 넘기지 않는다.
- allowlist ERP 전경 활성화는 relay가 복원할 수 있다. 앱이 렌더링한 비밀번호
  bullet을 유지하며 별도 화이트/블랙 덮개를 추가하지 않는다.

## 3. 모델과 호출 계약

- 기본 화면 작업자는 `config/models.toml`의 `claude-haiku-5-5`다.
  Luna는 계측된 폴백·비교 경로이며 Gemini와 Jev 어댑터도 보존한다.
- Haiku의 현재 알려진 차단 요인은 큰 `ScreenMatch` JSON schema에서 발생하는
  Anthropic structured-output grammar HTTP 400이다. 이 문제는 요청 계약을
  축소·분리해 해결하며 Haiku를 조용히 다른 모델로 대체하지 않는다.
- 모든 모델 호출은 공용 measured `ModelClient`를 통과한다. 호출 목적, decision ID,
  frame, prompt/schema byte, 이미지 개수, 토큰, 비용, 지연을 기록한다.
- 누적 총비용 상한은 사용하지 않는다. 건별 `$3` 상한, 호출 전 예약,
  알려진 비용과 사용량 미반환 비용의 분리 기록은 유지한다.
- 테스트는 replay/fake를 사용하고, 실제 호출 결과를 가짜 응답으로 재분류하지
  않는다.

## 4. 교육, 재사용, 복구

- 회사/시드별 사용자 교육을 독립적으로 적용한다. ERP 라벨, 좌표, AutomationId,
  네비게이션 경로, 탭 순서를 프로덕션 코드에 하드코딩하지 않는다.
- 교육 문맥은 전체 색인과 현재 초점 상세를 나누고, 모델이 필요한 step ID의
  상세를 요청할 수 있게 한다. 뒤 단계를 선택했다는 이유로 앞 단계를 완료
  처리하지 않는다. 삭제한 `MethodRuntime.procedure_prompt_context` 중복 채널을
  복원하지 않는다.
- visual-match cache, 성공 실행에서 편찬한 learned method, 실행 화면 기록만
  재사용 범위다. 구형 UIA 의미 캐시와 observation-delta 시연 학습은 복원하지
  않는다.
- 메서드 승격은 전체 업무 성공 후에만 한다. 웜 메서드 재결합이 실패하면
  현재 화면의 visual cache, 그다음 모델 판단으로 폴백한다.
- 관찰 주장과 행동이 함께 있는 모델 응답은 주장을 버리지 말고 독립 검증 여부를
  표시한다. 종결 응답의 부수 행동은 실행하지 않는다.
- 현재는 거대한 FSM, 단계별 캐시 즉시 승격, 증거 없는 새 의미 휴리스틱을
  추가하지 않는다. 먼저 제한된 콜드·웜 파일럿으로 검증한다.

## 5. 증거와 데이터 경계

- `run_log.jsonl`은 append-only로 유지하고 status 파일은 atomic replace로 갱신한다.
- relay 전달, target binding, 클릭 성공을 업무 성공으로 간주하지 않는다. 저장과
  최종 재조회 결과를 독립 증거로 검증한다.
- evaluation seed 5000–5999는 개발·테스트에 사용하지 않는다.
- API key, 원본 AI Hub 데이터, 실제 업무 정보, 비공개 prompt/cache/screenshot을
  커밋하지 않는다. 키는 `.env`에만 보관하고 로그에 출력하지 않는다.
- 이 비공개 저장소와 prompt/cache를 루틴 작업으로 공개·배포하지 않는다.

## 6. 코드와 검증 기준

- Core는 bench에서 공개한 `sawon_interfaces`만 import할 수 있다.
- Python 3.13, typed code, Pydantic v2, Ruff, pytest를 사용한다. 데스크톱 테스트는
  `gui` marker를 붙인다.
- 테스트가 아닌 코드를 바꾸면 아래 검사를 실행한다.

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

- 실제 GUI 실행은 개발 seed, 회사별 교육, 모델 정체, 캐시 초기 상태,
  건별 상한을 계획에 고정한다. 성공률·시간·행동·관찰·호출 이유·비용·
  미확정 호출·ERP 채점을 함께 보고한다.
