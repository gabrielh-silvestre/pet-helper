"""The pet-helper eval command: selection, pre-flight, confirmation, smoke, held-out, judge modes and the PII scan (no real model)."""

import csv
import hashlib
import json
import re
import tempfile
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from inspect_ai.log import read_eval_log, write_eval_log
from langchain_core.messages import SystemMessage

from pet_helper.cli import EvalEnvironment, main
from pet_helper.conversation import KitError, system_prompt_sha256
from pet_helper.conversation.prompt import SYSTEM_PROMPT
from pet_helper.evals.judge import current_verdicts
from pet_helper.evals.round import GitState
from pet_helper.gateway import FakeGateway, ModelConfigError
from pet_helper.orders.api import FAILURE_ENV
from pet_helper.trace.tracer import CONTENT_ENV
from tests.unit.test_cli import _set_input_lines
from tests.unit.test_eval_round import TURNS, BatchStub, Clock, Plan, api_error
from tests.unit.test_eval_task import KIT, JudgeStub

CPF = "000.000.001-91"
HELD_OUT_ENV = "PET_HELPER_HELD_OUT"
SHA = "abc1234"
HIDDEN_TEXT = "SECRETMARKER"
JUDGED_RUNS = 3  # one judge call per run of a default round


class WatchedBatch(BatchStub):
    """Batch client that records each collection and becomes ready on the Nth one."""

    def __init__(self, *, ready_after: int = 1) -> None:
        super().__init__(ready=False)
        self.ready_after = ready_after
        self.collected: list[str] = []

    def collect(self, batch_id, item_ids):
        self.collected.append(batch_id)
        self.ready = len(self.collected) >= self.ready_after
        return super().collect(batch_id, item_ids)


UNPRICED_MODEL = "claude-mystery-9"


# helpers


def write_case(directory: Path, case_id: str, **fields: Any) -> None:
    case = {
        "id": case_id,
        "suite": "capability",
        "today": "2026-11-03",
        "turns": list(TURNS),
        "checks": [{"type": "must_include", "value": "7 dias"}],
        **fields,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{case_id}.yaml").write_text(
        yaml.safe_dump(case, allow_unicode=True), encoding="utf-8"
    )


def eval_cli(monkeypatch, env, *argv, answers: Sequence[str] = ("y",)):
    _set_input_lines(monkeypatch, answers)
    try:
        return main(["eval", *argv], eval_environment=env)
    except SystemExit as stop:
        return stop.code


def output_lines(capsys) -> list[str]:
    captured = capsys.readouterr()
    return [
        line for line in f"{captured.out}{captured.err}".splitlines() if line.strip()
    ]


def saved_rounds(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def only_round(root: Path) -> Path:
    [directory] = saved_rounds(root)
    return directory


def read_info(directory: Path) -> dict[str, Any]:
    return json.loads((directory / "round.json").read_text(encoding="utf-8"))


def read_rows(directory: Path) -> list[dict[str, str]]:
    with (directory / "cases.csv").open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_rows(directory: Path, rows: list[dict[str, str]]) -> None:
    with (directory / "cases.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def save_round(
    root: Path,
    round_id: str,
    suite: str,
    *,
    case_id: str = "steady",
    runs: int = 3,
) -> None:
    directory = root / round_id
    directory.mkdir(parents=True)
    write_rows(directory, [{"case_id": case_id, "suite": suite, "cost_usd": ""}])
    (directory / "round.json").write_text(
        json.dumps({"round_id": round_id, "runs": runs})
    )


def reference_of_newest_round(root: Path) -> str:
    return read_info(saved_rounds(root)[-1])["reference_round"]


def plant(directory: Path, where: str) -> str:
    """Put a raw CPF in one output of a saved round and return the name of that file."""
    if where == "traces":
        path = directory / "traces" / "planted.jsonl"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"text": f"CPF {CPF}"}) + "\n", encoding="utf-8")
    elif where == "judge.jsonl":
        path = directory / "judge.jsonl"
        record = {
            "run_id": "planted",
            "criterion": "tone",
            "critique": f"CPF {CPF}",
            "verdict": "pass",
            "usage": {},
            "cost_usd": "0",
            "judge_prompt_sha256": "planted",
        }
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record) + "\n")
    elif where == "cases.csv":
        path = directory / "cases.csv"
        rows = read_rows(directory)
        rows[0]["first_failure_note"] = f"CPF {CPF}"
        write_rows(directory, rows)
    else:
        path = next(directory.glob("*.eval"))
        log = read_eval_log(str(path))
        assert log.samples
        log.samples[0].input[0].content = f"Meu CPF é {CPF}"
        write_eval_log(log, str(path))
    return path.name


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch, tmp_path):
    for name in (HELD_OUT_ENV, "PET_HELPER_TODAY", "PET_HELPER_KIT_DIR", CONTENT_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(FAILURE_ENV, raising=False)
    # the smoke round writes into the temporary directory: keep it under tmp_path
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    # a stray ./logs of Inspect would land here
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def plan() -> Plan:
    return Plan()


@pytest.fixture
def judge() -> JudgeStub:
    return JudgeStub()


@pytest.fixture
def batch() -> BatchStub:
    return BatchStub()


@pytest.fixture
def env(tmp_path, plan, judge, batch) -> EvalEnvironment:
    write_case(tmp_path / "cases", "steady")
    write_case(tmp_path / "held-cases", "reserved", suite="held-out")
    return EvalEnvironment(
        cases_dir=tmp_path / "cases",
        held_out_cases_dir=tmp_path / "held-cases",
        results_root=tmp_path / "results",
        held_out_results_root=tmp_path / "held-results",
        gateway_factory=plan,
        judge_client=judge,
        batch_client=batch,
        batch_wait_seconds=0.0,
        batch_poll_seconds=0.0,
        kit_dir=KIT,
        git_state=lambda: GitState(SHA, False),
        clock=Clock(),
    )


@pytest.fixture
def held_out(monkeypatch):
    monkeypatch.setenv(HELD_OUT_ENV, "1")


@pytest.fixture
def pending_round(monkeypatch, env) -> Path:
    write_case(env.cases_dir, "judged", judge=["tone"])
    eval_cli(monkeypatch, env, "--case", "judged", "--judge", "none")
    return only_round(env.results_root)


# selection


def test_it_should_run_only_the_cases_of_the_given_suite(monkeypatch, env, plan):
    write_case(env.cases_dir, "regress", suite="regression")

    eval_cli(monkeypatch, env, "--suite", "regression")

    assert {case_id for case_id, _ in plan.built} == {"regress"}


def test_it_should_run_only_the_case_with_the_given_id(monkeypatch, env, plan):
    write_case(env.cases_dir, "other")

    eval_cli(monkeypatch, env, "--case", "other")

    assert {case_id for case_id, _ in plan.built} == {"other"}


def test_it_should_run_every_case_when_no_selection_is_given(monkeypatch, env, plan):
    write_case(env.cases_dir, "other")

    eval_cli(monkeypatch, env)

    assert {case_id for case_id, _ in plan.built} == {"steady", "other"}


def test_it_should_exit_with_one_line_naming_an_unknown_case(
    monkeypatch, env, plan, capsys
):
    code = eval_cli(monkeypatch, env, "--case", "no-such-case")

    lines = output_lines(capsys)
    assert (code != 0, len(lines), plan.built) == (True, 1, [])
    assert "no-such-case" in lines[0]


def test_it_should_judge_with_the_sync_client_when_no_judge_flag_is_given(
    monkeypatch, env, judge
):
    write_case(env.cases_dir, "judged", judge=["tone"])

    eval_cli(monkeypatch, env, "--case", "judged")

    assert len(judge.calls) == JUDGED_RUNS
    assert read_info(only_round(env.results_root))["judge_mode"] == "sync"


def test_it_should_exit_with_zero_after_a_clean_round(monkeypatch, env):
    assert eval_cli(monkeypatch, env, "--case", "steady") == 0


def test_it_should_print_each_case_with_its_k_of_three(monkeypatch, env, capsys):
    eval_cli(monkeypatch, env, "--case", "steady")

    assert "steady 3/3" in capsys.readouterr().out


def test_it_should_print_the_reason_next_to_k_when_a_run_is_unable(
    monkeypatch, env, capsys
):
    unable = replace(env, gateway_factory=Plan(["unable"]))

    eval_cli(monkeypatch, unable, "--case", "steady")

    assert "steady 2/3 (1 unable: off_script:cpf)" in capsys.readouterr().out


def test_it_should_write_the_round_under_the_results_root(monkeypatch, env):
    eval_cli(monkeypatch, env, "--case", "steady")

    directory = only_round(env.results_root)
    assert read_info(directory)["round_id"] == directory.name
    assert (directory / "cases.csv").is_file()


# pre-flight


def test_it_should_name_the_case_and_the_code_of_a_structure_error(
    monkeypatch, env, capsys
):
    bad_check = {"type": "must_include", "value": "7 dias", "trun": 1}
    write_case(env.cases_dir, "typo", checks=[bad_check])

    code = eval_cli(monkeypatch, env, "--case", "typo")

    output = "\n".join(output_lines(capsys))
    assert code != 0
    assert "typo" in output
    assert "unknown_key" in output


def test_it_should_name_the_case_and_the_failed_check_of_a_wrong_reference(
    monkeypatch, env, capsys
):
    reference = {"replies": [{"text": "Claro."}, {"text": "Não sei."}]}
    write_case(env.cases_dir, "wrong-reference", reference=reference)

    code = eval_cli(monkeypatch, env, "--case", "wrong-reference")

    output = "\n".join(output_lines(capsys))
    assert code != 0
    assert "wrong-reference" in output
    assert "must_include" in output


def test_it_should_run_a_case_whose_reference_holds(monkeypatch, env, plan):
    reference = {"replies": [{"text": "Claro."}, {"text": "O prazo é de 7 dias."}]}
    write_case(env.cases_dir, "right-reference", reference=reference)

    code = eval_cli(monkeypatch, env, "--case", "right-reference")

    assert code == 0
    assert plan.built


@pytest.mark.parametrize("flaw", ["structure", "reference"])
def test_it_should_call_no_model_and_print_no_estimate_when_the_pre_flight_fails(
    flaw, monkeypatch, env, plan, judge, capsys
):
    if flaw == "structure":
        write_case(env.cases_dir, "flawed", turns=[])
    else:
        write_case(env.cases_dir, "flawed", reference={"replies": [{"text": "a"}] * 2})

    eval_cli(monkeypatch, env, "--case", "flawed")

    assert (plan.built, judge.calls, saved_rounds(env.results_root)) == ([], [], [])
    assert "US$" not in "\n".join(output_lines(capsys))


def test_it_should_name_only_the_case_and_a_code_for_a_held_out_structure_error(
    monkeypatch, held_out, env, plan, capsys
):
    bad_check = {"type": "must_include", "value": HIDDEN_TEXT, "trun": 1}
    write_case(
        env.held_out_cases_dir, "reserved-bad", turns=[HIDDEN_TEXT], checks=[bad_check]
    )

    code = eval_cli(monkeypatch, env, "--held-out", "--case", "reserved-bad")

    output = "\n".join(output_lines(capsys))
    assert (code != 0, plan.built) == (True, [])
    assert "reserved-bad" in output
    assert HIDDEN_TEXT not in output
    assert "unknown_key" in output


# estimate and confirmation


def test_it_should_print_the_estimate_before_running(monkeypatch, env, capsys):
    eval_cli(monkeypatch, env, "--case", "steady")

    output = capsys.readouterr()
    assert "US$" in output.out + output.err
    assert "0.024" in output.out + output.err


@pytest.mark.parametrize("answer", ["", "n", "no", "maybe", "sim"])
def test_it_should_call_no_model_when_the_answer_is_not_an_explicit_yes(
    answer, monkeypatch, env, plan, judge, capsys
):
    eval_cli(monkeypatch, env, "--case", "steady", answers=[answer])

    assert (plan.built, judge.calls, saved_rounds(env.results_root)) == ([], [], [])
    assert "US$" in "\n".join(output_lines(capsys))


def test_it_should_abort_without_a_call_when_stdin_ends(monkeypatch, env, plan):
    eval_cli(monkeypatch, env, "--case", "steady", answers=[])

    assert (plan.built, saved_rounds(env.results_root)) == ([], [])


@pytest.mark.parametrize("answer", ["y", "YES"])
def test_it_should_run_the_round_after_an_explicit_yes(answer, monkeypatch, env, plan):
    eval_cli(monkeypatch, env, "--case", "steady", answers=[answer])

    assert plan.built


# smoke


def test_it_should_print_the_smoke_flag_for_a_single_run(monkeypatch, env, capsys):
    eval_cli(monkeypatch, env, "--case", "steady", "--runs", "1")

    assert '"smoke": true' in capsys.readouterr().out


def test_it_should_write_nothing_under_the_results_root_for_a_smoke_run(
    monkeypatch, env
):
    eval_cli(monkeypatch, env, "--case", "steady", "--runs", "1")

    assert saved_rounds(env.results_root) == []
    assert not env.results_root.exists() or not any(env.results_root.iterdir())


def test_it_should_print_the_temporary_directory_of_a_smoke_run_on_the_last_line(
    monkeypatch, env, capsys, tmp_path
):
    eval_cli(monkeypatch, env, "--case", "steady", "--runs", "1")

    last = Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert last.is_dir()
    assert (tmp_path / "scratch") in [last, *last.parents]
    assert list(last.rglob("*.eval"))
    assert list(last.rglob("traces"))


def test_it_should_not_label_a_smoke_run_as_a_round(monkeypatch, env, capsys):
    eval_cli(monkeypatch, env, "--case", "steady", "--runs", "1")

    # the clock of the fixture gives the round id its first reading
    printed = capsys.readouterr().out.strip().splitlines()[:-1]
    assert "2026-11-02T100000" not in "\n".join(printed)


# decisive rerun


def test_it_should_write_a_round_of_ten_runs_for_a_decisive_rerun(monkeypatch, env):
    save_round(env.results_root, "2026-10-31T090000", "capability")

    eval_cli(monkeypatch, env, "--case", "steady", "--runs", "10")

    directory = saved_rounds(env.results_root)[-1]
    assert read_info(directory)["runs"] == 10
    assert "k_of_10" in read_rows(directory)[0]


def test_it_should_decide_against_the_latest_saved_round_of_three_runs_with_the_case(
    monkeypatch, env, capsys
):
    save_round(env.results_root, "2026-10-30T090000", "capability")
    save_round(env.results_root, "2026-10-31T090000", "capability")

    eval_cli(monkeypatch, env, "--case", "steady", "--runs", "10")

    assert reference_of_newest_round(env.results_root) == "2026-10-31T090000"
    assert "2026-10-31T090000" in capsys.readouterr().out


def test_it_should_not_take_a_newer_round_of_ten_runs_as_the_reference(
    monkeypatch, env
):
    save_round(env.results_root, "2026-10-31T090000", "capability")
    save_round(env.results_root, "2026-11-01T090000", "capability", runs=10)

    eval_cli(monkeypatch, env, "--case", "steady", "--runs", "10")

    assert reference_of_newest_round(env.results_root) == "2026-10-31T090000"


def test_it_should_not_take_a_newer_round_without_the_case_as_the_reference(
    monkeypatch, env
):
    save_round(env.results_root, "2026-10-31T090000", "capability")
    save_round(env.results_root, "2026-11-01T090000", "capability", case_id="other")

    eval_cli(monkeypatch, env, "--case", "steady", "--runs", "10")

    assert reference_of_newest_round(env.results_root) == "2026-10-31T090000"


def test_it_should_refuse_a_decisive_rerun_with_one_line_when_no_round_can_be_the_reference(
    monkeypatch, env, capsys
):
    save_round(env.results_root, "2026-10-31T090000", "capability", runs=10)

    code = eval_cli(monkeypatch, env, "--case", "steady", "--runs", "10")

    lines = output_lines(capsys)
    assert (code, len(lines)) == (1, 1)
    assert "steady" in lines[0]


def test_it_should_call_no_model_and_print_no_estimate_for_a_decisive_rerun_without_reference(
    monkeypatch, env, plan, judge, capsys
):
    eval_cli(monkeypatch, env, "--case", "steady", "--runs", "10")

    assert (plan.built, judge.calls, saved_rounds(env.results_root)) == ([], [], [])
    assert "US$" not in "\n".join(output_lines(capsys))


def test_it_should_name_only_the_case_when_a_held_out_decisive_rerun_has_no_reference(
    monkeypatch, held_out, env, plan, capsys
):
    write_case(
        env.held_out_cases_dir,
        "reserved-hidden",
        suite="held-out",
        turns=[HIDDEN_TEXT],
    )

    code = eval_cli(
        monkeypatch, env, "--held-out", "--case", "reserved-hidden", "--runs", "10"
    )

    lines = output_lines(capsys)
    assert (code, len(lines), plan.built) == (1, 1, [])
    assert "reserved-hidden" in lines[0]
    assert HIDDEN_TEXT not in lines[0]
    assert "held-out" not in lines[0]


def test_it_should_retry_an_unable_run_until_ten_are_valid(monkeypatch, env):
    save_round(env.results_root, "2026-10-31T090000", "capability")
    unable = Plan(["unable"])

    eval_cli(
        monkeypatch,
        replace(env, gateway_factory=unable),
        *["--case", "steady", "--runs", "10"],
    )

    assert len(unable.built) == 11


# held-out


def test_it_should_exit_with_two_and_one_line_without_the_held_out_gate(
    monkeypatch, env, plan, capsys, tmp_path
):
    closed = replace(env, held_out_cases_dir=tmp_path / "never-read")

    code = eval_cli(monkeypatch, closed, "--held-out")

    assert (code, len(output_lines(capsys)), plan.built) == (2, 1, [])


def test_it_should_run_only_the_reserved_cases_with_the_held_out_gate(
    monkeypatch, held_out, env, plan
):
    eval_cli(monkeypatch, env, "--held-out")

    assert {case_id for case_id, _ in plan.built} == {"reserved"}


def test_it_should_not_load_the_reserved_cases_without_the_held_out_flag(
    monkeypatch, held_out, env, plan
):
    eval_cli(monkeypatch, env)

    assert {case_id for case_id, _ in plan.built} == {"steady"}


def test_it_should_write_a_held_out_round_to_the_held_out_results_root(
    monkeypatch, held_out, env
):
    eval_cli(monkeypatch, env, "--held-out")

    assert (only_round(env.held_out_results_root) / "round.json").is_file()
    assert saved_rounds(env.results_root) == []


def test_it_should_show_only_the_case_lines_and_the_git_sha_for_a_held_out_run(
    monkeypatch, held_out, env, capsys
):
    eval_cli(monkeypatch, env, "--held-out")

    # the estimate is the only other line the terminal may carry
    lines = [line for line in capsys.readouterr().out.splitlines() if "US$" not in line]
    assert lines[-1] == SHA
    assert all(re.fullmatch(r"\S+ \d+/3", line) for line in lines[:-1])
    assert [line.split()[0] for line in lines[:-1]] == ["reserved"]


def test_it_should_print_only_the_class_name_of_an_exception_in_a_held_out_run(
    monkeypatch, held_out, env, capsys
):
    def leak():
        raise RuntimeError("leaky detail")

    code = eval_cli(monkeypatch, replace(env, git_state=leak), "--held-out")

    output = "\n".join(output_lines(capsys))
    assert code != 0
    assert "RuntimeError" in output
    assert "leaky detail" not in output


# judge none


def test_it_should_run_no_judge_when_the_judge_is_none(monkeypatch, env, judge):
    write_case(env.cases_dir, "judged", judge=["tone"])

    eval_cli(monkeypatch, env, "--case", "judged", "--judge", "none")

    info = read_info(only_round(env.results_root))
    assert judge.calls == []
    assert (info["judge_mode"], info["judge_prompt_sha256"]) == ("none", None)


# rejudge


def test_it_should_print_the_estimate_and_ask_before_a_rejudge(
    monkeypatch, env, judge, pending_round, capsys
):
    capsys.readouterr()

    eval_cli(monkeypatch, env, "--rejudge", pending_round.name, answers=["n"])

    output = capsys.readouterr()
    assert judge.calls == []
    assert "0.021" in output.out + output.err


def test_it_should_judge_each_saved_run_when_a_round_is_rejudged(
    monkeypatch, env, judge, pending_round
):
    eval_cli(monkeypatch, env, "--rejudge", pending_round.name)

    assert len(judge.calls) == JUDGED_RUNS
    assert set(current_verdicts(pending_round / "judge.jsonl")) == {
        ("judged-e1", "tone"),
        ("judged-e2", "tone"),
        ("judged-e3", "tone"),
    }


def test_it_should_recompute_cases_csv_from_the_rejudge(
    monkeypatch, env, pending_round
):
    env = replace(env, judge_client=JudgeStub("fail"))

    eval_cli(monkeypatch, env, "--rejudge", pending_round.name)

    [row] = read_rows(pending_round)
    assert (row["k_of_3"], row["failed_checks"]) == ("0/3", "judge:tone")


def test_it_should_keep_the_cells_written_by_the_analysis_when_rejudging(
    monkeypatch, env, pending_round
):
    kept = {
        "first_failure_note": "wrong tone",
        "category": "tone",
        "human_label": "fail",
    }
    rows = read_rows(pending_round)
    write_rows(pending_round, [{**rows[0], **kept}])

    eval_cli(monkeypatch, env, "--rejudge", pending_round.name)

    [row] = read_rows(pending_round)
    assert {name: row[name] for name in kept} == kept


def test_it_should_record_the_judge_in_round_json_after_the_rejudge(
    monkeypatch, env, pending_round
):
    eval_cli(monkeypatch, env, "--rejudge", pending_round.name)

    info = read_info(pending_round)
    assert (info["judge_mode"], info["judge_pending"]) == ("sync", False)


def test_it_should_stop_a_rejudge_at_the_cap(
    monkeypatch, env, judge, pending_round, capsys
):
    capped = replace(env, budget_usd=Decimal("0.0001"))
    capsys.readouterr()

    eval_cli(monkeypatch, capped, "--rejudge", pending_round.name)

    assert judge.calls == []
    assert "cap" in "\n".join(output_lines(capsys))


def test_it_should_end_a_rejudge_with_one_line_when_the_judge_model_has_no_price(
    monkeypatch, env, pending_round, capsys
):
    unpriced = JudgeStub()
    unpriced.model = UNPRICED_MODEL
    capsys.readouterr()

    code = eval_cli(
        monkeypatch,
        replace(env, judge_client=unpriced),
        "--rejudge",
        pending_round.name,
    )

    errors = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert code not in (0, None)
    assert len(errors) == 1
    assert "price" in errors[0]


def test_it_should_keep_the_rejudged_round_pending_when_the_judge_model_has_no_price(
    monkeypatch, env, pending_round
):
    unpriced = JudgeStub()
    unpriced.model = UNPRICED_MODEL

    eval_cli(
        monkeypatch,
        replace(env, judge_client=unpriced),
        "--rejudge",
        pending_round.name,
    )

    assert read_info(pending_round)["judge_pending"] is True
    assert [row["case_id"] for row in read_rows(pending_round)] == ["judged"]


def test_it_should_refuse_a_held_out_rejudge_without_the_gate(
    monkeypatch, env, judge, capsys
):
    code = eval_cli(monkeypatch, env, "--held-out", "--rejudge", "any-round")

    assert (code, len(output_lines(capsys)), judge.calls) == (2, 1, [])


def test_it_should_rejudge_a_held_out_round_with_the_gate(
    monkeypatch, held_out, env, judge
):
    write_case(
        env.held_out_cases_dir, "reserved-judged", suite="held-out", judge=["tone"]
    )
    eval_cli(
        monkeypatch, env, "--held-out", "--case", "reserved-judged", "--judge", "none"
    )
    saved = only_round(env.held_out_results_root)

    eval_cli(monkeypatch, env, "--held-out", "--rejudge", saved.name)

    assert len(judge.calls) == JUDGED_RUNS
    assert set(current_verdicts(saved / "judge.jsonl"))


# batch judge


def test_it_should_submit_the_judge_through_the_batch_client(monkeypatch, env, batch):
    write_case(env.cases_dir, "judged", judge=["tone"])

    eval_cli(monkeypatch, env, "--case", "judged", "--judge", "batch")

    directory = only_round(env.results_root)
    assert len(batch.submitted) == 1
    assert set(current_verdicts(directory / "judge.jsonl"))
    assert read_info(directory)["judge_mode"] == "batch"


def test_it_should_wait_up_to_the_limit_and_save_the_batch_id_when_it_has_not_returned(
    monkeypatch, env, capsys
):
    watched = WatchedBatch(ready_after=10**9)
    waiting = replace(
        env, batch_client=watched, batch_wait_seconds=0.3, batch_poll_seconds=0.02
    )
    write_case(env.cases_dir, "judged", judge=["tone"])

    eval_cli(monkeypatch, waiting, "--case", "judged", "--judge", "batch")

    info = read_info(only_round(env.results_root))
    assert (info["batch_id"], info["judge_pending"]) == ("batch-1", True)
    assert len(watched.collected) >= 2
    assert "batch-1" in capsys.readouterr().out


def test_it_should_finish_the_round_when_the_batch_returns_within_the_limit(
    monkeypatch, env
):
    watched = WatchedBatch(ready_after=3)
    waiting = replace(
        env, batch_client=watched, batch_wait_seconds=5.0, batch_poll_seconds=0.0
    )
    write_case(env.cases_dir, "judged", judge=["tone"])

    eval_cli(monkeypatch, waiting, "--case", "judged", "--judge", "batch")

    info = read_info(only_round(env.results_root))
    assert (info.get("batch_id"), info["judge_pending"]) == (None, False)


@pytest.fixture
def pending_batch(monkeypatch, env) -> Path:
    write_case(env.cases_dir, "judged", judge=["tone"])
    waiting = replace(env, batch_client=WatchedBatch(ready_after=10**9))
    eval_cli(monkeypatch, waiting, "--case", "judged", "--judge", "batch")
    return only_round(env.results_root)


def test_it_should_retrieve_the_saved_batch_by_its_id_on_collect(
    monkeypatch, env, pending_batch
):
    returned = WatchedBatch(ready_after=1)

    eval_cli(
        monkeypatch,
        replace(env, batch_client=returned),
        "--collect",
        pending_batch.name,
        answers=[],
    )

    assert (returned.collected, returned.submitted) == (["batch-1"], [])


def test_it_should_append_the_collected_verdicts_and_complete_the_round(
    monkeypatch, env, pending_batch
):
    eval_cli(
        monkeypatch,
        replace(env, batch_client=WatchedBatch(ready_after=1)),
        "--collect",
        pending_batch.name,
        answers=[],
    )

    assert set(current_verdicts(pending_batch / "judge.jsonl")) == {
        ("judged-e1", "tone"),
        ("judged-e2", "tone"),
        ("judged-e3", "tone"),
    }
    assert read_info(pending_batch)["judge_pending"] is False


def test_it_should_end_a_collect_with_one_line_when_the_judge_model_has_no_price(
    monkeypatch, env, pending_batch, capsys
):
    unpriced = WatchedBatch(ready_after=1)
    unpriced.model = UNPRICED_MODEL
    capsys.readouterr()

    code = eval_cli(
        monkeypatch,
        replace(env, batch_client=unpriced),
        "--collect",
        pending_batch.name,
        answers=[],
    )

    errors = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert code not in (0, None)
    assert len(errors) == 1
    assert "price" in errors[0]


def test_it_should_keep_the_batch_pending_when_the_judge_model_has_no_price(
    monkeypatch, env, pending_batch
):
    unpriced = WatchedBatch(ready_after=1)
    unpriced.model = UNPRICED_MODEL

    eval_cli(
        monkeypatch,
        replace(env, batch_client=unpriced),
        "--collect",
        pending_batch.name,
        answers=[],
    )

    info = read_info(pending_batch)
    assert (info["batch_id"], info["judge_pending"]) == ("batch-1", True)


def test_it_should_wait_for_the_batch_on_collect_up_to_the_configured_limit(
    monkeypatch, env, pending_batch
):
    waiting = replace(
        env,
        batch_client=WatchedBatch(ready_after=3),
        batch_wait_seconds=5.0,
        batch_poll_seconds=0.0,
    )

    eval_cli(monkeypatch, waiting, "--collect", pending_batch.name, answers=[])

    assert read_info(pending_batch)["judge_pending"] is False


def test_it_should_poll_the_batch_on_collect_and_stay_pending_when_the_limit_passes(
    monkeypatch, env, pending_batch
):
    watched = WatchedBatch(ready_after=10**9)
    waiting = replace(
        env, batch_client=watched, batch_wait_seconds=0.3, batch_poll_seconds=0.02
    )

    eval_cli(monkeypatch, waiting, "--collect", pending_batch.name, answers=[])

    assert len(watched.collected) >= 2
    assert read_info(pending_batch)["batch_id"] == "batch-1"


def test_it_should_exit_non_zero_with_one_line_when_the_batch_submit_fails(
    monkeypatch, env, capsys
):
    write_case(env.cases_dir, "judged", judge=["tone"])
    failing = replace(env, batch_client=BatchStub(submit_error=api_error("connection")))
    capsys.readouterr()

    code = eval_cli(monkeypatch, failing, "--case", "judged", "--judge", "batch")

    errors = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert code not in (0, None)
    assert len(errors) == 1


def test_it_should_keep_the_error_body_out_of_a_held_out_batch_failure(
    monkeypatch, held_out, env, capsys
):
    write_case(
        env.held_out_cases_dir, "reserved-judged", suite="held-out", judge=["tone"]
    )
    failing = replace(
        env,
        batch_client=BatchStub(submit_error=api_error("bad_request", HIDDEN_TEXT)),
    )
    capsys.readouterr()

    code = eval_cli(
        monkeypatch,
        failing,
        *["--held-out", "--case", "reserved-judged", "--judge", "batch"],
    )

    output = capsys.readouterr()
    errors = [line for line in output.err.splitlines() if line.strip()]
    assert code not in (0, None)
    assert len(errors) == 1
    assert HIDDEN_TEXT not in output.out + output.err


@pytest.mark.parametrize("argv", [["--judge", "batch"], ["--collect", "any-round"]])
def test_it_should_reject_the_batch_judge_with_one_line_without_a_batch_client(
    argv, monkeypatch, env, plan, capsys
):
    unusable = replace(env, batch_client=None)

    code = eval_cli(monkeypatch, unusable, *argv)

    lines = output_lines(capsys)
    assert code != 0
    assert len(lines) == 1
    assert "batch" in lines[0].lower()
    assert (plan.built, saved_rounds(env.results_root)) == ([], [])


# round-level PII scan


def test_it_should_mark_a_clean_round_as_pii_free(monkeypatch, env):
    code = eval_cli(monkeypatch, env, "--case", "steady")

    assert code == 0
    assert read_info(only_round(env.results_root))["pii_check"] == "pass"


@pytest.mark.parametrize("where", ["traces", "judge.jsonl", "cases.csv", "log"])
def test_it_should_exit_with_three_and_one_line_naming_the_file_with_a_raw_cpf(
    where, monkeypatch, env, pending_round, capsys
):
    name = plant(pending_round, where)
    capsys.readouterr()

    code = eval_cli(monkeypatch, env, "--rejudge", pending_round.name)

    errors = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert code == 3
    assert len(errors) == 1
    assert name in errors[0]


@pytest.mark.parametrize("where", ["traces", "judge.jsonl", "cases.csv", "log"])
def test_it_should_mark_the_pii_check_as_failed_in_round_json(
    where, monkeypatch, env, pending_round
):
    plant(pending_round, where)

    eval_cli(monkeypatch, env, "--rejudge", pending_round.name)

    assert read_info(pending_round)["pii_check"] == "fail"


def test_it_should_pass_the_pii_scan_of_a_clean_rejudge(
    monkeypatch, env, pending_round
):
    code = eval_cli(monkeypatch, env, "--rejudge", pending_round.name)

    assert code == 0
    assert read_info(pending_round)["pii_check"] == "pass"


# Inspect's default log directory


@pytest.mark.parametrize("argv", [[], ["--runs", "1"]])
def test_it_should_not_write_inspect_logs_to_the_working_directory(
    argv, monkeypatch, env, tmp_path
):
    eval_cli(monkeypatch, env, "--case", "steady", *argv)

    assert not (tmp_path / "logs").exists()


# the pending judge


def test_it_should_count_the_cases_whose_judge_is_pending_after_a_round_without_a_judge(
    monkeypatch, env, capsys
):
    write_case(env.cases_dir, "judged", judge=["tone"])

    eval_cli(monkeypatch, env, "--judge", "none")

    assert "judge pending for 1 cases" in output_lines(capsys)


def test_it_should_print_no_pending_judge_line_when_no_case_has_a_criterion(
    monkeypatch, env, capsys
):
    eval_cli(monkeypatch, env, "--judge", "none")

    assert not any("judge pending" in line for line in output_lines(capsys))


def test_it_should_count_the_pending_judge_without_naming_the_case_in_a_held_out_run(
    monkeypatch, held_out, env, capsys
):
    write_case(
        env.held_out_cases_dir, "reserved-judged", suite="held-out", judge=["tone"]
    )

    eval_cli(
        monkeypatch, env, "--held-out", "--case", "reserved-judged", "--judge", "none"
    )

    [line] = [line for line in output_lines(capsys) if "judge pending" in line]
    assert line == "judge pending for 1 cases"


# the pre-flight


def test_it_should_refuse_a_case_whose_check_has_no_value_before_any_call(
    monkeypatch, env, plan, capsys
):
    write_case(env.cases_dir, "valueless", checks=[{"type": "must_include"}])

    code = eval_cli(monkeypatch, env, "--case", "valueless")

    assert (code, plan.built) == (1, [])
    assert "missing_value" in "\n".join(output_lines(capsys))


def test_it_should_refuse_an_unpriced_judge_with_one_line_before_asking(
    monkeypatch, env, plan, capsys
):
    unpriced = JudgeStub()
    unpriced.model = UNPRICED_MODEL
    write_case(env.cases_dir, "judged", judge=["tone"])

    code = eval_cli(
        monkeypatch, replace(env, judge_client=unpriced), "--case", "judged"
    )

    lines = output_lines(capsys)
    assert (code, plan.built, len(lines)) == (1, [], 1)
    assert UNPRICED_MODEL in lines[0]


# the error paths


@pytest.mark.parametrize(
    "error", [KitError("leaky detail"), ModelConfigError("leaky detail")]
)
def test_it_should_print_only_the_class_name_of_a_config_error_in_a_held_out_run(
    monkeypatch, held_out, env, capsys, error
):
    def fail():
        raise error

    code = eval_cli(monkeypatch, replace(env, git_state=fail), "--held-out")

    assert (code, output_lines(capsys)[-1]) == (1, type(error).__name__)


def test_it_should_print_the_message_of_a_config_error_in_a_visible_run(
    monkeypatch, env, capsys
):
    def fail():
        raise ModelConfigError("leaky detail")

    code = eval_cli(monkeypatch, replace(env, git_state=fail), "--case", "steady")

    assert (code, output_lines(capsys)[-1]) == (1, "leaky detail")


def test_it_should_let_an_unexpected_error_through_in_a_visible_run(monkeypatch, env):
    def fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        eval_cli(monkeypatch, replace(env, git_state=fail), "--case", "steady")


def test_it_should_refuse_to_collect_a_round_that_does_not_exist(
    monkeypatch, env, capsys
):
    code = eval_cli(monkeypatch, env, "--collect", "no-such-round")

    assert (code, output_lines(capsys)) == (1, ["Unknown round: no-such-round"])


def test_it_should_refuse_to_collect_a_round_without_a_pending_batch(
    monkeypatch, env, pending_round, capsys
):
    code = eval_cli(monkeypatch, env, "--collect", pending_round.name)

    assert code == 1
    assert "no pending batch" in output_lines(capsys)[-1]


# the candidate assistant prompt (--system-prompt-file)

CANDIDATE_PROMPT = "Prompt candidato de teste\nsegunda linha\n"


class RecordingGateways:
    """Gateway factory that scripts the steady case and keeps every gateway it built."""

    def __init__(self) -> None:
        self.gateways: list[FakeGateway] = []

    def __call__(self, case, epoch) -> FakeGateway:
        gateway = FakeGateway(["Claro, posso ajudar.", "O prazo é de 7 dias."])
        self.gateways.append(gateway)
        return gateway

    def system_prompts(self) -> set[str]:
        return {
            call[0].content
            for gateway in self.gateways
            for call in gateway.calls
            if isinstance(call[0], SystemMessage)
        }


def eval_steady(monkeypatch, env, gateways, *extra: str):
    return eval_cli(
        monkeypatch,
        replace(env, gateway_factory=gateways),
        "--case",
        "steady",
        "--judge",
        "none",
        *extra,
    )


@pytest.fixture
def gateways() -> RecordingGateways:
    return RecordingGateways()


@pytest.fixture
def candidate_file(tmp_path) -> Path:
    path = tmp_path / "candidate.txt"
    path.write_text(CANDIDATE_PROMPT, encoding="utf-8")
    return path


def test_it_should_run_the_round_with_the_text_of_the_prompt_file(
    monkeypatch, env, gateways, candidate_file
):
    eval_steady(monkeypatch, env, gateways, "--system-prompt-file", str(candidate_file))

    assert gateways.system_prompts() == {CANDIDATE_PROMPT}


def test_it_should_record_the_hash_of_the_prompt_file_in_round_json(
    monkeypatch, env, gateways, candidate_file
):
    eval_steady(monkeypatch, env, gateways, "--system-prompt-file", str(candidate_file))

    recorded = read_info(only_round(env.results_root))["assistant_prompt_sha256"]
    assert recorded == hashlib.sha256(CANDIDATE_PROMPT.encode()).hexdigest()
    assert recorded != system_prompt_sha256()


def test_it_should_run_the_round_with_the_current_prompt_when_no_file_is_given(
    monkeypatch, env, gateways
):
    eval_steady(monkeypatch, env, gateways)

    assert gateways.system_prompts() == {SYSTEM_PROMPT}


def test_it_should_record_the_current_prompt_hash_when_no_file_is_given(
    monkeypatch, env, gateways
):
    eval_steady(monkeypatch, env, gateways)

    recorded = read_info(only_round(env.results_root))["assistant_prompt_sha256"]
    assert recorded == system_prompt_sha256()


def test_it_should_refuse_a_missing_prompt_file_naming_it(
    monkeypatch, env, gateways, tmp_path, capsys
):
    missing = tmp_path / "no-such-prompt.txt"

    code = eval_steady(monkeypatch, env, gateways, "--system-prompt-file", str(missing))

    assert code == 1
    assert "no-such-prompt.txt" in "\n".join(output_lines(capsys))


@pytest.mark.parametrize("content", ["", "  \n\n"])
def test_it_should_refuse_an_empty_prompt_file_naming_it(
    content, monkeypatch, env, gateways, tmp_path, capsys
):
    empty = tmp_path / "empty-prompt.txt"
    empty.write_text(content, encoding="utf-8")

    code = eval_steady(monkeypatch, env, gateways, "--system-prompt-file", str(empty))

    assert code == 1
    assert "empty-prompt.txt" in "\n".join(output_lines(capsys))


def test_it_should_spend_nothing_when_the_prompt_file_is_refused(
    monkeypatch, env, gateways, tmp_path, capsys
):
    missing = tmp_path / "no-such-prompt.txt"

    eval_steady(monkeypatch, env, gateways, "--system-prompt-file", str(missing))

    assert gateways.gateways == []
    assert saved_rounds(env.results_root) == []
    assert "US$" not in "\n".join(output_lines(capsys))
