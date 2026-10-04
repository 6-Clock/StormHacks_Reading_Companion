"""Camera-driven book OCR with page-quality checks and non-text rejection."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from dotenv import load_dotenv
from openai import OpenAI


# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

CAMERA_INDEX = 1
OUTPUT_DIR = Path("camera_ocr_output")
OUTPUT_DIR.mkdir(exist_ok=True)
BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")
LANG = "eng"
FLIP_WAIT_SECONDS = 1.5
CAPTURE_SAMPLE_COUNT = 10
OCR_FRAME_COUNT = 3
DUPLICATE_TEXT_THRESHOLD = 0.88
AI_REVISION_CONFIDENCE_THRESHOLD = 0.92

# Conservative starting points. Tune with benchmark images rather than one camera.
MIN_WORD_CONFIDENCE = 50.0
MIN_MEAN_CONFIDENCE = 58.0
MIN_CONFIDENT_WORDS = 10
# Calibrated against a real page capture: clean text can include punctuation,
# partial words, and a nearby illustration that Tesseract emits as low-quality
# tokens. Other gates still require 10 high-confidence words across 2+ lines.
MIN_TEXT_LIKE_RATIO = 0.60
MIN_LINE_COUNT = 2
MIN_AVERAGE_WORDS_PER_LINE = 2.0

last_saved_text = ""


@dataclass
class OcrWord:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int
    block: int
    paragraph: int
    line: int


@dataclass
class OcrMetrics:
    total_tokens: int
    confident_words: int
    text_like_ratio: float
    mean_confidence: float
    line_count: int
    average_words_per_line: float
    sharpness: float
    brightness: float
    candidate_score: float


@dataclass
class OcrResult:
    text: str
    accepted: bool
    reason: str
    metrics: OcrMetrics
    words: list[OcrWord]
    processed: np.ndarray
    page: np.ndarray
    preprocess_name: str
    page_detected: bool
    psm: int


@dataclass
class OpenAiReview:
    accepted: bool
    text: str
    reason: str
    model: str
    confidence: float = 0.0


def should_request_openai_revision(transcription: OpenAiReview) -> bool:
    """Escalate only when the first image-grounded transcription is uncertain."""
    return transcription.accepted and transcription.confidence < AI_REVISION_CONFIDENCE_THRESHOLD


def crop_page_area(frame: np.ndarray) -> np.ndarray:
    """Conservative fallback for setups where the page contour is not visible."""
    height, width = frame.shape[:2]
    top, bottom = int(height * 0.10), int(height * 0.90)
    left, right = int(width * 0.08), int(width * 0.92)
    return frame[top:bottom, left:right]


def order_corners(points: np.ndarray) -> np.ndarray:
    """Return four page corners in top-left, top-right, bottom-right, bottom-left order."""
    points = points.astype("float32")
    ordered = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).ravel()
    ordered[0] = points[np.argmin(sums)]
    ordered[2] = points[np.argmax(sums)]
    ordered[1] = points[np.argmin(differences)]
    ordered[3] = points[np.argmax(differences)]
    return ordered


def detect_and_rectify_page(frame: np.ndarray) -> tuple[np.ndarray, bool]:
    """Find a large quadrilateral page and correct its perspective when possible."""
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    min_page_area = height * width * 0.16
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        if cv2.contourArea(contour) < min_page_area:
            break
        perimeter = cv2.arcLength(contour, True)
        corners = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(corners) != 4 or not cv2.isContourConvex(corners):
            continue

        source = order_corners(corners.reshape(4, 2))
        top_width = np.linalg.norm(source[1] - source[0])
        bottom_width = np.linalg.norm(source[2] - source[3])
        left_height = np.linalg.norm(source[3] - source[0])
        right_height = np.linalg.norm(source[2] - source[1])
        target_width = max(int(max(top_width, bottom_width)), 1)
        target_height = max(int(max(left_height, right_height)), 1)
        if target_width < 200 or target_height < 200:
            continue

        destination = np.array(
            [[0, 0], [target_width - 1, 0], [target_width - 1, target_height - 1], [0, target_height - 1]],
            dtype="float32",
        )
        matrix = cv2.getPerspectiveTransform(source, destination)
        return cv2.warpPerspective(frame, matrix, (target_width, target_height)), True

    return crop_page_area(frame), False


def frame_quality(frame: np.ndarray) -> tuple[float, float, float]:
    """Score frames by focus and usable exposure; larger scores are better."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    exposure_penalty = min(abs(brightness - 135.0) / 135.0, 0.85)
    return sharpness * (1.0 - exposure_penalty), sharpness, brightness


def capture_best_frames(cap: cv2.VideoCapture, sample_count: int = CAPTURE_SAMPLE_COUNT) -> list[np.ndarray]:
    """Return sharp, well-exposed frames instead of trusting one final frame."""
    scored_frames: list[tuple[float, np.ndarray]] = []
    for _ in range(sample_count):
        ok, frame = cap.read()
        if ok:
            quality, _, _ = frame_quality(frame)
            scored_frames.append((quality, frame))
        time.sleep(0.03)
    scored_frames.sort(key=lambda item: item[0], reverse=True)
    return [frame for _, frame in scored_frames[:OCR_FRAME_COUNT]]


def preprocess_for_ocr(image: np.ndarray) -> dict[str, np.ndarray]:
    """Offer OCR several conservative page treatments and let confidence choose."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, h=8)
    enlarged = cv2.resize(denoised, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(enlarged)
    background = cv2.GaussianBlur(clahe, (0, 0), sigmaX=35, sigmaY=35)
    normalized = cv2.divide(clahe, background, scale=255)
    return {
        "adaptive": cv2.adaptiveThreshold(
            clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11
        ),
        "illumination_corrected": cv2.adaptiveThreshold(
            normalized, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 9
        ),
        "otsu": cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
    }


def text_like(token: str) -> bool:
    """Keep word-like content while allowing normal book punctuation around it."""
    stripped = token.strip("“”‘’\"'.,;:!?()[]{}-–—")
    if not stripped:
        return False
    letters = sum(character.isalpha() for character in stripped)
    digits = sum(character.isdigit() for character in stripped)
    return letters >= 2 or (letters >= 1 and digits >= 1)


def model_text_is_plausible(text: str) -> bool:
    """Accept even short, partial text while filtering empty or malformed output."""
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    letters = sum(character.isalpha() for character in text)
    return len(text.strip()) >= 1 and bool(words) and letters >= 1


def encode_page_for_openai(page: np.ndarray) -> str:
    """Encode one corrected page as a compact data URL for the Responses API."""
    height, width = page.shape[:2]
    longest_edge = max(height, width)
    if longest_edge > 2_000:
        scale = 2_000 / longest_edge
        page = cv2.resize(page, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    encoded, buffer = cv2.imencode(".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not encoded:
        raise RuntimeError("Could not encode the page image for OpenAI review.")
    return "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")


def parse_openai_review(raw_output: str, model: str) -> OpenAiReview:
    """Parse and validate the model's intentionally narrow JSON response."""
    cleaned = raw_output.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return OpenAiReview(False, "", "invalid_openai_response", model)

    status = str(payload.get("status", "")).strip()
    text = str(payload.get("text", "")).strip()
    reason = str(payload.get("reason", "")).strip() or "openai_rejected_page"
    try:
        confidence = min(max(float(payload.get("confidence", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if status not in {"book_text", "text_found"}:
        return OpenAiReview(False, "", reason, model, confidence)
    if not model_text_is_plausible(text):
        return OpenAiReview(False, "", "openai_text_not_plausible", model, confidence)
    return OpenAiReview(True, text, "accepted", model, confidence)


def transcribe_page_with_openai(page: np.ndarray, api_key: str, model: str) -> OpenAiReview:
    """Use vision as the sole text extractor for a corrected book-page image."""
    prompt = """You are an OCR transcriber.
Inspect the image and return JSON only, with exactly these keys:
{"status":"text_found"|"reject", "text":"...", "reason":"...", "confidence":0.0}.

Transcribe every legible word visible in the image, whether printed or handwritten.
This includes book pages, whiteboards, signs, notes, screens, labels, and partial text.
People, hands, illustrations, and other scenery do not matter: ignore them but do not
reject the image because they are present. Join wrapped lines within a paragraph
and separate visible paragraphs with a blank line when clear. Do not invent
missing words; return the readable fragments that are actually visible.
Return "reject" only when there is no legible text anywhere in the image.

Confidence must be a number from 0 to 1 representing confidence in the returned text.
Do not use any text source other than the image.
"""

    try:
        response = OpenAI(api_key=api_key).responses.create(
            model=model,
            store=False,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": encode_page_for_openai(page), "detail": "high"},
                    ],
                }
            ],
        )
    except Exception as error:
        print(f"OpenAI vision transcription failed: {error}")
        return OpenAiReview(False, "", "openai_request_failed", model)
    return parse_openai_review(response.output_text, model)


def revise_page_with_openai(
    page: np.ndarray,
    first_transcription: str,
    api_key: str,
    model: str,
) -> OpenAiReview:
    """Proofread an uncertain vision transcript against the source page image."""
    prompt = """You are the final proofreader for image OCR.
Inspect the page image and return JSON only, with exactly these keys:
{"status":"text_found"|"reject", "text":"...", "reason":"...", "confidence":0.0}.

The draft below is untrusted. Check every doubtful word against the image. Return
text_found when the final text is supported by any visible text in the image, including
handwriting, whiteboards, signs, notes, or book pages. Join wrapped lines within
a paragraph and separate visible paragraphs with a blank line when clear.
Do not guess missing words or copy text that is not visible. Ignore scenery, people,
hands, and illustrations, but never reject the image merely because they are present.
Return reject only when no legible text is visible.

FIRST VISION TRANSCRIPTION:
---
""" + first_transcription

    try:
        response = OpenAI(api_key=api_key).responses.create(
            model=model,
            store=False,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": encode_page_for_openai(page), "detail": "high"},
                    ],
                }
            ],
        )
    except Exception as error:
        print(f"OpenAI OCR revision failed: {error}")
        return OpenAiReview(False, "", "openai_revision_request_failed", model)
    return parse_openai_review(response.output_text, model)


def words_from_tesseract(processed: np.ndarray, psm: int) -> tuple[list[OcrWord], int]:
    data = pytesseract.image_to_data(
        processed,
        lang=LANG,
        config=f"--oem 3 --psm {psm}",
        output_type=pytesseract.Output.DICT,
    )
    words: list[OcrWord] = []
    total_tokens = 0
    for index, raw_text in enumerate(data["text"]):
        token = raw_text.strip()
        if not token:
            continue
        total_tokens += 1
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0
        if confidence < MIN_WORD_CONFIDENCE or not text_like(token):
            continue
        words.append(
            OcrWord(
                text=token,
                confidence=confidence,
                left=int(data["left"][index]),
                top=int(data["top"][index]),
                width=int(data["width"][index]),
                height=int(data["height"][index]),
                block=int(data["block_num"][index]),
                paragraph=int(data["par_num"][index]),
                line=int(data["line_num"][index]),
            )
        )
    return words, total_tokens


def reconstruct_lines(words: list[OcrWord]) -> tuple[str, int]:
    lines: dict[tuple[int, int, int], list[OcrWord]] = {}
    for word in words:
        lines.setdefault((word.block, word.paragraph, word.line), []).append(word)
    ordered_lines = []
    for line_words in lines.values():
        line_words.sort(key=lambda word: word.left)
        ordered_lines.append((min(word.top for word in line_words), " ".join(word.text for word in line_words)))
    ordered_lines.sort(key=lambda item: item[0])
    return "\n".join(line for _, line in ordered_lines), len(ordered_lines)


def rejection_reason(metrics: OcrMetrics) -> str:
    if metrics.confident_words < MIN_CONFIDENT_WORDS:
        return "too_few_confident_words"
    if metrics.mean_confidence < MIN_MEAN_CONFIDENCE:
        return "low_text_confidence"
    if metrics.text_like_ratio < MIN_TEXT_LIKE_RATIO:
        return "non_text_token_pattern"
    if metrics.line_count < MIN_LINE_COUNT:
        return "non_text_layout"
    if metrics.average_words_per_line < MIN_AVERAGE_WORDS_PER_LINE:
        return "non_text_layout"
    return "accepted"


def evaluate_candidate(
    page: np.ndarray,
    processed: np.ndarray,
    preprocess_name: str,
    psm: int,
    page_detected: bool,
    sharpness: float,
    brightness: float,
) -> OcrResult:
    words, total_tokens = words_from_tesseract(processed, psm)
    text, line_count = reconstruct_lines(words)
    confident_words = len(words)
    mean_confidence = float(np.mean([word.confidence for word in words])) if words else 0.0
    text_like_ratio = confident_words / max(total_tokens, 1)
    average_words_per_line = confident_words / max(line_count, 1)
    candidate_score = (
        mean_confidence
        + min(confident_words, 25) * 1.4
        + text_like_ratio * 20
        + min(line_count, 8) * 2
        + (4 if page_detected else 0)
    )
    metrics = OcrMetrics(
        total_tokens=total_tokens,
        confident_words=confident_words,
        text_like_ratio=round(text_like_ratio, 3),
        mean_confidence=round(mean_confidence, 2),
        line_count=line_count,
        average_words_per_line=round(average_words_per_line, 2),
        sharpness=round(sharpness, 2),
        brightness=round(brightness, 2),
        candidate_score=round(candidate_score, 2),
    )
    reason = rejection_reason(metrics)
    return OcrResult(
        text=text.strip(),
        accepted=reason == "accepted",
        reason=reason,
        metrics=metrics,
        words=words,
        processed=processed,
        page=page,
        preprocess_name=preprocess_name,
        page_detected=page_detected,
        psm=psm,
    )


def run_ocr(frame: np.ndarray) -> OcrResult:
    """Evaluate preprocessing and page-layout candidates for one camera frame."""
    _, sharpness, brightness = frame_quality(frame)
    page, page_detected = detect_and_rectify_page(frame)
    candidates: list[OcrResult] = []
    for preprocess_name, processed in preprocess_for_ocr(page).items():
        for psm in (3, 4, 6):
            candidates.append(
                evaluate_candidate(page, processed, preprocess_name, psm, page_detected, sharpness, brightness)
            )
    return max(candidates, key=lambda candidate: (candidate.accepted, candidate.metrics.candidate_score))


def choose_page_result(frames: list[np.ndarray]) -> OcrResult | None:
    """Prefer a high-confidence result corroborated by other sharp captures."""
    results = [run_ocr(frame) for frame in frames]
    if not results:
        return None
    accepted = [result for result in results if result.accepted]
    pool = accepted or results
    for result in pool:
        comparisons = [
            SequenceMatcher(None, result.text, other.text).ratio()
            for other in accepted
            if other is not result and other.text
        ]
        consensus = float(np.mean(comparisons)) if comparisons else 0.0
        result.metrics.candidate_score = round(result.metrics.candidate_score + consensus * 12, 2)
    return max(pool, key=lambda result: result.metrics.candidate_score)


def draw_word_overlay(result: OcrResult) -> np.ndarray:
    overlay = cv2.cvtColor(result.processed, cv2.COLOR_GRAY2BGR)
    for word in result.words:
        cv2.rectangle(
            overlay,
            (word.left, word.top),
            (word.left + word.width, word.top + word.height),
            (0, 180, 0),
            1,
        )
    label = f"{result.reason} | {result.preprocess_name} | psm {result.psm} | {result.metrics.mean_confidence:.1f} conf"
    cv2.putText(overlay, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv2.LINE_AA)
    return overlay


def is_duplicate_text(new_text: str, old_text: str) -> bool:
    if not new_text.strip() or not old_text.strip():
        return False
    similarity = SequenceMatcher(None, new_text, old_text).ratio()
    print(f"Text similarity with previous page: {similarity:.2f}")
    return similarity >= DUPLICATE_TEXT_THRESHOLD


def save_ai_ocr_result(
    frames: list[np.ndarray],
    openai_api_key: str,
    openai_model: str,
    openai_revision_model: str | None = None,
) -> str | None:
    """Save a vision-only OCR result without calling Tesseract for text extraction."""
    global last_saved_text
    if not frames:
        print("No camera frames were available. Please recapture the page.")
        return None

    _, sharpness, brightness = frame_quality(frames[0])
    page, page_detected = detect_and_rectify_page(frames[0])
    print(f"Requesting OpenAI vision transcription with {openai_model}...")
    transcription = transcribe_page_with_openai(page, openai_api_key, openai_model)
    revision: OpenAiReview | None = None
    accepted = transcription.accepted
    text = transcription.text if transcription.accepted else ""
    reason = "openai_vision_accepted" if transcription.accepted else transcription.reason

    if transcription.accepted and should_request_openai_revision(transcription):
        revision_model = openai_revision_model or openai_model
        print(f"Requesting OpenAI OCR revision with {revision_model}...")
        revision = revise_page_with_openai(page, transcription.text, openai_api_key, revision_model)
        if revision.accepted:
            text = revision.text
            reason = "openai_vision_revised"
        elif revision.reason not in {"openai_revision_request_failed", "invalid_openai_response"}:
            accepted = False
            text = ""
            reason = f"openai_revision_rejected;{revision.reason}"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    capture_file = OUTPUT_DIR / f"{timestamp}_capture.png"
    page_file = OUTPUT_DIR / f"{timestamp}_page.png"
    metrics_file = OUTPUT_DIR / f"{timestamp}_metrics.json"
    cv2.imwrite(str(capture_file), frames[0])
    cv2.imwrite(str(page_file), page)
    metrics_file.write_text(
        json.dumps(
            {
                "accepted": accepted,
                "reason": reason,
                "page_detected": page_detected,
                "metrics": {"sharpness": round(sharpness, 2), "brightness": round(brightness, 2)},
                "openai_review": asdict(transcription),
                "openai_revision": asdict(revision) if revision else None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if not accepted:
        print(f"AI OCR rejected ({reason}). Diagnostics saved; adjust the page and recapture.")
        return None
    if is_duplicate_text(text, last_saved_text):
        print("Duplicate page detected. Skipping save.")
        return None

    text_file = OUTPUT_DIR / f"{timestamp}_page.txt"
    text_file.write_text(text, encoding="utf-8")
    last_saved_text = text
    print(f"AI OCR accepted. Saved: {text_file}")
    print("\nTEXT PREVIEW:")
    print(text[:800])
    return text


def save_ocr_result(
    frames: list[np.ndarray],
    openai_api_key: str | None = None,
    openai_model: str | None = None,
    openai_revision_model: str | None = None,
) -> str | None:
    global last_saved_text
    print("Running OCR across sharp camera frames...")
    if openai_api_key and openai_model:
        return save_ai_ocr_result(frames, openai_api_key, openai_model, openai_revision_model)
    result = choose_page_result(frames)
    if result is None:
        print("No camera frames were available. Please recapture the page.")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    capture_file = OUTPUT_DIR / f"{timestamp}_capture.png"
    page_file = OUTPUT_DIR / f"{timestamp}_page.png"
    processed_file = OUTPUT_DIR / f"{timestamp}_processed.png"
    words_file = OUTPUT_DIR / f"{timestamp}_words.png"
    metrics_file = OUTPUT_DIR / f"{timestamp}_metrics.json"
    cv2.imwrite(str(capture_file), frames[0])
    cv2.imwrite(str(page_file), result.page)
    cv2.imwrite(str(processed_file), result.processed)
    cv2.imwrite(str(words_file), draw_word_overlay(result))
    metrics_file.write_text(
        json.dumps(
            {
                "accepted": result.accepted,
                "reason": result.reason,
                "preprocess": result.preprocess_name,
                "psm": result.psm,
                "page_detected": result.page_detected,
                "metrics": asdict(result.metrics),
                "openai_review": None,
                "openai_revision": None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if not result.accepted:
        print(f"OCR rejected ({result.reason}). Diagnostics saved; adjust the page and recapture.")
        return None
    if is_duplicate_text(result.text, last_saved_text):
        print("Duplicate page detected. Skipping save.")
        return None

    text_file = OUTPUT_DIR / f"{timestamp}_page.txt"
    text_file.write_text(result.text, encoding="utf-8")
    last_saved_text = result.text
    print(f"OCR accepted. Saved: {text_file}")
    print("\nTEXT PREVIEW:")
    print(result.text[:800])
    return result.text


def flip_page() -> None:
    """Hardware placeholder. Replace with an Arduino/serial command later."""
    print("FLIP command sent to hardware.")


def open_camera(camera_index: int) -> cv2.VideoCapture:
    """Open a Windows camera quickly, then fall back to OpenCV's default backend."""
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(camera_index)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan a book page with a USB camera and Tesseract.")
    parser.add_argument("--camera", type=int, default=CAMERA_INDEX, help="OpenCV camera index (default: 1).")
    parser.add_argument(
        "--local-ocr",
        action="store_true",
        help="Use the legacy local Tesseract fallback instead of AI vision OCR.",
    )
    parser.add_argument(
        "--openai-model",
        default=os.getenv("OPENAI_OCR_MODEL") or os.getenv("OPENAI_MODEL", ""),
        help="Vision-capable Responses API model. Defaults to OPENAI_OCR_MODEL, then OPENAI_MODEL.",
    )
    parser.add_argument(
        "--openai-revision-model",
        default=os.getenv("OPENAI_OCR_REVIEW_MODEL") or os.getenv("OPENAI_OCR_MODEL") or os.getenv("OPENAI_MODEL", ""),
        help="Vision-capable model for the conditional final revision pass.",
    )
    args = parser.parse_args()

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_model = args.openai_model.strip()
    openai_revision_model = args.openai_revision_model.strip()
    if not args.local_ocr and (not openai_api_key or not openai_model):
        parser.error("AI OCR requires OPENAI_API_KEY and OPENAI_OCR_MODEL, OPENAI_MODEL, or --openai-model.")

    print(f"Opening camera index {args.camera}...")
    cap = open_camera(args.camera)
    if not cap.isOpened():
        print(f"Camera {args.camera} could not be opened. Close other camera apps and try --camera 0.")
        return 1

    print(f"Camera {args.camera} opened.")
    print("Press f to flip + scan, c to scan, or q to quit.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Failed to read frame.")
                break
            preview = frame.copy()
            height, width = preview.shape[:2]
            top, bottom = int(height * 0.10), int(height * 0.90)
            left, right = int(width * 0.08), int(width * 0.92)
            cv2.rectangle(preview, (left, top), (right, bottom), (255, 255, 0), 2)
            cv2.putText(
                preview,
                "f = flip + scan | c = scan | q = quit",
                (30, preview.shape[0] - 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )
            cv2.imshow("Book OCR", preview)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("f"):
                flip_page()
                print(f"Waiting {FLIP_WAIT_SECONDS} seconds for the page to settle...")
                time.sleep(FLIP_WAIT_SECONDS)
                save_ocr_result(
                    capture_best_frames(cap), None if args.local_ocr else openai_api_key, openai_model, openai_revision_model
                )
            elif key == ord("c"):
                save_ocr_result(
                    capture_best_frames(cap), None if args.local_ocr else openai_api_key, openai_model, openai_revision_model
                )
    finally:
        cap.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
