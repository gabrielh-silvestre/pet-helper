from pathlib import Path

from rapidfuzz import fuzz

from pet_helper.catalog.models import Product, load_products
from pet_helper.text import fold_text

_MIN_SCORE = 65.0
_BOTH_SPECIES = "ambos"


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
        folded_query = fold_text(text).strip()
        if not folded_query:
            return []

        folded_species = fold_text(species) if species is not None else None
        folded_category = fold_text(category) if category is not None else None

        scored = [
            (score, product)
            for product in self._products
            if _matches_filters(product, folded_species, folded_category)
            and (score := _similarity_score(folded_query, product)) >= _MIN_SCORE
        ]
        scored.sort(key=lambda item: (-item[0], item[1].sku))
        return [product for _, product in scored]
