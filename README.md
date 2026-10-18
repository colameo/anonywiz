# anonywiz

Redacts sensitive data, salary statements and pension fund statements, **locally and offline**. Your file never leaves your computer.

The redaction is real: text and image pixels are removed from the PDF, not just covered with black boxes. Afterwards the script automatically re-checks the output and renders a preview image of every page.

```bash
pipx install git+https://github.com/colameo/anonywiz.git
```

Show the version with `anonywiz --version`.

> The tool targets Swiss, German-language documents (the label and address detection looks for German words such as "Ehefrau" or "Bahnhofstrasse"). Its messages and reports are in English.

---

## What gets redacted

| Category | Examples |
|---|---|
| Patterns | AHV/AVS social security no. (756.xxxx.xxxx.xx), IBAN CH/LI, company UID (CHE-…), ISIN, e-mail, phone, dates |
| Addresses | street + house number, postcode + town |
| Your own terms | everything in `terms.txt` (names, company, employer, bank …) |
| Names found automatically | names after a label ("Ehefrau: Anna Beispiel") and, with spaCy, person names; each name part is then redacted as a whole word everywhere in the document (`--no-auto-names` to disable) |
| Barcodes | as images, as vector graphics (1D, PDF417, QR) and in barcode fonts, including the human-readable number below |
| Scans | text recognition (OCR) on the image itself, only the matches are painted over |
| Hidden content | white text, micro text, text under black bars (fake redaction) |
| File content | metadata, XMP, embedded files (EasyTax sometimes embeds an XML with all data), form fields |

Figures such as salary, assets or deductions stay visible.

---

## Installation

**1. Install pipx** (once, then open a new terminal):

| macOS | Linux (Debian/Ubuntu) | Windows (PowerShell) |
|---|---|---|
| `brew install pipx && pipx ensurepath` | `sudo apt install pipx && pipx ensurepath` | `py -m pip install --user pipx` then `py -m pipx ensurepath` |

**2. Install anonywiz** (same on every OS):

```bash
pipx install git+https://github.com/colameo/anonywiz.git
```

Done. This provides the command `anonywiz`:

```bash
anonywiz --version
anonywiz file.pdf -t terms.txt
```

### Optional extras

- **Scanned pages (OCR):** Tesseract is a system tool, not installable via pip. It must be on the `PATH` and list "deu" in `tesseract --list-langs`.

  | macOS | Linux (Debian/Ubuntu) | Windows |
  |---|---|---|
  | `brew install tesseract tesseract-lang` | `sudo apt install tesseract-ocr tesseract-ocr-deu` | Installer from [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki), select German under "Additional language data", add its folder to `PATH` |

- **Update:** `pipx upgrade anonywiz`
- **Optional AI name detection:** `pipx inject anonywiz spacy`, then download the model into the same environment:
  `$(pipx environment --value PIPX_LOCAL_VENVS)/anonywiz/bin/python -m spacy download de_core_news_md`
  (Windows: replace `bin/python` with `Scripts\python.exe`)
- **From a local checkout:** `pipx install .`
- **Without Python:** download the standalone program for your OS (`anonywiz-linux-x86_64`, `-macos-arm64`, `-windows-x86_64.exe`) from the [Releases](https://github.com/colameo/anonywiz/releases) page. Checksums are in `SHA256SUMS.txt`. Tesseract (OCR) is still a separate install.
- **Make a release (maintainers):** see [Releases (maintainers)](#releases-maintainers).

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
anonywiz tax_return.pdf -t terms.txt
```

Output in the same folder:

- `tax_return_anonymized.pdf`: the redacted file
- `tax_return_anonymized_preview/`: one PNG per page for review

### 3. Read the report

The re-check is printed at the end:

```
Redacted:    AHV no. 1x, IBAN 1x, Term 9x, Vector barcode 1x, ...
Re-check:    OK - nothing sensitive found
```

If it reports findings, each comes with a ready-made `--region` parameter to redact it.

### 4. Look at the preview

**Always.** Detection is heuristic. Names that have no field label, are not in `terms.txt` and are missed by the optional AI recognition, as well as handwriting and stamps in scans, can slip through.

---

## Comparison with Microsoft Presidio

[Presidio](https://github.com/microsoft/presidio) is a general PII detection library (analyzer, anonymizer, image redactor) that you assemble into a pipeline. anonywiz is a ready-to-use tool for redacting Swiss PDFs. The ratings below are a subjective assessment for Swiss PDFs (tax returns, salary and pension statements), not benchmarks.

| Feature | anonywiz | Presidio |
|---|---|---|
| Ready to use out of the box | ★★★★★ | ★★ |
| PDF redaction (text and pixels removed) | ★★★★★ | ★★ |
| Hidden content (white text, micro text, fake redaction) | ★★★★★ | ★ |
| Metadata, XMP, embedded files | ★★★★★ | ★ |
| Swiss patterns (AHV, IBAN, UID, Valor, phone, address) | ★★★★★ | ★★ |
| International entity coverage | ★★ | ★★★★★ |
| Name and entity recognition (NER) | ★★★ | ★★★★★ |
| Redacting a detected name everywhere (all occurrences, whole words) | ★★★★ | ★★ |
| Languages supported | ★★ | ★★★★ |
| Scans and OCR | ★★★★ | ★★★ |
| Barcodes and QR codes | ★★★★ | ★ |
| Self-check and preview of the output | ★★★★★ | ★ |
| Anonymization options (mask, hash, replace, encrypt) | ★★ | ★★★★★ |
| Extensibility and architecture | ★★ | ★★★★★ |
| Free text, databases, pipelines | ★ | ★★★★★ |
| Offline and privacy | ★★★★★ | ★★★★ |
| Maturity, testing, community | ★★ | ★★★★★ |
| Install simplicity | ★★★★ | ★★★ |

anonywiz wins on PDF handling and Swiss data; Presidio wins on general detection, scale and engineering maturity. Detected names (after a label, or via spaCy) are redacted throughout the document, but the detection itself is weaker than Presidio's. They combine well: Presidio's analyzer could replace the name detection while anonywiz does the PDF redaction and verification. Both are heuristic and neither guarantees that nothing was missed.

---

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                      # 100+ tests, ~1 s, no Tesseract needed
.venv/bin/pytest --cov=anonywiz --cov-report=term-missing
```

They also run on every push and pull request (Linux, macOS, Windows) via GitHub Actions.

The tests build small PDFs on the fly (patterns, hidden text, barcodes, scans, metadata, attachments) and cover the helpers, the detection (`--check`) and the full command line (redaction, `--pages`, `--region`, `--inspect`, exit codes). OCR and spaCy are replaced by stubs.

---

## Common tasks

```bash
# Check only, change nothing (original or already redacted file)
anonywiz file.pdf -t terms.txt --check

# Check report as JSON, values unmasked
anonywiz file.pdf -t terms.txt --check --json report.json --show-values

# Keep only certain pages
anonywiz file.pdf -t terms.txt --pages 1-4,9

# Redact an area by hand (percent of page: x0,y0,x1,y1)
anonywiz file.pdf -t terms.txt --region "1:60,5,95,12"      # page 1
anonywiz file.pdf -t terms.txt --region "*:0,0,100,8"       # header on all pages

# Show positions of images, barcodes and unreadable text
anonywiz file.pdf --inspect

# Visualise barcode detection (debug PNGs, nothing is redacted)
anonywiz file.pdf --debug-barcodes

# Redact barcodes with a wider margin
anonywiz file.pdf -t terms.txt --barcode-pad 10

# Black out scanned pages completely instead of running OCR
anonywiz file.pdf -t terms.txt --remove-scans
```

### All options

| Option | Effect |
|---|---|
| `-t, --terms FILE` | redact your own terms |
| `-o, --out FILE` | output file name (default: `<name>_anonymized.pdf`) |
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
| `--no-auto-names` | do not redact automatically detected names (after a label, or via spaCy) throughout the document |
| `--inspect` | analysis without redaction |
| `--debug-barcodes` | barcode diagnosis with debug PNGs |
| `--version` | show version |

---

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| `unrecognized arguments` | Old version. Check with `--version` and update with `pipx upgrade anonywiz`. |
| A common word was blacked out everywhere | It was detected as a name (shown as "Automatically detected names" at the start). Run again with `--no-auto-names`, or fix the label in the source. |
| Whole page black | The page is a scan and Tesseract is missing. Install Tesseract, or use `--remove-scans`. |
| Text selectable in macOS Preview, script reports "no readable text" | macOS Live Text recognises text in images on screen only. The file is still a scan. |
| Barcode partly visible | Try `--barcode-pad 10`, otherwise `--debug-barcodes` and `--region`. |
| "Possible barcode remnant" on the canton's coat of arms | False alarm (wave lines in the logo). |
| `error: externally-managed-environment` from pip | Install with pipx as described in the installation section. |
| `File not found` | Put file names with spaces in quotes, or drag the file into the terminal. |
| `is not a PDF` | EasyTax data file instead of a PDF. Print or export it as PDF from EasyTax. |

---

## Limitations

- Detection is heuristic. An automated check does not replace looking at the preview.
- Automatically detected names are redacted as whole words everywhere, so a wrong detection can black out a common word (see `--no-auto-names`).
- OCR misses handwriting, stamps and poorly scanned areas.
- Redact too much rather than too little. Remove pages you don't need with `--pages`.
- Safer still: type out only the figures you need instead of sharing a PDF.

---

## Contributing

Contributions are welcome: bug reports, new patterns, better detection, documentation, tests.

### Reporting a problem

Open an [issue](https://github.com/colameo/anonywiz/issues) with the command you ran, the output of `anonywiz --version`, your OS and Python version, and what you expected.

> **Never attach real documents or real personal data** (PDFs, screenshots, reports with `--show-values`) to an issue or pull request. Reproduce the problem with a synthetic PDF, or describe the layout and use made-up values.

### Making a change

You cannot push to this repository directly. Changes come in through a fork and a pull request:

1. **Fork** the repository on GitHub (button top right) and clone your fork:
   ```bash
   git clone https://github.com/<your-username>/anonywiz.git
   cd anonywiz
   git remote add upstream https://github.com/colameo/anonywiz.git
   ```
2. **Create a branch** from an up-to-date `master`. One topic per branch:
   ```bash
   git fetch upstream && git checkout -b fix-short-description upstream/master
   ```
3. **Set up** the development environment (Python 3.10 or newer):
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
   ```
4. **Make the change** and add or update tests in `tests/`. Bug fixes should come with a test that fails without the fix.
5. **Run the tests** and make sure they pass:
   ```bash
   .venv/bin/pytest
   ```
6. **Commit** with a short, imperative one-line message (for example `Fix crash on older PyMuPDF`), then push the branch to your fork:
   ```bash
   git push -u origin fix-short-description
   ```
7. **Open a pull request** against `master` of `colameo/anonywiz`. Describe what changed and why, and link the issue if there is one. The CI workflow runs the tests on Linux, macOS and Windows; they must be green before merging.
8. **Respond to review comments** by pushing more commits to the same branch. The pull request updates automatically.

For larger changes (new features, refactorings), open an issue first to agree on the approach.

### Guidelines

- Keep the style of the surrounding code: naming, comment density, and English user-facing messages and comments (only the detection vocabulary, such as street suffixes and label words, is German).
- Detection changes need tests with synthetic data and should not weaken what is already caught. Prefer redacting too much over too little.
- The tool must stay **offline**: no network calls, no telemetry.
- Do not commit personal data, real documents, `terms.txt` or generated output (`*_anonymized.pdf`, `*_preview/`).
- Keep dependencies minimal; new ones need a good reason.
- Update this README when behaviour or options change.
- Contributions are licensed under the project's [MIT License](LICENSE).

### Releases (maintainers)

Versions are `major.minor.patch` and live in one place, `__version__` in `anonywiz.py` (`pyproject.toml` reads it).

Run **Actions → Release → Run workflow** and choose:

| Input | Effect |
|---|---|
| `bump` = `patch` / `minor` / `major` | raise that part of the current version (`0.1.0` → `0.1.1` / `0.2.0` / `1.0.0`) |
| `bump` = `current` | release the version as it is now in the code (for the very first release) |
| `version` = `x.y.z` | optional, release exactly this version instead of bumping |
| `dry_run` | build and test only; no commit, tag or release |

The workflow then commits `Release vX.Y.Z` to the branch, runs the tests on Linux, macOS and Windows, builds the wheel/sdist and the standalone binaries, creates the tag `vX.Y.Z` on the tested commit and publishes the GitHub release with checksums and release notes that list every commit since the previous release (the automatic `Release vX.Y.Z` commits are left out), plus a compare link. The branch must allow pushes from GitHub Actions.

You can also tag by hand (`git tag vX.Y.Z && git push --tags`); the tag has to match `__version__`.

---

## Disclaimer

This software is provided **"as is"**, without warranty or guarantee of any kind, express or implied. In particular, there is no guarantee that all sensitive data will be detected and removed.

- You use it **at your own risk**. Check every result yourself before sharing a document.
- The authors are not liable for any damage, data loss or disclosure of personal data resulting from its use.
- This tool is **not tax, legal or data protection advice**.

## License

Freely available under the [MIT License](LICENSE): you may use, copy, modify and distribute the code free of charge, including commercially. The license notice must be retained.

Note: the dependency [PyMuPDF](https://pymupdf.readthedocs.io) is licensed under the AGPL-3.0 (or a commercial licence from Artifex). Check this if you redistribute the tool, for example the standalone binaries, in a closed-source product.
