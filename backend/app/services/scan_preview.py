"""In-memory JPEG encoding for page capture and browser framing."""

from __future__ import annotations

import base64
import time
from typing import Any

import cv2


def capture_preview(
    page: Any, *, max_edge: int = 2_000, quality: int = 85, captured_at: float | None = None,
) -> dict[str, Any]:
    height, width = page.shape[:2]
    image = page
    if max(height, width) > max_edge:
        scale = max_edge / max(height, width)
        image = cv2.resize(
            page, (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("Could not encode the captured page.")
    return {
        "data_url": "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode(),
        "width": int(image.shape[1]), "height": int(image.shape[0]),
        "captured_at": time.time() if captured_at is None else captured_at,
    }
