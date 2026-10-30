from pathlib import Path

import pytest

from pet_helper.knowledge import PolicyHit, PolicyIndex

KIT_POLICIES = Path(__file__).resolve().parents[2] / "kit" / "policies"


def write_policies(directory: Path, policies: dict[str, str]) -> Path:
    for name, content in policies.items():
        (directory / name).write_text(content, encoding="utf-8")
    return directory


@pytest.fixture(scope="module")
def kit_index() -> PolicyIndex:
    return PolicyIndex.load(KIT_POLICIES)


def test_it_should_extract_the_file_name_as_source_from_a_markdown_directory(tmp_path):
    policies_dir = write_policies(
        tmp_path,
        {
            "entrega.md": "# Prazo de entrega\n\nEntregamos pedidos expressos na capital.\n",
            "pagamento.md": "# Formas de pagamento\n\nAceitamos boleto bancário.\n",
            "cadastro.md": "# Cadastro\n\nAtualize seus dados cadastrais no site.\n",
        },
    )
    index = PolicyIndex.load(policies_dir)

    hits = index.search("expressos capital")

    assert len(hits) == 1
    assert isinstance(hits[0], PolicyHit)
    assert hits[0].source == "entrega.md"
    assert "expressos" in hits[0].text


def test_it_should_extract_the_first_h1_as_title(tmp_path):
    policies_dir = write_policies(
        tmp_path,
        {
            "entrega.md": "# Prazo de entrega\n\n## Regra geral\n\nEntregamos rápido.\n",
            "pagamento.md": "# Formas de pagamento\n\nAceitamos cartão.\n",
            "cadastro.md": "# Cadastro\n\nAtualize sua senha.\n",
        },
    )
    index = PolicyIndex.load(policies_dir)

    hits = index.search("entregamos rapido")

    assert hits[0].title == "Prazo de entrega"


def test_it_should_index_the_real_kit_policies(kit_index):
    hits = kit_index.search("reembolso pix cinco dias")

    assert hits[0].source == "reembolso.md"
    assert hits[0].title == "Reembolso"


@pytest.mark.parametrize("query", ["devolucao", "devolução"])
def test_it_should_find_the_return_policy_regardless_of_accents_in_the_query(
    kit_index, query
):
    hits = kit_index.search(query)

    assert "troca-e-devolucao.md" in [hit.source for hit in hits]


def test_it_should_match_an_inflected_query_term_to_the_singular_in_the_document(
    tmp_path,
):
    policies_dir = write_policies(
        tmp_path,
        {
            "embalagem.md": "# Embalagem\n\nO pacote lacrado protege o alimento.\n",
            "horario.md": "# Horário\n\nAtendemos de segunda a sexta.\n",
            "retirada.md": "# Retirada\n\nApresente documento com foto no balcão.\n",
        },
    )
    index = PolicyIndex.load(policies_dir)

    hits = index.search("pacotes lacrados")

    assert [hit.source for hit in hits] == ["embalagem.md"]


def test_it_should_return_at_most_two_hits_ordered_by_positive_score(tmp_path):
    policies_dir = write_policies(
        tmp_path,
        {
            "forte.md": "# Forte\n\nCupom promocional cupom promocional cupom exclusivo.\n",
            "medio.md": "# Médio\n\nCupom válido na primeira compra da loja.\n",
            "fraco.md": "# Fraco\n\nTexto longo sobre regras gerais de atendimento e uso de cupom.\n",
            "outro.md": "# Outro\n\nPolítica sobre transporte de cargas pesadas.\n",
        },
    )
    index = PolicyIndex.load(policies_dir)

    hits = index.search("cupom promocional", k=2)

    assert len(hits) == 2
    assert hits[0].source == "forte.md"
    assert hits[0].score >= hits[1].score > 0


def test_it_should_exclude_zero_score_documents_when_fewer_than_k_match(tmp_path):
    policies_dir = write_policies(
        tmp_path,
        {
            "unico.md": "# Único\n\nVacinação anual comprovada.\n",
            "frete.md": "# Frete\n\nEntrega grátis acima do valor mínimo.\n",
            "troca.md": "# Troca\n\nPrazo de sete dias corridos.\n",
        },
    )
    index = PolicyIndex.load(policies_dir)

    hits = index.search("vacinacao", k=2)

    assert len(hits) == 1
    assert hits[0].source == "unico.md"
    assert hits[0].score > 0


def test_it_should_return_an_empty_list_when_no_document_matches_the_query(kit_index):
    hits = kit_index.search("astronomia telescópio espacial")

    assert hits == []


@pytest.mark.parametrize(
    "query",
    [
        "de para com por em uma",
        "não está até você também",
        "nao esta ate voce tambem",
    ],
)
def test_it_should_return_an_empty_list_for_a_query_of_only_stopwords(kit_index, query):
    hits = kit_index.search(query)

    assert hits == []


@pytest.mark.parametrize("query", ["", "   "])
def test_it_should_return_an_empty_list_for_a_blank_query(kit_index, query):
    hits = kit_index.search(query)

    assert hits == []


def test_it_should_find_a_newly_added_policy_file_after_reloading(tmp_path):
    policies_dir = write_policies(
        tmp_path,
        {
            "frete.md": "# Frete\n\nEntrega rápida para todo o país.\n",
            "troca.md": "# Troca\n\nDevolução em até sete dias corridos.\n",
        },
    )
    before = PolicyIndex.load(policies_dir).search("aniversario pet brinde")
    (policies_dir / "brinde.md").write_text(
        "# Brinde de aniversário\n\nSeu pet ganha um brinde no mês de aniversário.\n",
        encoding="utf-8",
    )

    after = PolicyIndex.load(policies_dir).search("aniversario pet brinde")

    assert before == []
    assert [(hit.source, hit.title) for hit in after] == [
        ("brinde.md", "Brinde de aniversário")
    ]
