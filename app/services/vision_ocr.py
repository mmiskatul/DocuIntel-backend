"""Vision OCR service using OpenAI GPT-4o-mini."""
import base64
import logging
from typing import Optional
from openai import OpenAI
from app.config import OPENAI_API_KEY

logger = logging.getLogger("docuintel.services.vision_ocr")

_client: Optional[OpenAI] = None


def get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not configured.")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def ocr_image(image_bytes: bytes) -> str:
    """Uses OpenAI GPT-4o-mini Vision to transcribe text and tables from image bytes."""
    try:
        client = get_openai_client()
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional OCR engine. Transcribe all text, numbers, labels, and tables visible in this image accurately without summarizing.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all readable text from this document image:"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                },
            ],
            max_tokens=2000,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        logger.error(f"OpenAI Vision OCR error: {exc}")
        return ""
