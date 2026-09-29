"""Cross-document rules; conflicting documents remain blocked, never repaired."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from itertools import combinations

from sawon_interfaces import PackageCount, ShipmentRecord

from .normalize import container_is_valid, normalized_text
from .schemas import (
    FIELD_NAMES,
    Check,
    CheckReport,
    ExtractedField,
    ExtractionBundle,
    FieldName,
    PipelineSettings,
)


def equivalent(name: FieldName, first, second) -> bool:
    if isinstance(first, PackageCount) or isinstance(second, PackageCount):
        return first == second
    if isinstance(first, str) and isinstance(second, str):
        if name in {"hbl_no", "mbl_no", "container_no", "pol", "pod"}:
            return (
                unicodedata.normalize("NFKC", first).strip().upper()
                == unicodedata.normalize("NFKC", second).strip().upper()
            )
        return normalized_text(first) == normalized_text(second)
    return first == second


def cross_validate(
    bundle: ExtractionBundle, settings: PipelineSettings
) -> tuple[CheckReport, ShipmentRecord | None]:
    checks: list[Check] = []
    sources = {**bundle.documents, **{page.document: page.kind for page in bundle.pages}}
    kinds = set(sources.values())
    indexed: dict[FieldName, list[ExtractedField]] = {name: [] for name in FIELD_NAMES}
    for page in bundle.pages:
        for item in page.fields:
            indexed[item.name].append(item)
    for kind in settings.required_documents:
        if kind not in kinds:
            checks.append(
                Check(
                    code="missing_document",
                    severity="blocking",
                    documents=(kind,),
                    message="A required document is missing; collect it before ERP entry.",
                )
            )
        else:
            for name in settings.shared_required_fields:
                if not any(sources[item.source.doc] == kind for item in indexed[name]):
                    checks.append(
                        Check(
                            code="missing_crosscheck_field",
                            severity="blocking",
                            field=name,
                            documents=(kind,),
                            message="A required cross-check field was not extracted.",
                        )
                    )

    confidences: dict[FieldName, float] = {}
    candidate_fields = {}
    for name in FIELD_NAMES:
        items = indexed[name]
        valid = [item for item in items if item.value is not None]
        if not valid:
            checks.append(
                Check(
                    code="missing_field",
                    severity="blocking",
                    field=name,
                    message="No usable source value exists for this field.",
                )
            )
            confidences[name] = 0.0
            continue
        for item in items:
            if item.normalization_error:
                checks.append(
                    Check(
                        code="invalid_value",
                        severity="blocking",
                        field=name,
                        documents=(item.source.doc,),
                        message=item.normalization_error,
                    )
                )
            if not item.evidence_supported:
                checks.append(
                    Check(
                        code="unsupported_extraction",
                        severity="blocking",
                        field=name,
                        documents=(item.source.doc,),
                        message="OCR evidence does not support the value.",
                    )
                )
            if item.ocr_confidence < settings.minimum_ocr_confidence:
                checks.append(
                    Check(
                        code="low_ocr_confidence",
                        severity="blocking",
                        field=name,
                        documents=(item.source.doc,),
                        message="OCR confidence requires human review.",
                    )
                )
        chosen = min(
            valid,
            key=lambda item: (
                settings.source_priority.index(sources[item.source.doc]),
                item.source.doc,
                item.source.page,
            ),
        )
        confidence = (
            settings.corroborated_confidence
            if len({item.source.doc for item in valid}) >= 2
            else settings.single_document_confidence
        )
        for first, second in combinations(valid, 2):
            if equivalent(name, first.value, second.value):
                continue
            docs = tuple(sorted({first.source.doc, second.source.doc}))
            code, severity = "field_disagreement", "blocking"
            if name == "gross_weight_kg":
                first_weight, second_weight = float(first.value), float(second.value)
                allowed = max(
                    settings.weight_absolute_tolerance_kg,
                    settings.weight_relative_tolerance * max(first_weight, second_weight),
                )
                if abs(first_weight - second_weight) <= allowed:
                    code, severity = "weight_within_tolerance", "low"
                    confidence = min(confidence, settings.within_tolerance_confidence)
                else:
                    code = "weight_outside_tolerance"
            elif name == "packages":
                code = (
                    "package_count_difference"
                    if (first.value.count != second.value.count)
                    else "package_unit_difference"
                )
            elif name == "description":
                code = "description_difference"
            checks.append(
                Check(
                    code=code,
                    severity=severity,
                    field=name,
                    documents=docs,
                    message="Source values differ; original values and locations are retained.",
                )
            )
        if name == "container_no":
            for item in valid:
                if not container_is_valid(str(item.value)):
                    checks.append(
                        Check(
                            code="invalid_container",
                            severity="blocking",
                            field=name,
                            documents=(item.source.doc,),
                            message="Invalid container format, category or ISO 6346 check digit.",
                        )
                    )
        if any(check.field == name and check.severity == "blocking" for check in checks):
            confidence = 0.0
        confidences[name] = confidence
        if confidence < settings.minimum_final_confidence:
            checks.append(
                Check(
                    code="low_final_confidence",
                    severity="blocking",
                    field=name,
                    message="Rule-based confidence is below the configured entry threshold.",
                )
            )
        candidate_fields[name] = {
            "value": chosen.value,
            "source": chosen.source,
            "confidence": confidence,
        }
    report = CheckReport(
        case_id=bundle.case_id,
        checks=checks,
        field_confidences=confidences,
        minimum_final_confidence=settings.minimum_final_confidence,
    )
    record = None
    if len(candidate_fields) == len(FIELD_NAMES):
        record = ShipmentRecord(
            case_id=bundle.case_id,
            fields=candidate_fields,
            checks=[item.model_dump(mode="json") for item in checks],
            origin="extracted",
        )
        report = report.model_copy(update={"record_sha256": record_fingerprint(record)})
    return report, record


def record_fingerprint(record: ShipmentRecord) -> str:
    """Bind case/origin, values, sources and scores; omit checks to avoid a hash cycle."""
    payload = {
        "case_id": record.case_id,
        "origin": record.origin,
        "fields": record.fields.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def can_enter(record: ShipmentRecord, report: CheckReport) -> bool:
    """Readiness check; callers must also require the prepared case's READY state."""
    return (
        record.origin == "extracted"
        and record.case_id == report.case_id
        and report.record_sha256 == record_fingerprint(record)
        and report.ready
        and set(report.field_confidences) == set(FIELD_NAMES)
        and all(getattr(record.fields, name).source is not None for name in FIELD_NAMES)
        and all(
            report.field_confidences[name] >= report.minimum_final_confidence
            and getattr(record.fields, name).confidence == report.field_confidences[name]
            for name in FIELD_NAMES
        )
        and record.checks == [check.model_dump(mode="json") for check in report.checks]
    )
