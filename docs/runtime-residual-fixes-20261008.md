# 런타임 잔여 문제 4항목 수정

기준 소스 `1582835`, 브랜치 `codex/runtime-residual-cleanup-20261008`.
[원인 조사](runtime-residual-audit-20261008.md)의 A2 → A3 → A1 → B 순서로 적용했다.
새 ERP 실행이나 유료 모델 호출은 하지 않았다. 아래 결과는 로컬 회귀 검증이다.

## 적용 결과

| 항목 | 실제 변경 | 검증 |
|---|---|---|
| 증거 이중 기록 | `verify_screen_fields`의 `_remember_model_task_evidence` 호출 제거. 관찰·새 판단 응답의 쓰기 책임 유지 | 실제 `_observe` 후 배치 검증을 실행하여 claim history와 기록 이벤트가 각 1건임을 확인 |
| 폐기된 탐지기 의존 | `pixel_rectangle`, 중첩 사각형/인접 격자 판별, 관련 프롬프트 제거 | 생산 출처인 OmniParser로 fixture 이관. 일반 입력·cell·row/table 안의 edit/combobox를 검증 |
| Task의 구형 단계 분기 | duplicate/final-query capability, enter 전용 record 재계산, 오래된 query anchor 유지, 불가능한 saved-review/save 후처리, Task 없는 notebook 분기 삭제 | Task/save_result 및 저장 거절 후 복귀, non-screen-matching의 기존 조회 정책 검증 유지 |
| 중복 노드·후보 계약 | private node 6개의 빈 state 인자와 route 번역기 제거. 단일 `ActionCapability` 사용 | 공개 WorkerState 인계·격리, 전체 그래프, 로그인/입력 묶음, 부분 전송 실패·좌표 보완 회귀 통과 |

### 증거 기록

과거 성공 실행에서 확인한 12건은 새 실행의 측정값이 아니다. 동일 proposal을 다시 저장하던
호출 경로를 제거했다. 기존 테스트를 생산 순서대로 바꾸어 **수정 전에는 claim revision이
1→2로 늘어 실패**, 수정 후에는 history/event가 증가하지 않고 통과함을 확인했다.
새 관찰이나 새 모델 응답의 갱신까지 막는 전역 중복 억제 규칙은 추가하지 않았다.

### 입력 표면 판별과 공통 실행

- `input_surface`는 현재 의미 역할과 부모 역할을 사용한다. `cell/dataitem` 또는
  row/table/list 아래의 edit/combobox는 table_cell, 독립 edit/combobox는 direct_input이다.
  상자들이 나란히 있다는 이유로 표 셀로 추정하지 않는다.
- `allowed_input_surfaces`가 교육의 입력 키를 공통 입력 매핑으로 해석한다.
  미지정은 direct_input, table_cell은 명시적 셀 입력, any는 둘 다 허용한다.
- 계획의 surface 검사뿐 아니라 실행 직전 검사, 전달된 입력 재연결, 값 확인,
  오래된 증거 무효화가 같은 규칙을 사용한다. 기존 실행기는 교육에서 허용한 셀도
  `is_record_input`으로 다시 거절했으므로 이 경로까지 수정했다.
- 실제 컴파일 → 가짜 relay 전송 → 새 화면 재관찰 → 배치 검증까지 연결했다.
  허용 셀은 값 전송과 검증에 성공하고, 교육과 다른 표면은 전송 전에 거절된다.
  일부 불일치가 있어도 같은 계획의 정상 입력은 유지한다.
- 의미 역할의 정확도는 화면 모델에 의존한다. 이번 수정은 실제 ERP의 모든 표를
  정확히 인식한다는 실증이나 읽기 전용 셀의 편집 가능성을 보장하는 결과가 아니다.

### 계약 단순화

- private 함수는 `WorkerNode`를 직접 반환한다. observation/decision/execution/review/finish
  외의 구형 observe/select/act/verify 경로명과 `{next: ...}` 포장은 제거했다.
- 전송 성공을 route 문자열로 추정하지 않는다. 완전 전송 시 생기는 pending execution으로
  방법 학습의 delivered/interrupted를 구분한다. blur 재확인도 전송 실패 시 실패 경로를 반환한다.
  중간 전송 실패와 blur relay 실패가 성공처럼 처리되지 않는 회귀 검증을 유지했다.
- `ProcedureStep` 후보 목록·선택·필드명 재변환을 없앴다. 현재 capability의 입력 범위,
  허용 행동, 목표와 완료 조건을 그대로 사용한다. 로그인 경계와 입력 범위 검증은 유지한다.
- 사용자 교육 `TaskInstruction.procedure` 및 기록/응답의 `procedure_step`은 유지한다.
  프롬프트는 `capabilities: [한 개]`에서 `capability: {...}`로 바뀐다. 따라서 이전 프롬프트의
  exact visual-cache key와는 일치하지 않는다. 과거 캐시·방법·로그는 삭제하거나 변환하지 않았다.
- 저장 결과 이후 사용되는 notebook 분기는 남겼다. non-screen-matching의 기존 phase,
  정책·공통 실행기·검증기는 이번 삭제 대상이 아니다.

## 검증과 측정

동일한 로컬 Python 3.13 환경에서 아래 명령으로 전후 각각 한 번 전체 분기 커버리지를 측정했다.

```text
python -m pytest --cov=brain --cov=models --cov=pipeline --cov-branch --cov-report=json:<output>
python -m ruff check .
python -m ruff format --check .
```

| 지표 | 이전 | 이후 |
|---|---:|---:|
| 전체 테스트 | 681 통과 | 677 통과 |
| 전체 실행 시간, coverage 포함 | 102.11초 | 98.44초 |
| 종합 커버리지 | 86.828% | 87.131% |
| statement 커버리지 | 89.504% | 89.723% |
| branch 커버리지 | 79.104% | 79.607% |
| 테스트 AST assert 문 수 | 1,854 | 1,862 |
| 생산 Python 줄 수 | 16,466 | 16,365 |
| 생산 Python 파일 수 | 83 | 83 |

단계별 전체 pytest도 A2 681개/75.51초, A3 674개/75.90초, A1 676개/74.41초로 통과했다.
마지막 WorkerState route assertion 보완은 해당 파일 2개 테스트로 다시 확인했다.
Ruff lint/format 통과. 기존 Windows CRLF를 허용하는 Git whitespace 검사도 통과했다.
시간은 한 번씩의 참고값이며 실행 속도 향상이나 ERP 모델 호출 시간 감소를 입증하지 않는다.
assert 수는 정적인 문장 수이며 실행된 assertion 수나 결함 탐지 점수가 아니다.
변이 테스트는 이번 생산 코드 수정에서 새로 수행하지 않았다.

[측정 요약 JSON](test-cleanup/runtime-residual-20261008.json).
원본 coverage JSON은 로컬 `.tools/runtime-residual-cleanup/`에 보존한다.

## 테스트 정리 근거

| 제거·이관 대상 | 남은 동작 검증 |
|---|---|
| 강제로 Task를 final_query로 바꾼 query_input 경계 테스트 1개 | Task 입력 정규화 테스트, 기존 non-screen 조회 정책·전체 그래프 테스트 |
| 구형 query anchor 유지 조건 3개 | 전달된 입력의 실제 pending descriptor/영역 유지·보완 8개 case로 이관. 현재 값 변경·이름 변경·화면 크기 변경 포함 |
| 구형 saved-review 전체 경로 1개 | 같은 조회→상세→탭 증거를 합치는 기존 generic Task 테스트에 추가 assertion 통합 |
| direct/screen context 비교의 불가능한 legacy phase 3개 | 실제 task case 1개로 대체, save_result 유지. 기존 legacy context 검증은 화면 매칭 호출 없이 계속 확인 |
| 폐기된 grid/nested rectangle 알고리즘 및 단일 사각형 테스트 3개 | 현재 OmniParser 기반 표면 6개 case와 정상 형제 입력 유지, 로컬 geometry만 클릭에 사용되는 기존 테스트 |

위 정리 -10개에 기존 표면 테스트의 parameter 확장 +5개, blur 전송 실패 +1개로 순감소 4개다.
관찰 증거, 표면 권한, 전송·검증, 상태 격리 검증을 통과시키기 위해 assertion을 삭제하지 않았다.

## 커밋 및 남은 실측

- `3293d19`: 관찰 증거의 배치 재저장 제거.
- `5320feb`: Task 경로의 도달 불가능한 업무 분기 정리.
- `372d593`: 현재 시각 의미 기반 표면 판별과 실행·검증 연결.
- `cd8473e`: 노드 경로 및 단일 capability 계약 통합.

OmniParser v2/640, Luna 및 결합·분리 판단 설정은 변경하지 않았다.
직전 v2 파일럿의 이미 열린 탭 재클릭/최종 완료 판단 실패는 이번 변경으로 해결됐다고
주장하지 않는다. 실제 저장·최종 완료와 불필요 행동은 다음 제한된 녹화 파일럿에서 확인할 사항이다.
