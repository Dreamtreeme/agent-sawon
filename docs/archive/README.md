# 날짜별 로컬 아카이브

현재 지침은 [런타임 기준](../current-runtime.md), [계약 지도](../contracts.md),
[코드 흐름](../code-flow.md), [테스트 유지 기준](../test-suite-policy.md)을 따른다.

수정·조사·파일럿의 일회성 문서와 첨부 자료, 사용이 끝난 감사 도구는 다음 형식으로 보관한다.

```text
docs/archive/YYYY-MM-DD/
  manifest.json
  docs/...                 # 기존 저장소 상대 경로
  tests/...                # 퇴역한 감사 도구·자기검사
  .tools/...               # 종료된 조건 제거 실험과 산출물
```

날짜별 폴더는 `.gitignore`에서 제외하므로 새 체크아웃에는 포함되지 않는다. 현행 테스트,
공용 fixture, 기준 문서는 계속 버전 관리한다. 테스트에 날짜나 합성 입력 표시가 있다는 이유만으로
아카이브하지 않는다.

각 `manifest.json`에는 원본·보관 경로, 날짜 선정 근거, 바이트 수, SHA256과 보관 직전 커밋을
남긴다. 문서는 파일명·본문의 날짜를 우선하고 날짜를 확인할 수 없으면 Git 변경일과 그 사유를
기록한다. 퇴역한 테스트 도구는 퇴역일로 묶는다. 원문은 수정하지 않으므로 과거 상대 링크는
원래 위치를 기준으로 해석하거나 manifest에서 대상을 찾는다.

## 2026-11-02 정리 범위

- 기존 `docs/test-cleanup/`, 과거 `docs/archive/` 자료와 2026-10-10 파일럿 첨부 화면.
- `tests/recorded_graph_replay.py`와 `test_recorded_graph_replay.py`: 과거 086·088·089 실행의
  입력 동일성 감사 CLI와 도구 자기검사 8건. 해당 테스트는 제품 그래프를 실행하지 않는다.
  제품·현행 실행 스크립트에는 이 도구의 소비자가 없으며, 현재 기억 재사용 회귀를 대체하지 않는다.
- `tests/conftest.py`: docstring만 있고 fixture·hook·실행문이 없는 빈 지원 파일.
- `.tools/route-condition-audit-144/`, `.tools/route-condition-fix-145/`: 수정 전후의 일회성
  조건 제거·기록 재생 실험. 과거 함수 이름을 참조하는 스크립트도 원문 그대로 보관한다.

회귀 테스트, 두 예상 실패 사례, 선택 가능한 공급자·설정 경로의 테스트는 현재 코드에서 사용하므로
유지한다. 예전 기록을 검사하는 제품 `models/replay.py`도 이 감사 도구와 다른 기능이다.

기존에 추적하던 파일은 정리 직전 커밋 `d28ae2e`에도 남아 있다. 로컬 보관본이 없는 환경에서는
아래와 같이 원문을 읽을 수 있다. 과거 전체 환경을 재현할 때는 당시 의존성과 설정도 필요하다.

```powershell
git show d28ae2e:docs/test-cleanup/memory-route-progress-fix-20261102.md
git show d28ae2e:tests/recorded_graph_replay.py
```
