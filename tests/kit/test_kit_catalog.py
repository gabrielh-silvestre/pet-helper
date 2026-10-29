import csv
import re
import unicodedata
from decimal import Decimal
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

import pytest
from pet_helper.catalog.models import SKU_PATTERN

pytestmark = pytest.mark.integration

KIT = Path(__file__).resolve().parents[2] / "kit"
CATALOG = KIT / "catalog.csv"
PROMPTS = KIT / "PROMPTS.md"

HEADER = "SKU,nome,categoria,espécie,preço,estoque,descrição"
BRANDS = ["Focinhex", "Bigodin", "Latidão", "Miauzito", "Patacorp"]
REAL_BRANDS = [
    "Royal Canin",
    "Pedigree",
    "Whiskas",
    "Golden",
    "Premier",
    "Purina",
    "Pipicat",
    "Petz",
    "Cobasi",
    "Hill's",
    "Friskies",
]


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with CATALOG.open(encoding="utf-8", newline="") as handle:
        return [
            {nfc(key): nfc(value) for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def test_it_should_have_at_least_40_products(rows):
    assert len(rows) >= 40


def test_it_should_use_the_challenge_header_literally():
    with CATALOG.open(encoding="utf-8") as handle:
        first_line = handle.readline().strip()

    assert nfc(first_line) == nfc(HEADER)


def test_it_should_use_the_px_pattern_in_every_sku(rows):
    malformed = [r["SKU"] for r in rows if not re.fullmatch(SKU_PATTERN, r["SKU"])]

    assert malformed == []


def test_it_should_have_unique_skus(rows):
    skus = [r["SKU"] for r in rows]

    assert len(set(skus)) == len(skus)


@pytest.mark.parametrize("species", ["cão", "gato", "ambos"])
def test_it_should_cover_every_species(rows, species):
    assert species in {r["espécie"] for r in rows}


@pytest.mark.parametrize("category", ["ração", "areia"])
def test_it_should_cover_the_food_and_litter_categories(rows, category):
    assert category in {r["categoria"] for r in rows}


def test_it_should_have_at_least_3_products_out_of_stock(rows):
    out_of_stock = [r for r in rows if int(r["estoque"]) == 0]

    assert len(out_of_stock) >= 3


def test_it_should_have_an_out_of_stock_food_product(rows):
    out_of_stock_food = [
        r for r in rows if int(r["estoque"]) == 0 and r["categoria"] == "ração"
    ]

    assert out_of_stock_food != []


def test_it_should_have_at_least_2_pairs_of_similar_names(rows):
    names = [r["nome"] for r in rows]
    similar = [
        (a, b)
        for a, b in combinations(names, 2)
        if SequenceMatcher(None, a, b).ratio() >= 0.8
    ]

    assert len(similar) >= 2


@pytest.mark.parametrize("price", ["149.00", "249.00"])
def test_it_should_have_a_product_at_each_free_shipping_threshold(rows, price):
    prices = {Decimal(r["preço"]) for r in rows}

    assert Decimal(price) in prices


@pytest.mark.parametrize("field", ["nome", "descrição"])
def test_it_should_not_mention_a_real_pet_brand(rows, field):
    mentions = [
        (r["SKU"], brand)
        for r in rows
        for brand in REAL_BRANDS
        if brand.lower() in r[field].lower()
    ]

    assert mentions == []


@pytest.fixture(scope="module")
def catalog_section() -> str:
    text = nfc(PROMPTS.read_text(encoding="utf-8"))
    match = re.search(r"^## Catálogo\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, "PROMPTS.md has no '## Catálogo' section"
    return match.group(1)


def test_it_should_record_the_catalog_prompt_in_a_fenced_block(catalog_section):
    assert re.search(r"^(`{3,}|~{3,})", catalog_section, re.M)


def test_it_should_mention_the_sku_pattern_in_the_catalog_prompt(catalog_section):
    assert "PX-" in catalog_section


@pytest.mark.parametrize("brand", BRANDS)
def test_it_should_mention_every_fictional_brand_in_the_catalog_prompt(
    catalog_section, brand
):
    assert nfc(brand) in catalog_section
