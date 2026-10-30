from pathlib import Path

import pytest

from pet_helper.catalog import Catalog
from pet_helper.catalog.models import load_products

CATALOG_CSV = Path(__file__).resolve().parents[2] / "kit" / "catalog.csv"

SIMILAR_NAMES_QUERY = "Queria a ração Bigodin de salmão para castrados, pode me passar?"
OUT_OF_STOCK_QUERY = (
    "Vocês têm a Ração Focinhex Adultos Carne 15 kg? Quero comprar hoje."
)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog(CATALOG_CSV)


@pytest.fixture(scope="module")
def csv_skus() -> set[str]:
    return {product.sku for product in load_products(CATALOG_CSV)}


def test_it_should_rank_both_sizes_first_for_the_real_similar_names_query(catalog):
    results = catalog.search(SIMILAR_NAMES_QUERY)

    assert {product.sku for product in results[:2]} == {"PX-0001", "PX-0002"}


@pytest.mark.parametrize(
    "query",
    [
        "racao bigodin salmao",
        "racao bigodim castrados salmao",
    ],
)
def test_it_should_find_the_same_size_pair_for_partial_or_misspelled_queries(
    catalog, query
):
    results = catalog.search(query)

    assert {product.sku for product in results[:2]} == {"PX-0001", "PX-0002"}


@pytest.mark.parametrize("sku", ["PX-0001", "PX-0005", "PX-0048"])
def test_it_should_confirm_that_a_real_catalog_sku_exists(catalog, sku):
    exists = catalog.exists(sku)

    assert exists is True


def test_it_should_report_that_an_invented_px_sku_does_not_exist(catalog):
    exists = catalog.exists("PX-9999")

    assert exists is False


@pytest.mark.parametrize("sku", ["PX-12", "PX-00010", "px-0001", "PED-10001", ""])
def test_it_should_report_that_a_malformed_sku_does_not_exist(catalog, sku):
    exists = catalog.exists(sku)

    assert exists is False


@pytest.mark.parametrize("dog_filter", ["cão", "cao"])
def test_it_should_include_both_species_products_when_filtering_for_dogs(
    catalog, dog_filter
):
    results = catalog.search("comedouro", species=dog_filter)

    skus = {product.sku for product in results}
    assert "PX-0039" in skus
    assert all(product.species in {"cão", "ambos"} for product in results)


def test_it_should_include_both_species_products_when_filtering_for_cats(catalog):
    results = catalog.search("comedouro", species="gato")

    skus = {product.sku for product in results}
    assert {"PX-0039", "PX-0044"} <= skus
    assert all(product.species in {"gato", "ambos"} for product in results)


def test_it_should_filter_search_results_by_category(catalog):
    results = catalog.search("Bigodin", category="areia")

    assert {product.sku for product in results} == {"PX-0015", "PX-0016"}
    assert all(product.category == "areia" for product in results)


@pytest.mark.parametrize(
    ("species", "category"),
    [
        ("cão", "acessório"),
        ("cao", "acessorio"),
    ],
)
def test_it_should_combine_species_and_category_filters(catalog, species, category):
    results = catalog.search("Bigodin", species=species, category=category)

    assert [product.sku for product in results] == ["PX-0043"]


def test_it_should_return_out_of_stock_products_flagged_for_the_out_of_stock_query(
    catalog,
):
    results = catalog.search(OUT_OF_STOCK_QUERY)

    assert results[0].sku == "PX-0005"
    assert results[0].in_stock is False


@pytest.mark.parametrize("query", [SIMILAR_NAMES_QUERY, OUT_OF_STOCK_QUERY, "bolinha"])
def test_it_should_never_return_a_sku_outside_the_csv(catalog, csv_skus, query):
    results = catalog.search(query)

    assert results != []
    assert {product.sku for product in results} <= csv_skus


@pytest.mark.parametrize("query", ["", "   "])
def test_it_should_return_an_empty_list_for_a_blank_query(catalog, query):
    results = catalog.search(query)

    assert results == []


def test_it_should_return_an_empty_list_when_no_product_matches_the_query(catalog):
    results = catalog.search("gaiola para calopsita")

    assert results == []
