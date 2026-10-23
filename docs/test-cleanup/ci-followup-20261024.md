# 저장소 경계와 CI 후속 검증 · 2026-10-24

## 범위와 변경

화면 파일럿 대신 중단 전 남아 있던 CI 결함을 먼저 처리했다. 제품의 판단·실행·검토
로직, 프롬프트, 모델 설정은 변경하지 않았다.

- bench `5ae931f`: 공개 계약 패키지의 Pydantic 의존성을 2.13.5로 고정했다.
  로컬 2.13.5와 기존 CI 2.14.1을 각각 로딩해 생성 스키마를 비교했다.
  `ShipmentRecord`의 중량 Generic 정의 이름·title·참조 3곳에서
  `_PydanticGeneralMetadata`와 `AllowInfNan` 표현이 달랐다. 필드 제약은 같았다.
  기존 스키마를 새 출력으로 덮거나 비교를 완화하지 않았다. 버전 업그레이드에는
  export 및 core fixture 차이 검토가 필요함을 interfaces/README.md에 기록했다.
- core `515f044`: CI가 bench 기본 브랜치를 임의로 가져오지 않고
  `5ae931f4edce3be4afa55c3023a6ee97a1b8cdc0`을 두 job에서 공통 참조한다.
  현재 공개 계약 0.5.0을 사용하는 양쪽 커밋을 함께 검증한다.
- `tests/test_memory_retrieval.py`: 흰색 이미지의 pHash 숫자 상수를 없애고 동일한
  합성 이미지에서 실제 함수로 기준 해시를 생성한다. 모델·OCR·화면을 실제로
  호출하지 않는다. 미일치 시 해시 비트를 반전하는 음성 사례와 재결합·호출 수
  단언은 그대로 남는다. 이는 라우팅 테스트의 플랫폼 의존성 제거이며 pHash의
  플랫폼 간 비트 동일성을 보장하는 수정은 아니다.

## 로컬 검증

| 검사 | 결과 |
|---|---|
| core 관련 기억 인출 테스트 | 29 passed |
| bench 계약·네이티브 초기화 테스트 | 109 passed |
| core 전체 pytest | 703 passed, 46.49초 |
| bench 전체 pytest | 536 passed, GUI 1건 제외, 14.14초 |
| 양쪽 Ruff lint / format 및 diff --check | 통과 |

## 판단의 한계와 반증 조건

실제 ERP·유료 모델 호출은 하지 않았다. 과거 실행의 결과는 변경하지 않았다.
정상 이미지에서 기억 경로를 못 고르거나 미일치 이미지에서도 기억 경로를 고르면
유지한 양성·음성 테스트가 실패해야 한다. 새 의존성 설치 시 스키마가 달라지면 기존
바이트 비교가 실패해야 한다. 원격 CI 결과를 실제 화면 성공으로 해석하지 않는다.

## 원격 CI 확인

- [core 39993647644](https://github.com/Dreamtreeme/agent-sawon/actions/runs/39993647644):
  `515f04447880e3debed671b1f6efc2db61c36d3c`, 모든 job success.
  Linux 순차 701 passed / Windows 전용 2 skipped (16.57초), Windows 703 passed
  (64.10초), Linux 4-worker 701 passed / 2 skipped (17.19초).
- [bench 39993586781](https://github.com/Dreamtreeme/agent-sawon-bench/actions/runs/39993586781):
  `5ae931f4edce3be4afa55c3023a6ee97a1b8cdc0`, 모든 job success.
  Linux 525 passed / 환경별 6 skipped / GUI 1 deselected (6.84초), .NET 도메인 34 passed,
  Windows 앱 빌드 및 VM 스크립트 테스트 10 passed. Windows/Linux의 VM 테스트
  매개변수 구성이 달라 수집 건수 차이를 삭제된 테스트로 해석하지 않는다.

양쪽 기능 변경 커밋과 앞선 정리 커밋을 각각 작업 브랜치에 push했다. main 병합은 하지 않았다.
보고서만 추가하는 커밋은 검증된 코드와 동일하며 CI를 중복 실행하지 않는다.

남은 별도 조사 후보는 091의 semantic_target_repeated 이후 현재 계획 유실이다.
이번 CI 작업에서 수정하거나 해결됐다고 판정하지 않았다. 086의 원문 관찰 부재도 그대로다.
