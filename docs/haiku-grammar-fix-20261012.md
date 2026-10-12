# Haiku 화면 응답 grammar 수정

## 변경

- `ScreenPlan`, `ScreenMatch`, `SemanticDetection`이 변환할 nullable 경로만 class metadata로
  명시한다. 일반 JSON Schema와 캐시 계약에는 이 설정이 노출되지 않는다.
- `NullableWireCodec`은 Anthropic 전송 시 문자열/ID/좌표의 미지정을 각각 빈 문자열/-1/빈 배열로
  표현하고 응답을 원래 None으로 복원한다. 공용 어댑터가 brain 모듈을 import하지 않는다.
- source, keys, point, parent_id, taught_step_id, method_id, grid, row, save_result의 union을
  제거했다. ID0, 좌표[0,0], 실제 source와 키는 유지하고 기존 Pydantic 검증을 적용한다.
- 실제 전송 스키마의 SHA-256·bytes·union 수·codec 버전을 성공/실패 모두 기록한다.
  기존 schema_utf8_bytes는 원본 계약 측정값으로 유지한다.

## 오프라인 검증

- 전체638개 테스트 통과(47.79초), Ruff check/format 통과.
- 성공044의 저장된 중간 응답17개를 변환 후 복원해 원본 ScreenMatch와 동일함을 확인했다.
- SDK 전달 스키마, inline target 복원, source/ID0/좌표 보존, 잘못된 키의 원본 검증,
  API400 실패 기록, 다음 호출로 metadata가 섞이지 않는지, 비대상 nullable 계약 보존을 검사했다.
- 전송 metadata의 union 집계 추가 후 관련43개 테스트를 다시 통과했다(2.59초).

## 실제 API probe 046

실행045의 저장된 합성 로그인 이미지를 대상으로 실제 Haiku 5.5를1회 호출했다.
SDK 자동 재시도는 없고 실제 화면 입력은 하지 않았다. 출력 상한8192/effort low는 유지했다.

- API 수용·원본 응답 검증 성공: 9.873초, $0.0010402, 입력4462/출력1188토큰.
- 전송 스키마6,692 bytes, union0, codec `nullable-sentinels-v1`.
  이전6,664 bytes보다 작아진 것이 아니라 분기 구조를 단순화한 결과다.
- 계정(마커7, point380/148), 비밀번호(9,380/195), 로그인(11,225/245)의3개 행동이 반환됐다.
  두 credential source가 정확히 보존되고 click.source는 내부None으로 복원됐다.
- 이는 새 스키마 수용을 검증한 고정 계획 probe이며, 전체 업무 수행 품질의 증거는 아니다.
- 로컬 증거: `cases/haiku-grammar-probe-20261012-046`.

## 실제 GUI 파일럿047

- core `1b70af6`, Haiku5.5, seed23 전용 교육, 빈 캐시로 실행했다.
- Haiku12회 모두 API 응답·사용량을 받았으며 HTTP400은0건이다.
  ScreenMatch9회, ScreenPlan2회, SemanticDetection1회가 실제 새 표현을 사용했다.
- 그중9회는 로컬 검증도 통과했고3회는 `screen_match_conflicting_target_binding`에서 실패했다.
  이는 P1의 inline target와 추가 controls에 같은 ID의 서로 다른 ControlMeaning이 생길 때
  거절하는 별도 검증이다. 이번 codec은 이 비교나 모델의 의미 선택을 수정하지 않았다.
- 화면에서 로그인→조회→신규 등록과 기본 정보4개 입력을 확인했다. 로그인 입력2회,
  조회 입력1회, 기본 정보 입력4회의 명시 좌표가 저장된 계획과 실제 relay 요청에서 일치했다.
- 115.955초, relay 행동12회, 비용$0.0285716, 미확정0. 마지막 관찰의 로컬 검증 오류로
  저장 전 종료되어 ERP 채점0/10이다. 전체 업무 성공으로 보고하지 않는다.
- probe 포함 신규 알려진 비용은$0.0296118. 앞선045의 미확정 비용은 이 수치에 포함하지 않는다.
- 상세는 bench `docs/p1-grammar-pilot-20261012.md`를 따른다. 실패 표본을 보존하고 임의의
  추가 유료 재실행이나 모델 변경은 하지 않았다.
