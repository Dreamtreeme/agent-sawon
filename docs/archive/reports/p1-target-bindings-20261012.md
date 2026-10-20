# P1 4/2: 행동 대상 연결과 명시 좌표 보존

`project-wide-methodology-review-20261012.md`의 두 번째 우선순위 묶음을 적용했다.
범위는 응답의 대상 연결과 실행 좌표 전달이다. 탐색 억제, 관찰 델타, 모델 교체는 포함하지 않는다.

## 발견한 결함과 변경

1. 결합 판단에서 모델이 `controls`와 `steps[].target` 정수 참조를 별도로 만들었다.
   행동 대상의 의미를 누락하면 `input_evidence_recovery` 보완 호출이 필요할 수 있었다.
   이제 공급자 응답 스키마는 행동의 `target` 안에 `ControlMeaning`을 받는다.
   역할·OCR 라벨 연결·source·point가 한 행동에 함께 들어오고, 입력 경계에서 기존
   `controls + target ID` 실행 형식으로 한 번 정규화한다. `controls`에는 추가 증거/부모만
   쓰도록 안내한다. 같은 ID가 추가 증거에도 있으면 행동의 target을 실행 의미의 기준으로 삼고,
   설명·확신도 등 보조 메타데이터 차이로 행동을 거부하지 않는다.
2. `VisualPointBindings.bind()`가 click/double_click만 처리해 fill/key의 명시 좌표를
   버렸다. 입력 행동도 `fill → set_text` 변환에 맞춰 연결한다. 좌표 없는 입력은 기존처럼
   관찰된 영역을 이용하고, 명시된 소수점 좌표는 실행 인계까지 보존한다.
3. 같은 프레임에서 계획을 교체해도 과거 좌표가 남았고 같은 대상의 서로 다른 입력 source가
   같은 키를 썼다. 계획마다 바인딩을 교체하고 source를 키에 포함했다.

## 공용 경로와 호환성

```text
모델 응답: steps[{target: {id, role, label_ids, ...}, source, point, action}]
    → ScreenMatch 입력 정규화
    → 기존 controls + steps[target ID, source, point]
    → 현재 SceneObservation에 좌표 바인딩
    → 기존 실행 compiler → ActionWiringRequest → relay 요청
```

- 관찰은 bbox와 프레임을, 응답은 의미와 명시 좌표를 제공한다. 기존 SceneObservation과
  실행 계약을 재사용하며 별도 target 저장소나 모델 호출을 만들지 않았다.
- 캐시/메서드 파일은 기존 정수 target 형태로 저장하고 읽는다. 이미 의미가 정해진 장면의
  `ScreenPlan`도 기존 ID 참조를 유지한다. 모델의 콜드 응답만 inline control 형식을 요청한다.
- 공급자 스키마에 컬렉션 상한을 다시 넣지 않도록 공통 스키마 축약을 적용한다.
- 새 프레임 재결합·pHash·좌표 보완은 기존 공용 경로를 따른다. 자동 중심 스냅은 추가하지 않았다.

## 검증과 한계

- 전체 비GUI 테스트 633개 통과(47.03초). Ruff check/format, `git diff --check` 통과.
- 대상 영역 120개 테스트 통과. 검출 입력칸과 모델 생성 입력칸 각각에서 inline control이
  실행 대상에 연결되고 `[175.25, 30.75]`가 relay 요청까지 유지된다. fake 모델 호출은 각 1회다.
- 입력 응답 불변성, 기존 캐시 직렬화 왕복, 공급자 스키마, 중복 ID 정규화, fill/key 좌표,
  동일 프레임 계획 교체와 source/프레임 격리를 검사한다. 기존의 ‘입력 좌표 무시’ 테스트는 교체했다.
- 실제 GUI/API는 이번 변경에서 실행하지 않았다. Haiku의 기존 grammar HTTP400 해결이나
  업무 성공률·호출 비용 개선을 이 회귀 검사로 주장하지 않는다.
- 잘못된 의미 선택 자체를 교정하는 변경은 아니다. 같은 대상·행동·intent·source가 완전히
  동일한 반복 행동의 서로 다른 좌표를 구별하는 실행 식별자는 별도 검토가 필요하다.
  콜드/웜 실제 좌표 재결합 비교와 좁은 탭·빈칸·DPI 변화 실험은 후속 실측으로 남긴다.

## 후속 실제 실행 045

구현 검증 이후 사용자 요청으로 Haiku 5.5/seed23/새 콜드 저장소를 실제 실행했다.
로그인 화면·14개 마커는 확보했지만 첫 ScreenMatch API가 HTTP400
`The compiled grammar is too large`로 거절됐다. 행동0·정답필드0/10, harness13.224초,
API사용량 미반환1건으로 비용은 미확정이다. 따라서 위 좌표 전달의 실제 GUI 검증은 여전히
미완료다. 모델 변경·유료 재시도 없이 결과를 보존했다.
상세: bench `docs/p1-target-pilot-20261012.md`.
