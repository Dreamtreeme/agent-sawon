# 이전 성공044와 현재 실패047 비교

## 결론

실패047의 직접 중단 원인은 P1 `1e5950f`에서 추가한 동일 ID의 ControlMeaning 전체 동등성
검사다. 이전 성공044에는 이 검사가 없었다. grammar400 수정과 분리해서 다뤄야 한다.
다만 실패 응답 전체가 저장되지 않아 실제 충돌이 역할·부모 같은 실행 의미 차이인지,
설명·확신도 차이인지는 아직 특정할 수 없다. 정상 행동이었다는 단정도 하지 않는다.

## 비교 조건

| 조건 | 성공044 | 실패047 |
|---|---|---|
| 시드/교육 | seed23/회사023 교육 | 동일 |
| 실제 입력 record | 합성 선적값 | 필드값 모두 동일, case_id만 다름 |
| ERP 바이너리/relay 설정/variant 해시 | 기준 | 모두 동일 |
| 초기 캐시 | 새 콜드 저장소 | 새 콜드 저장소 |
| 모델 | Luna | Haiku5.5 |
| 행동 target | 정수ID → controls의 단일 정의 | 행동 내부 ControlMeaning + 추가 controls |
| 중복 객체 전체 비교 | 없음 | 다르면 전체 응답 거절 |
| 결과 | 저장·재조회10/10 | 기본4필드 입력 후 저장 전 종료 |

plan.json의 record_sha256은 다르지만 실제 case record를 비교하면 case_id만 다르다.
저장된 task-instruction.json의 SHA-256도 두 실행 모두 `41d6dd…bf7b9`로 같다.
앞선 Haiku 성공041도 seed23/회사023 교육으로10/10이었다. 다만 구 코드에서의 성공이므로
현재 코드로 모델만 바꾼 통제 실험은 아니다. Haiku 자체가 업무를 못한다는 결론은 지지하지 않는다.

## 같은 화면에서 갈린 처리

기본4필드 입력 직후 capture.png를 직접 열어 대조했다. 두 이미지 크기는1182×956이며
RGB 픽셀 차이는0이다. HBL, MBL, 송하인, 수하인이 채워져 있고 아래 품명 입력칸은 가려져 있다.

- 044 seq188 input_review: 응답 수용. seq192에서 target33, key=pagedown,
  point=[1100,600]으로 아래쪽을 보도록 계획하고 품명→추가정보→저장→재조회로 진행했다.
- 047 seq189 input_review: API 응답은 왔으나 `screen_match_conflicting_target_binding`으로
  전체 검토 응답이 사라졌다. 재관찰 seq201에서도 같은 종류의 오류가 발생해 종료했다.
- 047은 그보다 앞선 조회 입력 후 검토 seq75에서도 같은 오류를1회 겪었다.

프롬프트/모델은 서로 다르다. 화면의 동일성이 모델의 내부 판단이나 실패 응답의 행동까지
같았다는 뜻은 아니다. 다만 입력 실패·화면 배치 변화가 이 분기의 직접 원인이라는 설명은 배제된다.

## 새 실패 경로를 만든 코드

`ScreenMatch.collect_action_bindings()`는 처음 controls를 모으고 inline target를 합치면서
같은ID에 대해 `controls[control.id] != control`이면 ValueError를 낸다.
비교 대상은 id/role뿐 아니라 parent_id, label_ids, text_ids, confidence, confidence_policy,
enabled, focused, visual_description, table_state 전체다.

P1은 모델의 대상 재참조 실수를 줄이려 행동 안에 control을 넣었다. 하지만 `controls`에도
같은ID를 쓸 수 있는 표현은 남아 있고 JSON Schema는 두 위치의 값 일치를 보장하지 않는다.
그 결과 **대상 연결 단순화에 객체 전체 일치 요구가 추가되어 새로운 실패 경로가 생겼다.**

로컬 합성 반례를 현재 코드로 실행했다:

| 입력 | 결과 |
|---|---|
| controls의 edit7 + steps.target=7 | 수용 |
| 같은 edit7/좌표/source, inline confidence만0.90→0.95 | 전체 응답 거절 |
| 같은 edit7/좌표/source, inline visual_description만 변경 | 전체 응답 거절 |

이는 과도한 거절이 가능한 코드라는 증명이며, 실제047의 차이가 확신도였다는 증명은 아니다.
당시 회귀 테스트는 role이 다른 객체를 거절하는 사례와 완전히 일치하는 객체만 검증했다.
실행 의미가 같은데 설명만 다른 중복 입력을 다루지 않아 실제 사용 문제를 놓쳤다.

## 왜 재시도하다 종료됐는가

- seq75/189는 입력 후 검토 경로다. `verification_exception`을 `_fail()`로 전달해
  다음 관찰/판단으로 이어졌다.
- seq201은 일반 관찰 경로다. 같은 ValidationError도 `observation_exception`에서
  `_finish("failed", "observation_error:ValidationError")`로 곧바로 종료한다.
- 따라서 단순히 ‘3번 실패 횟수 제한’으로 끝났다고 설명하면 틀리다. 오류가 발생한 그래프
  책임에 따라 복구/종료 처리가 달랐다. 이 비대칭은 기존 코드이고 P1이 새 예외 유입 지점을 만들었다.

## 수정 우선순위

1. 실제 충돌의 ID·서로 다른 필드명·발생 위치만 기록해 실행 의미 충돌인지 확인한다.
   원본 값이나 credential을 로깅할 필요는 없다.
2. action target의 의미 작성자를 하나로 정하고, evidence/parent용 controls의 중복 때문에
   정상 행동 전체를 버리지 않도록 정규화한다. 설명/확신도 차이를 실행 불일치로 취급하지 않는다.
   동일ID에 실제로 상충하는 두 행동 target는 별도로 처리해야 하며 무조건 덮어쓰지 않는다.
3. 정규화 가능한 응답 문제를 일반 관찰의 즉시 종료까지 보내지 않는다. 모든 예외에 무한 재시도를
   추가하는 대신 대상 정규화 경계에서 문제를 해결한다.
4. 무해한 중복·실제 role/target 충돌·복합 증거와 행동을 대표 회귀로 검사하고 같은 seed23으로
   다시 실측한다. 모델 교체나 출력 토큰 증가는 우선 수정 대상이 아니다.

분석만 수행했으며 프로덕션 코드나 기존 실행 증거는 수정하지 않았다.
