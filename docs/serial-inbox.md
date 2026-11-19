# 한 PC에서 Gmail과 카카오톡 순차 확인

수신자 역할의 Windows PC 한 대가 업무용 Gmail, 지정한 카카오톡 방, ERP를 차례로 사용한다. 발신자는 자료의 도착 시점을 만들고, 수신 에이전트는 실제로 도착한 자료에서 업무를 판단한다.

```text
Gmail 수집 → 카톡 수집 → 새 원문과 기존 업무 대조 → ERP 한 건 수행·결과 확인
     ↑                                                    │
     └──────────────── 다음 점검 차례 ─────────────────────┘
```

점검 시각이 ERP 입력 도중 지나면 다음 차례에 수집한다. GUI를 쓰는 동안 한 작업자가 데스크톱을 점유한다. 현재 작업 단위는 하나의 `Brain.run()` 호출이며 새 case에서 시작해 결과를 남기고 끝난다. 기존 case에는 검증된 문서 추출 결과를 인계하는 경로가 있다. 열린 ERP 편집 화면을 그대로 둔 채 그래프 중간부터 재개하는 기능은 후속 범위다. [작업자](../brain/graph/engine.py) · [CLI](../brain/run.py)

## 시험 자료와 기대 결과

| 도착 순서 | 발신자가 준비하는 합성 원문 | 수신 측에서 확인할 결과 |
| --- | --- | --- |
| 1. Gmail | 제목 ‘선적 A 등록 요청’. 본문 ‘선적 A의 포장 수량은 24박스입니다.’ 실제 ERP 검증에는 해당 업무의 다른 필수 선적값도 메일에 넣는다. | 새 선적 A와 수량 24의 출처가 메일에 연결된다. |
| 2. 카카오톡 | 같은 시험 거래처가 ‘선적 A의 포장 수량을 30박스로 정정합니다. 다른 내용은 그대로입니다.’라고 보낸다. | 같은 선적 A에 정정이 연결되고 최초 요청과 정정 원문이 모두 남는다. |

정정이 ERP 등록 전에 도착하면 최신 수량으로 등록한다. 등록 후 도착하는 조건은 기존 행 수정 경로를 별도로 시험한다. ERP 검증의 완료 기준은 재조회 결과에서 선적 A가 한 행이고 수량이 30인 것이다. 같은 수신 자료로 다시 점검하고 프로세스를 재시작해 한 번 더 점검했을 때, 새 업무·중복 등록·같은 정정의 재실행이 없어야 한다.

## 로컬 준비

수신 Gmail에서 대상 메일을 열어 **메일 다운로드**로 원본 `.eml`을 시험 전용 폴더에 저장한다. Windows 카카오톡에서는 지정한 시험방의 **대화 내보내기**로 TXT를 저장한다. 같은 파일 형식의 합성 자료로도 수집 경계를 점검할 수 있다. 수집기의 `collected_at`은 PC가 파일을 가져온 UTC 시각이다. 실제 메시지 도착 시각의 근거는 수신 측에서 별도로 남긴다. [Gmail 원본 다운로드](https://support.google.com/mail/answer/9261412?hl=ko) · [카카오톡 Windows 대화 안내](https://cs.kakao.com/helps_html/1073209354?locale=ko)

## 실제 수신함과 시험방 한 차례 읽기

`collect-live`는 한 차례의 PC 사용권을 얻어 Gmail 원문을 수집하고, 이어서 한국어 Windows 카카오톡의 지정한 방을 연다. Gmail에서는 `--mail-subject`와 제목이 일치하는 수신 메일을 읽는다. 카톡에서는 relay와 로컬 OCR로 방 창과 저장 대화상자를 찾아 대화를 임시 TXT로 내보낸다. 방 제목을 확인한 뒤 본문에 `--chat-marker`가 있는 메시지만 원문 블록 단위로 DB에 보관한다.

시작 전에 카톡에 로그인하고 `kakaotalk` 앱과 해당 창이 등록된 relay를 구동한다. 예제의 relay 포트를 실제 값으로 바꾸고, 실행 중인 relay에 대응하는 `SAWON_RELAY_TOKEN`과 로컬 OCR 설정을 준비한다. `--room-title`은 실제 창 제목이고, `--room`과 `--mailbox`는 다음 점검에도 유지할 출처 이름이다. ‘나와의 채팅’ 프로필을 통해 들어가는 시험은 `--self-chat`을 추가한다. [relay 설정 예](../examples/relay.mail-chat-erp.yaml) · [OCR 설정](../config/pipeline.toml)

```powershell
$py = '.\.venv\Scripts\python.exe'
$state = '.\cases\desk\state'
$sources = @(
  '--mailbox', 'desk', '--mail-subject', '선적 A 등록 요청',
  '--room', 'trial-supplier', '--room-title', '시험 거래처', '--chat-marker', '선적 A',
  '--relay-url', 'http://127.0.0.1:8790', '--ocr-config', '.\config\pipeline.toml'
)
& $py -m pipeline.inbox --state $state collect-live @sources --gmail-token .\cases\desk\credentials\gmail-authorized-user.json
```

Gmail 인증은 다음 두 방식 중 하나를 사용한다.

- **로컬 OAuth** — 수신 계정이 `gmail.readonly` 권한을 승인해 발급한 authorized-user JSON을 `--gmail-token`으로 전달한다. 수집기는 Gmail API의 `users.messages.list` 검색 결과를 끝까지 확인하고 각 메일을 `users.messages.get(format=raw)`로 가져온다. [Gmail 목록 조회](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list) · [Gmail 원문 조회](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get)
- **인증된 커넥터 호스트** — 호스트가 매 점검마다 같은 수신함·제목 조건으로 새로 검색하고 각 ID의 `raw` 응답을 읽는다. 호스트가 JSON 배열 `[{"id":"<Gmail 메시지 ID>","raw":"<base64url 원문>"}]`을 표준 입력으로 전달할 때 위 명령에서 `--gmail-token <경로>` 대신 `--gmail-raw-stdin`을 쓴다. 검색 결과가 없으면 `[]`을 전달한다. CLI는 이 경우 호스트가 넘긴 원문에서 메일 제목을 대조한다.

같은 상태 경로와 조건으로 두 번 실행한다. 각 출처의 `read`는 이번에 읽은 대상 건수, `source_key_hashes`는 식별자의 해시, `raw_sha256`은 이번에 읽은 원문 바이트의 해시, `new_receipt_ids`는 새로 저장된 행이다. 두 번째 점검에서 같은 식별자와 원문이 읽히고 추가 행이 0건인지 확인한다. Gmail 저장 후 카톡 단계가 중단되면 이미 저장된 메일은 유지되며 다음 점검이 이어받는다. 로컬 `collect`의 카톡 이력은 방 전체 스냅샷이고 `collect-live`의 카톡 이력은 개별 메시지다. 두 방식의 카톡 행 개수는 별도로 비교한다.

## 한 차례 실행

한 PC에서는 아래 명령에 같은 `--state` 경로를 사용한다. 이 경로의 잠금은 수집, 계획 기록, 작업자 한 건이 겹치는 것을 막는다. 별도 실행한 bench나 일반 GUI 프로그램까지 통제하지는 않는다. 입력 폴더에는 대상 메일만 두고, 한 방의 내보내기 파일을 늘 같은 `--room` 이름으로 연결한다.

```powershell
$py = '.\.venv\Scripts\python.exe'
$state = '.\cases\desk\state'
& $py -m pipeline.inbox --state $state collect --mail-dir .\cases\desk\mail --mailbox desk --chat-export .\cases\desk\room.txt --room 시험거래처
& $py -m pipeline.inbox --state $state pending
```

첫 방 내보내기는 기본적으로 판단 대상이다. 시험 시작 전 기존 대화를 기준으로만 저장하려면 첫 수집에 `--baseline-chat`을 붙인다. 이후 새로 뒤에 붙은 줄만 전달하며, 과거 어느 시점과 완전히 같은 파일은 동일 자료 재수집으로 처리한다. 이전 기록과 이어지지 않는 내보내기에는 `review`를 표시하며 원문을 함께 보존한다. 이 경계에서 읽는 단위는 메일 한 통 또는 방 내보내기 한 버전이다.

### 모델로 다음 업무 계획하기

`collect` 또는 `collect-live`가 끝난 뒤 같은 상태 경로에서 `plan-next`를 실행한다. 모델은 미처리 원문을 기존 업무의 최신 계획과 대조한다. 비교 대상에는 실행 전 계획과 작업자가 정상 종료한 계획이 들어간다. 미처리 원문이 없으면 모델 호출 없이 끝난다. 모델이 추가 자료를 기다리기로 하면 원문을 미처리 상태로 남긴다.

```powershell
& $py -m pipeline.inbox --state $state plan-next --env-path .\.env
& $py -m pipeline.inbox --state $state plans
```

`--model-config`의 기본값은 `config/models.toml`이고 `--max-cost-usd`의 기본값은 3달러다. 유료 공급자를 사용할 때는 `--env-path`로 키가 있는 파일을 지정한다. [모델 설정](../config/models.toml) · [모델 클라이언트](../models/factory.py)

계획에는 업무 목표와 원문에서 읽은 사실을 기록한다. 각 사실의 `key`, `value`, `receipt_id`를 보면 어떤 원문이 그 값을 제공했는지 알 수 있다. 선적 A의 수량을 24에서 30으로 고치는 카톡이 도착하면, 정정 계획은 같은 업무 ID에서 `supersedes_plan_id`로 이전 버전을 가리킨다. 수량의 출처는 정정 카톡으로 바뀌고, 나머지 사실은 이전 출처와 함께 이어진다. 최초 등록을 실행한 버전에는 당시 종료 상태와 작업자 기록이 남는다.

작업 요청이 없는 단독 참고 자료는 `reference`로 기록되어 미처리 목록에서 빠진다. 이때 `plan_id`는 `null`이다. 다른 업무가 미처리로 남아 있으면 같은 상태 경로에서 `plan-next`를 다시 실행한다.

운영자가 직접 계획을 작성할 때는 `record-plan`을 사용한다. 예를 들어 미처리 원문 ID가 1이면 `decision.json`에 `{"work_id":"선적 A","goal":"선적 A를 수량 24로 등록하고 재조회한다"}`를 저장한 뒤 다음과 같이 기록한다.

```powershell
& $py -m pipeline.inbox --state $state record-plan --receipts 1 --file .\cases\desk\decision.json
```

### 계획을 ERP 작업자에게 전달하기

계획 사실을 입력으로 쓸 때는 `worker --from-plan`에 계획 번호, ERP 교육 파일과 새 실행 경로를 지정한다. 아래 예시에서 `$relay`는 실행 중인 ERP 전용 relay 주소, `$task`는 해당 회사의 ERP 입력 절차와 입력 키를 선언한 JSON 경로다. 로그인 정보는 기존 작업자의 실행 환경에서 공급한다.

같은 선적을 여러 날에 걸쳐 처리할 때는 core 루트에서 `& ..\agent-sawon-bench\scripts\start_mock_erp.ps1`로 ERP를 열고 이 인스턴스에 relay를 연결한다. 수신 원문·계획은 같은 `--state`에, 선적과 저장 번호는 bench의 `data/erp.db`에 누적된다. 각 작업자의 `--case-dir`은 매번 새로 지정해 그때의 화면과 로그를 보존한다. 독립 채점용 하네스는 실행별 DB를 사용한다.

```powershell
& $py -m pipeline.inbox --state $state worker --plan 1 --from-plan -- `
  --task $task --case-dir .\cases\desk\erp-01 --relay-url $relay `
  --config .\config\brain.visual.toml --model-config .\config\models.toml `
  --visual-ocr-config .\config\pipeline.toml --env-path .\.env --max-cost-usd 3
```

입력 키와 계획 사실 키가 같으면 바로 연결한다. 명칭이 다른 입력은 모델이 교육의 필드 이름을 보고 기존 사실 키를 고른다. 코드는 선택된 원래 값과 출처를 복사하고 교육의 숫자·단위 형식을 적용할 수 있는지 확인한다. `<state>/worker-inputs/<실행 ID>/input-provenance.json`에는 원문 값과 실행값이 나란히 남는다. 작업자 case의 `record.json`에는 같은 입력과 사실별 원문 ID가 보존된다. 미리 만든 레코드를 전달할 때는 기존 `worker --plan <ID> -- --record ...` 형식을 쓴다.

입력 결합이 끝난 뒤 계획을 `started`로 기록하고 작업자를 호출한다. 정상 복귀하면 종료 코드를 기록한다. 중단돼 `started`가 남은 계획은 `plans`의 case 경로와 실제 저장 결과를 대조해 후속 계획을 정한다. ERP 저장 성공은 독립 채점과 재조회로 확인한다. [작업자 CLI](../brain/run.py)

등록 후 포장 수량 정정까지 처리하는 시험에서는 같은 작업자 명령에 두 교육을 지정한다. `$planId`에는 이번 `plan-next`가 반환한 계획 번호를 넣고 매 실행마다 새 `$caseDir`을 사용한다.

```powershell
& $py -m pipeline.inbox --state $state worker --plan $planId --from-plan `
  --after-dispatch-task .\examples\shipment-quantity-correction-task.json -- `
  --task $task --case-dir $caseDir --relay-url $relay `
  --config .\config\brain.visual.toml --model-config .\config\models.toml `
  --visual-ocr-config .\config\pipeline.toml --env-path .\.env --max-cost-usd 3
```

처음 등록하거나 아직 등록을 실행하기 전에 정정이 오면 기본 교육을 쓴다. 앞선 버전에 정상 종료 이력이 있으면 정정 교육으로 House B/L을 조회해 기존 한 행의 수량을 바꾸고 같은 건을 다시 연다. 이 예제 교육의 수정 대상은 포장 수량이다. 다른 종류의 정정은 해당 업무용 교육이 필요하다. [포장 수량 정정 교육](../examples/shipment-quantity-correction-task.json)

주기 실행과 별도 발신 계정에서 보낸 메시지의 실제 도착은 별도 검증 대상이다.

같은 거래처의 미전송 선적 두 건을 열어 둔 상태에서 신규 메일, 수량 정정, 참고 카톡을 함께 처리하는 시험이 있다. 업무 연결, 실제 ERP 쓰기, 재조회와 중복 수집을 각각 채점한다. [시험 원문과 실행 조건](../tests/fixtures/mixed_inbox/README.md) · [합격 기준](../tests/fixtures/mixed_inbox/RUBRIC.md)

## 실제 계정으로 수신 조건 준비

1. 수신 PC에 시험용 Gmail과 카카오톡을 로그인한다. 발신에는 수신자와 구별되는 별도의 시험 계정을 사용한다. 두 카카오 계정만 참여하는 비공개 일반 채팅방을 만들고 수신 PC에서 방과 발신자 표시를 확인한다.
2. 발신 기기에서 메일의 받는 주소를 확인하고 Gmail의 **보내기 예약**을 선택한다. 시간대는 Asia/Seoul로 맞춘다. Google은 실제 발송이 예약 시각보다 몇 분 늦을 수 있다고 안내한다. [Gmail 예약 발송](https://support.google.com/mail/answer/9214606?co=GENIE.Platform%3DDesktop&hl=ko)
3. 발신자의 같은 시험방에서 **예약 메시지**로 정정 내용을 나중 시각에 등록한다. 수신 PC에서는 발신 계정으로 창을 전환할 필요가 없다. 예약 시각, 실제 도착 시각, 에이전트가 본문을 읽은 시각을 구분해 기록한다. [카카오톡 예약 메시지](https://pc.kakao.com/talk/notices/ko/2731?agent=win32)
4. 처음에는 지정한 방 하나만 확인한다. 수신 메일의 발신 주소·원문과 카톡방의 발신자·본문을 대조하고, ERP 저장 결과는 독립적으로 재조회한다. 기존 합성 자료를 실제 계정에서 읽은 검증과, 별도 계정에서 방금 보낸 메시지가 도착한 검증은 따로 기록한다.

발신 기기를 코드로 구성할 때 Gmail은 발신 계정이 승인한 OAuth 권한으로 `messages.send`를 사용할 수 있다. 카카오톡 친구 메시지 API에는 같은 서비스에 가입한 사용자, 동의·권한, 수신자 선택과 템플릿 조건이 있다. 개인 카톡방 대화 이력을 가져오는 공개 API는 제공되지 않는다. 일반 사용자 간 대화 수신을 검증할 때는 별도 발신 계정의 예약 메시지가 준비하기 쉽다. [Gmail API](https://developers.google.com/workspace/gmail/api/guides/sending) · [카카오 메시지 API](https://developers.kakao.com/docs/ko/kakaotalk-message/common) · [카카오 개발팀의 대화 조회 안내](https://devtalk.kakao.com/t/api/135418)
