# 실행 설정

운영 설정은 아래 3개만 유지합니다. API 키는 설정에 넣지 않고 비공개 `.env`에 저장합니다.

| 파일 | 역할 |
|---|---|
| `brain.visual.toml` | 화면 인식·판단·실행 한도와 화면 캐시 |
| `models.toml` | 주 모델 Luna, 호출 한도, 단가표 경로 |
| `pipeline.toml` | 화면/서류 OCR 및 문서 검증; 한국어 GPU OCR 기본 |

실행 예시(변수는 실제 입력 경로·relay 주소·업무 설명으로 지정):

```powershell
./.venv/Scripts/python.exe -m brain.run --record $RecordPath --case-dir $NewCasePath --relay-url $RelayUrl --goal $Goal --task $TaskPath --config config/brain.visual.toml --model-config config/models.toml --visual-ocr-config config/pipeline.toml --env-path .env
```

업무별 교육은 `--task`로 전달합니다. 비주얼 설정의 예제 task_path를 다른 회사의 교육으로
대신 사용하지 마세요. `methods`는 기본 비활성이며 사용 시 해당 업무의 검토된 라이브러리
경로를 설정합니다. 화면 캐시 경로도 재사용할 업무 범위에 맞춰 지정합니다.

OCR 언어·팩스 전처리는 pipeline.toml의 필드를 수정하며 별도 프리셋을 만들지 않습니다.
OmniParser 가중치와 OCR Python 경로는 로컬 설치에 맞춰 수정합니다.

`models/pricing.toml`은 비용 측정용 단가 데이터이며 실행 프리셋이 아닙니다.
기존 단가와 확인 날짜를 그대로 옮겼으며 이번 정리에서 가격을 재검증하지 않았습니다.
Gemini/replay 어댑터는 유지하지만 비교용 운영 프리셋은 제공하지 않습니다.
테스트의 비교 설정은 임시 경로에서 생성합니다.

과거 하네스·M1/M2·프롬프트 비교 프리셋은 삭제했습니다. 이전 기록은 Git 이력과
기존 실행 폴더에 보존하며, 필요 시 개별 실험의 작업 폴더에서 조건을 명시합니다.

2026-10-08에 소비자가 없던 `hybrid_assist_focus`, `hybrid_assist_ime`,
`hybrid_assist_scroll`, `jev_request_max_bytes` 설정을 제거했습니다.
과거 설정을 가져올 때 이 키들은 제거해야 합니다. 알 수 없는 키를 무시하는 호환 처리는
추가하지 않았으며, 실제 relay의 전경 복원·IME·스크롤 동작은 변경하지 않았습니다.

화면 매칭은 `--task` 또는 `task_path`를 요구합니다. 구형 `--procedure`와 `procedure_path`는
제거했습니다. 과거 고정 Procedure JSON은 직접 로드할 수 없으며 사용자 교육을
TaskInstruction 형식으로 옮겨야 합니다. TaskInstruction.procedure의 교육 순서는 유지됩니다.
