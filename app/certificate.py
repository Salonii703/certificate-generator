"""PDF generation using the single predefined certificate template."""
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1f3a5f")
GOLD = colors.HexColor("#b8962e")


def _fit_font_size(text: str, font: str, max_size: float, max_width: float) -> float:
    """Shrink the font until the text fits on one line."""
    size = max_size
    while size > 10 and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def generate_certificate(
    recipient_name: str, event_name: str, issued_date: date, out_path: Path
) -> Path:
    """Render one certificate PDF to out_path and return the path.

    Raises an exception if the PDF cannot be produced (e.g. a name containing
    characters the built-in font cannot draw). The caller treats that as a
    failure of this single certificate only.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Fail early on text the standard Helvetica font cannot encode.
    for text in (recipient_name, event_name):
        text.encode("latin-1")

    c = canvas.Canvas(str(out_path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Certificate - {recipient_name}")

    # Double border
    c.setStrokeColor(NAVY)
    c.setLineWidth(6)
    c.rect(25, 25, PAGE_W - 50, PAGE_H - 50)
    c.setStrokeColor(GOLD)
    c.setLineWidth(2)
    c.rect(38, 38, PAGE_W - 76, PAGE_H - 76)

    cx = PAGE_W / 2
    max_text_w = PAGE_W - 160

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 40)
    c.drawCentredString(cx, PAGE_H - 130, "CERTIFICATE")
    c.setFont("Helvetica", 18)
    c.drawCentredString(cx, PAGE_H - 160, "OF PARTICIPATION")

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 16)
    c.drawCentredString(cx, PAGE_H - 215, "This is to certify that")

    name_size = _fit_font_size(recipient_name, "Helvetica-Bold", 38, max_text_w)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", name_size)
    c.drawCentredString(cx, PAGE_H - 275, recipient_name)

    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.line(cx - 200, PAGE_H - 288, cx + 200, PAGE_H - 288)

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 16)
    c.drawCentredString(cx, PAGE_H - 325, "has successfully participated in")

    event_size = _fit_font_size(event_name, "Helvetica-Bold", 26, max_text_w)
    c.setFont("Helvetica-Bold", event_size)
    c.drawCentredString(cx, PAGE_H - 365, event_name)

    c.setFont("Helvetica", 14)
    c.drawCentredString(cx, 85, f"Issued on {issued_date.strftime('%d %B %Y')}")

    c.showPage()
    c.save()
    return out_path
