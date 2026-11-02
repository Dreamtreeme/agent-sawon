# 복구 행동과 기억 진도의 연결: pHash 조건 제거

## 변경

`MemoryRetrieval._replacement_group`에서 화면 전체의 pHash 조건을 제거했다.
모델이 현재 화면에서 선택한 행동을 남은 원본 경로의 첫 그룹에 연결하는 지점이다.
창 크기, 행동 수·종류, 입력 키, 키 조합, 대상 좌표 또는 bbox 비교는 유지한다.
실제로 전달한 대체 행동은 기존 효과 검토를 거쳐 `replaced_edge`로 확정된다.

저장된 좌표를 실행할 수 있는지 판단하는 `_matching_route`와
`_coordinate_bindings`의 화면 유사도 조건은 변경하지 않았다.
프롬프트, 스키마, 모델 호출, relay, 편찬물도 변경하지 않았다.

## 기존 140 기록의 함수 재생

140 changed 실행의 저장된 화면·수용된 계획·전달·효과 이벤트를 사용했다.
비교 대상은 `aebd9c1`의 기존 함수와 수정한 함수다.
protocol의 138 편찬 원본 14그룹을 사용했으며 산출물 SHA-256은
`1b76842dbf3885b25cc7a2652cb3922b2289f556bf72a8bcce820a9ffa7fa2ea`다.

| 구간 | 기존 함수 | 수정한 함수 |
|---|---|---|
| 닫기, 모델 seq350, pHash 거리 14 | 원본 닫기 그룹 연결 실패 | `2c045989298bdaa4` 연결 |
| 메뉴, 모델 seq375 | 남은 첫 그룹이 닫기여서 매칭 실패 | `896c791020a40f8d` 매칭, 거리 0 |
| 조회, 모델 seq401 | 남은 첫 그룹이 닫기여서 매칭 실패 | `a33ca5d4b5099213` 매칭, 거리 0 |

메뉴 seq375는 닫기 전달 seq366 이후, 효과 확정 seq384 이전이다. 기존 코드의
`unverified.replaced_edge`로 조건부 연결하는 순서를 그대로 반영했다. 조회 seq401도
메뉴 전달 seq393 이후, 효과 확정 seq410 이전이다. 미래의 효과를 이미 확정한 것으로
취급하지 않았다. 닫기 화면 자체의 `_matching_route`는 수정 후에도 실패한다.

재생 스크립트와 상세 결과는 로컬 `.tools/memory-progress-140/`의
`replay_route_progress.py`, `route-progress-before.json`, `route-progress-after.json`에 있다.
실제 기록에서 상태를 재구성한 함수 재생이며 새 ERP 실행이나 전체 그래프 실행은 아니다.
원본 로그를 바꾸지 않았으며 API 호출과 신규 비용은 없다.

## 회귀 확인

기존 진도 연결 테스트에 14비트 pHash 변화 조건을 추가했다. 제품 수정 전에는
`replaced_edge` 단언에서 2건이 실패했고, 수정 후에는 통과했다. 성공 효과와 실패 효과를
각각 검증하며, 실패한 대체는 원본 경로를 완료한 것으로 확정하지 않는다.
기존 다른 컨트롤 거절 테스트에는 창 크기가 다른 경우를 추가했다.

| 명령 | 결과 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests/test_memory_retrieval.py tests/test_methods.py -q` | 49 passed |
| `.venv/Scripts/python.exe -m pytest` | 778 passed, 2 xfailed, 44.59초 |
| `.venv/Scripts/python.exe -m ruff check .` | 통과 |
| `.venv/Scripts/python.exe -m ruff format --check .` | 280 files already formatted |

기존 xfail 2건은 변경하지 않았다. 테스트 함수는 늘리지 않고 기존 2개를 보강했으며,
viewport 파라미터 1건만 늘었다.

## 확인 범위와 한계

140에서 닫기 이후 진도가 남아 메뉴·조회 기억을 찾지 못한 원인은 이 조건으로 설명된다.
새 ERP 실행에서 모델 호출이나 시간이 실제로 줄었는지는 아직 측정하지 않았다.

이 함수는 기존부터 intent 설명·대상 이름·화면 경로를 비교하지 않는다. pHash 제거 뒤에는
같은 크기의 다른 화면에서 같은 위치에 같은 종류의 행동을 한 경우도 대체로 연결될 수 있다.
이것은 코드 비교로 확인한 적용 범위이며, 이번 재생에서 발생한 추가 오작동은 아니다.
이를 막는 새 의미 규칙을 임의로 추가하지 않았다.

반증 조건은 현재 모델의 동일 컨트롤 행동이 성공했는데도 다음 원본 그룹으로 연결되지 않거나,
다른 업무 단계의 행동이 원본 단계의 완료로 잘못 연결되는 실제 기록이 나오는 경우다.
