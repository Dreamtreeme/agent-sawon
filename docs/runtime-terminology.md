# 화면 처리 용어와 코드 대응

기준일: 2026-10-21

화면 처리 책임은 탐색(Scanning) → 패턴 매칭(Pattern Matching) → 기억 인출(Memory
Retrieval) → 맥락 회상(Context Recall)으로 설명한다. 처음 보는 화면과 기억이 있는 화면은
서로 다른 실행 모드가 아니라, 기억 검색 결과가 다른 경우다.

## 책임과 진입점

| 책임 | 의미 | 현재 코드 |
|---|---|---|
| Scanning | 현재 캡처·OCR·탐지 기하 확보 | `brain/visual/scanning.py`: `ScreenScanner.scan` |
| Pattern Matching | 관찰과 현재 업무에 맞는 저장 패턴 검색 | `brain/cache/pattern_matching.py`: `ScreenPatternCache.lookup`, `screen_pattern_key`; 좌표 패턴은 `brain/cache/methods.py`: `_coordinate_bindings` |
| Memory Retrieval | 저장한 수행 방법과 대상을 현재 화면에 연결 | `brain/graph/memory_retrieval.py`: `MemoryRetrieval.retrieve` |
| Context Recall | 현재 관찰·입력값·교육·인출한 기억·누적 메모를 결합하여 계획 생성 | `brain/graph/context_recall.py`: `recall_context`, `build_context_prompt`, `select_context_response` |
| Memory Compilation | 성공한 실행 기록을 정제하여 재사용할 기억 생성 | `brain/demo_learning/memory_compilation.py`: `read_memory_candidates`, `assemble_memory`, `compile_memory` |

`Brain.screen_scanner`와 `Brain.memory_retrieval`이 해당 서비스를 제공한다.
모델 경계의 `ScreenMapping`, `ScreenMatch`, 입력/행동/증거 필드 이름은 유지한다.
맥락 회상은 반드시 모델 호출을 뜻하지 않는다. 인출된 계획이나 패턴 캐시도 같은 경로에 들어간다.

## 이번 변경과 구현 범위

이번 변경은 파일·Python 심볼·프롬프트의 용어를 정리한다. 현재 `ScreenScanner.scan`은
기존 해석 콜백을 통해 맥락 회상을 호출하며, `MemoryRetrieval.retrieve`는 패턴 비교와 실행
이력 관리를 함께 수행한다. 네 책임이 네 개의 독립 그래프 노드로 분리되었다는 뜻은 아니다.

일치 이미지의 관찰 재사용은 파일 해시·크기·창 기준이고, `ScreenPatternCache`는 정규화한
화면/업무 문맥의 정확한 일치를 사용한다. 좌표 기억은 기존 viewport·pHash 기준을 사용한다.
명칭 변경으로 유사도 기준이나 실행 순서가 바뀌지 않는다.

캡처 직후 패턴 검색, 읽기 전용 필드를 포함한 완전한 화면 매핑 편찬, 인출한 매핑으로
누적 메모를 채우는 구조는 후속 동작 변경이다. 현재 편찬의 일반 증거 누락과 기존 예상 실패를
이번 이름 변경으로 해결했다고 보지 않는다.

## 저장 형식과 과거 기록

- `methods`, `visual_match_cache`, `method_id`, `artifact_kind="warm_compiled"` 등 기존 설정·
  저장 필드와 판정값은 유지한다. Python의 구 모듈이나 클래스 별칭은 제공하지 않는다.
- bench의 `execution_phase=cold/warm/unknown`과 재현 지표는 실험 조건의 저장 계약이다.
  Scanning 등을 이 값에 넣지 않는다. 과거 protocol·파일럿 기록은 소급 수정하지 않는다.
- `warm_method_*`, `warm_context_anchors_truncated` 오류·계측 식별자는 기존 기록의 소비
  계약으로 유지한다. `model_call.context.purpose` 분류도 유지한다.
- 모델 응답 편찬 클래스는 `MemoryAssembly`, `MemoryGroupSelection`, `RemovedMemoryGroup`으로
  바뀐다. JSON Schema의 title/정의 이름은 새 이름이며, 필드·제약은 그대로다.
- 새 편찬 CLI는 `python -m brain.demo_learning.memory_compilation`이다. bench의
  `harness.compilation`도 이 진입점을 호출한다. 이전 명령은 역사 기록에만 남는다.

## 검증

핵심 회귀 파일은 `test_context_recall.py`, `test_memory_retrieval.py`,
`test_pattern_matching.py`, `test_memory_compilation.py`다. 실제 실행 명령과 전체 검증은
[AGENTS.md](../AGENTS.md)를 따른다. 어휘 스키마 fixture는 변경하지 않는다.
