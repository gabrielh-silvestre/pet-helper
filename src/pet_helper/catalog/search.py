import re
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

from pet_helper.catalog.models import Product, load_products
from pet_helper.text import PORTUGUESE_STOPWORDS, fold_text

_MIN_SCORE = 65.0
_BOTH_SPECIES = "ambos"
_SPECIES_WORDS = {
    "cao",
    "caes",
    "cachorro",
    "cachorros",
    "gato",
    "gatos",
    "gata",
    "gatas",
}
_NOISE_WORDS = frozenset(PORTUGUESE_STOPWORDS) | _SPECIES_WORDS


@dataclass(frozen=True)
class SearchOutcome:
    products: list[Product]
    name_matched: bool


def _product_words(text: str) -> str:
    words = re.findall(r"\w+", fold_text(text))
    return " ".join(word for word in words if word not in _NOISE_WORDS)


def _matches_filters(
    product: Product,
    folded_species: str | None,
    folded_category: str | None,
) -> bool:
    if folded_species is not None and fold_text(product.species) not in {
        folded_species,
        _BOTH_SPECIES,
    }:
        return False
    return folded_category is None or fold_text(product.category) == folded_category


def _similarity_score(folded_query: str, product: Product) -> float:
    searchable_text = fold_text(f"{product.name} {product.category}")
    return float(fuzz.token_set_ratio(folded_query, searchable_text))


class Catalog:
    def __init__(self, path: Path):
        self._products = tuple(load_products(path))
        self._skus = frozenset(product.sku for product in self._products)

    def exists(self, sku: str) -> bool:
        return sku in self._skus

    def search(
        self,
        text: str,
        species: str | None = None,
        category: str | None = None,
    ) -> list[Product]:
        return self.search_with_match(text, species, category).products

    def search_with_match(
        self,
        text: str,
        species: str | None = None,
        category: str | None = None,
    ) -> SearchOutcome:
        folded_species = fold_text(species) if species is not None else None
        folded_category = fold_text(category) if category is not None else None
        query = _product_words(text)

        scored = sorted(
            (
                (_similarity_score(query, product) if query else 0.0, product)
                for product in self._products
                if _matches_filters(product, folded_species, folded_category)
            ),
            key=lambda item: (-item[0], item[1].sku),
        )
        matched = [product for score, product in scored if score >= _MIN_SCORE]
        if matched:
            return SearchOutcome(matched, name_matched=True)
        # com filtro, a lista filtrada serve de alternativa; sem filtro não há o que oferecer
        if folded_species is not None or folded_category is not None:
            return SearchOutcome([product for _, product in scored], name_matched=False)
        return SearchOutcome([], name_matched=False)
