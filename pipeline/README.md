# Pipeline (M2)

The pipeline rasterizes explicitly supplied PDFs/images, runs PaddleOCR locally,
then uses the measured `ModelClient.complete_json(..., images=[page])` to identify
the ten shipment fields. It does not read embedded PDF text, synthetic HTML,
truth sidecars, ERP internals, or unrelated sibling files.

```powershell
python -m pip install -e '.[pipeline,ocr]'
python -m pipeline.run --manifest ../agent-sawon-bench/cases/example/input/manifest.json --case-dir cases/document-example --model-config config/models.lite.toml --env .env
```

Alternatively pass `--hbl`, `--invoice` and `--packing-list` explicitly. Missing
documents block the case before OCR or model calls. Each case directory must be
new: failures and append-only logs are preserved. `--env` is explicit; keys are
never printed. Use `config/pipeline.toml` to set tolerances, confidence thresholds,
document limits and cost budget. A different OCR Python may be configured with
`ocr_python`; its fixed worker runs hidden without shell interpolation.

PaddleOCR is lazy-loaded and uses CPU by default. Its first initialization may
download public OCR model weights. Missing dependencies/model failures are
reported as failures, with no replacement OCR implementation. Windows Python
3.13 is listed in the [official PaddlePaddle installation guide](https://www.paddlepaddle.org.cn/documentation/docs/install/pip/windows-pip_en.html).
The adapter uses the documented v3 `predict()` / `rec_texts`, `rec_scores`,
`rec_boxes` [result format](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.md).

The measured Windows preset uses PP-OCRv5 mobile detection/recognition, four CPU
threads and `enable_mkldnn=false`. The installed v6 medium preset raised a oneDNN
runtime error; server detection without oneDNN was too slow for this host.
`ocr_execution="subprocess"` is the default: the fixed worker is hidden, has a
configured deadline, and timeout cleanup kills its owned descendants as well as
the Windows virtualenv launcher. `in_process` is an explicit option without a
hard native-call timeout. Use `config/pipeline.korean.toml` for Korean documents.

Provider-facing extraction schemas omit large string/array upper bounds to avoid
HTTP 400 during constrained decoding; full Pydantic validation still enforces
those bounds locally. The original failed calls remain recorded with unknown cost.
The ready gate binds record values, source boxes and scores to `checks.json` by
SHA-256. Extracted records cannot use the unchecked manual-record execution path.

## Case outputs and confidence

- `originals/`: original bytes copied under generated document names.
- `pages/`: rendered image pages; source bounding boxes are **pixels in these
  rendered pages**, with dimensions recorded in `extracted.json`.
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
