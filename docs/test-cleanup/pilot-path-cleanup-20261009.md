# 실제 파일럿 호출 기준 테스트 정리 — 2026-10-09

## 기준과 조사 방법

대상 실행은 성공한 `core-pilot-20261009-refactor-trace-002`이다. 해당 실행의
`case-code-trace.jsonl`에서 core 프로덕션 함수 호출을 추출하고, 같은 함수 본문을 실제로
통과한 pytest context를 clean branch coverage와 대조했다. 단순히 파일럿에서 호출되지
않았다는 이유로 문서 pipeline, 저장 무결성, 비용·비밀·wire 계약, 공급자 비교 경로를
삭제하지 않았다. 이들은 현재 지원 범위이거나 입력 전 안전 경계다.

- 파일럿이 호출한 프로덕션 함수: 228개
- 정리 전 대응 테스트가 없던 함수: 3개
  - `OmniParserDetector._cost`
  - `OmniParserDetector.close`
  - `VisualPerception.close`
- 정리 후 대응 테스트가 없는 함수: 0개
- 80% 이상 신뢰도의 vulture 미사용 프로덕션 심볼: 0개

핵심 그래프 호출은 이미 다수의 실제 테스트 context를 통과했다. 예를 들어
`Brain.run` 64건, `observation_node` 53건, `decision_node` 51건,
`execution_node` 39건, `review_node` 51건이었다. 따라서 “파일럿과 직접 관련 없음”만으로
하위 계약 테스트를 지우는 것은 결함 탐지 공백을 만든다고 판단했다.

## 적용한 정리

1. `ocr_median_kernel: Literal[0, 3, 5]`에 잘못된 값 다섯 개를 넣어 Pydantic 자체 동작만
   되풀이하던 테스트를 삭제했다. 실제 필터 없음, median 3 적용, 이중 OCR view, 좌표 보존,
   크기 불일치 거부 테스트는 남아 있다.
2. `OcrBlock.input_variant` 기본값과 `ExtractedPage` 0.1 기본 보정만 다시 확인하던 구형
   Pydantic 기본값 검증을 삭제했다. 현재 0.2 산출물의 provenance, 원본/필터 이미지 분리,
   OCR source bbox 검증은 그대로 남아 있다. 호환 프로덕션 코드는 이번 범위에서 삭제하지
   않았다.
3. caret 입력 재검사를 위해 같은 18-action 그래프를 다섯 번 실행하던 테스트를 계층에
   맞게 나눴다. 성공과 relay 실패는 전체 그래프로 유지하고, 재검사 비활성·값 불일치·포커스
   잔류는 `Brain._verify_text_input`을 직접 호출하는 한 테스트로 통합했다. 세 실패 사유와
   blur 발생 여부, 반환 observation을 모두 단언한다.
4. 종료 테스트가 `SimpleNamespace.close`만 확인하던 우회를 제거했다. 이제 실제
   `Brain.run → VisualPerception.close → OmniParserDetector.close` 경로가 정상 종료와
   interrupt 양쪽에서 OCR과 detector를 모두 닫는지 확인한다. 로컬 detector의 0달러 비용
   함수도 기존 “유료 fallback 없음” 테스트에 결합했다.
5. 사용하지 않는 test double 인자와 명시적으로 소비하지 않던 fixture 인자를 정리했다.
   pipeline workflow의 provider 차단 fixture는 module 내 autouse로 한 번 선언한다.

프로덕션 모듈은 삭제하지 않았다. 호출 trace, 정적 참조, 현재 지원 범위를 함께 만족하면서
삭제할 수 있는 미사용 프로덕션 심볼이 없었기 때문이다. 특히 pipeline, Gemini/Jev 비교,
저장 무결성, non-screen-matching 정책은 최근 한 번의 Luna 파일럿에 없다는 사실만으로
삭제할 수 없다.

## 전후 비교

| 지표 | 적용 전 | 적용 후 | 변화 |
|---|---:|---:|---:|
| pytest case | 677 | 669 | -8 |
| test 함수 | 434 | 433 | -1 |
| AST assert 문 | 1,858 | 1,861 | +3 |
| 일반 전체 실행 | 중앙값 81.29초 (80.32/81.29/82.61) | 64.54초 (1회) | -16.75초 참고값 |
| coverage 실행 | 104.735초 | 101.206초 | -3.529초 |
| 종합 coverage | 87.6118% | 87.7318% | +0.1200%p |
| statement coverage | 90.2367% | 90.3594% | +0.1227%p |
| branch coverage | 79.6503% | 79.7619% | +0.1116%p |
| 파일럿 호출 중 미검증 함수 | 3 / 228 | 0 / 228 | -3 |

일반 실행 시간은 적용 후 한 번의 값이므로 확정 성능 수치로 보지 않는다. 같은 coverage 계측
조건에서는 3.529초 줄었고, 삭제 기준인 0.5%p 하락 대신 모든 coverage 지표가 올랐다.

## 보류한 묶음

- wire schema의 action 종류별 유효·무효 matrix는 같은 Python 라인을 지나도 각 union branch의
  공급자 계약이 달라 유지한다.
- validation metadata의 공격 문자열 matrix는 위치·메시지·context를 로그에서 제거하는
  보안 계약이라 유지한다.
- storage의 손상, 다중 writer, Windows atomic replace 테스트는 파일럿 함수와 직접 겹치지
  않지만 append-only 증거와 원자적 상태 파일의 운영 계약이라 유지한다.
- 문서 pipeline과 provider adapter 테스트는 최신 ERP 화면 작업 전에 record를 만드는 M2
  경로 및 명시적으로 보존한 비교 profile을 검증하므로 유지한다.

## 검증

- `python -m pytest -q`: 669 passed / 64.54초
- branch coverage 포함 전체 실행: 669 passed / 101.27초
- `python -m ruff check .`: 통과
- `python -m ruff format --check .`: 168 files already formatted
- `git diff --check`: 통과

