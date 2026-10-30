"""Local (offline) OCR using RapidOCR (PaddleOCR models on ONNX Runtime).

Runs on CPU with no API calls: faster and cheaper than vision-LLM OCR, nothing leaves the server, and it returns a
real per-line confidence so unreliable scans can be routed to manual review instead of being silently guessed.
"""
import io
import logging
import re
import threading
from typing import List, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger("docuintel.services.local_ocr")

_engine = None
_lock = threading.Lock()
LINE_MIN_CONFIDENCE = 0.60  # lines below this are replaced by [illegible]

# Detectors sometimes drop the narrow gap between two capitalised words ("MetroIndustrial"). Split only the safe
# case: a purely alphabetic token with >=3 lowercase letters on each side of the lower->Upper boundary.
_GLUED_WORDS = re.compile(r"\b([A-Z][a-z]{2,})([A-Z][a-z]{3,})\b")


def fix_glued_words(text: str) -> str:
    return _GLUED_WORDS.sub(r"\1 \2", text)


def _get_engine():
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR
                _engine = RapidOCR()
    return _engine


def _group_rows(items: List[Tuple[list, str, float]]) -> List[Tuple[str, float]]:
    """Merges boxes that sit on the same visual row ("Coffee x2" ... "8.00") into one line, top to bottom."""
    boxes = []
    for box, text, score in items:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append({"cy": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys), "x": min(xs), "text": text.strip(), "score": float(score)})
    if not boxes:
        return []
    boxes.sort(key=lambda b: b["cy"])
    median_h = float(np.median([b["h"] for b in boxes])) or 10.0
    rows: List[List[dict]] = [[boxes[0]]]
    for b in boxes[1:]:
        row_cy = np.mean([r["cy"] for r in rows[-1]])
        if abs(b["cy"] - row_cy) <= 0.6 * median_h:
            rows[-1].append(b)
        else:
            rows.append([b])
    lines = []
    for row in rows:
        row.sort(key=lambda b: b["x"])
        text = fix_glued_words("   ".join(b["text"] for b in row if b["text"]))
        score = float(np.mean([b["score"] for b in row]))
        if score < LINE_MIN_CONFIDENCE:
            text = "[illegible]"
        lines.append((text, score))
    return lines


def ocr_local(image_bytes: bytes) -> Tuple[str, float]:
    """Returns (text, mean_confidence 0..1). Empty text and 0.0 when nothing was recognised."""
    img = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
    result, _ = _get_engine()(img)
    if not result:
        return "", 0.0
    lines = _group_rows(result)
    text = "\n".join(t for t, _ in lines)
    conf = float(np.mean([s for _, s in lines])) if lines else 0.0
    return text, round(conf, 3)
