"""Small standalone camera capture diagnostic; no OCR or provider calls."""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.book_scanner import scan_camera


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("page.jpg"))
    args = parser.parse_args()
    result = scan_camera(args.camera, show_preview=False)
    args.output.write_bytes(base64.b64decode(result["data_url"].split(",", 1)[1]))
    print(f"Captured {result['width']}x{result['height']} page to {args.output}")


if __name__ == "__main__":
    main()
