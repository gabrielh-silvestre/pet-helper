"""The judge-label tooling over a saved round: seed, pool, judge-pool, present, record, tpr-tnr (no model).

Assumed public surface of `pet_helper.evals.labels` (also `python -m pet_helper.evals.labels <command>`):

- `main(argv=None, *, client=None, batch_client=None, cap_usd=Decimal(5), poll_seconds=..., timeout_seconds=...)
  -> int`: like `pet_helper.evals.judge.main`. `client` is a `JudgeClient`, `batch_client` a `BatchClient`
  (default: the Anthropic ones, built only by `judge-pool`). The confirmation is read from stdin
  (`ask_confirmation`, an explicit yes). A refusal is one line on stderr, nothing on stdout, a non-zero
  return and no file written. Held-out rounds are recognised by the path (a component named `held_out`)
  and `judge_pending` is read from `round.json`.
- `seed <round_dir> [--seed N]`, `pool <round_dir>`, `judge-pool <round_dir> --split dev|test
  [--judge sync|batch] [--force-new-validation]`, `present <round_dir> --n N`, `record <round_dir>
  <labels.json>` (a path to a JSON file holding a list of `{"id", "label"}`, as `annotate` takes a path) and
  `tpr-tnr <round_dir> --split dev|test`.
- the transcripts come from the Inspect log of the round (`*.eval`, the layout `run_round` writes); a run's
  `judge` entries say which criteria it feeds and, for `dont_know_accuracy`, the turn it judges; the slice
  of an item is `build_slice(criterion, transcript, turn)`. A policy-scope question (in scope or out of
  scope) is answered by the retrieved policy titles of the judged turn: some is in scope, none is out.
- files under `<round_dir>/labels/`:
  - `seeds.jsonl` (`seed`): one JSON object per line with `criterion`, `case_id`, `slice`, `seeded` (true) and
    `seed_kind`: `dismissive` | `rude` (tone), `handoff_removed` | `handoff_added`
    (escalation_judgment), `invented_answer` | `false_dont_know` (dont_know_accuracy).
  - `owner.jsonl` (written by the owner, read by `pool`): lines `{"criterion", "slice"}`, failure examples.
  - `pool.jsonl` (`pool`): lines with exactly `id`, `criterion` and `slice`; the id is opaque.
  - `key.json` (`pool`, then `judge-pool`): `{id: {"criterion", "split": "dev"|"test", "seeded": bool,
    "group": "natural"|"seeded"|"owner_written", "case_id", "seed_kind"?, "verdict"?}}`; `verdict`
    (`pass`|`fail`|`unable`) is written by `judge-pool` for the items of the split it ran.
  - `judge-runs.jsonl` (`judge-pool`, append-only): one line per run with `split`, `judge_prompt_sha256`,
    `forced` (bool), `items` and `cost_usd`.
  - `floor.json` (written by the owner before the test split; the plan's "unit report"): `{criterion:
    {"tpr": float, "tnr": float}}` for the three criteria, Wilson lower bounds.
  - `human.jsonl` (`record`, append-only): lines `{"id", "label": "pass"|"fail"}`.
- `tpr-tnr` lines the tests pin: `<criterion> <natural|seeded|owner_written> <TPR|TNR> <k>/<n> items,
  <c> cases, wilson95 <low>-<high>, floor <value>` (two decimals); `<criterion> <group> <metric>
  undefined` for an empty class; `<criterion> natural TNR seeded only` when no natural item was labelled
  fail; `<criterion> unable <k>/<n> items` (coverage); `<criterion> weak seeds <n>`.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import anthropic
import httpx
import pytest

from pet_helper.evals import judge as judge_module
from pet_helper.evals import labels as labels_module
from pet_helper.evals.judge import (
    CRITERIA,
    FewShotExample,
    build_prompt,
    judge_prompt_sha256,
)
from pet_helper.evals.labels import main
from pet_helper.orders.api import FAILURE_ENV
from pet_helper.trace import UnknownPriceError
from tests.unit.test_eval_judge import FakeBatchClient, FakeClient
from tests.unit.test_eval_round import play
from tests.unit.test_eval_task import (
    KIT,  # noqa: F401  (kept next to play for readers of the fixtures)
    POLICY_REPLY,
    ROOT,
    Gateways,
    JudgeStub,
    _isolated_environment,  # noqa: F401  (autouse: keeps the environment clean)
    call,
    make_case,
)

HELD_OUT = "held_out"
FLOOR = {name: {"tpr": 0.7, "tnr": 0.8} for name in CRITERIA}
SEED_KINDS = {
    "tone": {"dismissive", "rude"},
    "escalation_judgment": {"handoff_removed", "handoff_added"},
    "dont_know_accuracy": {"invented_answer", "false_dont_know"},
}
ALL_SEED_KINDS = set().union(*SEED_KINDS.values())
JUDGED = list(CRITERIA)
ESCALATION_REPLY = "Vou encaminhar seu atendimento para um atendente humano."
FORBIDDEN_IN_BLIND_OUTPUT = re.compile(
    r"\b(seed|seeded|pass|fail|critique)\b", re.IGNORECASE
)


@pytest.fixture(autouse=True)
def _labels_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.delenv("PET_HELPER_JUDGE_MODEL", raising=False)


# the baseline round: 26 judged cases of three kinds, built once and copied per test


def baseline_cases() -> tuple[list[Any], dict[str, list[Any]], dict[str, str]]:
    cases, scripts, users = [], {}, {}

    def add(case_id: str, script: list[Any]) -> None:
        users[case_id] = f"Pergunta {len(cases) + 1:02d} do cliente"
        cases.append(make_case(case_id, (users[case_id],), judge=JUDGED))
        scripts[case_id] = script

    for number in range(6):
        add(
            f"esc-{number}",
            [call("escalate_to_human", {"reason": "explicit_request"})],
        )
    for number in range(10):
        add(
            f"policy-{number}",
            [call("search_policies", {"query": "devolução"}), POLICY_REPLY],
        )
    for number in range(10):
        add(f"dk-{number}", [f"Não encontrei essa informação {number}."])
    return cases, scripts, users


CASES, SCRIPTS, USERS = baseline_cases()
CASE_PREFIXES = ("esc-", "policy-", "dk-")


@pytest.fixture(scope="module")
def base_round(tmp_path_factory) -> Path:
    patcher = pytest.MonkeyPatch()
    patcher.chdir(ROOT)
    for name in (
        "PET_HELPER_KIT_DIR",
        "PET_HELPER_TODAY",
        "PET_HELPER_TRACE_CONTENT",
        FAILURE_ENV,
    ):
        patcher.delenv(name, raising=False)
    try:
        root = tmp_path_factory.mktemp("base")
        play(root, CASES, Gateways(SCRIPTS), judge_client=JudgeStub())
    finally:
        patcher.undo()
    return root / "round-1"


@pytest.fixture(scope="module")
def seeded_base(tmp_path_factory, base_round) -> Path:
    directory = tmp_path_factory.mktemp("seeded") / "round-1"
    shutil.copytree(base_round, directory)
    assert main(["seed", str(directory)]) == 0
    return directory


@pytest.fixture(scope="module")
def pooled_base(tmp_path_factory, seeded_base) -> Path:
    directory = tmp_path_factory.mktemp("pooled") / "round-1"
    shutil.copytree(seeded_base, directory)
    assert main(["pool", str(directory)]) == 0
    return directory


@pytest.fixture
def round_dir(tmp_path, base_round) -> Path:
    directory = tmp_path / "results" / "round-1"
    shutil.copytree(base_round, directory)
    return directory


@pytest.fixture
def pooled(tmp_path, pooled_base) -> Path:
    directory = tmp_path / "results" / "round-1"
    shutil.copytree(pooled_base, directory)
    return directory


# fakes and helpers


class NoModel:
    """Client that fails the test when the command under test reaches for a model."""

    model = "no-model"

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        raise AssertionError("a model call was made")

    def submit(self, items: Sequence[Any]) -> str:
        raise AssertionError("a batch was submitted")

    def collect(self, batch_id: str, item_ids: Sequence[str]) -> Any:
        raise AssertionError("a batch was collected")


@pytest.fixture
def cli(capsys, monkeypatch) -> Callable[..., tuple[int, str, str]]:
    def invoke(*argv: Any, stdin: str = "yes\n", **kwargs: Any):
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        kwargs.setdefault("client", NoModel())
        kwargs.setdefault("batch_client", NoModel())
        code = main([str(arg) for arg in argv], **kwargs)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return invoke


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def labels_of(directory: Path) -> Path:
    return directory / "labels"


def read_pool(directory: Path) -> list[dict[str, Any]]:
    return read_jsonl(labels_of(directory) / "pool.jsonl")


def read_key(directory: Path) -> dict[str, dict[str, Any]]:
    return json.loads((labels_of(directory) / "key.json").read_text(encoding="utf-8"))


def read_seeds(directory: Path, criterion: str, case_id: str) -> list[dict[str, Any]]:
    return [
        seed
        for seed in read_jsonl(labels_of(directory) / "seeds.jsonl")
        if seed["criterion"] == criterion and seed["case_id"] == case_id
    ]


def items_of(directory: Path, split: str) -> list[dict[str, Any]]:
    key = read_key(directory)
    return [item for item in read_pool(directory) if key[item["id"]]["split"] == split]


def write_floor(directory: Path, floor: Mapping[str, Any] = FLOOR) -> None:
    labels_of(directory).mkdir(exist_ok=True)
    (labels_of(directory) / "floor.json").write_text(json.dumps(floor))


def write_labels_file(path: Path, labels: Sequence[Mapping[str, str]]) -> Path:
    path.write_text(json.dumps(list(labels)), encoding="utf-8")
    return path


def one_line(text: str) -> bool:
    return len(text.strip().splitlines()) == 1


# seed


def test_it_should_mark_every_seed_as_seeded_with_its_kind_and_case(seeded_base):
    seeds = read_jsonl(labels_of(seeded_base) / "seeds.jsonl")

    assert seeds
    assert all(
        seed["seeded"] is True
        and seed["seed_kind"]
        and seed["case_id"]
        and seed["slice"]
        for seed in seeds
    )


def test_it_should_seed_each_criterion_with_its_own_kinds(seeded_base):
    seeds = read_jsonl(labels_of(seeded_base) / "seeds.jsonl")

    kinds = {
        criterion: {s["seed_kind"] for s in seeds if s["criterion"] == criterion}
        for criterion in CRITERIA
    }

    assert kinds == SEED_KINDS


def test_it_should_replace_the_final_reply_of_a_tone_seed(seeded_base):
    seeds = read_seeds(seeded_base, "tone", "dk-3")

    assert seeds
    for seed in seeds:
        assert USERS["dk-3"] in seed["slice"]
        assert "Não encontrei essa informação 3." not in seed["slice"]


def test_it_should_remove_the_handoff_in_an_escalation_seed_of_an_escalated_case(
    seeded_base,
):
    seeds = read_seeds(seeded_base, "escalation_judgment", "esc-0")

    assert {seed["seed_kind"] for seed in seeds} == {"handoff_removed"}
    assert all("Escalonamento: nenhum" in seed["slice"] for seed in seeds)


def test_it_should_add_a_handoff_in_an_escalation_seed_of_an_ordinary_complaint(
    seeded_base,
):
    seeds = read_seeds(seeded_base, "escalation_judgment", "dk-0")

    assert {seed["seed_kind"] for seed in seeds} == {"handoff_added"}
    assert all(
        "Escalonamento:" in seed["slice"]
        and "Escalonamento: nenhum" not in seed["slice"]
        for seed in seeds
    )


def test_it_should_replace_an_out_of_scope_answer_by_an_invented_one(seeded_base):
    seeds = read_seeds(seeded_base, "dont_know_accuracy", "dk-1")

    assert {seed["seed_kind"] for seed in seeds} == {"invented_answer"}
    for seed in seeds:
        assert USERS["dk-1"] in seed["slice"]
        assert "Políticas recuperadas: nenhuma" in seed["slice"]
        assert "Não encontrei essa informação 1." not in seed["slice"]


def test_it_should_replace_an_in_scope_answer_by_a_false_dont_know(seeded_base):
    seeds = read_seeds(seeded_base, "dont_know_accuracy", "policy-1")

    assert {seed["seed_kind"] for seed in seeds} == {"false_dont_know"}
    for seed in seeds:
        assert "não sei" in seed["slice"].lower()
        assert "Troca e devolução" in seed["slice"]
        assert "Você pode devolver em 7 dias" not in seed["slice"]


def test_it_should_write_byte_identical_seeds_for_a_fixed_seed(
    tmp_path, base_round, cli
):
    first, second = tmp_path / "a" / "round-1", tmp_path / "b" / "round-1"
    shutil.copytree(base_round, first)
    shutil.copytree(base_round, second)

    cli("seed", first, "--seed", 7)
    cli("seed", second, "--seed", 7)

    assert (labels_of(first) / "seeds.jsonl").read_bytes() == (
        labels_of(second) / "seeds.jsonl"
    ).read_bytes()


def test_it_should_make_no_model_call_while_seeding_and_pooling(round_dir, cli):
    client = FakeClient()
    batch = FakeBatchClient()

    seeded = cli("seed", round_dir, client=client, batch_client=batch)
    pooled = cli("pool", round_dir, client=client, batch_client=batch)

    assert (seeded[0], pooled[0]) == (0, 0)
    assert client.calls == [] and batch.submitted == []


# pool


def test_it_should_write_a_blind_pool_of_id_criterion_and_slice_only(pooled_base):
    pool = read_pool(pooled_base)

    assert pool
    assert all(set(item) == {"id", "criterion", "slice"} for item in pool)


def test_it_should_give_the_items_unique_opaque_ids(pooled_base):
    ids = [item["id"] for item in read_pool(pooled_base)]

    assert len(set(ids)) == len(ids)
    assert not any(
        token in item_id for item_id in ids for token in (*CASE_PREFIXES, "seed")
    )


def test_it_should_key_every_pool_item_with_split_seeded_flag_group_and_case(
    pooled_base,
):
    pool, key = read_pool(pooled_base), read_key(pooled_base)

    assert set(key) == {item["id"] for item in pool}
    for item in pool:
        entry = key[item["id"]]
        assert entry["criterion"] == item["criterion"]
        assert entry["split"] in {"dev", "test"}
        assert entry["case_id"].startswith(CASE_PREFIXES)
        assert entry["group"] == ("seeded" if entry["seeded"] else "natural")
        assert entry["seeded"] is (entry["group"] == "seeded")


def test_it_should_build_about_fifty_items_per_criterion(pooled_base):
    key = read_key(pooled_base)

    sizes = {c: sum(1 for e in key.values() if e["criterion"] == c) for c in CRITERIA}

    assert all(40 <= size <= 55 for size in sizes.values()), sizes


def test_it_should_balance_natural_and_seeded_items_near_fifty_fifty(pooled_base):
    key = read_key(pooled_base)

    for criterion in CRITERIA:
        entries = [e for e in key.values() if e["criterion"] == criterion]
        share = sum(e["seeded"] for e in entries) / len(entries)
        assert 0.4 <= share <= 0.6, (criterion, share)


def test_it_should_split_each_criterion_about_forty_percent_dev(pooled_base):
    key = read_key(pooled_base)

    for criterion in CRITERIA:
        entries = [e for e in key.values() if e["criterion"] == criterion]
        share = sum(e["split"] == "dev" for e in entries) / len(entries)
        assert 0.2 <= share <= 0.6, (criterion, share)


def test_it_should_keep_every_item_of_a_case_in_the_same_split(pooled_base):
    splits: dict[str, set[str]] = {}
    for entry in read_key(pooled_base).values():
        splits.setdefault(entry["case_id"], set()).add(entry["split"])

    assert all(len(found) == 1 for found in splits.values()), splits


def test_it_should_collapse_identical_final_answers_of_a_case_into_one_item(
    tmp_path, cli
):
    cases = [
        make_case("same", ("Pergunta única",), judge=["tone"]),
        make_case("varied", ("Pergunta variada",), judge=["tone"]),
    ]
    gateways = Gateways(
        {
            "same": ["Resposta única."],
            "varied": lambda epoch: ["Resposta B."] if epoch == 1 else ["Resposta A."],
        }
    )
    play(tmp_path / "small", cases, gateways, judge_client=JudgeStub())
    directory = tmp_path / "small" / "round-1"
    cli("seed", directory)

    cli("pool", directory)

    natural = [e["case_id"] for e in read_key(directory).values() if not e["seeded"]]
    assert sorted(natural) == ["same", "varied", "varied"]


def test_it_should_slice_the_turn_a_dont_know_entry_names(tmp_path, cli):
    case = make_case(
        "turns-1",
        ("Primeira pergunta", "Segunda pergunta"),
        judge=[{"criterion": "dont_know_accuracy", "turn": 1}],
    )
    play(
        tmp_path / "small",
        [case],
        Gateways({"turns-1": ["Resp um.", "Resp dois."]}),
        judge_client=JudgeStub(),
    )
    directory = tmp_path / "small" / "round-1"
    cli("seed", directory)

    cli("pool", directory)

    key = read_key(directory)
    [natural] = [i for i in read_pool(directory) if not key[i["id"]]["seeded"]]
    assert "Primeira pergunta" in natural["slice"]
    assert "Resp um." in natural["slice"]
    assert "Segunda pergunta" not in natural["slice"]


def test_it_should_print_no_verdict_critique_or_seed_flag_while_pooling(
    tmp_path, seeded_base, cli
):
    directory = tmp_path / "results" / "round-1"
    shutil.copytree(seeded_base, directory)

    code, out, err = cli("pool", directory)

    assert code == 0
    assert not FORBIDDEN_IN_BLIND_OUTPUT.search(out + err)


def test_it_should_write_the_same_pool_for_the_same_round(tmp_path, seeded_base, cli):
    first, second = tmp_path / "a" / "round-1", tmp_path / "b" / "round-1"
    shutil.copytree(seeded_base, first)
    shutil.copytree(seeded_base, second)

    cli("pool", first)
    cli("pool", second)

    for name in ("pool.jsonl", "key.json"):
        assert (labels_of(first) / name).read_bytes() == (
            labels_of(second) / name
        ).read_bytes()


def test_it_should_pool_owner_written_failures_as_a_third_group(
    tmp_path, seeded_base, cli
):
    directory = tmp_path / "results" / "round-1"
    shutil.copytree(seeded_base, directory)
    written = "Cliente: Cadê meu pedido?\nAssistente: Não é problema meu."
    (labels_of(directory) / "owner.jsonl").write_text(
        json.dumps({"criterion": "tone", "slice": written}) + "\n", encoding="utf-8"
    )

    cli("pool", directory)

    key = read_key(directory)
    [item] = [i for i in read_pool(directory) if i["slice"] == written]
    assert set(item) == {"id", "criterion", "slice"}
    assert (key[item["id"]]["group"], key[item["id"]]["seeded"]) == (
        "owner_written",
        False,
    )


@pytest.fixture
def doubled_seeds(tmp_path, seeded_base) -> Path:
    directory = tmp_path / "results" / "round-1"
    shutil.copytree(seeded_base, directory)
    seeds = labels_of(directory) / "seeds.jsonl"
    seeds.write_text(seeds.read_text(encoding="utf-8") * 2, encoding="utf-8")
    return directory


def test_it_should_list_a_seed_repeated_in_seeds_jsonl_once_in_the_pool(
    doubled_seeds, cli
):
    cli("pool", doubled_seeds)

    ids = [item["id"] for item in read_pool(doubled_seeds)]
    assert len(set(ids)) == len(ids)


def test_it_should_count_only_unique_ids_in_the_pooled_total(doubled_seeds, cli):
    _, out, _ = cli("pool", doubled_seeds)

    unique = {item["id"] for item in read_pool(doubled_seeds)}
    assert f"Pooled {len(unique)} items" in out


def test_it_should_cap_each_group_by_unique_ids(doubled_seeds, pooled_base, cli):
    cli("pool", doubled_seeds)

    for name in ("pool.jsonl", "key.json"):
        assert (labels_of(doubled_seeds) / name).read_bytes() == (
            labels_of(pooled_base) / name
        ).read_bytes()


def test_it_should_judge_a_seed_repeated_in_seeds_jsonl_once(doubled_seeds, cli):
    cli("pool", doubled_seeds)
    client = FakeClient()
    dev_ids = {item["id"] for item in items_of(doubled_seeds, "dev")}

    judge_pool(cli, doubled_seeds, "dev", client=client)

    assert len(client.calls) == len(dev_ids)


# judge-pool


def judge_pool(cli, directory: Path, split: str, *extra: Any, **kwargs: Any):
    kwargs.setdefault("client", FakeClient())
    return cli("judge-pool", directory, "--split", split, *extra, **kwargs)


def test_it_should_print_the_cost_estimate_before_asking_for_confirmation(pooled, cli):
    _, out, _ = judge_pool(cli, pooled, "dev", stdin="no\n")

    assert "US$" in out


@pytest.mark.parametrize("typed", ["\n", "no\n", "n\n", "maybe\n", ""])
def test_it_should_call_no_model_on_any_answer_but_an_explicit_yes(pooled, cli, typed):
    client = FakeClient()

    code, _, _ = judge_pool(cli, pooled, "dev", stdin=typed, client=client)

    assert code != 0
    assert client.calls == []
    assert all("verdict" not in entry for entry in read_key(pooled).values())


def test_it_should_judge_every_dev_item_with_the_prompt_of_its_criterion(pooled, cli):
    client = FakeClient()
    dev = items_of(pooled, "dev")

    code, _, _ = judge_pool(cli, pooled, "dev", client=client)

    assert code == 0
    assert sorted(client.calls) == sorted(
        build_prompt(item["criterion"], item["slice"]) for item in dev
    )


def test_it_should_store_the_verdict_of_each_dev_item_in_the_key(pooled, cli):
    judge_pool(cli, pooled, "dev")

    key = read_key(pooled)
    dev_ids = {item["id"] for item in items_of(pooled, "dev")}
    assert {i for i, entry in key.items() if entry.get("verdict") == "pass"} == dev_ids


def test_it_should_leave_the_test_split_unjudged_when_judging_dev(pooled, cli):
    judge_pool(cli, pooled, "dev")

    test_ids = {item["id"] for item in items_of(pooled, "test")}
    key = read_key(pooled)
    assert all("verdict" not in key[item_id] for item_id in test_ids)


def test_it_should_store_an_unable_verdict_as_unable(pooled, cli):
    client = FakeClient({"critique": "Cannot tell.", "verdict": "unable"})

    judge_pool(cli, pooled, "dev", client=client)

    verdicts = {e["verdict"] for e in read_key(pooled).values() if "verdict" in e}
    assert verdicts == {"unable"}


def test_it_should_call_no_model_when_the_cap_cannot_cover_one_call(pooled, cli):
    client = FakeClient()

    code, _, _ = judge_pool(
        cli, pooled, "dev", client=client, cap_usd=Decimal("0.0001")
    )

    assert code != 0
    assert client.calls == []


def test_it_should_submit_one_batch_item_per_pool_item_of_the_split(pooled, cli):
    batch = FakeBatchClient()
    dev_ids = {item["id"] for item in items_of(pooled, "dev")}

    code, _, _ = cli(
        "judge-pool",
        pooled,
        "--split",
        "dev",
        "--judge",
        "batch",
        batch_client=batch,
        poll_seconds=0,
        timeout_seconds=5,
    )

    assert code == 0
    [submitted] = batch.submitted
    assert {item.item_id for item in submitted} == dev_ids


def test_it_should_store_the_batch_verdicts_in_the_key(pooled, cli):
    cli(
        "judge-pool",
        pooled,
        "--split",
        "dev",
        "--judge",
        "batch",
        batch_client=FakeBatchClient(),
        poll_seconds=0,
        timeout_seconds=5,
    )

    dev_ids = {item["id"] for item in items_of(pooled, "dev")}
    key = read_key(pooled)
    assert {i for i, entry in key.items() if entry.get("verdict") == "pass"} == dev_ids


def test_it_should_allow_the_dev_split_to_run_again(pooled, cli):
    client = FakeClient()
    dev = items_of(pooled, "dev")

    first = judge_pool(cli, pooled, "dev", client=client)
    second = judge_pool(cli, pooled, "dev", client=client)

    assert (first[0], second[0]) == (0, 0)
    assert len(client.calls) == 2 * len(dev)


def test_it_should_refuse_the_test_split_without_a_written_floor(pooled, cli):
    client = FakeClient()

    code, _, _ = judge_pool(cli, pooled, "test", client=client)

    assert code != 0
    assert client.calls == []


def test_it_should_refuse_the_test_split_when_the_floor_lacks_a_criterion(pooled, cli):
    write_floor(pooled, {"tone": FLOOR["tone"]})
    client = FakeClient()

    code, _, _ = judge_pool(cli, pooled, "test", client=client)

    assert code != 0
    assert client.calls == []


def test_it_should_judge_the_test_split_once_the_floor_is_written(pooled, cli):
    write_floor(pooled)
    client = FakeClient()

    code, _, _ = judge_pool(cli, pooled, "test", client=client)

    assert code == 0
    assert len(client.calls) == len(items_of(pooled, "test"))


def test_it_should_record_the_prompt_hash_of_the_test_run(pooled, cli):
    write_floor(pooled)

    judge_pool(cli, pooled, "test")

    [run] = read_jsonl(labels_of(pooled) / "judge-runs.jsonl")
    assert (run["split"], run["judge_prompt_sha256"], run["forced"]) == (
        "test",
        judge_prompt_sha256(),
        False,
    )


def test_it_should_refuse_a_second_test_run_with_the_same_prompt_hash(pooled, cli):
    write_floor(pooled)
    client = FakeClient()
    judge_pool(cli, pooled, "test", client=client)
    calls_before = len(client.calls)

    code, _, _ = judge_pool(cli, pooled, "test", client=client)

    assert code != 0
    assert len(client.calls) == calls_before


def test_it_should_refuse_a_test_run_with_a_changed_hash_without_the_force_flag(
    pooled, cli, monkeypatch
):
    write_floor(pooled)
    client = FakeClient()
    judge_pool(cli, pooled, "test", client=client)
    calls_before = len(client.calls)
    changed = [FewShotExample("tone", "an unrelated slice", "Note.", "pass")]
    monkeypatch.setattr(judge_module, "FEW_SHOT_EXAMPLES", changed)

    code, _, _ = judge_pool(cli, pooled, "test", client=client)

    assert code != 0
    assert len(client.calls) == calls_before


def test_it_should_run_a_changed_hash_with_the_force_flag_and_record_it(
    pooled, cli, monkeypatch
):
    write_floor(pooled)
    client = FakeClient()
    judge_pool(cli, pooled, "test", client=client)
    changed = [FewShotExample("tone", "an unrelated slice", "Note.", "pass")]
    monkeypatch.setattr(judge_module, "FEW_SHOT_EXAMPLES", changed)

    code, _, _ = judge_pool(
        cli, pooled, "test", "--force-new-validation", client=client
    )

    runs = read_jsonl(labels_of(pooled) / "judge-runs.jsonl")
    assert code == 0
    assert [(r["split"], r["forced"]) for r in runs] == [
        ("test", False),
        ("test", True),
    ]
    assert runs[1]["judge_prompt_sha256"] == judge_prompt_sha256()


def test_it_should_refuse_to_judge_a_round_without_a_pool(round_dir, cli):
    client = FakeClient()

    code, _, _ = judge_pool(cli, round_dir, "dev", client=client)

    assert code != 0
    assert client.calls == []


API_ERRORS = ["connection", "timeout", "rate-limit"]


def api_error(kind: str) -> Exception:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    if kind == "connection":
        return anthropic.APIConnectionError(request=request)
    if kind == "timeout":
        return anthropic.APITimeoutError(request=request)
    response = httpx.Response(429, request=request)
    return anthropic.RateLimitError("HTTP 429", response=response, body=None)


class DroppingClient(FakeClient):
    """Answers `after` calls, then fails every call with the error."""

    def __init__(self, error: Exception, *, after: int) -> None:
        super().__init__()
        self.failure = error
        self.after = after

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        self.error = self.failure if len(self.calls) >= self.after else None
        return super().complete(system, user)


class WatchingClient(FakeClient):
    """Records how many verdicts key.json held when each call began."""

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory
        self.stored: list[int] = []

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        self.stored.append(len(verdicts_of(self.directory)))
        return super().complete(system, user)


class PartialBatchClient(FakeBatchClient):
    """Batch that ends with a completion for only the first `answered` items."""

    def __init__(self, answered: int) -> None:
        super().__init__()
        self.answered = answered

    def collect(self, batch_id: str, item_ids: Sequence[str]) -> Any:
        return super().collect(batch_id, list(item_ids)[: self.answered])


def verdicts_of(directory: Path) -> dict[str, dict[str, Any]]:
    return {i: e for i, e in read_key(directory).items() if "verdict" in e}


def judge_batch(cli, directory: Path, split: str, batch: Any):
    return cli(
        "judge-pool",
        directory,
        "--split",
        split,
        "--judge",
        "batch",
        batch_client=batch,
        poll_seconds=0,
        timeout_seconds=5,
    )


def partial_test_run(cli, directory: Path, kind: str = "rate-limit", after: int = 5):
    write_floor(directory)
    client = DroppingClient(api_error(kind), after=after)
    return judge_pool(cli, directory, "test", client=client)


def change_judge_prompt(monkeypatch) -> None:
    changed = [FewShotExample("tone", "an unrelated slice", "Note.", "pass")]
    monkeypatch.setattr(judge_module, "FEW_SHOT_EXAMPLES", changed)


@pytest.mark.parametrize("kind", API_ERRORS)
def test_it_should_end_with_one_line_and_exit_one_when_the_api_fails_mid_run(
    pooled, cli, kind
):
    client = DroppingClient(api_error(kind), after=3)

    code, _, err = judge_pool(cli, pooled, "dev", client=client)

    assert code == 1
    assert one_line(err)
    assert "Traceback" not in err


@pytest.mark.parametrize("kind", API_ERRORS)
def test_it_should_keep_the_verdicts_already_paid_when_the_api_fails_mid_run(
    pooled, cli, kind
):
    client = DroppingClient(api_error(kind), after=3)

    judge_pool(cli, pooled, "dev", client=client)

    assert len(verdicts_of(pooled)) == 3


def test_it_should_write_each_verdict_to_the_key_before_the_next_call(pooled, cli):
    client = WatchingClient(pooled)

    judge_pool(cli, pooled, "dev", client=client)

    assert client.stored == list(range(len(client.calls)))


def test_it_should_stamp_each_stored_verdict_with_the_prompt_hash(pooled, cli):
    judge_pool(cli, pooled, "dev")

    stamps = {e["judge_prompt_sha256"] for e in verdicts_of(pooled).values()}
    assert stamps == {judge_prompt_sha256()}


def test_it_should_leave_key_json_untouched_when_replacing_it_is_interrupted(
    pooled, cli, monkeypatch
):
    key = labels_of(pooled) / "key.json"
    before = key.read_bytes()

    def interrupted(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", interrupted)

    judge_pool(cli, pooled, "dev")

    assert key.read_bytes() == before


def test_it_should_resume_a_partial_test_run_without_the_force_flag(pooled, cli):
    partial_test_run(cli, pooled)
    done = verdicts_of(pooled)
    pending = [i for i in items_of(pooled, "test") if i["id"] not in done]
    client = FakeClient()

    code, _, _ = judge_pool(cli, pooled, "test", client=client)

    assert code == 0
    assert sorted(client.calls) == sorted(
        build_prompt(item["criterion"], item["slice"]) for item in pending
    )


def test_it_should_cover_every_test_item_once_a_partial_run_is_resumed(pooled, cli):
    partial_test_run(cli, pooled)

    judge_pool(cli, pooled, "test")

    test_ids = {item["id"] for item in items_of(pooled, "test")}
    assert set(verdicts_of(pooled)) >= test_ids


def test_it_should_refuse_a_test_run_once_a_resumed_run_has_covered_every_item(
    pooled, cli
):
    partial_test_run(cli, pooled)
    resumed, _, _ = judge_pool(cli, pooled, "test")
    client = FakeClient()

    code, _, _ = judge_pool(cli, pooled, "test", client=client)

    assert resumed == 0
    assert code != 0
    assert client.calls == []


def test_it_should_refuse_to_resume_a_partial_test_run_with_a_changed_hash(
    pooled, cli, monkeypatch
):
    partial_test_run(cli, pooled)
    change_judge_prompt(monkeypatch)
    client = FakeClient()

    code, _, _ = judge_pool(cli, pooled, "test", client=client)

    assert code != 0
    assert client.calls == []


def test_it_should_judge_every_test_item_again_after_a_forced_new_validation(
    pooled, cli, monkeypatch
):
    partial_test_run(cli, pooled)
    change_judge_prompt(monkeypatch)
    client = FakeClient()

    code, _, _ = judge_pool(
        cli, pooled, "test", "--force-new-validation", client=client
    )

    assert code == 0
    assert len(client.calls) == len(items_of(pooled, "test"))


@pytest.mark.parametrize("kind", API_ERRORS)
def test_it_should_record_a_partial_test_run_as_incomplete(pooled, cli, kind):
    partial_test_run(cli, pooled, kind)

    [run] = read_jsonl(labels_of(pooled) / "judge-runs.jsonl")
    assert (run["complete"], run["items"]) == (False, 5)


def test_it_should_record_a_full_test_run_as_complete(pooled, cli):
    write_floor(pooled)

    judge_pool(cli, pooled, "test")

    [run] = read_jsonl(labels_of(pooled) / "judge-runs.jsonl")
    assert run["complete"] is True


def test_it_should_resume_a_partial_batch_with_only_the_missing_items(pooled, cli):
    write_floor(pooled)
    judge_batch(cli, pooled, "test", PartialBatchClient(answered=5))
    test_ids = {item["id"] for item in items_of(pooled, "test")}
    missing = test_ids - set(verdicts_of(pooled))
    batch = FakeBatchClient()

    code, _, _ = judge_batch(cli, pooled, "test", batch)

    assert code == 0
    [submitted] = batch.submitted
    assert {item.item_id for item in submitted} == missing


def unpriced_after(monkeypatch, settled: int) -> None:
    """Makes the batch settle `settled` replies, then meet a model without a price."""
    real = labels_module.verdict_from_reply
    seen = []

    def settle(text: str, usage: Mapping[str, Any], model: str) -> Any:
        if len(seen) >= settled:
            raise UnknownPriceError(f"No price for anthropic/{model}")
        seen.append(text)
        return real(text, usage, model)

    monkeypatch.setattr(labels_module, "verdict_from_reply", settle)


def test_it_should_end_a_paid_batch_with_one_line_when_a_price_is_unknown(
    pooled, cli, monkeypatch
):
    unpriced_after(monkeypatch, 3)

    code, _, err = judge_batch(cli, pooled, "dev", FakeBatchClient())

    assert code == 1
    assert one_line(err)
    assert "Traceback" not in err


def test_it_should_keep_the_batch_verdicts_settled_before_an_unknown_price(
    pooled, cli, monkeypatch
):
    unpriced_after(monkeypatch, 3)

    judge_batch(cli, pooled, "dev", FakeBatchClient())

    assert len(verdicts_of(pooled)) == 3


# present and record


def present(cli, directory: Path, count: int = 3):
    return cli("present", directory, "--n", count)


def test_it_should_present_the_first_unlabelled_items_in_pool_order(pooled, cli):
    pool = read_pool(pooled)

    _, out, _ = present(cli, pooled)

    assert all(item["id"] in out for item in pool[:3])
    assert pool[3]["id"] not in out


def test_it_should_present_the_criterion_and_slice_of_each_item(pooled, cli):
    pool = read_pool(pooled)

    _, out, _ = present(cli, pooled)

    for item in pool[:3]:
        assert item["criterion"] in out
        assert item["slice"] in out


def test_it_should_leave_the_seed_flag_kind_and_case_out_of_the_presented_items(
    pooled, cli
):
    key = read_key(pooled)
    first = [item["id"] for item in read_pool(pooled)[:10]]

    _, out, _ = present(cli, pooled, 10)

    assert not any(kind in out for kind in ALL_SEED_KINDS)
    assert not re.search(r"seed", out, re.IGNORECASE)
    assert not any(key[i]["case_id"] in out for i in first)
    assert not re.search(r"\b(natural|dev|test)\b", out)


def test_it_should_skip_the_items_already_labelled(pooled, cli, tmp_path):
    pool = read_pool(pooled)
    labels = write_labels_file(
        tmp_path / "labels.json",
        [
            {"id": pool[0]["id"], "label": "pass"},
            {"id": pool[1]["id"], "label": "fail"},
        ],
    )
    cli("record", pooled, labels)

    _, out, _ = present(cli, pooled)

    assert pool[0]["id"] not in out and pool[1]["id"] not in out
    assert all(item["id"] in out for item in pool[2:5])


def test_it_should_append_the_labels_to_human_jsonl(pooled, cli, tmp_path):
    pool = read_pool(pooled)
    labels = write_labels_file(
        tmp_path / "labels.json",
        [
            {"id": pool[0]["id"], "label": "pass"},
            {"id": pool[1]["id"], "label": "fail"},
        ],
    )

    code, _, _ = cli("record", pooled, labels)

    recorded = read_jsonl(labels_of(pooled) / "human.jsonl")
    assert code == 0
    assert [(r["id"], r["label"]) for r in recorded] == [
        (pool[0]["id"], "pass"),
        (pool[1]["id"], "fail"),
    ]


def test_it_should_keep_the_earlier_lines_when_recording_again(pooled, cli, tmp_path):
    pool = read_pool(pooled)
    first = write_labels_file(
        tmp_path / "first.json", [{"id": pool[0]["id"], "label": "pass"}]
    )
    second = write_labels_file(
        tmp_path / "second.json", [{"id": pool[1]["id"], "label": "fail"}]
    )
    cli("record", pooled, first)
    earlier = (labels_of(pooled) / "human.jsonl").read_bytes()

    cli("record", pooled, second)

    assert (labels_of(pooled) / "human.jsonl").read_bytes().startswith(earlier)
    assert len(read_jsonl(labels_of(pooled) / "human.jsonl")) == 2


def test_it_should_reject_a_batch_with_an_unknown_id_and_write_nothing(
    pooled, cli, tmp_path
):
    pool = read_pool(pooled)
    labels = write_labels_file(
        tmp_path / "labels.json",
        [{"id": pool[0]["id"], "label": "pass"}, {"id": "no-such-id", "label": "fail"}],
    )

    code, _, _ = cli("record", pooled, labels)

    assert code != 0
    assert not (labels_of(pooled) / "human.jsonl").exists()


def test_it_should_reject_an_id_that_was_already_recorded(pooled, cli, tmp_path):
    pool = read_pool(pooled)
    labels = write_labels_file(
        tmp_path / "labels.json", [{"id": pool[0]["id"], "label": "pass"}]
    )
    cli("record", pooled, labels)
    earlier = (labels_of(pooled) / "human.jsonl").read_bytes()
    again = write_labels_file(
        tmp_path / "again.json", [{"id": pool[0]["id"], "label": "fail"}]
    )

    code, _, _ = cli("record", pooled, again)

    assert code != 0
    assert (labels_of(pooled) / "human.jsonl").read_bytes() == earlier


def test_it_should_reject_an_id_repeated_inside_one_batch(pooled, cli, tmp_path):
    pool = read_pool(pooled)
    labels = write_labels_file(
        tmp_path / "labels.json",
        [
            {"id": pool[0]["id"], "label": "pass"},
            {"id": pool[0]["id"], "label": "fail"},
        ],
    )

    code, _, _ = cli("record", pooled, labels)

    assert code != 0
    assert not (labels_of(pooled) / "human.jsonl").exists()


@pytest.mark.parametrize("label", ["maybe", "unable", ""])
def test_it_should_reject_a_label_other_than_pass_or_fail(pooled, cli, tmp_path, label):
    pool = read_pool(pooled)
    labels = write_labels_file(
        tmp_path / "labels.json", [{"id": pool[0]["id"], "label": label}]
    )

    code, _, _ = cli("record", pooled, labels)

    assert code != 0
    assert not (labels_of(pooled) / "human.jsonl").exists()


# tpr-tnr


class Labels:
    """Hand-built labels directory: the pool, the key with verdicts, the human labels and the floor."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def add(
        self,
        criterion: str,
        human: str | None,
        judge: str | None,
        *,
        group: str = "natural",
        split: str = "test",
        case: str | None = None,
        count: int = 1,
        slice: str | None = None,
    ) -> "Labels":
        for _ in range(count):
            number = len(self.entries)
            self.entries.append(
                {
                    "id": f"item-{number:03d}",
                    "criterion": criterion,
                    "human": human,
                    "judge": judge,
                    "group": group,
                    "split": split,
                    "case": case or f"case-{number:03d}",
                    "slice": slice or f"Slice {number:03d} of {criterion}",
                }
            )
        return self

    def write(self, parent: Path, floor: Mapping[str, Any] | None = FLOOR) -> Path:
        directory = parent / "results" / "round-1"
        labels = labels_of(directory)
        labels.mkdir(parents=True)
        (directory / "round.json").write_text(
            json.dumps({"round_id": "round-1", "judge_pending": False})
        )
        pool = [
            {"id": e["id"], "criterion": e["criterion"], "slice": e["slice"]}
            for e in self.entries
        ]
        key = {
            e["id"]: {
                "criterion": e["criterion"],
                "split": e["split"],
                "seeded": e["group"] == "seeded",
                "group": e["group"],
                "case_id": e["case"],
                **({"verdict": e["judge"]} if e["judge"] else {}),
            }
            for e in self.entries
        }
        human = [
            {"id": e["id"], "label": e["human"]} for e in self.entries if e["human"]
        ]
        (labels / "pool.jsonl").write_text(
            "\n".join(json.dumps(line) for line in pool) + "\n", encoding="utf-8"
        )
        (labels / "key.json").write_text(json.dumps(key), encoding="utf-8")
        (labels / "human.jsonl").write_text(
            "\n".join(json.dumps(line) for line in human) + "\n", encoding="utf-8"
        )
        if floor is not None:
            write_floor(directory, floor)
        return directory


def metric_line(out: str, criterion: str, group: str, metric: str) -> str:
    match = re.search(rf"^{criterion} {group} {metric}\b.*$", out, re.MULTILINE)
    assert match, out
    return match.group(0)


def counts_of(line: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)/(\d+) items, (\d+) cases", line)
    assert match, line
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def tpr_tnr(cli, directory: Path, split: str = "test"):
    return cli("tpr-tnr", directory, "--split", split)


def test_it_should_count_the_judge_passes_among_the_human_passes_as_tpr(tmp_path, cli):
    labels = Labels()
    labels.add("tone", "pass", "pass", count=2).add("tone", "pass", "fail")
    labels.add("tone", "fail", "fail")

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert counts_of(metric_line(out, "tone", "natural", "TPR"))[:2] == (2, 3)


def test_it_should_count_the_judge_fails_among_the_human_fails_as_tnr(tmp_path, cli):
    labels = Labels()
    labels.add("tone", "fail", "fail").add("tone", "fail", "pass")
    labels.add("tone", "pass", "pass")

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert counts_of(metric_line(out, "tone", "natural", "TNR"))[:2] == (1, 2)


def test_it_should_count_the_distinct_cases_behind_n(tmp_path, cli):
    labels = Labels()
    labels.add("tone", "pass", "pass", case="a", count=2).add(
        "tone", "pass", "pass", case="b"
    )

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert counts_of(metric_line(out, "tone", "natural", "TPR")) == (3, 3, 2)


def test_it_should_print_the_wilson_interval_of_seven_of_seven(tmp_path, cli):
    labels = Labels().add("tone", "pass", "pass", count=7)

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    line = metric_line(out, "tone", "natural", "TPR")
    assert counts_of(line)[:2] == (7, 7)
    assert "0.65" in line and "1.00" in line


def test_it_should_print_the_wilson_interval_of_sixteen_of_twenty(tmp_path, cli):
    labels = Labels().add("tone", "pass", "pass", count=16)
    labels.add("tone", "pass", "fail", count=4)

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    line = metric_line(out, "tone", "natural", "TPR")
    assert counts_of(line)[:2] == (16, 20)
    assert "0.58" in line and "0.92" in line


def test_it_should_print_the_written_floor_beside_every_interval(tmp_path, cli):
    labels = Labels()
    labels.add("tone", "pass", "pass", count=3).add("tone", "fail", "fail", count=3)
    labels.add("tone", "fail", "fail", group="seeded", count=3)

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert "floor 0.70" in metric_line(out, "tone", "natural", "TPR")
    assert "floor 0.80" in metric_line(out, "tone", "natural", "TNR")
    assert "floor 0.80" in metric_line(out, "tone", "seeded", "TNR")


def test_it_should_report_natural_and_seeded_items_apart(tmp_path, cli):
    labels = Labels()
    labels.add("tone", "fail", "fail").add("tone", "fail", "pass")
    labels.add("tone", "fail", "fail", group="seeded", count=3)

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    natural = counts_of(metric_line(out, "tone", "natural", "TNR"))[:2]
    seeded = counts_of(metric_line(out, "tone", "seeded", "TNR"))[:2]
    assert (natural, seeded) == ((1, 2), (3, 3))


def test_it_should_report_the_tnr_as_seeded_only_without_a_natural_failure(
    tmp_path, cli
):
    labels = Labels().add("tone", "pass", "pass", count=2)
    labels.add("tone", "fail", "fail", group="seeded", count=2)

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert "seeded only" in metric_line(out, "tone", "natural", "TNR")


def test_it_should_report_owner_written_failures_as_a_third_group(tmp_path, cli):
    labels = Labels().add("tone", "fail", "fail", group="owner_written")
    labels.add("tone", "fail", "pass", group="seeded")

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert counts_of(metric_line(out, "tone", "owner_written", "TNR"))[:2] == (1, 1)
    assert counts_of(metric_line(out, "tone", "seeded", "TNR"))[:2] == (0, 1)


def test_it_should_leave_a_seed_labelled_pass_out_of_the_tnr(tmp_path, cli):
    labels = Labels().add("tone", "fail", "fail", group="seeded", count=2)
    labels.add("tone", "pass", "pass", group="seeded")

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert counts_of(metric_line(out, "tone", "seeded", "TNR"))[:2] == (2, 2)


def test_it_should_count_the_seeds_the_owner_labelled_pass(tmp_path, cli):
    labels = Labels().add("tone", "fail", "fail", group="seeded")
    labels.add("tone", "pass", "pass", group="seeded", count=2)

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert re.search(r"^tone weak seeds 2\b", out, re.MULTILINE)


def test_it_should_keep_unable_verdicts_out_of_tpr_and_tnr(tmp_path, cli):
    labels = Labels().add("tone", "pass", "pass", count=2)
    labels.add("tone", "pass", "unable").add("tone", "fail", "unable")
    labels.add("tone", "fail", "fail")

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert counts_of(metric_line(out, "tone", "natural", "TPR"))[:2] == (2, 2)
    assert counts_of(metric_line(out, "tone", "natural", "TNR"))[:2] == (1, 1)


def test_it_should_report_the_unable_rate_as_coverage(tmp_path, cli):
    labels = Labels().add("tone", "pass", "pass", count=3)
    labels.add("tone", "pass", "unable")

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert re.search(r"^tone unable 1/4 items", out, re.MULTILINE)


def test_it_should_report_an_empty_class_as_undefined(tmp_path, cli):
    labels = Labels().add("tone", "fail", "fail", count=2)

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert re.search(r"^tone natural TPR undefined", out, re.MULTILINE)


def test_it_should_report_each_criterion_apart(tmp_path, cli):
    labels = Labels().add("tone", "pass", "pass")
    labels.add("escalation_judgment", "pass", "fail")

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert counts_of(metric_line(out, "tone", "natural", "TPR"))[:2] == (1, 1)
    assert counts_of(metric_line(out, "escalation_judgment", "natural", "TPR"))[:2] == (
        0,
        1,
    )


def test_it_should_leave_the_other_split_out_of_the_counts(tmp_path, cli):
    labels = Labels().add("tone", "pass", "pass", split="test")
    labels.add("tone", "pass", "fail", split="dev", count=3)

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path), "test")

    assert counts_of(metric_line(out, "tone", "natural", "TPR"))[:2] == (1, 1)


def test_it_should_skip_the_items_without_a_human_label(tmp_path, cli):
    labels = Labels().add("tone", "pass", "pass")
    labels.add("tone", None, "fail", count=2)

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert counts_of(metric_line(out, "tone", "natural", "TPR"))[:2] == (1, 1)


def test_it_should_leave_the_few_shot_items_out_of_the_dev_metrics(
    tmp_path, cli, monkeypatch
):
    labels = Labels().add("tone", "pass", "pass", split="dev", count=3, slice="shared")
    labels.entries[1]["slice"] = "a few-shot slice"
    example = FewShotExample("tone", "a few-shot slice", "Fine.", "pass")
    monkeypatch.setattr(judge_module, "FEW_SHOT_EXAMPLES", [example])

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path), "dev")

    assert counts_of(metric_line(out, "tone", "natural", "TPR"))[:2] == (2, 2)


def test_it_should_report_the_dev_split_without_a_floor(tmp_path, cli):
    labels = Labels().add("tone", "pass", "pass", split="dev")

    code, out, _ = tpr_tnr(cli, labels.write(tmp_path, floor=None), "dev")

    assert code == 0
    assert counts_of(metric_line(out, "tone", "natural", "TPR"))[:2] == (1, 1)


def test_it_should_refuse_the_test_split_without_a_written_floor_when_reporting(
    tmp_path, cli
):
    labels = Labels().add("tone", "pass", "pass")

    code, out, err = tpr_tnr(cli, labels.write(tmp_path, floor=None))

    assert code != 0
    assert out == ""
    assert one_line(err)


def test_it_should_refuse_a_split_no_judge_run_has_covered(tmp_path, cli):
    labels = Labels().add("tone", "pass", None, count=3)

    code, out, err = tpr_tnr(cli, labels.write(tmp_path))

    assert code != 0
    assert out == ""
    assert one_line(err)


def test_it_should_refuse_the_test_split_while_some_of_its_items_wait_for_a_verdict(
    tmp_path, cli
):
    labels = Labels().add("tone", "pass", "pass").add("tone", "pass", None)

    code, out, err = tpr_tnr(cli, labels.write(tmp_path))

    assert code != 0
    assert out == ""
    assert one_line(err)


def test_it_should_not_print_a_bias_corrected_prevalence(tmp_path, cli):
    labels = Labels().add("tone", "pass", "pass", count=3)
    labels.add("tone", "fail", "fail", count=3)

    _, out, _ = tpr_tnr(cli, labels.write(tmp_path))

    assert not re.search(r"prevalence|corrected|rogan", out, re.IGNORECASE)


# refusals shared by every command


COMMANDS = ["seed", "pool", "judge-pool", "present", "record", "tpr-tnr"]


def command_line(command: str, directory: Path, tmp_path: Path) -> list[str]:
    arguments = {
        "judge-pool": ["--split", "dev"],
        "present": ["--n", "3"],
        "record": [str(write_labels_file(tmp_path / "labels.json", []))],
        "tpr-tnr": ["--split", "dev"],
    }
    return [command, str(directory), *arguments.get(command, [])]


def refusable_round(tmp_path: Path, *, held_out: bool, pending: bool) -> Path:
    parts = ("evals", HELD_OUT, "results") if held_out else ("results",)
    directory = tmp_path.joinpath(*parts, "round-1")
    directory.mkdir(parents=True)
    saved = {"round_id": "round-1", "runs": 3, "judge_pending": pending}
    (directory / "round.json").write_text(json.dumps(saved))
    return directory


@pytest.mark.parametrize("command", COMMANDS)
def test_it_should_refuse_a_held_out_round_with_one_line_and_no_output(
    tmp_path, cli, command
):
    directory = refusable_round(tmp_path, held_out=True, pending=False)
    client = FakeClient()

    code, out, err = cli(*command_line(command, directory, tmp_path), client=client)

    assert code != 0
    assert out == ""
    assert one_line(err)
    assert client.calls == [] and not labels_of(directory).exists()


@pytest.mark.parametrize("command", COMMANDS)
def test_it_should_refuse_a_round_with_a_judge_pending_with_one_line(
    tmp_path, cli, command
):
    directory = refusable_round(tmp_path, held_out=False, pending=True)
    client = FakeClient()

    code, out, err = cli(*command_line(command, directory, tmp_path), client=client)

    assert code != 0
    assert out == ""
    assert one_line(err)
    assert client.calls == [] and not labels_of(directory).exists()


# the command line


def test_it_should_seed_from_the_command_line_without_an_api_key(round_dir):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pet_helper.evals.labels", "seed", str(round_dir)],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert (done.returncode, (labels_of(round_dir) / "seeds.jsonl").is_file()) == (
        0,
        True,
    ), done.stderr
