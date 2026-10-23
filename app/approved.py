"""Load the approved product information the artwork must match.

Accepts JSON, CSV, XLSX (field/value pairs or a header row + one data row) and
documents (DOCX/PDF/TXT/MD). Documents are read with the same fact extractors
used for artwork, plus 'Field: value' lines, so a product spec sheet works too.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Optional

from app.util import parse_quantity, to_float

SYNONYMS = {
    "product_name": ["product name", "name of the food", "name of food", "product", "name", "product title"],
    "statement_of_identity": ["statement of identity", "legal name / statement of identity", "legal name",
                              "legal denomination", "legal name/statement of identity"],
    "brand": ["brand", "brand name"],
    "net_quantity": ["net quantity", "net qty", "net weight", "net wt", "pack size", "net content", "quantity",
                     "package size / net weight", "package size/net weight", "package size"],
    "mrp": ["mrp", "m.r.p", "m.r.p.", "maximum retail price", "price", "retail price"],
    "fssai_license": ["fssai", "fssai licence", "fssai license", "licence number", "license number", "fssai lic no",
                      "lic no", "licence no", "license no"],
    "gtin": ["gtin", "ean", "barcode", "ean-13", "ean13", "upc", "upc / barcode", "upc/barcode"],
    "veg": ["veg", "vegetarian", "veg/non-veg", "veg / non-veg", "veg status", "food type", "veg or non veg"],
    "ingredients": ["ingredients", "ingredient list", "ingredient declaration"],
    "allergens": ["allergens", "allergen", "contains", "allergen declaration"],
    "allergen_information": ["allergen information", "allergen statement", "allergy information"],
    "serving_size_g": ["serving size", "serve size", "serving size (g)", "per serve"],
    "servings_per_container": ["number of servings per package", "servings per container", "servings per package",
                               "number of servings"],
    "manufacturer": ["manufacturer", "manufactured by", "manufacturer address"],
    "customer_care": ["customer care", "consumer care"],
    "sku": ["sku", "item code", "material code"],
    "storage": ["storage instructions", "storage", "storage instruction"],
    "dietary_information": ["dietary information", "dietary statement", "diet information"],
    "marketing_copy": ["marketing copy", "marketing claim copy", "tagline"],
    "claims": ["front/back panel claims & certifications", "claims & certifications", "claims", "approved claims",
               "claims and certifications"],
    "nf_footnote": ["nutrition panel bottom statement", "nutrition footnote", "%dv footnote"],
    "nutrition_format": ["nutrition panel format"],
    "market": ["country of market", "market", "country of sale"],
    "label_id": ["label identifier", "label id"],
    "status": ["status"],
    "product_type": ["product type"],
}
NUTRI = {
    "energy_kcal": ["energy", "energy (kcal)", "calories", "energy kcal"],
    "protein_g": ["protein", "protein (g)"],
    "carbohydrate_g": ["carbohydrate", "carbohydrate (g)", "carbohydrates", "total carbohydrate"],
    "total_sugars_g": ["total sugars", "total sugars (g)", "sugars"],
    "added_sugars_g": ["added sugars", "added sugars (g)", "incl. added sugars", "includes added sugars"],
    "total_fat_g": ["total fat", "total fat (g)", "fat"], "saturated_fat_g": ["saturated fat", "saturated fat (g)"],
    "trans_fat_g": ["trans fat", "trans fat (g)"], "cholesterol_mg": ["cholesterol", "cholesterol (mg)"],
    "sodium_mg": ["sodium", "sodium (mg)"], "dietary_fibre_g": ["dietary fiber", "dietary fibre", "fibre", "fiber"],
    "vitamin_d_mcg": ["vitamin d"], "calcium_mg": ["calcium"], "iron_mg": ["iron"], "potassium_mg": ["potassium"],
}
MISSING_VALUE = re.compile(r"(?i)^\s*(n/?a|none|tbc|tbd|-+|—|–|not\s+(clearly\s+)?(legible|available|applicable|"
                           r"known|provided).*)\s*$")
TEXT_FIELDS = {"ingredients": r"(?i)^\s*ingredients?\s*[:\-]\s*"}


def _norm_key(k: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_:*]+", " ", str(k))).strip().lower()


def _lookup(k: str) -> Optional[tuple[str, Optional[str]]]:
    nk = _norm_key(k)
    for field, names in SYNONYMS.items():
        if nk == field or nk == field.replace("_", " ") or nk in names:
            return field, None
    for nut, names in NUTRI.items():
        if nk == nut or nk == nut.replace("_", " ") or nk in names:
            return "nutrition_per_100g", nut
    return None


def _coerce(field: str, v: Any) -> Any:
    if v is None:
        return None
    if field == "veg":
        s = str(v).strip().lower()
        if s in ("true", "yes", "y", "veg", "vegetarian", "1", "green"):
            return True
        if s in ("false", "no", "n", "non-veg", "non veg", "nonveg", "non-vegetarian", "0", "brown"):
            return False
        return None
    if isinstance(v, str) and MISSING_VALUE.match(v):
        return None
    if field in ("mrp", "serving_size_g", "servings_per_container"):
        return to_float(str(v))
    if field == "allergens" and isinstance(v, str):
        return [a.strip().lower() for a in re.split(r"[,;/]| and ", v) if a.strip()]
    if field == "claims" and isinstance(v, str):
        return [a.strip() for a in re.split(r"[;\n]", v) if a.strip()]
    if field in ("fssai_license", "gtin"):
        return re.sub(r"\D", "", str(v))
    if field in TEXT_FIELDS and isinstance(v, str):
        v = re.sub(TEXT_FIELDS[field], "", v)
    return re.sub(r"\s+", " ", str(v)).strip() if not isinstance(v, (list, dict)) else v


def normalise(raw: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k == "nutrition_per_100g" and isinstance(v, dict):
            out.setdefault("nutrition_per_100g", {}).update({kk: to_float(str(vv)) for kk, vv in v.items()})
            continue
        hit = _lookup(k)
        if not hit:
            out.setdefault("_other", {})[k] = v
            continue
        field, nut = hit
        if nut:
            out.setdefault("nutrition_per_100g", {})[nut] = to_float(str(v))
        else:
            out[field] = _coerce(field, v)
    return out


NUTRI_SECTION = re.compile(r"(?i)^(nutrient\s*composition|nutrition\s*values?|nutrition\s*facts\s*values|"
                           r"nutritional?\s*(values|information)\s*\(per)")


def _scan_rows(rows: list[list]) -> Optional[dict]:
    """Read a brand 'content sheet': label/value pairs anywhere in a row, reviewer notes in the next
    column, and a nutrition block. Returns None if it does not look like one."""
    rows = [[("" if c is None else str(c).strip()) for c in r] for r in rows]
    raw: dict[str, Any] = {}
    notes: dict[str, str] = {}
    nutrition: dict[str, float] = {}
    loose_nutrition: dict[str, float] = {}
    dv: dict[str, float] = {}
    basis = "per_serving"
    nutri_mode = False
    for row in rows:
        nonempty = [(j, c) for j, c in enumerate(row) if c]
        if not nonempty:
            nutri_mode = False
            continue
        first = nonempty[0][1]
        if NUTRI_SECTION.search(first):
            joined = " ".join(c for _, c in nonempty)
            if re.search(r"(?i)per\s*100\s*(g|ml)", joined):
                basis = "per_100g"
            elif re.search(r"(?i)per\s*serv", joined):
                basis = "per_serving"
            nutri_mode = True
            continue
        hit = _lookup(first)
        if nutri_mode and hit and hit[1]:
            vals = [c for _, c in nonempty[1:]]
            if vals and not MISSING_VALUE.match(vals[0]):
                v = to_float(vals[0])
                if v is not None:
                    nutrition[hit[1]] = v
            if len(vals) > 1 and "%" in vals[1]:
                d = to_float(vals[1])
                if d is not None:
                    dv[hit[1]] = d
            continue
        nutri_mode = False
        if hit and hit[1] and len(nonempty) >= 2 and nonempty[1][0] == nonempty[0][0] + 1:
            v = to_float(nonempty[1][1])  # simple two-column sheet: 'Energy (kcal) | 609'
            if v is not None:
                loose_nutrition[hit[1]] = v
            continue
        k = 0
        while k < len(nonempty):
            j, c = nonempty[k]
            h = _lookup(c)
            if h and not h[1] and k + 1 < len(nonempty) and (nonempty[k + 1][0] == j + 1 or
                                                             _lookup(nonempty[k + 1][1]) is None):
                vj, vc = nonempty[k + 1]
                if h[0] not in raw:
                    raw[h[0]] = vc
                k += 2
                if k < len(nonempty):
                    nj, nc = nonempty[k]
                    if nj == vj + 1 and _lookup(nc) is None and len(nc) > 20:
                        notes.setdefault(h[0], nc)
                        k += 1
                continue
            k += 1
    if len(raw) < 2:
        return None
    out = normalise(raw)
    for f, v in raw.items():
        if isinstance(v, str) and MISSING_VALUE.match(v):
            notes.setdefault(f, v)
    if not nutrition and loose_nutrition:
        nutrition, basis = loose_nutrition, "per_100g"
    if nutrition:
        out["nutrition_per_serving" if basis == "per_serving" else "nutrition_per_100g"] = nutrition
    if dv:
        out["dv_per_serving"] = dv
    if notes:
        out["_notes"] = notes
    return out


def _rows_to_dict(rows: list[list[str]]) -> dict:
    rows = [[str(c).strip() if c is not None else "" for c in r] for r in rows if any(str(c or "").strip() for c in r)]
    if not rows:
        return {}
    if all(len([c for c in r if c]) <= 2 for r in rows) and len(rows) > 2:
        return {r[0]: (r[1] if len(r) > 1 else "") for r in rows if r and r[0]}
    header, first = rows[0], rows[1] if len(rows) > 1 else []
    return {h: (first[i] if i < len(first) else "") for i, h in enumerate(header) if h}


def _from_document(path: Path) -> dict:
    from app.rules.rulebook import document_pages
    text = "\n".join(t for _, t in document_pages(path))
    raw: dict[str, Any] = {}
    nutrition: dict[str, float] = {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z][A-Za-z ./()&-]{1,40}?)\s*[:|=\t]\s*(.+)$", line)
        if m:
            hit = _lookup(m.group(1))
            if hit:
                field, nut = hit
                if nut:
                    v = to_float(m.group(2))
                    if v is not None:
                        nutrition[nut] = v
                elif field not in raw:
                    raw[field] = m.group(2).strip()
    out = normalise(raw)
    if nutrition:
        out.setdefault("nutrition_per_100g", {}).update(nutrition)
    if "ingredients" not in out:
        m = re.search(r"(?is)ingredients?\s*[:\-]\s*(.+?)(?:\n\s*\n|allergen|contains:|nutrition|$)", text)
        if m:
            out["ingredients"] = re.sub(r"\s+", " ", m.group(1)).strip().rstrip(".")
    return out


def load_approved(path: Path) -> tuple[dict, str]:
    ext = path.suffix.lower()
    if ext == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return normalise(data if isinstance(data, dict) else {}), "JSON"
    if ext == ".csv":
        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            rows = list(csv.reader(fh))
        scanned = _scan_rows(rows)
        return (scanned, "CSV content sheet") if scanned else (normalise(_rows_to_dict(rows)), "CSV")
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        scanned = _scan_rows(rows)
        if scanned and len([k for k in scanned if not k.startswith("_")]) >= 4:
            return scanned, "content sheet (spreadsheet)"
        return normalise(_rows_to_dict(rows)), "spreadsheet"
    return _from_document(path), "document"


# ---------------------------------------------------------------- sanity checks on the approved data itself

def approved_warnings(appr: Optional[dict]) -> list[dict]:
    """The source of truth can be wrong too: flag internal problems in the approved data."""
    if not appr:
        return []
    out: list[dict] = []
    ing = appr.get("ingredients")
    if isinstance(ing, str) and ing:
        from app.util import split_top_level
        items = [re.sub(r"\s*\(.*?\)", "", i).strip().lower() for i in split_top_level(ing.rstrip("."))]
        dups = sorted({i for i in items if i and items.count(i) > 1})
        if dups:
            out.append({"field": "ingredients", "message": f"The approved ingredient list names "
                        f"{', '.join(repr(d.title()) for d in dups)} more than once."})
    for basis in ("nutrition_per_serving", "nutrition_per_100g"):
        n = appr.get(basis) or {}
        label = "per serving" if basis.endswith("serving") else "per 100 g"
        if n.get("total_sugars_g") is not None and n.get("carbohydrate_g") is not None and \
                n["total_sugars_g"] > n["carbohydrate_g"] + 0.05:
            out.append({"field": basis, "message": f"Approved nutrition ({label}) is impossible: total sugars "
                        f"{n['total_sugars_g']:g} g exceed total carbohydrate {n['carbohydrate_g']:g} g."})
        if n.get("added_sugars_g") is not None and n.get("total_sugars_g") is not None and \
                n["added_sugars_g"] > n["total_sugars_g"] + 0.05:
            out.append({"field": basis, "message": f"Approved added sugars exceed total sugars ({label})."})
        if all(n.get(k) is not None for k in ("energy_kcal", "protein_g", "carbohydrate_g", "total_fat_g")):
            calc = 4 * n["protein_g"] + 4 * n["carbohydrate_g"] + 9 * n["total_fat_g"]
            if calc and abs(n["energy_kcal"] - calc) / calc > 0.2:
                out.append({"field": basis, "message": f"Approved energy {n['energy_kcal']:g} kcal ({label}) does not "
                            f"match its own macronutrients (4/4/9 → {calc:.0f} kcal)."})
    for f, note in (appr.get("_notes") or {}).items():
        if re.search(r"(?i)confirm|verify|assum|not\s+clearly|legible|reconstructed|draft|n/?a\b", note):
            out.append({"field": f, "message": f"Content-sheet note on '{f.replace('_', ' ')}': {note}"})
    return out


def describe(approved: Optional[dict]) -> list[dict]:
    if not approved:
        return []
    rows = []
    for k, v in approved.items():
        if k.startswith("_"):
            continue
        rows.append({"field": k, "value": v})
    return rows


def quantity_of(approved: dict) -> Optional[dict]:
    q = approved.get("net_quantity")
    return parse_quantity(str(q)) if q else None
