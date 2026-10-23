# 관찰 정보 처리

OCR·화면 탐지로 만든 내부 `SceneObservation`의 필드·행 관계, 값 비교와 증거를
처리한다. relay의 원본 `Observation`은 캡처와 창 정보를 담고 `elements`는 비어 있다.
내부 장면은 relay에 전송하지 않는다.

`summary.py`는 의미 정보 요약과 민감 문자열 제거, `rows.py`는 화면상 행 관계,
`values.py`는 업무 값 비교, `evidence.py`와 `progress.py`는 증거와 진행 정보를 다룬다.
현재 화면 판단은 [화면 처리](../visual/README.md)와 [실행 흐름](../../docs/code-flow.md)을 따른다.
