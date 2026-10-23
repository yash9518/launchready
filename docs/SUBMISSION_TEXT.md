# Text for the HackerEarth submission fields

## Short explanation of the problem and your approach

**Problem (statement 2):** before printing, someone must confirm that the artwork carries every required
element in the right place, matches the approved content sheet, makes only true claims, and has a
nutrition panel that adds up. Done by hand across a PDF, a spreadsheet and a rulebook, this is slow, and
the costly errors are cross-document. A checker that raises false alarms gets ignored.

**Approach: LaunchReady, "AI reads, code checks, people decide".** It reads the print PDF's text layer
exactly: words, fonts, sizes, positions, dieline panels, barcodes, and text hidden under artwork. Photos go
through neural OCR. The text becomes facts that remember where they came from. The rulebook becomes rule
cards: a verbatim quote, a source and one of 24 deterministic check types. Free-text regulations are
compiled by an LLM, and every quote must exist in the source. Code runs every check. Anything that looks
"missing" gets a second look first. A strict policy decides the verdict: **FAIL** only when code proves a
violation on a confident reading; **REVIEW** for uncertain readings, judgment rules or AI-only flags;
**PASS** with its evidence. Rules can carry effective dates. Every finding draws a box on the artwork,
quotes its rule, and says why it matters. Users can ask follow-up questions, switch rules off, change the
check date, and export a report.

**On Esko's sample carton** it reports 18 definite problems in about 10 seconds. Examples: "Suitable for
Vegans" with honey in the ingredients; "No added sugar" with honey and coconut sugar; tree nuts without a
"Contains" statement; 166 kcal declared vs 535 kcal computed; values that differ from the content sheet;
the net weight missing from the front panel. It also sends 4 items to review and passes 7 rules with
evidence.

**Evaluation:** across 84 artworks with known errors (26 single-error variants and 2 clean ones, each as
a print PDF, PNG and phone photo), every error was flagged with zero false alarms.

## Brief description of the AI models and technologies used

- **PyMuPDF**: exact text, fonts and positions from the print PDF, plus a paint-order test that finds hidden text.
- **RapidOCR**: PaddleOCR PP-OCRv3 neural text detection and recognition on ONNX Runtime. Runs offline on
  CPU for photos, scans and outlined fonts, with a second reading of uncertain words.
- **OpenCV and zxing-cpp**: dieline and panel detection, symbol detection, glyph height in mm, barcode
  decoding with a GS1 check-digit verifier.
- **Optional LLM: Google Gemini** (Gemini API or Vertex AI express key; Claude or GPT also supported).
  It compiles free-text rulebooks, gives photos a second reading, judges subjective rules, confirms
  "missing" items and answers follow-up questions. Every AI output is verified before use: quotes must
  exist in the source, readings must cite OCR line IDs, AI can never produce a definite FAIL, and every AI
  action is logged in the UI.
- **RapidFuzz** for near-miss matching and diffs; **FastAPI** backend; plain-JavaScript UI; printable
  HTML report and JSON export; **pytest** (23 tests) and a planted-defect evaluation harness.
