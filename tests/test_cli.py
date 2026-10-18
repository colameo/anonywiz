import json

import pymupdf as fitz
import pytest

import anonywiz as aw
from conftest import AHV, EMAIL, IBAN, draw_barcode, make_pdf, page_text


def run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["anonywiz", *map(str, argv)])
    monkeypatch.setattr(aw, "BARCODE_PAD", 4)
    aw.main()


def exit_code(monkeypatch, *argv):
    with pytest.raises(SystemExit) as e:
        run(monkeypatch, *argv)
    return e.value.code


@pytest.fixture
def terms_file(tmp_path):
    f = tmp_path / "terms.txt"
    f.write_text("# Namen\nBeispiel\nAnna\n", encoding="utf-8")
    return f


# --- arguments / error cases ------------------------------------------------------------

def test_version(monkeypatch, capsys):
    assert exit_code(monkeypatch, "--version") == 0
    assert aw.__version__ in capsys.readouterr().out


def test_missing_file(monkeypatch, tmp_path):
    code = exit_code(monkeypatch, tmp_path / "nope.pdf")
    assert "File not found" in str(code)


def test_not_a_pdf(monkeypatch, tmp_path):
    f = tmp_path / "x.pdf"
    f.write_text("das ist kein pdf")
    assert "is not a PDF" in str(exit_code(monkeypatch, f))


def test_broken_pdf(monkeypatch, tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4\ngarbage")
    assert "PDF" in str(exit_code(monkeypatch, f))


def test_password_protected(monkeypatch, tmp_path):
    src = make_pdf(tmp_path / "p.pdf", ["x"])
    enc = tmp_path / "enc.pdf"
    with fitz.open(src) as d:
        d.save(enc, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="geheim", owner_pw="geheim")
    assert "password-protected" in str(exit_code(monkeypatch, enc))


# --- --check ---------------------------------------------------------------------------------

def test_check_exit_codes(monkeypatch, tmp_path, sample_pdf):
    assert exit_code(monkeypatch, sample_pdf, "--check", "--no-ner") == 2
    clean = make_pdf(tmp_path / "clean.pdf", ["Total CHF 1000"])
    assert exit_code(monkeypatch, clean, "--check", "--no-ner") == 0
    medium = make_pdf(tmp_path / "m.pdf", ["Karte 4111 1111 1111 1111"])
    assert exit_code(monkeypatch, medium, "--check", "--no-ner") == 1


def test_check_does_not_modify(monkeypatch, sample_pdf):
    before = sample_pdf.read_bytes()
    exit_code(monkeypatch, sample_pdf, "--check", "--no-ner")
    assert sample_pdf.read_bytes() == before


def test_check_json_masked_and_unmasked(monkeypatch, sample_pdf, tmp_path):
    out = tmp_path / "r.json"
    exit_code(monkeypatch, sample_pdf, "--check", "--no-ner", "--json", out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data and AHV not in out.read_text(encoding="utf-8")
    assert all("rect" in x and "sev" in x for x in data)
    exit_code(monkeypatch, sample_pdf, "--check", "--no-ner", "--json", out, "--show-values")
    assert AHV in out.read_text(encoding="utf-8")


def test_check_with_terms(monkeypatch, sample_pdf, terms_file, capsys):
    exit_code(monkeypatch, sample_pdf, "-t", terms_file, "--check", "--no-ner", "--show-values")
    assert "Term (terms.txt)" in capsys.readouterr().out


# --- anonymising ----------------------------------------------------------------------------

def test_anonymise_removes_sensitive_text(monkeypatch, sample_pdf, terms_file, tmp_path, capsys):
    out = tmp_path / "out.pdf"
    run(monkeypatch, sample_pdf, "-t", terms_file, "-o", out, "--no-ner")
    text = page_text(out)
    for secret in (AHV, IBAN, EMAIL, "Beispiel", "Anna", "Bahnhofstrasse", "Zürich",
                   "01.02.1980", "31.12.2023", "044 123 45 67", "CHE-123"):
        assert secret not in text, secret
    assert "Total CHF 1000" in text            # amounts stay visible
    assert "Saved:" in capsys.readouterr().out


def test_anonymise_default_output_name_and_preview(monkeypatch, sample_pdf):
    run(monkeypatch, sample_pdf, "--no-ner")
    out = sample_pdf.with_name("sample_anonymized.pdf")
    assert out.is_file()
    assert (sample_pdf.parent / "sample_anonymized_preview" / "seite_01.png").is_file()


def test_no_preview(monkeypatch, sample_pdf):
    run(monkeypatch, sample_pdf, "--no-ner", "--no-preview")
    assert not (sample_pdf.parent / "sample_anonymized_preview").exists()


def test_output_gets_clean_check(monkeypatch, sample_pdf, tmp_path):
    out = tmp_path / "o.pdf"
    run(monkeypatch, sample_pdf, "-o", out, "--no-ner")
    with fitz.open(out) as doc:
        F = aw.scan_document(doc, [], use_ner=False)
    assert not [f for f in F if f["sev"] == "HIGH"]


def test_keep_dates(monkeypatch, sample_pdf, tmp_path):
    out = tmp_path / "o.pdf"
    run(monkeypatch, sample_pdf, "-o", out, "--no-ner", "--keep-dates")
    text = page_text(out)
    assert "31.12.2023" in text and AHV not in text


def test_metadata_and_attachments_removed(monkeypatch, tmp_path):
    def setup(doc, page):
        doc.set_metadata({"author": "Max Muster", "title": "Steuer"})
        doc.embfile_add("daten.xml", b"<x>geheim</x>")
    src = make_pdf(tmp_path / "m.pdf", ["Total CHF 5"], setup=setup)
    out = tmp_path / "o.pdf"
    run(monkeypatch, src, "-o", out, "--no-ner", "--no-preview")
    with fitz.open(out) as doc:
        assert not doc.metadata.get("author") and not doc.metadata.get("title")
        assert doc.embfile_names() == []
        assert not doc.get_xml_metadata().strip()


def test_pages_selection(monkeypatch, tmp_path):
    src = tmp_path / "multi.pdf"
    doc = fitz.open()
    for i in range(1, 4):
        doc.new_page().insert_text((72, 100), f"Seite Nummer {i}")
    doc.save(src)
    out = tmp_path / "o.pdf"
    run(monkeypatch, src, "-o", out, "--pages", "1,3", "--no-ner", "--no-preview")
    with fitz.open(out) as d:
        assert d.page_count == 2
    text = page_text(out)
    assert "Nummer 1" in text and "Nummer 3" in text and "Nummer 2" not in text


def test_region_on_specific_page_and_all(monkeypatch, tmp_path):
    src = tmp_path / "multi.pdf"
    doc = fitz.open()
    for i in range(1, 3):
        doc.new_page().insert_text((72, 100), f"Kopf {i}")
    doc.save(src)
    out = tmp_path / "o.pdf"
    run(monkeypatch, src, "-o", out, "--region", "1:0,0,100,20", "--no-ner", "--no-preview")
    with fitz.open(out) as d:
        assert "Kopf" not in d[0].get_text()
        assert "Kopf 2" in d[1].get_text()
    run(monkeypatch, src, "-o", out, "--region", "*:0,0,100,20", "--no-ner", "--no-preview")
    assert "Kopf" not in page_text(out)


def test_images_and_barcodes_removed_unless_kept(monkeypatch, tmp_path):
    def setup(doc, page):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        pix.set_rect(pix.irect, (10, 10, 10))
        page.insert_image(fitz.Rect(300, 50, 350, 100), pixmap=pix)
        draw_barcode(page)
    src = make_pdf(tmp_path / "i.pdf", ["Text"], setup=setup)
    out = tmp_path / "o.pdf"
    run(monkeypatch, src, "-o", out, "--no-ner", "--no-preview")
    with fitz.open(out) as d:
        assert d[0].get_images() == []
        assert aw.barcode_boxes(d[0]) == []
        assert "Text" in d[0].get_text()
    run(monkeypatch, src, "-o", out, "--no-ner", "--no-preview", "--keep-images")
    with fitz.open(out) as d:
        assert d[0].get_images() != []


def test_remove_scans(monkeypatch, tmp_path):
    def setup(doc, page):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
        pix.set_rect(pix.irect, (10, 10, 10))
        page.insert_image(page.rect, pixmap=pix)
    src = make_pdf(tmp_path / "s.pdf", setup=setup)
    out = tmp_path / "o.pdf"
    run(monkeypatch, src, "-o", out, "--remove-scans", "--no-ner", "--no-preview")
    with fitz.open(out) as d:
        assert d[0].get_images() == []


def test_scan_without_tesseract_warns(monkeypatch, tmp_path, capsys):
    def setup(doc, page):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
        pix.set_rect(pix.irect, (10, 10, 10))
        page.insert_image(page.rect, pixmap=pix)
    src = make_pdf(tmp_path / "s.pdf", setup=setup)
    run(monkeypatch, src, "-o", tmp_path / "o.pdf", "--no-ner", "--no-preview")
    assert "Tesseract (OCR) is missing" in capsys.readouterr().out


def test_pseudo_redaction_is_removed(monkeypatch, tmp_path):
    def setup(doc, page):
        page.insert_text((72, 300), "versteckt", fontsize=11, color=(0, 0, 0))
        page.draw_rect(fitz.Rect(60, 285, 200, 305), color=None, fill=(0, 0, 0))
    src = make_pdf(tmp_path / "h.pdf", ["sichtbar", "eins", "zwei", "drei"], setup=setup)
    out = tmp_path / "o.pdf"
    run(monkeypatch, src, "-o", out, "--no-ner", "--no-preview")
    text = page_text(out)
    assert "versteckt" not in text and "sichtbar" in text


def test_hidden_text_majority_is_treated_as_false_positive(monkeypatch, tmp_path, capsys):
    """If >30 % of the text counts as hidden, nothing is redacted (false detection)."""
    def setup(doc, page):
        page.insert_text((72, 300), "versteckt", fontsize=11, color=(0, 0, 0))
        page.draw_rect(fitz.Rect(60, 285, 200, 305), color=None, fill=(0, 0, 0))
    src = make_pdf(tmp_path / "h.pdf", ["sichtbar"], setup=setup)
    out = tmp_path / "o.pdf"
    run(monkeypatch, src, "-o", out, "--no-ner", "--no-preview")
    assert "looks like a false detection" in capsys.readouterr().out
    assert "versteckt" in page_text(out)


def test_barcode_pad_option_sets_global(monkeypatch, sample_pdf, tmp_path):
    run(monkeypatch, sample_pdf, "-o", tmp_path / "o.pdf", "--no-ner", "--no-preview",
        "--barcode-pad", "12")
    assert aw.BARCODE_PAD == 12


# --- --inspect / --debug-barcodes ---------------------------------------------------------------

def test_inspect(monkeypatch, tmp_path, capsys):
    def setup(doc, page):
        draw_barcode(page)
    src = make_pdf(tmp_path / "i.pdf", ["Text"], setup=setup)
    run(monkeypatch, src, "--inspect")
    out = capsys.readouterr().out
    assert "Page 1:" in out and "Barcode (vector)" in out and "--region 1:" in out


def test_inspect_flags_pages_without_text(monkeypatch, tmp_path, capsys):
    src = make_pdf(tmp_path / "e.pdf", [])
    run(monkeypatch, src, "--inspect")
    assert "no readable text" in capsys.readouterr().out


def test_debug_barcodes(monkeypatch, tmp_path, capsys):
    src = make_pdf(tmp_path / "d.pdf", ["Text"], setup=lambda d, p: draw_barcode(p))
    run(monkeypatch, src, "--debug-barcodes")
    assert (tmp_path / "d_debug" / "debug_seite_01.png").is_file()
    assert "candidates" in capsys.readouterr().out


# --- scan paths with stubs (OCR / barcode reader) --------------------------------------------------

def _scan_pdf(tmp_path, lines=()):
    def setup(doc, page):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
        pix.set_rect(pix.irect, (255, 255, 255))
        page.insert_image(page.rect, pixmap=pix)
    return make_pdf(tmp_path / "scan.pdf", lines, setup=setup)


def test_scan_path_with_ocr_and_barcode_stub(monkeypatch, tmp_path, capsys):
    src = _scan_pdf(tmp_path, [f"AHV {AHV}"])
    monkeypatch.setattr(aw, "ocr_textpage", lambda page: page.get_textpage())
    monkeypatch.setattr(aw, "scan_barcodes", lambda page, area: [fitz.Rect(10, 10, 60, 40)])
    out = tmp_path / "o.pdf"
    run(monkeypatch, src, "-o", out, "--no-ner", "--no-preview")
    text = capsys.readouterr().out
    assert "Scan checked via OCR" in text and "Barcode in scan" in text
    assert AHV not in page_text(out)


def test_scan_without_barcode_reader_warns(monkeypatch, tmp_path, capsys):
    src = _scan_pdf(tmp_path)
    monkeypatch.setattr(aw, "scan_barcodes", lambda page, area: None)
    run(monkeypatch, src, "-o", tmp_path / "o.pdf", "--no-ner", "--no-preview")
    assert "barcodes in scan not checkable" in capsys.readouterr().out


def test_redact_unreadable_option(monkeypatch, sample_pdf, tmp_path, capsys):
    monkeypatch.setattr(aw, "unreadable_spans", lambda page: [fitz.Rect(70, 60, 200, 100)])
    out = tmp_path / "o.pdf"
    run(monkeypatch, sample_pdf, "-o", out, "--no-ner", "--no-preview")
    assert "not readable" in capsys.readouterr().out
    run(monkeypatch, sample_pdf, "-o", out, "--no-ner", "--no-preview", "--redact-unreadable")
    assert "Unreadable text" in capsys.readouterr().out


def test_scan_barcodes_with_stub_reader(monkeypatch, tmp_path):
    import sys
    import types

    class P:
        def __init__(self, x, y):
            self.x, self.y = x, y

    class Pos:
        top_left, top_right = P(10, 10), P(50, 10)
        bottom_left, bottom_right = P(10, 30), P(50, 30)

    class Res:
        position = Pos()

    stub = types.SimpleNamespace(read_barcodes=lambda img: [Res()])
    monkeypatch.setitem(sys.modules, "zxingcpp", stub)
    src = make_pdf(tmp_path / "s.pdf", ["x"])
    with fitz.open(src) as doc:
        boxes = aw.scan_barcodes(doc[0], doc[0].rect)
    assert len(boxes) == 1 and boxes[0].width > 40 * 72 / 200


def test_polygon_barcode_is_split_into_bars(tmp_path):
    def setup(doc, page):
        shape = page.new_shape()
        for i in range(30):
            x = 100 + i * 5
            shape.draw_polyline([(x, 300), (x + 2, 300), (x + 2, 340), (x, 340), (x, 300)])
        shape.finish(color=None, fill=(0, 0, 0), closePath=False)
        shape.commit()
    src = make_pdf(tmp_path / "poly.pdf", ["x"], setup=setup)
    with fitz.open(src) as doc:
        assert len(aw.barcode_boxes(doc[0])) >= 1


# --- automatic names ----------------------------------------------------------------------------

def _name_pdf(tmp_path):
    return make_pdf(tmp_path / "n.pdf", ["Ehefrau: Anna Beispiel", "Später im Text:",
                                         "Beispiel und Anna wohnen hier.", "Annahme bleibt."])


def test_names_after_label_are_redacted_everywhere(monkeypatch, tmp_path, capsys):
    src = _name_pdf(tmp_path)
    out = tmp_path / "o.pdf"
    run(monkeypatch, src, "-o", out, "--no-ner", "--no-preview")
    text = page_text(out)
    assert "Anna" not in text.replace("Annahme", "") and "Beispiel" not in text
    assert "Annahme bleibt" in text      # whole word, not a substring
    msg = capsys.readouterr().out
    assert "Automatically detected names" in msg and "Name (automatic)" in msg


def test_no_auto_names(monkeypatch, tmp_path):
    src = _name_pdf(tmp_path)
    out = tmp_path / "o.pdf"
    run(monkeypatch, src, "-o", out, "--no-ner", "--no-preview", "--no-auto-names")
    assert "Beispiel und Anna wohnen hier." in page_text(out)
