"""Synthetic packaging artwork generator (flat flow-wrap layout: back panel | front panel).

Used for (a) demo samples and (b) the evaluation set: every artwork is rendered
from product data plus optional "defect" overrides, so ground truth is known.
All brands, addresses, licence numbers and barcodes are fictional.
"""
from __future__ import annotations

import copy
from typing import Any, Optional

import pymupdf

MM = 72.0 / 25.4
W_MM, H_MM, SPLIT_MM = 310.0, 130.0, 155.0

INK = (0.13, 0.13, 0.15)
GREY = (0.55, 0.55, 0.58)
DIELINE = (0.93, 0.1, 0.75)
VEG_GREEN = (0.106, 0.56, 0.23)
NONVEG_BROWN = (0.48, 0.24, 0.04)


def mm(v: float) -> float:
    return v * MM


def R(x0, y0, x1, y1) -> pymupdf.Rect:
    return pymupdf.Rect(mm(x0), mm(y0), mm(x1), mm(y1))


# ---------------------------------------------------------------- EAN-13

_L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
_G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
_R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLG", "LGLGLG", "LGLGGL", "LGGLGL"]


def gtin_with_check(body12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return body12 + str((10 - total % 10) % 10)


def ean13_modules(code: str) -> str:
    first, left, right = int(code[0]), code[1:7], code[7:13]
    bits = "101"
    for d, p in zip(left, _PARITY[first]):
        bits += (_L if p == "L" else _G)[int(d)]
    bits += "01010"
    for d in right:
        bits += _R[int(d)]
    return bits + "101"


def draw_ean13(page: pymupdf.Page, code: str, x_mm: float, y_mm: float, module_mm: float = 0.33,
               bar_h_mm: float = 20.0) -> None:
    bits = ean13_modules(code)
    guard_idx = set(range(0, 3)) | set(range(45, 50)) | set(range(92, 95))
    page.draw_rect(R(x_mm - 4.5, y_mm - 1.5, x_mm + 95 * module_mm + 3.0, y_mm + bar_h_mm + 5.5),
                   color=None, fill=(1, 1, 1))
    i = 0
    while i < len(bits):
        if bits[i] == "1":
            j = i
            while j < len(bits) and bits[j] == "1":
                j += 1
            ext = 1.6 if i in guard_idx else 0.0
            page.draw_rect(R(x_mm + i * module_mm, y_mm, x_mm + j * module_mm, y_mm + bar_h_mm + ext),
                           color=None, fill=(0, 0, 0), width=0)
            i = j
        else:
            i += 1
    fs = 8.0
    ty = mm(y_mm + bar_h_mm + 4.4)
    page.insert_text((mm(x_mm - 3.4), ty), code[0], fontsize=fs, fontname="helv", color=(0, 0, 0))
    page.insert_text((mm(x_mm + 4 * module_mm), ty), code[1:7], fontsize=fs, fontname="helv", color=(0, 0, 0),
                     render_mode=0)
    page.insert_text((mm(x_mm + 50 * module_mm), ty), code[7:13], fontsize=fs, fontname="helv", color=(0, 0, 0))


# ---------------------------------------------------------------- simple QR-like placeholder is not drawn:
# (the "upcoming" company rule asks for a QR code; base artworks deliberately have none)


def text_w(text: str, size: float, font: str = "helv") -> float:
    return pymupdf.get_text_length(text, fontname=font, fontsize=size)


def put(page, x_mm, y_mm, text, size=7.0, bold=False, color=INK, align="left"):
    font = "hebo" if bold else "helv"
    x = mm(x_mm)
    if align == "right":
        x -= text_w(text, size, font)
    elif align == "center":
        x -= text_w(text, size, font) / 2
    page.insert_text((x, mm(y_mm)), text, fontsize=size, fontname=font, color=color)


def para(page, rect_mm, text, size=7.0, bold=False, color=INK) -> float:
    """Wrapped paragraph; returns the bottom y (mm) actually used."""
    font = "hebo" if bold else "helv"
    rect = R(*rect_mm)
    rc = page.insert_textbox(rect, text, fontsize=size, fontname=font, color=color, lineheight=1.18)
    if rc < 0:
        raise ValueError(f"Text overflow ({rc:.1f}pt) for: {text[:40]}")
    return rect_mm[3] - rc / MM


def veg_mark(page, x_mm, y_mm, size_mm=8.0, nonveg=False):
    col = NONVEG_BROWN if nonveg else VEG_GREEN
    page.draw_rect(R(x_mm, y_mm, x_mm + size_mm, y_mm + size_mm), color=col, fill=(1, 1, 1), width=mm(0.75))
    cx, cy = x_mm + size_mm / 2, y_mm + size_mm / 2
    if nonveg:
        s = size_mm * 0.30
        pts = [pymupdf.Point(mm(cx), mm(cy - s)), pymupdf.Point(mm(cx + s * 1.1), mm(cy + s * 0.8)),
               pymupdf.Point(mm(cx - s * 1.1), mm(cy + s * 0.8))]
        page.draw_polyline(pts, color=col, fill=col, closePath=True, width=0.5)
    else:
        page.draw_circle(pymupdf.Point(mm(cx), mm(cy)), mm(size_mm * 0.27), color=col, fill=col, width=0.5)


def badge(page, x_mm, y_mm, text, fill=(0.96, 0.62, 0.04), size=8.5):
    w = text_w(text, size, "hebo") / MM + 6
    page.draw_rect(R(x_mm, y_mm, x_mm + w, y_mm + 6.5), color=None, fill=fill, radius=0.35)
    put(page, x_mm + 3, y_mm + 4.6, text, size=size, bold=True, color=(1, 1, 1))
    return x_mm + w


NUTRIENT_ROWS = [
    ("energy_kcal", "Energy (kcal)", 0), ("protein_g", "Protein (g)", 0), ("carbohydrate_g", "Carbohydrate (g)", 0),
    ("total_sugars_g", "Total Sugars (g)", 1), ("added_sugars_g", "Added Sugars (g)", 1),
    ("total_fat_g", "Total Fat (g)", 0), ("saturated_fat_g", "Saturated Fat (g)", 1),
    ("trans_fat_g", "Trans Fat (g)", 1), ("cholesterol_mg", "Cholesterol (mg)", 0), ("sodium_mg", "Sodium (mg)", 0),
]
RDA_REF = {"energy_kcal": 2000, "total_fat_g": 67, "saturated_fat_g": 22, "trans_fat_g": 2, "added_sugars_g": 50,
           "sodium_mg": 2000}


def fmt_val(v: float, key: str) -> str:
    if key == "energy_kcal" or key.endswith("_mg"):
        return f"{v:.0f}"
    if abs(v) < 0.1 and v != 0:
        return f"{v:.2f}"
    return f"{v:.1f}"


def nutrition_cells(product: dict, overrides: dict[str, dict]) -> list[tuple[str, str, str, str]]:
    ss = product["serving_size_g"]
    rows = []
    for key, label, _indent in NUTRIENT_ROWS:
        p100 = product["nutrition_per_100g"][key]
        serve = p100 * ss / 100.0
        rda = f"{round(serve / RDA_REF[key] * 100):.0f}%" if key in RDA_REF else "-"
        cell = {"p100": fmt_val(p100, key), "serve": fmt_val(serve, key), "rda": rda}
        cell.update(overrides.get(key, {}))
        rows.append((label, cell["p100"], cell["serve"], cell["rda"]))
    return rows


def build_label(product: dict, **o: Any) -> pymupdf.Document:
    """Render a product label. Keyword overrides introduce controlled defects."""
    p = copy.deepcopy(product)
    doc = pymupdf.open()
    page = doc.new_page(width=mm(W_MM), height=mm(H_MM))
    theme = p.get("theme", (0.11, 0.31, 0.54))

    # ---- front panel
    page.draw_rect(R(SPLIT_MM, 0, W_MM, H_MM), color=None, fill=(1.0, 0.975, 0.92))
    page.draw_rect(R(SPLIT_MM, 0, W_MM, 17), color=None, fill=theme)
    put(page, SPLIT_MM + 6, 11.6, p["brand"].upper(), size=19, bold=True, color=(1, 1, 1))
    put(page, W_MM - 6, 11.0, "Since 1987", size=8, color=(0.85, 0.9, 1.0), align="right")
    mark = o.get("veg_mark", "veg" if p["veg"] else "nonveg")
    if mark:
        veg_mark(page, W_MM - 15, 21, nonveg=(mark == "nonveg"))
    put(page, SPLIT_MM + 6, 34, o.get("name_text", p["product_name"]), size=25, bold=True, color=INK)
    put(page, SPLIT_MM + 6, 41.5, p["tagline"], size=10.5, color=theme)
    x = SPLIT_MM + 6
    for b in p.get("front_badges", []) + o.get("extra_badges", []):
        x = badge(page, x, 46, b) + 3
    for cx, cy, r in p.get("illustration", []):
        page.draw_circle(pymupdf.Point(mm(cx), mm(cy)), mm(r), color=None, fill=p.get("illus_fill", (0.88, 0.64, 0.35)))
        for dx, dy in ((-0.35, -0.2), (0.25, -0.35), (0.1, 0.25), (-0.15, 0.45), (0.45, 0.15)):
            page.draw_circle(pymupdf.Point(mm(cx + dx * r), mm(cy + dy * r)), mm(r * 0.08), color=None,
                             fill=p.get("dot_fill", (0.42, 0.27, 0.14)))
    fq = o.get("front_qty_text", f"Net Wt. {p['net_quantity']}")
    if fq:
        put(page, SPLIT_MM + 6, 123, fq, size=o.get("front_qty_size", 14), bold=True, color=INK)
    put(page, W_MM - 6, 123, "Serve with tea or milk", size=7, color=GREY, align="right")

    # ---- back panel: left column
    xl, wl = 6.0, 76.0
    y = 6.0
    ing = o.get("ingredients_text", p["ingredients"])
    y = para(page, (xl, y, xl + wl, y + 30), f"Ingredients: {ing}.", size=7.2) + 1.2
    alg = o.get("allergen_text", p["allergen_statement"])
    if alg:
        y = para(page, (xl, y, xl + wl, y + 10), alg, size=7.2, bold=o.get("allergen_bold", True)) + 0.8
    if p.get("precaution"):
        y = para(page, (xl, y, xl + wl, y + 8), p["precaution"], size=6.8) + 1.6
    y = para(page, (xl, y, xl + wl, y + 8), "Storage: Store in a cool, dry and hygienic place. "
             "Once opened, transfer to an airtight container.", size=6.8) + 1.2
    bb = o.get("best_before_text", p["best_before"])
    if bb:
        y = para(page, (xl, y, xl + wl, y + 6), bb, size=6.8) + 0.6
    coding = o.get("coding_text", "Batch No., Date of Mfg. and Use By: see crimp.")
    if coding:
        y = para(page, (xl, y, xl + wl, y + 6), coding, size=6.8) + 1.6
    mfr = o.get("manufacturer_text", f"Manufactured & Marketed by: {p['manufacturer']}")
    if mfr:
        y = para(page, (xl, y, xl + wl, y + 14), mfr, size=6.8) + 1.2
    cc = o.get("customer_care_text", f"Customer Care: {p['customer_care']}")
    if cc:
        y = para(page, (xl, y, xl + wl, y + 8), cc, size=6.8) + 1.2
    page.draw_rect(R(xl, 108, xl + 44, 122), color=GREY, width=0.6, dashes="[3 2] 0")
    put(page, xl + 22, 116, "CODING AREA", size=6, color=GREY, align="center")

    # ---- back panel: right column — nutrition table
    x0, x1 = 86.0, 149.0
    c100, cserve, crda = 115.5, 134.5, 148.0
    ty = 6.0
    put(page, x0 + 1.5, ty + 4.2, "NUTRITIONAL INFORMATION", size=7.4, bold=True)
    hy = ty + 8.6
    put(page, c100, hy, "Per 100 g", size=6.2, bold=True, align="right")
    put(page, cserve, hy, f"Per serve ({p['serving_size_g']} g)", size=6.2, bold=True, align="right")
    put(page, crda, hy, "%RDA*", size=6.2, bold=True, align="right")
    page.draw_line(pymupdf.Point(mm(x0), mm(hy + 1.4)), pymupdf.Point(mm(x1), mm(hy + 1.4)), color=INK, width=0.6)
    ry = hy + 5.0
    cells = nutrition_cells(p, o.get("nutrition", {}))
    for (label, v100, vserve, vrda), (_k, _l, indent) in zip(cells, NUTRIENT_ROWS):
        put(page, x0 + 1.5 + 2.5 * indent, ry, label, size=6.4)
        put(page, c100, ry, v100, size=6.4, align="right")
        put(page, cserve, ry, vserve, size=6.4, align="right")
        put(page, crda, ry, vrda, size=6.4, align="right")
        page.draw_line(pymupdf.Point(mm(x0), mm(ry + 1.2)), pymupdf.Point(mm(x1), mm(ry + 1.2)),
                       color=(0.75, 0.75, 0.78), width=0.3)
        ry += 3.55
    foot = para(page, (x0 + 1.5, ry - 1.0, x1 - 1, ry + 5.0),
                "*%RDA per serve is based on 2000 kcal energy requirement of an average adult per day.", size=5.2)
    page.draw_rect(R(x0, ty, x1, foot + 1.0), color=INK, width=0.7)

    # FSSAI block (logo placeholder + licence)
    ly = 60.0
    page.draw_rect(R(x0, ly, x0 + 15, ly + 7), color=(0.1, 0.45, 0.5), width=0.8, radius=0.2)
    put(page, x0 + 7.5, ly + 4.9, "fssai", size=9, bold=True, color=(0.1, 0.45, 0.5), align="center")
    put(page, x0 + 18, ly + 4.9, o.get("license_text", f"Lic. No. {p['fssai_license']}"), size=7.4)
    mrp = o.get("mrp_text", f"M.R.P. Rs. {p['mrp']:.2f} (Incl. of all taxes)")
    if mrp:
        put(page, x0, ly + 13, mrp, size=7.6, bold=True)
    bq = o.get("back_qty_text", f"Net Wt.: {p['net_quantity']}")
    if bq:
        put(page, x0, ly + 18, bq, size=7.6, bold=True)
    gtin = o.get("gtin", p["gtin"])
    if gtin:
        draw_ean13(page, gtin, x0 + 26, 86)

    # ---- dieline (cut/fold lines, as on a real artwork file)
    page.draw_rect(R(0.4, 0.4, W_MM - 0.4, H_MM - 0.4), color=DIELINE, width=0.6)
    page.draw_line(pymupdf.Point(mm(SPLIT_MM), mm(0.4)), pymupdf.Point(mm(SPLIT_MM), mm(H_MM - 0.4)), color=DIELINE,
                   width=0.6)
    return doc


# ---------------------------------------------------------------- products (fictional)

COOKIES: dict[str, Any] = {
    "sku": "SF-COOK-OAT-200",
    "brand": "Sunfield",
    "product_name": "Crunchy Oat Cookies",
    "tagline": "Baked with whole wheat & rolled oats",
    "net_quantity": "200 g",
    "mrp": 60.0,
    "fssai_license": "10024011000123",
    "gtin": gtin_with_check("200123456789"),
    "veg": True,
    "ingredients": ("Whole Wheat Flour (Atta) (38%), Sugar, Edible Vegetable Oil (Palm), Rolled Oats (12%), "
                    "Invert Syrup, Milk Solids, Raising Agents [INS 500(ii), INS 503(ii)], Iodised Salt, "
                    "Emulsifier [INS 322(i) (Soy Lecithin)], Artificial Flavouring Substances (Vanilla)"),
    "allergen_statement": "Contains: Gluten (Wheat, Oats), Milk and Soy.",
    "allergens": ["gluten", "milk", "soy"],
    "precaution": "May contain traces of nuts.",
    "best_before": "Best before 6 months from manufacture.",
    "serving_size_g": 30,
    "nutrition_per_100g": {"energy_kcal": 483, "protein_g": 7.4, "carbohydrate_g": 66.0, "total_sugars_g": 24.0,
                           "added_sugars_g": 21.5, "total_fat_g": 21.0, "saturated_fat_g": 10.2, "trans_fat_g": 0.1,
                           "cholesterol_mg": 0, "sodium_mg": 310},
    "front_badges": ["BAKED, NOT FRIED"],
    "manufacturer": ("Sunfield Foods Pvt. Ltd., Plot 42, KIADB Industrial Area, Baikampady, Mangaluru 575011, "
                     "Karnataka, India."),
    "customer_care": "1800-123-4567 | care@sunfieldfoods.example",
    "theme": (0.11, 0.31, 0.54),
    "illustration": [(212, 88, 15), (246, 98, 13), (281, 84, 14.5)],
}

PEANUTS: dict[str, Any] = {
    "sku": "SF-PNUT-RS-150",
    "brand": "Sunfield",
    "product_name": "Roasted Salted Peanuts",
    "tagline": "Slow-roasted, lightly salted",
    "net_quantity": "150 g",
    "mrp": 45.0,
    "fssai_license": "10024011000123",
    "gtin": gtin_with_check("200123456796"),
    "veg": True,
    "ingredients": "Peanuts (93%), Edible Vegetable Oil (Groundnut), Iodised Salt",
    "allergen_statement": "Contains: Peanuts.",
    "allergens": ["peanut"],
    "precaution": "Processed in a facility that also handles tree nuts and sesame.",
    "best_before": "Best before 9 months from packing.",
    "serving_size_g": 25,
    "nutrition_per_100g": {"energy_kcal": 609, "protein_g": 26.0, "carbohydrate_g": 16.0, "total_sugars_g": 4.2,
                           "added_sugars_g": 0.0, "total_fat_g": 49.0, "saturated_fat_g": 7.5, "trans_fat_g": 0.0,
                           "cholesterol_mg": 0, "sodium_mg": 420},
    "front_badges": ["SOURCE OF PROTEIN", "NO ADDED SUGAR"],
    "manufacturer": ("Sunfield Foods Pvt. Ltd., Plot 42, KIADB Industrial Area, Baikampady, Mangaluru 575011, "
                     "Karnataka, India."),
    "customer_care": "1800-123-4567 | care@sunfieldfoods.example",
    "theme": (0.23, 0.25, 0.56),
    "illustration": [(215, 92, 9), (236, 99, 8), (258, 90, 9.5), (279, 100, 8.5)],
    "illus_fill": (0.86, 0.66, 0.42),
    "dot_fill": (0.75, 0.55, 0.32),
}


def approved_record(p: dict) -> dict:
    """The 'approved product information' a brand team would hold (what the artwork must match)."""
    return {
        "sku": p["sku"],
        "product_name": p["product_name"],
        "brand": p["brand"],
        "net_quantity": p["net_quantity"],
        "mrp": p["mrp"],
        "fssai_license": p["fssai_license"],
        "gtin": p["gtin"],
        "veg": p["veg"],
        "ingredients": p["ingredients"],
        "allergens": p["allergens"],
        "serving_size_g": p["serving_size_g"],
        "nutrition_per_100g": p["nutrition_per_100g"],
        "approved_claims": [b.title() for b in p.get("front_badges", [])],
        "manufacturer": p["manufacturer"],
        "customer_care": p["customer_care"],
    }


# Defects used for the evaluation set: name -> (overrides, rules expected to be flagged, description)
def defects(p: dict) -> dict[str, tuple[dict, list[str], str]]:
    n = p["nutrition_per_100g"]
    ss = p["serving_size_g"]
    return {
        "D01_name_typo": ({"name_text": "Crunchy Oat Cookeis"}, ["R01"], "Product name misspelt on front"),
        "D02_front_qty_mismatch": ({"front_qty_text": "Net Wt. 250 g"}, ["R03", "R04"],
                                   "Front net quantity 250 g vs approved 200 g"),
        "D03_front_qty_missing": ({"front_qty_text": None}, ["R02"], "Net quantity missing from front panel"),
        "D04_front_qty_too_small": ({"front_qty_size": 5.0}, ["R05"], "Net quantity numerals too small"),
        "D05_mrp_no_tax_text": ({"mrp_text": f"M.R.P. Rs. {p['mrp']:.2f}"}, ["R06"],
                                "MRP without 'inclusive of all taxes'"),
        "D06_mrp_mismatch": ({"mrp_text": "M.R.P. Rs. 65.00 (Incl. of all taxes)"}, ["R07"], "MRP 65 vs approved 60"),
        "D07_licence_13_digits": ({"license_text": "Lic. No. 1002401100123"}, ["R08", "R09"],
                                  "FSSAI licence has 13 digits"),
        "D08_licence_wrong_digit": ({"license_text": "Lic. No. 10024011000128"}, ["R09"],
                                    "FSSAI licence differs from approved"),
        "D09_veg_mark_missing": ({"veg_mark": None}, ["R10"], "Veg mark missing"),
        "D10_nonveg_mark_on_veg": ({"veg_mark": "nonveg"}, ["R10"], "Non-veg mark on a veg product"),
        "D11_ingredient_pct_changed": (
            {"ingredients_text": p["ingredients"].replace("Rolled Oats (12%)", "Rolled Oats (21%)")}, ["R11"],
            "Ingredient percentage changed"),
        "D12_ingredient_missing": ({"ingredients_text": p["ingredients"].replace("Milk Solids, ", "")}, ["R11"],
                                   "Ingredient missing from list"),
        "D13_allergen_soy_missing": ({"allergen_text": "Contains: Gluten (Wheat, Oats) and Milk."}, ["R12"],
                                     "Soy missing from allergen declaration"),
        "D14_allergen_statement_missing": ({"allergen_text": None}, ["R12"], "Allergen declaration missing"),
        "D15_allergen_not_bold": ({"allergen_bold": False}, ["R13"], "Allergen declaration not bold"),
        "D16_energy_typo": ({"nutrition": {"energy_kcal": {"p100": "438"}}}, ["R14", "R16", "R17"],
                            "Energy per 100 g typed as 438"),
        "D17_sugar_exceeds_carbs": (
            {"nutrition": {"total_sugars_g": {"p100": "76.0", "serve": f"{76.0 * ss / 100:.1f}"}}},
            ["R15", "R17"], "Total sugars greater than carbohydrate"),
        "D18_per_serve_wrong": ({"nutrition": {"total_fat_g": {"serve": "9.3"}}}, ["R16", "R18"],
                                "Per-serve fat inconsistent"),
        "D19_false_low_fat_claim": ({"extra_badges": ["LOW FAT"]}, ["R19"], "'Low fat' claim with 21 g fat"),
        "D20_false_high_protein_claim": ({"extra_badges": ["HIGH PROTEIN"]}, ["R19"],
                                         "'High protein' claim not supported"),
        "D21_prohibited_term": ({"extra_badges": ["100% NATURAL"]}, ["R25"], "Prohibited marketing term"),
        "D22_date_coding_missing": ({"coding_text": None}, ["R20", "R21"],
                                    "Batch / date-of-manufacture / use-by line missing"),
        "D23_manufacturer_missing": ({"manufacturer_text": None}, ["R22"], "Manufacturer name/address missing"),
        "D24_customer_care_missing": ({"customer_care_text": None}, ["R23"], "Consumer care details missing"),
        "D25_barcode_wrong_gtin": ({"gtin": gtin_with_check("200987654321")}, ["R24"],
                                   "Barcode encodes another product's GTIN"),
        "D26_rda_wrong": ({"nutrition": {"sodium_mg": {"rda": "15%"}}}, ["R18"], "%RDA for sodium wrong"),
    }


DEMO_ERRORS: dict[str, Any] = {
    "front_qty_text": "Net Wt. 250 g",
    "allergen_text": "Contains: Gluten (Wheat, Oats) and Milk.",
    "extra_badges": ["LOW FAT"],
    "license_text": "Lic. No. 1002401100123",
    "nutrition": {"energy_kcal": {"p100": "438"}},
}
DEMO_EXPECTED = ["R03", "R04", "R08", "R09", "R12", "R14", "R16", "R17", "R19"]


def save_pdf(doc: pymupdf.Document, path) -> None:
    doc.save(str(path), garbage=3, deflate=True)


def render_png(pdf_path, png_path, dpi: int = 200, photo: bool = False, seed: int = 7) -> None:
    """Rasterise an artwork; optionally simulate a phone photo (tilt, blur, noise, JPEG, no DPI)."""
    import numpy as np
    from PIL import Image, ImageFilter

    doc = pymupdf.open(str(pdf_path))
    pix = doc[0].get_pixmap(dpi=dpi, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if not photo:
        img.save(str(png_path), dpi=(dpi, dpi))
        return
    rng = np.random.default_rng(seed)
    img = img.rotate(1.3, resample=Image.BICUBIC, expand=True, fillcolor=(236, 233, 228))
    img = img.filter(ImageFilter.GaussianBlur(radius=0.7))
    arr = np.asarray(img).astype(np.float32)
    arr = arr * 0.94 + 10 + rng.normal(0, 4.0, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    img.save(str(png_path), quality=82)
