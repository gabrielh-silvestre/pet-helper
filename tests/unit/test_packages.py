import importlib

import pytest

PACKAGES = [
    "cli",
    "conversation",
    "gateway",
    "guard",
    "evals",
    "knowledge",
    "orders",
    "catalog",
    "trace",
]


@pytest.mark.parametrize("package", PACKAGES)
def test_it_should_import_every_package(package):
    module = importlib.import_module(f"pet_helper.{package}")

    assert module.__name__ == f"pet_helper.{package}"
