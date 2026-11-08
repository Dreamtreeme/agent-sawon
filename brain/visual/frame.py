"""Project one current capture without exposing internal marker namespaces."""

from __future__ import annotations

from copy import deepcopy

from brain.vocabulary import SCROLL_DIRECTION_KEYS

from .scene import contains


def frame_input(screen: dict) -> dict:
    """Keep current text connected to detector regions and memory separate."""
    blocks = screen.get("ocr", [])
    controls = []
    for marker in screen.get("markers", []):
        if marker["source"] != "omniparser":
            continue
        if "contained_ocr_ids" in marker:
            contained = [b for b in blocks if b["id"] in marker["contained_ocr_ids"]]
        else:
            contained = [b for b in blocks if contains(marker["bbox"], b["bbox"])]
        controls.append(
            {
                "bbox": marker["bbox"],
                "contained_text": [{"text": b["text"], "bbox": b["bbox"]} for b in contained],
            }
        )
    result = {
        "image_size": screen.get("image_size"),
        "text_regions": [{"text": b["text"], "bbox": b["bbox"]} for b in blocks],
        "control_regions": controls,
    }
    for key in ("active_window", "windows", "available_apps"):
        if key in screen:
            result[key] = screen[key]
    if screen.get("memory_fields"):
        result["memory_fields"] = screen["memory_fields"]
        result["memory_screen_path"] = screen.get("screen_path", [])
        procedures = []
        for group in screen.get("retrieved_procedure", []):
            steps = []
            for stored in group["steps"]:
                step = {key: value for key, value in stored.items() if value is not None}
                if step["action"] == "key":
                    step.pop("source", None)
                    for direction, key in SCROLL_DIRECTION_KEYS.items():
                        if step["keys"] == key:
                            step.update(action="scroll", direction=direction)
                            del step["keys"]
                            break
                steps.append(step)
            procedures.append({**group, "steps": steps})
        result["retrieved_procedure"] = procedures
    if "correction" in screen:
        result["correction"] = screen["correction"]
    return deepcopy(result)
