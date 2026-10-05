# 별도 프로덕션 작업: RunLog append 확장성

상태: **별도 작업으로 제안**, 테스트 정리 범위에서는 구현하지 않음
측정일: 2026-10-05

## 문제

`RunLog.append()`는 cross-process lock을 얻은 뒤 `_read_unlocked()`로 기존 JSONL 전체를 읽고,
모든 event의 schema, 연속 sequence와 case identity를 검증한 다음 새 event를 append하고
`fsync`한다. event가 n개인 실행에서 매 append가 이전 prefix를 다시 읽으므로 전체 비용은
O(n²)로 증가한다.

전체 테스트 계측에서 `_verify_node`와 `_review_node`만 합쳐 저장 경계가 25.689초였고, 그중
파일 재읽기와 기존 로그 parse가 18.426초였다. 테스트 전용 현상이 아니라 긴 실제 업무 로그와
느린 디스크에서도 커질 수 있는 프로덕션 성능 문제다.

## 현재 읽기의 설계 목적

현재 구현과 테스트가 보장하는 계약은 다음과 같다.

- 여러 process가 같은 case log에 append할 때 lock 안에서 다음 sequence를 결정한다.
- 마지막 줄이 잘렸거나 JSON/schema가 잘못된 로그를 덮어쓰지 않는다.
- event sequence와 case identity가 prefix 전체에서 어긋나면 append를 거부한다.
- process 재시작과 새 `RunLog` instance에서도 기존 prefix를 보존한다.

따라서 instance 메모리에 마지막 sequence만 저장하면 restart, 다른 process writer, 기존 로그
변조를 감지하지 못한다. 성능 개선은 이 무결성 수준을 먼저 결정한 뒤 진행해야 한다.

## 설계 후보

1. **Tail 검증 + 명시적 전체 audit**
   - append lock 안에서 newline 종료, 마지막 event의 sequence/case만 seek하여 검증한다.
   - 전체 prefix 검증은 `RunLog` 초기화, `read_all()` 또는 승인 전 audit에서 수행한다.
   - append는 O(1)에 가까워지지만 중간 줄 변조는 다음 전체 audit까지 늦게 발견된다.
2. **Event hash chain**
   - 각 event가 이전 canonical event hash를 포함하도록 versioned contract를 추가한다.
   - tail event 검증만으로 prefix 연결을 확인하고, 전체 audit으로 실제 prefix를 재검증한다.
   - 가장 강한 설계지만 `sawon_interfaces.RunEvent` 계약 변경과 migration이 필요하다.
3. **원자적 sidecar checkpoint**
   - 마지막 sequence, byte offset, tail hash를 log와 같은 lock 아래 갱신한다.
   - restart는 빨라지지만 log와 sidecar의 crash consistency, rollback/tamper 정책이 필요하다.

## 권장 검증

- 10, 100, 1,000, 10,000 event append의 증가율을 측정한다.
- 두 process 동시 append에서도 sequence가 유일하고 연속인지 검사한다.
- truncated tail, 중간 줄 변조, 잘못된 case/sequence, stale sidecar를 모두 거부한다.
- append 도중 process 종료 후 restart recovery를 검사한다.
- 개선 전후 전체 업무 결과와 append-only prefix bytes를 비교한다.

프로덕션 계약 결정 전에는 테스트에서 in-memory log로 우회하지 않는다. 그렇게 하면 실제
무결성·내구성 경계가 테스트에서 사라진다.
