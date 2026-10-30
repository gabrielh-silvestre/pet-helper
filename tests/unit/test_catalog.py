from pathlib import Path

import pytest

from pet_helper import text
from pet_helper.catalog import Catalog
from pet_helper.catalog import search as catalog_search
from pet_helper.catalog.models import load_products

CATALOG_CSV = Path(__file__).resolve().parents[2] / "kit" / "catalog.csv"

SIMILAR_NAMES_QUERY = "Queria a ração Bigodin de salmão para castrados, pode me passar?"
OUT_OF_STOCK_QUERY = (
    "Vocês têm a Ração Focinhex Adultos Carne 15 kg? Quero comprar hoje."
)
CAT_FOOD_SKUS = ["PX-0001", "PX-0002", "PX-0003", "PX-0009", "PX-0010", "PX-0012"]
DOG_FOOD_SKUS = ["PX-0004", "PX-0005", "PX-0006", "PX-0007", "PX-0008", "PX-0011"]
CAT_LITTER_SKUS = [f"PX-{number:04d}" for number in range(13, 21)]


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


@pytest.mark.parametrize(
    ("query", "species", "category", "expected_skus"),
    [
        ("ração para gato", "gato", "ração", CAT_FOOD_SKUS),
        ("ração de gato", "gato", "ração", CAT_FOOD_SKUS),
        ("ração para cachorro", "cão", "ração", DOG_FOOD_SKUS),
        ("qual ração é boa para cachorro", "cão", "ração", DOG_FOOD_SKUS),
        ("Preciso de uma ração para o meu cachorro", "cão", "ração", DOG_FOOD_SKUS),
        ("areia para gato", "gato", "areia", CAT_LITTER_SKUS),
    ],
)
def test_it_should_return_the_filtered_products_for_a_natural_phrase_with_filters(
    catalog, query, species, category, expected_skus
):
    results = catalog.search(query, species=species, category=category)

    assert sorted(product.sku for product in results) == expected_skus


def test_it_should_return_only_cat_products_for_a_cat_phrase_with_the_species_filter(
    catalog,
):
    results = catalog.search("ração para gatos adultos", species="gato")

    assert results != []
    assert all(product.species in {"gato", "ambos"} for product in results)


def test_it_should_list_the_filtered_products_by_sku_when_the_query_has_only_noise(
    catalog,
):
    results = catalog.search("para o meu gato", species="gato", category="ração")

    assert [product.sku for product in results] == CAT_FOOD_SKUS


@pytest.mark.parametrize("query", ["para o meu", "gato", "meu cachorro"])
def test_it_should_return_an_empty_list_when_the_query_has_only_noise_and_no_filter(
    catalog, query
):
    results = catalog.search(query)

    assert results == []


@pytest.mark.parametrize(
    "query",
    [
        "Preciso de algo bom pro meu cachorro",
        "xpto",
        "qual ração é boa para cachorro",
    ],
)
def test_it_should_never_return_an_empty_list_for_a_noisy_phrase_with_filters(
    catalog, query
):
    results = catalog.search(query, species="cão", category="ração")

    assert results != []


def test_it_should_list_the_filtered_products_when_none_scores_above_the_cutoff(
    catalog,
):
    results = catalog.search("xpto", species="gato", category="ração")

    assert sorted(product.sku for product in results) == CAT_FOOD_SKUS


def test_it_should_keep_the_cutoff_when_a_filtered_product_scores_above_it(catalog):
    results = catalog.search("Bigodin", species="gato", category="acessório")

    assert {product.sku for product in results} == {"PX-0043", "PX-0044"}


def test_it_should_report_a_name_match_when_a_product_scores_above_the_cutoff(
    catalog,
):
    outcome = catalog.search_with_match("Bigodin", species="cão", category="acessório")

    assert [product.sku for product in outcome.products] == ["PX-0043"]
    assert outcome.name_matched is True


def test_it_should_report_no_name_match_when_the_results_are_the_filtered_fallback(
    catalog,
):
    outcome = catalog.search_with_match("xpto", species="gato", category="ração")

    assert sorted(product.sku for product in outcome.products) == CAT_FOOD_SKUS
    assert outcome.name_matched is False


def test_it_should_report_no_name_match_when_the_query_has_only_noise_and_filters(
    catalog,
):
    outcome = catalog.search_with_match(
        "para o meu gato", species="gato", category="ração"
    )

    assert [product.sku for product in outcome.products] == CAT_FOOD_SKUS
    assert outcome.name_matched is False


def test_it_should_report_no_name_match_for_an_empty_result(catalog):
    outcome = catalog.search_with_match("gaiola para calopsita")

    assert outcome.products == []
    assert outcome.name_matched is False


def test_it_should_return_the_same_products_from_search_and_search_with_match(
    catalog,
):
    products = catalog.search("racao bigodin salmao")

    outcome = catalog.search_with_match("racao bigodin salmao")

    assert outcome.products == products


def test_it_should_use_the_shared_portuguese_stopwords_constant(catalog):
    assert catalog_search.PORTUGUESE_STOPWORDS is text.PORTUGUESE_STOPWORDS
