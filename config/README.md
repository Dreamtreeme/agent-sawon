# 실행 설정 선택

설정 파일은 비교 조건과 과거 실행의 해시에 연결된다. 이름이 오래됐거나 내용이 비슷하다는
이유로 합치거나 삭제하지 않는다. 새 비교는 사용할 파일과 해시를 별도 계획에 고정한다.

| 목적 | 설정 |
|---|---|
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

`models.toml`/`models.lite.toml`과 `pricing.toml`은 비용 기록에 사용한다. 과거 모델의
가격 항목은 당시 설정을 재현하기 위해 보존한다. 여기에 API 키를 넣지 않는다.

반복 화면 실행의 전경 인계는 벤치 CLI `--desktop-handoff`에서 선택하며 계획에 남긴다.
이 옵션은 모델·캐시 설정 파일이나 relay 허용 목록을 대신하지 않는다.
