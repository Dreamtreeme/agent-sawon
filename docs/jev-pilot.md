# Jev 화면 매칭 파일럿 · 2026-10-01

사용자가 이번 파일럿 도입을 요청했다. Jev를 전체 기본 모델로 확정한 것은 아니다.
사용자가 비공개 .env에 JEV_API_KEY를 설정했다. TYPESAFE_API_KEY 또는 JEV_API_KEY를
명시한 .env에서만 읽는다. 구현 검증과 실제 유료/화면 결과는 별도로 기록한다.

## 연결

`brain.run --matching-model-config config/models.jev.toml`이 선택형 화면 매칭을 활성화한다.
기본 `--model-config config/models.visual.toml`은 Gemini 이미지 보완에 사용한다.
Jev에 임의 ScreenMatch JSON을 생성시키지 않는다. 같은 state에 화면 종류, 제공된 절차,
마커 역할·라벨·부모, 입력 대상, 명령, 현재 검증 근거 질문을 한 HTTP 요청으로 전송한다.
질문 ID는 모델이 읽지 않으므로 의미를 instructions에 명시한다. 다른 질문의 답에 의존하지
않으며 code가 결과를 ScreenMatch로 조립하고 기존 관찰 바인딩·실행·재검증을 적용한다.

- 로그인은 계정·비밀번호 대상과 로그인 클릭을 합쳐 기존 LoginScreenPlan으로 실행한다.
- 입력은 원본 메모지 source를 그대로 사용한다. 자격증명 값은 Jev state에 전달하지 않는다.
- OCR 박스는 텍스트 탭/버튼 후보에 포함하되 입력 칸이나 표 영역으로 승격하지 않는다.
- 이미지 필요 선택, 필수 검증 컨트롤 누락, 클릭 후 화면 무반응 보정은 기존 Gemini를 쓴다.
  정상 Jev 매칭 앞에 별도 Gemini 화면 해석 호출은 없다. 일반 오류 복구/서류 추출/캐시
  재연결까지 Jev로 바꾸지는 않았고 이번 프로필의 캐시는 꺼져 있다.
- 독립 선택의 같은 입력 칸 중복 배정과 입력+이동 혼합은 실행하지 않는다. 기존 제한 내
  재관찰/재시도 경로를 사용하며, 새로운 무제한 복구 루프를 만들지 않는다.
- 저장 결과 단계에도 전체 레코드를 제공해 오류 필드 선택을 가능하게 했다.

## 모델·측정

`models/jev.py`는 공식 POST /v1/systemone에 state/questions/model을 전송한다.
추가 SDK나 암묵 재시도는 없다. 사용량을 먼저 확보한 뒤 응답 스키마를 검사하므로
답변 형식 오류여도 제공된 사용량/비용은 보존한다. 사용량 없는 오류는 기존 미확정 비용
규칙을 따른다. `pricing.jev.toml`은 2026-10-01 공식 가격(입력 $0.042/M, 출력 무료)을
기록한다. 실제 계정 청구액이 아닌 설정 가격 추정이다. Jev/Gemini 합계로 건당 $1,
이전 승인 총 $50를 유지하며 이번 구현에는 유료 호출이 없어 알려진 누적 $4.014263300 유지.

건 폴더 `jev/<요청 ID>/`에 request.json, answers.json, screen-match.json을 기록한다.
모든 실제 호출의 사용량·지연은 기존 run_log.jsonl의 provider별 model_call로 비교한다.
자료는 비공개이며 벤치 공개용 문서로 복사하지 않는다.

현재 선택형 변환은 각 컨트롤에 대표 OCR 라벨 1개, 저장 메시지 근거 1개를 선택한다.
복수 라벨·복수 메시지가 필수인 화면은 추가 검증이 필요하다. Choice당 최대255개 옵션을
로컬에서 검사하고 후보를 몰래 잘라내지 않는다. 큰 화면의 계층 선택은 아직 구현하지 않았다.
한국어 의미 매칭, 여러 질문의 일관성, 컨텍스트 한도와 실제 지연은 API 파일럿으로 확인한다.
저장 화면 8개의 OCR/마커에 합성 메모를 넣은 요청 구성 점검에서는 42–149질문/요청이었다.
이는 42–149회 API 호출이 아니며 요청 바이트 수는 실제 토큰 수가 아니다.

## 실행 준비

키는 저장소의 비공개 `.env`에 직접 설정한다. 채팅·설정 TOML·Git에는 넣지 않는다.
벤치 루트에서 실행할 단일 개발 시드 계획 확인 명령:

```powershell
./.venv/Scripts/python.exe -m harness.run --agent core_jev --agent-config ../agent-sawon/config/harness_agents.jev.json --seeds 0 --modes none --observation-mode visual_only --repeats 1 --timeout 600 --max-cost-usd 1 --output ../agent-sawon/cases/jev-pilot-new --plan-only
```

plan-only는 화면 입력·모델 API를 실행하지 않는다. 실제 실행 때 새 출력 폴더를 지정하고
plan-only를 제거한다. 현재까지 이 새 프로필로 실제 유료/화면 실행은 하지 않았다.
키 확인 후 한 건의 로그인→중복 조회→입력→저장→최종 조회 성공 여부를 먼저 측정한다.
이전 실패 파일럿 대비 호출 수·Jev/Gemini별 비용·전체 시간·실패 위치를 함께 보고한다.

공식 근거: [API](https://docs.typesafe.ai/api),
[Choice](https://docs.typesafe.ai/primitives/choice), [모델/가격](https://docs.typesafe.ai/models).
