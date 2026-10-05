# 별도 프로덕션 작업: RunLog append 확장성

상태: **후속 프로덕션 개선 구현·로컬 및 CI 검증 완료**, `codex/runlog-scaling-ci-coverage`
측정일: 2026-10-05

## 개선 전 문제

`RunLog.append()`는 cross-process lock을 얻은 뒤 `_read_unlocked()`로 기존 JSONL 전체를 읽고,
모든 event의 schema, 연속 sequence와 case identity를 검증한 다음 새 event를 append하고
`fsync`한다. event가 n개인 실행에서 매 append가 이전 prefix를 다시 읽으므로 전체 비용은
O(n²)로 증가한다.

전체 테스트 계측에서 `_verify_node`와 `_review_node`만 합쳐 저장 경계가 25.689초였고, 그중
파일 재읽기와 기존 로그 parse가 18.426초였다. 테스트 전용 현상이 아니라 긴 실제 업무 로그와
느린 디스크에서도 커질 수 있는 프로덕션 성능 문제다.

## 기존 전체 읽기의 설계 목적

현재 구현과 테스트가 보장하는 계약은 다음과 같다.

- 여러 process가 같은 case log에 append할 때 lock 안에서 다음 sequence를 결정한다.
- 마지막 줄이 잘렸거나 JSON/schema가 잘못된 로그를 덮어쓰지 않는다.
- event sequence와 case identity가 prefix 전체에서 어긋나면 append를 거부한다.
- process 재시작과 새 `RunLog` instance에서도 기존 prefix를 보존한다.

따라서 instance 메모리에 마지막 sequence만 저장하면 restart, 다른 process writer, 기존 로그
변조를 감지하지 못한다. 성능 개선은 이 무결성 수준을 먼저 결정한 뒤 진행해야 한다.

## 적용한 설계

사용자의 후속 구현 요청에 따라 테스트 정리와 별도 브랜치에서 변경했다. 기존 모듈의
협력 작성자 계약(동일 sidecar lock 사용, 로그 직접 편집 금지)을 기준으로, 검증된 파일의
device/inode/size/mtime_ns/ctime_ns와 다음 sequence만 인스턴스 메모리에 보관한다.

- 같은 lock 안에서 파일 정보가 일치할 때만 전체 재독을 생략한다.
- 최초 append, 새 인스턴스·프로세스, 파일 변경·교체, 다른 작성자 뒤에는 전체 검증한다.
- 새 event의 schema 검증, append-only 쓰기, flush/fsync는 그대로 수행한다.
- 쓰기 전에 캐시를 무효화하고 성공적으로 닫힌 뒤에만 갱신한다. 실패 후에는 다시 전체
  검증하므로 디스크에 기록됐지만 fsync가 실패한 event의 sequence를 재사용하지 않는다.
- `read_all()`은 항상 전체를 검증하며 캐시도 무효화한다.
- `verify_each_append=True`로 기존 매번 전체 검증을 선택할 수 있다.

정상 단일 인스턴스의 연속 append에서 prefix read/parse를 제거한다. 여러 작성자가 매번
교대하거나 인스턴스를 매번 새로 만들면 전체 재검증 비용은 남는다. 로그 format과 기존
prefix bytes, 프로세스 간 lock, RunEvent 계약은 변경하지 않았다.

파일 metadata는 암호학적 무결성 증명이 아니다. lock을 무시하는 작성자가 내용을 바꾸고
metadata까지 동일하게 복원하는 경우 빠른 append는 이를 보장해 탐지하지 못한다. 이런
환경에서는 strict 옵션 또는 전체 audit을 사용해야 한다. hash chain도 prefix를 읽지 않는
한 중간 줄의 실제 변조를 즉시 확인할 수 없으므로 이번 변경에 추가하지 않았다.

## 검토했으나 이번에 적용하지 않은 설계

1. **Tail 검증 + 명시적 전체 audit**
   - append lock 안에서 newline 종료, 마지막 event의 sequence/case만 seek하여 검증한다.
   - 전체 prefix 검증은 `RunLog` 초기화, `read_all()` 또는 승인 전 audit에서 수행한다.
   - append는 O(1)에 가까워지지만 중간 줄 변조는 다음 전체 audit까지 늦게 발견된다.
2. **Event hash chain**
   - 각 event가 이전 canonical event hash를 포함하도록 versioned contract를 추가한다.
   - tail event 검증만으로 prefix 연결을 확인하고, 전체 audit으로 실제 prefix를 재검증한다.
   - 연결 기록을 제공하지만 실제 prefix 변조 탐지는 전체 audit이 필요하며,
     `sawon_interfaces.RunEvent` 계약 변경과 migration이 필요하다.
3. **원자적 sidecar checkpoint**
   - 마지막 sequence, byte offset, tail hash를 log와 같은 lock 아래 갱신한다.
   - restart는 빨라지지만 log와 sidecar의 crash consistency, rollback/tamper 정책이 필요하다.

## 검증 범위

- 10, 100, 1,000, 10,000 event append의 증가율을 측정한다.
- 두 process 동시 append에서도 sequence가 유일하고 연속인지 검사한다.
- truncated tail, 중간 줄 순번 손상, 잘못된 case/sequence, 같은 크기 파일 교체를 검사한다.
  checkpoint sidecar는 도입하지 않았으므로 stale checkpoint 검사는 해당하지 않는다.
- append 도중 process 종료 후 restart recovery를 검사한다.
- 개선 전후 전체 업무 결과와 append-only prefix bytes를 비교한다.

이번 테스트도 in-memory log로 우회하지 않는다. 실제 파일·lock·fsync와 다중 프로세스를 사용한다.

## append 확장성 실측

Windows/Python 3.13, 기존 main `da354b3`의 storage 소스를 별도 module로 로드해 개선 코드와
비교했다. 각 크기는 새 임시 디렉터리·단일 RunLog 인스턴스에서 실행했다. synthetic payload는
index 한 개이며 각 append는 실제 flush/fsync를 포함한다. 각 크기에서 기존→개선 순서로 1회씩
실행했고 측정끼리는 겹치지 않았다. 시간이 아닌 누적 read_bytes도 계측해 재독 제거를 확인했다.
마지막 `read_all()`로 전체 sequence를 확인했고 이 최종 audit은 append 시간에서 제외했다.

| event 수 | 기존 시간 | 개선 시간 | 기존 prefix 재읽기 | 개선 prefix 재읽기 |
|---|---:|---:|---:|---:|
| 10 | 0.083초 | 0.011초 | 10,260 bytes | 0 |
| 100 | 0.842초 | 0.093초 | 1,136,610 bytes | 0 |
| 1,000 | 13.449초 | 1.350초 | 115,674,210 bytes | 0 |
| 10,000 | 572.777초 | 24.818초 | 11,677,641,210 bytes | 0 |

원시값은 `runlog-scaling-measurements.json`에 있다. 10,000건 단일 실측은 약 23.08배지만
파일 캐시·디스크 상태를 통제한 반복 중앙값이 아니며, ERP 전체 지연 개선율도 아니다.
검증 가능한 구조적 개선은 정상 연속 append의 기존 prefix 읽기가 0회라는 것이다.

전체 suite는 **852 passed in 108.43s**, Ruff check/format도 통과했다. 이번 8개 회귀 case는
strict/fast 경로의 재독 비용과 교대 작성자, 중간 손상·교체·잘림, fsync 실패 후 재시도,
프로세스 강제 종료 후 정상/부분 tail 복구를 다룬다. 기존 동시 3-process append 테스트도 유지한다.
`--cov=brain/runtime --cov-branch` 측정에서 storage.py 문장 148/148, 분기 21/22를 실행했다.
미실행 분기는 기존 replace retry loop의 exit이며, 새 append 경로의 누락 문장은 없다.
이 시간은 coverage 계측을 포함하므로 이전 일반 suite 시간과 직접 속도 비교하지 않는다.

최초 `--cov=brain.runtime.storage` 실행은 커버리지 수집 시 import 순환으로 Pydantic 모듈
조회에 실패해 테스트 실행 전 중단됐다. 디렉터리 대상을 지정한 위 명령으로 전체 검증했다.

## CI 검증 공백 해결

CI unit을 Ubuntu/Windows matrix로 바꾸고 순차 기본값을 유지했다. 별도 Ubuntu n4 job도
유지하며 모든 job에 `opencv-python-headless` 설치와 실제 import 검사를 추가했다.
테스트 결과 XML은 job별 artifact로 남긴다.

첫 Windows 실행에서 테스트 두 곳이 UTF-8 JSON의 encoding을 생략해 CP1252 decode 오류로
실패했다. 전역 UTF-8 강제로 가리지 않고 해당 두 read_text에 UTF-8을 명시했다.

수정 코드 `6db348a`의 [실제 CI 37274815048](https://github.com/Dreamtreeme/agent-sawon/actions/runs/37274815048)은
Ubuntu 순차·Windows 순차·Ubuntu 병렬 모두 성공했다. 내려받은 JUnit으로 확인한 결과:

- Windows: 852 passed, skip 0, JUnit 104.439초.
- Ubuntu 순차: 850 passed, Windows 전용 2 skipped, JUnit 29.950초.
- Ubuntu 병렬: 850 passed, Windows 전용 2 skipped, JUnit 40.113초.
- OpenCV 사각형 인식 4개는 양 OS 모두 실제 통과했다.
- Windows의 열린 reader replace와 응답 파일 publication 테스트 2개도 실제 통과했다.

이번 작업은 ERP 실화면·유료 모델 재실행을 포함하지 않으며 M1/M2 완료 상태를 변경하지 않는다.
