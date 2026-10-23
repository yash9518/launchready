from app.util import gtin_valid, ocr_plausible_misread, parse_quantity, split_top_level, word_diff


def test_gtin_check_digit():
    assert gtin_valid("120000000036")          # UPC-A on the Esko sample carton
    assert gtin_valid("2001234567893")         # EAN-13 on the synthetic cookies
    assert not gtin_valid("2001234567894")     # one digit off
    assert not gtin_valid("12345")


def test_parse_quantity_units():
    q = parse_quantity("Net Wt. 200 g")
    assert q["base"] == 200 and q["base_unit"] == "g"
    assert parse_quantity("1.5 kg")["base"] == 1500
    assert parse_quantity("480g")["base"] == 480
    assert parse_quantity("no quantity here") is None


def test_ocr_misread_tolerance_never_hides_digit_changes():
    assert ocr_plausible_misread("Iodised Salt", "lodised Salt")       # I/l look-alike
    assert not ocr_plausible_misread("Rolled Oats (12%)", "Rolled Oats (21%)")
    assert not ocr_plausible_misread("Milk Solids", "Sugar")


def test_split_top_level_respects_brackets():
    items = split_top_level("Flour (Wheat, Oats), Sugar, Emulsifier [INS 322(i) (Soy Lecithin)]")
    assert items == ["Flour (Wheat, Oats)", "Sugar", "Emulsifier [INS 322(i) (Soy Lecithin)]"]


def test_word_diff_marks_changes():
    d = word_diff("Honey (9%)", "Honey (1%)")
    assert {"op": "delete", "text": "(9%)"} in d and {"op": "insert", "text": "(1%)"} in d
