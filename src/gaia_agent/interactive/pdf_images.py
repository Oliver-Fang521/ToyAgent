"""Render PDF pages for image-capable models without extracting text."""
from io import BytesIO
from math import ceil

import pypdfium2 as pdfium

from gaia_agent.mcp_client import AttachmentPolicyError

MAX_PAGES = 100
MAX_PAGE_PIXELS = 4_000_000
MAX_IMAGE_BYTES = 40 * 1024 * 1024


def render_pdf_pages(data: bytes) -> list[bytes]:
    pages = []
    total = 0
    try:
        document = pdfium.PdfDocument(data)
        try:
            if not 1 <= len(document) <= MAX_PAGES:
                raise AttachmentPolicyError('pdf_page_limit')
            for index in range(len(document)):
                page = document[index]
                try:
                    width, height = page.get_size()
                    if width <= 0 or height <= 0:
                        raise AttachmentPolicyError('pdf_invalid_page')
                    scale = min(2.0, (MAX_PAGE_PIXELS / (width * height)) ** 0.5)
                    if ceil(width * scale) * ceil(height * scale) > MAX_PAGE_PIXELS:
                        scale *= 0.999
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil()
                        try:
                            output = BytesIO()
                            image.save(output, format='PNG', optimize=True)
                            encoded = output.getvalue()
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                    total += len(encoded)
                    if total > MAX_IMAGE_BYTES:
                        raise AttachmentPolicyError('pdf_image_limit')
                    pages.append(encoded)
                finally:
                    page.close()
        finally:
            document.close()
    except AttachmentPolicyError:
        raise
    except Exception:
        raise AttachmentPolicyError('pdf_render_failed') from None
    return pages
