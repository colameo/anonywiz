# anonywiz

Schwärzt sensible Daten in ein PDF-Dokument, Steuererklärungen, Lohnausweisen, PK-Ausweisen usw. **lokal und offline**. Die Datei verlässt deinen Computer nicht.

Die Schwärzung ist echt: Text und Bildpunkte werden aus dem PDF entfernt und nicht nur mit schwarzen Kästen überdeckt. Danach prüft das Skript die Ausgabe automatisch noch einmal und erzeugt Vorschaubilder jeder Seite.

Aktuelle Version: `2026-10-17.9` (anzeigen mit `python3 anonywiz.py --version`)

---

## Was geschwärzt wird

| Kategorie | Beispiele |
|---|---|
| Muster | AHV-Nr. (756.xxxx.xxxx.xx), IBAN CH/LI, UID (CHE-…), ISIN, E-Mail, Telefon, Datumsangaben |
| Adressen | Strasse + Hausnummer, PLZ + Ort |
| Eigene Begriffe | alles aus `terms.txt` (Namen, Firma, Arbeitgeber, Bank …) |
| Barcodes | als Bild, als Vektorgrafik (Strichcode, PDF417, QR) und in Barcode-Schriften, inkl. Klartextnummer darunter |
| Scans | Texterkennung (OCR) direkt auf dem Bild, nur die Fundstellen werden übermalt |
| Versteckt | weisser Text, Mikroschrift, Text unter schwarzen Balken (Pseudo-Schwärzung) |
| Datei-Inhalt | Metadaten, XMP, eingebettete Dateien (EasyTax bettet teils XML mit allen Daten ein), Formularfelder |

Zahlen wie Lohn, Vermögen oder Abzüge bleiben stehen.

---

## Installation (macOS)

```bash
# Python-Umgebung (nötig, weil Homebrew-Python «pip install» blockiert)
python3 -m venv ~/anonywiz-venv
~/anonywiz-venv/bin/pip install pymupdf zxing-cpp numpy

# Texterkennung für eingescannte Seiten
brew install tesseract tesseract-lang
tesseract --list-langs      # «deu» muss in der Liste stehen
```

Danach das Skript immer mit `~/anonywiz-venv/bin/python anonywiz.py …` starten. Kürzer geht es mit einem Alias:

```bash
alias anonywiz='~/anonywiz-venv/bin/python ~/pfad/zu/anonywiz.py'
```

Was die Pakete machen:

| Paket | Wozu | Pflicht? |
|---|---|---|
| `pymupdf` | PDF lesen und schwärzen | ja |
| `tesseract` | Texterkennung in Scans | für Scans |
| `zxing-cpp`, `numpy` | Barcodes und QR-Codes in Scans finden | für Scans |
| `spacy` + `de_core_news_md` | zusätzliche Namenserkennung per KI (lokal) | nein |

---

## Bedienung in 4 Schritten

### 1. `terms.txt` anlegen

Ein Begriff pro Zeile, `#` leitet einen Kommentar ein. Führe Namensteile einzeln auf, weil die Reihenfolge im PDF variiert.

```text
# Personen
Muster
Peter
Anna
# Adresse
Bahnhofstrasse
Aarau
# Firma, Bank, Versicherung
Beispiel GmbH
Kantonalbank
# Nummern, die kein Standardformat haben
457 221
```

Nimm auch Nummern auf, die das Skript nicht automatisch erkennt: Registernummer, Versichertennummer, Adressnummer, Vertragsnummer und eine IBAN, bei der jede Ziffer in einem eigenen Kästchen steht.

### 2. anonywizmisieren

```bash
anonywiz steuererklaerung.pdf -t terms.txt
```

Ergebnis im gleichen Ordner:

- `secretdoc_anonywizm.pdf`: die geschwärzte Datei
- `secretdoc_anonywizm_preview/`: ein PNG pro Seite zur Kontrolle

### 3. Bericht lesen

Am Ende steht die Gegenprüfung:

```
Geschwärzt: AHV-Nr 1x, IBAN 1x, Begriff 9x, Vektor-Barcode 1x, …
Gegenprüfung: OK – nichts Sensibles mehr gefunden
```

Meldet sie Funde, steht bei jedem ein fertiger `--region`-Parameter zum Nachschwärzen.

### 4. Vorschau ansehen

**Immer.** Die Erkennung arbeitet heuristisch. Namen ohne Feldbezeichnung, die nicht in `terms.txt` stehen, sowie Handschrift und Stempel in Scans können durchrutschen.

---

## Häufige Aufgaben

```bash
# Nur prüfen, nichts verändern (Original oder bereits geschwärzte Datei)
anonywiz datei.pdf -t terms.txt --check

# Prüfbericht als JSON, Werte unmaskiert
anonywiz datei.pdf -t terms.txt --check --json bericht.json --show-values

# Nur bestimmte Seiten behalten
anonywiz datei.pdf -t terms.txt --pages 1-4,9

# Bereich von Hand schwärzen (Prozent der Seite: x0,y0,x1,y1)
anonywiz datei.pdf -t terms.txt --region "1:60,5,95,12"      # Seite 1
anonywiz datei.pdf -t terms.txt --region "*:0,0,100,8"       # Kopfzeile auf allen Seiten

# Positionen von Bildern, Barcodes und unlesbarem Text anzeigen
anonywiz datei.pdf --inspect

# Barcode-Erkennung sichtbar machen (Debug-PNGs, nichts wird geschwärzt)
anonywiz datei.pdf --debug-barcodes

# Barcodes mit grösserem Rand schwärzen
anonywiz datei.pdf -t terms.txt --barcode-pad 10

# Eingescannte Seiten komplett schwärzen statt per OCR prüfen
anonywiz datei.pdf -t terms.txt --remove-scans
```

### Alle Optionen

| Option | Wirkung |
|---|---|
| `-t, --terms DATEI` | eigene Begriffe schwärzen |
| `-o, --out DATEI` | Name der Ausgabedatei (Standard: `<name>_anonywizm.pdf`) |
| `--pages 1,3-6` | nur diese Seiten behalten |
| `--region S:x0,y0,x1,y1` | Bereich von Hand schwärzen, mehrfach möglich |
| `--keep-dates` | Datumsangaben nicht schwärzen |
| `--keep-images` | Bilder und Barcodes nicht entfernen |
| `--remove-scans` | Scans komplett schwärzen statt OCR |
| `--redact-unreadable` | auch Text ohne Unicode-Zuordnung schwärzen |
| `--barcode-pad PT` | zusätzlicher Rand um Barcodes (Standard 4 pt) |
| `--no-preview` | keine Vorschaubilder erzeugen |
| `--check` | nur prüfen; Exit-Code 2 = HOCH, 1 = MITTEL, 0 = sauber |
| `--show-values` | Werte im Bericht nicht maskieren |
| `--json DATEI` | Prüfbericht zusätzlich als JSON |
| `--no-ner` | KI-Namenserkennung ausschalten |
| `--inspect` | Analyse ohne Schwärzung |
| `--debug-barcodes` | Barcode-Diagnose mit Debug-PNGs |
| `--version` | Version anzeigen |

---

## Fehlerbehebung

| Problem | Ursache und Lösung |
|---|---|
| `unrecognized arguments` | Alte Version. Mit `--version` prüfen und die neue Datei über `anonywiz.py` kopieren. |
| Ganze Seite schwarz | Die Seite ist ein Scan, Tesseract fehlt. Tesseract installieren, sonst `--remove-scans` verwenden. |
| Text in der Vorschau markierbar, Skript meldet «kein auslesbarer Text» | macOS Live Text erkennt Text in Bildern nur auf dem Bildschirm. Die Datei ist trotzdem ein Scan. |
| Barcode teilweise sichtbar | `--barcode-pad 10` versuchen, sonst `--debug-barcodes` und `--region`. |
| «Möglicher Barcode-Rest» beim Kantonswappen | Falscher Alarm (Wellenlinien im Logo). |
| `error: externally-managed-environment` bei pip | Die venv aus der Installation verwenden. |
| `Datei nicht gefunden` | Dateinamen mit Leerzeichen in Anführungszeichen setzen oder die Datei ins Terminal ziehen. |
| `ist kein PDF` | EasyTax-Datendatei statt PDF. In EasyTax als PDF drucken bzw. exportieren. |

---

## Grenzen

- Die Erkennung arbeitet heuristisch. Eine automatische Prüfung ersetzt den Blick auf die Vorschau nicht.
- OCR übersieht Handschrift, Stempel und schlecht gescannte Stellen.
- Schwärze lieber zu viel als zu wenig. Nicht benötigte Seiten entfernst du mit `--pages`.
- Noch sicherer ist es, nur die benötigten Zahlen abzutippen, statt ein PDF weiterzugeben.
