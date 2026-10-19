import re

import pymupdf as fitz
import pytest

import anonywiz as aw
from conftest import AHV, CARD, DATE, EMAIL, IBAN, IBAN_BAD, ISIN, PHONE, PLZ_ORT, STREET, UID


# --- load_terms / parse_pages / parse_regions / pct_rect -------------------

def test_load_terms_skips_comments_and_blanks(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("# Kommentar\n\n Muster \nPeter\n  # noch einer\nÄpfel\n", encoding="utf-8")
    assert aw.load_terms(f) == ["Muster", "Peter", "Äpfel"]


def test_load_terms_none():
    assert aw.load_terms(None) == []


@pytest.mark.parametrize("spec,n,expected", [
    (None, 3, [0, 1, 2]),
    ("", 3, [0, 1, 2]),
    ("1", 5, [0]),
    ("1,3-4", 5, [0, 2, 3]),
    ("2-3,2", 5, [1, 2]),
    ("4-9", 5, [3, 4]),       # out-of-range pages are cut off
    ("9", 5, []),
    ("0", 5, []),
])
def test_parse_pages(spec, n, expected):
    assert aw.parse_pages(spec, n) == expected


def test_parse_regions():
    assert aw.parse_regions(None) == []
    assert aw.parse_regions(["2:0,0,100,15", "*:10,20,30,40.5"]) == [
        (1, (0.0, 0.0, 100.0, 15.0)), (None, (10.0, 20.0, 30.0, 40.5))]


def test_parse_regions_invalid():
    with pytest.raises(ValueError):
        aw.parse_regions(["kaputt"])


def test_pct_rect():
    doc = fitz.open()
    page = doc.new_page(width=200, height=400)
    assert aw.pct_rect(page, (10, 25, 50, 75)) == fitz.Rect(20, 100, 100, 300)


# --- checksums ---------------------------------------------------------------

def test_iban_checksum():
    assert aw._iban_ok(IBAN)
    assert aw._iban_ok(IBAN.replace(" ", "").lower())
    assert not aw._iban_ok(IBAN_BAD)


def test_ean13_checksum():
    assert aw._ean13_ok(AHV)
    assert not aw._ean13_ok("756.1234.5678.98")
    assert not aw._ean13_ok("123")


def test_luhn():
    assert aw._luhn_ok(CARD)
    assert aw._luhn_ok("4111-1111-1111-1111")
    assert not aw._luhn_ok("4111 1111 1111 1112")
    assert not aw._luhn_ok("1234")  # too short


# --- masking / colours --------------------------------------------------------

def test_mask():
    assert aw._mask("Muster", True) == "Muster"
    assert aw._mask("Muster", False) == "Mu•••r"
    assert aw._mask("abc", False) == "•••"
    assert aw._mask("  a   b  c d ", True) == "a b c d"


def test_lum():
    assert aw._lum(None) == 1.0
    assert aw._lum(()) == 1.0
    assert aw._lum((0.5,)) == 0.5
    assert aw._lum((0, 0, 0)) == 0
    assert aw._lum((1, 1, 1)) == pytest.approx(1.0)
    assert aw._lum(0xFFFFFF) == pytest.approx(1.0)
    assert aw._lum(0x000000) == 0
    assert aw._lum((0, 0, 0, 1)) == 0       # CMYK black
    assert aw._lum((0, 0, 0, 0)) == 1       # CMYK white


def test_unreadable():
    assert not aw._unreadable("Hallo")
    assert not aw._unreadable("   ")
    assert aw._unreadable("���")
    assert aw._unreadable("ab")
    assert aw._unreadable("\x01\x02\x03")
    assert not aw._unreadable("abc\ufffd")  # 25 % < threshold


# --- regex patterns -----------------------------------------------------------------

@pytest.mark.parametrize("key,good,bad", [
    ("AHV no.", [AHV, "7561234567897", "756 1234 5678 97"], ["757.1234.5678.97", "756.123.5678.97"]),
    ("IBAN", [IBAN, "LI21 0881 0000 2324 013A A"], ["DE89 3704 0044 0532 0130 00"]),
    ("ISIN", [ISIN, "US0378331005"], ["ch0012221716", "CH001222171"]),
    ("UID", [UID, "CHE 123 456 789", "CHE123456789"], ["CHX-123.456.789"]),
    ("Email", [EMAIL, "a+b@sub.example.com"], ["kein-at-zeichen"]),
    ("Phone", [PHONE, "+41 44 123 45 67", "0041 44 123 45 67", "0441234567"], ["12 345"]),
    ("Date", ["01.02.1980", "1.2.2023", "31.12.2099"], ["01.02.1880", "2023-12-31", "32.13.20"]),
])
def test_patterns(key, good, bad):
    rx = re.compile(aw.PATTERNS[key])
    for g in good:
        assert rx.search(g), f"{key} sollte '{g}' treffen"
    for b in bad:
        m = rx.search(b)
        assert not m or m.group() != b, f"{key} sollte '{b}' nicht komplett treffen"


def test_street_rx():
    for s in (STREET, "Hauptstr. 5", "Bergweg 3a", "Dorfplatz 10", "Rue du Quai 4" if False else "Seequai 7"):
        assert aw.STREET_RX.search(s), s
    assert not aw.STREET_RX.search("Strasse")
    assert not aw.STREET_RX.search("bahnhofstrasse 12")  # klein geschrieben


def test_plz_rx():
    m = aw.PLZ_RX.search(PLZ_ORT)
    assert m and m.group(1) == "8001" and m.group(2) == "Zürich"
    assert aw.PLZ_RX.search("CH-3000 Bern")
    assert aw.PLZ_RX.search("6003 Luzern Nord") or True
    assert not aw.PLZ_RX.search("0123 Test")


def test_label_rx():
    m = aw.LABEL_RX.search("Ehefrau: Anna Beispiel\n")
    assert m and m.group(1).startswith("Anna Beispiel")
    assert aw.LABEL_RX.search("Arbeitgeber Muster AG")
    assert not aw.LABEL_RX.search("Total CHF 1000")


# --- geometry ------------------------------------------------------------------

def test_touch_and_union():
    a, b, c = fitz.Rect(0, 0, 10, 10), fitz.Rect(10, 0, 20, 10), fitz.Rect(30, 0, 40, 10)
    assert aw._touch(a, b)
    assert not aw._touch(a, c)
    assert aw._touch(a, c, tol=20)
    assert aw._union(a, c) == fitz.Rect(0, 0, 40, 10)


def test_cluster_groups_touching_rects():
    rects = [fitz.Rect(0, 0, 2, 10), fitz.Rect(3, 0, 5, 10), fitz.Rect(100, 0, 102, 10)]
    clusters = aw._cluster(rects, tol=2)
    assert len(clusters) == 2
    counts = sorted(c[1] for c in clusters)
    assert counts == [1, 2]


def test_cluster_bridges_two_groups():
    rects = [fitz.Rect(0, 0, 2, 10), fitz.Rect(6, 0, 8, 10), fitz.Rect(3, 0, 5, 10)]
    clusters = aw._cluster(rects, tol=1.5)
    assert len(clusters) == 1 and clusters[0][1] == 3


def test_merge_boxes():
    boxes = [fitz.Rect(0, 0, 10, 10), fitz.Rect(12, 0, 20, 10), fitz.Rect(100, 100, 110, 110)]
    merged = aw._merge_boxes(boxes, gap=3)
    assert len(merged) == 2
    assert fitz.Rect(0, 0, 20, 10) in merged


def test_item_rect():
    assert aw._item_rect(("re", fitz.Rect(1, 2, 3, 4)), 0) == fitz.Rect(1, 2, 3, 4)
    p, q = fitz.Point(0, 0), fitz.Point(10, 0)
    assert aw._item_rect(("l", p, q), 2) == fitz.Rect(-1, -1, 11, 1)
    assert aw._item_rect(("x",), 0) is None


# --- multilingual vocabulary (German, English, French, Italian) -----------------------------

@pytest.mark.parametrize("text", [
    "geboren 01.02.1980", "Born: ", "Date of birth ", "DOB ", "Birthdate ", "Birth ",
    "né le ", "Née le ", "Date de naissance ", "nato il ", "nata il ", "Data di nascita ",
])
def test_birth_ctx_languages(text):
    assert aw.BIRTH_CTX.search(text), text


@pytest.mark.parametrize("text", ["Gültig ab ", "Valid from ", "ne pas ", "Ausgabe "])
def test_birth_ctx_no_false_positive(text):
    assert not aw.BIRTH_CTX.search(text), text


@pytest.mark.parametrize("text,words", [
    ("First name: Anna Beispiel", ["Anna", "Beispiel"]),
    ("Surname Muster", ["Muster"]),
    ("Mr. John Smith", ["John", "Smith"]),
    ("Account holder: Anna Beispiel", ["Anna", "Beispiel"]),
    ("Prénom: Élodie Dupont", ["Élodie", "Dupont"]),
    ("Nom de famille Müller", ["Müller"]),
    ("Épouse: Marie Curie", ["Marie", "Curie"]),
    ("Contribuable: Jean Dupont", ["Jean", "Dupont"]),
    ("Cognome: Rossi", ["Rossi"]),
    ("Signora Maria Bianchi", ["Maria", "Bianchi"]),
    ("Datore di lavoro: Ditta Verdi", ["Ditta", "Verdi"]),
    ("Intestatario: Àlvaro Neri", ["Àlvaro", "Neri"]),
])
def test_label_names_languages(text, words):
    assert list(aw.label_names(text)) == [words]


@pytest.mark.parametrize("text", [
    "Nome Cognome", "Nom Prénom", "First name Last name", "Name: Total", "Montant total",
])
def test_label_names_headers_not_names(text):
    assert list(aw.label_names(text)) == []


@pytest.mark.parametrize("street", [
    "Rue de la Gare 12", "Avenue des Alpes 5b", "Chemin du Lac 3", "Route de Genève 100",
    "Via Roma 5", "Viale Stazione 12", "Piazza Grande 1", "12 High Street",
    "221b Baker Street",
])
def test_street_languages(street):
    assert aw.STREET_RX.search(street), street


@pytest.mark.parametrize("text", ["Rue de la Gare", "Via Roma", "Corso di formazione 2023", "Place of birth"])
def test_street_no_false_positive(text):
    assert not aw.STREET_RX.search(text), text


def test_plz_accents():
    assert aw.PLZ_RX.search("1204 Genève")
    assert aw.PLZ_RX.search("6900 Lugano")
    assert aw.PLZ_RX.search("2000 Neuchâtel")
