# Sunfield Foods — Packaging Artwork Rulebook (India)

**Scope:** pre-packaged biscuits, snacks and nuts sold in India · **Version:** 2.1 (October 2026) · **Owner:** Regulatory Affairs

This rulebook is how Sunfield interprets the regulations for artwork review. Every rule has a
human-readable statement (what a reviewer reads) and a machine-readable block (what the checker runs).
The checker quotes the **Rule** sentence as evidence in every finding, so reviewers can always see
which requirement a finding comes from.

> Demo document for the Esko AIForge hackathon. Thresholds and interpretations are illustrative and
> must be verified against the official regulation text before real use.

Referenced sources: FSS (Labelling & Display) Regulations, 2020 (as amended, incl. the First Amendment
Regulations, 2026); Legal Metrology (Packaged Commodities) Rules, 2011; FSS (Advertising & Claims)
Regulations, 2018; GS1 General Specifications; FSSAI front-of-pack warning proposal (Aug–Sep 2026).

## R01 — Product name on the front panel
**Rule:** The name of the food shall appear on the principal display panel exactly as approved in the product master data.
**Basis:** FSS (Labelling & Display) Regulations 2020 — name of the food; Sunfield master-data policy.
**Why it matters:** A misspelt or wrong product name means the pack no longer matches its approvals and listings, and is a classic reprint cause.
```rule
check: matches_approved
severity: major
params: {fact: product_name, approved_field: product_name, mode: text, scope: front}
```

## R02 — Net quantity on the front panel
**Rule:** The net quantity shall be declared on the principal display panel.
**Basis:** Legal Metrology (Packaged Commodities) Rules 2011 — declarations on the principal display panel.
**Why it matters:** Net quantity is a legal metrology declaration; a pack without it on the front panel can be seized at inspection.
```rule
check: fact_present
severity: critical
params: {fact: net_quantity, scope: front}
```

## R03 — Net quantity matches the approved pack size
**Rule:** The declared net quantity shall match the approved pack size.
**Basis:** Legal Metrology (Packaged Commodities) Rules 2011; Sunfield master data.
**Why it matters:** Declaring more than the pack contains is short weight; declaring less loses money and breaks the price-per-unit calculation.
```rule
check: matches_approved
severity: critical
params: {fact: net_quantity, approved_field: net_quantity, mode: quantity}
```

## R04 — Net quantity consistent across panels
**Rule:** Where the net quantity appears more than once, every declaration shall state the same quantity.
**Basis:** Legal Metrology (Packaged Commodities) Rules 2011.
**Why it matters:** Two different quantities on one pack is misleading on its face, whichever one is right.
```rule
check: cross_consistency
severity: major
params: {fact: net_quantity}
```

## R05 — Net quantity numeral height
**Rule:** Numerals of the net quantity declaration on the principal display panel shall be at least the minimum height for the panel area: 1 mm up to 100 cm², 2 mm above 100 and up to 500 cm², 4 mm above 500 and up to 2,500 cm², and 6 mm above 2,500 cm².
**Basis:** Legal Metrology (Packaged Commodities) Rules 2011 — minimum height of numerals (normal case).
**Why it matters:** Undersized declarations are treated as not declared at all during inspection.
```rule
check: min_text_height
severity: major
params:
  fact: net_quantity
  scope: front
  area_table: [[100, 1.0], [500, 2.0], [2500, 4.0], [null, 6.0]]
```

## R06 — M.R.P. declared inclusive of all taxes
**Rule:** The retail sale price shall be declared as "M.R.P. Rs. … (inclusive of all taxes)".
**Basis:** Legal Metrology (Packaged Commodities) Rules 2011 — retail sale price.
**Why it matters:** An MRP without the tax statement is an incomplete declaration and a frequent inspection finding.
```rule
check: fact_present
severity: major
params: {fact: mrp, require: {inclusive_of_taxes: true}}
```

## R07 — M.R.P. matches the approved price
**Rule:** The declared M.R.P. shall match the approved price in the product master data.
**Basis:** Sunfield pricing policy.
**Why it matters:** A wrong printed MRP cannot be corrected on shelf; it forces relabelling or a write-off.
```rule
check: matches_approved
severity: major
params: {fact: mrp, approved_field: mrp, mode: number}
```

## R08 — FSSAI licence number present and well-formed
**Rule:** The FSSAI logo and the 14-digit FSSAI licence number shall be displayed on the label.
**Basis:** FSS (Labelling & Display) Regulations 2020 — FSSAI logo and licence number.
**Why it matters:** A missing or malformed licence number makes the product unsaleable and untraceable.
```rule
check: fact_format
severity: critical
params: {fact: fssai_license, regex: '^\d{14}$', description: "exactly 14 digits"}
```

## R09 — FSSAI licence matches the manufacturing unit
**Rule:** The licence number shall be that of the manufacturing unit recorded in the product master data.
**Basis:** FSS (Licensing & Registration) requirements; Sunfield master data.
**Why it matters:** A valid-looking but wrong licence points regulators to the wrong factory.
```rule
check: matches_approved
severity: critical
params: {fact: fssai_license, approved_field: fssai_license, mode: digits}
```

## R10 — Veg / non-veg symbol
**Rule:** Vegetarian food shall carry the green veg symbol (green dot in a green square) and non-vegetarian food the brown non-veg symbol (brown triangle in a brown square), on the principal display panel, in contrast against the background.
**Basis:** FSS (Labelling & Display) Regulations 2020 — declaration regarding veg or non-veg.
**Why it matters:** The wrong symbol misleads consumers on a matter of religious and dietary importance; it is treated as a serious violation.
```rule
check: symbol_present
severity: critical
params: {kind: auto, scope: front}
```

## R11 — Ingredient list matches the approved declaration
**Rule:** The list of ingredients shall reproduce the approved ingredient declaration, in descending order of weight, including percentage declarations and additive class names with INS numbers.
**Basis:** FSS (Labelling & Display) Regulations 2020 — list of ingredients.
**Why it matters:** Ingredient errors are the most common cause of artwork-driven recalls.
```rule
check: matches_approved
severity: critical
params: {fact: ingredients, approved_field: ingredients, mode: ingredients}
```

## R12 — Allergen declaration covers every allergenic ingredient
**Rule:** Every allergen present in the ingredients — cereals containing gluten, crustaceans, milk, egg, fish, peanut, tree nuts, soy and sulphites — shall be declared in a separate "Contains:" statement.
**Basis:** FSS (Labelling & Display) Regulations 2020 — declaration of food allergens.
**Why it matters:** An undeclared allergen can cause a life-threatening reaction and triggers a mandatory recall.
```rule
check: allergen_coverage
severity: critical
params: {}
```

## R13 — Allergen statement in bold (company standard)
**Rule:** Company standard: the "Contains:" allergen statement shall be set in bold type.
**Basis:** Sunfield artwork standard SAS-04 (stricter than the regulation).
**Why it matters:** Bold type makes the statement findable at a glance — Sunfield's interpretation of "clearly declared".
```rule
check: text_style
severity: minor
params: {fact: allergen_statement, bold: true}
```

## R14 — Energy consistent with macronutrients
**Rule:** Declared energy shall be consistent with the declared protein, carbohydrate and fat, using 4, 4 and 9 kcal per gram; differences above 5% must be corrected or justified.
**Basis:** FSS (Labelling & Display) Regulations 2020 — nutritional information; Sunfield nutrition QA.
**Why it matters:** An energy value that does not add up is almost always a typo, and energy drives the %RDA and any front-of-pack warning.
```rule
check: nutrition_math
severity: major
params: {kind: energy, tolerance: 0.05}
```

## R15 — Nutrition sub-components do not exceed totals
**Rule:** Sub-components shall not exceed their totals: total sugars ≤ carbohydrate, added sugars ≤ total sugars, and saturated plus trans fat ≤ total fat.
**Basis:** Sunfield nutrition QA.
**Why it matters:** Impossible numbers destroy the credibility of the whole panel and signal a data-entry error.
```rule
check: nutrition_math
severity: major
params: {kind: subtotals}
```

## R16 — Per-serve values consistent with per-100 g values
**Rule:** Per-serve values shall equal the per-100 g values scaled to the declared serving size, allowing for rounding.
**Basis:** FSS (Labelling & Display) Regulations 2020 — nutritional information per serve.
**Why it matters:** Consumers read the per-serve column; an inconsistent value misstates what they actually eat.
```rule
check: nutrition_math
severity: major
params: {kind: per_serve, tolerance: 0.05}
```

## R17 — Nutrition values match the approved data
**Rule:** Per-100 g nutrition values shall match the approved, lab-verified nutrition data within 2%.
**Basis:** Sunfield master data.
**Why it matters:** Printed values must be defensible against the lab report if the product is sampled.
```rule
check: matches_approved
severity: major
params: {fact: nutrition, approved_field: nutrition_per_100g, mode: nutrition, tolerance: 0.02}
```

## R18 — %RDA values correctly calculated
**Rule:** %RDA per serve shall be calculated on the reference values energy 2000 kcal, total fat 67 g, saturated fat 22 g, trans fat 2 g, added sugars 50 g and sodium 2000 mg.
**Basis:** FSS (Labelling & Display) Regulations 2020 — %RDA declaration.
**Why it matters:** Wrong %RDA understates intake of nutrients of concern.
```rule
check: nutrition_math
severity: minor
params:
  kind: rda
  tolerance_points: 1.5
  reference: {energy_kcal: 2000, total_fat_g: 67, saturated_fat_g: 22, trans_fat_g: 2, added_sugars_g: 50, sodium_mg: 2000}
```

## R19 — Nutrition claims are supported by the nutrition table
**Rule:** Nutrition claims shall be made only when the product meets the claim conditions: "low fat" ≤ 3 g fat per 100 g; "fat free" ≤ 0.5 g per 100 g; "sugar free" ≤ 0.5 g sugars per 100 g; "low sugar" ≤ 5 g per 100 g; "no added sugar" only with no added sugars; "source of protein" ≥ 10% and "high protein" ≥ 20% of energy from protein; "low sodium" ≤ 120 mg per 100 g; "trans fat free" < 0.2 g per 100 g.
**Basis:** FSS (Advertising & Claims) Regulations 2018 — Sunfield interpretation for solid foods.
**Why it matters:** An unsupported claim is misleading advertising and a frequent cause of regulatory action.
```rule
check: claim_threshold
severity: critical
params:
  claims:
    - {name: "Low fat", patterns: ['low[\s-]*fat'], nutrient: total_fat_g, op: "<=", value: 3.0, unit: g}
    - {name: "Fat free", patterns: ['fat[\s-]*free', '\bzero\s*fat'], nutrient: total_fat_g, op: "<=", value: 0.5, unit: g}
    - {name: "Sugar free", patterns: ['sugar[\s-]*free', '\bno\s*sugar\b', 'zero\s*sugar'], nutrient: total_sugars_g, op: "<=", value: 0.5, unit: g}
    - {name: "Low sugar", patterns: ['low[\s-]*sugar'], nutrient: total_sugars_g, op: "<=", value: 5.0, unit: g}
    - {name: "No added sugar", patterns: ['no\s*added\s*sugars?'], nutrient: added_sugars_g, op: "<=", value: 0.0, unit: g}
    - {name: "Source of protein", patterns: ['source\s*of\s*protein'], derived: protein_energy_pct, op: ">=", value: 10, unit: "% energy"}
    - {name: "High protein", patterns: ['high[\s-]*protein', 'rich\s*in\s*protein', 'protein[\s-]*rich'], derived: protein_energy_pct, op: ">=", value: 20, unit: "% energy"}
    - {name: "Low sodium", patterns: ['low[\s-]*(sodium|salt)'], nutrient: sodium_mg, op: "<=", value: 120, unit: mg}
    - {name: "Trans fat free", patterns: ['trans[\s-]*fat[\s-]*free', '(zero|no|0\s*g)\s*trans[\s-]*fat'], nutrient: trans_fat_g, op: "<", value: 0.2, unit: g}
```

## R20 — Date marking
**Rule:** The label shall carry a "Use by" or "Best before" (or expiry) declaration and the date of manufacture or packing.
**Basis:** FSS (Labelling & Display) Regulations 2020 — date marking.
**Why it matters:** Without date marking a product cannot legally be sold, and stock rotation fails.
```rule
check: fact_present
severity: major
params: {fact: [best_before, mfg_date]}
```

## R21 — Batch or lot identification
**Rule:** A batch or lot number reference shall be provided.
**Basis:** FSS (Labelling & Display) Regulations 2020 — lot/code/batch identification.
**Why it matters:** Without batch identification a recall has to cover every pack ever made.
```rule
check: fact_present
severity: minor
params: {fact: batch_no}
```

## R22 — Manufacturer name and complete address
**Rule:** The name and complete address of the manufacturer (and of the marketer, if different) shall be declared, including the PIN code.
**Basis:** FSS (Labelling & Display) Regulations 2020 — name and complete address.
**Why it matters:** Regulators and consumers must be able to identify who is responsible for the product.
```rule
check: fact_present
severity: major
params: {fact: manufacturer, require: {has_address: true}}
```

## R23 — Consumer care details
**Rule:** Consumer care details — a telephone number or e-mail address for complaints — shall be declared.
**Basis:** Legal Metrology (Packaged Commodities) Rules 2011 — consumer care details.
**Why it matters:** A complaint channel is a legal requirement and the first line of defence before a complaint escalates.
```rule
check: fact_present
severity: major
params: {fact: customer_care, require: {has_contact: true}}
```

## R24 — Retail barcode scans and matches the SKU
**Rule:** The retail barcode shall scan, carry a valid GS1 check digit and encode the GTIN assigned to this SKU.
**Basis:** GS1 General Specifications; Sunfield master data.
**Why it matters:** A wrong or unreadable barcode stops the product at the retailer's goods-in and at the till.
```rule
check: barcode_valid
severity: major
params: {}
```

## R25 — Prohibited marketing terms
**Rule:** Company policy: the terms "100% natural", "all natural", "healthy", "immunity booster", "doctor recommended" and "cures" shall not appear on the pack.
**Basis:** Sunfield marketing policy MP-02 (derived from FSS Advertising & Claims Regulations 2018).
**Why it matters:** These terms attract regulatory scrutiny and are difficult or impossible to substantiate.
```rule
check: text_absent
severity: major
params:
  label: prohibited marketing terms
  patterns: ['100\s*%\s*natural', '\ball[\s-]*natural', '\bhealthy\b', 'immunity\s*boost', 'doctor\s*recommended', '\bcures?\b']
```

## R26 — Front-of-pack statements must not mislead
**Rule:** Front-of-pack statements and imagery shall not mislead the consumer about the nature, composition or health benefits of the product.
**Basis:** FSS (Advertising & Claims) Regulations 2018 — general principles.
**Why it matters:** This is a judgment call; the checker flags candidates, a person decides.
```rule
check: ai_judgment
severity: major
params:
  instruction: >-
    Judge whether any front-panel statement could mislead the consumer about the nature, composition or
    health benefits of the product. Compare the statements with the ingredient list and nutrition values.
    A statement that is factually supported (for example 'baked, not fried' on a baked product) is not misleading.
```

## R27 — QR code to digital product information (company rule from 1 Jan 2027)
**Rule:** From 1 January 2027, every new artwork shall carry a QR code linking to the product's digital information page.
**Basis:** Sunfield digital label programme (company rule, announced September 2026).
**Why it matters:** Artwork approved today will still be printing in 2027; adding the QR now avoids a second reprint.
```rule
check: qr_present
severity: minor
status: upcoming
effective_from: 2027-01-01
params: {}
```

## R28 — Front-of-pack warning for high sugar, salt or fat (proposed — not law)
**Rule:** Readiness rule for a proposed regulation: if a product is high in sugar, salt or saturated fat, a front-of-pack warning such as "HIGH SUGAR" will be required. Provisional company thresholds pending FSSAI notification: total sugars above 22.5 g, sodium above 600 mg, saturated fat above 5 g per 100 g.
**Basis:** FSSAI front-of-pack warning proposal filed before the Supreme Court (Aug–Sep 2026); thresholds are Sunfield's provisional assumption, not FSSAI's.
**Why it matters:** Shows which SKUs will need artwork changes if the proposal is adopted, before it becomes urgent.
```rule
check: fop_warning
severity: major
status: proposed
params:
  scope: front
  nutrients:
    - {nutrient: total_sugars_g, threshold: 22.5, unit: g, warning: "HIGH SUGAR", patterns: ['high\s*sugar']}
    - {nutrient: sodium_mg, threshold: 600, unit: mg, warning: "HIGH SALT", patterns: ['high\s*(salt|sodium)']}
    - {nutrient: saturated_fat_g, threshold: 5.0, unit: g, warning: "HIGH FAT", patterns: ['high\s*(saturated\s*)?fat']}
```
