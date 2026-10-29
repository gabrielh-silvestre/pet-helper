# Manual check that kit/ has the shape the challenge asks for; never reads evals/.
import sys
import unicodedata
from pathlib import Path

from pet_helper.catalog.models import load_products
from pet_helper.orders.models import load_orders

HEADER = "SKU,nome,categoria,espécie,preço,estoque,descrição"
POLICIES, WORDS, PRODUCTS, ORDERS = (8, 10), (150, 400), (40, 60), (8, 10)


def _range(area: str, what: str, n: int, limits: tuple[int, int]) -> list[str]:
    lo, hi = limits
    return [] if lo <= n <= hi else [f"{area}: {what} {n}, expected {lo}-{hi}"]


def validate(kit_dir: Path = Path(__file__).parent) -> list[str]:
    policies = sorted((kit_dir / "policies").glob("*.md"))
    failures = _range("policies", "policy count", len(policies), POLICIES)
    for policy in policies:
        text = policy.read_text(encoding="utf-8")
        failures += _range("policies", f"{policy.name} words", len(text.split()), WORDS)
        if not text.startswith("# "):
            failures.append(f"policies: {policy.name} first line is not an H1 title")
    catalog = kit_dir / "catalog.csv"
    lines = (
        catalog.read_text(encoding="utf-8").splitlines() if catalog.is_file() else []
    )
    header = lines[0] if lines else ""
    products = []
    if unicodedata.normalize("NFC", header) != HEADER:
        failures.append(f"catalog: header is not {HEADER}")
    else:
        products = load_products(catalog)
        failures += _range("catalog", "product count", len(products), PRODUCTS)
    orders_file = kit_dir / "orders.json"
    orders = load_orders(orders_file).orders if orders_file.is_file() else []
    failures += _range("orders", "order count", len(orders), ORDERS)
    skus = {product.sku for product in products}
    missing = {item.sku for order in orders for item in order.items} - skus
    failures += [f"orders: {sku} not in the catalog" for sku in sorted(missing)]
    return failures


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    failures = validate(Path(args[0])) if args else validate()
    if failures:
        print(*failures, sep="\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
