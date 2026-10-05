# Verify/Review 구성과 xdist 검증 보고

측정일: 2026-10-05

최초 계측과 후속 적용·검증 결과를 함께 기록한다. 프로덕션 코드는 수정하지 않았다.
`pytest-xdist`는 개발 의존성에 추가했고 로컬 opt-in 스크립트와 CI 병렬 job을 적용했다.
Windows 응답 파일 race 테스트의 fault injection도 수정했다. 기본 pytest `addopts`는 순차
실행을 유지한다. 이 문서의 기존 경로는 다른 보고서의 링크를 보존하기 위해 유지한다.

## 측정 방법

844개 전체 suite를 임시 pytest plugin으로 계측했다. 계측 실행은 `844 passed in 137.13s`였고,
임시 plugin은 측정 후 삭제했다. `_verify_node`와 `_review_node`의 각 호출에서 다음 경계를
exclusive wall time으로 분리했다.

- 계산: 아래 경계에 포함되지 않은 Python 검증·매칭·상태 계산
- 파일 읽기: `Path.read_bytes`
- 로그 scan/parse: `RunLog._read_unlocked`에서 실제 파일 읽기를 제외한 JSONL 분리·검증
- 로그 append/write: `RunLog.append`에서 lock, 기존 로그 읽기·parse와 `fsync`를 제외한
  event 생성·직렬화·append·flush
- snapshot write: `atomic_write_json`에서 `fsync`를 제외한 JSON 직렬화·temporary write·replace
- 내구성 동기화: `os.fsync`
- 대기 상한: `portalocker.Lock.acquire`와 replace retry의 `sleep`; 이번 node 경로에서 retry
  sleep은 발생하지 않아 실제로는 비경합 lock 획득 시간이다.
- 외부 경계 대체: relay `observe`, visual perception `observe`, model `complete_json`

중첩 구간은 한 범주에만 귀속했다. 예를 들어 로그 scan 안의 `read_bytes`는 파일 읽기로만
계산했다. Windows `process_time`의 해상도가 짧은 호출을 분류하기에는 거칠어 CPU time은
계산 비율로 사용하지 않았다.

## `_verify_node`와 `_review_node` 구성

| 구성 | `_verify_node` | 비율 | `_review_node` | 비율 |
|---|---:|---:|---:|---:|
| 호출 수 | 491 |  | 1,162 |  |
| 전체 | 19.974초 | 100.00% | 7.173초 | 100.00% |
| 계산 | 0.596초 | 2.98% | 0.084초 | 1.17% |
| 파일·저장 경계 합계 | 18.800초 | 94.12% | 6.889초 | 96.04% |
| └ 파일 읽기 | 11.046초 | 55.30% | 4.496초 | 62.68% |
| └ 로그 scan/parse | 2.036초 | 10.19% | 0.848초 | 11.83% |
| └ 로그 append/write | 1.212초 | 6.07% | 0.469초 | 6.54% |
| └ snapshot write | 0.939초 | 4.70% | 0.009초 | 0.12% |
| └ `fsync` | 3.567초 | 17.86% | 1.067초 | 14.87% |
| lock 획득 대기 상한 | 0.473초 | 2.37% | 0.200초 | 2.78% |
| 외부 경계 대체 | 0.105초 | 0.53% | 0.000초 | 0.00% |

호출당 `_verify_node`는 평균 40.681ms, 중앙값 37.853ms, p95 60.241ms였다.
`_review_node`는 평균 6.173ms, 중앙값 3.809ms, p95 16.073ms였다.

가장 큰 비용은 검증 계산이 아니라 append-only log를 추가할 때 기존 JSONL 전체를 다시 읽는
경로다. `RunLog.append`는 lock 안에서 `_read_unlocked()`로 이전 event를 모두 읽고 검증한 뒤
새 event를 쓰고 매번 `fsync`한다. `_verify_node`는 여기에 새 observation snapshot의 atomic
write도 수행한다. 프로덕션 코드 변경 금지 조건 때문에 이 경로는 수정하지 않았다.

외부 경계는 모두 fake/replay/local 대체였다. `_verify_node`에서 relay fake 0.094초, visual
fake 0.003초, model fake 0.008초를 사용했고 live ERP, SDK, 네트워크 대기는 없었다.
`_review_node`에는 외부 호출이 없었다. 따라서 이 수치는 실제 ERP나 유료 모델 latency를
대표하지 않으며, 테스트 자체의 저장 비용을 보여준다.

## 동일 `Brain.run()` 입력 탐지

전체 suite에서 `Brain.run()` 82회를 관찰했다. fingerprint에는 record, goal, settings,
task instruction/procedure, relay 초기 상태와 `health/observe/act` 구현·closure, model script와
가격/unknown-cost 설정, call budget, cache 초기 bytes hash, visual perception 구현을 포함했다.

처음에는 80개 fingerprint와 두 중복 후보가 나왔지만 전체 선행 입력을 확인한 결과 둘 다
실제 동일 입력이 아니었다.

| 후보 | 실행 합계 | 한 번 공유할 때 상한 | 제외 근거 |
|---|---:|---:|---|
| prepared `missing_hash` / `not_ready` | 3.670ms | 1.835ms | case directory의 record/check/status가 서로 다름 |
| worker close 정상 / interrupt | 0.006ms | 0.003ms | monkeypatch한 `_run` 동작이 서로 다르고 일반 업무 graph가 아님 |

따라서 **완전한 입력 기준으로 안전하게 공유할 다른 `Brain.run()` 묶음은 0개**, 예상 절감도
0초다. 이전 단위에서 공유한 cold cache snapshot이 현재 남아 있던 실제 중복이었다.

환경·실패 주입 차이를 의도적으로 무시하면 아래 near-duplicate가 보이지만, 무시한 차이가
각 테스트의 검증 대상이다. 수치는 실행 결과까지 공유한다고 가정한 비현실적인 상한이다.

| near-duplicate 묶음 | case | 절감 상한 | 공유할 수 없는 이유 |
|---|---:|---:|---|
| visual diagnostic + caret 3종 | 4 | 8.481초 | relay/caret 변화와 성공·실패 결과가 검증 대상 |
| 동일 창 의미 변화 / 새 창 전환 | 2 | 0.799초 | 서로 다른 navigation progress 조건을 검증 |
| foreground 변화 / unknown cost retry | 2 | 0.202초 | 실패 원인과 차단 시점이 다름 |
| disabled query / dispatch 오류 | 2 | 0.171초 | selection 거부와 relay 거부 경계가 다름 |
| foreground·cost early stop 4종 | 4 | 0.207초 | 서로 다른 budget/환경 guard를 검증 |

이 near-duplicate를 fixture 결과 공유로 바꾸면 약 9.86초의 이론 상한과 함께 결함 탐지 경계를
잃는다. 적용 대상에서 제외한다.

## 순차·병렬 5회 반복 실측

코드 기준은 `70d48d4`이며 Windows/Python 3.13, 로컬 `pytest-xdist` 3.8.0 환경이다.
coverage 계측 없이 각 명령을 5회씩 실행했고, 실행마다 새 Python 프로세스와 고유 basetemp,
JUnit 파일을 사용했다. 로컬 테스트 실행끼리는 겹치지 않았다. 1·3·5번째 쌍은 순차→병렬,
2·4번째 쌍은 병렬→순차 순서로 실행했다. 아래 시간은 pytest 터미널 반올림 값이 아닌 JUnit
testsuite wall time이며 Python 프로세스 시작·종료 전체 시간과는 다르다.

| 반복 | 순차 | `-n 4 --dist load` | 결과(각 실행) |
|---|---:|---:|---|
| 1 | 167.170초 | 73.854초 | 844 passed |
| 2 | 167.092초 | 69.191초 | 844 passed |
| 3 | 160.063초 | 39.496초 | 844 passed |
| 4 | 131.886초 | 40.416초 | 844 passed |
| 5 | 132.477초 | 39.456초 | 844 passed |
| **중앙값** | **160.063초** | **40.416초** | 실패·skip 0 |
| 범위 | 131.886–167.170초 | 39.456–73.854초 | 총 8,440 case 통과 |

중앙값 차이는 **119.647초(74.75%)**, 시간 비율은 **3.96배**다. 두 방식 모두 후반 실행이
빨라졌다. OS 파일 캐시나 시스템 부하를 별도로 계측하지 않았으므로 변동 원인은 확정하지
않는다. 이 결과는 해당 로컬 환경의 관측값이며 CI 개선율로 일반화하지 않는다.
원시 JUnit 집계와 실행 순서는 `xdist-repeat-timings.json`에 보존했다.

실행 명령은 다음과 같다. 두 명령 모두 실행별 고유 경로를 사용했다.

```powershell
python -m pytest -q --basetemp=<unique-temp> --junitxml=<unique-xml>
python -m pytest -q -n 4 --dist load --max-worker-restart=0 --basetemp=<unique-temp> --junitxml=<unique-xml>
```

## 초기 탐색 측정과 격리

`pytest-xdist` 3.8.0을 로컬 설치한 뒤 default `--dist load`와 비교용 `loadfile`을 실행했다.
모든 실행은 GUI 테스트를 제외하는 기존 설정을 사용했고, worker별 고유 `--basetemp`와 하나의
JUnit 결과를 사용했다.

| 실행 | 전체 결과 | JUnit 시간 | 순차 188.693초 대비 |
|---|---:|---:|---:|
| 순차 최신 기준 | 844 passed | 188.693초 | 기준 |
| `-n 2 --dist load` | 844 passed | 76.517초 | -112.176초 (-59.45%) |
| `-n 4 --dist load`, 1회 | 844 passed | 52.115초 | -136.578초 (-72.38%) |
| `-n 4 --dist load`, 반복 | 844 passed | 117.059초 | -71.634초 (-37.96%) |
| `-n 4 --dist loadfile` | 844 passed | 53.198초 | -135.495초 (-71.81%) |
| `-n 8 --dist load` | 844 passed | 59.934초 | -128.759초 (-68.24%) |

이 표는 5회 반복 전의 탐색 기록이다. 표본이 적고 시점 차이가 있으므로 n2/n4/n8의 우열이나
worker 초기화·파일 I/O의 인과관계를 확정하는 근거로 사용하지 않는다. 현재 n4 절감 수치는
위 5회 반복 중앙값을 사용한다.

격리 확인 결과는 다음과 같다.

- n2, n4 두 번, n8, n4 loadfile의 합계 4,220 case 실행이 모두 통과했다.
- xdist가 `popen-gw0` 등 worker별 temp root를 만들었고, 확인한 test-managed write 대상은
  function-scope `tmp_path` 또는 worker별 session temp 아래였다.
- 실제 포트를 쓰는 HTTP/worker fixture는 `127.0.0.1:0`으로 OS가 빈 포트를 할당한다.
  1, 1234, 8765 같은 고정 포트 문자열은 거부·mock 경로라 listener 충돌이 없었다.
- 환경 변수 변경은 `monkeypatch.setenv/delenv`이고 worker process 안에서 복구된다. API key,
  worker URL과 PATH 변경이 다른 worker로 전파되지 않았다.
- `graph_cache_snapshot`은 session scope지만 worker별 session이다. default load에서는 실제로
  한 worker만 생성했다. loadfile은 두 관련 파일을 다른 worker에 배치해 cold graph를 두 번
  만들었고, 측정 시간도 default load보다 1.083초 길었다.
- `.pytest_cache`는 저장소 공용 경로지만 xdist 실행에서 충돌·실패는 관찰되지 않았다.
- coverage 병합은 아래 후속 검사에서 순차 결과와 파일별 line·branch 집합까지 동일함을
  확인했다. mutation 도구의 xdist 병렬 실행·병합은 검사하지 않았다.

## 적용 판단과 남은 한계

| 후보 | 관측값 또는 절감 상한 | 위험 | 최종 판단 |
|---|---:|---|---|
| 동일 `Brain.run()` 결과 fixture 공유 | 0초 | 낮음 | 완전 동일 입력 묶음이 없어 적용할 것이 없음 |
| near-duplicate 결과 공유 | 최대 9.86초 | 매우 높음 | 테스트가 의도한 환경·실패 분기를 제거하므로 제외 |
| 테스트 전용 buffered/in-memory log | node 저장 구간 최대 25.69초 | 높음 | persistence 결함을 가릴 수 있어 미적용. RunLog 개선은 별도 프로덕션 이슈로 분리; 이 수치는 보장 절감량이 아님 |
| xdist `-n 4 --dist load` | 로컬 중앙값 119.647초 절감; CI 1회는 2.50초 증가 | 환경별 성능 차이, 잠재적 순서 의존성 | 로컬 opt-in 적용; CI는 순차·병렬 모두 통과; coverage 집합 동일 |
| n2 / n4 loadfile / n8 | 위 초기 탐색 표의 단일 표본 | 표본 부족 | 기본값·최적 worker 수의 근거로 채택하지 않음 |

`-n 4 --dist load` opt-in과 coverage 비교는 완료했다. 기본 `pytest`는 순차 실행이다.
이번 후속 검증에서는 fixture 추가 공유와 프로덕션 `RunLog` 변경을 수행하지 않았다.

## 후속 coverage 병합 검증

같은 코드에서 순차와 `-n 4 --dist load` coverage를 각각 실행해 JSON 결과의 전체 수치와
파일별 `executed_lines`, `missing_lines`, `executed_branches`, `missing_branches` 집합을 직접
비교했다.

| 지표 | 순차 | xdist n4 | 차이 |
|---|---:|---:|---:|
| tests | 844 passed | 844 passed | 0 |
| 실행 line | 7,890 / 8,741 | 7,890 / 8,741 | 0 |
| 누락 line | 851 | 851 | 0 |
| 실행 branch | 2,438 / 3,042 | 2,438 / 3,042 | 0 |
| 누락 branch | 604 | 604 | 0 |
| 전체 coverage | 87.651702% | 87.651702% | 0.000000%p |
| 문장 coverage | 90.264272% | 90.264272% | 0.000000%p |
| 분기 coverage | 80.144642% | 80.144642% | 0.000000%p |
| 파일별 실행·누락 집합 | 기준 | 동일 | 0개 차이 |

첫 병렬 coverage 실행에서는 Windows response publication race 테스트 한 건이 `exists()`와
`read_text()` 사이의 TOCTOU 때문에 fault를 주입하지 못해 실패했고 branch 한 개가 누락됐다.
동일 조건의 단일 테스트는 3회 모두 통과했고 전체 재실행도 통과했다. 테스트 fault injection은
응답을 실제로 한 번 읽어 publication을 확인한 뒤 `PermissionError`를 발생시키도록 바꿔 이
race를 제거했다. 위 coverage 비교표는 수정 후 같은 코드에서 얻은 결과다.

기본 `python -m pytest`는 그대로 유지한다. 로컬 병렬 실행은
`scripts/test-parallel.ps1`의 opt-in 명령으로 제공하고, CI는 순차 job이 통과한 뒤 별도 병렬
job을 실행한다. 프로덕션 `RunLog` 개선은 `runlog-append-production-issue.md`로 분리했다.

## 실제 GitHub Actions 러너 검증

브랜치 `cleanup/tests-20261005`, commit `70d48d4`를 push해
[checks 실행 37270570811](https://github.com/Dreamtreeme/agent-sawon/actions/runs/37270570811)을
실제로 실행했다. Ubuntu 24.04 이미지 `20260927.320.1`, Python 3.13에서 두 job이 통과했다.

| job | 결과 | pytest 시간 | job 전체 시간 |
|---|---|---:|---:|
| `unit` | 838 passed, 3 skipped | 38.49초 | 66초 |
| `parallel` | 838 passed, 3 skipped | 40.99초 | 69초 |

job 전체 시간은 GitHub의 startedAt/completedAt 차이로 설치·checkout 등을 포함한다.
병렬 pytest는 순차보다 2.50초(6.50%) 느렸다. CI 1회는 호환성과 통과 여부를 확인한 것이며,
속도 개선 근거가 아니다. CI 병렬 job은 순서·격리 문제를 확인하는 추가 검사로 유지한다.

CI의 skip은 두 job에서 동일했다. OpenCV가 설치되지 않아 `test_visual_rectangles.py`의
4개 case가 모듈 단위로 수집되지 않았고(모듈 skip 1건), Windows 전용 replacement/response
publication 테스트 2개가 skip됐다. 따라서 로컬 844 passed와 CI 838 passed의 차이를 실패나
병렬 누락으로 해석하지 않는다. CI에서는 이 6개 case의 동작을 검증하지 못했다.
