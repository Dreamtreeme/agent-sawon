"""Prepare a private case for ERP entry, stopping on uncertainty or conflicting facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from pydantic import Field
from sawon_interfaces import ShipmentRecord

from brain.runtime.storage import CaseStatus, RunLog, StateStore, atomic_write_json
from models.accounting import read_model_accounting
from models.base import MeasuredModelClient

from .checks import can_enter, cross_validate
from .extract import Reservation, extract_page, extraction_prompt
from .ocr import OcrEngine, PaddleOcrEngine
from .ocr_input import prepare_ocr_input, read_ocr_views
from .render import DOCUMENT_SUFFIXES, PipelineDependencyError, render_document
from .schemas import (
    Check,
    CheckReport,
    DocumentInput,
    ExtractionBundle,
    FrozenModel,
    PageExtraction,
    PipelineSettings,
)


class PipelineResult(FrozenModel):
    case_id: str
    case_dir: Path
    ready: bool
    record: ShipmentRecord | None
    checks: CheckReport
    known_cost_usd: float = Field(ge=0)
    unknown_cost: bool
    error_type: str | None = None
    error_detail: str | None = None


class PipelineBudgetError(RuntimeError):
    pass


def load_inputs(manifest_path: Path) -> tuple[str, list[DocumentInput]]:
    """Read only the explicit input manifest and its declared PDF/image files.

    Truth, HTML sources, ERP files and other sibling files are never discovered.
    """
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0":
        raise ValueError("unsupported_document_manifest_version")
    root = manifest_path.parent
    documents = []
    for entry in manifest["documents"]:
        supplied = Path(entry["path"])
        path = (root / supplied).resolve()
        if supplied.is_absolute() or not path.is_relative_to(root):
            raise ValueError("document_path_escapes_manifest")
        if path.suffix.lower() not in DOCUMENT_SUFFIXES:
            raise ValueError("manifest_must_reference_document_images_or_pdfs")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("document_digest_mismatch")
        documents.append(DocumentInput(kind=entry["document_type"], path=path))
    return manifest["case_id"], documents


def prepare_case(
    case_dir: Path,
    *,
    case_id: str,
    documents: list[DocumentInput],
    model_factory: Callable[[RunLog], MeasuredModelClient],
    settings: PipelineSettings | None = None,
    reservation: Reservation | None = None,
    ocr: OcrEngine | None = None,
) -> PipelineResult:
    """Create a fresh case. No relay/ERP action is performed by the pipeline.

    A failed preparation is preserved and must be retried in a new directory.
    Records with discrepancies are candidate records only, in FAILED state.
    """
    settings = settings or PipelineSettings()
    case_dir = Path(case_dir).resolve()
    if case_dir.exists() and any(case_dir.iterdir()):
        raise ValueError("pipeline_requires_fresh_case_directory")
    log = RunLog(case_dir, case_id=case_id, agent_id="pipeline")
    store = StateStore(case_dir, case_id=case_id)
    store.initialize()
    store.transition(CaseStatus.EXTRACTING, expected=CaseStatus.RECEIVED)
    log.append("plan", payload={"stage": "document_extraction", "document_count": len(documents)})
    bundle = ExtractionBundle(case_id=case_id, pages=[])
    atomic_write_json(case_dir / "extracted.json", bundle)
    owns_ocr = ocr is None
    ocr = ocr or PaddleOcrEngine(settings)
    model = None
    error_type = None
    error_detail = None
    pipeline_failure = None
    record = None
    try:
        copied = []
        for index, document in enumerate(documents):
            original = document.path.resolve()
            if not original.is_file() or original.stat().st_size > settings.max_document_bytes:
                raise ValueError("document_missing_or_too_large")
            suffix = original.suffix.lower()
            if suffix not in DOCUMENT_SUFFIXES:
                raise ValueError("unsupported_document_type")
            destination = case_dir / "originals" / f"{document.kind}-{index + 1:02d}{suffix}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = original.read_bytes()
            with destination.open("xb") as stream:
                stream.write(content)
            copied.append(DocumentInput(kind=document.kind, path=destination))
            log.append(
                "observe",
                payload={
                    "stage": "document_received",
                    "kind": document.kind,
                    "document": destination.relative_to(case_dir).as_posix(),
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            )
        bundle = bundle.model_copy(
            update={"documents": {f"originals/{item.path.name}": item.kind for item in copied}}
        )
        atomic_write_json(case_dir / "extracted.json", bundle)
        missing = set(settings.required_documents) - {document.kind for document in copied}
        if not missing:
            page_count = 0
            for document in copied:
                pages = render_document(document, case_dir / "pages", settings)
                page_count += len(pages)
                if page_count > settings.max_pages:
                    raise ValueError("case_page_limit")
                for page in pages:
                    ocr_page, ocr_input = prepare_ocr_input(page, settings, case_dir=case_dir)
                    blocks = read_ocr_views(ocr, page, ocr_page, settings)
                    if not blocks:
                        raise ValueError("ocr_returned_no_evidence")
                    # Persist OCR even if a later model call or schema validation fails.
                    atomic_write_json(
                        case_dir / "ocr" / f"{document.path.stem}-{page.page:04d}.json",
                        {
                            "document": page.document,
                            "page": page.page,
                            "ocr_input": ocr_input.model_dump(mode="json"),
                            "blocks": [block.model_dump(mode="json") for block in blocks],
                        },
                    )
                    if model is None:
                        model = model_factory(log)
                        if not isinstance(model, MeasuredModelClient):
                            raise TypeError("pipeline_requires_measured_model_client")
                    accounting = read_model_accounting(log)
                    if accounting.unknown:
                        raise PipelineBudgetError("unknown_previous_model_cost")
                    if reservation is None and model.provider != "replay":
                        raise PipelineBudgetError("live_model_reservation_required")
                    reserved = (
                        reservation(extraction_prompt(page, blocks), PageExtraction, page)
                        if reservation
                        else Decimal(0)
                    )
                    limit = Decimal(str(settings.max_cost_usd))
                    if (
                        not reserved.is_finite()
                        or reserved < 0
                        or accounting.known_usd + reserved > limit
                    ):
                        raise PipelineBudgetError("pipeline_cost_reservation_exceeds_limit")
                    before_calls = read_model_accounting(log).model_calls
                    extracted, charged = extract_page(model, page, blocks, case_dir=case_dir)
                    extracted = extracted.model_copy(update={"ocr_input": ocr_input})
                    accounting = read_model_accounting(log)
                    if accounting.model_calls != before_calls + 1:
                        raise PipelineBudgetError("model_measurement_missing_from_case_log")
                    bundle = bundle.model_copy(update={"pages": [*bundle.pages, extracted]})
                    atomic_write_json(case_dir / "extracted.json", bundle)
                    if accounting.unknown or charged > reserved or accounting.known_usd > limit:
                        raise PipelineBudgetError("model_cost_exceeded_reservation_or_is_unknown")
                    log.append(
                        "verify",
                        payload={
                            "stage": "page_extracted",
                            "document": page.document,
                            "page": page.page,
                            "field_count": len(extracted.fields),
                        },
                    )
    except Exception as exc:
        error_type = type(exc).__name__
        if isinstance(exc, (PipelineDependencyError, PipelineBudgetError)):
            error_detail = str(exc)
        pipeline_failure = Check(
            code="pipeline_error",
            severity="blocking",
            message=error_detail or f"{error_type}: processing stopped; inspect local evidence.",
        )
        log.append("stop", payload={"stage": "document_extraction", "error_type": error_type})
    finally:
        if owns_ocr:
            ocr.close()
    report, record = cross_validate(bundle, settings)
    if pipeline_failure:
        report = report.model_copy(update={"checks": [*report.checks, pipeline_failure]})
        if record:
            record = record.model_copy(
                update={"checks": [check.model_dump(mode="json") for check in report.checks]}
            )
    ready = record is not None and can_enter(record, report)
    atomic_write_json(case_dir / "checks.json", report)
    if record:
        atomic_write_json(case_dir / "record.json", record)
    store.transition(
        CaseStatus.READY if ready else CaseStatus.FAILED,
        expected=CaseStatus.EXTRACTING,
        reason=None if ready else "document_review_required",
    )
    log.append(
        "verify" if ready else "stop",
        payload={
            "stage": "document_checks",
            "ready": ready,
            "blocking_checks": sum(check.severity == "blocking" for check in report.checks),
            "low_priority_checks": sum(check.severity == "low" for check in report.checks),
        },
    )
    accounting = read_model_accounting(log)
    return PipelineResult(
        case_id=case_id,
        case_dir=case_dir,
        ready=ready,
        record=record,
        checks=report,
        known_cost_usd=float(accounting.known_usd),
        unknown_cost=accounting.unknown,
        error_type=error_type,
        error_detail=error_detail,
    )
