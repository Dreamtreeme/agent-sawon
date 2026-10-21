# 현재 시각 경로

기준일: 2026-10-18. 현재 운영 기준은 [`docs/current-runtime.md`](../../docs/current-runtime.md),
그래프와 공개 계약은 [`docs/contracts.md`](../../docs/contracts.md)를 따릅니다. 이전 시각 경로의
상세 실험 기록은 [`docs/archive/reports/brain-visual-readme-20261018.md`](../../docs/archive/reports/brain-visual-readme-20261018.md)에 보존합니다.

## 책임 분리

| 단계 | 입력 | 출력 | 식별자 수명 |
|---|---|---|---|
| PaddleOCR | 현재 캡처 | 문자열과 원본 이미지 bbox | 현재 관찰 |
| OmniParser v2 | 현재 캡처 | 조작 후보 bbox | 현재 관찰 |
| 로컬 의미 탐지 | 스크린샷, OCR, 후보 marker | role·label 관계를 가진 `Detection` | marker ID는 현재 catalog 안에서만 유효 |
| 업무 화면 매핑 | 사용자 교육, notebook, 스크린샷, text/control bbox | 의미 이름, 입력 key, 행동 point의 `ScreenMapping` | 의미 이름은 현재 화면 계약, point는 현재 캡처 |
| 편찬된 기억 | 성공 trace, OCR anchor, pHash | 현재 화면에 재결합한 행동 | 교육 digest와 화면 유사도 범위 |

캡처 직후 기존 viewport·pHash 조건으로 기억을 검색합니다. 일치하면 저장한 필드 bbox를
인출하여 OmniParser를 생략하고, 미일치하면 OmniParser로 bbox를 찾습니다. 현재 문자열은
두 경로 모두 OCR로 읽습니다. 바이트까지 동일한 관찰의 기존 catalog 재사용은 유지합니다.
로컬 의미 탐지는 주어진 후보를 분류하며, 업무 모델의 ScreenMapping은 의미 이름과 bbox를
선택합니다. 기억이 불충분하면 같은 캡처·OCR에서 탐지 경로로 돌아갑니다.
Florence-2의 기능 캡션은 빈 입력칸을 `Maximize`, `Center` 등으로 잘못 설명했으므로 현재
런타임에서 사용하지 않습니다.

## marker 번호와 의미 이름

두 모델 경계를 구분합니다.

1. `brain/visual/scanning.py`의 로컬 의미 탐지는 이미 검출된 후보를 분류하기 위해 현재
   catalog의 marker ID를 사용할 수 있습니다. 이 번호는 후보 bbox를 가리키는 임시 handle입니다.
2. `brain/graph/screen_mapping.py`의 업무 판단은 OCR text+bbox와 detector control bbox를 받아
   `수량`, `포장정보/단위` 같은 의미 이름을 만듭니다. `ScreenMapping`의 field, assignment,
   step과 evidence는 detector 번호를 업무 key로 사용하지 않습니다.

따라서 OCR/OmniParser 번호가 다음 캡처에서 바뀌어도 notebook과 업무 입력의 의미 key가 번호에
묶이지 않습니다. 기억 재사용은 성공 좌표를 viewport와 pHash로 검증해 재사용하고, 재결합 실패 시
현재 화면 모델 경로로 돌아갑니다.

## 행동 point와 안전 경계

업무 모델은 선언한 의미 field의 bbox 안에서 원본 이미지 point를 선택합니다. 실행 전에 현재
캡처 좌표로 변환하고 허용 창, 캡처 크기, DPI, point 경계를 확인합니다. 관찰 만료나 창·기하
변화는 행동을 중단시킵니다.

전체 픽셀 해시와 캡처 hash는 관찰 메타데이터, 동일 화면 catalog, pHash 비교와 안정화 측정에
남습니다. 같은 허용 창과 같은 캡처 기하에서 픽셀이 달라졌다는 사실만으로 행동을 거절하지
않습니다. 입력·caret·animation 같은 정상 변화가 정확한 픽셀 일치를 깨기 때문입니다.

## 현재 설정

`config/brain.visual.toml`의 현재 프로필은 다음을 사용합니다.

- `visual_screen_fill = true`
- `visual_model_led_semantics = true`
- `visual_model_only_review = true`
- `screen_matching = true`
- `guard_mode = "observe_only"`
- OmniParser v2 `cuda:0`, image size 640
- visual cache와 compiled memory 활성화
- method pHash 최대 거리 6

OCR Python과 detector 가중치 경로는 로컬 설치값이므로 실행 전에 확인합니다. 비밀번호 영역을
추가로 흰색이나 검은색으로 덮지 않으며 ERP 자체의 마스킹 표시를 사용합니다. 비밀값은 화면
모델에 보내지 않고 실행기가 credential source에서 공급합니다.

## 검증

일반 테스트는 fake/replay와 합성 화면을 사용합니다. 실제 화면 문제를 분석할 때는 로그만 보지
않고 같은 observation의 원본 캡처, OCR, detector bbox, `ScreenMapping`, 전달 행동과 후속 캡처를
한 흐름으로 확인합니다. 실제 파일럿의 화면·프롬프트·응답은 무시되는 case/evidence 경로에 두고
비밀이나 원본 업무 데이터를 커밋하지 않습니다.
