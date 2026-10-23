"""Generate the demo samples shipped with the app (all fictional products)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.labelgen import (COOKIES, DEMO_ERRORS, PEANUTS, approved_record, build_label, render_png,  # noqa: E402
                            save_pdf)

SAMPLES = ROOT / "data" / "samples"


def write_xlsx(record: dict, path: Path) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Approved data"
    ws.append(["Field", "Value"])
    for k in ("sku", "product_name", "brand", "net_quantity", "mrp", "fssai_license", "gtin", "ingredients",
              "manufacturer", "customer_care"):
        ws.append([k.replace("_", " ").title(), record[k]])
    ws.append(["Veg / Non-veg", "Veg" if record["veg"] else "Non-veg"])
    ws.append(["Allergens", ", ".join(record["allergens"])])
    ws.append(["Serving size", record["serving_size_g"]])
    names = {"energy_kcal": "Energy (kcal)", "protein_g": "Protein (g)", "carbohydrate_g": "Carbohydrate (g)",
             "total_sugars_g": "Total Sugars (g)", "added_sugars_g": "Added Sugars (g)", "total_fat_g": "Total Fat (g)",
             "saturated_fat_g": "Saturated Fat (g)", "trans_fat_g": "Trans Fat (g)",
             "cholesterol_mg": "Cholesterol (mg)", "sodium_mg": "Sodium (mg)"}
    for k, v in record["nutrition_per_100g"].items():
        ws.append([names[k], v])
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 110
    wb.save(path)


GENERATED = ("cookies_errors", "cookies_clean", "peanuts", "cookies_photo")


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    manifest = SAMPLES / "manifest.json"
    kept = [m for m in (json.loads(manifest.read_text()) if manifest.exists() else []) if m["id"] not in GENERATED]
    for sid in GENERATED:  # Esko's own sample files are kept as they are
        shutil.rmtree(SAMPLES / sid, ignore_errors=True)
    cookies = approved_record(COOKIES)
    peanuts = approved_record(PEANUTS)
    items = []

    def add(sid, title, desc, doc_or_src, approved, kind="pdf", expected=None):
        d = SAMPLES / sid
        d.mkdir()
        (d / "approved.json").write_text(json.dumps(approved, indent=2))
        if kind == "pdf":
            save_pdf(doc_or_src, d / "artwork.pdf")
            art = "artwork.pdf"
        else:
            art = doc_or_src(d)
        items.append({"id": sid, "title": title, "description": desc, "artwork": art, "approved": "approved.json",
                      "rulebook": "sunfield_labelling_rulebook.md", "market": "India (FSSAI)",
                      "source": "Synthetic test artwork (fictional brand)", "expected_failures": expected or []})

    add("cookies_errors", "Cookies — artwork with errors",
        "Agency artwork v2 with five planted errors (quantity, allergen, claim, licence, energy).",
        build_label(COOKIES, **DEMO_ERRORS), cookies)
    add("cookies_clean", "Cookies — corrected artwork", "The same artwork after the fixes. Should be ready to print.",
        build_label(COOKIES), cookies)
    add("peanuts", "Peanuts — clean artwork with valid claims",
        "A second product whose 'Source of protein' and 'No added sugar' claims are genuinely supported.",
        build_label(PEANUTS), peanuts)

    def photo(d: Path) -> str:
        tmp = d / "_src.pdf"
        save_pdf(build_label(COOKIES, **DEMO_ERRORS), tmp)
        render_png(tmp, d / "artwork.jpg", dpi=200, photo=True)
        tmp.unlink()
        return "artwork.jpg"

    add("cookies_photo", "Cookies — phone photo of a printed proof",
        "Raster input: tilted, blurred, no physical scale. Shows how uncertainty is handled.", photo, cookies,
        kind="custom")
    write_xlsx(peanuts, SAMPLES / "peanuts" / "approved.xlsx")
    manifest.write_text(json.dumps(kept + items, indent=2, ensure_ascii=False))
    print(f"wrote {len(items)} samples to {SAMPLES} (kept {len(kept)} provided samples)")


if __name__ == "__main__":
    main()
