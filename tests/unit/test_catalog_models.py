import re
import unicodedata
from decimal import Decimal

import pytest
from pet_helper.catalog.models import SKU_PATTERN, Product, load_products

HEADER = "SKU,nome,categoria,espécie,preço,estoque,descrição"
ROWS = [
    "PX-0042,Ração Focinhex Adulto 10kg,ração,cão,129.90,12,Ração seca para cães adultos",
    "PX-0043,Areia Miauzito Sílica 4kg,areia,gato,39.50,0,Areia de sílica para gatos",
]


def write_csv(tmp_path, header: str = HEADER, rows: list[str] = ROWS):
    path = tmp_path / "catalog.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def test_it_should_load_one_product_per_csv_row(tmp_path):
    products = load_products(write_csv(tmp_path))

    assert [p.sku for p in products] == ["PX-0042", "PX-0043"]


def test_it_should_map_the_csv_columns_to_english_fields(tmp_path):
    product = load_products(write_csv(tmp_path))[0]

    assert product.sku == "PX-0042"
    assert product.name == "Ração Focinhex Adulto 10kg"
    assert product.category == "ração"
    assert product.species == "cão"
    assert product.description == "Ração seca para cães adultos"


def test_it_should_parse_the_price_as_decimal(tmp_path):
    product = load_products(write_csv(tmp_path))[0]

    assert product.price == Decimal("129.90")


def test_it_should_parse_the_stock_as_int(tmp_path):
    product = load_products(write_csv(tmp_path))[0]

    assert product.stock == 12
    assert isinstance(product.stock, int)


def test_it_should_flag_a_product_with_stock_as_in_stock(tmp_path):
    product = load_products(write_csv(tmp_path))[0]

    assert product.in_stock is True


def test_it_should_flag_a_product_without_stock_as_not_in_stock(tmp_path):
    product = load_products(write_csv(tmp_path))[1]

    assert product.in_stock is False


def test_it_should_load_a_header_written_in_nfd(tmp_path):
    nfd_header = unicodedata.normalize("NFD", HEADER)
    assert nfd_header != HEADER

    products = load_products(write_csv(tmp_path, header=nfd_header))

    assert products[0].price == Decimal("129.90")
    assert products[0].species == "cão"


def test_it_should_build_a_product_by_field_name():
    product = Product(
        sku="PX-0001",
        name="Bola",
        category="brinquedo",
        species="ambos",
        price=Decimal("9.90"),
        stock=3,
        description="Bola de borracha",
    )

    assert product.in_stock is True


@pytest.mark.parametrize("sku", ["PX-0042", "PX-0000", "PX-9999"])
def test_it_should_match_a_sku_with_the_sku_pattern(sku):
    assert re.fullmatch(SKU_PATTERN, sku)


@pytest.mark.parametrize("sku", ["PX-042", "PX-00420", "px-0042", "PED-10001"])
def test_it_should_not_match_a_malformed_sku_with_the_sku_pattern(sku):
    assert not re.fullmatch(SKU_PATTERN, sku)
