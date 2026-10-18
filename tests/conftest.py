import sys
from pathlib import Path

import pymupdf as fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anonywiz  # noqa: E402

# valid test values (check digits are correct)
AHV = "756.1234.5678.97"
IBAN = "CH93 0076 2011 6238 5295 7"
IBAN_BAD = "CH94 0076 2011 6238 5295 7"
CARD = "4111 1111 1111 1111"
EMAIL = "max.muster@example.ch"
PHONE = "044 123 45 67"
UID = "CHE-123.456.789"
ISIN = "CH0012221716"
STREET = "Bahnhofstrasse 12"
PLZ_ORT = "8001 Zürich"
DOB = "geboren 01.02.1980"
DATE = "Stichtag 31.12.2023"


@pytest.fixture(autouse=True)
def _no_tesseract(monkeypatch):
    """Tests run independently of a local Tesseract installation."""
    monkeypatch.setattr(anonywiz, "_TESS", None)


def make_pdf(path, lines=(), pages=1, setup=None):
    """Creates a PDF with lines of text; setup(doc, page) may modify it further."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        y = 80
        for line in lines:
            page.insert_text((72, y), line, fontsize=11)
            y += 20
        if setup:
            setup(doc, page)
    doc.save(path)
    doc.close()
    return path


def page_text(path):
    with fitz.open(path) as d:
        return "\n".join(p.get_text() for p in d)


def categories(findings):
    return {f["cat"] for f in findings}


def draw_barcode(page, x=100, y=300, bars=40, h=40):
    for i in range(bars):
        w = 1 if i % 2 else 2
        page.draw_rect(fitz.Rect(x + i * 4, y, x + i * 4 + w, y + h), color=None, fill=(0, 0, 0))


@pytest.fixture
def sample_pdf(tmp_path):
    lines = [f"AHV {AHV}", f"IBAN {IBAN}", f"Mail {EMAIL}", f"Tel {PHONE}", UID, ISIN,
             STREET, PLZ_ORT, DATE, "Ehefrau: Anna Beispiel", "Total CHF 1000", DOB]
    return make_pdf(tmp_path / "sample.pdf", lines)
