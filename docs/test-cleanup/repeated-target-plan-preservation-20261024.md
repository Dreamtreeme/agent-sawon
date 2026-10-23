# 반복 대상 판정 뒤 현재 계획 보존 · 2026-10-24

## 확인한 결함

091 원문에서 직전 조회 클릭의 결과를 관찰하는 동안 현재 화면의 다음 계획이 이미 만들어졌다.

- seq 147: 조회 결과 0건을 근거로 `새 건 등록` 버튼 `[271, 729]`을 누르는 계획
- seq 152: 직전 조회 클릭에 `semantic_target_repeated` 기록
- seq 155~166: 같은 화면을 다시 관찰하고 `screen_decision`을 다시 호출
- seq 166: 같은 `새 건 등록` 행동을 `[269, 728]`로 다시 계획

`_verify_node`는 navigation no-progress/cycle 결과에만 현재 관찰의 계획을 보존했고,
`_verify_repeated_target` 결과는 그대로 Observation으로 보냈다. 따라서 이미 유효한 현재 계획을
버리고 같은 화면을 다시 판단했다. 반복 대상 판정 자체와 실패 횟수 기록은 잘못이 아니며 유지했다.

## 수정

현재 관찰에서 만들어진 계획이 다음 네 조건을 모두 만족하는지 확인하는 공통 함수를 사용한다.

1. 계획이 존재한다.
2. 계획의 observation ID가 현재 관찰과 같다.
3. 지연된 plan error가 없다.
4. 추가 로컬 탐지가 필요한 계획이 아니다.

navigation 실패와 semantic-target 반복 실패 모두 이 조건을 만족하면 Observation 대신 Review로
보낸다. 실패 피드백, 반복 횟수, pending 해제와 클릭 상세 보완 정책은 변경하지 않았다.

## 검증

- 새 합성 회귀는 수정 전 `observation`을 반환해 실패했고 수정 후 `review`를 반환했다.
  현재 계획 객체 보존, pending 해제, 반복 실패 기록, 추가 모델 호출 0회를 함께 단언한다.
- 관련 테스트 53 passed, 전체 core 704 passed (45.05초).
- Ruff lint/format 및 `git diff --check` 통과.
- 091 원문 오프라인 분기 재생은 기존 7번째 화면 모델 호출을 건너뛰고 seq 147 계획을
  `combined_screen_match_reused`로 컴파일했다. 새 행동 `[271,729]`은 재판단 뒤 기록된 기존 행동
  `[269,728]`과 달라 strict replay가 그 경계에서 중단했다. 이는 새 경로가 원본과 달라졌다는
  확인이며, 이후 전체 성공을 재생한 결과가 아니다.

실제 ERP와 유료 모델은 실행하지 않았다. 새 좌표가 실제 화면에서 성공하는지는 별도 화면 실행의
반증 조건이다. 현재 계획이 다른 observation에 속하거나 plan error/needs_refinement가 있으면 기존
Observation 경로를 유지해야 한다.
