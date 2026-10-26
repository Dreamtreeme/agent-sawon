"""Observe structural motion between captures; never certify a business result."""

from pathlib import Path
from time import perf_counter


def compare_frames(before: Path, after: Path) -> dict:
    """Use the ORB settings evaluated on historical captures (report 109).

    Missing captures or too few matches are unknown, not evidence of no movement.
    This comparison does not choose screen nodes or authorize cached coordinates.
    """
    import cv2
    import numpy as np

    started = perf_counter()
    result = {"method": "orb", "changed": None, "reason": "insufficient_matches"}
    try:
        images = [
            cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
            for path in (before, after)
        ]
    except OSError:
        images = [None, None]
    if any(image is None for image in images):
        return {**result, "reason": "capture_unavailable"}
    a, b = images
    if a.shape != b.shape:
        return {**result, "changed": True, "reason": "capture_size_changed"}
    factor = min(1, 960 / max(a.shape))
    size = (round(a.shape[1] * factor), round(a.shape[0] * factor))
    a, b = [cv2.resize(image, size, interpolation=cv2.INTER_AREA) for image in images]
    orb = cv2.ORB_create(nfeatures=1200)
    ka, da = orb.detectAndCompute(a, None)
    kb, db = orb.detectAndCompute(b, None)
    matches = (
        [
            pair[0]
            for pair in cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
            if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
        ]
        if da is not None and db is not None
        else []
    )
    coverage = len(matches) / max(1, min(len(ka), len(kb)))
    moved_fraction = (
        float(
            np.mean(
                [
                    np.linalg.norm(np.array(kb[m.trainIdx].pt) - ka[m.queryIdx].pt) > 3
                    for m in matches
                ]
            )
        )
        if matches
        else None
    )
    if len(matches) >= 20:
        result.update(changed=bool(moved_fraction > 0.15 or coverage < 0.5), reason="orb_matches")
    return {
        **result,
        "analysis_size": list(size),
        "keypoints": [len(ka), len(kb)],
        "matches": len(matches),
        "coverage": coverage,
        "moved_fraction": moved_fraction,
        "elapsed_ms": (perf_counter() - started) * 1000,
    }
