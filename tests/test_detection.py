import pymupdf as fitz
import pytest

import anonywiz as aw
from conftest import (AHV, CARD, DOB, EMAIL, IBAN, IBAN_BAD, ISIN, PHONE, PLZ_ORT, STREET, UID,
                      categories, draw_barcode, make_pdf)


def scan(path, terms=(), **kw):
    kw.setdefault("use_ner", False)
    with fitz.open(path) as doc:
        return aw.scan_document(doc, list(terms), **kw)


def by_cat(F, cat):
    return [f for f in F if f["cat"] == cat]


# --- find_hits ------------------------------------------------------------------

def test_find_hits_patterns_and_terms(sample_pdf):
    with fitz.open(sample_pdf) as doc:
        hits = aw.find_hits(doc[0], aw.PATTERNS, ["beispiel", "Anna"])
    for cat in ("AHV-Nr", "IBAN", "E-Mail", "Telefon", "UID", "ISIN", "Datum",
                "Strasse/Nr.", "PLZ/Ort", "Begriff"):
        assert hits.get(cat), cat
    assert all(isinstance(r, fitz.Rect) for r in hits["AHV-Nr"])


def test_find_hits_without_date_pattern(sample_pdf):
    patterns = {k: v for k, v in aw.PATTERNS.items() if k != "Datum"}
    with fitz.open(sample_pdf) as doc:
        assert "Datum" not in aw.find_hits(doc[0], patterns, [])


def test_find_hits_skips_year_and_stopword_plz(tmp_path):
    p = make_pdf(tmp_path / "a.pdf", ["2023 Steuerjahr", "1950 Total", "8001 Zürich"])
    with fitz.open(p) as doc:
        hits = aw.find_hits(doc[0], {}, [])
    texts = [doc_text for doc_text in hits.get("PLZ/Ort", [])]
    assert len(texts) == 1  # nur "8001 Zürich"


def test_find_hits_nothing_on_clean_page(tmp_path):
    p = make_pdf(tmp_path / "c.pdf", ["Total CHF 1000"])
    with fitz.open(p) as doc:
        assert aw.find_hits(doc[0], aw.PATTERNS, ["Muster"]) == {}


# --- scan_document: Muster ----------------------------------------------------------

def test_scan_detects_patterns(sample_pdf):
    F = scan(sample_pdf)
    cats = categories(F)
    for c in ("AHV-Nr", "IBAN", "UID", "E-Mail", "Telefon", "ISIN/Valor", "Strasse/Hausnr.",
              "PLZ/Ort", "Geburtsdatum", "Datum", "Name (nach Label)"):
        assert c in cats, c
    ahv = by_cat(F, "AHV-Nr")[0]
    assert ahv["sev"] == "HOCH" and ahv["note"] == "Prüfziffer gültig" and ahv["page"] == 1
    assert by_cat(F, "IBAN")[0]["sev"] == "HOCH"
    assert by_cat(F, "Geburtsdatum")[0]["value"] == "01.02.1980"
    assert by_cat(F, "Datum")[0]["sev"] == "INFO"


def test_scan_invalid_iban_is_medium(tmp_path):
    p = make_pdf(tmp_path / "i.pdf", [f"IBAN {IBAN_BAD}"])
    f = by_cat(scan(p), "IBAN")[0]
    assert f["sev"] == "MITTEL" and "ungültig" in f["note"]


def test_scan_card_number(tmp_path):
    p = make_pdf(tmp_path / "k.pdf", [f"Karte {CARD}"])
    assert "Kartennummer?" in categories(scan(p))


def test_scan_ahv_not_reported_as_card(tmp_path):
    p = make_pdf(tmp_path / "k.pdf", [f"AHV {AHV}"])
    assert "Kartennummer?" not in categories(scan(p))


def test_scan_keep_dates(sample_pdf):
    cats = categories(scan(sample_pdf, keep_dates=True))
    assert "Datum" not in cats
    assert "Geburtsdatum" in cats  # Geburtsdatum wird immer gemeldet


def test_scan_terms(sample_pdf):
    F = scan(sample_pdf, terms=["Beispiel"])
    assert by_cat(F, "Begriff (terms.txt)")
    assert not by_cat(scan(sample_pdf, terms=["gibtsnicht"]), "Begriff (terms.txt)")


def test_scan_label_stopword_not_name(tmp_path):
    p = make_pdf(tmp_path / "s.pdf", ["Name: Total Betrag"])
    assert "Name (nach Label)" not in categories(scan(p))


def test_scan_clean_document(tmp_path):
    p = make_pdf(tmp_path / "c.pdf", ["Total CHF 1000", "Abzüge 200"])
    F = scan(p)
    assert not [f for f in F if f["sev"] in ("HOCH", "MITTEL")]


# --- Versteckter Text ---------------------------------------------------------------

def test_hidden_text_variants(tmp_path):
    def setup(doc, page):
        page.insert_text((72, 300), "weiss", fontsize=11, color=(1, 1, 1))
        page.insert_text((72, 320), "winzig", fontsize=1)
        page.insert_text((72, 340), "unterbalken", fontsize=11, color=(0, 0, 0))
        page.draw_rect(fitz.Rect(60, 325, 200, 345), color=None, fill=(0, 0, 0))
    p = make_pdf(tmp_path / "h.pdf", ["sichtbar"], setup=setup)
    with fitz.open(p) as doc:
        found = {(cat, t) for _, cat, t in aw.hidden_text(doc[0])}
    cats = {c for c, _ in found}
    assert "Weisser/unsichtbarer Text" in cats
    assert "Mikroschrift (<2pt)" in cats
    assert "Text unter schwarzer Fläche" in cats
    assert not any(t == "sichtbar" for _, t in found)

    F = scan(p)
    under = by_cat(F, "Text unter schwarzer Fläche")
    assert under and under[0]["sev"] == "HOCH"


def test_hidden_text_ignores_thin_lines(tmp_path):
    def setup(doc, page):
        page.draw_line((50, 70), (300, 70), color=(0, 0, 0), width=1)
    p = make_pdf(tmp_path / "l.pdf", ["Text auf Linie"], setup=setup)
    with fitz.open(p) as doc:
        assert aw.hidden_text(doc[0]) == []


# --- Bilder / Scans / Barcodes -----------------------------------------------------------

def _png(w, h):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), False)
    pix.set_rect(pix.irect, (200, 50, 50))
    return pix.tobytes("png")


def test_small_image_detected_and_split(tmp_path):
    def setup(doc, page):
        page.insert_image(fitz.Rect(50, 50, 100, 100), stream=_png(20, 20))
    p = make_pdf(tmp_path / "img.pdf", setup=setup)
    with fitz.open(p) as doc:
        small, scans = aw.split_images(doc[0])
        assert len(small) == 1 and scans == []
        assert len(aw.image_boxes(doc[0])) == 1
    F = scan(p)
    assert by_cat(F, "Bild")[0]["sev"] == "MITTEL"
    assert by_cat(scan(p, keep_images=True), "Bild")[0]["sev"] == "INFO"


def test_large_image_is_scan(tmp_path):
    def setup(doc, page):
        page.insert_image(page.rect, stream=_png(40, 40))
    p = make_pdf(tmp_path / "scan.pdf", setup=setup)
    with fitz.open(p) as doc:
        small, scans = aw.split_images(doc[0])
        assert small == [] and len(scans) == 1
    cats = categories(scan(p))
    assert "Scan nicht prüfbar" in cats  # kein Tesseract im Test


def test_scan_with_ocr_stub(tmp_path, monkeypatch):
    def setup(doc, page):
        page.insert_image(page.rect, stream=_png(40, 40))
    p = make_pdf(tmp_path / "scan.pdf", setup=setup)
    monkeypatch.setattr(aw, "ocr_textpage", lambda page: page.get_textpage())
    assert "Scan (per OCR geprüft)" in categories(scan(p))


def test_vector_barcode_detected(tmp_path):
    p = make_pdf(tmp_path / "bc.pdf", ["Text"], setup=lambda d, pg: draw_barcode(pg))
    with fitz.open(p) as doc:
        boxes = aw.barcode_boxes(doc[0])
        dbg = {}
        aw.barcode_boxes(doc[0], debug=dbg)
    assert len(boxes) == 1
    assert boxes[0].width >= 150 and boxes[0].height >= 40
    assert set(dbg["found"]) == {"A", "B", "C", "D"}
    assert by_cat(scan(p), "Barcode")[0]["sev"] == "HOCH"


def test_barcode_pad_widens_box(tmp_path):
    p = make_pdf(tmp_path / "bc.pdf", setup=lambda d, pg: draw_barcode(pg))
    with fitz.open(p) as doc:
        small = aw.barcode_boxes(doc[0], pad=0)[0]
        big = aw.barcode_boxes(doc[0], pad=20)[0]
    assert big.get_area() > small.get_area()


def test_no_barcode_on_text_page(sample_pdf):
    with fitz.open(sample_pdf) as doc:
        assert aw.barcode_boxes(doc[0]) == []


def test_barcode_remnants(tmp_path):
    def setup(doc, page):
        for i in range(8):
            page.draw_rect(fitz.Rect(100 + i * 4, 400, 102 + i * 4, 420), color=None, fill=(0, 0, 0))
    p = make_pdf(tmp_path / "r.pdf", setup=setup)
    with fitz.open(p) as doc:
        assert len(aw.barcode_remnants(doc[0])) == 1


def test_barcode_font_span(tmp_path):
    def setup(doc, page):
        page.insert_text((72, 300), "ABC123", fontsize=20, fontname="helv")
    p = make_pdf(tmp_path / "f.pdf", setup=setup)
    with fitz.open(p) as doc:
        assert aw.barcode_boxes(doc[0]) == []  # Helvetica ist keine Barcode-Schrift


def test_scan_barcodes_without_reader_or_with(tmp_path):
    p = make_pdf(tmp_path / "s.pdf", ["Text"])
    with fitz.open(p) as doc:
        res = aw.scan_barcodes(doc[0], doc[0].rect)
    assert res is None or res == []


# --- Annotationen, Links, Metadaten, Anhänge -----------------------------------------------

def test_annotation_link_metadata_embedded(tmp_path):
    def setup(doc, page):
        page.add_text_annot((100, 100), "geheime Notiz")
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 70, 200, 90),
                          "uri": "https://example.com/secret"})
        doc.set_metadata({"author": "Max Muster", "title": "Steuer"})
        doc.embfile_add("daten.xml", b"<x>756.1234.5678.97</x>")
    p = make_pdf(tmp_path / "m.pdf", ["Link"], setup=setup)
    F = scan(p)
    cats = categories(F)
    assert any(c.startswith("Annotation") for c in cats)
    assert by_cat(F, "Link")[0]["value"] == "https://example.com/secret"
    assert "Metadaten: author" in cats and "Metadaten: title" in cats
    emb = by_cat(F, "Eingebettete Datei")[0]
    assert emb["sev"] == "HOCH" and emb["value"] == "daten.xml"


def test_incremental_save_detected(tmp_path):
    p = make_pdf(tmp_path / "v.pdf", ["alt"])
    with fitz.open(p) as doc:
        doc[0].insert_text((72, 200), "neu")
        doc.saveIncr()
    with fitz.open(p) as doc:
        if getattr(doc, "version_count", 1) > 1:
            assert "Inkrementelle Speicherung" in categories(aw.scan_document(doc, [], use_ner=False))


def test_raw_scan_finds_ahv_in_object(tmp_path):
    def setup(doc, page):
        xref = doc.get_new_xref()
        doc.update_object(xref, f"<< /Secret ({AHV}) >>")
    p = make_pdf(tmp_path / "raw.pdf", ["x"], setup=setup)
    F = scan(p, terms=["Secret"])
    assert "Rohdaten: AHV-Nr" in categories(F)
    assert "Rohdaten: Begriff" in categories(F)


def test_javascript_detected(tmp_path):
    def setup(doc, page):
        xref = doc.get_new_xref()
        doc.update_object(xref, "<< /S /JavaScript /JS (app.alert(1)) >>")
    p = make_pdf(tmp_path / "js.pdf", ["x"], setup=setup)
    assert "JavaScript" in categories(scan(p))


def test_form_field_with_value(tmp_path):
    def setup(doc, page):
        w = fitz.Widget()
        w.field_name = "vorname"
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(72, 200, 250, 220)
        w.field_value = "Max"
        page.add_widget(w)
    p = make_pdf(tmp_path / "w.pdf", setup=setup)
    F = scan(p)
    assert by_cat(F, "Formularfeld mit Wert")[0]["value"] == "vorname=Max"


# --- unreadable spans / NER -------------------------------------------------------------

def test_unreadable_spans_on_clean_page(sample_pdf):
    with fitz.open(sample_pdf) as doc:
        assert aw.unreadable_spans(doc[0]) == []


def test_load_ner_missing_returns_none(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "spacy":
            raise ImportError
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    assert aw._load_ner() is None


def test_scan_with_stub_ner(sample_pdf, monkeypatch):
    class Ent:
        def __init__(self, text, label):
            self.text, self.label_ = text, label

    class Doc:
        ents = [Ent("Anna Beispiel", "PER"), Ent("Zürich", "LOC"), Ent("Muster AG", "ORG"),
                Ent("Ab", "PER"), Ent("Total", "PER")]

    monkeypatch.setattr(aw, "_load_ner", lambda: (lambda text: Doc()))
    F = scan(sample_pdf, use_ner=True)
    assert by_cat(F, "Person (NER)")[0]["value"] == "Anna Beispiel"
    assert len(by_cat(F, "Person (NER)")) == 1   # zu kurz / Stopwort gefiltert
    assert by_cat(F, "Ort (NER)") and by_cat(F, "Organisation (NER)")


# --- Tesseract-Erkennung ------------------------------------------------------------------

def test_tesseract_info_missing(monkeypatch):
    monkeypatch.setattr(aw, "_TESS", "unset")
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert aw.tesseract_info() is None


def test_tesseract_info_parses_languages(monkeypatch):
    import subprocess
    monkeypatch.setattr(aw, "_TESS", "unset")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")

    class R:
        stdout = 'List of available languages in "/usr/share/tessdata/" (3):\neng\ndeu\nosd\n'
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert aw.tesseract_info() == ("/usr/share/tessdata/", "deu+eng")
    assert aw.tesseract_info() == ("/usr/share/tessdata/", "deu+eng")  # gecached


def test_tesseract_info_error(monkeypatch):
    import subprocess
    monkeypatch.setattr(aw, "_TESS", "unset")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")

    def boom(*a, **k):
        raise OSError
    monkeypatch.setattr(subprocess, "run", boom)
    assert aw.tesseract_info() is None


def test_ocr_textpage_without_tesseract(sample_pdf):
    with fitz.open(sample_pdf) as doc:
        assert aw.ocr_textpage(doc[0]) is None


# --- Bericht -------------------------------------------------------------------------------

def test_print_report(sample_pdf, capsys):
    with fitz.open(sample_pdf) as doc:
        F = aw.scan_document(doc, [], use_ner=False)
        aw.print_report(F, doc)
        masked = capsys.readouterr().out
        aw.print_report(F, doc, show_values=True)
        shown = capsys.readouterr().out
    assert "Zusammenfassung:" in masked and "--region 1:" in masked
    assert AHV not in masked and AHV in shown
    assert masked.index("[HOCH") < masked.index("[INFO")


def test_print_report_empty(capsys):
    aw.print_report([], None)
    assert "keine sensiblen Informationen" in capsys.readouterr().out


# --- automatische Namenserkennung ------------------------------------------------------------

def test_label_names_filters_stopwords(tmp_path):
    assert list(aw.label_names("Ehefrau: Anna Beispiel\n")) == [["Anna", "Beispiel"]]
    assert list(aw.label_names("Name: Total Betrag")) == []
    assert list(aw.label_names("Total CHF 1000")) == []


def test_detect_names_from_labels(tmp_path):
    p = make_pdf(tmp_path / "n.pdf", ["Ehemann: Hans Meier-Müller", "Kind: Ida Muster"])
    with fitz.open(p) as doc:
        names = aw.detect_names(doc, use_ner=False)
    assert {"Hans", "Meier", "Müller", "Ida", "Muster"} <= set(names)


def test_detect_names_with_stub_ner(sample_pdf, monkeypatch):
    class Ent:
        text, label_ = "Dr. Peter Gerber", "PER"

    class Doc:
        ents = [Ent()]

    monkeypatch.setattr(aw, "_load_ner", lambda: (lambda text: Doc()))
    with fitz.open(sample_pdf) as doc:
        names = aw.detect_names(doc, use_ner=True)
    assert {"Peter", "Gerber"} <= set(names)
    assert "Dr." not in names  # nicht alphabetisch


def test_find_name_hits_whole_words_only(tmp_path):
    p = make_pdf(tmp_path / "w.pdf", ["Anna wohnt hier", "Annahme ist offen", "von Meier-Müller, Anna."])
    with fitz.open(p) as doc:
        hits = aw.find_name_hits(doc[0], ["Anna", "Müller"])
        assert aw.find_name_hits(doc[0], []) == []
    assert len(hits) == 3          # "Anna", "Anna.", "Meier-Müller," – nicht "Annahme"
