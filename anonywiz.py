#!/usr/bin/env python3
"""
anonywiz: anonymise sensitive documents locally.

Runs completely offline. Real redaction via PyMuPDF (text and images are removed from
the PDF, not just covered).

Removes:
  - patterns: AHV no. (756.xxxx.xxxx.xx), IBAN CH/LI, ISIN/Valor, UID (CHE-...),
              email, Swiss phone numbers, dates (can be switched off),
              street + house number, postcode + town
  - hidden text (white, micro text, under black bars)
  - your own terms from terms.txt (names, street, town, employer, bank ...)
  - all images (barcodes/QR codes, logos)
  - metadata, XMP, embedded files (EasyTax sometimes embeds XML data!)
  - form fields/annotations are "baked" into the page content first, so that their
    content is redacted as well

Afterwards: automatic re-check of the output + PNG preview of every page.

Check mode (--check) - changes nothing, looks for:
  - AHV no. (with check digit), IBAN (with checksum), UID, email, phone, ISIN,
    card numbers (Luhn), dates of birth (context), street + no., postcode + town,
    names after labels ("Name:", "Ehefrau", "Arbeitgeber" ...), terms from terms.txt
  - optional AI name recognition (spaCy, local)
  - fake redaction (text under black bars still readable), white text,
    micro text, text outside the page, unreadable text
  - images, barcodes, annotations, form fields, links
  - metadata, XMP, embedded files, old versions (incremental saves),
    hidden layers, JavaScript, deep scan of all PDF objects
  Exit code: 2 = HIGH, 1 = MEDIUM, 0 = clean

Installation:  pipx install git+https://github.com/colameo/anonywiz.git
               for scans:  brew install tesseract tesseract-lang   (OCR, local)
               optional:   pip install spacy && python3 -m spacy download de_core_news_md

Scans: images covering 25 % or more of the page count as a scan. They are NOT removed
but read locally via OCR; only the matches are painted over in the image pixels.
Without Tesseract the scan stays unchecked (warning) - or use --remove-scans.

Usage:         anonywiz tax_return.pdf -t terms.txt
               anonywiz in.pdf -t terms.txt --keep-dates --pages 3-8
               anonywiz any.pdf -t terms.txt --check
               anonywiz in.pdf --check --json report.json

terms.txt: one term per line, # = comment. List name parts separately, because
their order varies ("Muster" and "Peter" instead of just "Peter Muster").
"""
__version__ = "0.1.0"

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
        sys.exit("PyMuPDF is missing:  pip install pymupdf")

PATTERNS = {
    "AHV no.":   r"\b756[.\s]?\d{4}[.\s]?\d{4}[.\s]?\d{2}\b",
    "IBAN":     r"\b(?:CH|LI)\d{2}(?:\s?[0-9A-Z]){17}\b",
    "ISIN":     r"\b[A-Z]{2}[A-Z0-9]{9}\d\b",
    "UID":      r"\bCHE[-\s]?\d{3}[.\s]?\d{3}[.\s]?\d{3}\b",
    "Email":   r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
    "Phone":  r"(?:\+41|0041|\b0)\s?\d{2}\s?\d{3}\s?\d{2}\s?\d{2}\b",
    "Date":    r"\b\d{1,2}\.\d{1,2}\.(?:19|20)\d{2}\b",
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
    """Returns {category: [Rects]} for all matches on the page.
    tp = optional OCR text layer (for scanned pages)."""
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
        hits.setdefault("Street/No.", []).extend(sf(mo.group()))
    for mo in PLZ_RX.finditer(text):
        if not (1900 <= int(mo.group(1)) <= 2099 or mo.group(2).lower() in STOPWORDS):
            hits.setdefault("Postcode/Town", []).extend(sf(mo.group()))
    for t in terms:
        rects = sf(t)  # case-insensitive
        if rects:
            hits.setdefault("Term", []).extend(rects)
    return hits


def _area(r):
    """Area of a Rect (Rect.get_area only exists in recent PyMuPDF versions)."""
    return max(r.width, 0) * max(r.height, 0)


SCAN_MIN_AREA = 0.25  # an image covering 25 % of the page counts as a scan
_TESS = "unset"


def split_images(page):
    """(small images, scans). Small images (logos, barcodes) are removed,
    scans are checked via OCR and only painted over where something was found."""
    pa = _area(page.rect)
    small, scans = [], []
    for r in image_boxes(page):
        (scans if _area(r & page.rect) >= SCAN_MIN_AREA * pa else small).append(r)
    return small, scans


def tesseract_info():
    """(tessdata path, language) or None if Tesseract is missing."""
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
    """OCR text layer for the image areas of the page (local, via Tesseract) or None."""
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
        print(f"  WARN: OCR failed ({type(e).__name__}: {e})")
        return None


def scan_barcodes(page, area, dpi=200):
    """Find barcodes/QR codes INSIDE a scan (zxing-cpp or pyzbar, local)."""
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
            return None  # no barcode reader installed
    out = []
    for b in boxes:
        r = fitz.Rect(area.x0 + b.x0 * sc, area.y0 + b.y0 * sc,
                      area.x0 + b.x1 * sc, area.y0 + b.y1 * sc)
        m = 4 + 0.1 * max(r.width, r.height)
        out.append((r + (-m, -m - 6, m, m + 14)) & area)  # incl. plain text below
    return out


def image_boxes(page):
    """All image areas: also inline images and images in form XObjects."""
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
    """Group rectangles that touch each other (within a tolerance)."""
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


BARCODE_PAD = 4  # margin around barcodes in pt, changeable via --barcode-pad


def barcode_remnants(page):
    """Small groups of narrow bars/modules (e.g. leftover start/stop patterns)."""
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
    """All individually drawn elements (bars/modules) of a page - regardless of how
    large the surrounding path is and which colour/colour space is used."""
    pw, ph = page.rect.width, page.rect.height
    elems, paths = [], []
    for d in page.get_drawings():
        lw = d.get("width") or 0
        fill, col = d.get("fill"), d.get("color")
        filled = fill is not None and _lum(fill) < 0.9      # ignore white backgrounds
        stroked = col is not None and lw > 0 and _lum(col) < 0.9
        if not (filled or stroked):
            continue
        items = d["items"]
        if any(it[0] == "c" for it in items):
            continue  # curves = logo/coat of arms/text as graphics, never a barcode
        if filled and items and all(it[0] == "l" for it in items):
            rects, cur = [], None          # split polygons into individual bars
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
    # A: original detection (proven)
    small = [r for r in elems if min(r.width, r.height) <= 6 and max(r.width, r.height) <= 80]
    for bbox, n, area in _cluster(small, tol=2.5):
        if n >= 15 and bbox.width >= 15 and bbox.height >= 8 and area / max(_area(bbox), 1) >= 0.15:
            found["A"].append(bbox)
    # B: more tolerant detection (tall bars, larger gaps)
    small = [r for r in elems if min(r.width, r.height) <= 8 and max(r.width, r.height) <= 160]
    for bbox, n, area in _cluster(small, tol=4):
        if n >= 12 and bbox.width >= 12 and bbox.height >= 8 and area / max(_area(bbox), 1) >= 0.12:
            found["B"].append(bbox)
    # C: one path with very many small parts = barcode
    for pr, rects in paths:
        if len(rects) >= 30 and pr.width < pw * 0.6 and pr.height < ph * 0.5:
            sm = [r for r in rects if min(r.width, r.height) <= 8]
            if len(sm) >= 0.8 * len(rects):
                bb = sm[0]
                for r in sm[1:]:
                    bb = _union(bb, r)
                found["C"].append(bb)
    # D: 1D barcode = row of parallel bars of equal height (also wide gaps)
    for vertical in (True, False):
        bars = []
        for r in elems:
            thin, long_ = (r.width, r.height) if vertical else (r.height, r.width)
            if thin <= 5 and long_ >= 6 and long_ >= 3 * max(thin, 0.1):
                bars.append(r)
        rows = {}
        for r in bars:  # group by shared top/bottom edge
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
    """Find barcodes and enclose them COMPLETELY (1D, PDF417 incl. start/stop patterns,
    QR/DataMatrix incl. finder squares, barcode fonts, plain-text number)."""
    elems, paths = _barcode_elements(page)
    found = _cores(elems, paths, page)
    cores = _merge_boxes(found["A"] + found["B"] + found["C"] + found["D"], gap=10)

    # grow the core - but limited, so that no table gets "swallowed"
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
    for c in grown:  # plain-text number directly below/above the barcode
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
    """Diagnosis: draws candidates in colour (not redacted) + statistics without contents."""
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
        print(f"Page {i}: {info['paths']} paths, {info['elems']} elements, types {kinds}, "
              f"largest path {biggest} parts, images {len(split_images(page)[0])}, "
              f"scans {len(split_images(page)[1])} | "
              f"candidates A={len(info['found']['A'])} B={len(info['found']['B'])} "
              f"C={len(info['found']['C'])} D={len(info['found']['D'])} -> final {len(final)}")
        for k, rs in info["found"].items():
            for r in rs:
                page.draw_rect(r, color=colors[k], width=1)
        for r in final:
            page.draw_rect(r, color=(1, 0, 0), width=2, dashes="[4] 0")
        for r in image_boxes(page):
            page.draw_rect(r, color=(0.6, 0, 0.6), width=1.5)
        page.get_pixmap(dpi=110).save(outdir / f"debug_seite_{i:02d}.png")
    print(f"\nDebug images: {outdir}/  (red dashed = will be redacted, "
          "purple = image, blue/green/orange = detection stages A/B/C)")


def _unreadable(s):
    if not s.strip():
        return False
    bad = sum(1 for c in s if c == "�" or 0xE000 <= ord(c) <= 0xF8FF
              or (ord(c) < 32 and c not in "\t\n"))
    return bad / len(s) > 0.3


def unreadable_spans(page):
    """Text that is visible but cannot be read as text (font without Unicode mapping).
    Such text is NOT found by the term search."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if _unreadable(s["text"]):
                    out.append(fitz.Rect(s["bbox"]))
    return out


def parse_regions(specs):
    """'2:0,0,100,15' -> page 2, area in % (x0,y0,x1,y1). '*' = all pages."""
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
    """Diagnosis without printing contents: only positions in % of the page."""
    def pct(page, r):
        w, h = page.rect.width, page.rect.height
        return f"{r.x0/w*100:.0f},{r.y0/h*100:.0f},{r.x1/w*100:.0f},{r.y1/h*100:.0f}"
    for i, page in enumerate(doc, 1):
        imgs = image_boxes(page)
        unr = unreadable_spans(page)
        print(f"Page {i}:")
        for r in imgs:
            print(f"  Image             --region {i}:{pct(page, r)}")
        for r in barcode_boxes(page):
            print(f"  Barcode (vector)  --region {i}:{pct(page, r)}")
        for r in unr:
            print(f"  Unreadable text   --region {i}:{pct(page, r)}")
        if hasattr(page, "cluster_drawings"):
            for r in page.cluster_drawings(x_tolerance=12, y_tolerance=12):
                if r.width > 20 and r.height > 6 and r.width < page.rect.width * 0.9:
                    print(f"  Vector graphic    --region {i}:{pct(page, r)}")
        if not page.get_text().strip():
            print("  WARNING: no readable text - page is probably a scan/image")


# ---------------------------------------------------------------------------
# Check mode (--check): detect sensitive information
# ---------------------------------------------------------------------------
SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}

# German words that look like names/places but are not (the tool targets German-language
# documents, so the detection vocabulary below is intentionally German)
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
    """Hidden text: white, micro text, outside the page, dark text under a black
    area (fake redaction). Returns [(Rect, category, text)]."""
    # Determine dark areas from the INDIVIDUAL shapes - not from the outline of the
    # whole path (a table grid in one path would otherwise span the whole page)
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
            if r is not None and r.width >= 4 and r.height >= 4:  # thin lines are not bars
                dark.append(r)
    for a in page.annots():
        if a.type[0] == fitz.PDF_ANNOT_REDACT:
            continue  # ignore our own redactions that have not been applied yet
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
                    out.append((r, "White/invisible text", t))
                if s["size"] < 2:
                    out.append((r, "Micro text (<2pt)", t))
                if not page.rect.intersects(r):
                    out.append((r, "Text outside the page", t))
                if _lum(s["color"]) < 0.3 and any(
                        _area(r & d) > 0.5 * max(_area(r), 1) for d in dark):
                    out.append((r, "Text under black area", t))
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
    """Names directly after a label ("Ehefrau: Anna Beispiel") as word lists."""
    for mo in LABEL_RX.finditer(text):
        words = [w for w in re.split(r"[ ,]+", mo.group(1)) if w]
        words = [w for w in words if w.lower() not in STOPWORDS]
        if words:
            yield words


def detect_names(doc, use_ner=True, min_len=3):
    """Find name parts in the whole document (label heuristic + optional spaCy).
    Each part is then redacted everywhere as a whole word, even without a label."""
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
    """Rects of all occurrences of the names as a WHOLE word (case-insensitive)."""
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
    """Finds sensitive information. Returns a list of dicts
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
                f("HIGH", "Scan not checkable", pno, "", sr,
                  "Tesseract missing: brew install tesseract tesseract-lang")
        text = page.get_text("text", textpage=tp)

        def locate(s):
            r = page.search_for(s, textpage=tp)
            return r[0] if r else None

        # --- patterns with validation ---
        for m in set(re.findall(PATTERNS["AHV no."], text)):
            f("HIGH", "AHV no.", pno, m, locate(m), "check digit valid" if _ean13_ok(m) else "")
        for m in set(re.findall(PATTERNS["IBAN"], text)):
            f("HIGH" if _iban_ok(m) else "MEDIUM", "IBAN", pno, m, locate(m),
              "checksum valid" if _iban_ok(m) else "checksum invalid")
        for key in ("UID", "Email", "Phone"):
            for m in set(re.findall(PATTERNS[key], text)):
                f("HIGH", key, pno, m, locate(m))
        for m in set(re.findall(PATTERNS["ISIN"], text)):
            f("MEDIUM", "ISIN/Valor", pno, m, locate(m), "reveals bank/custody account")
        for m in set(CARD_RX.findall(text)):
            if _luhn_ok(m) and not re.match(PATTERNS["AHV no."], m):
                f("MEDIUM", "Card number?", pno, m, locate(m), "Luhn check passed")

        # dates; date of birth via context
        for mo in re.finditer(PATTERNS["Date"], text):
            ctx = text[max(0, mo.start() - 30):mo.start()]
            if BIRTH_CTX.search(ctx):
                f("HIGH", "Date of birth", pno, mo.group(), locate(mo.group()))
            elif not keep_dates:
                f("INFO", "Date", pno, mo.group(), locate(mo.group()))

        # --- people/addresses (heuristic) ---
        for words in label_names(text):
            val = " ".join(words)
            f("MEDIUM", "Name (after label)", pno, val, locate(words[0]))
        for mo in STREET_RX.finditer(text):
            f("HIGH", "Street/house no.", pno, mo.group(), locate(mo.group()))
        for mo in PLZ_RX.finditer(text):
            plz = int(mo.group(1))
            if 1900 <= plz <= 2099 or mo.group(2).lower() in STOPWORDS:
                continue  # probably a year
            f("MEDIUM", "Postcode/Town", pno, mo.group(), locate(mo.group()))

        # --- own terms ---
        for t in terms:
            for r in page.search_for(t, textpage=tp):
                f("HIGH", "Term (terms.txt)", pno, t, r)

        # --- NER (optional, spaCy) ---
        if nlp is not None:
            for ent in nlp(text).ents:
                v = ent.text.strip()
                if len(v) < 3 or v.lower() in STOPWORDS:
                    continue
                if ent.label_ == "PER":
                    f("MEDIUM", "Person (NER)", pno, v, locate(v))
                elif ent.label_ in ("LOC", "ORG"):
                    f("INFO", f"{'Place' if ent.label_ == 'LOC' else 'Organisation'} (NER)",
                      pno, v, locate(v))

        # --- hidden / fake-redacted text ---
        for r, cat, t in hidden_text(page):
            if cat == "Text under black area":
                f("HIGH", cat, pno, t, r, "Fake redaction: text is still readable")
            else:
                f("MEDIUM", cat, pno, t, r)
        for r in unreadable_spans(page):
            f("MEDIUM", "Unreadable text", pno, "", r, "check visually")

        # --- images, barcodes ---
        for r in small_imgs:
            f("INFO" if keep_images else "MEDIUM", "Image", pno, "", r, "may contain names/logos")
        for sr in scans:
            if tp is not None:
                f("INFO", "Scan (checked via OCR)", pno, "", sr,
                  "OCR is not perfect - check handwriting/stamps visually")
            sb = scan_barcodes(page, sr)
            if sb is None:
                f("MEDIUM", "Barcodes in scan not checkable", pno, "", sr, "pip install zxing-cpp numpy")
            else:
                for r in sb:
                    f("HIGH", "Barcode in scan", pno, "", r)
        bcs = barcode_boxes(page)
        for r in bcs:
            f("HIGH", "Barcode", pno, "", r, "often contains the complete data")
        for r in barcode_remnants(page):
            if not any(_touch(r, b) for b in bcs):
                f("MEDIUM", "Possible barcode remnant", pno, "", r,
                  "narrow group of bars - check visually, maybe use --region/--barcode-pad")

        # --- annotations, form fields, links ---
        for a in page.annots():
            info = a.info.get("content", "") or a.info.get("title", "")
            f("MEDIUM", f"Annotation ({a.type[1]})", pno, info, a.rect)
        for w in page.widgets():
            if w.field_value not in (None, "", "Off", False):
                f("HIGH", "Form field with value", pno, f"{w.field_name}={w.field_value}", w.rect)
        for ln in page.get_links():
            if ln.get("uri"):
                f("INFO", "Link", pno, ln["uri"], ln.get("from"))

    # --- document level ---
    for k, v in (doc.metadata or {}).items():
        if v and k not in ("format", "encryption"):
            f("MEDIUM", f"Metadata: {k}", None, v)
    if doc.get_xml_metadata().strip():
        f("MEDIUM", "XMP metadata", None, "", None, "may contain author/software/paths")
    for name in doc.embfile_names():
        f("HIGH", "Embedded file", None, name, None, "e.g. XML with all tax data")
    if getattr(doc, "version_count", 1) > 1:
        f("HIGH", "Incremental save", None, f"{doc.version_count} versions", None,
          "an older version with the original data may still be in the file")
    try:
        ocgs = doc.get_ocgs() or {}
        if ocgs:
            hidden = [o["name"] for o in ocgs.values() if not o.get("on", True)]
            f("MEDIUM" if hidden else "INFO", "PDF layers (OCG)", None,
              ", ".join(hidden) or f"{len(ocgs)} layer(s)", None,
              "hidden layers found" if hidden else "")
    except Exception:
        pass

    # --- deep scan of raw data (all objects except page content/images/fonts) ---
    raw_rx = [(k, re.compile(PATTERNS[k])) for k in ("AHV no.", "IBAN", "Email")]
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=False)
        except Exception:
            continue
        if xref in content_xrefs or "/Image" in obj or "/FontFile" in obj or "/Form" in obj:
            continue
        if "/JavaScript" in obj or "/JS " in obj or "/JS(" in obj:
            f("MEDIUM", "JavaScript", None, f"Object {xref}")
        data = obj
        if doc.xref_is_stream(xref):
            try:
                data += doc.xref_stream(xref).decode("latin-1", "ignore")
            except Exception:
                pass
        low = data.lower()
        for t in terms:
            if t.lower() in low:
                f("HIGH", "Raw data: term", None, t, None, f"in PDF object {xref}")
        for k, rx in raw_rx:
            for m in set(rx.findall(data)):
                f("HIGH", f"Raw data: {k}", None, m, None, f"in PDF object {xref}")
    return F


def print_report(F, doc, show_values=False):
    if not F:
        print("Check: no sensitive information found.")
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
        pg = f"p.{x['page']}" if x["page"] else "Doc"
        val = _mask(x["value"], show_values) if x["value"] else ""
        note = f"  ({x['note']})" if x["note"] else ""
        rows.append(f"  [{x['sev']:<6}] {pg:<5} {x['cat']:<28} {val:<26}{note}"
                    + (f"\n{'':<45}{reg}" if reg else ""))
    print("\n".join(rows))
    c = {s: sum(1 for x in F if x["sev"] == s) for s in SEV_ORDER}
    print(f"\nSummary: {c['HIGH']} HIGH, {c['MEDIUM']} MEDIUM, {c['INFO']} INFO")


def run_check(doc, terms, args, label):
    F = scan_document(doc, terms, keep_dates=args.keep_dates,
                      keep_images=args.keep_images, use_ner=not args.no_ner)
    print(f"\n=== Check: {label} ===")
    if not args.no_ner and _load_ner() is None:
        print("  (AI name recognition inactive - optional: pip install spacy && "
              "python3 -m spacy download de_core_news_md)")
    print_report(F, doc, args.show_values)
    if args.json:
        import json
        out = [dict(x, rect=list(x["rect"]) if x["rect"] is not None else None) for x in F]
        if not args.show_values:
            for x in out:
                x["value"] = _mask(x["value"], False) if x["value"] else ""
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON report: {args.json}")
    return F


def main():
    ap = argparse.ArgumentParser(prog="anonywiz", description="Anonymise tax returns, salary and pension fund statements locally")
    ap.add_argument("pdf")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("-t", "--terms", help="file with your own terms (names, address ...)")
    ap.add_argument("-o", "--out", help="output PDF (default: <name>_anonymized.pdf)")
    ap.add_argument("--pages", help="keep only these pages, e.g. 1,3-6")
    ap.add_argument("--keep-dates", action="store_true", help="do not redact dates")
    ap.add_argument("--remove-scans", action="store_true",
                    help="black out scanned pages completely instead of checking via OCR")
    ap.add_argument("--keep-images", action="store_true", help="do not remove images and barcodes (also vector barcodes)")
    ap.add_argument("--no-preview", action="store_true", help="do not render PNG previews")
    ap.add_argument("--region", action="append", metavar="S:x0,y0,x1,y1",
                    help="redact an area by hand, coordinates in %% of the page, "
                         "S = page number (original) or * for all. Can be repeated.")
    ap.add_argument("--redact-unreadable", action="store_true",
                    help="also redact unreadable text (font without Unicode mapping)")
    ap.add_argument("--inspect", action="store_true",
                    help="analyse only: list images, vector graphics, unreadable text")
    ap.add_argument("--barcode-pad", type=float, default=4, metavar="PT",
                    help="extra margin around barcodes in pt (default 4, e.g. 10 for more safety)")
    ap.add_argument("--debug-barcodes", action="store_true",
                    help="visualise barcode detection (debug PNGs + statistics, nothing is redacted)")
    ap.add_argument("--check", action="store_true",
                    help="check only: search the PDF for sensitive information, change nothing")
    ap.add_argument("--show-values", action="store_true",
                    help="show found values unmasked in the report")
    ap.add_argument("--json", metavar="FILE", help="also save the check report as JSON")
    ap.add_argument("--no-ner", action="store_true", help="do not use AI name recognition (spaCy)")
    ap.add_argument("--no-auto-names", action="store_true",
                    help="do not automatically redact detected names (after label / spaCy) throughout the document")
    args = ap.parse_args()

    print(f"anonywiz {__version__}")
    global BARCODE_PAD
    BARCODE_PAD = args.barcode_pad
    src = Path(args.pdf)
    out = Path(args.out) if args.out else src.with_name(src.stem + "_anonymized.pdf")
    terms = load_terms(args.terms)
    patterns = {k: v for k, v in PATTERNS.items() if not (k == "Date" and args.keep_dates)}

    src = src.expanduser()
    if not src.is_file():
        pdfs = sorted(p.name for p in Path.cwd().glob("*.pdf"))
        sys.exit(f"File not found: {src}\n"
                 f"Current folder: {Path.cwd()}\n"
                 f"PDFs here: {', '.join(pdfs) or 'none'}\n"
                 "Tip: put file names containing spaces in quotes, "
                 "or drag the file into the terminal.")
    head = src.read_bytes()[:1024]
    if b"%PDF" not in head:
        sys.exit(f"'{src.name}' is not a PDF (file starts with: {head[:8]!r}).\n"
                 "EasyTax files (.tax/.esteuer) are not PDFs - export a PDF from EasyTax "
                 "via 'Print/Create PDF'.")
    try:
        doc = fitz.open(src)
    except Exception as e:
        sys.exit(f"PDF could not be opened: {type(e).__name__}: {e}\n"
                 "It may be damaged - open it in a viewer and try 'Export as PDF'.")
    if doc.needs_pass:
        sys.exit("PDF is password-protected - please unlock it first.")

    if args.check:
        F = run_check(doc, terms, args, src.name)
        sys.exit(2 if any(x["sev"] == "HIGH" for x in F)
                 else 1 if any(x["sev"] == "MEDIUM" for x in F) else 0)

    # bake form fields & annotations into the page content
    if hasattr(doc, "bake"):
        doc.bake()
    else:
        print("WARN: PyMuPDF too old for doc.bake() - form fields may be preserved. "
              "Update: pip install -U pymupdf")

    if args.inspect:
        inspect(doc)
        return
    if args.debug_barcodes:
        debug_barcodes(doc, src.with_name(src.stem + "_debug"))
        return

    auto_names = [] if args.no_auto_names else detect_names(doc, use_ner=not args.no_ner)
    if auto_names:
        print(f"Automatically detected names ({len(auto_names)}): "
              f"{', '.join(_mask(n, False) for n in auto_names)} - redacted everywhere "
              "(disable: --no-auto-names)")

    # select pages
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
                print(f"  WARN page {orig+1}: contains a scan, but Tesseract (OCR) is missing - "
                      "the scan stays UNCHECKED! Install: brew install tesseract tesseract-lang "
                      "(or use --remove-scans)")
            else:
                stats["Scan checked via OCR"] = stats.get("Scan checked via OCR", 0) + 1

        for name, rects in find_hits(page, patterns, terms, tp).items():
            for r in rects:
                add(page, r, name + (" (Scan)" if tp is not None else ""))

        if not args.keep_images:
            for r in small_imgs:
                add(page, r, "Image/Barcode")
            for r in barcode_boxes(page):
                add(page, r, "Vector barcode")
            for sr in scans:
                if args.remove_scans:
                    add(page, sr, "Scan removed")
                    continue
                bcs = scan_barcodes(page, sr)
                if bcs is None:
                    print(f"  WARN page {orig+1}: barcodes in scan not checkable - "
                          "pip install zxing-cpp numpy")
                else:
                    for r in bcs:
                        add(page, r, "Barcode in scan")

        for r in find_name_hits(page, auto_names, tp):
            add(page, r, "Name (automatic)")

        hid = hidden_text(page)
        n_spans = sum(1 for b in page.get_text("dict")["blocks"] for l in b.get("lines", [])
                      for sp in l["spans"] if sp["text"].strip())
        if hid and n_spans and len(hid) > 0.3 * n_spans:
            print(f"  WARN page {orig+1}: {len(hid)} of {n_spans} text spans count as "
                  "hidden - looks like a false detection, NOT redacted. "
                  "Please check visually (--check shows the places).")
        else:
            for r, cat, _ in hid:
                add(page, r, cat)

        unr = unreadable_spans(page)
        if unr:
            if args.redact_unreadable:
                for r in unr:
                    add(page, r, "Unreadable text")
            else:
                print(f"  WARN page {orig+1}: {len(unr)} text span(s) not readable - "
                      "not found by the search. Use --inspect or --redact-unreadable")

        for pg, c in regions:
            if pg is None or pg == orig:
                add(page, pct_rect(page, c), "Manual region")

        red_area = sum(_area(a.rect) for a in page.annots()
                       if a.type[0] == fitz.PDF_ANNOT_REDACT)
        if red_area > 0.5 * _area(page.rect):
            print(f"  WARN page {orig+1}: over 50 % of the page would be redacted - "
                  "please check the preview. Narrow down the cause with --check or --debug-barcodes.")

        # scans: paint over only the affected pixels, do not remove the whole image
        keep_scan = scans and not args.remove_scans
        kw = {"images": fitz.PDF_REDACT_IMAGE_PIXELS if keep_scan else fitz.PDF_REDACT_IMAGE_REMOVE}
        if hasattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED"):
            kw["graphics"] = fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED
        page.apply_redactions(**kw)

    # remove metadata & attachments
    doc.set_metadata({})
    doc.del_xml_metadata()
    for name in list(doc.embfile_names()):
        doc.embfile_del(name)
        stats["Attachment"] = stats.get("Attachment", 0) + 1

    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()

    # re-check the output
    check = fitz.open(out)
    F = run_check(check, terms, args, out.name)
    leftovers = sum(1 for x in F if x["sev"] in ("HIGH", "MEDIUM"))

    if not args.no_preview:
        prev_dir = out.with_name(out.stem + "_preview")
        prev_dir.mkdir(exist_ok=True)
        for i, page in enumerate(check, 1):
            page.get_pixmap(dpi=110).save(prev_dir / f"seite_{i:02d}.png")
    check.close()

    print(f"\nSaved:       {out}")
    print("Redacted:   ", ", ".join(f"{k} {v}x" for k, v in stats.items()) or "nothing")
    print("Re-check:   ", "OK - nothing sensitive found" if leftovers == 0
          else f"{leftovers} finding(s) HIGH/MEDIUM - see report above, redact more with --region if needed")
    if not args.no_preview:
        print(f"Preview:     {prev_dir}/  -> please look through every page")
    print("\nNote: automatic detection is heuristic. Names without a label that are "
          "not in terms.txt can slip through - always check the preview visually.")


if __name__ == "__main__":
    main()
