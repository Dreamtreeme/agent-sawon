# 테스트 정리 결과

브랜치: `cleanup/tests-20261005`

## 결과

| 지표 | 기준선 | 정리 후 | 변화 |
|---|---:|---:|---:|
| 수집 테스트 | 855 | 846 | -9 (-1.05%) |
| `test_*` 함수 | 531 | 532 | +1 |
| pytest 3회 평균 | 190.44초 | 160.02초 | -30.42초 (-15.98%) |
| JUnit 3회 평균 | 189.26초 | 159.16초 | -30.10초 (-15.90%) |
| 실패 | 0/3회 | 0/3회 | 변화 없음 |
| flaky | 0 | 0 | 변화 없음 |

정리 후 pytest 실행은 149.75초, 165.07초, 165.23초였고 모두 846 passed였다.
테스트 함수는 하나 늘었다. 세부 분기를 빠른 단위 테스트로 옮기고 전체 그래프 반복을 줄였기
때문이다. 따라서 테스트 수 감소가 아니라 계층 조정과 반복 준비 제거가 시간 개선의 원인이다.

## 변경한 파일의 직접 시간 비교

| 파일 | 기준선 평균 | 정리 후 평균 | 변화 |
|---|---:|---:|---:|
| `test_graph_cache_rebinding.py` | 32.415초 | 25.807초 | -6.608초 |
| `test_graph_cache_failure_recovery.py` | 21.879초 | 17.555초 | -4.324초 |
| `test_shared_graph_cache_integration.py` | 18.074초 | 10.105초 | -7.969초 |
| `test_navigation_progress.py` | 11.882초 | 7.484초 | -4.398초 |
| `test_visual_workflow.py` | 12.813초 | 11.881초 | -0.932초 |
| **합계** | **97.063초** | **72.832초** | **-24.231초** |

## 결함 탐지력 보존 근거

- 실제 삭제는 더 강한 전체 그래프 대체 검증이 특정된 중복 테스트 한 개뿐이다.
- cache proposal 3종과 cache failure 2종은 case 수가 줄어도 입력 변형 자체는 모두 실행된다.
- navigation 의미 변화 6종과 relay 오류 allowlist 14종은 빠른 단위 계층에서 전부 검증한다.
- 새 창 탐색, 비신뢰 오류 로그 redaction, 캐시 전체 성공·실패는 대표 통합 경로를 유지한다.
- 정확히 같은 AST 본문, 실제 assertion 없는 테스트, flaky 테스트, 폐기 기능 테스트는 없었다.
- 삭제 근거가 부족한 후보는 `review.md`의 보류 목록에 남겼다.

## 측정 제한

- coverage와 pytest-cov가 로컬 환경에 없어 커버리지를 측정하지 못했다.
- mutmut와 cosmic-ray가 로컬 환경에 없어 변이 점수를 측정하지 못했다.
- 외부 네트워크 금지 규칙에 따라 도구를 설치하지 않았다.
- 이 제한 때문에 coverage 또는 변이 점수로 판정해야만 하는 후보는 삭제하지 않았다.

## 검증 명령

Python 3.13에 저장소 가상환경의 site-packages, bench `sawon_interfaces`, pywin32 DLL 경로를
명시한 뒤 다음 로컬 명령을 사용했다.

```powershell
python -m pytest -q --junitxml=<temporary-path>
python -m ruff check .
python -m ruff format --check .
```

외부 네트워크 명령은 실행하지 않았다.
