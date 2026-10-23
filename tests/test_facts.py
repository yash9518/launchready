from app.facts import find_allergens


def test_allergen_keywords_and_exclusions():
    hits = find_allergens("Whole Wheat Flour, Milk Solids, Emulsifier (Soy Lecithin), Cocoa Butter, Coconut")
    assert set(hits) == {"gluten", "milk", "soy"}
    assert "tree_nuts" not in find_allergens("Coconut, Nutmeg")
    assert "milk" not in find_allergens("Cocoa butter, peanut butter")


def test_tree_nuts_in_us_ingredient_list():
    hits = find_allergens("Organic Wholegrain Oat Flakes (79%), Almonds, Pecans, Sunflower Seeds")
    assert "tree_nuts" in hits and "gluten" in hits


def test_loose_matching_only_when_asked():
    # OCR dropped a space: "Milkand Soy". Strict matching must not invent a hit,
    # loose matching (used only for OCR-read declarations) accepts the merged word.
    assert "milk" not in find_allergens("Contains: Gluten, Milkand Soy.")
    assert "milk" in find_allergens("Contains: Gluten, Milkand Soy.", loose=True)
