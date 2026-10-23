# US Packaging Artwork Rulebook — Breakfast Cereals & Granola (FDA)

**Scope:** pre-packaged granola and cereals sold in the United States · **Version:** 1.0 (October 2026) · **Owner:** Regulatory Affairs

This rulebook combines the federal requirements (FDA, 21 CFR 101; USDA NOP for organic claims) with the
company's own, stricter interpretations ("company standard"). Every rule has a human-readable statement (what a
reviewer reads) and a machine-readable block (what the checker runs). The checker quotes the **Rule** sentence as
evidence in every finding.

> Demo document written for the Esko AIForge sample artwork. Citations are to the US Code of Federal Regulations as
> understood in October 2026; interpretations and thresholds marked "company standard" are illustrative and must be
> verified against the official text before real use.

## U01 — Statement of identity on the principal display panel
**Rule:** The statement of identity shall appear on the principal display panel as one of its principal features, using the approved name of the food.
**Basis:** 21 CFR 101.3(a)–(d) — identity labeling.
**Why it matters:** Shoppers and inspectors identify the food from the front panel; a missing or different name misbrands the product.
```rule
check: identity_on_panel
severity: major
params: {approved_field: [statement_of_identity, product_name], scope: front}
```

## U02 — Net quantity on the principal display panel
**Rule:** The net quantity of contents shall be declared on the principal display panel, within the bottom 30 percent of the panel.
**Basis:** 21 CFR 101.105(a) and (f).
**Why it matters:** Net quantity is checked by state weights-and-measures inspectors on the front panel; declaring it only on the back is a misbranding.
```rule
check: fact_present
severity: critical
params: {fact: net_quantity, scope: front}
```

## U03 — Net quantity term and dual units
**Rule:** The net quantity shall use a term such as "Net Wt" and be stated in both US customary units (ounces, and pounds for 1–4 lb packages) and metric units, e.g. "NET WT 16.9 OZ (1 LB 0.9 OZ) 480 g".
**Basis:** 21 CFR 101.105(a), (b) and (j); Fair Packaging and Labeling Act, 15 U.S.C. 1453(a)(3).
**Why it matters:** A metric-only quantity does not meet the federal dual-declaration requirement and is rejected by US retailers.
```rule
check: net_quantity_format
severity: major
params: {require_label: true, require_us: true, require_metric: true, tolerance: 0.02}
```

## U04 — Net quantity matches the content sheet
**Rule:** The declared net quantity shall match the pack size in the approved content sheet.
**Basis:** Company master data.
**Why it matters:** A wrong pack size breaks pricing, logistics and the servings-per-container calculation.
```rule
check: matches_approved
severity: critical
params: {fact: net_quantity, approved_field: net_quantity, mode: quantity}
```

## U05 — Ingredient list matches the content sheet
**Rule:** The ingredient statement shall reproduce the approved ingredient list exactly, in descending order of predominance by weight, including percentage declarations.
**Basis:** 21 CFR 101.4; company master data.
**Why it matters:** Ingredient differences are the leading cause of artwork-driven recalls — an added ingredient can be an undeclared allergen.
```rule
check: matches_approved
severity: critical
params: {fact: ingredients, approved_field: ingredients, mode: ingredients}
```

## U06 — Major food allergens declared in a "Contains" statement
**Rule:** Each major food allergen present — milk, egg, fish, crustacean shellfish, tree nuts (naming each nut), peanuts, wheat, soybeans and sesame — shall be declared in a "Contains:" statement directly after the ingredient list (company standard; FALCPA also allows naming the source within the ingredient list).
**Basis:** FD&C Act 403(w) (FALCPA 2004, FASTER Act 2021 — sesame); company allergen policy AP-01.
**Why it matters:** Undeclared allergens are the most common reason for FDA food recalls and can cause fatal reactions.
```rule
check: allergen_coverage
severity: critical
params: {allergens: us}
```

## U07 — Advisory "may contain" must not name real ingredients
**Rule:** Precautionary ("may contain") statements may only describe unintended cross-contact; they shall not name an allergen that is an ingredient of the product.
**Basis:** FD&C Act 403(a)(1) — labeling must not be false or misleading; FDA guidance on advisory allergen labeling; company policy AP-01.
**Why it matters:** "May contain almonds" on a product that does contain almonds understates a certain risk and confuses allergic consumers.
```rule
check: precautionary_conflict
severity: critical
params: {allergens: us}
```

## U08 — Serving size in household measure and metric units
**Rule:** The serving size shall be declared in a common household measure followed by the metric quantity in parentheses, e.g. "2/3 cup (60g)".
**Basis:** 21 CFR 101.9(b)(7).
**Why it matters:** Without a unit, every per-serving value on the panel is meaningless to the consumer.
```rule
check: nutrition_math
severity: major
params: {kind: serving_size}
```

## U09 — Servings per container consistent with the net quantity
**Rule:** The number of servings per container multiplied by the serving size shall equal the net quantity of the package, allowing for rounding.
**Basis:** 21 CFR 101.9(b)(8).
**Why it matters:** An inconsistent serving count misstates how much a consumer eats per pack.
```rule
check: nutrition_math
severity: major
params: {kind: servings_net, tolerance: 0.1}
```

## U10 — Calories consistent with protein, carbohydrate and fat
**Rule:** Declared calories shall be consistent with the declared protein, total carbohydrate and total fat using 4, 4 and 9 calories per gram; a difference of more than 20% is treated as an error.
**Basis:** 21 CFR 101.9(c)(1)(i) (general factors); compliance tolerance 101.9(g)(5).
**Why it matters:** A calorie figure that does not add up signals a data-entry error that FDA sampling would expose.
```rule
check: nutrition_math
severity: major
params: {kind: energy, basis: per_serve, tolerance: 0.20}
```

## U11 — Nutrient sub-totals do not exceed totals
**Rule:** Sub-components shall not exceed their totals: total sugars and dietary fiber ≤ total carbohydrate, added sugars ≤ total sugars, saturated plus trans fat ≤ total fat.
**Basis:** 21 CFR 101.9(c); company nutrition QA.
**Why it matters:** Impossible numbers destroy the credibility of the whole Nutrition Facts panel.
```rule
check: nutrition_math
severity: major
params: {kind: subtotals}
```

## U31 — Nutrition values are physically plausible
**Rule:** Nutrition values shall be physically possible and consistent with the ingredients: protein, carbohydrate and fat together cannot weigh more than the serving, and trans fat, cholesterol and vitamin or mineral levels must be explained by the ingredient list.
**Basis:** Company nutrition QA; FDA determination that partially hydrogenated oils are not GRAS (2015, in force 2018–2020).
**Why it matters:** Impossible values prove the panel was filled in wrongly, so none of its numbers can be trusted until it is rebuilt from lab data.
```rule
check: nutrition_math
severity: major
params: {kind: plausibility}
```

## U12 — %Daily Values correctly calculated
**Rule:** Each %Daily Value shall be calculated from the amount per serving and the FDA Daily Values (e.g. total fat 78 g, saturated fat 20 g, cholesterol 300 mg, sodium 2,300 mg, total carbohydrate 275 g, dietary fiber 28 g, added sugars 50 g, vitamin D 20 mcg, calcium 1,300 mg, iron 18 mg, potassium 4,700 mg) and expressed to the nearest whole percent.
**Basis:** 21 CFR 101.9(c)(8)(iv), (c)(9) and (d)(7)(ii).
**Why it matters:** %DV is what consumers use to compare products; a wrong %DV is a misbranding.
```rule
check: nutrition_math
severity: major
params: {kind: dv, tolerance_points: 1.5}
```

## U13 — Nutrition values match the content sheet
**Rule:** Amounts per serving on the Nutrition Facts panel shall match the approved values in the content sheet within 2%.
**Basis:** Company master data.
**Why it matters:** The printed panel must be defensible against the approved formulation and lab data.
```rule
check: matches_approved
severity: major
params: {fact: nutrition, approved_field: nutrition_per_serving, mode: nutrition, basis: per_serve, tolerance: 0.02}
```

## U14 — "No added sugar" claim conditions
**Rule:** "No added sugar" may be used only if no sugars or sugar-containing ingredients — such as honey, syrups, molasses, fruit juice concentrates, coconut or cane sugar — are added during processing.
**Basis:** 21 CFR 101.60(c)(2).
**Why it matters:** An unsupported sugar claim is a classic false-advertising case and triggers FDA warning letters.
```rule
check: claim_conflict
severity: critical
params:
  claims:
    - name: No added sugar
      patterns: ['no\s*added\s*sugars?', 'without\s*added\s*sugars?']
      forbidden: ['sugar', 'honey', 'syrup', 'molasses', 'agave', 'nectar', 'dextrose', 'fructose', 'glucose', 'maltose', 'sucrose', 'maltodextrin', 'juice\s*concentrate', 'treacle', 'jaggery']
      reason: sugars and sugar-containing ingredients are added
```

## U15 — "Added Sugars" declaration consistent with the ingredients
**Rule:** When sugars or syrups are ingredients, the "Includes … Added Sugars" line shall declare them; 0 g is permitted only when added sugars are below 0.5 g per serving.
**Basis:** 21 CFR 101.9(c)(6)(iii).
**Why it matters:** Declaring 0 g added sugars while honey and sugar are listed misleads consumers and contradicts the ingredient statement.
```rule
check: claim_conflict
severity: major
params:
  claims:
    - name: 0 g added sugars declared
      nutrient: added_sugars_g
      op: "=="
      value: 0
      review: true
      forbidden: ['sugar', 'honey', 'syrup', 'molasses', 'agave', 'nectar', 'dextrose', 'fructose', 'glucose', 'maltodextrin', 'juice\s*concentrate']
      reason: confirm with the recipe that added sugars are below 0.5 g per serving
```

## U16 — Vegan claims and marks
**Rule:** Vegan claims or vegan certification marks shall be used only if the product contains no animal-derived ingredients such as honey, milk, eggs, gelatin, beeswax or carmine.
**Basis:** Company claims policy CP-03 (FDA has no definition of "vegan"; certification bodies such as Vegan Action exclude honey).
**Why it matters:** A vegan claim on a honey-containing product is false and is a frequent consumer complaint and class-action trigger.
```rule
check: claim_conflict
severity: critical
params:
  claims:
    - name: Vegan claim
      patterns: ['\bvegans?\b', 'certified\s*vegan', 'vegan\.?\s*org', 'v\.org']
      forbidden: ['honey', '\bmilk\b', 'whey', 'casein', '\beggs?\b', 'gelatin', 'beeswax', 'carmine', 'shellac', 'lanolin', 'anchov']
      reason: these ingredients are of animal origin
```

## U17 — Organic claims name the certifying agent
**Rule:** When the product is labeled "organic" or carries the USDA Organic seal, the information panel shall state "Certified organic by" followed by the name of the certifying agent.
**Basis:** 7 CFR 205.303(b)(2) (USDA National Organic Program).
**Why it matters:** Without the certifier statement the organic claim is non-compliant and the USDA seal may not be used.
```rule
check: text_present
severity: major
params:
  patterns: ['certified\s+organic\s+by\s+\S+']
  when: ['usda\s*organic', '^\s*usda\s*$', '\borganic\b']
  label: "“Certified organic by …” statement"
  when_label: an organic claim or the USDA Organic seal
```

## U18 — Name and place of business
**Rule:** The label shall state the name and place of business — city, state and ZIP code — of the manufacturer, packer or distributor, qualified by "Manufactured for" or "Distributed by" where the firm is not the manufacturer.
**Basis:** 21 CFR 101.5.
**Why it matters:** Regulators and consumers must be able to reach the responsible firm; a name without an address is non-compliant.
```rule
check: fact_present
severity: major
params: {fact: manufacturer, require: {has_address: true}, require_desc: {has_address: "a place of business (city, state and ZIP code)"}}
```

## U19 — UPC barcode scans and matches the content sheet
**Rule:** The retail barcode shall scan, carry a valid GS1 check digit and encode the UPC assigned to this item in the content sheet.
**Basis:** GS1 General Specifications; company master data.
**Why it matters:** A wrong UPC stops the product at the retailer's receiving dock and at the register.
```rule
check: barcode_valid
severity: major
params: {}
```

## U20 — Prohibited marketing terms (company standard)
**Rule:** Company policy: the terms "GMO free", "all natural", "100% natural", "healthy" and "superfood" shall not appear on the pack; use "Non-GMO Project Verified" only with that certification.
**Basis:** FDA guidance on voluntary labeling of foods derived from genetically engineered plants (2019) — "GMO free" may be misleading; FDA "healthy" claim rule, 21 CFR 101.65(d); company claims policy CP-01.
**Why it matters:** These terms are hard to substantiate and attract regulatory and legal challenges.
```rule
check: text_absent
severity: major
params:
  label: prohibited marketing terms
  patterns: ['gmo[\s-]*free', '\ball[\s-]*natural', '100\s*%\s*natural', '\bhealthy\b', 'super[\s-]*food']
```

## U21 — Claims are on the approved claims list
**Rule:** Every claim, badge and certification on the pack shall be on the approved claims list of the content sheet.
**Basis:** Company claims-approval process (legal sign-off of every claim).
**Why it matters:** Unapproved claims bypass substantiation and legal review.
```rule
check: claims_approved
severity: minor
params: {approved_field: claims, also_approved_fields: [dietary_information, marketing_copy]}
```

## U22 — Front-of-pack energy matches the Nutrition Facts panel
**Rule:** Calorie or energy values printed outside the Nutrition Facts panel (front-of-pack badges, call-outs) shall match the panel's values per serving or per container.
**Basis:** 21 CFR 101.13(h)–(i) principles on nutrient statements outside the panel; FD&C Act 403(a)(1); company standard.
**Why it matters:** Two different calorie figures on one pack mislead consumers and invite regulatory action.
```rule
check: energy_consistency
severity: major
params: {tolerance: 0.1}
```

## U23 — Approved copy: dietary information
**Rule:** The dietary information statement shall be printed exactly as approved in the content sheet.
**Basis:** Company copy-approval process.
**Why it matters:** Dietary statements (vegetarian / vegan) are claims; any deviation from the approved text is an unapproved claim.
```rule
check: approved_copy
severity: major
params: {field: dietary_information, label: dietary information}
```

## U24 — Approved copy: storage instructions
**Rule:** The storage instructions shall be printed exactly as approved in the content sheet.
**Basis:** Company copy-approval process.
**Why it matters:** Storage text drives shelf-life performance and consumer safety.
```rule
check: approved_copy
severity: minor
params: {field: storage, label: storage instructions}
```

## U25 — Approved copy: allergen information box
**Rule:** The allergen information box shall be printed exactly as approved in the content sheet.
**Basis:** Company copy-approval process.
**Why it matters:** Allergen wording is safety-critical copy; it must match what the allergen team approved.
```rule
check: approved_copy
severity: minor
params: {field: allergen_information, label: allergen information}
```

## U26 — Approved copy: Nutrition Facts footnote
**Rule:** The Nutrition Facts footnote shall be printed exactly as approved in the content sheet.
**Basis:** 21 CFR 101.9(d)(9); company copy-approval process.
**Why it matters:** The footnote wording is prescribed by FDA.
```rule
check: approved_copy
severity: minor
params: {field: nf_footnote, label: Nutrition Facts footnote}
```

## U27 — No hidden text in the print file
**Rule:** The print-ready file shall not contain text that is hidden behind other artwork.
**Basis:** Company prepress standard PP-07.
**Why it matters:** Hidden copy (for example an old nutrition table under a new one) confuses proofreading tools and can reappear if layers move.
```rule
check: hidden_text
severity: minor
params: {}
```

## U28 — Front-panel imagery must not mislead
**Rule:** The front-panel vignette and statements shall not misrepresent the product — for example, fruit pictured on the pack must be fruit that is in the ingredient list.
**Basis:** FD&C Act 403(a)(1) — false or misleading labeling; company claims policy.
**Why it matters:** This is a judgment call; the checker flags candidates, a person decides.
```rule
check: ai_judgment
severity: major
params:
  instruction: >-
    Compare the foods pictured on the front panel (fruits, nuts, grains) with the ingredient list. Flag any
    prominently pictured ingredient that is not in the ingredient list, and any front-panel statement
    (for example "HIGH-GRAIN" or "PACKED WITH GOODNESS") that the ingredients or nutrition values do not support.
```

## U29 — FDA front-of-package "Nutrition Info" box (proposed — not law)
**Rule:** Readiness rule for a proposed regulation: FDA's January 2025 proposal would require a "Nutrition Info" box on the principal display panel rating saturated fat, sodium and added sugars Low, Medium or High, where High means 20% DV or more per serving.
**Basis:** FDA proposed rule "Food Labeling: Front-of-Package Nutrition Information" (January 2025); not final as of October 2026 — thresholds as proposed.
**Why it matters:** Shows which nutrients would be rated High before the rule is finalised, so artwork changes can be planned.
```rule
check: fop_warning
severity: major
status: proposed
params:
  basis: dv
  what: a front-of-pack "Nutrition Info" box rating
  nutrients:
    - {nutrient: saturated_fat_g, threshold: 20, warning: "Saturated Fat HIGH", patterns: ['nutrition\s*info']}
    - {nutrient: sodium_mg, threshold: 20, warning: "Sodium HIGH", patterns: ['nutrition\s*info']}
    - {nutrient: added_sugars_g, threshold: 20, warning: "Added Sugars HIGH", patterns: ['nutrition\s*info']}
```

## U30 — QR code to the digital label (company rule from 1 Jan 2027)
**Rule:** From 1 January 2027, every new US carton shall carry a QR code linking to the product's digital label page.
**Basis:** Company digital-label programme (announced September 2026).
**Why it matters:** Artwork approved now will still be printing in 2027; adding the QR now avoids a second reprint.
```rule
check: qr_present
severity: minor
status: upcoming
effective_from: 2027-01-01
params: {}
```
