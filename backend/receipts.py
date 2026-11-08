"""Turn the text of a grocery receipt into fridge items.

The browser reads the receipt photo with free, offline text recognition (Tesseract.js) and
sends the raw text here. This module does the "understanding" part, without any paid AI:

* finds the purchase date printed on the receipt,
* reads item lines ("BIO VOLLMILCH 3,5% 1L   1,19 A"), including multi-buy lines
  ("2 x 0,99") and weighed items ("0,512 kg x 2,99 EUR/kg"),
* skips everything that isn't food: deposits (Pfand), bags, discounts, totals, payment
  and tax lines, household products,
* matches each item against a food dictionary (German receipt abbreviations and English
  names) to get a clean name, a fridge category and a typical shelf life,
* returns each item with an expiry date and a confidence level, so the app can ask the
  user to check anything it isn't sure about.

Shelf lives are typical values for unopened food kept correctly; the user can edit every date
before adding it, and the printed date on the pack always wins.
"""
import re
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Food:
    name: str
    category: str
    shelf_days: int
    keys: Tuple[str, ...]  # word prefixes, matched against the normalised receipt line


# Categories must match the frontend's CATEGORIES.
DAIRY, MEAT, VEG, FRUIT, BAKERY = "Dairy & Eggs", "Meat & Fish", "Vegetables", "Fruit", "Bakery"
COOKED, GRAINS, SAUCES, DRINKS, OTHER = "Cooked & Leftovers", "Grains & Pasta", "Sauces & Condiments", "Drinks", "Other"

# Order matters: more specific entries first ("H-MILCH" before "MILCH", "HAFERMILCH" before "HAFER").
FOODS: List[Food] = [
    # Checked first: products named after a fruit or vegetable they are made from
    Food("Orange juice", DRINKS, 7, ("ORANGENSAFT", "O-SAFT", "OSAFT", "ORANGE JUICE")),
    Food("Juice", DRINKS, 7, ("APFELSAFT", "SAFT", "JUICE", "SMOOTHIE")),
    Food("Passata", SAUCES, 365, ("PASSATA", "PASSIERTE", "TOMATENMARK", "STUECKIGE", "STÜCKIGE")),
    # Dairy & eggs
    Food("Oat milk", DRINKS, 120, ("HAFERMILCH", "HAFERDRINK", "OATMILK", "OAT MILK", "OATLY")),
    Food("Soy milk", DRINKS, 120, ("SOJAMILCH", "SOJADRINK", "SOY MILK")),
    Food("Long-life milk", DAIRY, 90, ("H-MILCH", "H MILCH", "HMILCH", "HALTB")),
    Food("Milk", DAIRY, 7, ("VOLLMILCH", "VOLLM", "FRISCHMILCH", "MILCH", "MILK", "WEIDEMILCH")),
    Food("Greek yogurt", DAIRY, 14, ("GRIECH", "GREEK")),
    Food("Yogurt", DAIRY, 14, ("JOGHURT", "JOGH", "JOGURT", "YOGURT", "YOGHURT", "YOG")),
    Food("Skyr", DAIRY, 14, ("SKYR",)),
    Food("Quark", DAIRY, 10, ("QUARK", "MAGERQ")),
    Food("Cream", DAIRY, 7, ("SCHLAGSAHNE", "SCHLAGS", "SAHNE", "CREME FRAICHE", "SCHMAND", "CREAM")),
    Food("Butter", DAIRY, 30, ("BUTTER", "MARKENBUTTER")),
    Food("Mozzarella", DAIRY, 7, ("MOZZARELLA", "MOZZ")),
    Food("Feta", DAIRY, 14, ("FETA", "HIRTENK")),
    Food("Cheese", DAIRY, 21, ("GOUDA", "EMMENT", "EMMENTALER", "CHEDDAR", "PARMES", "PARMIGIANO", "GRANA",
                               "BERGKAESE", "KAESE", "KASE", "KÄSE", "CHEESE", "SCHEIBENK", "REIBEK")),
    Food("Eggs", DAIRY, 21, ("EIER", "FREILANDEIER", "BODENH", "EGGS", "EGG")),
    # Meat & fish
    Food("Minced beef", MEAT, 1, ("HACKFL", "HACK", "MINCE", "GEMISCHTES")),
    Food("Chicken breast", MEAT, 2, ("HAEHNCHENBR", "HÄHNCHENBR", "HAEHN BRUST", "HÄHN BRUST", "CHICKEN BREAST")),
    Food("Chicken", MEAT, 2, ("HAEHNCHEN", "HÄHNCHEN", "HAEHN", "HÄHN", "HUHN", "CHICKEN", "GEFLUEGEL", "GEFLÜGEL")),
    Food("Turkey", MEAT, 2, ("PUTE", "PUTEN", "TURKEY")),
    Food("Beef", MEAT, 3, ("RIND", "RINDER", "BEEF", "STEAK")),
    Food("Pork", MEAT, 2, ("SCHWEIN", "PORK", "SCHNITZEL")),
    Food("Ham", MEAT, 7, ("SCHINKEN", "KOCHSCHINKEN", "HAM")),
    Food("Sausages", MEAT, 7, ("WUERSTCHEN", "WÜRSTCHEN", "WIENER", "BRATWURST", "WURST", "SALAMI", "SAUSAGE")),
    Food("Salmon", MEAT, 2, ("LACHS", "SALMON")),
    Food("Tuna (tinned)", OTHER, 365, ("THUNFISCH", "THUNF", "TUNA")),
    Food("Fish", MEAT, 2, ("FISCH", "FISH", "KABELJAU", "SEELACHS", "FORELLE")),
    Food("Tofu", OTHER, 10, ("TOFU",)),
    # Vegetables
    Food("Cherry tomatoes", VEG, 6, ("CHERRYTOM", "CHERRY TOM", "CHERRYROMA", "STRAUCHTOM")),
    Food("Tomatoes", VEG, 6, ("TOMATEN", "TOMATE", "TOMATO", "ROMATOM")),
    Food("Cucumber", VEG, 7, ("SALATGURKE", "GURKE", "GURKEN", "CUCUMBER")),
    Food("Bell pepper", VEG, 7, ("PAPRIKA", "SPITZPAPRIKA", "BELL PEPPER", "PEPPERS")),
    Food("Onions", VEG, 30, ("ZWIEBELN", "ZWIEBEL", "ONION", "SCHALOTTEN")),
    Food("Spring onions", VEG, 5, ("LAUCHZWIEBEL", "FRUEHLINGSZW", "FRÜHLINGSZW", "SPRING ONION")),
    Food("Garlic", VEG, 30, ("KNOBLAUCH", "KNOBL", "GARLIC")),
    Food("Carrots", VEG, 21, ("KAROTTEN", "KAROTTE", "MOEHREN", "MÖHREN", "MOEHRE", "MÖHRE", "CARROT")),
    Food("Potatoes", VEG, 30, ("KARTOFFELN", "KARTOFFEL", "KARTOF", "SPEISEKART", "POTATO")),
    Food("Sweet potatoes", VEG, 21, ("SUESSKART", "SÜSSKART", "SWEET POTATO")),
    Food("Baby spinach", VEG, 4, ("BABYSPINAT", "BLATTSPINAT", "SPINAT", "SPINACH")),
    Food("Salad", VEG, 4, ("EISBERG", "RUCOLA", "FELDSALAT", "KOPFSALAT", "ROMANA", "SALATMIX", "SALAT", "LETTUCE")),
    Food("Broccoli", VEG, 5, ("BROKKOLI", "BROCCOLI")),
    Food("Cauliflower", VEG, 6, ("BLUMENKOHL", "CAULIFLOWER")),
    Food("Zucchini", VEG, 7, ("ZUCCHINI", "ZUCCH", "COURGETTE")),
    Food("Mushrooms", VEG, 4, ("CHAMPIGNONS", "CHAMPIGN", "CHAMP", "PILZE", "MUSHROOM")),
    Food("Leek", VEG, 10, ("LAUCH", "PORREE", "LEEK")),
    Food("Avocado", FRUIT, 4, ("AVOCADO",)),
    # Fruit
    Food("Bananas", FRUIT, 5, ("BANANEN", "BANANE", "BANANA")),
    Food("Apples", FRUIT, 21, ("AEPFEL", "ÄPFEL", "APFEL", "APPLE")),
    Food("Lemons", FRUIT, 21, ("ZITRONEN", "ZITRONE", "LEMON", "LIMETTE", "LIME")),
    Food("Oranges", FRUIT, 14, ("ORANGEN", "ORANGE", "MANDARINE", "CLEMENTINE")),
    Food("Strawberries", FRUIT, 3, ("ERDBEEREN", "ERDBEER", "STRAWBERR")),
    Food("Grapes", FRUIT, 7, ("TRAUBEN", "WEINTRAUB", "GRAPE")),
    Food("Blueberries", FRUIT, 5, ("HEIDELBEER", "HEIDELB", "BLAUBEER", "BLUEBERR")),
    Food("Pears", FRUIT, 7, ("BIRNEN", "BIRNE", "PEAR")),
    Food("Kiwis", FRUIT, 10, ("KIWI",)),
    # Bakery
    Food("Toast bread", BAKERY, 7, ("TOASTBROT", "TOAST", "SANDWICH")),
    Food("Bread rolls", BAKERY, 2, ("BROETCHEN", "BRÖTCHEN", "SEMMEL", "BRETZEL", "BREZEL", "LAUGEN")),
    Food("Baguette", BAKERY, 2, ("BAGUETTE", "CIABATTA")),
    Food("Bread", BAKERY, 4, ("VOLLKORNBROT", "SAUERTEIG", "MISCHBROT", "BROT", "BREAD", "SOURDOUGH")),
    Food("Tortillas", BAKERY, 14, ("TORTILLA", "WRAPS", "WRAP")),
    # Cupboard (long shelf life, but useful for recipes)
    Food("Pasta", GRAINS, 365, ("SPAGHETTI", "SPAGH", "PENNE", "FUSILLI", "FARFALLE", "NUDELN", "NUDEL", "PASTA", "TORTELLINI")),
    Food("Rice", GRAINS, 365, ("BASMATI", "JASMINREIS", "LANGKORN", "REIS", "RICE")),
    Food("Flour", GRAINS, 365, ("WEIZENMEHL", "MEHL", "FLOUR")),
    Food("Oats", GRAINS, 180, ("HAFERFLOCKEN", "HAFERFL", "HAFER", "OATS", "MUESLI", "MÜSLI")),
    Food("Lentils", GRAINS, 365, ("LINSEN", "LENTIL")),
    Food("Chickpeas", GRAINS, 365, ("KICHERERBSEN", "KICHERERB", "CHICKPEA")),
    Food("Beans", GRAINS, 365, ("KIDNEY", "BOHNEN", "BEANS")),
    Food("Sweetcorn", GRAINS, 365, ("MAIS", "SWEETCORN", "CORN")),
    Food("Frozen peas", OTHER, 180, ("ERBSEN", "PEAS")),
    Food("Soy sauce", SAUCES, 365, ("SOJASAUCE", "SOJASOSSE", "SOY SAUCE", "SOJA")),
    Food("Ketchup", SAUCES, 180, ("KETCHUP",)),
    Food("Mustard", SAUCES, 180, ("SENF", "MUSTARD")),
    Food("Mayonnaise", SAUCES, 60, ("MAYO", "MAYONNAISE", "REMOULADE")),
    Food("Pesto", SAUCES, 14, ("PESTO",)),
    Food("Honey", SAUCES, 365, ("HONIG", "HONEY")),
    # Drinks
]

# Lines that are never food: deposits, bags, discounts, store/payment info, household products.
SKIP_KEYWORDS = (
    "PFAND", "LEERGUT", "EINWEG", "MEHRWEG", "RABATT", "COUPON", "GUTSCHEIN", "NACHLASS", "AKTION",
    "TUETE", "TÜTE", "TRAGETASCHE", "TASCHE", "BEUTEL",
    "MWST", "STEUER", "BRUTTO", "KASSE", "KASSIERER", "FILIALE", "UST-ID", "STEUERNR", "DANKE", "VIELEN", "WIEDERSEHEN", "OEFFNUNGS", "ÖFFNUNGS",
    "GIROCARD", "VISA", "MASTERCARD", "KONTAKTLOS", "TERMINAL", "BELEG",
    "RUECKGELD", "RÜCKGELD", "GEGEBEN", "PAYBACK", "PUNKTE",
    "KLOPAPIER", "TOILETTENP", "KUECHENROLLE", "KÜCHENROLLE", "TASCHENT", "SPUELMITTEL", "SPÜLMITTEL",
    "WASCHMITTEL", "WEICHSPUELER", "SHAMPOO", "DUSCH", "ZAHNPASTA", "ZAHNB", "DEO", "SEIFE",
    "BATTERIE", "MUELLBEUTEL", "MÜLLBEUTEL", "ALUFOLIE", "FRISCHHALTE", "KERZE",
)
# Short words that must match as whole words only (BON is not BONNE MAMAN, BAR is not BARILLA).
SKIP_WORDS = ("BON", "TEL", "UST", "NETTO", "MARKT", "TRANS", "TA-NR", "BNR")
# Once one of these appears (as a whole word), the item list is over.
END_KEYWORDS = ("SUMME", "ZU ZAHLEN", "ZWISCHENSUMME", "GESAMT", "TOTAL", "BETRAG", "BAR", "EC-KARTE", "EC KARTE", "KARTENZAHLUNG")
# Drinks that don't belong in a fridge tracker unless the user wants them.
LONG_LIFE_DRINKS = ("WASSER", "MINERAL", "SPRUDEL", "COLA", "FANTA", "SPRITE", "LIMO", "BIER", "PILS",
                    "WEIN", "SEKT", "KAFFEE", "COFFEE", "TEE ", "TEA ")

PRICE_RE = re.compile(r"(-?\d{1,4}[.,]\d{2})\s*(?:€|EUR)?\s*(?:[A-D]|\*|[A-D]\s*\*)?\s*$")
MULTI_RE = re.compile(r"^\s*(\d{1,2})\s*(?:STK|ST)?\.?\s*[xX×]\s*(\d{1,4}[.,]\d{2})")
WEIGHT_RE = re.compile(r"(\d{1,3}[.,]\d{1,3})\s*KG\s*[xX×*]\s*(\d{1,4}[.,]\d{2})\s*(?:€|EUR)?\s*/\s*KG", re.I)
SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(KG|G|GR|ML|L|LTR|ST|STK|STUECK|STÜCK)\b", re.I)
DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b")
ARTICLE_NO_RE = re.compile(r"^\s*\d{4,14}\s+")
SPACE_RE = re.compile(r"\s+")


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _normalise(line: str) -> str:
    """Uppercase, unify umlauts written as AE/OE/UE, and drop stray OCR symbols."""
    up = line.upper()
    up = up.replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE").replace("ß", "SS")
    up = re.sub(r"[|_~^`´'\"]", " ", up)
    return SPACE_RE.sub(" ", up).strip()


def _key_norm(key: str) -> str:
    return key.upper().replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE").replace("ß", "SS")


def match_food(text: str) -> Optional[Food]:
    """Find the first dictionary food whose key starts a word in the text."""
    norm = _normalise(text)
    for food in FOODS:
        for key in food.keys:
            k = re.escape(_key_norm(key))
            if re.search(rf"(?:^|[\s\-/.,0-9]){k}", norm):
                return food
    return None


def _has_keyword(norm: str, keywords) -> bool:
    return any(re.search(rf"(?:^|[\s\-/.,0-9]){re.escape(_key_norm(k.strip()))}", norm) for k in keywords)


def _has_word(norm: str, words) -> bool:
    return any(re.search(rf"(?:^|[^A-Z]){re.escape(_key_norm(w))}(?:[^A-Z]|$)", norm) for w in words)


def find_purchase_date(text: str, today: date) -> date:
    """Use the date printed on the receipt if it is plausible (within the last two weeks)."""
    for d, m, y in DATE_RE.findall(text):
        year = int(y) + 2000 if len(y) == 2 else int(y)
        try:
            found = date(year, int(m), int(d))
        except ValueError:
            continue
        if today - timedelta(days=14) <= found <= today:
            return found
    return today


def _clean_name(raw: str) -> str:
    name = ARTICLE_NO_RE.sub("", raw)
    name = PRICE_RE.sub("", name)
    name = SIZE_RE.sub("", name)
    name = re.sub(r"\d+[.,]?\d*\s*%", "", name)           # "3,5%"
    name = re.sub(r"\b(BIO|GUT&GUENSTIG|JA!|K-CLASSIC|REWE|ALDI|LIDL|EDEKA|NETTO|PENNY)\b", "", name, flags=re.I)
    name = re.sub(r"[^\wÄÖÜäöüß&\- ]", " ", name)
    name = SPACE_RE.sub(" ", name).strip(" -")
    return name.title()[:40] or "Unknown item"


def _size(raw: str) -> Tuple[float, str]:
    """Pack size printed in the name, e.g. '500G' -> (500, 'g'), '1L' -> (1, 'L'). Default: 1 pcs."""
    m = SIZE_RE.search(raw)
    if not m:
        return 1, "pcs"
    qty, unit = _num(m.group(1)), m.group(2).upper()
    if unit in ("G", "GR"):
        return qty, "g"
    if unit == "KG":
        return qty, "kg"
    if unit == "ML":
        return qty, "ml"
    if unit in ("L", "LTR"):
        return qty, "L"
    return qty, "pcs"


@dataclass
class ReceiptItem:
    name: str
    category: str
    quantity: float
    unit: str
    shelf_days: int
    expiry_date: str
    price: Optional[float]
    confidence: str          # "high": known food; "low": couldn't recognise it
    suggested: bool          # pre-ticked in the review screen
    frozen: bool
    line: str                # original receipt text, shown to the user for checking


def parse_receipt(text: str, today: Optional[date] = None) -> Dict[str, object]:
    today = today or date.today()
    purchase = find_purchase_date(text, today)
    items: List[ReceiptItem] = []
    skipped: List[str] = []
    lines = [l for l in (text or "").splitlines() if l.strip()]

    for raw in lines[:300]:
        norm = _normalise(raw)
        if _has_word(norm, END_KEYWORDS):
            break

        # "0,512 kg x 2,99 EUR/kg" and "2 x 0,99" describe the item on the line above.
        weight = WEIGHT_RE.search(norm)
        multi = MULTI_RE.match(norm)
        if (weight or multi) and items and not match_food(PRICE_RE.sub("", WEIGHT_RE.sub("", norm))):
            if weight:
                items[-1].quantity, items[-1].unit = round(_num(weight.group(1)), 3), "kg"
            else:
                items[-1].quantity, items[-1].unit = int(multi.group(1)), "pcs"
            continue

        price_match = PRICE_RE.search(norm)
        if not price_match:
            continue  # store header, address, blank-ish OCR noise
        price = _num(price_match.group(1))
        if price <= 0 or _has_keyword(norm, SKIP_KEYWORDS) or _has_word(norm, SKIP_WORDS):
            skipped.append(raw.strip())
            continue

        food = match_food(PRICE_RE.sub("", norm))
        frozen = bool(re.search(r"(?:^|\s)(TK|TIEFK|FROZEN)", norm))
        is_drink = _has_keyword(norm, LONG_LIFE_DRINKS)
        qty, unit = _size(norm)

        if food:
            shelf = 90 if frozen else food.shelf_days
            name = ("Frozen " + food.name.lower()) if frozen and not food.name.startswith("Frozen") else food.name
            category, confidence = food.category, "high"
            suggested = shelf <= 60 or frozen   # frozen food still belongs in the tracker
        else:
            shelf = 180 if (frozen or is_drink) else 5
            name = _clean_name(raw)
            category = DRINKS if is_drink else OTHER
            confidence, suggested = "low", False

        items.append(ReceiptItem(
            name=name, category=category, quantity=qty, unit=unit, shelf_days=shelf,
            expiry_date=(purchase + timedelta(days=shelf)).isoformat(), price=price,
            confidence=confidence, suggested=suggested, frozen=frozen, line=raw.strip(),
        ))

    return {
        "purchase_date": purchase.isoformat(),
        "items": [asdict(i) for i in items],
        "skipped": skipped,
    }
