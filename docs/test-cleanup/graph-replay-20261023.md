# 전체 그래프 오프라인 재생 기준 확보

## 범위와 결과

제품 코드·프롬프트·설정 파일은 변경하지 않았다. `tests/recorded_graph_replay.py`는
저장된 외부 응답을 공급하고 실제 `Brain.run()`과 네 책임 노드를 실행한다.
이 작업 이전부터 남아 있던 engine/screen_decision 변경을 포함한 작업 트리를 검사했다.
따라서 HEAD 0323812만의 동작이라고 해석하면 안 된다.

| 기록/조건 | 모델 요청 일치 | 행동 일치 | 노드 실행 | 결과 |
|---|---:|---:|---:|---|
| 089, 현재 코드 | 19/19 | 29/29 | 88 | 전체 재생, 원래 success 및 종료 사유 일치 |
| 089, 메모 갱신 무력화 합성 변이 | 7 | 13 | 37 | seq 200의 프롬프트 차이를 검출 |
| 088-r3, 현재 코드 | 13/14 | 24/24 후 새 행동 요청 | 66 | 과거 거절 지점을 넘어가 기록 소진, 전체 재생 아님 |
| 088-r3, 과거 확인 행동 정규화 결함의 합성 재주입 | 14/14 | 24/24 | 66 | 전체 재생, 원래 stopped 및 종료 사유 일치 |
| 086-r3 시드3, 현재 코드 | 5/23 | 7/20 | 22 | 갱신 화면 OCR 기록 누락, 부분 재생 |

089는 저장 후 재조회와 최종 확인까지 실행했으며 verified/post_save_verified가 모두
11개 입력 키를 포함했다. 이는 ERP 독립 채점을 새로 수행한 결과가 아니다.
088 합성 결함 조건의 전체 재생은 **과거 실패를 재현했다는 뜻**이지 업무 성공이 아니다.

## 실제로 실행한 경계

- `build_worker_graph`의 관찰→판단→실행→검토 라우팅과 상태 갱신을 실행한다.
  노드 반환값이나 다음 route를 미리 지정하지 않는다.
- 실제 ScreenScanner의 pHash·마커 구성·장면 결합과 실제 응답 검증·행동 준비를 실행한다.
- OCR/탐지 결과, 모델 응답, relay 관찰·행동 결과는 기록으로 대체한다.
- 모델 호출마다 purpose, 응답 타입, 스키마, 프롬프트 원문, 이미지 파일 바이트 해시를
  비교한다. 순서나 입력이 다르면 기록 응답으로 계속 진행하지 않는다.
- 행동의 생성 UUID를 제외한 대상·좌표·입력·캡처 ID 등 전체 payload를 비교한다.
  가려진 로그인 값만 고정 fake 자격값으로 대체한다. 배치 호출도 기록의 크기와 결과를 따른다.
- 모든 모델·관찰·행동·배치 큐가 소진되고 원래 종료 상태와 사유가 같아야 complete_replay다.
  모든 노드의 전후 메모와 route를 새 출력의 node-trace.json에 보관한다.
- socket 연결과 subprocess 실행을 테스트 프로세스에서 차단한다. 실제 API·ERP·GPU 워커는
  실행하지 않았다. 로컬 탐지 호출은 저장된 결과로 대체하므로 원격 모델 요청 수와 구분한다.

## 한계와 반증

원본 relay JSON이 없는 실행 직전 관찰은 기록된 ID·해시·캡처 ID와 직전 wire를 조합한다.
origin_changed가 거짓인 경우만 허용하며, 화면이 달라진 경우 저장된 갱신 캡처가 필요하다.
089의 21건, 088의 19건이 이 합성 관찰에 해당한다. 실제 Win32 창 선택, DPI/크기 변화,
포커스, 지연·타임아웃까지 재현했다고 주장하지 않는다.

현재 재생은 빈 캐시로 시작하는 콜드 기록을 대상으로 한다. 과거 웜의 메서드 저장소와
초기 캐시를 복원하는 기능은 없다. 누락된 외부 입력은 추측하여 생성하지 않는다.

088의 seq 355 계획은 과거에는 save_result_required로 거절되었다. 현재 코드는 이를
실행하려 하므로 과거 로그에 다음 행동의 결과가 없다. 이를 검사 실패로 숨기거나,
추가 행동이 성공했다는 fake를 넣지 않았다. 테스트 프로세스에서 compile_match의
normalized_acknowledgement 조건만 False로 만들어 원래 중단까지 재생한 결과와 대조했다.
이것은 과거 전체 소스를 체크아웃한 실험이 아니라 한 결함의 합성 재주입이다.

086은 seq 152의 갱신 관찰 ID `704e4d28-6c5d-4dd7-a717-ed25f22fe613`에서 현재 스캐너가
필요로 하는 visual/<ID>/ocr.json이 없다. 해당 visual 디렉터리 자체가 없다.
갱신 캡처만으로 당시 OCR 응답을 정확히 복원할 수 없으므로 중단했다.
따라서 086의 후반 계획 인계와 창 선택 결함에 대한 전체 그래프 기준은 아직 확보되지 않았다.

089에서 `_remember_model_task_evidence`를 monkeypatch로 무력화하면 seq 200의 모델
요청 문맥이 달라져 실패했다. 이 변이가 통과하거나, 입력이 다른데 기록 응답을 계속
소비하거나, 큐가 남았는데 complete_replay가 참이면 재생기의 검증 주장이 반증된다.
이번 한 변이 탐지를 전체 상태 결함의 탐지 보장으로 확대하지 않는다.

## 실행과 산출물

core 루트에서 실행하며 원본 case는 읽기만 한다. goal은 해당 cold-agents.json의
command에서 --goal 다음 값을 그대로 사용한다. 원본 task_path의 SHA256도 로그와 비교한다.

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe tests/recorded_graph_replay.py --source <case> --config <cold.toml> --output <새-출력-폴더> --goal <기록의-goal>
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
git diff --check
```

비공개 출력(커밋하지 않음):

- `.tools/graph-replay-089-05`: 이미지 비교까지 포함한 전체 정상 재생
- `.tools/graph-replay-089-mutation-06`: 메모 갱신 합성 변이와 prompt diff
- `.tools/graph-replay-088-05`: 현재 코드의 기록 소진
- `.tools/graph-replay-088-historical-07`: 과거 결함 합성 재주입
- `.tools/graph-replay-086-08`: OCR 누락 위치를 명시한 부분 재생

기본 pytest에는 비공개 기록을 포함하지 않는다. 합성 테스트 6건으로 재생기의
정상 응답 공급, 프롬프트/스키마/이미지/purpose 변경 거절, 누락 관찰의 중단을 검증한다.
실제 089 전체 재생은 로컬 기록이 있어야 별도로 실행할 수 있으며 CI 확보와 구분한다.

검증 결과: 전체 **696 passed, 59.91초**, Ruff check 통과, format 227개 파일 통과,
git diff --check 통과. 새 합성 테스트만 실행한 결과는 6 passed였다.
시간은 이번 단일 테스트 실행의 측정값이며 실제 ERP 속도 개선을 뜻하지 않는다.
