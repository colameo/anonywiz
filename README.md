# anonywiz

Redacts sensitive data, salary statements and pension fund statements, **locally and offline**. Your file never leaves your computer.

The redaction is real: text and image pixels are removed from the PDF, not just covered with black boxes. Afterwards the script automatically re-checks the output and renders a preview image of every page.

Works on **PDFs** (real redaction, with preview) and on **plain text files** (`.txt`, `.md`, `.csv` …):

```bash
anonywiz tax_return.pdf -t terms.txt     # -> tax_return_anonymized.pdf (+ preview images)
anonywiz notes.txt -t terms.txt          # -> notes_anonymized.txt (matches become [REDACTED])
```

Details for text files: [Text files](#text-files).

```bash
pipx install git+https://github.com/colameo/anonywiz.git
```

Show the version with `anonywiz --version` (or `-v`).

> The tool targets Swiss documents in German, English, French and Italian. The label, address and birth date detection looks for words such as "Ehefrau" / "Spouse" / "Épouse" / "Coniuge", "Bahnhofstrasse" / "Rue de la Gare" / "Via Roma" or "Geburtsdatum" / "Date of birth" / "Date de naissance" / "Data di nascita". Its messages and reports are in English.

---

## What gets redacted

| Category | Examples |
|---|---|
| Patterns | AHV/AVS social security no. (756.xxxx.xxxx.xx), IBAN CH/LI, company UID (CHE-…), ISIN, e-mail, phone, dates (whole, or day/month only with `--date-mode year`) |
| Addresses | street + house number (`Bahnhofstrasse 12`, `Rue de la Gare 12`, `Via Roma 5`, `12 High Street`), postcode + town |
| Your own terms | everything in `terms.txt` (names, company, employer, bank …) |
| Names found automatically | names after a label ("Ehefrau: Anna Beispiel", "Prénom: Élodie Dupont", "Cognome: Rossi", "Surname Muster") and, with spaCy, person names; each name part is then redacted as a whole word everywhere in the document (`--no-auto-names` to disable) |
| Barcodes | as images, as vector graphics (1D, PDF417, QR) and in barcode fonts, including the human-readable number below |
| Scans | text recognition (OCR) on the image itself, only the matches are painted over |
| Hidden content | white text, micro text, text under black bars (fake redaction) |
| File content | metadata, XMP, embedded files (EasyTax sometimes embeds an XML with all data), form fields |

Figures such as salary, assets or deductions stay visible.

Plain text files (`.txt`, `.md`, `.csv` …) get the same detectors for patterns, addresses, your terms, names and dates; see [Text files](#text-files).

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

Dates are redacted completely by default. To keep the year visible (`31.12.2023` becomes `▮▮▮▮▮▮2023`, also for birth dates) add `--date-mode year`; to keep dates add `--date-mode none` (birth dates after a label stay redacted).

### Text files

Any file that is not a PDF but readable as text (UTF-8 or Windows-1252) is anonymised with the same detectors: patterns, addresses, your terms, automatically detected names and dates (`--date-mode`).

```bash
anonywiz notes.txt -t terms.txt
```

- Output: `notes_anonymized.txt` next to the original (or the name given with `-o`).
- Every match is replaced by `[REDACTED]`. With `--date-mode year`, `31.12.2023` becomes `XX.XX.2023`.
- The output is checked again and leftover findings are reported.
- `--check` only counts the findings per category and changes nothing (exit code 2 if anything was found).
- PDF-only options (`--pages`, `--region`, `--keep-images`, `--no-preview` …) have no effect on text files.
- Files with a `.pdf` extension are always treated as PDFs.

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
.venv/bin/pytest                      # 160+ tests, ~1 s, no Tesseract needed
.venv/bin/pytest --cov=anonywiz --cov-report=term-missing
```

They also run on every push and pull request (Linux, macOS, Windows) via GitHub Actions.

The tests build small PDFs on the fly (patterns, dates, hidden text, barcodes, scans, metadata, attachments) and cover the helpers (including the German, English, French and Italian vocabulary), the detection (`--check`) and the full command line (redaction, `--date-mode`, `--pages`, `--region`, `--inspect`, exit codes). OCR and spaCy are replaced by stubs.

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

# Redact day and month of dates, keep the year (also for birth dates)
anonywiz file.pdf -t terms.txt --date-mode year

# Anonymise a text file (writes notes_anonymized.txt)
anonywiz notes.txt -t terms.txt
```

### All options

| Option | Effect |
|---|---|
| `-t, --terms FILE` | redact your own terms |
| `-o, --out FILE` | output file name (default: `<name>_anonymized.pdf`, or `<name>_anonymized.<ext>` for text files) |
| `--pages 1,3-6` | keep only these pages |
| `--region S:x0,y0,x1,y1` | redact an area by hand, can be repeated |
| `--date-mode full\|year\|none` | `full` redacts whole dates (default), `year` redacts day and month but keeps the year, `none` keeps dates. In `full` and `none` mode birth dates (after a label such as `geb.`, `Geburtsdatum`, `born`, `date of birth`, `né le`, `date de naissance`, `nato il`, `data di nascita`) are always redacted completely; `year` applies to them too |
| `--keep-dates` | same as `--date-mode none` |
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
| `-v, --version` | show version |

---

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| `unrecognized arguments` | Old version. Check with `--version` and update with `pipx upgrade anonywiz`. |
| A common word was blacked out everywhere | It was detected as a name (shown as "Automatically detected names" at the start). Run again with `--no-auto-names`, or fix the label in the source. |
| Whole page black | The page is a scan and Tesseract is missing. Install Tesseract, or use `--remove-scans`. |
| Text selectable in macOS Preview, script reports "no readable text" | macOS Live Text recognises text in images on screen only. The file is still a scan. |
| Year of a date is blacked out although `--date-mode year` was used | Old version (before the barcode fix): redaction boxes of a date column were mistaken for a barcode. Update with `pipx upgrade anonywiz` or reinstall from the repository. If it still happens, see the open issues about birth dates and the fallback to whole dates. |
| Birth date still visible with `--date-mode year` or `none` | The date has no birth label right before it (table header, or no label). See open issues. Use `--date-mode full` or `--region`. |
| A header such as `Nome Cognome` or `First name` was blacked out | It was taken for a person's name. Label words of all four languages are on the stop word list; if another word slips through, run with `--no-auto-names` or add it to `STOPWORDS`. |
| Barcode partly visible | Try `--barcode-pad 10`, otherwise `--debug-barcodes` and `--region`. |
| "Possible barcode remnant" on the canton's coat of arms | False alarm (wave lines in the logo). |
| `error: externally-managed-environment` from pip | Install with pipx as described in the installation section. |
| `File not found` | Put file names with spaces in quotes, or drag the file into the terminal. |
| `is not a PDF` | EasyTax data file instead of a PDF. Print or export it as PDF from EasyTax. |

---

## Known limitations and open issues

### Limitations

- Detection is heuristic. An automated check does not replace looking at the preview.
- Automatically detected names are redacted as whole words everywhere, so a wrong detection can black out a common word (see `--no-auto-names`).
- OCR misses handwriting, stamps and poorly scanned areas.
- The detection vocabulary (label words, street words, birth date labels, stop words) covers German, English, French and Italian. Other languages are covered only by the language-independent patterns (AHV, IBAN, UID, ISIN, e-mail, phone, card numbers, dates).
- In text files only the detectors apply; there are no images, hidden content or metadata to handle, and the card number check (Luhn) is not used.
- Dates are only recognised as `dd.mm.yyyy` (1 or 2 digit day and month, year 19xx or 20xx).
- Redact too much rather than too little. Remove pages you don't need with `--pages`.
- Safer still: type out only the figures you need instead of sharing a PDF.

### Open issues

Known problems that are not solved yet. Pull requests are welcome (see [Contributing](#contributing)).

- [ ] **Birth dates are only recognised by a label right before the date** (`geb.`, `Geburtsdatum`, `date of birth`, `date de naissance`, `data di nascita` and so on, within 30 characters). Two cases are missed:
  - a table where `Geburtsdatum` is a column header and the dates are in the rows below,
  - a date without any label, for example after the AHV number (`756.… - 24.06.1965`).

  Such dates count as ordinary dates. With `--date-mode full` (default) they are still redacted. With `--date-mode year` the year stays visible, and with `--date-mode none` the whole date stays visible, so the birth date remains in the output.
- [ ] **`--date-mode year` falls back to redacting the whole date** if the day/month part cannot be located exactly (for example a date wrapped across two lines).
- [ ] **`--date-mode none` with a birth label:** if the same date text appears once with and once without a birth label, all occurrences are redacted.
- [ ] **Language coverage is a word list, not a model.** Label words for other Swiss or neighbouring languages (Romansh, Portuguese, Spanish …) are not included, and generic words after a label can still be taken for a name (`--no-auto-names`). Add words to `LABEL_RX`, `STREET_RX`, `BIRTH_CTX` and `STOPWORDS` in `anonywiz.py`, with a test.
- [ ] **Other date formats are not detected** (`1.3.80`, `12. März 1980`, `1980-03-12`, `12/03/1980`).
- [ ] **Vector barcode detection is a heuristic.** It can mark logos or coats of arms as "Possible barcode remnant", and a barcode drawn in an unusual way may be missed. Check the preview of every page. Redaction boxes queued by the script itself are ignored since this version; before that, a column of dates could be mistaken for a barcode and blacked out completely.
- [ ] **Noisy `--check` report.** Typical false alarms are `Raw data: Email` (random bytes inside embedded fonts that look like an e-mail address), `Unreadable text` (fonts without a Unicode mapping) and `Possible barcode remnant` on logos. A re-check of a correctly redacted file can therefore report HIGH/MEDIUM findings. Look at what was found before assuming something leaked.
- [ ] **Unreadable text is not redacted by default** (fonts without a Unicode mapping cannot be searched). Use `--inspect` and `--redact-unreadable` or `--region`.
- [ ] **Names without a label** are only found with the optional spaCy model, or by listing them in `terms.txt`.
- [ ] **No automatic end-to-end test on real documents.** The tests use synthetic PDFs. Layouts that behave differently (tables, tightly stacked rows, unusual fonts) are only found by looking at the output.

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
   To try your change on a real file before pushing, use the editable install from step 3. `.venv/bin/anonywiz` then runs your working copy, and edits to `anonywiz.py` take effect immediately without reinstalling:
   ```bash
   .venv/bin/anonywiz sample.pdf -t terms.txt
   ```
   Without installing, `.venv/bin/python anonywiz.py sample.pdf` from the repository folder works as well.

   **Using your working copy as the global `anonywiz` command:** if you installed anonywiz with pipx before, a plain `anonywiz` still runs that installed release, not your changes (the version number is the same, so it is easy to miss; typical sign: new features report "not a PDF" or an unknown option). Replace it once with an editable install:
   ```bash
   pipx install --force -e .      # run in the repository folder
   which -a anonywiz              # should list only ~/.local/bin/anonywiz
   ```
   From then on `anonywiz` runs your working copy. To go back to the release: `pipx install --force git+https://github.com/colameo/anonywiz.git`.
6. **Commit** with a short, imperative one-line message (for example `Fix crash on older PyMuPDF`), then push the branch to your fork:
   ```bash
   git push -u origin fix-short-description
   ```
7. **Open a pull request** against `master` of `colameo/anonywiz`. Describe what changed and why, and link the issue if there is one. The CI workflow runs the tests on Linux, macOS and Windows; they must be green before merging.
8. **Respond to review comments** by pushing more commits to the same branch. The pull request updates automatically.

For larger changes (new features, refactorings), open an issue first to agree on the approach.

### Guidelines

- Keep the style of the surrounding code: naming, comment density, and English user-facing messages and comments (only the detection vocabulary, such as street words and label words, is German, English, French and Italian).
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
