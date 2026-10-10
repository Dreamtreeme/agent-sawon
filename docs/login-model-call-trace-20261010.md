# 로그인 모델 호출 입력·출력 추적

대상 실행은 `completion-latch-pilot-20261010-004`다. 로그인 화면에서 Haiku 5.5가 세 번
호출된 이유를 실제 보존 아티팩트와 `run_log.jsonl`로 추적했다.

## 보존 범위

실행 로그는 원문 prompt/response body 전체를 저장하지 않는다. 요청은 SHA-256과 글자 수,
토큰 사용량만 남기고, 구조화된 모델 응답은 `matching/*-markers.json`으로 저장한다. 따라서
아래 입력은 소스 코드의 고정 규칙과 실행 폴더에 남은 동적 JSON·이미지로 복원한 내용이다.
요청 해시까지 동일한 원문 문자열을 다시 제시하는 것은 불가능하다.

세 호출 모두 다음 묶음을 받았다.

1. `TASK_RULES`, 사용자 교육 절차 규칙, 화면 경계 배치 규칙
2. `task-instruction.json`의 전체 사용자 교육
3. 현재 notebook과 사용 가능한 입력 키. 자격증명 값 대신
   `credential.username`, `credential.password`라는 source key를 사용
4. OCR 텍스트와 bbox
5. OmniParser bbox와 OCR bbox를 합친 marker 목록
6. marker 번호가 그려진 현재 화면 이미지
7. 출력 스키마 `ScreenMatch`

프롬프트에는 다음 규칙이 명시돼 있었다.

```text
Distinguish field captions and column headers from the editable areas they name.
Ground an input in its actual visible editable area.
An OCR input marker must enclose that input's own current literal value, never its nearby label.
Every referenced marker MUST also be defined in controls in this same response.
```

즉, 모델에게 OCR 라벨을 입력 표면으로 사용하라고 지시한 것은 아니다.

## 1차 호출: seq 5

| 항목 | 값 |
|---|---|
| 시각 | 05:42:41 UTC |
| request SHA-256 | `de672080afcc3443769716fa47b24f1bc02d671ac69769288377476a61befeaf` |
| prompt 크기 | 21,098자 / 13,475 입력 토큰 |
| 출력 | 336 토큰 |
| 지연 | 3.061초 |

전달된 번호 이미지에서 주요 marker는 다음과 같았다.

| marker | source | bbox | 실제 의미 |
|---:|---|---|---|
| 6 | OCR | `[44,79,430,37]` | 화면 제목 `한결로지스 · 선적 업무 전산` |
| 7 | OmniParser | `[172,130,420,41]` | 계정 입력칸 |
| 8 | OCR | `[34,135,48,32]` | `계정` 라벨 |
| 9 | OmniParser | `[172,176,419,41]` | 비밀번호 입력칸 |
| 10 | OCR | `[37,184,85,25]` | `비밀번호` 라벨 |
| 11 | OmniParser | `[173,222,110,49]` | 로그인 버튼 외곽 |
| 12 | OCR | `[193,233,66,26]` | `로그인` 버튼 글자 |

![1차 호출에 전달된 marker 화면](../cases/completion-latch-pilot-20261010/15c0157495732134b282/case-abf1b268a0be45d4a5cd734770afd560/case/visual/c138d436-d7d0-45d7-ad1c-bafa0ef3233a/markers.png)

모델의 핵심 응답은 다음과 같았다.

```json
{
  "purpose": "Log in with credentials to reach the business menu",
  "screen_kind": "login",
  "steps": [
    {
      "target": 6,
      "action": "fill",
      "source": "credential.username",
      "intent": "login"
    }
  ],
  "controls": [
    {
      "id": 6,
      "role": "edit",
      "label_ids": [8],
      "visual_description": "Username input box"
    }
  ],
  "uncertainties": [
    "Password and login button are not yet grounded as separate steps in this batch"
  ]
}
```

`target=6`은 입력칸 7이 아니라 제목 OCR 박스다. 모델은 `계정` 라벨 8과 연결해야 한다는
의미는 이해했지만, 실제 사각형 ID를 제목인 6으로 선택했다. 런타임은 marker 6의 source가
OCR임을 확인하고 `unwritable_fill_target_deferred`로 거절했다. 행동은 전송되지 않았다.

## 잘못 파생된 로컬 확대

첫 응답이 고른 잘못된 target 6을 “필요한 입력 표면이 누락됐다”고 해석해 로컬 refinement가
실행됐다. 확대 영역은 `[0,0,622,95]`로 로그인 입력칸이 아니라 제목과 창 상단이었다.
이미 marker 7과 9에 입력칸이 있었는데도 잘못된 모델 target을 기준으로 불필요한 확대가
발생한 것이다.

확대 결과는 입력칸을 개선하지 않았고 상단 잡음 박스만 추가했다. marker가 다시 정렬되면서
계정 입력칸은 7에서 8, 비밀번호 입력칸은 9에서 10, 로그인 버튼은 11에서 12로 바뀌었다.

![2·3차 호출에 전달된 marker 화면](../cases/completion-latch-pilot-20261010/15c0157495732134b282/case-abf1b268a0be45d4a5cd734770afd560/case/visual/c138d436-d7d0-45d7-ad1c-bafa0ef3233a/markers-refined.png)

## 2차 호출: seq 11

| 항목 | 값 |
|---|---|
| 시각 | 05:42:45 UTC |
| request SHA-256 | `7f6a04819a55a01d031c1865abb30e77f48deaac05f2eb89db3cdf3e7c4d69fc` |
| prompt 크기 | 21,248자 / 13,551 입력 토큰 |
| 출력 | 781 토큰 |
| 지연 | 3.891초 |

모델은 controls에서는 올바른 표면을 분류했다.

```json
{
  "controls": [
    {"id": 7, "role": "group", "visual_description": "Title banner for the login screen"},
    {"id": 8, "role": "edit", "label_ids": [9], "visual_description": "Account input field"},
    {"id": 10, "role": "edit", "label_ids": [11], "visual_description": "Password input field"},
    {"id": 12, "role": "button", "text_ids": [13], "visual_description": "Login button"},
    {"id": 13, "role": "text", "parent_id": 12, "visual_description": "Login button caption"}
  ]
}
```

하지만 같은 응답의 actions는 다른 ID를 사용했다.

```json
{
  "steps": [
    {"target": 7, "action": "fill", "source": "credential.username"},
    {"target": 10, "action": "fill", "source": "credential.password"},
    {"target": 13, "action": "click"}
  ]
}
```

계정은 올바르게 분류한 8 대신 제목 OCR 7을, 로그인은 버튼 12 대신 버튼 글자 OCR 13을
선택했다. 이는 OCR/OmniParser가 입력칸을 못 찾은 문제가 아니라, 한 모델 응답 내부에서
`controls`와 `steps`의 참조가 불일치한 문제다. 런타임은 계정 fill target 7을 다시 거절했고
로그인 배치가 원자적이므로 안전한 비밀번호 입력까지 포함해 전체 행동을 보류했다.

## 3차 호출: seq 15

| 항목 | 값 |
|---|---|
| 시각 | 05:42:51 UTC |
| request SHA-256 | `4a41b9b080c8944e76c524c78f3eac94a8d44437dea49ef176ee2eb6197d58db` |
| prompt 크기 | 21,689자 / 13,684 입력 토큰 |
| 출력 | 1,305 토큰 |
| 지연 | 6.054초 |

세 번째 요청에는 같은 refined 화면과 함께 다음 교정 정보가 추가됐다.

```json
{
  "error": "required_control_missing_after_refinement",
  "instruction": "Local refinement did not find the required control extent. Do not request the same refinement again and do not relabel OCR text as an input. Classify only grounded CURRENT controls ..."
}
```

이번에는 올바른 detector 표면을 행동에도 사용했다.

```json
{
  "purpose": "로그인 후 House B/L 조회 메뉴로 진행",
  "steps": [
    {"target": 8, "action": "fill", "source": "credential.username", "intent": "credential"},
    {"target": 10, "action": "fill", "source": "credential.password", "intent": "credential"},
    {"target": 12, "action": "click", "intent": "login", "point": [228.0, 246.0]}
  ],
  "controls": [
    {"id": 8, "role": "edit", "label_ids": [9], "visual_description": "계정 입력 칸"},
    {"id": 10, "role": "edit", "label_ids": [11], "visual_description": "비밀번호 입력 칸"},
    {"id": 12, "role": "button", "text_ids": [13], "visual_description": "로그인 버튼"}
  ]
}
```

이 계획이 계정 `[382,150.5]`, 비밀번호 `[381.5,196.5]`, 로그인 `[228,246]`으로 실행됐고
업무 메뉴로 정상 전환됐다.

## 원인 판정

첫 화면의 입력칸 검출은 이미 성공해 있었다. 계정과 비밀번호의 OmniParser bbox가 모두
정확했고 OCR 라벨도 각각 가까운 위치에 있었다. 세 번의 호출을 만든 원인은 다음 순서다.

1. 첫 모델 응답이 올바른 입력칸 7 대신 제목 OCR 6을 선택했다.
2. 런타임이 이를 “입력칸 검출 누락”으로 해석해 잘못된 상단 영역을 확대했다.
3. 두 번째 응답은 controls에서 정답을 찾았지만 steps가 각각 다른 marker를 참조했다.
4. 로컬 validator가 OCR fill을 거절한 뒤 세 번째 교정 호출에서야 controls와 steps가 일치했다.

핵심 설계 문제는 모델에게 한 번에 화면 의미 분류와 업무 행동 계획을 모두 반환하게 하면서,
`steps.target`이 같은 응답의 writable `controls`를 참조하는지 스키마 수준에서 제한하지 못한
것이다. 현재 validator는 잘못된 행동을 막지만, 올바른 detector 표면이 이미 있을 때에도
“검출 누락 → 확대 → 전체 모델 재호출”로 처리해 두 번의 추가 호출을 만든다.

## 적용한 수정

OCR은 계속 `id`, 텍스트, 좌표를 포함한 의미 증거로 모델에 전달하지만 marker 이미지에는
OmniParser 검출 표면만 번호를 표시한다. 각 marker에는 `target_kind`와 `allowed_actions`를
추가했다. OmniParser 표면은 `fill/click/double_click/key`, OCR은 텍스트 자체가 실제로
누를 수 있는 버튼·탭·메뉴·행·셀일 때의 `click/double_click`만 허용한다.

모델이 OCR marker를 `fill` 또는 `key` 대상으로 반환하면 해당 계획 전체를 실행 전에
`screen_match_action_surface_mismatch`로 거절한다. 같은 현재 화면 계약으로 한 번 다시
계획하게 하며, 잘못 고른 OCR 사각형을 확대 탐지하지 않는다. 저장된 로그인 프레임에서
입력 칸 7·9와 로그인 버튼 11만 이미지 번호로 남고 OCR 제목·라벨 번호는 사라지는 것을
확인했다. 전체 로컬 회귀 테스트는 597건이 통과했다.

## 원본 증거

- 첫 응답: `matching/05d6ad00a5954084a415335d031020b7-markers.json`
- 두 번째 응답: `matching/45d3688177b6478ea7cdeb0d50e6fc5b-markers.json`
- 최종 응답: `matching/1f7f93e42a8f4cce9e463a163302915a-markers.json`
- OCR: `visual/c138d436-d7d0-45d7-ad1c-bafa0ef3233a/ocr.json`
- 최초 marker: `visual/c138d436-d7d0-45d7-ad1c-bafa0ef3233a/markers.json`
- 확대 marker: `visual/c138d436-d7d0-45d7-ad1c-bafa0ef3233a/markers-refined.json`
