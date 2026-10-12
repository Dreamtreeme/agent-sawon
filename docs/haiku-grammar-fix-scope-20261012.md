# Haiku grammar 오류 분석과 수정 범위

분석 대상: core `4aeacb5`, 실제 실패043/045, 성공044의 저장된 응답17개.
이번 작업은 로컬 분석과 계획이며 런타임 변경·유료 호출은 하지 않았다.

## 확인된 사실과 아직 모르는 것

- 043과045 모두 첫 ScreenMatch에서 `The compiled grammar is too large`로 거절됐다.
  043은 P1 inline target 도입 전이다. P1이 최초 원인이라는 설명은 성립하지 않는다.
- SDK 변환과 required 보정까지 수행한 현재 스키마는 compact JSON 직렬화 기준6,664 bytes,
  최상위21필드, 전체44개 property 정의, 객체6개, 배열12개, nullable union9곳이다.
  `$ref` 정의는 한 번씩 센 값이며 서버의 실제 grammar 상태 수가 아니다.
- required에서 제외된 필드는0이다. 기존 `require_properties` 처리는 적용되고 있다.
- nullable은 ControlMeaning.parent_id, MatchAction.source/keys/point,
  taught_step_id/method_id/grid/row/save_result다. `ControlMeaning`은 controls 항목과
  행동 target의 두 위치에서 참조된다. 중첩이 비용을 늘릴 수 있으나 그 증가량은 알 수 없다.
- 서버는 어떤 필드 조합이 내부 한도를 초과했는지 알려주지 않는다. union9곳이 직접 원인이라는
  단정도 아직 불가능하다. 토큰 상한이나 프롬프트 길이 부족으로 진단할 증거는 없다.

공식 문서는 선택 필드24개·union 매개변수16개 외에도 결합 복잡도에 따른 내부 제한이 있음을
설명한다. 따라서 현재 테스트의 `optional == []`, `len(unions) == 9` 통과는 API 수용 보장이 아니다.
출처: [Anthropic schema complexity limits](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#schema-complexity-limits).

## 필드 소비 경로

| 필드 묶음 | 실제 소비 | 범위 판단 |
|---|---|---|
| steps / inline target / controls / source / point | matching 컴파일, scene 생성, visual_points, executor | 유지. P1 연결과 좌표 보존을 되돌리지 않음 |
| screen_kind / operation / evidence / completion_evidence / grid / row | compile_match, 증거·완료 처리 | 유지. 로그인 표본에서 비었다고 삭제 불가 |
| taught_step_id / requested_instruction_ids | engine 교육 초점, select_match_response 상세 회수 | 유지. P0 교육 기능 |
| method_id / method_bindings / purpose | MethodRuntime.remember, 메서드 선택·슬롯 바인딩 | 유지. compiled_only도 이 소비를 끄는 설정은 아님 |
| save_result / message_targets / repair_fields | sanitize_unused_controls, compile_match, save review | 단계 한정 필드지만 실제 경로 존재. 전역 삭제 금지 |
| needs_image / needs_refinement / uncertainties | 이미지·보완 탐지 요청, scene 불확실성 | 유지. 정상 실행에서 드물어도 폐기 근거 아님 |
| expected_result | Decision.reason, 실행 결과 기대·메서드 목적 보조 | 유지 |
| procedure_step | prepare_match_plan이 task-action으로 덮어씀, 목적 fallback·기존 캐시 계약 | 전송 생략 후보지만 grammar 영향이 작아 1차에서 보류 |

성공044의 `screen-match.json`17개에서 method_id는7개, method_bindings는2개,
save_result/message_targets는1개에 값이 있었다. 모두 정규화 후 저장된 중간 응답이며
전체27회 API 응답이나 최종 완료 응답을 대표하는 표본은 아니다. 미등장을 미사용으로 판정하지 않는다.

## 로컬 스키마 변형 비교

아래는 메모리에서만 만든 설계 후보다. 서버 컴파일이나 모델 품질은 측정하지 않았다.
설명 문구/codec metadata를 추가하기 전 값이므로 최종 전송 크기 목표도 아니다.

| 후보 | bytes | 최상위 필드 | nullable union 정의 | 평가 |
|---|---:|---:|---:|---|
| 현재 | 6,664 | 21 | 9 | 실제400 확인 |
| nullable를 단일 타입의 미지정 값으로 표현 | 6,245 | 21 | 0 | 기능 정보를 유지하며 분기만 줄이는 1차 후보 |
| procedure/method/save 관련6필드 제거 | 5,666 | 15 | 7 | 콜드 일부에는 가능하지만 단계별 투영 필요, 전역 삭제는 기능 손실 |

첫 후보의 의미는 파일 크기6.3% 감소보다 union9→0이다. 이 값으로 서버 수용이나 속도 개선을
예측할 수 없다. 단순히 null alternative를 지우고 null 응답을 금지하면 기능이 바뀌므로 복원이 필요하다.

## 확정할 1차 수정 범위

**Anthropic의 화면 응답 전송·복원 경계에 한정한다. 내부 ScreenMatch/Scene/캐시 실행 의미는 유지한다.**

1. 화면 계약에만 명시적으로 적용되는 codec을 둔다. 전송 시 nullable 문자열은 빈 문자열,
   nullable marker ID는 -1, nullable point는 빈 배열로 미지정을 표현하고, 받은 JSON에서
   해당 경로만 None으로 복원한다. ID0·좌표[0,0]·유효한 source를 미지정으로 오인하지 않는다.
   parent_id의 -1도 실제 marker로 전달하지 않는다. 빈 문자열과 충돌하는 도메인에는 적용하지 않는다.
2. nullable enum에는 미지정 표현을 명시하고 설명과 실제 전송 스키마를 일치시킨다.
   Pydantic 원본 검증 전에 복원한다. 내부 응답·저장 캐시에 특수값을 남기지 않는다.
3. ScreenMatch와 후속 ScreenPlan/SemanticDetection에서 같은 화면 필드 표현을 재사용한다.
   일반 문서 추출·다른 응답 스키마 전체에 자동 적용하지 않는다. Anthropic 어댑터가 brain을
   import하지 않도록 계약의 opt-in metadata와 models의 codec 책임을 구분한다.
4. 실제 전송 스키마의 크기·해시·union 수와 codec 버전을 오류 시에도 기록한다.
   현재 base의 schema_utf8_bytes는 원본 계약 값이므로 대체하지 않고 의미를 구분한다.

예상 프로덕션 변경 파일:

- `brain/graph/matching.py`: 화면 응답 계약의 codec opt-in 정의.
- `models/schema.py`: 한 곳에서 전송 변환·응답 복원, 명시한 경로에만 적용.
- `models/anthropic_client.py`: SDK 전송 직전 변환/수신 직후 복원 연결.
- `models/base.py`: 성공/실패 공통으로 전송 스키마 metadata 기록을 위한 최소 확장.

graph 전이, 캐시 키/승격, 좌표 계산, OCR/탐지, ERP relay, 교육 내용, 모델/출력 토큰 설정은
이 수정 범위에 포함하지 않는다. 전송 필드를 단계별로 삭제하거나 판단 호출을 분할하는 것은
1차 수용 확인 후에만 검토한다. 자동 폴백·자동 유료 재시도는 추가하지 않는다.

## 검증 순서와 종료 기준

1. nullable9곳의 왕복 보존, ID0/point[0,0]/빈 리스트 경계, enum·배열 검증, 원본 스키마 불변성,
   비대상 공급자/문서 계약 불변을 대표 사례로 확인한다. SDK mock은 실제 전송된 스키마를 검사한다.
2. 성공044의 저장 응답을 인코딩→복원해 비교한다. canonical ID 형식과 P1 inline control 응답은
   별도 대표 입력으로 검사한다. 과거 응답 재생을 새 모델 품질의 증거로 쓰지 않는다.
3. 전체 회귀·lint·format 통과 후, 저장된 로그인 프레임으로 계측 API1회 수용 여부를 확인한다.
   수용돼도 control/source/point 보존과 응답 검증을 확인하기 전에는 GUI 성공으로 판정하지 않는다.
4. API 수용 후에만 새 seed23 콜드 파일럿을 실행해 로그인→저장→재조회10/10을 확인한다.
   반복400이면 전체 ERP를 반복 실행하지 않고 다음 후보의 nested schema 축소를 따로 평가한다.

현재는 분석/수정 범위 정의 완료 상태다. codec 효과와 API 호환성은 아직 미검증이다.
