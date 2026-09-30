# L2C 방식의 로컬 좌표와 의미 판단 분리

실행 경로는 relay 캡처 → PaddleOCR + 로컬 OmniParser → 중복/글자 조각 제거 →
원본 이미지 좌표의 마커 목록/번호 이미지 → 측정된 모델의 ID별 의미 분석 →
로컬 좌표 결합 → 현재 캡처와 업무 가드 검증 → relay 입력 → 새 관찰이다.

`SemanticDetection`에는 bbox 필드가 없다. 모델이 좌표를 추가하면 스키마에서 거절한다.
현재 목록에 없는 ID, 중복 ID, OCR 라벨을 입력 칸으로 바꾸는 응답도 거절한다.
이미지 밖 검출 좌표를 화면 끝으로 강제 보정하지 않는다. OCR 값과 부모 포함 관계는
기존 `bind_scene`에서 다시 검사한다. 캡처 ID, 전경, 픽셀 해시와 물리 좌표 변환은
기존 relay/brain 경계를 유지한다. UIA나 ERP 내부 데이터 접근은 추가하지 않는다.

## L2C 소스에서 확인한 내용과 적용 범위

참조 위치는 로컬 `L2C/agent/`이며 L2C 패키지를 import하지 않는다.

| 확인한 원본 | 동작 | 이식 |
|---|---|---|
| tools/omni_parser.py | YOLO 모델의 xyxy 검출, resize 역변환 | detector_worker.py, 실제 정수 resize 후 x/y 배율 각각 복원 |
| tools/ocr_engine.py | PaddleOCR+아이콘 병합, 중복 제거, 번호와 사각형 표시 | markers.py, OCR 근거 보존 및 문자 내부 오탐 제거 |
| graph/worker_execution_context.py | 현재 current_markers에서 ID를 찾아 bbox 반환 | ground_meanings에서 현재 관찰 ID만 조회 |
| graph/worker_execution_dispatch.py | click/type 요청에 marker_id 사용 | 내부 관찰 ref와 로컬 bbox 결합 후 기존 ImageTarget 경로 |
| tools/actions.py | 검출 사각형 중심과 캡처 원점으로 클릭 좌표 계산 | 기존 Screenshot.desktop_point 사용, DPI 이중 적용 방지 |
| vision/target_snapshot.py | 대상 ID/사각형/중심을 실행 근거로 기록 | markers.json, meanings.json, perception.json, 기존 행동 로그 |

L2C의 브라우저 조작, 채용 사이트 지식, 자체 데이터베이스, 직접 pyautogui 호출은
가져오지 않는다. 캐시는 기존 의미 재연결을 유지하고 과거 마커 번호나 좌표를
새 관찰의 클릭 위치로 재사용하지 않는다.

차이: L2C의 `remove_text_containers`는 OCR 글자를 감싸는 아이콘 상자를 제거한다.
데스크톱에서는 입력 칸이나 버튼 자체일 수 있으므로 여기서는 큰 컨트롤을 보존한다.
입력 칸과 표의 역할은 모델이 해석하되, 탐지기가 찾지 못한 영역을 모델이 만들어
내지는 못한다. 표/행 영역 미검출은 여전히 업무 중단 원인이 될 수 있다.

## 설정과 실행 근거

`BrainSettings.visual_detector`에 weights, python, confidence, max_dimension,
timeout_seconds를 둔다. weights 상대 경로는 brain 설정 파일 기준이다.
현재 visual 설정은 이 컴퓨터에 이미 설치된 L2C 가중치와 격리된 Python을 사용한다.
다른 컴퓨터에서는 해당 경로를 설정해야 한다. 가중치나 L2C 환경 자체는 커밋하지 않는다.
동일 환경 설치가 필요하면 optional extra `visual`을 사용한다. 자동 다운로드나
탐지 실패 후 유료 좌표 생성 대체 경로는 없다.

탐지 후보 confidence 기본값0.15는 L2C의 후보 수집 기준이다. 모델의 의미 분석
confidence 기본 문턱0.8과 다른 값이며 확률을 서로 곱하거나 동일시하지 않는다.
로컬 탐지와 의미 분석 모두 측정 ModelClient를 거친다. 로컬 탐지는 provider=local,
토큰 해당 없음(0), API비용0과 실제 지연을 기록하며 로컬 실패도 청구 미확정으로 바꾸지 않는다.
현재 worker는 관찰마다 모델을 새로 로드한다. L2C의 상주 모델과 같은 지연 성능을
달성했다고 주장하지 않으며 최적화는 실제 측정 뒤 판단한다.

시연 기록은 `python -m brain.visual.record`의 `--brain-config`로 같은 탐지 설정을 받는다.
UIA가 차단된 observation-only relay라는 기존 제한을 유지한다.

## 2026-09-30 로컬 확인

이전 실패 캡처622×476과 저장된 OCR6개를 사용했다. 로컬 OmniParser42개 후보를
검출하고 병합 후19개 마커(OCR6개, 탐지13개)를 얻었다. 모두 이미지 경계 안이다.
계정 칸 `[172,132,416,36]`, 로그인 버튼 `[174,224,108,45]`를 그림과 대조했다.
이는 업무 라벨을 코드에 넣은 것이 아니라 특정 캡처에 대한 사후 검토 결과다.
검게 가려진 비밀번호 영역은 검출되지 않았다. 일부 글자 오탐과 중첩 아이콘 후보도
남아 있어 탐지 완벽성/로그인 성공으로 집계하지 않는다.

비공개 근거: `cases/l2c-port-20260930/markers.json`, `markers.png`와 audit.json.
추가 유료 모델 호출0, 실제 화면 입력0. 새 의미 스키마의 실제 API 응답 및 전체
ERP 업무는 아직 미검증이다. 기존 청구 확인 전 유료 실행 보류를 유지한다.

## 후속: 마스킹·글자 오탐·확대 탐지 보완

비밀번호의 검은 사각형은 relay가 native password Edit의 GetWindowRect 전체를
검게 덮은 결과였다. relay는 이제 보호용 native 조회로 확인한 client 영역 전체를
단색으로 지우고 nonclient 테두리는 보존한다. 내부 경계를 확인하지 못하면 전체
불투명 마스킹을 유지한다. 이 보호 좌표는 두뇌 관찰이나 클릭 후보로 전달하지 않는다.
이 변경은 새 캡처부터 적용되며 이미 검게 저장된 이미지의 경계를 복구하지 않는다.

`visual_merge`는 높은 신뢰도의 OCR에만 높이 비례 허용 오차(최대4px)를 적용하여
글자 조각을 제거한다. 낮은 OCR 신뢰도나 글자를 감싼 더 큰 입력 칸·버튼은 보존한다.
IoU 외에 중심·면적 비율을 사용해 중첩 아이콘을 정리하되 표/작은 자식은 유지한다.

`visual_detector.refinement_enabled` 기본 true, `refinement_max_regions` 기본3,
`refinement_scale` 기본2다. 연결되는 탐지 영역이 없는 짧은 OCR 주변을 검색하되
라벨 좌표를 클릭 후보로 승격하지 않는다. 최대1회의 추가 로컬 ModelClient 호출에서
최대3영역을 확대 탐지하고 원본 좌표로 역변환한다. crop 인공 경계에 걸쳐 잘린 후보는
버린다. 확대만 반복하는 루프나 유료 fallback은 없고, 원본 후보와 탐색 영역을 기록한다.

오프라인 확인(`cases/visual-recovery-20260930/`):

- 이전 저장 캡처의 같은42개 후보에서 마커19→11로 정리했다. 계정 칸/로그인 버튼은
  유지했다. 확대 후 추가 아이콘 후보를 포함한 최종13개이며 검은 비밀번호는 여전히 미검출이다.
- 별도로 만든 합성 폼에서 전체 검은 마스크의 보호 입력 칸은 IoU0, 테두리 보존+확대는
  IoU0.91566이었다. 이 숫자는 한 합성 영역과 검출 사각형의 겹침이며 업무 성공률이 아니다.
- 합성 폼의 다른 입력 칸 미검출과 겹치는 아이콘 후보 등은 남아 있다. 일반적인 탐지 완전성은
  미검증이다. 실 ERP의 새 캡처와 실제 의미 모델/클릭 결과는 아직 확인하지 않았다.
- 로컬 detector ModelClient5회, API비용0. 실제 화면 입력0, 추가 유료 호출0.
