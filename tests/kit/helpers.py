import re
import unicodedata
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parents[2] / "kit"

FOOD_AND_LITTER = frozenset({"ração", "areia"})

# expected_outcome -> (required terms, forbidden terms), already folded (no accent, lowercase)
OUTCOME_TERMS = {
    "regret_allowed": (
        ("dentro do prazo",),
        (
            "fora do prazo",
            "somente por defeito",
            "passou do prazo",
            "nao esta dentro do prazo",
        ),
    ),
    "regret_denied": (("defeito",), ("dentro do prazo",)),
    "defect_allowed": (("30 dias",), ("fora do prazo",)),
    "defect_denied": (("30 dias", "fora do prazo"), ()),
    "refund_pix_5_business_days": (("pix", "5 dias uteis"), ("faturas",)),
    "refund_card_2_invoices": (("cartao", "2 faturas"), ("5 dias uteis",)),
    "free_shipping": (
        ("frete foi gratis",),
        ("nao tem frete gratis", "nao foi gratis", "nao se qualificou"),
    ),
    "shipping_charged": (
        ("nao tem frete gratis",),
        ("o frete foi gratis", "o frete saiu gratis"),
    ),
    "cancel_allowed": (("cancel",), ("fluxo de devolucao",)),
    "cancel_denied_return_flow": (("devolucao",), ("foi cancelado",)),
}


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def fold(text: str) -> str:
    """NFC, lowercase and accent-free, the comparison form of the lexical net."""
    decomposed = unicodedata.normalize("NFD", nfc(text))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def section_of(text: str, heading: str, level: int = 2) -> str | None:
    """Body of a heading up to the next heading of the same or a higher level."""
    marks = "#" * level
    pattern = rf"^{marks} {re.escape(heading)}\n(.*?)(?=^#{{1,{level}}} |\Z)"
    match = re.search(pattern, text, re.M | re.S)
    return match.group(1) if match else None


def prompts_section(title: str) -> str:
    text = nfc((KIT_DIR / "PROMPTS.md").read_text(encoding="utf-8"))
    body = section_of(text, title)
    assert body is not None, f"PROMPTS.md has no '## {title}' section"
    return body
