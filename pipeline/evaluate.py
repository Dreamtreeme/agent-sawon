"""Offline metrics using explicitly supplied truth, never imported by extraction.

Usage: python -m pipeline.evaluate --case-dir cases/prepared --truth path/truth.json
An ERP outcome is absent unless separately measured and supplied by the caller.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sawon_interfaces import PackageCount, ShipmentRecord

from .checks import equivalent
from .schemas import FIELD_NAMES, CheckReport, ExtractionBundle

ANOMALY_CODES = frozenset(
    {
        "missing_document",
        "weight_within_tolerance",
        "weight_outside_tolerance",
        "package_count_difference",
        "description_difference",
        "invalid_container",
    }
)


def evaluate(
    record: ShipmentRecord | None,
    checks: CheckReport,
    truth: dict,
    *,
    erp_verified_success: bool | None = None,
    extracted: ExtractionBundle | None = None,
) -> dict:
    expected = ShipmentRecord.model_validate(truth["canonical_record"])
    field_matches = {
        name: record is not None
        and equivalent(
            name, getattr(record.fields, name).value, getattr(expected.fields, name).value
        )
        for name in FIELD_NAMES
    }
    expected_anomalies = {(item["kind"], item["expected_severity"]) for item in truth["injections"]}
    if any(code not in ANOMALY_CODES for code, _ in expected_anomalies):
        raise ValueError("unsupported_truth_anomaly")
    observed = {
        (check.code, check.severity) for check in checks.checks if check.code in ANOMALY_CODES
    }
    matched = expected_anomalies & observed
    needs_correction = any(check.severity == "blocking" for check in checks.checks)
    document_matches = {}
    if extracted is not None:
        for kind, values in truth.get("document_values", {}).items():
            observed_fields = [
                field for page in extracted.pages if page.kind == kind for field in page.fields
            ]
            for name in FIELD_NAMES:
                target = (
                    PackageCount(count=values["packages.count"], unit=values["packages.unit"])
                    if name == "packages"
                    else values[name]
                )
                found = [field.value for field in observed_fields if field.name == name]
                document_matches[f"{kind}.{name}"] = bool(found) and all(
                    equivalent(name, value, target) for value in found
                )
    return {
        "schema_version": "0.1",
        "case_id": checks.case_id,
        "field_count": len(FIELD_NAMES),
        "matched_fields": sum(field_matches.values()),
        "field_accuracy": sum(field_matches.values()) / len(FIELD_NAMES),
        "field_matches": field_matches,
        "document_field_matches": document_matches,
        "document_field_accuracy": (
            sum(document_matches.values()) / len(document_matches) if document_matches else None
        ),
        "expected_anomalies": len(expected_anomalies),
        "detected_anomalies": len(matched),
        "missed_anomalies": len(expected_anomalies - observed),
        "unexpected_anomalies": len(observed - expected_anomalies),
        "anomaly_recall": len(matched) / len(expected_anomalies) if expected_anomalies else None,
        "correction_decision_matches": needs_correction == truth["expected_needs_correction"],
        "ready": checks.ready and record is not None,
        "erp_verified_success": erp_verified_success,
        "documents_to_erp_success": (
            checks.ready
            and record is not None
            and all(field_matches.values())
            and erp_verified_success
            if erp_verified_success is not None
            else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    record_path = args.case_dir / "record.json"
    record = (
        ShipmentRecord.model_validate_json(record_path.read_bytes())
        if record_path.exists()
        else None
    )
    checks = CheckReport.model_validate_json((args.case_dir / "checks.json").read_bytes())
    extracted_path = args.case_dir / "extracted.json"
    extracted = (
        ExtractionBundle.model_validate_json(extracted_path.read_bytes())
        if extracted_path.is_file()
        else None
    )
    metrics = evaluate(
        record, checks, json.loads(args.truth.read_text(encoding="utf-8")), extracted=extracted
    )
    if args.output:
        from brain.runtime.storage import atomic_write_json

        atomic_write_json(args.output, metrics)
    print(json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
