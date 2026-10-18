# anonywiz

Redacts sensitive data, salary statements and pension fund statements, **locally and offline**. Your file never leaves your computer.

The redaction is real: text and image pixels are removed from the PDF, not just covered with black boxes. Afterwards the script automatically re-checks the output and renders a preview image of every page.

```bash
pipx install git+https://github.com/colameo/anonywiz.git
```

Current version: `2026-10-17.9` (show with `anonywizard --version`)

> The tool targets German-language documents. Its messages and reports are in German.

---

## What gets redacted

| Category | Examples |
|---|---|
| Patterns | AHV/AVS social security no. (756.xxxx.xxxx.xx), IBAN CH/LI, company UID (CHE-…), ISIN, e-mail, phone, dates |
| Addresses | street + house number, postcode + town |
| Your own terms | everything in `terms.txt` (names, company, employer, bank …) |
| Barcodes | as images, as vector graphics (1D, PDF417, QR) and in barcode fonts, including the human-readable number below |
| Scans | text recognition (OCR) on the image itself, only the matches are painted over |
| Hidden content | white text, micro text, text under black bars (fake redaction) |
| File content | metadata, XMP, embedded files (EasyTax sometimes embeds an XML with all data), form fields |

Figures such as salary, assets or deductions stay visible.

---

## Installation (macOS)

**1. Install pipx** (once, then open a new terminal):

```bash
brew install pipx && pipx ensurepath
```

**2. Install anonywiz:**

```bash
pipx install git+https://github.com/colameo/anonywiz.git
```

Done. This provides the commands `anonywizard` and the short alias `anonywiz`:

```bash
anonywizard --version
anonywizard file.pdf -t terms.txt
```

- **Scanned pages (OCR):** Tesseract is a system tool, not installable via pip: `brew install tesseract tesseract-lang` (check: `tesseract --list-langs` must list "deu").
- **Update:** `pipx upgrade anonywiz`
- **Optional AI name detection:** `pipx inject anonywiz spacy && ~/.local/pipx/venvs/anonywiz/bin/python -m spacy download de_core_news_md`
- **From a local checkout:** `pipx install .`

What the packages do:

| Package | Purpose | Required? |
|---|---|---|
| `pymupdf` | read and redact PDFs | yes |
| `tesseract` | text recognition in scans | for scans |
| `zxing-cpp`, `numpy` | find barcodes and QR codes in scans | yes (installed automatically) |
| `spacy` + `de_core_news_md` | additional AI name recognition (local) | no |

---

## Usage in 4 steps

### 1. Create `terms.txt`

```bash
cp terms.example.txt terms.txt
```

`terms.txt` is listed in `.gitignore` and is never committed.

One term per line, `#` starts a comment. List name parts separately, because their order varies in the PDF.

```text
# People
Muster
Peter
Anna
# Address
Bahnhofstrasse
Aarau
# Company, bank, insurer
Beispiel GmbH
Kantonalbank
# Numbers without a standard format
123 456
```

Also add numbers the script cannot recognise automatically: register number, insurance number, address number, contract number, and an IBAN printed with each digit in its own box.

### 2. Anonymise

```bash
anonywizard tax_return.pdf -t terms.txt
```

Output in the same folder:

- `tax_return_anonym.pdf`: the redacted file
- `tax_return_anonym_preview/`: one PNG per page for review

### 3. Read the report

The re-check is printed at the end:

```
Geschwärzt: AHV-Nr 1x, IBAN 1x, Begriff 9x, Vektor-Barcode 1x, …
Gegenprüfung: OK – nichts Sensibles mehr gefunden
```

("Redacted: …" / "Re-check: OK – nothing sensitive found".) If it reports findings, each comes with a ready-made `--region` parameter to redact it.

### 4. Look at the preview

**Always.** Detection is heuristic. Names without a field label that are not in `terms.txt`, as well as handwriting and stamps in scans, can slip through.

---

## Common tasks

```bash
# Check only, change nothing (original or already redacted file)
anonywizard file.pdf -t terms.txt --check

# Check report as JSON, values unmasked
anonywizard file.pdf -t terms.txt --check --json report.json --show-values

# Keep only certain pages
anonywizard file.pdf -t terms.txt --pages 1-4,9

# Redact an area by hand (percent of page: x0,y0,x1,y1)
anonywizard file.pdf -t terms.txt --region "1:60,5,95,12"      # page 1
anonywizard file.pdf -t terms.txt --region "*:0,0,100,8"       # header on all pages

# Show positions of images, barcodes and unreadable text
anonywizard file.pdf --inspect

# Visualise barcode detection (debug PNGs, nothing is redacted)
anonywizard file.pdf --debug-barcodes

# Redact barcodes with a wider margin
anonywizard file.pdf -t terms.txt --barcode-pad 10

# Black out scanned pages completely instead of running OCR
anonywizard file.pdf -t terms.txt --remove-scans
```

### All options

| Option | Effect |
|---|---|
| `-t, --terms FILE` | redact your own terms |
| `-o, --out FILE` | output file name (default: `<name>_anonym.pdf`) |
| `--pages 1,3-6` | keep only these pages |
| `--region S:x0,y0,x1,y1` | redact an area by hand, can be repeated |
| `--keep-dates` | do not redact dates |
| `--keep-images` | do not remove images and barcodes |
| `--remove-scans` | black out scans completely instead of OCR |
| `--redact-unreadable` | also redact text without a Unicode mapping |
| `--barcode-pad PT` | extra margin around barcodes (default 4 pt) |
| `--no-preview` | do not render preview images |
| `--check` | check only; exit code 2 = HIGH, 1 = MEDIUM, 0 = clean |
| `--show-values` | do not mask values in the report |
| `--json FILE` | also save the check report as JSON |
| `--no-ner` | turn off AI name recognition |
| `--inspect` | analysis without redaction |
| `--debug-barcodes` | barcode diagnosis with debug PNGs |
| `--version` | show version |

---

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| `unrecognized arguments` | Old version. Check with `--version` and replace `anonywizard.py` with the new file. |
| Whole page black | The page is a scan and Tesseract is missing. Install Tesseract, or use `--remove-scans`. |
| Text selectable in macOS Preview, script reports "kein auslesbarer Text" (no readable text) | macOS Live Text recognises text in images on screen only. The file is still a scan. |
| Barcode partly visible | Try `--barcode-pad 10`, otherwise `--debug-barcodes` and `--region`. |
| "Möglicher Barcode-Rest" (possible barcode remnant) on the canton's coat of arms | False alarm (wave lines in the logo). |
| `error: externally-managed-environment` from pip | Install with pipx as described in the installation section. |
| `Datei nicht gefunden` (file not found) | Put file names with spaces in quotes, or drag the file into the terminal. |
| `ist kein PDF` (not a PDF) | EasyTax data file instead of a PDF. Print or export it as PDF from EasyTax. |

---

## Limitations

- Detection is heuristic. An automated check does not replace looking at the preview.
- OCR misses handwriting, stamps and poorly scanned areas.
- Redact too much rather than too little. Remove pages you don't need with `--pages`.
- Safer still: type out only the figures you need instead of sharing a PDF.

---

## Disclaimer

This software is provided **"as is"**, without warranty or guarantee of any kind, express or implied. In particular, there is no guarantee that all sensitive data will be detected and removed.

- You use it **at your own risk**. Check every result yourself before sharing a document.
- The authors are not liable for any damage, data loss or disclosure of personal data resulting from its use.
- This tool is **not tax, legal or data protection advice**.

## License

Freely available under the [MIT License](LICENSE): you may use, copy, modify and distribute the code free of charge, including commercially. The license notice must be retained.
