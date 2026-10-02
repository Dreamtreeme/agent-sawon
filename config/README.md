# 실행 설정 선택

설정 파일은 비교 조건과 과거 실행의 해시에 연결된다. 이름이 오래됐거나 내용이 비슷하다는
이유로 합치거나 삭제하지 않는다. 새 비교는 사용할 파일과 해시를 별도 계획에 고정한다.

| 목적 | 설정 |
|---|---|
| 현재 Luna 주 화면 작업자 | `harness_agents.visual.json` (`core_visual`) |
| Luna 명시 프로필·과거 비교 재현 | `harness_agents.luna.json` (`core_visual`) |
| Gemini 비교 프로필 | `harness_agents.gemini.json` (`core_visual`) |
| Jev 화면 매칭 + Gemini 이미지 보완 파일럿 | `harness_agents.jev.json` (`core_jev`) |
| 개정 M1 연습 0–29, 경량 기본·상위 복구·매 실행 빈 캐시 | `harness_agents.m1-practice.json` |
| M1 두 관찰 요약 전략, 캐시 없음·동일 상위 복구 | `harness_agents.m1-final.json` |
| M2 경량 기본·상위 복구·캐시 | `harness_agents.m2.json` |
| M2 서류→ERP | `harness_agents.m2-workflow.json` |
| 캐시 없음/빈 캐시/스냅샷 격리 | `harness_agents.cache-isolated.json` |
| 준비된 서류의 캐시 격리 | `harness_agents.m2-workflow-cache-isolated.json` |
| 초기 M1 상위/경량 비교 재현 | `harness_agents.json`, `harness_agents.lite.json` |
| 영문/한글 서류 | `pipeline.toml`, `pipeline.korean.toml` |
| 영문/한글 팩스 OCR 프로필 | `pipeline.fax.toml`, `pipeline.korean-fax.toml` |

`brain.toml`은 초기 캐시 없는 조건이고 `brain.m2-no-cache.toml`은 M2의 복구 정책을
유지한 캐시 없는 조건이다. 서로 대체하면 비교 조건이 달라진다.
`brain.m2.toml`의 정확 일치와 `brain.m2-rebind.toml`의 재연결도 별도 조건이다.

새 연습의 `core_practice_cold`는 `brain.m2.toml`의 정확 일치를 사용하며 매 실행 캐시를
격리한다. 기존 캐시나 시연 결과를 가져오지 않는다. `label_grouped`는 개발 기본안으로
고정하며 우승 전략으로 확정한 것이 아니다. 파일럿과 연습의 조건·횟수·비용 배정은
[연습 실행 준비](../../agent-sawon-bench/docs/m1-practice-preparation-20260930.md)를 따른다.

`models.toml`/`models.lite.toml`과 `pricing.toml`은 비용 기록에 사용한다. 과거 모델의
가격 항목은 당시 설정을 재현하기 위해 보존한다. 여기에 API 키를 넣지 않는다.

반복 화면 실행의 전경 인계는 벤치 CLI `--desktop-handoff`에서 선택하며 계획에 남긴다.
이 옵션은 모델·캐시 설정 파일이나 relay 허용 목록을 대신하지 않는다.

화면 전용 `brain.visual.toml`/`brain.visual-cache.toml`은 `[visual_detector]`의 로컬
OmniParser 가중치·Python을 사용한다. 현재 값은 이 컴퓨터의 기존 L2C 설치 위치다.
다른 환경에서는 경로를 수정해야 하며, weights 상대 경로는 설정 파일 기준이다.
설치 파일/가중치는 저장소에 포함되지 않는다. [화면 인식 이식 상세](../brain/visual/README.md).

확대 재탐지는 `[visual_detector]`의 `refinement_enabled`(기본true),
`refinement_max_regions`(기본3, 상한6), `refinement_scale`(기본2)로 조정한다.
OCR 겹침 제거 허용 오차는 `[visual_merge]`의 `min_ocr_confidence`, `text_overlap`,
`text_padding_ratio`, `max_text_padding`, `duplicate_iou`, `nested_area_ratio`로 조정한다.
새 실행은 이 기본값까지 포함한 설정과 코드 버전을 고정하며 과거 측정과 합치지 않는다.

Jev 파일럿은 기존 `brain.visual.toml`을 사용하고 `--matching-model-config models.jev.toml`을
추가한다. 기본 `--model-config models.visual.toml`은 이미지 보완용 Gemini다.
명시적으로 지정한 비공개 `.env`에 `TYPESAFE_API_KEY`(또는 `JEV_API_KEY`)와 `GEMINI_API_KEY`가 필요하다.
상세 동작·재현 명령·현재 한계는 [Jev 파일럿](../docs/jev-pilot.md)을 따른다.


2026-10-03 정리: 현재 화면 작업자는 `screen_matching=true`와 `task_path`를 사용한다.
`task_path` 파일은 목표·요구사항·완료 조건·제약만 포함하며 화면 순서나 좌표를 정의하지 않는다.
기존 `procedure_path`는 과거 비교 호환용이다. CLI에서는 `--task`로 업무 설명을 바꿀 수 있다.
이 방식으로 대체된 `numbered_selection`/`visual_joint_planning` 옵션과 전용 실행기는
제거했다. 이전 옵션을 켠 별도 로컬 설정은 현재 화면 매칭 설정으로 갱신해야 한다.
저장소의 여섯 `brain*.toml` 프로필은 모두 로드 검증을 통과했다.
과거 실행 폴더의 설정 스냅샷·결과는 보존하며 현재 코드로 옛 실험 경로를 지원하지 않는다.
