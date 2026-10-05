# 테스트 정리 결과

브랜치: `cleanup/tests-20261005`

## 결과

| 지표 | 기준선 | 정리 후 | 변화 |
|---|---:|---:|---:|
| 수집 테스트 | 855 | 846 | -9 (-1.05%) |
| `test_*` 함수 | 531 | 532 | +1 |
| pytest 3회 평균 | 190.44초 | 160.02초 | -30.42초 (-15.98%) |
| JUnit 3회 평균 | 189.26초 | 159.16초 | -30.10초 (-15.90%) |
| 전체 커버리지 | 87.63% | 87.63% | 0.00%p |
| 문장 커버리지 | 90.24% | 90.24% | 0.00%p |
| 분기 커버리지 | 80.14% | 80.14% | 0.00%p |
| 변경 모듈 변이 점수 | 0% (0/20 탐지) | 65% (13/20 탐지) | +65%p |
| 실패 | 0/3회 | 0/3회 | 변화 없음 |
| flaky | 0 | 0 | 변화 없음 |

정리 후 pytest 실행은 149.75초, 165.07초, 165.23초였고 모두 846 passed였다.
테스트 함수는 하나 늘었다. 세부 분기를 빠른 단위 테스트로 옮기고 전체 그래프 반복을 줄였기
때문이다. 따라서 테스트 수 감소가 아니라 계층 조정과 반복 준비 제거가 시간 개선의 원인이다.

세밀한 test-context 감사 후 query/editor 재선택을 한 전체 업무로 합치고 prepared 성공은
public CLI 대표 경로만 남겼다. 현재 결과는 844 passed이며, 단일 일반 실행은 pytest
163.48초, JUnit 160.947초였다. 전체 실행은 변동 범위 안이므로 속도 개선을 주장하지 않고,
변경한 두 파일의 직접 합계가 18.033초에서 12.506초로 줄어든 것을 근거로 사용한다.

## 변경한 파일의 직접 시간 비교

| 파일 | 기준선 평균 | 정리 후 평균 | 변화 |
|---|---:|---:|---:|
| `test_graph_cache_rebinding.py` | 32.415초 | 25.807초 | -6.608초 |
| `test_graph_cache_failure_recovery.py` | 21.879초 | 17.555초 | -4.324초 |
| `test_shared_graph_cache_integration.py` | 18.074초 | 10.105초 | -7.969초 |
| `test_navigation_progress.py` | 11.882초 | 7.484초 | -4.398초 |
| `test_visual_workflow.py` | 12.813초 | 11.881초 | -0.932초 |
| **합계** | **97.063초** | **72.832초** | **-24.231초** |

세밀 감사에서 추가로 변경한 파일은 다음과 같다.

| 파일 | 변경 전 | 변경 후 | 변화 |
|---|---:|---:|---:|
| `test_input_target_recovery.py` | 11.261초 / 19 case | 8.589초 / 18 case | -2.672초 |
| `test_prepared_brain.py` | 6.772초 / 12 case | 3.917초 / 11 case | -2.855초 |
| **합계** | **18.033초 / 31 case** | **12.506초 / 29 case** | **-5.527초** |

## 결함 탐지력 보존 근거

- 실제 삭제는 더 강한 전체 그래프 대체 검증이 특정된 중복 테스트 한 개뿐이다.
- cache proposal 3종과 cache failure 2종은 case 수가 줄어도 입력 변형 자체는 모두 실행된다.
- navigation 의미 변화 6종과 relay 오류 allowlist 14종은 빠른 단위 계층에서 전부 검증한다.
- 새 창 탐색, 비신뢰 오류 로그 redaction, 캐시 전체 성공·실패는 대표 통합 경로를 유지한다.
- 정확히 같은 AST 본문, 실제 assertion 없는 테스트, flaky 테스트, 폐기 기능 테스트는 없었다.
- 삭제 근거가 부족한 후보는 `review.md`의 보류 목록에 남겼다.

## 커버리지와 변이 검사

- 사용자의 설치 승인 후 프로젝트 가상환경에 `coverage` 7.16.2, `pytest-cov` 7.1.0,
  `mutmut` 3.8.0, `cosmic-ray` 8.7.0을 로컬 설치했다.
- 정리 전후 모두 8,741문장 중 7,888줄, 3,042개 분기 중 2,438개를 실행했다. 누락 문장
  853줄과 누락 분기 604개도 동일했다.
- 변이 검사는 검증 계층을 바꾼 `brain/observe/progress.py`와
  `tests/test_navigation_progress.py` 조합으로 한정했다. 동일한 변이 20개를 정리 전후에
  적용했고, 탐지 수는 0개에서 13개로 증가했다.
- `mutmut` 3.8.0은 네이티브 Windows 실행을 거부하므로 실제 비교 실행에는 Cosmic Ray를
  사용했다. 전체 저장소 변이 검사는 범위와 시간이 과도해 실행하지 않았다.

## 검증 명령

Python 3.13에 저장소 가상환경의 site-packages, bench `sawon_interfaces`, pywin32 DLL 경로를
명시한 뒤 다음 로컬 명령을 사용했다.

```powershell
python -m pytest -q --junitxml=<temporary-path>
python -m pytest -q --cov=brain --cov=models --cov=pipeline --cov-branch
cosmic-ray baseline <config>
cosmic-ray init <config> <session>
cosmic-ray exec <config> <session>
python -m ruff check .
python -m ruff format --check .
```
