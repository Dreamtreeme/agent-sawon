# L2 전체 그래프 시간·격리 감사

측정일: 2026-10-05
브랜치: `cleanup/tests-20261005`

## 범위와 측정 방법

요청 시점의 224개 L2 case 중 query/editor 재선택 두 case가 직전 정리 단위에서 하나의
통합 workflow로 합쳐져, 이번 변경 직전 수집 결과는 **223개**였다. 이 223개와
변경 직전 JUnit 시간이 가장 긴 **15개 case**를 모두 측정했다. 두 집합은 11개가 겹친다.
개별 결과는 [graph-timing.csv](graph-timing.csv)에 기록했다. 고비용 파일 안의 비대상 case도
fixture 효과를 왜곡 없이 비교하기 위해 측정했으며 CSV의 `target_group`으로 구분했다.

임시 pytest 계측기는 측정에만 사용하고 저장소에는 남기지 않았다. 계측 지점은 다음과 같다.

- `graph_build_ms`: `build_worker_graph` 호출 시간. 실제 그래프 생성 시간이다.
- `graph_invoke_ms`: 컴파일된 그래프의 `invoke` 시간이다.
- `runtime_validation_ms`: `Brain._verify_node`와 `Brain._review_node`의 합이다. 이 값은
  `graph_invoke_ms`에 포함되므로 두 값을 더하지 않는다.
- `test_side_ms`: pytest setup+call에서 `Brain.run`을 뺀 값이다. fixture 준비와 테스트 코드의
  assertion 실행을 포함하는 근사치다.
- `before/after_setup_ms`, `before/after_call_ms`: pytest가 case별로 기록한 wall time이다.

fixture scope에 따른 시간 귀속을 확인할 수 있도록 setup과 call을 분리해서 보존했다. 세션
fixture를 처음 요구한 case에 한 번의 cold 준비 시간이 setup으로 귀속되므로 개별 after 행의
순위보다 집합 합계와 직접 대상 파일 합계를 주 비교값으로 사용했다.

## 측정 결과

| 집합 | case | 적용 전 setup+call | 적용 후 setup+call | 변화 | 생성 전→후 | 실행 전→후 | 런타임 검증 전→후 |
|---|---:|---:|---:|---:|---:|---:|---:|
| L2 전체 그래프 | 223 | 109.061초 | 99.260초 | -9.801초 (-8.99%) | 0.109→0.140초 | 83.668→75.016초 | 28.642→24.804초 |
| 고비용 15 | 15 | 73.244초 | 63.606초 | -9.638초 (-13.16%) | 0.043→0.044초 | 70.305→61.051초 | 22.906→19.494초 |
| 전체 계측 대상 | 296 | 129.800초 | 117.804초 | -11.996초 (-9.24%) | 0.117→0.148초 | 98.307→88.054초 | 33.304→28.918초 |

그래프 생성 호출은 71회에서 67회로 줄었다. 생성 합계가 오히려 0.031초 늘어난 것은
호출당 1~2ms 구간의 실행 잡음이다. 생성 자체를 모두 공유해도 기준선상 가능한 절감은
약 0.12초뿐이다. 실제 비용은 그래프 실행과 그 안의 모델 fake, relay, 상태 저장, 검토에
있었다.

고비용 15개 각각의 생성·검증·테스트 측 시간은 CSV에 모두 있다. 사람이 빠르게 확인할 수
있도록 적용 전 핵심 값을 아래에 요약한다.

| case | 전체 | 생성 | 그래프 실행 | 런타임 검증 | 테스트 측 |
|---|---:|---:|---:|---:|---:|
| disabled cache CLI | 4.021초 | 1.87ms | 3.867초 | 1.242초 | 0.052초 |
| final business mismatch | 5.093초 | 1.95ms | 4.904초 | 1.649초 | 0.020초 |
| temporary cache failure recovery | 8.326초 | 4.94ms | 8.015초 | 2.672초 | 0.051초 |
| empty/invalid rebind proposals | 10.612초 | 4.52ms | 10.243초 | 3.348초 | 0.044초 |
| six-field full rebind | 5.127초 | 4.47ms | 4.938초 | 1.682초 | 0.025초 |
| unknown rebind cost stop | 3.835초 | 1.96ms | 3.678초 | 1.196초 | 0.011초 |
| query/editor target recovery | 2.630초 | 1.99ms | 2.552초 | 0.833초 | 0.017초 |
| delivery/final-proof policy | 3.997초 | 2.26ms | 3.867초 | 1.285초 | 0.042초 |
| prepared CLI success | 4.078초 | 2.30ms | 3.874초 | 1.304초 | 0.141초 |
| prepared cold cache | 3.838초 | 2.02ms | 3.549초 | 1.086초 | 0.213초 |
| prepared snapshot cache | 3.604초 | 2.21ms | 3.349초 | 1.029초 | 0.192초 |
| shared cache cold/warm | 6.708초 | 5.74ms | 6.447초 | 2.059초 | 0.034초 |
| corrupt cache recovery | 4.281초 | 2.60ms | 4.142초 | 1.237초 | 0.028초 |
| caret recheck correct | 3.652초 | 2.14ms | 3.550초 | 1.276초 | 0.013초 |
| caret recheck wrong value | 3.442초 | 2.02ms | 3.331초 | 1.006초 | 0.010초 |

## 반복 그래프 묶음과 조치

전체 L2 graph는 구조가 같아도 `build_worker_graph(brain)`이 `brain.observation_node`,
`brain.decision_node` 같은 bound method를 노드에 넣는다. 컴파일된 graph를 공유하면 먼저
생성한 `Brain`의 model, relay, 상태 저장소, run log와 cache session이 다음 테스트에 섞인다.
따라서 graph 객체 공유는 **격리 불충족**이고, 가능한 시간 절감도 0.12초 이하여서 적용하지
않았다.

안전하게 공유할 수 있는 반복은 다음 다섯 case의 cold 성공 cache 준비였다.

- graph cache rebind 3개
- graph cache failure/recovery 2개

각 case는 동일한 cold 전체 graph를 먼저 실행해 같은 cache snapshot을 만들었다. 그 cold
실행 표본은 3.117, 3.126, 3.100, 3.304, 3.160초로 합계 15.807초였다. 한 번만 생성하면
네 번을 제거할 수 있으므로 이론상 약 **12.6초**를 줄일 수 있다. 직접 대상 세 파일의 실제
실행은 49.85초에서 39.48초로 **10.37초(20.8%)** 감소했다. 실행 변동을 고려해 관측 절감과
이론 추정을 구분한다.

`test_cold_promotes_warm_reuses_with_new_values_and_refs`는 cold promotion 자체가 검증 대상이고,
corrupt cache case는 손상 복구가 검증 대상이므로 공유하지 않았다. prepared cache 조합도
cold/snapshot과 비용 차단 모드가 서로 다른 계약이다. visual caret 조합은 서로 다른 perception,
relay, 설정과 종료 경로를 사용한다. 이 묶음들은 graph 형태만 같을 뿐 공유 가능한 입력 상태가
아니어서 그대로 유지했다.

## 격리 근거

공유 fixture는 완성된 cache 파일의 **immutable `bytes`만 반환**한다. 원본 경로나 mutable
객체는 노출하지 않는다.

- 각 소비 테스트는 function-scope `tmp_path/cache.json`에 bytes를 새로 쓴다.
- `Brain`, model, relay, `StateStore`, `RunLog`, cache session은 매 case 새로 생성한다.
- 같은 테스트 안의 변형도 각 실행 전 원본 bytes를 다시 기록한다.
- 다섯 소비 case를 역순으로 실행해도 `5 passed in 29.11s`였다.
- 대상 세 파일 전체는 적용 후 `7 passed`였고, 전체 suite도 통과했다.

따라서 공유되는 것은 읽기 전용 초기 입력이며, 실행 중 변경되는 상태는 모두 테스트별로
격리된다.

## 전체 suite 적용 전후

| 지표 | 적용 전 | 적용 후 | 변화 |
|---|---:|---:|---:|
| 수집 case | 844 | 844 | 0 |
| pytest 전체 wall time | 163.48초 | 148.92초 | -14.56초 (-8.91%) |
| JUnit suite time | 160.947초 | 148.628초 | -12.319초 (-7.65%) |
| 전체 커버리지 | 87.634728% | 87.634728% | 0.000000%p |
| 문장 커버리지 | 90.241391% | 90.241391% | 0.000000%p |
| 분기 커버리지 | 80.144642% | 80.144642% | 0.000000%p |
| 정적 validation site | 2,388 | 2,384 | -4 |

커버리지의 실행 문장 7,888/8,741, 누락 문장 853, 실행 분기 2,438/3,042, 누락 분기 604도
정확히 같다. assertion 비교는 AST 기준 `assert` + `pytest.raises` + `pytest.fail`의 정적
validation site 수다. 다섯 cold-success assertion을 fixture의 단일 assertion으로 바꿔 4개가
줄었으며, 업무 시나리오 결과를 검증하는 assertion은 삭제하지 않았다.
