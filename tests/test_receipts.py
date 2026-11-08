"""Tests for the offline receipt reader (backend/receipts.py)."""
from datetime import date

import pytest

from backend.receipts import find_purchase_date, match_food, parse_receipt

TODAY = date(2026, 11, 8)

REWE_STYLE = """
REWE Markt GmbH
Musterstrasse 12, 10115 Berlin
Tel. 030 1234567
                                EUR
BIO VOLLMILCH 3,5% 1L          1,19 B
BANANEN                        1,53 B
  0,512 kg x 2,99 EUR/kg
HAEHNCHENBRUSTFILET 400G       5,49 B
BABYSPINAT 125G                1,79 B
TOMATEN STRAUCH                2,29 B
JOGHURT GRIECH. ART 500G       1,49 B
  2 Stk x 0,75
BARILLA SPAGHETTI 500G         1,79 B
TK ERBSEN 750G                 1,99 B
MINERALWASSER 1,5L             0,49 A
PFAND 0,25                     0,25 A
TRAGETASCHE                    0,20 A
RABATT JOGHURT                -0,30 B
ZAHNPASTA                      1,95 A
XYZ FEINKOST DIP               2,49 B
--------------------------------------
SUMME                     EUR 21,33
Geg. EC-Karte             EUR 21,33
MWST  7%  B  ...
05.11.2026  18:42   Bon-Nr. 1234
Vielen Dank fuer Ihren Einkauf
"""


def names(result):
    return [i["name"] for i in result["items"]]


def by_name(result, name):
    return next(i for i in result["items"] if i["name"] == name)


def test_reads_food_items_in_order():
    result = parse_receipt(REWE_STYLE, TODAY)
    assert names(result) == [
        "Milk", "Bananas", "Chicken breast", "Baby spinach", "Tomatoes",
        "Greek yogurt", "Pasta", "Frozen peas", "Mineralwasser", "Xyz Feinkost Dip",
    ]


def test_skips_deposit_bags_discounts_and_household_products():
    result = parse_receipt(REWE_STYLE, TODAY)
    skipped = " ".join(result["skipped"])
    for word in ("PFAND", "TRAGETASCHE", "RABATT", "ZAHNPASTA"):
        assert word in skipped
    assert not any("SUMME" in i["line"] for i in result["items"])


def test_uses_receipt_date_for_expiry():
    result = parse_receipt(REWE_STYLE, TODAY)
    assert result["purchase_date"] == "2026-11-05"
    assert by_name(result, "Baby spinach")["expiry_date"] == "2026-11-09"   # 4 days
    assert by_name(result, "Milk")["expiry_date"] == "2026-11-12"           # 7 days


def test_weight_and_multibuy_lines_set_quantity():
    result = parse_receipt(REWE_STYLE, TODAY)
    bananas = by_name(result, "Bananas")
    assert (bananas["quantity"], bananas["unit"]) == (0.512, "kg")
    yogurt = by_name(result, "Greek yogurt")
    assert (yogurt["quantity"], yogurt["unit"]) == (2, "pcs")


def test_pack_sizes_are_read():
    result = parse_receipt(REWE_STYLE, TODAY)
    assert (by_name(result, "Chicken breast")["quantity"], by_name(result, "Chicken breast")["unit"]) == (400, "g")
    assert (by_name(result, "Milk")["quantity"], by_name(result, "Milk")["unit"]) == (1, "L")


def test_frozen_items_get_long_shelf_life():
    peas = by_name(parse_receipt(REWE_STYLE, TODAY), "Frozen peas")
    assert peas["frozen"] is True
    assert peas["shelf_days"] == 90
    assert peas["suggested"] is True


def test_unknown_and_long_life_items_are_not_pre_ticked():
    result = parse_receipt(REWE_STYLE, TODAY)
    assert by_name(result, "Xyz Feinkost Dip")["confidence"] == "low"
    assert by_name(result, "Xyz Feinkost Dip")["suggested"] is False
    assert by_name(result, "Mineralwasser")["category"] == "Drinks"
    assert by_name(result, "Pasta")["suggested"] is False        # a year on the shelf
    assert by_name(result, "Baby spinach")["suggested"] is True


def test_brand_names_do_not_end_the_receipt():
    # "BARILLA" starts with "BAR" (cash payment) but must not stop parsing.
    result = parse_receipt("BARILLA PENNE 1,49\nEIER 10 ST 2,99\nSUMME 4,48", TODAY)
    assert names(result) == ["Pasta", "Eggs"]


@pytest.mark.parametrize("text,expected", [
    ("ORANGENSAFT 1L", "Orange juice"),
    ("APFELSAFT", "Juice"),
    ("PASSIERTE TOMATEN", "Passata"),
    ("H-MILCH 1,5%", "Long-life milk"),
    ("HAFERMILCH", "Oat milk"),
    ("Käse Gouda Scheiben", "Cheese"),
    ("KASE GOUDA", "Cheese"),
    ("Möhren 1kg", "Carrots"),
    ("BRÖTCHEN 6ST", "Bread rolls"),
    ("salatgurke", "Cucumber"),
    ("fresh salmon fillet", "Salmon"),
])
def test_dictionary_matches_german_and_english(text, expected):
    assert match_food(text).name == expected


def test_no_match_for_non_food():
    assert match_food("BLUMENTOPF") is None


def test_implausible_dates_fall_back_to_today():
    assert find_purchase_date("01.01.2020", TODAY) == TODAY       # too old
    assert find_purchase_date("31.12.2026", TODAY) == TODAY       # in the future
    assert find_purchase_date("no date here", TODAY) == TODAY
    assert find_purchase_date("Datum 07.11.26", TODAY) == date(2026, 11, 7)


def test_empty_text():
    assert parse_receipt("", TODAY)["items"] == []
