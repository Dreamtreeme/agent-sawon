# 반복 클릭과 현재 계획 관계 분류 · 2026-10-24

## 문제와 역사 로그 조사

092에서는 현재 화면 모델이 `새 건 등록`을 이미 선택했지만, 검토기는 직전 `조회` 클릭의
`semantic_target_repeated`를 처리하기 위해 DetailedClick을 한 번 더 호출했다. 좌표 보완의 대상은
끝난 행동이고 현재 계획의 대상은 다음 행동이므로 그 호출은 결과를 바꾸지 못했다.

`cases/**/run_log.jsonl`의 `semantic_target_repeated` 56건을 검색했다. 그중 현재 형식의 선행
`screen_plan_proposed`가 남은 기록은 16건이었다. `open-new-record`처럼 직전 조회와 다른 행동으로
진행하는 기록과 `check-duplicate`처럼 같은 조회 대상을 다시 확인할 수 있는 기록이 함께 있었다.
따라서 반복 판정 전체를 우회하지 않고 현재 계획과 실패 행동의 관계를 구분했다.

## 변경

현재 관찰에 결합된 계획의 첫 행동과 실패한 point action을 비교한다.

- 행동 종류가 point action이 아니거나 click/double-click 종류가 다르면 `alternative`다.
- 둘 다 같은 point action이면 현재 마커를 관찰 요소로 해석하고 `semantic_target_key`를 비교한다.
- 계획이 현재 관찰에 속하지 않거나 마커·과거 의미 대상이 없으면 `unknown`이다.
- `alternative`만 DetailedClick을 생략하고 실패 기록을 남긴 뒤 현재 계획을 Review로 보낸다.
- `same`은 기존 좌표 보완을 유지한다. `unknown`은 보완을 시도하되 실패하면 재관찰한다.

판정은 `verify/repeated_target_current_plan` 이벤트의 `relation`으로 기록한다. 반복 실패 횟수,
피드백, pending 해제와 실행 안전 계약은 완화하지 않았다.

## 합성 회귀와 정적 검증

- 대체 대상: `alternative`, DetailedClick 모델 호출 0회, 현재 계획 보존, Review 이동.
- 같은 대상: `same`, 기존 DetailedClick 호출 및 보정 실행 유지.
- 해석할 수 없는 대상: `unknown`, 기존 DetailedClick 호출 후 재관찰.
- 수정 전 대체 대상 테스트는 Observation으로 이동해 실패했고 수정 후 통과했다.
- 관련 테스트: 165 passed.
- 전체 테스트: 706 passed in 49.14 s.
- Ruff lint/format과 `git diff --check` 통과.
- GitHub Actions run `40002309216`: Linux, Windows, parallel jobs 모두 성공.

제품 커밋은 `7bbd7d5261f72d320e7ea55aeb253c1b58113c97`이다.

## 실제 ERP 093

### 고정 조건

- Core: `7bbd7d5261f72d320e7ea55aeb253c1b58113c97`
- Bench: `5ae931f4edce3be4afa55c3023a6ee97a1b8cdc0`
- Seed / education: `3` / `company-003.json`
- Education SHA-256: `125c304b1e7746ae90ddef3c639b92c8957518d89d6d3d4d7238d0ef1e5154b1`
- Model: Anthropic `claude-haiku-5-5`, low thinking, 8192 output tokens
- Observation / ID mode: `visual_only` / `none`
- Per-case cost limit: `$0.30`; 새 case/cache/method 경로; 재시도 없음

무시된 실행 protocol은
`../agent-sawon-bench/evidence/local/seed3-recheck-093/protocol.json`에 있다.

| Measure | 092 | 093 |
| --- | ---: | ---: |
| ERP matched fields | 10/10 | 10/10 |
| Worker exit | success | success |
| Harness elapsed | 177.44 s | 253.39 s |
| Graph steps | 32 | 41 |
| All model-call events | 31 | 44 |
| Remote intervention calls | 20 | 30 |
| Local detection calls | 10 | 14 |
| Known cost | $0.0365276 | $0.0519764 |

093의 실행 성공은 회귀가 없다는 증거다. 그러나 목표 분기의 실제 증거는 아니다. 093의 조회
결과 0건 화면에서는 `새 건 등록` 계획이 바로 실행되어 `semantic_target_repeated`,
`repeated_target_current_plan`, `click_coordinate_review` 이벤트가 모두 발생하지 않았다. 따라서
한 번뿐인 실제 실행으로 대체 관계 우회가 호출을 줄였다고 주장할 수 없다.

호출 증가의 첫 원인은 seq 22의 로그인 입력이 relay에서
`point_outside_allowed_window`로 거절된 것이다. 이후 재계획, 로그인 실패 팝업과 재로그인이
추가되었다. 두 번째 차이는 저장 뒤 재조회에서 092보다 많은 확인·닫기·행 열기 판단을 거친
것이다. purpose 집계 차이는 local detection +4, screen decision +2, input review +1,
completion review +7로 총 +14이며, 092의 unclassified remote 1회가 093에는 없어 전체 차이는
+13이다. 이 편차는 새 관계 분류 이벤트가 한 번도 실행되지 않은 093에서 발생했으므로 이번
변경의 효과로 귀속할 수 없다.

## 성립하는 결론과 반증 조건

코드와 합성 회귀는 대체·동일·불명 경계를 검증했고 실제 ERP 093은 전체 업무 회귀가 없음을
확인했다. 실제 화면에서의 호출 절감 효과는 미확인이다. 다음에 같은 반복 조건이 자연스럽게
발생했을 때 `relation=alternative` 뒤 DetailedClick이 기록되거나, 현재 계획이 사라지거나, 새 대상
실행 전에 다시 ScreenMatch를 호출하면 구현 가설은 반증된다. 같은 조건이 발생하지 않은 실행을
성공 표본으로 세거나 이를 만들기 위해 유료 실행을 반복하지 않는다.
