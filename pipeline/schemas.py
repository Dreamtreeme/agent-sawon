"""Document contracts. Self-reported model confidence is never final confidence."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sawon_interfaces import PackageCount, SourceLocation

FieldName = Literal[
    "hbl_no",
    "mbl_no",
    "shipper",
    "consignee",
    "description",
    "packages",
    "gross_weight_kg",
    "container_no",
    "pol",
    "pod",
]
FIELD_NAMES: tuple[FieldName, ...] = (
    "hbl_no",
    "mbl_no",
    "shipper",
    "consignee",
    "description",
    "packages",
    "gross_weight_kg",
    "container_no",
    "pol",
    "pod",
)
DocumentKind = Literal["hbl", "invoice", "packing_list"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentInput(FrozenModel):
    kind: DocumentKind
    path: Path


class PipelineSettings(FrozenModel):
    schema_version: Literal["0.1"] = "0.1"
    required_documents: tuple[DocumentKind, ...] = ("hbl", "invoice", "packing_list")
    required_fields: tuple[FieldName, ...] = FIELD_NAMES
    shared_required_fields: tuple[FieldName, ...] = (
        "packages",
        "gross_weight_kg",
        "description",
    )
    source_priority: tuple[DocumentKind, ...] = ("hbl", "packing_list", "invoice")
    weight_absolute_tolerance_kg: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    weight_relative_tolerance: float = Field(default=0.005, ge=0, le=1, allow_inf_nan=False)
    minimum_ocr_confidence: float = Field(default=0.8, ge=0, le=1)
    minimum_final_confidence: float = Field(default=0.8, ge=0, le=1)
    single_document_confidence: float = Field(default=0.8, ge=0, le=1)
    corroborated_confidence: float = Field(default=0.95, ge=0, le=1)
    within_tolerance_confidence: float = Field(default=0.85, ge=0, le=1)
    max_cost_usd: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    max_pages: int = Field(default=20, ge=1, le=100)
    max_document_bytes: int = Field(default=30_000_000, ge=1)
    max_pixels: int = Field(default=8_000_000, ge=10_000, le=40_000_000)
    render_dpi: int = Field(default=144, ge=72, le=300)
    max_ocr_blocks: int = Field(default=500, ge=1, le=5000)
    max_ocr_characters: int = Field(default=50_000, ge=100, le=500_000)
    ocr_language: str = "en"
    ocr_device: str = "cpu"
    ocr_version: str = "PP-OCRv5"
    ocr_detection_model: str | None = "PP-OCRv5_mobile_det"
    ocr_recognition_model: str | None = None
    ocr_execution: Literal["subprocess", "in_process"] = "subprocess"
    ocr_enable_mkldnn: bool = False
    ocr_cpu_threads: int = Field(default=4, ge=1, le=64)
    ocr_python: str | None = None
    ocr_timeout_seconds: int = Field(default=300, ge=1, le=1800)
    ocr_median_kernel: Literal[0, 3, 5] = 0
    ocr_include_original: bool = False

    @model_validator(mode="after")
    def consistent_configuration(self) -> Self:
        if self.ocr_include_original and self.ocr_median_kernel == 0:
            raise ValueError("Original OCR comparison requires a separate filtered image")
        if set(self.required_fields) != set(FIELD_NAMES):
            raise ValueError("The current public ShipmentRecord requires all ten fields")
        if len(self.required_fields) != len(FIELD_NAMES):
            raise ValueError("Duplicate required field")
        if set(self.source_priority) != {"hbl", "invoice", "packing_list"}:
            raise ValueError("Source priority must include all document kinds exactly once")
        if len(self.source_priority) != 3:
            raise ValueError("Duplicate document source priority")
        if not self.required_documents or len(set(self.required_documents)) != len(
            self.required_documents
        ):
            raise ValueError("Required document kinds must be nonempty and unique")
        return self

    @classmethod
    def load(cls, path: Path) -> PipelineSettings:
        with path.open("rb") as stream:
            return cls.model_validate(tomllib.load(stream))


class OcrBlock(FrozenModel):
    id: int = Field(ge=0)
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    # Pixel coordinates on the rendered page: x, y, width, height.
    bbox: tuple[int, int, int, int]
    input_variant: Literal["unspecified", "original", "median"] = "unspecified"

    @model_validator(mode="after")
    def valid_bbox(self) -> Self:
        x, y, width, height = self.bbox
        if min(x, y) < 0 or min(width, height) <= 0:
            raise ValueError("OCR bounds must have nonnegative origin and positive size")
        return self


class RenderedPage(FrozenModel):
    document: str
    kind: DocumentKind
    page: int = Field(ge=1)
    image_path: Path
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class VisionField(FrozenModel):
    name: FieldName
    value_text: str = Field(min_length=1, max_length=2000)
    unit_text: str | None = Field(default=None, max_length=80)
    evidence_block_ids: list[int] = Field(min_length=1, max_length=30)
    model_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class PageExtraction(FrozenModel):
    fields: list[VisionField] = Field(max_length=50)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Keep decoding compact; enforce resource bounds locally after parsing.

        Expanding nested bounded arrays and long strings can exceed provider
        decoding limits. These limits remain in the Pydantic validator.
        """
        schema = super().model_json_schema(*args, **kwargs)

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                node.pop("maxLength", None)
                node.pop("maxItems", None)
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        visit(schema)
        return schema


class ExtractedField(FrozenModel):
    name: FieldName
    raw_value: str
    raw_unit: str | None = None
    value: str | float | PackageCount | None
    source: SourceLocation
    evidence_block_ids: tuple[int, ...]
    evidence_text: str
    ocr_confidence: float = Field(ge=0, le=1)
    model_confidence: float = Field(ge=0, le=1)
    evidence_supported: bool
    normalization_error: str | None = None


class OcrInput(FrozenModel):
    image: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    median_kernel: Literal[0, 3, 5] = 0
    coordinate_mapping: Literal["identity"] = "identity"
    original_image: str | None = None
    original_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ExtractedPage(FrozenModel):
    schema_version: Literal["0.1", "0.2"] = "0.2"
    document: str
    kind: DocumentKind
    page: int
    width: int
    height: int
    image: str
    bbox_coordinate_space: Literal["rendered_page_pixels"] = "rendered_page_pixels"
    blocks: list[OcrBlock]
    fields: list[ExtractedField]
    ocr_input: OcrInput | None = None


class ExtractionBundle(FrozenModel):
    schema_version: Literal["0.1"] = "0.1"
    case_id: str
    documents: dict[str, DocumentKind] = Field(default_factory=dict)
    pages: list[ExtractedPage]


class Check(FrozenModel):
    code: str
    severity: Literal["info", "low", "blocking"]
    field: FieldName | None = None
    documents: tuple[str, ...] = ()
    message: str


class CheckReport(FrozenModel):
    schema_version: Literal["0.1"] = "0.1"
    case_id: str
    checks: list[Check]
    confidence_method: Literal["rule_cross_validation"] = "rule_cross_validation"
    field_confidences: dict[FieldName, float]
    minimum_final_confidence: float = Field(default=0.8, ge=0, le=1)
    record_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @property
    def ready(self) -> bool:
        return not any(check.severity == "blocking" for check in self.checks)
