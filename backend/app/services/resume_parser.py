from io import BytesIO
from pypdf import PdfReader


def extract_pdf(content: bytes) -> dict:
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported")
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        pages = len(reader.pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The PDF is corrupted or cannot be read") from exc
    if len(text) < 40:
        raise ValueError("No useful selectable text was found. Please upload a text-based PDF.")
    return {"text": text, "pages": pages, "characters_extracted": len(text)}


def extract_pdf_text(content: bytes) -> str:
    return extract_pdf(content)["text"]
