"""Vision OCR service using LangChain and ChatOpenAI with advanced image enhancement."""
import io
import base64
import logging
from PIL import Image, ImageOps, ImageEnhance, UnidentifiedImageError
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
import threading
from app.config import OPENAI_API_KEY, OCR_ENGINE, OCR_MIN_CONFIDENCE

logger = logging.getLogger("docuintel.services.vision_ocr")

# Chat Model for Vision OCR
vision_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.0,
    api_key=OPENAI_API_KEY,
    max_tokens=2500,
)


def preprocess_image(image_bytes: bytes) -> bytes:
    """Enhances image quality: fixes phone EXIF rotation, boosts contrast/sharpness, and scales optimally."""
    if not image_bytes or len(image_bytes) == 0:
        raise ValueError("Image file is empty (0 bytes).")

    try:
        # Step 1: Verify image integrity
        try:
            verifier = Image.open(io.BytesIO(image_bytes))
            verifier.verify()
        except Exception as verif_err:
            raise ValueError(f"Corrupted or invalid image file: {verif_err}")

        # Step 2: Open image for processing (reopened as verify() consumes stream)
        img = Image.open(io.BytesIO(image_bytes))

        # 1. Correct smartphone/scanner EXIF rotation
        img = ImageOps.exif_transpose(img)

        # 2. Normalize color mode to RGB
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # 3. Enhance contrast for low-contrast/faded scans and thermal receipts
        if img.mode == "L":
            img = ImageOps.autocontrast(img, cutoff=2)
            img = img.convert("RGB")
        else:
            img = ImageOps.autocontrast(img, cutoff=1)

        # 4. Enhance contrast and sharpness for blurry/faded captures
        try:
            contrast_enhancer = ImageEnhance.Contrast(img)
            img = contrast_enhancer.enhance(1.25)
            sharpness_enhancer = ImageEnhance.Sharpness(img)
            img = sharpness_enhancer.enhance(1.15)
        except Exception as enh_err:
            logger.debug(f"Pillow enhancement skipped: {enh_err}")

        # 4b. Upscale small images so fine print survives (OCR accuracy drops sharply on tiny text)
        min_dim = 1400
        if max(img.size) < min_dim:
            factor = min_dim / max(img.size)
            img = img.resize((int(img.width * factor), int(img.height * factor)), Image.Resampling.LANCZOS)

        # 5. Scale down if huge (>2400px on longest side) to optimize processing while retaining high text clarity
        max_dim = 2400
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        out_buf = io.BytesIO()
        img.save(out_buf, format="JPEG", quality=92, optimize=True)
        return out_buf.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        logger.warning(f"Image preprocessing notice: {exc}")
        return image_bytes


_meta = threading.local()


def reset_ocr_meta():
    _meta.value = {"engine": None, "confidence": None, "low_confidence": False, "pages": 0}


def get_ocr_meta() -> dict:
    """OCR facts about the current thread's last extraction (engine used, min confidence, low-confidence flag)."""
    if not hasattr(_meta, "value"):
        reset_ocr_meta()
    return dict(_meta.value)


def _record(engine: str, confidence, low: bool):
    if not hasattr(_meta, "value"):
        reset_ocr_meta()
    m = _meta.value
    m["pages"] += 1
    m["engine"] = engine if m["engine"] in (None, engine) else "mixed"
    if confidence is not None:
        m["confidence"] = confidence if m["confidence"] is None else min(m["confidence"], confidence)
    m["low_confidence"] = m["low_confidence"] or low


def ocr_image(image_bytes: bytes) -> str:
    """Local OCR first (fast, private, with real confidence); vision-LLM fallback when the local read is unsure."""
    if not image_bytes:
        raise ValueError("Image file is empty (0 bytes).")
    if OCR_ENGINE == "vision":
        text = ocr_image_vision(image_bytes)
        _record("vision", None, False)
        return text

    local_text, local_conf = "", 0.0
    try:
        from app.services.local_ocr import ocr_local
        local_text, local_conf = ocr_local(preprocess_image(image_bytes))
    except ValueError:
        raise
    except Exception as exc:
        logger.warning(f"Local OCR unavailable ({exc}); using vision OCR.")
        if OCR_ENGINE == "local":
            raise ValueError(f"Local OCR failed: {exc}")

    if OCR_ENGINE == "local":
        _record("local", local_conf, local_conf < OCR_MIN_CONFIDENCE)
        return local_text
    if local_text.strip() and local_conf >= OCR_MIN_CONFIDENCE:
        _record("local", local_conf, False)
        return local_text
    logger.info(f"Local OCR confidence {local_conf:.2f} < {OCR_MIN_CONFIDENCE}; falling back to vision OCR.")
    text = ocr_image_vision(image_bytes)
    _record("vision", local_conf, True)  # the local engine could not read it reliably, so flag for review
    return text


def ocr_image_vision(image_bytes: bytes) -> str:
    """Transcribes text, numbers, labels, and tables accurately even from poor-quality images."""
    if not image_bytes or len(image_bytes) == 0:
        raise ValueError("Image file is empty (0 bytes).")

    try:
        enhanced_bytes = preprocess_image(image_bytes)
        b64 = base64.b64encode(enhanced_bytes).decode("utf-8")

        messages = [
            SystemMessage(
                content=(
                    "You are a professional OCR engine. Transcribe all text, numbers, labels, and tables visible in this document accurately. "
                    "Maintain table rows and column layouts. Transcribe exactly what is printed; never guess, complete, or infer text. "
                    "If any word or number is blurry, faded, cut off, or otherwise not clearly legible, write [illegible] in its place instead of guessing. "
                    "Output the transcription only, with no commentary or code fences."
                )
            ),
            HumanMessage(
                content=[
                    {"type": "text", "text": "Extract all readable text, line items, and key-value fields from this document image:"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ]
            ),
        ]
        response = vision_llm.invoke(messages)
        return response.content if isinstance(response.content, str) else str(response.content)
    except ValueError:
        raise
    except Exception as exc:
        logger.error(f"Vision OCR error: {exc}")
        raise ValueError(f"Optical text recognition failed: {exc}")

