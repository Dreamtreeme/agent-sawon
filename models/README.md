# 모델 호출과 비용 기록

`ModelClient.complete_json(prompt, schema, images=...)`는 pydantic 결과와 사용량,
USD 비용 추정치, 지연 시간을 반환한다. `GeminiClient`, `OpenAIClient`,
`AnthropicClient`, `JevClient`, `ReplayClient` 모두
필수 실행 기록 작성기를 받아 공개 호출마다 `model_call` 이벤트를 남긴다.
호출부가 기록 래퍼를 빠뜨리는 별도 경로는 제공하지 않는다.

```python
from pathlib import Path
from pydantic import BaseModel
from sawon_runtime.storage import RunLog
from models import create_model_client


class Response(BaseModel):
    value: str


log = RunLog(Path("cases/example"), case_id="example", agent_id="host")
client = create_model_client(Path("config/models.toml"), run_log=log, env_path=Path(".env"))
# 실제 API 호출은 명시적으로 complete_json을 실행할 때만 발생한다.
# result = client.complete_json("Return a synthetic greeting.", Response)
```

비밀값은 명시한 `.env`에서 선택한 공급자의 키만 읽는다(Gemini: `GEMINI_API_KEY`,
OpenAI: `OPENAI_API_KEY`, Anthropic: `CLAUDE_API_KEY`). 부모 폴더 검색, 환경 변수
키 자동 선택, 환경 변수 보간은 하지 않는다. 테스트는 가짜 SDK를 주입하며
실제 API를 호출하지 않는다. SDK의 자동 재시도와 자동 함수 호출을 끄므로
측정 한 건이 숨은 추가 요청을 만들지 않는다.

`models/pricing.toml`은 모델별 텍스트/이미지 입력과 JSON 출력의 추정 가격표다.
사용 모델은 `config/models.toml`에서 지정하며,
가격표에 보존된 과거 모델 항목이 현재 기본 모델을 뜻하지는 않는다.
설정의 확인 날짜·출처를 기준으로 사용하고 실제 계정 청구서나 무료 할당량과 구분한다.
입력에서 캐시 적중 토큰을 분리하고 출력에는 응답과 생각 토큰을 합산한다.
모델 또는 캐시 가격이 없으면 명시적으로 실패한다. 오디오, 도구 호출,
배치, 명시적 캐시 저장은 지원하지 않는다. 가격 변경 시 설정과 확인 날짜를
갱신해야 한다. 결과 JSON 검증에 실패해도 이미 발생한 비용은 기록한다.
타임아웃이나 사용량 메타데이터 누락은 0이 아닌 `null`로 기록한다.
미측정 비용이 있으면 후속 집계에서 전체 비용을 확정값으로 제시하면 안 된다.

실행 기록은 프롬프트·응답 원문, 이미지, 예외 메시지를 담지 않고 요청 해시와
예외 종류를 기록한다. 외부 SDK 예외에 API 키가 포함될 가능성 때문이다.
응답 내용이 필요한 경우 호출자가 별도의 비공개 건 파일로 보관한다.
기록 파일 쓰기 실패는 호출 실패로 전파한다. 프로세스 강제 종료·전원 차단
도중에는 완료 이벤트를 기록할 수 없으므로 해당 실행을 불완전한 측정으로
처리해야 한다.

오프라인 재생은 테스트가 생성하는 합성 fixture로 검증합니다.


이는 모델 계층과 기록 작성기만 확인하는 합성 예제이며 ERP 시연이나 성능
평가가 아니다. Replay JSONL의 각 행은 `ReplayEntry`이며 `request_sha256`은
`models.base.request_fingerprint(prompt, schema, images)`로 만든다.
프롬프트, 스키마, 이미지 내용과 순서를 모두 검사한다. 불일치는 항목을
소비하지 않으며, 소진 시 순환하지 않고 실패한다. 실제 API 토큰/비용은 0,
원래 사용량과 비용은 `recorded_usage`/`recorded_usd`에 별도로 유지한다.
지연 시간은 현재 재생 호출의 실측값이므로 원본 모델의 지연 비교에 쓰지 않는다.

## Claude Haiku 5.5 화면 작업자

Haiku 비교 프로필은 `claude-haiku-5-5`, Messages API structured outputs,
adaptive thinking low, 출력 8192, 요청 제한 120초를 사용한다. 원본 크기 이미지를 base64로 전달하며
Pydantic 스키마는 Anthropic SDK의 `transform_schema`로 지원 형식에 맞춘 뒤 로컬에서
원래 계약을 다시 검증한다. 100K 입력 토큰 초과 시 공식 장문 단가를 적용한다.

## 기본 Luna 화면 작업자

`config/models.toml`은 `gpt-5.6-luna`, Responses API, reasoning low, 출력8192를 사용한다.
화면 작업자는 `brain.run --model-config config/models.toml`로 실행한다.
Gemini 어댑터는 유지하고, 비교 프리셋은 삭제했다.
OpenAI SDK는 자동 재시도0이며 명시적 OpenAI API 주소를 사용한다. 원본 크기의 이미지를
전달하고 Pydantic 스키마를 strict JSON schema로 변환한다. 파싱 전 사용량을 확보하므로
불완전/잘못된 응답도 발생 비용을 기록하며 실행 가능한 행동으로 처리하지 않는다.

`models/pricing.toml`은 [공식 Luna 가격](https://developers.openai.com/api/docs/models/gpt-5.6-luna)을
기록한다. 사용량의 캐시 읽기/쓰기와 추론 토큰을 분리한다. 272K 초과 입력의 단가 배수를
적용하고 호출 전 예약은 긴 문맥·캐시 쓰기 상한 단가로 보수적으로 계산한다.

공식 참고 자료 (2026-09-28 확인):

- [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/):
  `generate_content`, JSON 스키마, inline image, HTTP 재시도 설정.
- [Gemini 토큰 집계](https://ai.google.dev/gemini-api/docs/generate-content/tokens):
  입력, 캐시, 응답, 생각 토큰의 구분.
- [Gemini 요금표](https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-flash):
  표준 유료 가격 설정의 출처.

`models.budget.CallBudget`와 `ImageCallBudget`는 텍스트/이미지 비용 예약을 담당한다.
`models.accounting.read_model_accounting`은 동일 실행 로그의 확인 비용·미확정 여부·호출 수를
합산한다. 두뇌·화면 기록·서류 처리에서 같은 구현을 재사용하며, 모델 계층은 서류/두뇌
구현에 의존하지 않는다. 이미지 비용 예약은 실제 과금표가 아닌 보수적 실행 정책이다.
