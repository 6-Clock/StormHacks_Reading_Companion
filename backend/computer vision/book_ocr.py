import cv2
import numpy as np
import pytesseract
from datetime import datetime
from pathlib import Path
import time
from difflib import SequenceMatcher

# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

CAMERA_INDEX = 1
OUTPUT_DIR = Path("camera_ocr_output")
OUTPUT_DIR.mkdir(exist_ok=True)

LANG = "eng"

FLIP_WAIT_SECONDS = 1.5

DUPLICATE_TEXT_THRESHOLD = 0.88
last_saved_text = ""


def crop_page_area(frame):
    height, width = frame.shape[:2]

    top = int(height * 0.10)
    bottom = int(height * 0.90)
    left = int(width * 0.08)
    right = int(width * 0.92)

    return frame[top:bottom, left:right]


def clean_ocr_text(text):
    cleaned_lines = []

    for line in text.splitlines():
        line = line.strip()

        if len(line) < 3:
            continue

        letters = sum(ch.isalpha() for ch in line)
        digits = sum(ch.isdigit() for ch in line)
        useful_chars = letters + digits
        total_chars = len(line)

        if total_chars == 0:
            continue

        useful_ratio = useful_chars / total_chars

        if useful_ratio < 0.35:
            continue

        unique_chars = len(set(line))
        if unique_chars <= 2 and total_chars >= 5:
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def preprocess_for_ocr(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    gray = cv2.fastNlMeansDenoising(gray, h=10)

    gray = cv2.resize(
        gray,
        None,
        fx=1.8,
        fy=1.8,
        interpolation=cv2.INTER_CUBIC,
    )

    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    thresholded = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35,
        11,
    )

    kernel = np.ones((2, 2), np.uint8)
    thresholded = cv2.morphologyEx(thresholded, cv2.MORPH_OPEN, kernel)

    return thresholded


def run_ocr(frame):
    cropped = crop_page_area(frame)
    processed = preprocess_for_ocr(cropped)

    text = pytesseract.image_to_string(
        processed,
        lang=LANG,
        config="--oem 3 --psm 4",
    )

    text = clean_ocr_text(text)

    return text.strip(), processed, cropped


def is_duplicate_text(new_text, old_text):
    if not new_text.strip() or not old_text.strip():
        return False

    similarity = SequenceMatcher(None, new_text, old_text).ratio()
    print(f"Text similarity with previous page: {similarity:.2f}")

    return similarity >= DUPLICATE_TEXT_THRESHOLD


def save_ocr_result(frame):
    global last_saved_text

    print("Running OCR...")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    text, processed, cropped = run_ocr(frame)

    if len(text.strip()) < 20:
        print("OCR result is too short. Skipping save.")
        return None

    if is_duplicate_text(text, last_saved_text):
        print("Duplicate page detected. Skipping save.")
        return None

    text_file = OUTPUT_DIR / f"{timestamp}_page.txt"
    debug_file = OUTPUT_DIR / f"{timestamp}_debug.png"
    cropped_file = OUTPUT_DIR / f"{timestamp}_cropped.png"
    capture_file = OUTPUT_DIR / f"{timestamp}_capture.png"

    text_file.write_text(text, encoding="utf-8")

    cv2.imwrite(str(debug_file), processed)
    cv2.imwrite(str(cropped_file), cropped)
    cv2.imwrite(str(capture_file), frame)

    last_saved_text = text

    print("OCR done.")
    print(f"Saved: {text_file}")

    print("\nTEXT PREVIEW:")
    print(text[:800])

    return text


def flip_page():
    """
    Hardware placeholder.
    Later, replace this with Arduino/serial command.
    """
    print("FLIP command sent to hardware.")

    # Example later:
    # arduino.write(b"FLIP\n")


def capture_latest_frame(cap, warmup_frames=5):
    frame = None

    for _ in range(warmup_frames):
        ret, frame = cap.read()
        if not ret:
            return None

    return frame


cap = cv2.VideoCapture(CAMERA_INDEX)

if not cap.isOpened():
    print(f"Camera {CAMERA_INDEX} could not be opened.")
    exit()

print(f"Camera {CAMERA_INDEX} opened.")
print("Press f to flip page and OCR.")
print("Press c to OCR current page without flip.")
print("Press q to quit.")

while True:
    ret, frame = cap.read()

    if not ret:
        print("Failed to read frame.")
        break

    preview = frame.copy()

    height, width = preview.shape[:2]
    top = int(height * 0.10)
    bottom = int(height * 0.90)
    left = int(width * 0.08)
    right = int(width * 0.92)

    cv2.rectangle(preview, (left, top), (right, bottom), (255, 255, 0), 2)

    cv2.putText(
        preview,
        "f = flip + OCR | c = OCR only | q = quit",
        (30, preview.shape[0] - 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
    )

    cv2.imshow("Hardware Triggered Book OCR", preview)

    key = cv2.waitKey(1) & 0xFF

    if key == ord("f"):
        flip_page()

        print(f"Waiting {FLIP_WAIT_SECONDS} seconds for page to settle...")
        time.sleep(FLIP_WAIT_SECONDS)

        latest_frame = capture_latest_frame(cap)

        if latest_frame is not None:
            save_ocr_result(latest_frame)
        else:
            print("Could not capture frame after flip.")

    elif key == ord("c"):
        latest_frame = capture_latest_frame(cap)

        if latest_frame is not None:
            save_ocr_result(latest_frame)
        else:
            print("Could not capture current frame.")

    elif key == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()