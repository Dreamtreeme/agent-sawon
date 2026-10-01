"""One visible ID namespace at the model boundary; local OCR IDs stay private."""

from __future__ import annotations

from pydantic import BaseModel


class MarkerReferenceError(ValueError):
    def __init__(self, issues: list[dict]) -> None:
        super().__init__("invalid_text_marker_reference")
        self.issues = issues


def text_index(screen: dict) -> dict[int, int]:
    """Each literal OCR block has one independently numbered text marker."""
    return {
        oid: marker["id"]
        for marker in screen.get("markers", [])
        if marker["source"] == "ocr"
        for oid in marker["text_ids"]
    }


def model_screen(screen: dict) -> dict:
    index = text_index(screen)

    def ids(values):
        return [index[i] for i in values if i in index]

    result = {
        k: v
        for k, v in screen.items()
        if k not in {"ocr", "markers", "ocr_evidence_refs", "correction"}
    }
    result["id_namespace"] = "visible_marker_ids"
    result["ocr"] = [dict(b, id=index[b["id"]]) for b in screen["ocr"] if b["id"] in index]
    result["markers"] = [
        {
            **m,
            "text_ids": ids(m.get("text_ids", [])),
            "contained_ocr_ids": ids(m.get("contained_ocr_ids", [])),
        }
        for m in screen.get("markers", [])
    ]
    if "correction" in screen:
        correction = screen["correction"]
        result["correction"] = {k: v for k, v in correction.items() if k != "invalid_text_links"}
        if "invalid_text_links" in correction:
            result["correction"]["invalid_text_links"] = [
                {
                    **item,
                    "expected_ocr_ids": ids(item["expected_ocr_ids"])
                    if item.get("expected_ocr_ids") is not None
                    else None,
                    "invalid_ocr_ids": ids(item.get("invalid_ocr_ids", [])),
                    "invalid_label_ids": ids(item.get("invalid_label_ids", [])),
                }
                for item in correction["invalid_text_links"]
            ]
    return result


def local_response[T: BaseModel](response: T, screen: dict) -> T:
    """Decode text-marker references explicitly, never guess from integer overlap."""
    text_markers = {
        m["id"]: m["text_ids"] for m in screen.get("markers", []) if m["source"] == "ocr"
    }
    payload = response.model_dump()
    issues = []
    for control in payload.get("controls", []):
        for field in ("label_ids", "text_ids"):
            refs = control[field]
            invalid = [ref for ref in refs if ref not in text_markers]
            if invalid:
                issues.append(
                    {
                        "marker_id": control["id"],
                        "field": field,
                        "rejected_ids": invalid,
                        "allowed_text_marker_ids": sorted(text_markers),
                    }
                )
            else:
                control[field] = [oid for ref in refs for oid in text_markers[ref]]
    if issues:
        raise MarkerReferenceError(issues)
    return type(response).model_validate(payload)


def model_controls(controls: list, screen: dict) -> list[dict]:
    """Encode internal controls for follow-up requests using the same visible IDs."""
    index = text_index(screen)
    result = []
    for control in controls:
        item = control.model_dump()
        for field in ("label_ids", "text_ids"):
            item[field] = [index[i] for i in item[field] if i in index]
        result.append(item)
    return result
