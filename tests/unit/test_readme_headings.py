import re
from pathlib import Path

import pytest

from pet_helper.text import fold_text

README_PATH = Path(__file__).resolve().parents[2] / "README.md"

REQUIRED_HEADINGS = [
    "arquitetura",
    "escolha de modelo",
    "trade-offs",
    "como rodar o chat",
    "como rodar o eval",
    "smoke",
    "metrics",
    "rastros da conversa",
    "premissas de custo",
    "limitacoes do juiz",
    "loop de melhoria",
    "o que faria com mais tempo",
    "como a ia foi usada",
]

PLACEHOLDERS = ["todo", "tbd", "fixme", "xx%", "<preencher>", "a preencher"]


@pytest.fixture(scope="module")
def readme() -> str:
    return fold_text(README_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def headings(readme) -> list[str]:
    outside_code = re.sub(r"```.*?```", "", readme, flags=re.DOTALL)
    return [
        match.group(1).replace("`", "").strip()
        for match in re.finditer(r"^#{2,3}\s+(.+)$", outside_code, re.MULTILINE)
    ]


@pytest.mark.parametrize("required", REQUIRED_HEADINGS)
def test_it_should_have_a_heading_for_each_required_readme_topic(headings, required):
    assert any(required in heading for heading in headings)


@pytest.mark.parametrize("placeholder", PLACEHOLDERS)
def test_it_should_not_contain_a_placeholder_for_a_measured_number(readme, placeholder):
    assert placeholder not in readme
