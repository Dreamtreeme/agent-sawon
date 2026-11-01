# 편찬의 행동 참조와 필드 의미 보존 수정

검증일: 2026-11-02. 140 ERP 검토에서 발견한 첫 번째 문제만 수정했다.
새 ERP 파일럿이나 유료 모델 호출은 실행하지 않았다.

## 결함과 변경

138 탐색에서 잘못 붙인 `새 건 등록` 이름은 실제 닫기 버튼 영역을 가리켰다.
편찬은 실제 결과에 맞춰 목적을 메뉴 복귀로 바꿨지만 필드 이름은 원본에서 복사했다.
또한 편찬용 관찰 재생성은 대상 ID를 40에서 36으로 바꾼 반면 전달 행동은 40을 유지했다.
그 결과 이름을 잃은 `target_1` 행동과 잘못된 관찰 이름이 함께 저장됐다.

제품 변경은 `brain/demo_learning/memory_compilation.py`에 한정했다.

| 변경 위치 | 변경 내용 |
|---|---|
| `read_memory_candidates` | 기존 컨트롤의 의미 이름으로 전달 행동을 새 필드 ID에 연결한다. 미실행 행동을 복원하거나 실제 행동·입력 키·좌표를 변경하지 않는다. |
| `MemoryCandidate` / 후보 수집 | 전달 이후 같은 `after_obs_id`의 계획에서 교육 관찰 결과를 연결한다. 이전 판단은 후속 증거로 사용하지 않는다. |
| `memory_assembly_prompt` | 필드 이름·role·bbox·source, OCR text·bbox, 의미 이름으로 표현한 행동 및 후속 관찰 결과를 모델에 제공한다. 업무 값은 기존 redaction을 적용하며 필드 관찰값은 포함하지 않는다. |
| `MemoryGroupSelection.field_renames` | 모델이 기존 이름을 교정할 수 있도록 `old_name`, `new_name` 목록을 추가했다. 생략 시 빈 목록이다. |
| `_rename_group_fields` | 제거 구간의 진입 관찰을 연결한 **뒤** 최종 그룹의 대상·행동 참조·관찰 필드·입력 연결·메시지 참조를 함께 바꾼다. |
| `_identify_group` | 내용이 바뀐 그룹의 ID 산정을 재사용한다. |

교정 계약은 이름에 한정한다. role·bbox·입력 source·전달 point를 모델이 변경하는 기능은
추가하지 않았다. 없는 이름 참조·중복 교정·교정 후 이름 충돌은 편찬 오류이며, 새로운
실시간 행동 거절 조건은 추가하지 않았다. 인출 경로와 산출물 스키마는 변경하지 않았다.
현행 런타임·계약 문서를 같은 변경에 갱신했다.

## 검증

기존 `tests/test_memory_compilation.py`의 세 시나리오를 확장했다. 독립 테스트 함수는
추가하지 않았으며 재해석·구간 생략 조건의 매개변수 케이스가 각각 하나 늘었다.

1. **행동 참조:** 같은 프레임을 두 번 해석하여 원본 ID와 편찬 재생성 ID를 다르게 만든다.
   수정 전 `fresh`는 통과, `reinterpreted`는 `KeyError: '2'`로 실패했다. 수정 후 이름·role·
   전체 bbox·point·입력 키가 연결된다.
2. **의미 교정과 인출:** 합성 모델 교정 `Open → Close`를 적용하고 artifact 저장·재로드,
   `MemoryRetrieval.retrieve_screen_memory`, 최종 `mapping_prompt`까지 연결을 검사한다.
   원본 후보·현재 값의 불변성도 확인한다. 구간 생략 후 entry 관찰이 예전 이름을 되살리는
   추가 결함을 리뷰에서 재현했고, 교정을 rebase 뒤로 이동한 뒤 두 조건 모두 통과했다.
3. **편찬 입력:** OCR 위치·필드·행동 이름·후속 관찰 전달을 검사한다. 같은 관찰 ID라도
   행동 전달 이전 판단은 선택되지 않으며, 후속 설명의 업무 값은 마스킹된다.

| 명령 | 결과 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest tests/test_memory_compilation.py -q` | 18 passed |
| `.\.venv\Scripts\python.exe -m pytest` | 764 passed, 2 xfailed, 48.31초 |
| `.\.venv\Scripts\python.exe -m ruff check .` | 통과 |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 277 files already formatted |
| `git diff --check` | 통과 |

기존 xfail 두 건은 `test_observation_path_equivalence.py`의 106 unused zero-size field,
106 recovery uses old memo이며 이번에 변경하지 않았다.

## 138 실제 기록의 무료 재생

로컬 `.tools/memory-compilation-141/offline_check.py`로 138 성공 실행 원본을 읽고 임시
디렉터리에서 후보 수집·편찬·라이브러리 로드·인출·요청 구성을 재생했다. 이 파일명의 141은
로컬 확인 작업명이며 새 ERP 실행 번호가 아니다. 모델 응답의 이름 교정은 **합성 입력**이다.

- 후보 16개, 기존 선택안의 편찬 그룹 14개.
- c003 행동 target은 새 필드 ID 36에 연결됐다. `target_1`의 1×1 영역으로 소실되지 않았다.
- 실제 후속 관찰의 `open-new-record=false`와 메뉴 도착 설명이 편찬 요청에 포함됐다.
- 합성 `새 건 등록 → 닫기` 교정 후 target·관찰 필드·인출 fields·절차 action에 `닫기`가 유지됐다.
- bbox `[352,708,102,41]`, 실제 전달 point `[403,728]`은 보존됐다.
- 원본 assembly-input·assembly-draft·warm-methods 파일은 전후 SHA256이 같았다.
- 유료 호출 0회, ERP 실행 0회.

## 주장 범위와 반증 조건

이번 검증은 참조 연결과 모델이 반환한 의미 교정의 저장·전달을 확인한다. 새 프롬프트를 받은
실제 모델이 올바른 교정을 선택하는지는 측정하지 않았다. 기존 138 편찬물은 수정되지 않았으므로
그 산출물을 그대로 쓰면 예전 이름이 남는다. 실제 적용에는 새 코드로 편찬한 산출물이 필요하다.

전달 행동의 point/source가 달라지거나, 구간 생략 후 예전 이름이 인출되거나, 전달 전 관찰이
후속 근거로 연결되면 이번 수정의 불변 조건을 위반한다. 실제 모델이 교정 응답을 주지 않는 경우는
전달 코드의 성공과 별개로 새 편찬 입력·모델 판단을 추가 검토할 근거다.
