"""Vision OCR service using LangChain and ChatOpenAI."""
import base64
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from app.config import OPENAI_API_KEY

logger = logging.getLogger("docuintel.services.vision_ocr")

# LangChain Chat Model for Vision OCR
vision_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.0,
    api_key=OPENAI_API_KEY,
    max_tokens=2000,
)


def ocr_image(image_bytes: bytes) -> str:
    """Uses LangChain ChatOpenAI Vision to transcribe text and tables from image bytes."""
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        messages = [
            SystemMessage(
                content="You are a professional OCR engine. Transcribe all text, numbers, labels, and tables visible in this image accurately without summarizing."
            ),
            HumanMessage(
                content=[
                    {"type": "text", "text": "Extract all readable text from this document image:"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ]
            ),
        ]
        response = vision_llm.invoke(messages)
        return response.content if isinstance(response.content, str) else str(response.content)
    except Exception as exc:
        logger.error(f"LangChain Vision OCR error: {exc}")
        return ""
