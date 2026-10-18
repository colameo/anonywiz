#!/usr/bin/env python3
"""
anonywiz für Sensitive Dokument lokal zu anonymisieren.

Läuft komplett offline. Echte Schwärzung via PyMuPDF (Text/Bilder werden aus dem PDF
entfernt, nicht nur überdeckt).

Entfernt:
  - Muster: AHV-Nr. (756.xxxx.xxxx.xx), IBAN CH/LI, ISIN/Valoren, UID (CHE-…),
            E-Mail, CH-Telefonnummern, Datumsangaben (optional abschaltbar),
            Strasse + Hausnummer, PLZ + Ort
  - versteckter Text (weiss, Mikroschrift, unter schwarzen Balken)
  - eigene Begriffe aus terms.txt (Namen, Strasse, Ort, Arbeitgeber, Bank …)
  - alle Bilder (Barcodes/QR-Codes, Logos)
  - Metadaten, XMP, eingebettete Dateien (EasyTax bettet teils XML-Daten ein!)
  - Formularfelder/Annotationen werden vorher in den Seiteninhalt "gebacken",
    damit deren Inhalt mitgeschwärzt wird

Danach: automatische Gegenprüfung der Ausgabe + PNG-Vorschau jeder Seite.

Prüfmodus (--check) – verändert nichts, sucht nach:
  - AHV-Nr (mit Prüfziffer), IBAN (mit Prüfsumme), UID, E-Mail, Telefon, ISIN,
    Kartennummern (Luhn), Geburtsdaten (Kontext), Strasse+Nr., PLZ+Ort,
    Namen nach Labels ("Name:", "Ehefrau", "Arbeitgeber" …), Begriffe aus terms.txt
  - optional KI-Namenserkennung (spaCy, lokal)
  - Pseudo-Schwärzung (Text unter schwarzen Balken noch auslesbar), weisser Text,
    Mikroschrift, Text ausserhalb der Seite, nicht auslesbarer Text
  - Bilder, Barcodes, Annotationen, Formularfelder, Links
  - Metadaten, XMP, eingebettete Dateien, alte Versionen (inkrementell gespeichert),
    ausgeblendete Ebenen, JavaScript, Tiefenscan aller PDF-Objekte
  Exit-Code: 2 = HOCH, 1 = MITTEL, 0 = sauber

Installation:  pip install pymupdf
               für Scans:  brew install tesseract tesseract-lang   (OCR, lokal)
                           pip install zxing-cpp numpy              (Barcodes im Scan)
               optional: pip install spacy && python3 -m spacy download de_core_news_md

Scans: Bilder ab 25 % der Seitenfläche gelten als Scan. Sie werden NICHT entfernt,
sondern lokal per OCR gelesen; nur die gefundenen Stellen werden in den Bildpixeln
überschrieben. Ohne Tesseract bleibt der Scan ungeprüft (Warnung) – oder --remove-scans.
Verwendung:    python3 anonywizard.py steuererklaerung.pdf -t terms.txt
               python3 anonywizard.py in.pdf -t terms.txt --keep-dates --pages 3-8
               python3 anonywizard.py irgendein.pdf -t terms.txt --check
               python3 anonywizard.py in.pdf --check --json bericht.json

terms.txt: ein Begriff pro Zeile, # = Kommentar. Namensteile einzeln aufführen,
da Reihenfolge variiert ("Muster" und "Peter" statt nur "Peter Muster").
"""
__version__ = "2026-10-17.9"

import argparse
import re
import sys
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:
    try:
        import fitz
    except ImportError:
        sys.exit("PyMuPDF fehlt:  pip install pymupdf")

PATTERNS = {
    "AHV-Nr":   r"\b756[.\s]?\d{4}[.\s]?\d{4}[.\s]?\d{2}\b",
    "IBAN":     r"\b(?:CH|LI)\d{2}(?:\s?[0-9A-Z]){17}\b",
    "ISIN":     r"\b[A-Z]{2}[A-Z0-9]{9}\d\b",
    "UID":      r"\bCHE[-\s]?\d{3}[.\s]?\d{3}[.\s]?\d{3}\b",
    "E-Mail":   r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
    "Telefon":  r"(?:\+41|0041|\b0)\s?\d{2}\s?\d{3}\s?\d{2}\s?\d{2}\b",
    "Datum":    r"\b\d{1,2}\.\d{1,2}\.(?:19|20)\d{2}\b",
}


def load_terms(path):
    if not path:
        return []
    terms = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def parse_pages(spec, n):
    if not spec:
        return list(range(n))
    out = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a) - 1, int(b)))
        else:
            out.add(int(part) - 1)
    return sorted(p for p in out if 0 <= p < n)


def find_hits(page, patterns, terms, tp=None):
    """Liefert {Kategorie: [Rects]} für alle Treffer auf der Seite.
    tp = optionale OCR-Textebene (für eingescannte Seiten)."""
    hits = {}
    text = page.get_text("text", textpage=tp)

    def sf(t):
        return page.search_for(t, textpage=tp)

    for name, rx in patterns.items():
        for m in set(re.findall(rx, text)):
            rects = sf(m)
            if rects:
                hits.setdefault(name, []).extend(rects)
    for mo in STREET_RX.finditer(text):
        hits.setdefault("Strasse/Nr.", []).extend(sf(mo.group()))
    for mo in PLZ_RX.finditer(text):
        if not (1900 <= int(mo.group(1)) <= 2099 or mo.group(2).lower() in STOPWORDS):
            hits.setdefault("PLZ/Ort", []).extend(sf(mo.group()))
    for t in terms:
        rects = sf(t)  # case-insensitive
        if rects:
            hits.setdefault("Begriff", []).extend(rects)
    return hits


def _area(r):
    """Fläche eines Rects (Rect.get_area gibt es erst in neuen PyMuPDF-Versionen)."""
    return max(r.width, 0) * max(r.height, 0)


SCAN_MIN_AREA = 0.25  # Bild ab 25 % der Seitenfläche gilt als Scan
_TESS = "unset"


def split_images(page):
    """(kleine Bilder, Scans). Kleine Bilder (Logos, Barcodes) werden entfernt,
    Scans werden per OCR geprüft und nur stellenweise übermalt."""
    pa = _area(page.rect)
    small, scans = [], []
    for r in image_boxes(page):
        (scans if _area(r & page.rect) >= SCAN_MIN_AREA * pa else small).append(r)
    return small, scans


def tesseract_info():
    """(tessdata-Pfad, Sprache) oder None, falls Tesseract fehlt."""
    global _TESS
    if _TESS != "unset":
        return _TESS
    _TESS = None
    import shutil
    import subprocess
    exe = shutil.which("tesseract")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=20)
        txt = out.stdout + out.stderr
        m = re.search(r'"([^"]+)"', txt)
        langs = set(l.strip() for l in txt.splitlines()[1:] if l.strip())
        lang = "+".join(x for x in ("deu", "fra", "eng") if x in langs) or "eng"
        path = m.group(1) if m else None
        _TESS = (path, lang)
    except Exception:
        _TESS = None
    return _TESS


def ocr_textpage(page):
    """OCR-Textebene für die Bildbereiche der Seite (lokal via Tesseract) oder None."""
    info = tesseract_info()
    if not info:
        return None
    path, lang = info
    try:
        kw = dict(language=lang, dpi=300, full=False)
        if path:
            kw["tessdata"] = path
        return page.get_textpage_ocr(**kw)
    except Exception as e:
        print(f"  WARN: OCR fehlgeschlagen ({type(e).__name__}: {e})")
        return None


def scan_barcodes(page, area, dpi=200):
    """Barcodes/QR-Codes INNERHALB eines Scans finden (zxing-cpp oder pyzbar, lokal)."""
    pix = page.get_pixmap(dpi=dpi, clip=area, colorspace=fitz.csGRAY)
    sc = 72 / dpi
    boxes = []
    try:
        import zxingcpp
        import numpy as np
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        for res in zxingcpp.read_barcodes(img):
            pts = [res.position.top_left, res.position.top_right,
                   res.position.bottom_left, res.position.bottom_right]
            xs, ys = [q.x for q in pts], [q.y for q in pts]
            boxes.append(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
    except ImportError:
        try:
            from pyzbar import pyzbar
            from PIL import Image
            img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            for res in pyzbar.decode(img):
                l, t, w, h = res.rect
                boxes.append(fitz.Rect(l, t, l + w, t + h))
        except ImportError:
            return None  # kein Barcode-Leser installiert
    out = []
    for b in boxes:
        r = fitz.Rect(area.x0 + b.x0 * sc, area.y0 + b.y0 * sc,
                      area.x0 + b.x1 * sc, area.y0 + b.y1 * sc)
        m = 4 + 0.1 * max(r.width, r.height)
        out.append((r + (-m, -m - 6, m, m + 14)) & area)  # inkl. Klartext darunter
    return out


def image_boxes(page):
    """Alle Bildflächen: auch Inline-Bilder und Bilder in Form-XObjects."""
    boxes = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
    for img in page.get_images(full=True):
        boxes.extend(page.get_image_rects(img[0]))
    uniq = []
    for b in boxes:
        if not b.is_empty and not b.is_infinite and all(b != u for u in uniq):
            uniq.append(b)
    return uniq


BARCODE_FONTS = ("barcode", "code128", "code39", "code 39", "ean", "upc", "pdf417",
                 "datamatrix", "qrcode", "idautomation", "interleaved", "i2of5", "ocr-b")


def _touch(a, b, tol=0.0):
    return (a.x0 - tol <= b.x1 and b.x0 - tol <= a.x1 and
            a.y0 - tol <= b.y1 and b.y0 - tol <= a.y1)


def _union(a, b):
    return fitz.Rect(min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1))


def _cluster(rects, tol):
    """Rechtecke, die sich (mit Toleranz) berühren, zu Gruppen zusammenfassen."""
    clusters = []  # [bbox, count, fill_area]
    for r in sorted(rects, key=lambda r: (r.y0, r.x0)):
        hit = [c for c in clusters if _touch(c[0], r, tol)]
        if not hit:
            clusters.append([fitz.Rect(r), 1, r.width * r.height])
            continue
        base = hit[0]
        base[0] = _union(base[0], r)
        base[1] += 1
        base[2] += r.width * r.height
        for c in hit[1:]:
            base[0] = _union(base[0], c[0])
            base[1] += c[1]
            base[2] += c[2]
            clusters.remove(c)
    return clusters


def _item_rect(it, lw):
    k = it[0]
    if k == "re":
        return fitz.Rect(it[1])
    if k == "qu":
        return it[1].rect
    pts = [q for q in it[1:] if isinstance(q, fitz.Point)]
    if not pts:
        return None
    r = fitz.Rect(min(q.x for q in pts), min(q.y for q in pts),
                  max(q.x for q in pts), max(q.y for q in pts))
    return r + (-lw / 2, -lw / 2, lw / 2, lw / 2)


def _merge_boxes(boxes, gap):
    boxes = [fitz.Rect(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _touch(boxes[i], boxes[j], gap):
                    boxes[i] = _union(boxes[i], boxes[j])
                    del boxes[j]
                    changed = True
                    break
            if changed:
                break
    return boxes


BARCODE_PAD = 4  # Rand um Barcodes in pt, per --barcode-pad änderbar


def barcode_remnants(page):
    """Kleine Gruppen schmaler Balken/Module (z. B. übrig gebliebene Start-/Stopp-Muster)."""
    small = []
    for d in page.get_drawings():
        lw = d.get("width") or 0
        if d.get("fill") is None and not (d.get("color") is not None and lw > 0):
            continue
        for it in d["items"]:
            r = _item_rect(it, lw if d.get("fill") is None else 0)
            if r is not None and min(r.width, r.height) <= 4 and 3 <= max(r.width, r.height) <= 160:
                small.append(r)
    out = []
    for bbox, count, area in _cluster(small, tol=3):
        if count >= 5 and area / max(bbox.width * bbox.height, 1) >= 0.1 and bbox.width >= 5:
            out.append(bbox)
    return out


def _barcode_elements(page):
    """Alle gezeichneten Einzelelemente (Balken/Module) einer Seite – unabhängig davon,
    wie gross der umgebende Pfad ist und welche Farbe/Farbraum verwendet wird."""
    pw, ph = page.rect.width, page.rect.height
    elems, paths = [], []
    for d in page.get_drawings():
        lw = d.get("width") or 0
        fill, col = d.get("fill"), d.get("color")
        filled = fill is not None and _lum(fill) < 0.9      # weisse Hintergründe ignorieren
        stroked = col is not None and lw > 0 and _lum(col) < 0.9
        if not (filled or stroked):
            continue
        items = d["items"]
        if any(it[0] == "c" for it in items):
            continue  # Kurven = Logo/Wappen/Schrift als Grafik, nie ein Barcode
        if filled and items and all(it[0] == "l" for it in items):
            rects, cur = [], None          # Polygone in einzelne Balken zerlegen
            for it in items:
                r = _item_rect(it, 0)
                if cur is not None and _touch(cur, r, 0.3) and cur.width < 12:
                    cur = _union(cur, r)
                else:
                    if cur is not None:
                        rects.append(cur)
                    cur = fitz.Rect(r)
            if cur is not None:
                rects.append(cur)
        else:
            rects = [_item_rect(it, lw if (stroked and not filled) else 0) for it in items]
        rects = [r for r in rects if r is not None and not (r.width == 0 and r.height == 0)
                 and r.width < pw * 0.5 and r.height < ph * 0.5 and _touch(r, page.rect)]
        elems.extend(rects)
        paths.append((fitz.Rect(d["rect"]), rects))
    return elems, paths


def _cores(elems, paths, page):
    pw, ph = page.rect.width, page.rect.height
    found = {"A": [], "B": [], "C": [], "D": []}
    # A: ursprüngliche Erkennung (bewährt)
    small = [r for r in elems if min(r.width, r.height) <= 6 and max(r.width, r.height) <= 80]
    for bbox, n, area in _cluster(small, tol=2.5):
        if n >= 15 and bbox.width >= 15 and bbox.height >= 8 and area / max(_area(bbox), 1) >= 0.15:
            found["A"].append(bbox)
    # B: tolerantere Erkennung (hohe Balken, grössere Lücken)
    small = [r for r in elems if min(r.width, r.height) <= 8 and max(r.width, r.height) <= 160]
    for bbox, n, area in _cluster(small, tol=4):
        if n >= 12 and bbox.width >= 12 and bbox.height >= 8 and area / max(_area(bbox), 1) >= 0.12:
            found["B"].append(bbox)
    # C: ein Pfad mit sehr vielen kleinen Teilen = Barcode
    for pr, rects in paths:
        if len(rects) >= 30 and pr.width < pw * 0.6 and pr.height < ph * 0.5:
            sm = [r for r in rects if min(r.width, r.height) <= 8]
            if len(sm) >= 0.8 * len(rects):
                bb = sm[0]
                for r in sm[1:]:
                    bb = _union(bb, r)
                found["C"].append(bb)
    # D: 1D-Strichcode = Reihe paralleler Balken gleicher Höhe (auch breite Lücken)
    for vertical in (True, False):
        bars = []
        for r in elems:
            thin, long_ = (r.width, r.height) if vertical else (r.height, r.width)
            if thin <= 5 and long_ >= 6 and long_ >= 3 * max(thin, 0.1):
                bars.append(r)
        rows = {}
        for r in bars:  # nach gemeinsamer Ober-/Unterkante gruppieren
            a, b = (r.y0, r.y1) if vertical else (r.x0, r.x1)
            rows.setdefault((round(a / 2), round(b / 2)), []).append(r)
        for row in rows.values():
            row.sort(key=lambda r: r.x0 if vertical else r.y0)
            th = sorted((r.width if vertical else r.height) for r in row)
            med = th[len(th) // 2] if th else 1
            maxgap = max(10, 5 * med)
            run = [row[0]] if row else []
            for r in row[1:] + [None]:
                prev = run[-1] if run else None
                gap = None if r is None or prev is None else \
                    ((r.x0 - prev.x1) if vertical else (r.y0 - prev.y1))
                if r is not None and gap is not None and gap <= maxgap:
                    run.append(r)
                    continue
                if len(run) >= 10:
                    bb = run[0]
                    for q in run[1:]:
                        bb = _union(bb, q)
                    found["D"].append(bb)
                run = [r] if r is not None else []
    return found


def barcode_boxes(page, pad=None, debug=None):
    """Barcodes finden und KOMPLETT umschliessen (1D, PDF417 inkl. Start-/Stopp-Muster,
    QR/DataMatrix inkl. Finder-Quadrate, Barcode-Schriften, Klartextnummer)."""
    elems, paths = _barcode_elements(page)
    found = _cores(elems, paths, page)
    cores = _merge_boxes(found["A"] + found["B"] + found["C"] + found["D"], gap=10)

    # Kern wachsen lassen – aber begrenzt, damit keine Tabelle "mitgefressen" wird
    pool = [r for r in elems if max(r.width, r.height) <= 160]
    grown = []
    for c0 in cores:
        lim = max(15, 0.3 * max(c0.width, c0.height))
        limit = c0 + (-lim, -lim, lim, lim)
        c = fitz.Rect(c0)
        for _ in range(50):
            add = [r for r in pool if _touch(c, r, 8)
                   and not (c.x0 <= r.x0 and c.y0 <= r.y0 and r.x1 <= c.x1 and r.y1 <= c.y1)
                   and limit.x0 <= r.x0 and limit.y0 <= r.y0 and r.x1 <= limit.x1 and r.y1 <= limit.y1]
            if not add:
                break
            for r in add:
                c = _union(c, r)
        grown.append(c)

    boxes = list(grown)
    spans = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                spans.append(s)
                if any(k in s["font"].lower() for k in BARCODE_FONTS):
                    r = fitz.Rect(s["bbox"])
                    boxes.append(r + (-2, -r.height * 0.5, 2, r.height * 0.5))
    for c in grown:  # Klartextnummer direkt unter/über dem Barcode
        near = c + (-4, -14, 4, 14)
        for s in spans:
            r, t = fitz.Rect(s["bbox"]), s["text"].strip()
            if t and _touch(near, r) and (re.fullmatch(r"[A-Z0-9][A-Z0-9 .\-/*<>+]{3,}", t) or _unreadable(t)):
                boxes.append(r)

    pad = BARCODE_PAD if pad is None else pad
    out = []
    for bx in _merge_boxes(boxes, gap=6):
        m = pad + 0.03 * max(bx.width, bx.height)
        out.append((bx + (-m, -m, m, m)) & page.rect)
    if debug is not None:
        debug.update(found=found, final=out, elems=len(elems), paths=len(paths))
    return out


def debug_barcodes(doc, outdir):
    """Diagnose: zeichnet Kandidaten farbig ein (nicht geschwärzt) + Statistik ohne Inhalte."""
    outdir.mkdir(exist_ok=True)
    colors = {"A": (0, 0, 1), "B": (0, 0.6, 0), "C": (1, 0.5, 0), "D": (0, 0.7, 0.7)}
    for i, page in enumerate(doc, 1):
        info = {}
        final = barcode_boxes(page, debug=info)
        kinds = {}
        for d in page.get_drawings():
            for it in d["items"]:
                kinds[it[0]] = kinds.get(it[0], 0) + 1
        biggest = max((len(d["items"]) for d in page.get_drawings()), default=0)
        print(f"Seite {i}: {info['paths']} Pfade, {info['elems']} Elemente, Typen {kinds}, "
              f"grösster Pfad {biggest} Teile, Bilder {len(split_images(page)[0])}, "
              f"Scans {len(split_images(page)[1])} | "
              f"Kandidaten A={len(info['found']['A'])} B={len(info['found']['B'])} "
              f"C={len(info['found']['C'])} D={len(info['found']['D'])} -> final {len(final)}")
        for k, rs in info["found"].items():
            for r in rs:
                page.draw_rect(r, color=colors[k], width=1)
        for r in final:
            page.draw_rect(r, color=(1, 0, 0), width=2, dashes="[4] 0")
        for r in image_boxes(page):
            page.draw_rect(r, color=(0.6, 0, 0.6), width=1.5)
        page.get_pixmap(dpi=110).save(outdir / f"debug_seite_{i:02d}.png")
    print(f"\nDebug-Bilder: {outdir}/  (rot gestrichelt = wird geschwärzt, "
          "violett = Bild, blau/grün/orange = Erkennungsstufen A/B/C)")


def _unreadable(s):
    if not s.strip():
        return False
    bad = sum(1 for c in s if c == "�" or 0xE000 <= ord(c) <= 0xF8FF
              or (ord(c) < 32 and c not in "\t\n"))
    return bad / len(s) > 0.3


def unreadable_spans(page):
    """Text, der sichtbar ist, aber nicht als Text auslesbar (Font ohne Unicode-Mapping).
    Solcher Text wird von der Begriffssuche NICHT gefunden."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if _unreadable(s["text"]):
                    out.append(fitz.Rect(s["bbox"]))
    return out


def parse_regions(specs):
    """'2:0,0,100,15' -> Seite 2, Bereich in % (x0,y0,x1,y1). '*' = alle Seiten."""
    regions = []
    for spec in specs or []:
        pg, coords = spec.split(":")
        x0, y0, x1, y1 = (float(v) for v in coords.split(","))
        regions.append((None if pg.strip() == "*" else int(pg) - 1, (x0, y0, x1, y1)))
    return regions


def pct_rect(page, c):
    w, h = page.rect.width, page.rect.height
    return fitz.Rect(c[0] / 100 * w, c[1] / 100 * h, c[2] / 100 * w, c[3] / 100 * h)


def inspect(doc):
    """Diagnose ohne Inhalte auszugeben: nur Positionen in % der Seite."""
    def pct(page, r):
        w, h = page.rect.width, page.rect.height
        return f"{r.x0/w*100:.0f},{r.y0/h*100:.0f},{r.x1/w*100:.0f},{r.y1/h*100:.0f}"
    for i, page in enumerate(doc, 1):
        imgs = image_boxes(page)
        unr = unreadable_spans(page)
        print(f"Seite {i}:")
        for r in imgs:
            print(f"  Bild              --region {i}:{pct(page, r)}")
        for r in barcode_boxes(page):
            print(f"  Barcode (Vektor)  --region {i}:{pct(page, r)}")
        for r in unr:
            print(f"  Unlesbarer Text   --region {i}:{pct(page, r)}")
        if hasattr(page, "cluster_drawings"):
            for r in page.cluster_drawings(x_tolerance=12, y_tolerance=12):
                if r.width > 20 and r.height > 6 and r.width < page.rect.width * 0.9:
                    print(f"  Vektorgrafik      --region {i}:{pct(page, r)}")
        if not page.get_text().strip():
            print("  ACHTUNG: kein auslesbarer Text – Seite ist vermutlich ein Scan/Bild")


# ---------------------------------------------------------------------------
# Prüfmodus (--check): sensible Informationen erkennen
# ---------------------------------------------------------------------------
SEV_ORDER = {"HOCH": 0, "MITTEL": 1, "INFO": 2}

STOPWORDS = {
    "abzug", "abzüge", "total", "betrag", "chf", "einkommen", "vermögen", "kanton",
    "gemeinde", "steuer", "steuern", "steuerjahr", "angaben", "seite", "beilage",
    "formular", "ja", "nein", "und", "oder", "der", "die", "das", "von", "für",
    "aargau", "schweiz", "bund", "staat", "total", "übertrag", "lohnausweis",
    "wertschriften", "verzeichnis", "schulden", "zinsen", "kinder", "kind",
    "betreuung", "beruf", "berufsauslagen", "versicherung", "unterhalt",
}

LABEL_RX = re.compile(
    r"(?i:\b(?:name|vorname|nachname|ehemann|ehefrau|ehepartner(?:in)?|partner(?:in)?|"
    r"steuerpflichtige[r]?|kind|arbeitgeber(?:in)?|herr|frau|vertreter(?:in)?|"
    r"kontoinhaber(?:in)?|inhaber(?:in)?)\b)[ \t]*[:,]?[ \t]+"
    r"((?:[A-ZÄÖÜ][a-zäöüéèàç'-]+[ ,]*){1,3})")
STREET_RX = re.compile(
    r"\b[A-ZÄÖÜ][\wäöüéè.-]*(?:strasse|str\.|weg|gasse|platz|allee|rain|ring|halde|"
    r"matte?|acker|steig|graben|quai|stutz)\s+\d{1,4}[a-zA-Z]?\b")
PLZ_RX = re.compile(r"\b(?:CH-)?([1-9]\d{3})\s+([A-ZÄÖÜ][a-zäöüéèà]+(?:[ -][A-ZÄÖÜ]?[a-zäöüéèà]+)?)")
CARD_RX = re.compile(r"\b(?:\d[ -]?){13,19}\b")
BIRTH_CTX = re.compile(r"(?i)(geb\.|geboren|geburtsdatum|geb\.-?dat)")


def _iban_ok(s):
    s = re.sub(r"\s", "", s).upper()
    num = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(num) % 97 == 1


def _ean13_ok(s):
    d = [int(c) for c in re.sub(r"\D", "", s)]
    if len(d) != 13:
        return False
    return (10 - sum(x * (3 if i % 2 else 1) for i, x in enumerate(d[:12])) % 10) % 10 == d[12]


def _luhn_ok(s):
    d = [int(c) for c in re.sub(r"\D", "", s)][::-1]
    return len(d) >= 13 and sum(x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2)
                                for i, x in enumerate(d)) % 10 == 0


def _mask(v, show):
    v = " ".join(str(v).split())
    if show or len(v) <= 3:
        return v if show else "•" * len(v)
    return v[:2] + "•" * (len(v) - 3) + v[-1]


def _lum(c):
    if isinstance(c, int):
        c = ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255
    if not c:
        return 1.0
    if len(c) == 1:
        return c[0]
    if len(c) == 4:  # CMYK
        return 1 - min(1, c[3] + sum(c[:3]) / 3)
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def hidden_text(page):
    """Versteckter Text: weiss, Mikroschrift, ausserhalb der Seite, dunkler Text unter
    schwarzer Fläche (Pseudo-Schwärzung). Liefert [(Rect, Kategorie, Text)]."""
    # Dunkle Flächen aus den EINZELNEN Teilformen bestimmen – nicht aus dem Umriss
    # des ganzen Pfads (ein Tabellengitter in einem Pfad umfasst sonst die ganze Seite)
    dark = []
    for d in page.get_drawings():
        if d.get("fill") is None or _lum(d["fill"]) >= 0.25:
            continue
        items = d["items"]
        if items and all(it[0] == "l" for it in items):
            parts, cur = [], None
            for it in items:
                r = _item_rect(it, 0)
                if cur is not None and _touch(cur, r, 0.3):
                    cur = _union(cur, r)
                else:
                    if cur is not None:
                        parts.append(cur)
                    cur = fitz.Rect(r)
            if cur is not None:
                parts.append(cur)
        else:
            parts = [_item_rect(it, 0) for it in items if it[0] in ("re", "qu")]
        for r in parts:
            if r is not None and r.width >= 4 and r.height >= 4:  # dünne Linien sind keine Balken
                dark.append(r)
    for a in page.annots():
        if a.type[0] == fitz.PDF_ANNOT_REDACT:
            continue  # eigene, noch nicht angewendete Schwärzungen ignorieren
        if a.colors.get("fill") and _lum(a.colors["fill"]) < 0.25:
            dark.append(a.rect)
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                t, r = s["text"].strip(), fitz.Rect(s["bbox"])
                if not t:
                    continue
                if _lum(s["color"]) > 0.95:
                    out.append((r, "Weisser/unsichtbarer Text", t))
                if s["size"] < 2:
                    out.append((r, "Mikroschrift (<2pt)", t))
                if not page.rect.intersects(r):
                    out.append((r, "Text ausserhalb der Seite", t))
                if _lum(s["color"]) < 0.3 and any(
                        _area(r & d) > 0.5 * max(_area(r), 1) for d in dark):
                    out.append((r, "Text unter schwarzer Fläche", t))
    return out


def _load_ner():
    try:
        import spacy
    except ImportError:
        return None
    for m in ("de_core_news_lg", "de_core_news_md", "de_core_news_sm"):
        try:
            return spacy.load(m)
        except Exception:
            continue
    return None


def label_names(text):
    """Namen direkt nach einem Label ("Ehefrau: Anna Beispiel") als Wortlisten."""
    for mo in LABEL_RX.finditer(text):
        words = [w for w in re.split(r"[ ,]+", mo.group(1)) if w]
        words = [w for w in words if w.lower() not in STOPWORDS]
        if words:
            yield words


def detect_names(doc, use_ner=True, min_len=3):
    """Namensteile im ganzen Dokument finden (Label-Heuristik + optional spaCy).
    Jeder Teil wird danach überall als ganzes Wort geschwärzt, auch ohne Label."""
    nlp = _load_ner() if use_ner else None
    names = set()
    for page in doc:
        text = page.get_text("text")
        for words in label_names(text):
            names.update(words)
        if nlp is not None:
            for ent in nlp(text).ents:
                if ent.label_ == "PER":
                    names.update(w for w in re.split(r"[\s,]+", ent.text) if w)
    parts = {p for n in names for p in re.split(r"[-']", n)}   # Meier-Müller -> Meier, Müller
    return sorted(n for n in parts
                  if len(n) >= min_len and n.lower() not in STOPWORDS and n.isalpha())


def find_name_hits(page, names, tp=None):
    """Rects aller Vorkommen der Namen als GANZES Wort (ohne Gross-/Kleinschreibung)."""
    wanted = {n.lower() for n in names}
    if not wanted:
        return []
    out = []
    for w in page.get_text("words", textpage=tp):
        token = w[4].strip(".,;:()\"'").lower()
        if token in wanted or any(p in wanted for p in re.split(r"[-']", token)):
            out.append(fitz.Rect(w[:4]))
    return out


def scan_document(doc, terms, keep_dates=False, keep_images=False, use_ner=True):
    """Findet sensible Informationen. Liefert Liste von dicts
    {sev, cat, page, value, rect, note}."""
    F = []

    def f(sev, cat, page=None, value="", rect=None, note=""):
        F.append(dict(sev=sev, cat=cat, page=page, value=value, rect=rect, note=note))

    nlp = _load_ner() if use_ner else None
    content_xrefs = set()

    for pno, page in enumerate(doc, 1):
        content_xrefs.update(page.get_contents())
        small_imgs, scans = split_images(page)
        tp = ocr_textpage(page) if scans else None
        if scans and tp is None:
            for sr in scans:
                f("HOCH", "Scan nicht prüfbar", pno, "", sr,
                  "Tesseract fehlt: brew install tesseract tesseract-lang")
        text = page.get_text("text", textpage=tp)

        def locate(s):
            r = page.search_for(s, textpage=tp)
            return r[0] if r else None

        # --- Muster mit Validierung ---
        for m in set(re.findall(PATTERNS["AHV-Nr"], text)):
            f("HOCH", "AHV-Nr", pno, m, locate(m), "Prüfziffer gültig" if _ean13_ok(m) else "")
        for m in set(re.findall(PATTERNS["IBAN"], text)):
            f("HOCH" if _iban_ok(m) else "MITTEL", "IBAN", pno, m, locate(m),
              "Prüfsumme gültig" if _iban_ok(m) else "Prüfsumme ungültig")
        for key in ("UID", "E-Mail", "Telefon"):
            for m in set(re.findall(PATTERNS[key], text)):
                f("HOCH", key, pno, m, locate(m))
        for m in set(re.findall(PATTERNS["ISIN"], text)):
            f("MITTEL", "ISIN/Valor", pno, m, locate(m), "verrät Bank/Depot")
        for m in set(CARD_RX.findall(text)):
            if _luhn_ok(m) and not re.match(PATTERNS["AHV-Nr"], m):
                f("MITTEL", "Kartennummer?", pno, m, locate(m), "Luhn-Prüfung bestanden")

        # Datumsangaben, Geburtsdatum mit Kontext
        for mo in re.finditer(PATTERNS["Datum"], text):
            ctx = text[max(0, mo.start() - 30):mo.start()]
            if BIRTH_CTX.search(ctx):
                f("HOCH", "Geburtsdatum", pno, mo.group(), locate(mo.group()))
            elif not keep_dates:
                f("INFO", "Datum", pno, mo.group(), locate(mo.group()))

        # --- Personen/Adressen (Heuristik) ---
        for words in label_names(text):
            val = " ".join(words)
            f("MITTEL", "Name (nach Label)", pno, val, locate(words[0]))
        for mo in STREET_RX.finditer(text):
            f("HOCH", "Strasse/Hausnr.", pno, mo.group(), locate(mo.group()))
        for mo in PLZ_RX.finditer(text):
            plz = int(mo.group(1))
            if 1900 <= plz <= 2099 or mo.group(2).lower() in STOPWORDS:
                continue  # wahrscheinlich Jahreszahl
            f("MITTEL", "PLZ/Ort", pno, mo.group(), locate(mo.group()))

        # --- eigene Begriffe ---
        for t in terms:
            for r in page.search_for(t, textpage=tp):
                f("HOCH", "Begriff (terms.txt)", pno, t, r)

        # --- NER (optional, spaCy) ---
        if nlp is not None:
            for ent in nlp(text).ents:
                v = ent.text.strip()
                if len(v) < 3 or v.lower() in STOPWORDS:
                    continue
                if ent.label_ == "PER":
                    f("MITTEL", "Person (NER)", pno, v, locate(v))
                elif ent.label_ in ("LOC", "ORG"):
                    f("INFO", f"{'Ort' if ent.label_ == 'LOC' else 'Organisation'} (NER)",
                      pno, v, locate(v))

        # --- Versteckter / pseudo-geschwärzter Text ---
        for r, cat, t in hidden_text(page):
            if cat == "Text unter schwarzer Fläche":
                f("HOCH", cat, pno, t, r, "Pseudo-Schwärzung: Text ist noch auslesbar")
            else:
                f("MITTEL", cat, pno, t, r)
        for r in unreadable_spans(page):
            f("MITTEL", "Nicht auslesbarer Text", pno, "", r, "visuell prüfen")

        # --- Bilder, Barcodes ---
        for r in small_imgs:
            f("INFO" if keep_images else "MITTEL", "Bild", pno, "", r, "kann Namen/Logos enthalten")
        for sr in scans:
            if tp is not None:
                f("INFO", "Scan (per OCR geprüft)", pno, "", sr,
                  "OCR ist nicht perfekt – Handschrift/Stempel visuell prüfen")
            sb = scan_barcodes(page, sr)
            if sb is None:
                f("MITTEL", "Barcodes im Scan nicht prüfbar", pno, "", sr, "pip install zxing-cpp numpy")
            else:
                for r in sb:
                    f("HOCH", "Barcode im Scan", pno, "", r)
        bcs = barcode_boxes(page)
        for r in bcs:
            f("HOCH", "Barcode", pno, "", r, "enthält oft die kompletten Daten")
        for r in barcode_remnants(page):
            if not any(_touch(r, b) for b in bcs):
                f("MITTEL", "Möglicher Barcode-Rest", pno, "", r,
                  "schmale Balkengruppe – visuell prüfen, ggf. --region/--barcode-pad")

        # --- Annotationen, Formularfelder, Links ---
        for a in page.annots():
            info = a.info.get("content", "") or a.info.get("title", "")
            f("MITTEL", f"Annotation ({a.type[1]})", pno, info, a.rect)
        for w in page.widgets():
            if w.field_value not in (None, "", "Off", False):
                f("HOCH", "Formularfeld mit Wert", pno, f"{w.field_name}={w.field_value}", w.rect)
        for ln in page.get_links():
            if ln.get("uri"):
                f("INFO", "Link", pno, ln["uri"], ln.get("from"))

    # --- Dokumentebene ---
    for k, v in (doc.metadata or {}).items():
        if v and k not in ("format", "encryption"):
            f("MITTEL", f"Metadaten: {k}", None, v)
    if doc.get_xml_metadata().strip():
        f("MITTEL", "XMP-Metadaten", None, "", None, "können Autor/Software/Pfade enthalten")
    for name in doc.embfile_names():
        f("HOCH", "Eingebettete Datei", None, name, None, "z. B. XML mit allen Steuerdaten")
    if getattr(doc, "version_count", 1) > 1:
        f("HOCH", "Inkrementelle Speicherung", None, f"{doc.version_count} Versionen", None,
          "ältere Version mit Originaldaten evtl. noch in der Datei")
    try:
        ocgs = doc.get_ocgs() or {}
        if ocgs:
            hidden = [o["name"] for o in ocgs.values() if not o.get("on", True)]
            f("MITTEL" if hidden else "INFO", "PDF-Ebenen (OCG)", None,
              ", ".join(hidden) or f"{len(ocgs)} Ebene(n)", None,
              "ausgeblendete Ebenen gefunden" if hidden else "")
    except Exception:
        pass

    # --- Tiefenscan Rohdaten (alle Objekte ausser Seiteninhalt/Bilder/Fonts) ---
    raw_rx = [(k, re.compile(PATTERNS[k])) for k in ("AHV-Nr", "IBAN", "E-Mail")]
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=False)
        except Exception:
            continue
        if xref in content_xrefs or "/Image" in obj or "/FontFile" in obj or "/Form" in obj:
            continue
        if "/JavaScript" in obj or "/JS " in obj or "/JS(" in obj:
            f("MITTEL", "JavaScript", None, f"Objekt {xref}")
        data = obj
        if doc.xref_is_stream(xref):
            try:
                data += doc.xref_stream(xref).decode("latin-1", "ignore")
            except Exception:
                pass
        low = data.lower()
        for t in terms:
            if t.lower() in low:
                f("HOCH", "Rohdaten: Begriff", None, t, None, f"in PDF-Objekt {xref}")
        for k, rx in raw_rx:
            for m in set(rx.findall(data)):
                f("HOCH", f"Rohdaten: {k}", None, m, None, f"in PDF-Objekt {xref}")
    return F


def print_report(F, doc, show_values=False):
    if not F:
        print("Prüfung: keine sensiblen Informationen gefunden.")
        return
    seen, rows = set(), []
    for x in sorted(F, key=lambda x: (SEV_ORDER[x["sev"]], x["page"] or 0, x["cat"])):
        key = (x["sev"], x["cat"], x["page"], x["value"], str(x["rect"]))
        if key in seen:
            continue
        seen.add(key)
        reg = ""
        if x["rect"] is not None and x["page"]:
            pr = doc[x["page"] - 1].rect
            r = x["rect"]
            reg = (f"--region {x['page']}:{max(0, r.x0/pr.width*100-0.5):.1f},"
                   f"{max(0, r.y0/pr.height*100-0.5):.1f},{min(100, r.x1/pr.width*100+0.5):.1f},"
                   f"{min(100, r.y1/pr.height*100+0.5):.1f}")
        pg = f"S.{x['page']}" if x["page"] else "Dok"
        val = _mask(x["value"], show_values) if x["value"] else ""
        note = f"  ({x['note']})" if x["note"] else ""
        rows.append(f"  [{x['sev']:<6}] {pg:<5} {x['cat']:<28} {val:<26}{note}"
                    + (f"\n{'':<45}{reg}" if reg else ""))
    print("\n".join(rows))
    c = {s: sum(1 for x in F if x["sev"] == s) for s in SEV_ORDER}
    print(f"\nZusammenfassung: {c['HOCH']} HOCH, {c['MITTEL']} MITTEL, {c['INFO']} INFO")


def run_check(doc, terms, args, label):
    F = scan_document(doc, terms, keep_dates=args.keep_dates,
                      keep_images=args.keep_images, use_ner=not args.no_ner)
    print(f"\n=== Prüfung: {label} ===")
    if not args.no_ner and _load_ner() is None:
        print("  (Namenserkennung per KI inaktiv – optional: pip install spacy && "
              "python3 -m spacy download de_core_news_md)")
    print_report(F, doc, args.show_values)
    if args.json:
        import json
        out = [dict(x, rect=list(x["rect"]) if x["rect"] is not None else None) for x in F]
        if not args.show_values:
            for x in out:
                x["value"] = _mask(x["value"], False) if x["value"] else ""
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON-Bericht: {args.json}")
    return F


def main():
    ap = argparse.ArgumentParser(prog="anonywizard", description="Steuererklärungen, Lohn- und PK-Ausweise lokal anonymisieren")
    ap.add_argument("pdf")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("-t", "--terms", help="Datei mit eigenen Begriffen (Namen, Adresse …)")
    ap.add_argument("-o", "--out", help="Ausgabe-PDF (Default: <name>_anonym.pdf)")
    ap.add_argument("--pages", help="nur diese Seiten behalten, z. B. 1,3-6")
    ap.add_argument("--keep-dates", action="store_true", help="Datumsangaben nicht schwärzen")
    ap.add_argument("--remove-scans", action="store_true",
                    help="eingescannte Seiten komplett schwärzen statt per OCR prüfen")
    ap.add_argument("--keep-images", action="store_true", help="Bilder und Barcodes (auch Vektor-Barcodes) nicht entfernen")
    ap.add_argument("--no-preview", action="store_true", help="keine PNG-Vorschau erzeugen")
    ap.add_argument("--region", action="append", metavar="S:x0,y0,x1,y1",
                    help="Bereich manuell schwärzen, Koordinaten in %% der Seite, "
                         "S = Seitennr. (Original) oder * für alle. Mehrfach möglich.")
    ap.add_argument("--redact-unreadable", action="store_true",
                    help="nicht auslesbaren Text (Font ohne Unicode) ebenfalls schwärzen")
    ap.add_argument("--inspect", action="store_true",
                    help="nur analysieren: Bilder, Vektorgrafiken, unlesbaren Text auflisten")
    ap.add_argument("--barcode-pad", type=float, default=4, metavar="PT",
                    help="zusätzlicher Rand um Barcodes in pt (Default 4, z. B. 10 für mehr Sicherheit)")
    ap.add_argument("--debug-barcodes", action="store_true",
                    help="Barcode-Erkennung sichtbar machen (Debug-PNGs + Statistik, nichts wird geschwärzt)")
    ap.add_argument("--check", action="store_true",
                    help="nur prüfen: sensible Informationen im PDF suchen, nichts verändern")
    ap.add_argument("--show-values", action="store_true",
                    help="gefundene Werte im Bericht unmaskiert anzeigen")
    ap.add_argument("--json", metavar="DATEI", help="Prüfbericht zusätzlich als JSON speichern")
    ap.add_argument("--no-ner", action="store_true", help="KI-Namenserkennung (spaCy) nicht verwenden")
    ap.add_argument("--no-auto-names", action="store_true",
                    help="erkannte Namen (nach Label / spaCy) nicht automatisch im ganzen Dokument schwärzen")
    args = ap.parse_args()

    print(f"anonywizard {__version__}")
    global BARCODE_PAD
    BARCODE_PAD = args.barcode_pad
    src = Path(args.pdf)
    out = Path(args.out) if args.out else src.with_name(src.stem + "_anonym.pdf")
    terms = load_terms(args.terms)
    patterns = {k: v for k, v in PATTERNS.items() if not (k == "Datum" and args.keep_dates)}

    src = src.expanduser()
    if not src.is_file():
        pdfs = sorted(p.name for p in Path.cwd().glob("*.pdf"))
        sys.exit(f"Datei nicht gefunden: {src}\n"
                 f"Aktueller Ordner: {Path.cwd()}\n"
                 f"PDFs hier: {', '.join(pdfs) or 'keine'}\n"
                 "Tipp: Dateinamen mit Leerzeichen in Anführungszeichen setzen, "
                 "oder die Datei ins Terminal ziehen (Drag & Drop).")
    head = src.read_bytes()[:1024]
    if b"%PDF" not in head:
        sys.exit(f"'{src.name}' ist kein PDF (Dateianfang: {head[:8]!r}).\n"
                 "EasyTax-Dateien (.tax/.esteuer) sind kein PDF – in EasyTax "
                 "über 'Drucken/PDF erstellen' exportieren.")
    try:
        doc = fitz.open(src)
    except Exception as e:
        sys.exit(f"PDF konnte nicht geöffnet werden: {type(e).__name__}: {e}\n"
                 "Evtl. beschädigt – in Vorschau öffnen und 'Exportieren als PDF' versuchen.")
    if doc.needs_pass:
        sys.exit("PDF ist passwortgeschützt – bitte zuerst entsperren.")

    if args.check:
        F = run_check(doc, terms, args, src.name)
        sys.exit(2 if any(x["sev"] == "HOCH" for x in F)
                 else 1 if any(x["sev"] == "MITTEL" for x in F) else 0)

    # Formularfelder & Annotationen in den Seiteninhalt übernehmen
    if hasattr(doc, "bake"):
        doc.bake()
    else:
        print("WARN: PyMuPDF zu alt für doc.bake() – Formularfelder bleiben evtl. erhalten. "
              "Update: pip install -U pymupdf")

    if args.inspect:
        inspect(doc)
        return
    if args.debug_barcodes:
        debug_barcodes(doc, src.with_name(src.stem + "_debug"))
        return

    auto_names = [] if args.no_auto_names else detect_names(doc, use_ner=not args.no_ner)
    if auto_names:
        print(f"Automatisch erkannte Namen ({len(auto_names)}): "
              f"{', '.join(_mask(n, False) for n in auto_names)} – werden überall geschwärzt "
              "(abschalten: --no-auto-names)")

    # Seiten auswählen
    keep = parse_pages(args.pages, doc.page_count)
    if len(keep) != doc.page_count:
        doc.select(keep)
    regions = parse_regions(args.region)

    def add(page, r, key):
        page.add_redact_annot(r, fill=(0, 0, 0))
        stats[key] = stats.get(key, 0) + 1

    stats = {}
    for idx, page in enumerate(doc):
        orig = keep[idx]
        small_imgs, scans = split_images(page)
        tp = None
        if scans and not args.remove_scans:
            tp = ocr_textpage(page)
            if tp is None:
                print(f"  WARN Seite {orig+1}: enthält einen Scan, aber Tesseract (OCR) fehlt – "
                      "Scan bleibt UNGEPRÜFT stehen! Installieren: brew install tesseract tesseract-lang "
                      "(oder --remove-scans)")
            else:
                stats["Scan per OCR geprüft"] = stats.get("Scan per OCR geprüft", 0) + 1

        for name, rects in find_hits(page, patterns, terms, tp).items():
            for r in rects:
                add(page, r, name + (" (Scan)" if tp is not None else ""))

        if not args.keep_images:
            for r in small_imgs:
                add(page, r, "Bild/Barcode")
            for r in barcode_boxes(page):
                add(page, r, "Vektor-Barcode")
            for sr in scans:
                if args.remove_scans:
                    add(page, sr, "Scan entfernt")
                    continue
                bcs = scan_barcodes(page, sr)
                if bcs is None:
                    print(f"  WARN Seite {orig+1}: Barcodes im Scan nicht prüfbar – "
                          "pip install zxing-cpp numpy")
                else:
                    for r in bcs:
                        add(page, r, "Barcode im Scan")

        for r in find_name_hits(page, auto_names, tp):
            add(page, r, "Name (automatisch)")

        hid = hidden_text(page)
        n_spans = sum(1 for b in page.get_text("dict")["blocks"] for l in b.get("lines", [])
                      for sp in l["spans"] if sp["text"].strip())
        if hid and n_spans and len(hid) > 0.3 * n_spans:
            print(f"  WARN Seite {orig+1}: {len(hid)} von {n_spans} Textstellen gelten als "
                  "versteckt – wirkt wie Fehlerkennung, wird NICHT geschwärzt. "
                  "Bitte visuell prüfen (--check zeigt die Stellen).")
        else:
            for r, cat, _ in hid:
                add(page, r, cat)

        unr = unreadable_spans(page)
        if unr:
            if args.redact_unreadable:
                for r in unr:
                    add(page, r, "Unlesbarer Text")
            else:
                print(f"  WARN Seite {orig+1}: {len(unr)} Textstelle(n) nicht auslesbar – "
                      "werden von der Suche nicht gefunden. --inspect bzw. --redact-unreadable nutzen")

        for pg, c in regions:
            if pg is None or pg == orig:
                add(page, pct_rect(page, c), "Bereich manuell")

        red_area = sum(_area(a.rect) for a in page.annots()
                       if a.type[0] == fitz.PDF_ANNOT_REDACT)
        if red_area > 0.5 * _area(page.rect):
            print(f"  WARN Seite {orig+1}: über 50 % der Seite würden geschwärzt – "
                  "bitte Vorschau prüfen. Ursache mit --check bzw. --debug-barcodes eingrenzen.")

        # Scans: nur die betroffenen Pixel übermalen, nicht das ganze Bild entfernen
        keep_scan = scans and not args.remove_scans
        kw = {"images": fitz.PDF_REDACT_IMAGE_PIXELS if keep_scan else fitz.PDF_REDACT_IMAGE_REMOVE}
        if hasattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED"):
            kw["graphics"] = fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED
        page.apply_redactions(**kw)

    # Metadaten & Anhänge entfernen
    doc.set_metadata({})
    doc.del_xml_metadata()
    for name in list(doc.embfile_names()):
        doc.embfile_del(name)
        stats["Anhang"] = stats.get("Anhang", 0) + 1

    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()

    # Gegenprüfung der Ausgabe
    check = fitz.open(out)
    F = run_check(check, terms, args, out.name)
    leftovers = sum(1 for x in F if x["sev"] in ("HOCH", "MITTEL"))

    if not args.no_preview:
        prev_dir = out.with_name(out.stem + "_preview")
        prev_dir.mkdir(exist_ok=True)
        for i, page in enumerate(check, 1):
            page.get_pixmap(dpi=110).save(prev_dir / f"seite_{i:02d}.png")
    check.close()

    print(f"\nGespeichert: {out}")
    print("Geschwärzt:", ", ".join(f"{k} {v}x" for k, v in stats.items()) or "nichts")
    print("Gegenprüfung:", "OK – nichts Sensibles mehr gefunden" if leftovers == 0
          else f"{leftovers} Fund(e) HOCH/MITTEL – siehe Bericht oben, ggf. mit --region nachschwärzen")
    if not args.no_preview:
        print(f"Vorschau:    {prev_dir}/  -> bitte jede Seite kurz durchsehen")
    print("\nHinweis: Die automatische Erkennung ist heuristisch. Namen ohne Label, die "
          "nicht in terms.txt stehen, können durchrutschen – Vorschau immer visuell prüfen.")


if __name__ == "__main__":
    main()
