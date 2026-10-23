# README 로직 대조 기록 — 2026-10-23

대조 기준은 core `38020db`, bench `1bdeba4`다. README 재작성 커밋 `21755f2`에서
확인한 사실과 검증 결과를 보존한다. 이 문서는 당시 검토 기록이며 현행 기준은
`docs/current-runtime.md`, `docs/contracts.md`, `docs/code-flow.md`다.

## 확인한 경계

- `ScreenMatch.from_plan`은 새 계획의 필드를 기존 해석보다 우선한다. 전체 ScreenMatch가
  반환되면 controls·화면 종류도 새 응답을 사용한다. 직렬화에서 제외되는 mapping은 별도로 연결한다.
- 직전 이동이 무진행이어도 현재 관찰에서 얻은 유효한 대안 계획은 Review로 전달한다.
  실패 이력과 중단 한도는 유지한다.
- ready_for_query의 단일 click + confirm_save는 navigate로 변환한다. 원문·좌표·대상은
  보존한다. confirmation_required는 변환하지 않는다.
- 대상·증거 보완 전후의 sanitize는 서로 다른 입력을 받을 수 있어 유지했다.
  observe_only는 모든 파싱·실행 계약 검증을 제거하는 설정이 아니다.
- 새 ScreenScanner.scan은 같은 캡처에서도 OCR을 수행한다. rematch와 동일 관찰 보완은
  확보된 OCR을 사용한다. 오래된 OCR 생략 설명을 연결 문서 두 곳에서 수정했다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_plan_composition.py tests/test_observation_path_equivalence.py tests/test_memory_retrieval.py tests/test_visual_markers.py -q
```

91 passed, 16.94초. 기존 전체 검사 결과는 [최종 정리 보고](cleanup-final-20261023.md)의
702 passed, Statement 87.8616%, Branch 76.7661%, bench 621 passed / 6 deselected다.
README 문서 변경 시 전체 검사를 재측정하지 않았다.

089 전체 오프라인 재생은 요청 19회·행동 29건·노드 88회 후 정상 종료다.
갱신 관찰 21건은 합성 envelope이며 실제 ERP 재실행이 아니다. 088은 과거 결함 재주입으로
원래 실패까지 재현했고, 086은 OCR 기록 누락으로 전체 재생이 미완료다.
세부 범위는 [재생 보고](graph-replay-20261023.md)에 있다.

## 빠른 시작 문서 후속 확인

기존 harness_agents.user-taught.json을 사용한 seed 0 / none / visual_only / repeat 1,
전체 예산 $3의 plan-only가 종료 코드 0으로 단일 trial을 출력했다. 출력 디렉터리는
생성되지 않았다. 제품 코드·설정 변경, 실제 ERP·relay 실행, 유료 모델 호출은 하지 않았다.

로컬 설치 조회에서 Windows 11 Pro 10.0.26200, PyTorch 2.13.0+cu130 (CUDA 13.0),
PaddlePaddle GPU 3.3.1 (CUDA 13.0, cuDNN 9.13.0)을 확인했다. 이는 확인 환경이며
다른 CUDA 조합의 지원 가능 여부를 판정한 시험은 아니다.
