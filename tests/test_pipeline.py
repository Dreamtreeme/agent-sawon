"""Document pipeline tests use explicitly fake OCR/model outputs, never live APIs."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from brain.runtime.storage import CaseStatus, RunLog, StateStore
from models.base import MeasuredModelClient, ProviderReply, TokenUsage
from pipeline.checks import can_enter
from pipeline.normalize import container_is_valid, normalize_value
from pipeline.runner import load_inputs, prepare_case
from pipeline.schemas import DocumentInput, OcrBlock, PipelineSettings

VALUES = {
    "hbl_no": ("TEST2600001", None),
    "mbl_no": ("TESTMASTER1", None),
    "shipper": ("Synthetic Export Company", None),
    "consignee": ("Synthetic Import Company", None),
    "description": ("Steel machine parts", None),
    "packages": ("12", "CT"),
    "gross_weight_kg": ("1000", "KG"),
    "container_no": ("CSQU3054383", None),
    "pol": ("CNSHA", None),
    "pod": ("KRPUS", None),
}


class FakeOcr:
    def __init__(self, overrides=None, confidence=0.99):
        self.overrides = overrides or {}
        self.confidence = confidence
        self.calls = 0

    def read(self, page):
        self.calls += 1
        values = {**VALUES, **self.overrides.get(page.kind, {})}
        return [
            OcrBlock(
                id=index,
                text=f"{name}: {value} {unit or ''}".strip(),
                confidence=self.confidence,
                bbox=(5, 5 + index * 20, 350, 18),
            )
            for index, (name, (value, unit)) in enumerate(values.items())
        ]


class FakeMeasuredVision(MeasuredModelClient):
    def __init__(self, log, *, model_confidence=0.99, fail=False, mutate=None, usd=0):
        super().__init__(provider="replay" if not usd else "fake", model="test", run_log=log)
        self.confidence, self.fail, self.mutate, self.usd = model_confidence, fail, mutate, usd
        self.prompts = []

    def _generate(self, prompt, schema, *, images=None):
        self.prompts.append(prompt)
        assert images and len(images) == 1 and images[0].is_file()
        if self.fail:
            raise RuntimeError("Private API key SECRET should never appear in logs")
        payload = json.loads(prompt)
        fields = []
        for block in payload["ocr_blocks"]:
            name, text = block["text"].split(": ", 1)
            value, unit = (
                text.rsplit(" ", 1) if name in {"gross_weight_kg", "packages"} else (text, None)
            )
            fields.append(
                {
                    "name": name,
                    "value_text": value,
                    "unit_text": unit,
                    "evidence_block_ids": [block["id"]],
                    "model_confidence": self.confidence,
                }
            )
        if self.mutate:
            self.mutate(fields)
        return ProviderReply(
            text=json.dumps({"fields": fields}),
            usage=TokenUsage(input_tokens=10, response_tokens=10),
        )

    def _cost(self, usage):
        return Decimal(str(self.usd))


@pytest.fixture
def documents(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    result = []
    for kind in ("hbl", "invoice", "packing_list"):
        path = inputs / f"{kind}.png"
        Image.new("RGB", (400, 260), "white").save(path)
        result.append(DocumentInput(kind=kind, path=path))
    return result


def prepare(tmp_path, documents, *, ocr=None, settings=None, model_kwargs=None, reserve=None):
    captured = []

    def factory(log):
        model = FakeMeasuredVision(log, **(model_kwargs or {}))
        captured.append(model)
        return model

    result = prepare_case(
        tmp_path / "case",
        case_id="pipeline-test",
        documents=documents,
        model_factory=factory,
        ocr=ocr or FakeOcr(),
        settings=settings,
        reservation=reserve,
    )
    return result, captured


def test_normal_case_ready_with_measured_calls_and_provenance(tmp_path, documents):
    result, clients = prepare(tmp_path, documents)
    assert result.ready and result.record.origin == "extracted"
    assert can_enter(result.record, result.checks)
    assert result.record.fields.hbl_no.confidence == 0.95
    assert result.record.fields.packages.value.count == 12
    assert result.record.fields.gross_weight_kg.source.doc.startswith("originals/hbl-")
    assert result.record.fields.gross_weight_kg.source.page == 1
    assert result.record.fields.gross_weight_kg.source.bbox == (5, 125, 350, 18)
    case = tmp_path / "case"
    assert StateStore(case, case_id="pipeline-test").read().status == CaseStatus.READY
    events = RunLog(case, case_id="pipeline-test", agent_id="test").read_all()
    assert [event.seq for event in events] == list(range(len(events)))
    assert sum(event.kind == "model_call" for event in events) == 3
    assert len(clients[0].prompts) == 3
    assert all("truth" not in prompt and "TEST2600001" in prompt for prompt in clients[0].prompts)
    extracted = json.loads((case / "extracted.json").read_text(encoding="utf-8"))
    assert len(extracted["pages"]) == 3
    assert extracted["pages"][0]["fields"][0]["model_confidence"] == 0.99


@pytest.mark.parametrize(
    ("field", "change", "code", "ready"),
    [
        ("gross_weight_kg", ("1002", "KG"), "weight_within_tolerance", True),
        ("gross_weight_kg", ("1040", "KG"), "weight_outside_tolerance", False),
        ("packages", ("13", "CT"), "package_count_difference", False),
        ("description", ("Glass kitchenware", None), "description_difference", False),
        ("container_no", ("CSQU3054384", None), "invalid_container", False),
    ],
)
def test_five_discrepancy_rules_preserve_both_values(
    tmp_path, documents, field, change, code, ready
):
    ocr = FakeOcr(overrides={"packing_list": {field: change}})
    result, _ = prepare(tmp_path, documents, ocr=ocr)
    assert result.ready is ready
    matching = [check for check in result.checks.checks if check.code == code]
    assert matching
    assert matching[0].severity == ("low" if ready else "blocking")
    # Even within tolerance, values are retained and the HBL value is not rewritten.
    if field == "gross_weight_kg":
        assert result.record.fields.gross_weight_kg.value == 1000
    stored = (tmp_path / "case" / "extracted.json").read_text(encoding="utf-8")
    assert change[0] in stored
    assert StateStore(tmp_path / "case", case_id="pipeline-test").read().status == (
        CaseStatus.READY if ready else CaseStatus.FAILED
    )


def test_missing_document_blocks_before_ocr_and_model(tmp_path, documents):
    ocr = FakeOcr()
    result, clients = prepare(tmp_path, documents[:1] + documents[2:], ocr=ocr)
    assert not result.ready and not clients and ocr.calls == 0
    missing = [check for check in result.checks.checks if check.code == "missing_document"]
    assert [check.documents for check in missing] == [("invoice",)]
    assert result.known_cost_usd == 0


def test_model_self_confidence_is_recorded_but_does_not_set_final_confidence(tmp_path, documents):
    result, _ = prepare(tmp_path, documents, model_kwargs={"model_confidence": 0.01})
    assert result.ready
    assert result.record.fields.description.confidence == 0.95
    extracted = json.loads((tmp_path / "case" / "extracted.json").read_text(encoding="utf-8"))
    assert extracted["pages"][0]["fields"][0]["model_confidence"] == 0.01


def test_high_model_confidence_cannot_override_low_ocr(tmp_path, documents):
    result, _ = prepare(tmp_path, documents, ocr=FakeOcr(confidence=0.4))
    assert not result.ready
    assert result.record.fields.description.confidence == 0
    assert "low_ocr_confidence" in {check.code for check in result.checks.checks}


def test_unsubstantiated_vision_value_is_blocked(tmp_path, documents):
    result, _ = prepare(
        tmp_path,
        documents,
        model_kwargs={"mutate": lambda fields: fields[0].update(value_text="INVENTED123")},
    )
    assert not result.ready
    assert "unsupported_extraction" in {check.code for check in result.checks.checks}


def test_numeric_substring_is_not_evidence(tmp_path, documents):
    result, _ = prepare(
        tmp_path,
        documents,
        model_kwargs={"mutate": lambda fields: fields[6].update(value_text="100")},
    )
    assert not result.ready
    assert "unsupported_extraction" in {check.code for check in result.checks.checks}


def test_invalid_evidence_id_stops_once_and_preserves_ocr(tmp_path, documents):
    result, clients = prepare(
        tmp_path,
        documents,
        model_kwargs={"mutate": lambda fields: fields[0].update(evidence_block_ids=[9999])},
    )
    assert not result.ready and result.error_type == "ValueError"
    assert len(clients[0].prompts) == 1
    assert list((tmp_path / "case" / "ocr").glob("*.json"))


def test_preflight_reservation_blocks_before_any_billable_call(tmp_path, documents):
    result, clients = prepare(
        tmp_path, documents, reserve=lambda *_: Decimal("0.6"), model_kwargs={"usd": 0.1}
    )
    assert not result.ready and result.error_type == "PipelineBudgetError"
    assert len(clients[0].prompts) == 0
    assert result.known_cost_usd == 0


def test_live_client_requires_image_aware_reservation(tmp_path, documents):
    result, clients = prepare(tmp_path, documents, model_kwargs={"usd": 0.1})
    assert not result.ready and result.error_type == "PipelineBudgetError"
    assert not clients[0].prompts


def test_unknown_cost_failure_preserved_and_no_retry(tmp_path, documents):
    result, clients = prepare(
        tmp_path,
        documents,
        model_kwargs={"usd": 0.01, "fail": True},
        reserve=lambda *_: Decimal("0.1"),
    )
    assert not result.ready and result.unknown_cost
    assert len(clients[0].prompts) == 1
    assert "SECRET" not in (tmp_path / "case" / "run_log.jsonl").read_text(encoding="utf-8")


def test_cost_above_reservation_stops_further_calls(tmp_path, documents):
    result, clients = prepare(
        tmp_path, documents, model_kwargs={"usd": 0.11}, reserve=lambda *_: Decimal("0.1")
    )
    assert not result.ready and result.known_cost_usd == 0.11
    assert len(clients[0].prompts) == 1
    assert result.error_type == "PipelineBudgetError"


def test_existing_case_not_overwritten(tmp_path, documents):
    prepare(tmp_path, documents)
    original = (tmp_path / "case" / "run_log.jsonl").read_bytes()
    with pytest.raises(ValueError, match="fresh_case"):
        prepare(tmp_path, documents)
    assert (tmp_path / "case" / "run_log.jsonl").read_bytes() == original


@pytest.mark.parametrize(
    ("name", "raw", "unit", "expected"),
    [
        ("gross_weight_kg", "1,250.50", "kg", 1250.5),
        ("gross_weight_kg", "1000", "g", 1.0),
        ("gross_weight_kg", "1.5", "MT", 1500.0),
        ("gross_weight_kg", "10", "LB", 4.5359237),
        ("hbl_no", " test 123 ", None, "TEST123"),
    ],
)
def test_normalization(name, raw, unit, expected):
    assert normalize_value(name, raw, unit) == expected


@pytest.mark.parametrize(
    ("raw", "unit"),
    [("1,25", "KG"), ("1.000,5", "KG"), ("NaN", "KG"), ("-1", "KG"), ("1", None), ("1", "TON")],
)
def test_ambiguous_weight_or_unit_requires_review(raw, unit):
    with pytest.raises(ValueError):
        normalize_value("gross_weight_kg", raw, unit)


def test_container_code_checkdigit():
    assert container_is_valid("CSQU3054383")
    assert not container_is_valid("CSQU3054384")
    assert not container_is_valid("CSQA3054383")
    assert not container_is_valid("CSQU305438")


def test_manifest_only_reads_document_input_not_truth(tmp_path, documents):
    manifest = {
        "schema_version": "1.0",
        "case_id": "manifest-test",
        "documents": [
            {
                "document_type": item.kind,
                "path": item.path.name,
                "sha256": hashlib.sha256(item.path.read_bytes()).hexdigest(),
            }
            for item in documents
        ],
    }
    path = tmp_path / "inputs" / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "truth.json").write_text("malformed secret must not be read", encoding="utf-8")
    case_id, loaded = load_inputs(path)
    assert case_id == "manifest-test" and loaded == documents
    manifest["documents"][0]["path"] = "../truth.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes_manifest"):
        load_inputs(path)


def test_manifest_digest_mismatch_is_rejected(tmp_path, documents):
    path = tmp_path / "inputs" / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "case_id": "bad-digest",
                "documents": [
                    {
                        "document_type": "hbl",
                        "path": documents[0].path.name,
                        "sha256": "0" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="digest_mismatch"):
        load_inputs(path)


def test_config_defaults_load():
    settings = PipelineSettings.load(Path("config/pipeline.toml"))
    assert settings == PipelineSettings()
