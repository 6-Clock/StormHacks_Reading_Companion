"""Captured page images and optional local word geometry for developer inspection."""

from __future__ import annotations

import base64
import csv
import io
import math
from types import ModuleType
from typing import Any

MAX_PREVIEW_EDGE = 1_200
MAX_PREVIEW_WORDS = 1_000
WORD_GEOMETRY_TIMEOUT = 2.0
MIN_WORD_CONFIDENCE = 50.0


def normalized_words(tsv: str, width: int, height: int) -> list[dict[str, Any]]:
    """Clip real Tesseract pixel boxes to the displayed image and normalize them."""
    rows = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {"level", "left", "top", "width", "height", "conf", "text"}
    if not required.issubset(rows.fieldnames or []):
        raise ValueError("Local OCR did not return word geometry.")
    words = []
    for row in rows:
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        try:
            left, top, box_width, box_height, confidence = (
                float(row[key]) for key in ("left", "top", "width", "height", "conf")
            )
        except (TypeError, ValueError, OverflowError):
            continue
        if not all(math.isfinite(value) for value in (
            left, top, box_width, box_height, confidence,
        )):
            continue
        if not MIN_WORD_CONFIDENCE <= confidence <= 100 or box_width <= 0 or box_height <= 0:
            continue
        right = min(float(width), left + box_width)
        bottom = min(float(height), top + box_height)
        left, top = max(0.0, left), max(0.0, top)
        if right <= left or bottom <= top:
            continue
        words.append({
            "text": text[:200],
            "x": left / width,
            "y": top / height,
            "width": (right - left) / width,
            "height": (bottom - top) / height,
            "confidence": confidence / 100,
        })
        if len(words) == MAX_PREVIEW_WORDS:
            break
    return words


def capture_preview(engine: ModuleType, page: Any) -> dict[str, Any] | None:
    """Use the same corrected page as AI OCR without changing its accepted text.

    Encoding and local OCR are optional diagnostics: either can fail without
    changing the reading result. The JPEG is returned in memory, never saved.
    """
    try:
        height, width = page.shape[:2]
        image = page
        if max(height, width) > MAX_PREVIEW_EDGE:
            scale = MAX_PREVIEW_EDGE / max(height, width)
            image = engine.cv2.resize(
                page,
                (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=engine.cv2.INTER_AREA,
            )
        height, width = image.shape[:2]
        encoded, buffer = engine.cv2.imencode(
            ".jpg", image, [engine.cv2.IMWRITE_JPEG_QUALITY, 78],
        )
        if not encoded:
            return None
        preview = {
            "data_url": "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode(),
            "width": int(width),
            "height": int(height),
            "words": [],
            "boxes_status": "unavailable",
        }
    except Exception:
        # A diagnostics failure must never discard an accepted AI transcription.
        return None

    try:
        rgb = engine.cv2.cvtColor(image, engine.cv2.COLOR_BGR2RGB)
        # Request TSV directly to avoid image_to_data's unbounded version probe.
        # This is one local process with a timeout, not the legacy multi-pass OCR.
        tsv = engine.pytesseract.run_and_get_output(
            rgb,
            extension="tsv",
            lang=engine.LANG,
            config="--oem 3 --psm 3 -c tessedit_create_tsv=1",
            timeout=WORD_GEOMETRY_TIMEOUT,
        )
        words = normalized_words(tsv, width, height)
        preview["words"] = words
        preview["boxes_status"] = "available" if words else "no_words"
    except Exception:
        # Tesseract is optional; missing executable/language/timeout keeps photo.
        pass
    return preview
