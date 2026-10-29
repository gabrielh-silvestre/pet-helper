import csv
import unicodedata
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, computed_field

SKU_PATTERN = r"PX-\d{4}"


class Product(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    sku: str = Field(alias="SKU")
    name: str = Field(alias="nome")
    category: str = Field(alias="categoria")
    species: str = Field(alias="espécie")
    price: Decimal = Field(alias="preço")
    stock: int = Field(alias="estoque")
    description: str = Field(alias="descrição")

    @computed_field
    @property
    def in_stock(self) -> bool:
        return self.stock > 0


def load_products(path: Path) -> list[Product]:
    with open(path, encoding="utf-8", newline="") as file:
        return [
            Product.model_validate(
                {unicodedata.normalize("NFC", key): value for key, value in row.items()}
            )
            for row in csv.DictReader(file)
        ]
