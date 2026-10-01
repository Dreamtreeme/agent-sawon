"""Truth is available only to the offline evaluator, never the extraction request."""

from __future__ import annotations

import json

import pytest
from sawon_interfaces import ShipmentRecord

from pipeline.checks import can_enter, record_fingerprint
from pipeline.evaluate import evaluate
from pipeline.schemas import FIELD_NAMES, Check, CheckReport


def record():
    fields = {
        "hbl_no": "SYNTHETIC1",
        "mbl_no": "MASTER1",
        "shipper": "Synthetic shipper",
        "consignee": "Synthetic consignee",
        "description": "Machine parts",
        "packages": {"count": 12, "unit": "CT"},
        "gross_weight_kg": 1000,
        "container_no": "CSQU3054383",
        "pol": "CNSHA",
        "pod": "KRPUS",
    }
    return ShipmentRecord(
        case_id="evaluation-test",
        origin="extracted",
        fields={
            name: {
                "value": value,
                "source": {"doc": "originals/hbl.pdf", "page": 1},
                "confidence": 0.95,
            }
            for name, value in fields.items()
        },
    )


def report(checks=None):
    return CheckReport(
        case_id="evaluation-test",
        checks=checks or [],
        field_confidences={name: 0.95 for name in FIELD_NAMES},
        record_sha256=record_fingerprint(record()),
    )


def test_pipeline_accuracy_is_not_reported_as_erp_success():
    candidate = record()
    truth = {
        "canonical_record": candidate.model_dump(mode="json"),
        "injections": [],
        "expected_needs_correction": False,
    }
    metrics = evaluate(candidate, report(), truth)
    assert metrics["field_accuracy"] == 1.0
    assert metrics["documents_to_erp_success"] is None
    assert metrics["anomaly_recall"] is None
    assert (
        evaluate(candidate, report(), truth, erp_verified_success=True)["documents_to_erp_success"]
        is True
    )


def test_error_recall_distinguishes_low_and_blocking_weight():
    candidate = record()
    truth = {
        "canonical_record": candidate.model_dump(mode="json"),
        "injections": [{"kind": "weight_within_tolerance", "expected_severity": "low"}],
        "expected_needs_correction": False,
    }
    checks = report(
        [
            Check(
                code="weight_within_tolerance",
                severity="low",
                field="gross_weight_kg",
                message="difference retained",
            )
        ]
    )
    metrics = evaluate(candidate, checks, truth)
    assert metrics["anomaly_recall"] == 1.0 and metrics["correction_decision_matches"]
    wrong = report([Check(code="weight_outside_tolerance", severity="blocking", message="wrong")])
    metrics = evaluate(candidate, wrong, truth)
    assert metrics["missed_anomalies"] == metrics["unexpected_anomalies"] == 1
    assert not metrics["correction_decision_matches"]


def test_missing_record_counts_all_fields_as_missing():
    truth = {
        "canonical_record": record().model_dump(mode="json"),
        "injections": [],
        "expected_needs_correction": False,
    }
    metrics = evaluate(None, report(), truth)
    assert metrics["field_accuracy"] == 0 and not metrics["ready"]


def test_unknown_truth_anomaly_rejected():
    truth = {
        "canonical_record": record().model_dump(mode="json"),
        "injections": [{"kind": "new_unsupported_error", "expected_severity": "blocking"}],
        "expected_needs_correction": True,
    }
    with pytest.raises(ValueError, match="unsupported_truth"):
        evaluate(record(), report(), truth)


def test_ready_gate_rejects_mismatched_confidences_and_checks():
    candidate = record()
    assert can_enter(candidate, report())
    manipulated = json.loads(candidate.model_dump_json())
    manipulated["fields"]["description"]["confidence"] = 0.9
    changed = ShipmentRecord.model_validate(manipulated)
    matching_hash = report().model_copy(update={"record_sha256": record_fingerprint(changed)})
    assert matching_hash.ready  # Both scores pass the threshold; only equality fails.
    assert not can_enter(changed, matching_hash)
    matching_scores = matching_hash.model_copy(
        update={"field_confidences": {**matching_hash.field_confidences, "description": 0.9}}
    )
    assert can_enter(changed, matching_scores)

    advisory = report([Check(code="review_note", severity="low", message="reviewed")])
    assert advisory.ready and advisory.record_sha256 == record_fingerprint(candidate)
    assert not can_enter(candidate, advisory)  # Only the attached check list differs.
    with_advisory = candidate.model_copy(
        update={"checks": [check.model_dump(mode="json") for check in advisory.checks]}
    )
    assert can_enter(with_advisory, advisory)

    blocked = report([Check(code="missing_document", severity="blocking", message="missing")])
    with_block = candidate.model_copy(
        update={"checks": [check.model_dump(mode="json") for check in blocked.checks]}
    )
    assert blocked.record_sha256 == record_fingerprint(with_block)
    assert not can_enter(with_block, blocked)
    manual = candidate.model_copy(update={"origin": "manual"})
    manual_report = report().model_copy(update={"record_sha256": record_fingerprint(manual)})
    assert not can_enter(manual, manual_report)
    assert not can_enter(candidate, report().model_copy(update={"record_sha256": None}))


@pytest.mark.parametrize("attribute", ["value", "source"])
def test_ready_gate_binds_actual_record_values_and_sources(attribute):
    candidate = record()
    manipulated = json.loads(candidate.model_dump_json())
    manipulated["fields"]["hbl_no"][attribute] = (
        "SUBSTITUTED_RECORD" if attribute == "value" else {"doc": "other.pdf", "page": 5}
    )
    assert not can_enter(ShipmentRecord.model_validate(manipulated), report())
