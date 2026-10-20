# 4-2 구현 및 신규 파일럿 측정 073

## 변경과 검증

bench 구현 커밋 `c565d20`, core 계약 문서 커밋 `193faad`.
core 작업자, 모델 프롬프트, 검증·행동·라우팅·설정은 변경하지 않았다.
bench의 신규 실행 종료 후 집계만 확장했다.

- `harness/reproduction.py`: purpose별·분류별 호출, 캐시 선택, origin별 메서드 중단·전달,
  원본 편찬 그룹의 선택·전달·미전달을 집계한다.
- `metrics.py`: 기존 계측에 execution_trace, summary에 execution_outcomes를 추가한다.
- `runner.py`: ERP 채점·작업자 결과·exit code 이후 결과 필드를 만든다.
- `run.py`: 명시적 cold/warm/unknown 실행 구분을 계획에 보존한다.
- 기존 결과 필드는 유지한다. 결과가 없는 과거 기록은 unknown이며 소급 재계산하지 않는다.
- 화면 캐시 허용·비허용 두 재현값을 모두 보존한다. 공식 정책 하나를 선택하지 않았다.

합성 양성·음성·미확인 로그, 호출 분류, 원본/수정 메서드 구분, 기존 하네스 저장 경로를
검증했다. `python -m pytest`: **603 passed, 6 GUI deselected, 15.08초**.
Ruff lint·format 통과. 기존 Starlette/httpx 폐기 예정 경고 1건.
합성 입력으로 통과한 양성 테스트를 실제 웜 성공 기록으로 취급하지 않는다.

## 실제 실행 조건

- seed 0, ID mode none, visual_only, Haiku 5.5 / low / 8192, 건별 $3.
- 교육: bench `education/development/company-000.json`.
- 교육 SHA256: `68bbb09474a25496fd225e61266deef71e3698c97da3b6cf0475bbb141d0388b`.
- 현재 visual 프로필에서 경로만 새 산출물 디렉터리로 지정했다.
- 콜드 1회: 빈 메서드·캐시, 새 ERP.
- 웜 1회: 이번 콜드의 캐시와 성공한 두 번째 편찬 산출물, 새 ERP.
- private protocol: bench `evidence/local/reproduction-20261020-073/protocol.json`.
  실제 사용 산출물과 편찬 시도는 `warm-source.json`에 별도로 기록했다.

## 결과

| 항목 | 콜드 | 웜 |
|---|---:|---:|
| ERP 채점 | 10/10 | 10/10 |
| 작업자 종료 | success / exit 0 | success / exit 0 |
| 작업자 시간 | 159.357초 | 57.127초 |
| 하네스 시간 | 163.335초 | 60.467초 |
| 원격 모델 호출 | 16 | 3 |
| 로컬 탐지 호출 | 13 | 14 |
| 화면 캐시 선택 | 0 | 1 |
| 메서드 중단 이벤트 | 0 | 0 |
| 원본 편찬 그룹 전달 | 0 | 10 |
| 수정/신규 학습 전달 | 0 / 13 | 2 / 1 |
| 추정 USD | 0.0297473 | 0.0058751 |
| erp_saved / worker_completed | true / true | true / true |
| warm_success | unknown(콜드) | true |
| 캐시 비허용 재현 | unknown(콜드) | false |
| 캐시 허용 재현 | unknown(콜드) | false |

한 쌍에서 작업자 시간은 64.15% 감소했다. 독립 반복 측정이나 다른 시드 성능의 증거는 아니다.
편찬 비용을 제외한 화면 실행 비용이며 편찬까지 합친 총 추정 비용은 아래와 같다.

## 편찬 첫 실패를 포함한 전체 비용

첫 편찬은 `WarmMethodAssembly.purpose`가 **172자**로 160자 제한을 넘어 거절됐다.
2-3 승인 범위에서 제외됐던 필드이며 이번에도 수정하거나 응답을 수동 절단하지 않았다.
첫 실패의 원문·거절 진단·비용을 `compilation/`에 보존했다.

웜 측정용 산출물을 얻기 위해 같은 입력으로 편찬만 한 번 추가 요청했다.
`compilation-retry1/`은 **11그룹 편찬 성공**이다. 첫 실패를 성공으로 덮어쓰지 않았다.
콜드·웜 화면 실행을 반복하여 성공한 시도만 고른 것은 아니다.

| 단계 | API 호출 | 추정 USD |
|---|---:|---:|
| 콜드 | 16 | 0.0297473 |
| 첫 편찬 실패 | 1 | 0.0022310 |
| 편찬 추가 요청 성공 | 1 | 0.0030105 |
| 웜 | 3 | 0.0058751 |
| 전체 | **21** | **0.0408639** |

모든 호출에서 사용량이 반환됐고 비용 미확정 호출은 0건이다. 위 금액은 설정 단가 기반
추정이며 실제 청구서 금액을 조회한 것은 아니다.

## 웜 성공과 재현이 달라진 실제 근거

편찬 산출물은 11그룹, method_bound 이벤트는 12건/고유 그룹 11개,
원본 reuse 전달은 고유 10그룹이다. 반복 결합된 같은 그룹을 2개로 세지 않는다.
나머지 하나는 모델 응답으로 수정된 그룹이 전달되어 원본 전달로 집계하지 않았다.
method_completion_bound는 0건이다. 전체 업무 완료는 실제 ERP 채점과 작업자 종료로 확인했다.

| 웜 seq | 모델 purpose | 지연 |
|---|---|---:|
| 242 | completion_review | 6.454초 |
| 338 | completion_review | 9.331초 |
| 364 | grounded_screen_plan | 9.474초 |

seq 240에서 저장 알림 닫기 원본 그룹 `4cac0325a0b78c80`이 결합됐지만,
seq 242의 phase는 save_result다. `matching.select_match_response`의 기존 save_result
분기는 자동 결합된 제안을 내려놓고 검토 경로로 간다. 뒤의 seq 260은 adaptation 전달이다.
따라서 “중단 이벤트 0 → 원본 경로 전체 재현”으로 해석하면 안 된다.
이번 측정은 이 차이를 false로 드러냈으며 해당 작업자 분기를 수정하지 않았다.
화면 캐시를 허용하더라도 모델 개입 3회와 원본 미전달 그룹 때문에 재현은 false다.

## 화면과 산출물 확인

실제 저장된 캡처를 열어 콜드 로그인, 조회 결과 0건, 추가 정보 탭,
저장 번호 1의 상세 화면을 확인했다. 웜 최종 기본 정보와 추가 정보에서도 저장 번호 1과
입력 내용을 확인했다. 화면 확인과 독립 ERP 10/10 채점이 일치한다.

- cold case: `cases/reproduction-073-000-cold/658adf3c5b85a9dc4f64/case-048fa555d43b429aac3206e392dd94b3/case`
- warm case: `cases/reproduction-073-000-warm/2b16dda1e32ba2bdd8f8/case-f4ce0b37b2014ab7bd779db552849d3a/case`
- bench rows/summary: `evidence/runs/reproduction-073-000-{cold,warm}/`
- private aggregate: bench `evidence/local/reproduction-20261020-073/measurement.json`

신규 trial_result, results.jsonl, summary의 새 값이 서로 일치함을 확인했다.
071·072나 다른 과거 실행 결과에 새 판정을 쓰지 않았다.
화면·원문·메서드·캐시·비공개 로그는 커밋하지 않는다.
