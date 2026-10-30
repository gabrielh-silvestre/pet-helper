import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "pet_helper"


def _owning_package(path: Path) -> str | None:
    relative = path.relative_to(SOURCE_ROOT)
    return relative.parts[0] if len(relative.parts) > 1 else None


def _sibling_module_imports() -> list[str]:
    violations = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        owner = _owning_package(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom) or node.level != 0:
                continue
            parts = (node.module or "").split(".")
            if parts[0] == "pet_helper" and len(parts) > 2 and parts[1] != owner:
                violations.append(f"{path.relative_to(SOURCE_ROOT)}: {node.module}")
    return violations


def test_it_should_import_other_packages_only_through_their_init():
    violations = _sibling_module_imports()

    assert violations == []


def test_it_should_export_the_cpf_helpers_from_the_orders_package():
    from pet_helper.orders import is_valid_cpf, normalize_cpf

    assert is_valid_cpf("529.982.247-25") is True
    assert normalize_cpf("529.982.247-25") == "52998224725"


def test_it_should_keep_normalize_cpf_strict_about_the_canonical_format():
    from pet_helper.orders import normalize_cpf

    assert normalize_cpf("529 982 247 25") == ""


def test_it_should_export_the_sku_pattern_from_the_catalog_package():
    from pet_helper.catalog import SKU_PATTERN

    assert SKU_PATTERN == r"PX-\d{4}"
