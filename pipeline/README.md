# Pipeline (M2)

The pipeline rasterizes explicitly supplied PDFs/images, runs PaddleOCR locally,
then uses the measured `ModelClient.complete_json(..., images=[page])` to identify
the ten shipment fields. It does not read embedded PDF text, synthetic HTML,
truth sidecars, ERP internals, or unrelated sibling files.

```powershell
python -m pip install -e '.[pipeline,ocr]'
python -m pipeline.run --manifest ../agent-sawon-bench/cases/example/input/manifest.json --case-dir cases/document-example --model-config config/models.toml --env .env
```

Alternatively pass `--hbl`, `--invoice` and `--packing-list` explicitly. Missing
documents block the case before OCR or model calls. Each case directory must be
new: failures and append-only logs are preserved. `--env` is explicit; keys are
never printed. Use `config/pipeline.toml` to set tolerances, confidence thresholds,
document limits and cost budget. A different OCR Python may be configured with
`ocr_python`; its fixed worker runs hidden without shell interpolation.

PaddleOCR is lazy-loaded. The checked-in Windows profiles use `gpu:0` and the
existing L2C `.venv-ocr` Python 3.13 environment; change `ocr_python` for another
machine. The schema default remains CPU for portable callers. Its first initialization may
download public OCR model weights. Missing dependencies/model failures are
reported as failures, with no replacement OCR implementation. Windows Python
3.13 is listed in the [official PaddlePaddle installation guide](https://www.paddlepaddle.org.cn/documentation/docs/install/pip/windows-pip_en.html).
The adapter uses the documented v3 `predict()` / `rec_texts`, `rec_scores`,
`rec_boxes` [result format](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.md).

The measured Windows GPU preset uses PP-OCRv5 mobile detection/recognition with
PaddlePaddle GPU 3.3.1 (CUDA 13), PaddleOCR 3.7.0 and PaddleX 3.7.2. The worker
registers CUDA/cuDNN DLL directories from its own environment before importing
Paddle. `PADDLE_CUDA_BIN_DIR` / `PADDLE_CUDNN_BIN_DIR` can specify alternate
runtime directories. The CPU settings (four threads, MKL-DNN disabled following
the earlier oneDNN failure) remain available by explicitly selecting `cpu`.
GPU failures do not silently fall back to CPU. Installing the `ocr` extra alone
installs CPU Paddle; use a GPU runtime through `ocr_python` for these profiles.
`ocr_execution="subprocess"` is the default: the fixed worker is hidden and reuses
its loaded model across pages/screens while inferring each image afresh. Each
request has a configured deadline. Failure, timeout, or task completion closes
the worker and its owned descendants, including the Windows virtualenv launcher.
A request after failure starts a new worker; it does not silently retry the failed
request. Call `close()` when supplying a caller-owned OCR adapter to a pipeline.
`in_process` is an explicit option without a
hard native-call timeout. `config/pipeline.toml` uses Korean OCR by default; edit `ocr_language` for other inputs.

Fax preprocessing is configured in `config/pipeline.toml`. The optional path reads both the
original raster and a separate 3-pixel median-filtered copy. Filtering can remove
isolated noise but can also damage punctuation, so both readings retain distinct
IDs and `input_variant` labels. They are alternative OCR readings of one page,
not corroborating documents. The measured vision model receives the original
image and must cite supported text; validation thresholds remain unchanged.
`ocr_median_kernel` accepts 0 (default, off), 3 or 5; `ocr_include_original=true`
requires a nonzero kernel. Aggregate OCR resource limits cover both views.

New `ExtractedPage` records use private schema 0.2 and include OCR input paths,
hashes and identity coordinate mapping. Older 0.1 records remain readable; missing
legacy per-block variant labels remain `unspecified` rather than inventing origin.

Provider-facing extraction schemas omit large string/array upper bounds to avoid
HTTP 400 during constrained decoding; full Pydantic validation still enforces
those bounds locally. The original failed calls remain recorded with unknown cost.
The ready gate binds record values, source boxes and scores to `checks.json` by
SHA-256. Extracted records cannot use the unchecked manual-record execution path.

## Case outputs and confidence

- `originals/`: original bytes copied under generated document names.
- `pages/`: rendered image pages; source bounding boxes are **pixels in these
  rendered pages**, with dimensions recorded in `extracted.json`.
- `pages/ocr-input/`: optional filtered rasters. Original pages are retained for
  vision and review; filtering does not rotate or resize source coordinates.
- `ocr/`: OCR text, boxes and confidence, retained even if the model fails.
- `extracted.json`: per-document/page values, all source locations, raw units,
  OCR evidence, OCR confidence and separate model-reported confidence.
- `checks.json`: blocking or low-priority rule results and final confidence.
- `record.json`: `origin: extracted`, created only when all ten values exist.
  A candidate record may exist in a failed case and must not be sent to ERP.
- `run_log.jsonl`, `status.json`: existing append-only/atomic storage format.

Final confidence is a configurable **rule score, not a calibrated probability**.
Corroborating documents default to 0.95; a supported single-document field is
0.80; a weight difference inside tolerance is 0.85. OCR below 0.80, unsupported
transcription, missing fields, invalid units and contradictory values block the
case. Model self-confidence is recorded but does not raise the rule score.

Required cross-check fields default to package count/unit, gross weight and
description in all three documents. HBL values take source priority for candidate
records; disagreement is retained and never silently corrected. The weight rule
uses `max(absolute_kg, relative * max(weight_a, weight_b))`. Differences inside
the configured tolerance are low priority; outside differences block. Other
rules detect missing documents, count/unit differences, description differences,
and invalid ISO 6346 container format/category/check digit.

Only a complete record with no blocking checks transitions `extracting -> ready`.
Every other outcome transitions to `failed` / `document_review_required`.
`pipeline.checks.can_enter(record, report)` is the entry gate in addition to the
stored READY state; it does not grant approval or transmission authority.

## Budgets and evaluation

Each image call reserves configured output cost plus text/schema bytes and a
conservative image allowance (one input token per 16 pixels, minimum 8192).
No call starts if its reservation cannot fit the remaining case budget. An
unknown charge, missing measured event or charge above reservation stops further
calls. The allowance is a local bound, not a provider billing guarantee.

Tests use fake OCR and measured fake clients; they are not real extraction
accuracy measurements. The isolated evaluator accepts truth only when explicitly
requested, after extraction:

```powershell
python -m pipeline.evaluate --case-dir cases/document-example --truth ../agent-sawon-bench/cases/example/truth.json --output cases/document-example/evaluation.json
```

It reports canonical-record field accuracy, per-document field accuracy when
available, anomaly recall/false positives and correction-decision agreement.
Document-to-ERP success stays `null` unless a separately measured ERP verification
outcome is supplied to the evaluation API. D7/D8 remain proposed in private
`docs/decisions/0007-extraction-fields.md` and `0008-confidence.md`.

Document comparisons preserve punctuation. Identifier comparisons also preserve
internal spaces, matching the benchmark's published evaluation rules. Previous
pipeline comparisons incorrectly removed punctuation and identifier spaces;
older evidence remains unchanged rather than being silently rescored.

Repeated measured development campaigns run through the same bounded pipeline:

```powershell
python -m pipeline.measure --evaluation-manifest cases/evaluation-manifest.json --output cases/document-repeat-run --config config/pipeline.toml --model-config config/models.toml --env .env --repeats 3 --max-cost-usd 1
```

The manifest contains `scenario`, explicit input `manifest`, and evaluation-only
`truth` paths. Truth contents are read only after extraction returns. Each trial
gets a fresh `case-NNN/repeat-NNN` directory; failures and unstarted cancellations
remain in planned denominators. Summary 0.2 distinguishes extraction success,
correct validation, expected review/ready outcome, and all-repeats success. A
processing failure never earns credit merely because the document needed review.
Anomaly and document-field rates show coverage when truth could not be evaluated.
An unknown charge stops subsequent trials. `--repeats 1` is an explicit smoke run
and has no three-consecutive-success estimate. These runs do not measure ERP entry.

## 서류 인계 검증

`pipeline.handoff.validate_prepared_case`는 READY 상태, 추출 레코드, 검사 결과와
기존 실행 로그를 확인합니다. `brain.run --prepared-case`는 이 검증을 거쳐 동일
케이스의 ERP 입력을 시작하며 서류 추출 비용도 전체 한도에 포함합니다.
