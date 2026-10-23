# Evaluation results

Planted-defect test set: 26 single-defect artworks + 2 clean artworks, each in 3 input form(s). Ground truth is known because every artwork is generated from data. AI features: OFF (offline run).

| Input | Defects flagged | …as definite FAIL | Expected rule hits (FAIL / REVIEW / missed) | False alarms (all files) | False alarms on clean files | Review items per file | Seconds per file |
|---|---|---|---|---|---|---|---|
| Vector PDF (print-ready file) | 26/26 | 26/26 | 33 / 0 / 0 of 33 | 0 | 0 | 0.0 | 0.3 |
| PNG, 200 dpi (flattened artwork) | 26/26 | 20/26 | 27 / 6 / 0 of 33 | 0 | 0 | 2.79 | 4.3 |
| Phone-style photo (tilted, blurred, no scale) | 26/26 | 21/26 | 28 / 5 / 0 of 33 | 0 | 0 | 3.93 | 8.1 |

*Flagged* = every rule the defect breaks is reported as FAIL or REVIEW. *False alarm* = a FAIL on a rule that should pass. Review items exclude the judgment-only rule R26, which always needs a person when AI is off.

## Per-artwork detail

| Artwork | Input | Expected | FAIL | REVIEW | Missed | False alarms |
|---|---|---|---|---|---|---|
| CLEAN_cookies — clean cookies artwork | pdf | — | — | R26 | — | — |
| CLEAN_cookies — clean cookies artwork | png | — | — | R11, R13, R17, R26 | — | — |
| CLEAN_cookies — clean cookies artwork | photo | — | — | R05, R11, R13, R17, R26 | — | — |
| CLEAN_peanuts — clean peanuts artwork | pdf | — | — | R26 | — | — |
| CLEAN_peanuts — clean peanuts artwork | png | — | — | R11, R13, R17, R26 | — | — |
| CLEAN_peanuts — clean peanuts artwork | photo | — | — | R05, R11, R13, R17, R26 | — | — |
| D01_name_typo — Product name misspelt on front | pdf | R01 | R01 | R26 | — | — |
| D01_name_typo — Product name misspelt on front | png | R01 | — | R01, R11, R13, R17, R26 | — | — |
| D01_name_typo — Product name misspelt on front | photo | R01 | — | R01, R05, R11, R13, R17, R26 | — | — |
| D02_front_qty_mismatch — Front net quantity 250 g vs approved 200 g | pdf | R03, R04 | R03, R04 | R26 | — | — |
| D02_front_qty_mismatch — Front net quantity 250 g vs approved 200 g | png | R03, R04 | R03 | R04, R11, R13, R17, R26 | — | — |
| D02_front_qty_mismatch — Front net quantity 250 g vs approved 200 g | photo | R03, R04 | R03, R04 | R05, R11, R13, R17, R24, R26 | — | — |
| D03_front_qty_missing — Net quantity missing from front panel | pdf | R02 | R02 | R26 | — | — |
| D03_front_qty_missing — Net quantity missing from front panel | png | R02 | R02 | R11, R13, R17, R26 | — | — |
| D03_front_qty_missing — Net quantity missing from front panel | photo | R02 | R02 | R11, R13, R17, R26 | — | — |
| D04_front_qty_too_small — Net quantity numerals too small | pdf | R05 | R05 | R26 | — | — |
| D04_front_qty_too_small — Net quantity numerals too small | png | R05 | R05 | R11, R13, R17, R26 | — | — |
| D04_front_qty_too_small — Net quantity numerals too small | photo | R05 | — | R05, R11, R13, R17, R26 | — | — |
| D05_mrp_no_tax_text — MRP without 'inclusive of all taxes' | pdf | R06 | R06 | R26 | — | — |
| D05_mrp_no_tax_text — MRP without 'inclusive of all taxes' | png | R06 | — | R06, R11, R13, R17, R26 | — | — |
| D05_mrp_no_tax_text — MRP without 'inclusive of all taxes' | photo | R06 | R06 | R05, R11, R13, R17, R24, R26 | — | — |
| D06_mrp_mismatch — MRP 65 vs approved 60 | pdf | R07 | R07 | R26 | — | — |
| D06_mrp_mismatch — MRP 65 vs approved 60 | png | R07 | R07 | R11, R13, R17, R26 | — | — |
| D06_mrp_mismatch — MRP 65 vs approved 60 | photo | R07 | R07 | R05, R11, R13, R17, R26 | — | — |
| D07_licence_13_digits — FSSAI licence has 13 digits | pdf | R08, R09 | R08, R09 | R26 | — | — |
| D07_licence_13_digits — FSSAI licence has 13 digits | png | R08, R09 | R08, R09 | R11, R13, R17, R26 | — | — |
| D07_licence_13_digits — FSSAI licence has 13 digits | photo | R08, R09 | R08, R09 | R05, R11, R13, R17, R26 | — | — |
| D08_licence_wrong_digit — FSSAI licence differs from approved | pdf | R09 | R09 | R26 | — | — |
| D08_licence_wrong_digit — FSSAI licence differs from approved | png | R09 | R09 | R11, R13, R17, R26 | — | — |
| D08_licence_wrong_digit — FSSAI licence differs from approved | photo | R09 | R09 | R05, R11, R13, R17, R26 | — | — |
| D09_veg_mark_missing — Veg mark missing | pdf | R10 | R10 | R26 | — | — |
| D09_veg_mark_missing — Veg mark missing | png | R10 | R10 | R11, R13, R17, R26 | — | — |
| D09_veg_mark_missing — Veg mark missing | photo | R10 | R10 | R05, R11, R13, R17, R26 | — | — |
| D10_nonveg_mark_on_veg — Non-veg mark on a veg product | pdf | R10 | R10 | R26 | — | — |
| D10_nonveg_mark_on_veg — Non-veg mark on a veg product | png | R10 | R10 | R11, R13, R17, R26 | — | — |
| D10_nonveg_mark_on_veg — Non-veg mark on a veg product | photo | R10 | R10 | R05, R11, R13, R17, R24, R26 | — | — |
| D11_ingredient_pct_changed — Ingredient percentage changed | pdf | R11 | R11 | R26 | — | — |
| D11_ingredient_pct_changed — Ingredient percentage changed | png | R11 | R11 | R13, R17, R26 | — | — |
| D11_ingredient_pct_changed — Ingredient percentage changed | photo | R11 | R11 | R05, R13, R17, R26 | — | — |
| D12_ingredient_missing — Ingredient missing from list | pdf | R11 | R11 | R26 | — | — |
| D12_ingredient_missing — Ingredient missing from list | png | R11 | R11 | R13, R17, R26 | — | — |
| D12_ingredient_missing — Ingredient missing from list | photo | R11 | R11 | R05, R13, R17, R26 | — | — |
| D13_allergen_soy_missing — Soy missing from allergen declaration | pdf | R12 | R12 | R26 | — | — |
| D13_allergen_soy_missing — Soy missing from allergen declaration | png | R12 | R12 | R11, R13, R17, R26 | — | — |
| D13_allergen_soy_missing — Soy missing from allergen declaration | photo | R12 | — | R05, R11, R12, R13, R17, R26 | — | — |
| D14_allergen_statement_missing — Allergen declaration missing | pdf | R12 | R12 | R26 | — | — |
| D14_allergen_statement_missing — Allergen declaration missing | png | R12 | — | R11, R12, R17, R26 | — | — |
| D14_allergen_statement_missing — Allergen declaration missing | photo | R12 | R12 | R05, R11, R17, R26 | — | — |
| D15_allergen_not_bold — Allergen declaration not bold | pdf | R13 | R13 | R26 | — | — |
| D15_allergen_not_bold — Allergen declaration not bold | png | R13 | — | R11, R13, R17, R26 | — | — |
| D15_allergen_not_bold — Allergen declaration not bold | photo | R13 | — | R05, R11, R13, R17, R26 | — | — |
| D16_energy_typo — Energy per 100 g typed as 438 | pdf | R14, R16, R17 | R14, R16, R17 | R26 | — | — |
| D16_energy_typo — Energy per 100 g typed as 438 | png | R14, R16, R17 | R14, R16, R17 | R11, R13, R26 | — | — |
| D16_energy_typo — Energy per 100 g typed as 438 | photo | R14, R16, R17 | R14, R16, R17 | R05, R11, R13, R26 | — | — |
| D17_sugar_exceeds_carbs — Total sugars greater than carbohydrate | pdf | R15, R17 | R15, R17 | R26 | — | — |
| D17_sugar_exceeds_carbs — Total sugars greater than carbohydrate | png | R15, R17 | R15, R17 | R11, R13, R26 | — | — |
| D17_sugar_exceeds_carbs — Total sugars greater than carbohydrate | photo | R15, R17 | R15, R17 | R05, R11, R13, R26 | — | — |
| D18_per_serve_wrong — Per-serve fat inconsistent | pdf | R16, R18 | R16, R18 | R26 | — | — |
| D18_per_serve_wrong — Per-serve fat inconsistent | png | R16, R18 | R16, R18 | R11, R13, R17, R26 | — | — |
| D18_per_serve_wrong — Per-serve fat inconsistent | photo | R16, R18 | R16, R18 | R05, R11, R13, R17, R24, R26 | — | — |
| D19_false_low_fat_claim — 'Low fat' claim with 21 g fat | pdf | R19 | R19 | R26 | — | — |
| D19_false_low_fat_claim — 'Low fat' claim with 21 g fat | png | R19 | R19 | R11, R13, R17, R26 | — | — |
| D19_false_low_fat_claim — 'Low fat' claim with 21 g fat | photo | R19 | R19 | R05, R11, R13, R17, R26 | — | — |
| D20_false_high_protein_claim — 'High protein' claim not supported | pdf | R19 | R19 | R26 | — | — |
| D20_false_high_protein_claim — 'High protein' claim not supported | png | R19 | R19 | R11, R13, R17, R26 | — | — |
| D20_false_high_protein_claim — 'High protein' claim not supported | photo | R19 | R19 | R05, R11, R13, R17, R26 | — | — |
| D21_prohibited_term — Prohibited marketing term | pdf | R25 | R25 | R26 | — | — |
| D21_prohibited_term — Prohibited marketing term | png | R25 | R25 | R11, R13, R17, R26 | — | — |
| D21_prohibited_term — Prohibited marketing term | photo | R25 | R25 | R05, R11, R13, R17, R24, R26 | — | — |
| D22_date_coding_missing — Batch / date-of-manufacture / use-by line missing | pdf | R20, R21 | R20, R21 | R26 | — | — |
| D22_date_coding_missing — Batch / date-of-manufacture / use-by line missing | png | R20, R21 | R20, R21 | R11, R13, R17, R26 | — | — |
| D22_date_coding_missing — Batch / date-of-manufacture / use-by line missing | photo | R20, R21 | R20, R21 | R05, R11, R13, R17, R24, R26 | — | — |
| D23_manufacturer_missing — Manufacturer name/address missing | pdf | R22 | R22 | R26 | — | — |
| D23_manufacturer_missing — Manufacturer name/address missing | png | R22 | — | R11, R13, R17, R22, R26 | — | — |
| D23_manufacturer_missing — Manufacturer name/address missing | photo | R22 | R22 | R05, R11, R13, R17, R26 | — | — |
| D24_customer_care_missing — Consumer care details missing | pdf | R23 | R23 | R26 | — | — |
| D24_customer_care_missing — Consumer care details missing | png | R23 | R23 | R11, R13, R17, R26 | — | — |
| D24_customer_care_missing — Consumer care details missing | photo | R23 | R23 | R05, R11, R13, R17, R26 | — | — |
| D25_barcode_wrong_gtin — Barcode encodes another product's GTIN | pdf | R24 | R24 | R26 | — | — |
| D25_barcode_wrong_gtin — Barcode encodes another product's GTIN | png | R24 | R24 | R11, R13, R17, R26 | — | — |
| D25_barcode_wrong_gtin — Barcode encodes another product's GTIN | photo | R24 | — | R05, R11, R13, R17, R24, R26 | — | — |
| D26_rda_wrong — %RDA for sodium wrong | pdf | R18 | R18 | R26 | — | — |
| D26_rda_wrong — %RDA for sodium wrong | png | R18 | R18 | R11, R13, R17, R26 | — | — |
| D26_rda_wrong — %RDA for sodium wrong | photo | R18 | R18 | R05, R11, R13, R17, R26 | — | — |
