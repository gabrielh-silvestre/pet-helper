"""The analysis tooling over a saved round: categories, annotate and report (no model).

Assumed public surface of `pet_helper.evals.analysis`:

- `main(argv: Sequence[str] | None = None) -> int`: prints to stdout, a refusal is one line on stderr
  and a non-zero return; also `python -m pet_helper.evals.analysis <command>`.
- `categories <cases.csv>`, `annotate <cases.csv> <json>` and `report <round_dir>
  [--previous <round_dir>] [--cases-dir <dir>]` (`--cases-dir` defaults to `evals/cases`; it names the
  checks each case carries, the saved Inspect log says which of them failed in each run).
- the `round.json` read for `judge_pending` and `runs` sits beside `cases.csv`.
- a held-out round is recognised by its path: any component of the resolved directory (the parent of
  `cases.csv` for `categories` and `annotate`) is named `held_out`, as in
  `evals/held_out/results/<round_id>`. No `round.json` field marks it.
- output lines the tests pin: `<name>: <count>` per category plus `unable: <n>` and `total: <n>`;
  `pass^3: <k>/<n> cases ...` with the interval as two decimals; `off_script unable runs: <n>`,
  `judge unable runs: <n>`; `improved: <n>` and `regressed: <n>`; a line `<case_id> <k display>` per case
  (the display being the runner's `2/3 (1 unable: off_script:cpf)`); and the blocks that start with
  the header lines `pass rate per check:`, `unable rate per criterion:`, `off_script runs:` and
  `graduation candidates:` (one `<name> <detail>` line each, ended by a blank line).
"""

import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from pet_helper.evals.analysis import main
from tests.unit.test_eval_round import Plan, play, steady
from tests.unit.test_eval_task import (
    _isolated_environment,  # noqa: F401  (autouse: keeps the environment clean)
)

COLUMNS = [
    "case_id",
    "suite",
    "k_of_3",
    "first_failure_note",
    "category",
    "human_label",
    "cost_usd",
    "failed_checks",
    "unable_runs",
    "unable_reasons",
]


# helpers


def row(case_id: str, k: str = "3/3", **cells: str) -> dict[str, str]:
    return {
        **dict.fromkeys(COLUMNS, ""),
        "case_id": case_id,
        "suite": "capability",
        "k_of_3": k,
        "cost_usd": "0.01",
        "unable_runs": "0",
        **cells,
    }


def make_round(
    parent: Path,
    rows: list[dict[str, str]],
    *,
    round_id: str = "round-1",
    runs: int = 3,
    pending: bool = False,
    verdicts: list[tuple[str, str]] = (),
) -> Path:
    directory = parent / round_id
    directory.mkdir(parents=True)
    columns = [f"k_of_{runs}" if name == "k_of_3" else name for name in COLUMNS]
    with (directory / "cases.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for line in rows:
            writer.writerow(
                {
                    (f"k_of_{runs}" if key == "k_of_3" else key): v
                    for key, v in line.items()
                }
            )
    (directory / "round.json").write_text(
        json.dumps({"round_id": round_id, "runs": runs, "judge_pending": pending})
    )
    lines = [
        json.dumps(
            {
                "run_id": f"case-{index}-e1",
                "criterion": criterion,
                "critique": "",
                "verdict": verdict,
                "usage": {},
                "cost_usd": "0",
                "judge_prompt_sha256": "h",
            }
        )
        for index, (criterion, verdict) in enumerate(verdicts)
    ]
    (directory / "judge.jsonl").write_text("\n".join(lines), encoding="utf-8")
    return directory


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def run(capsys, *argv: Any) -> tuple[int, str, str]:
    code = main([str(arg) for arg in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def counts(out: str) -> dict[str, int]:
    return {
        name: int(number)
        for name, number in (
            line.rsplit(": ", 1) for line in out.splitlines() if ": " in line
        )
    }


def section(out: str, header: str) -> list[str]:
    lines = out.splitlines()
    block = []
    for line in lines[lines.index(header) + 1 :]:
        if not line.strip():
            break
        block.append(line.strip())
    return block


def line_of(out: str, prefix: str) -> str:
    return next(line for line in out.splitlines() if line.startswith(prefix))


def assert_refused(code: int, out: str, err: str) -> None:
    assert code != 0
    assert out == ""
    assert len(err.strip().splitlines()) == 1


@pytest.fixture
def results(tmp_path) -> Path:
    return tmp_path / "results"


@pytest.fixture
def reserved(tmp_path) -> Path:
    return tmp_path / "evals" / "held_out" / "results"


# categories


def failed_rows() -> list[dict[str, str]]:
    return [
        row("a", "3/3", category="ignored-on-a-pass"),
        row("b", "1/3", category="tool", failed_checks="tool_called"),
        row("c", "0/3", category="tool", failed_checks="must_include"),
        row("d", "2/3", category="tone", failed_checks="judge:tone"),
        row("e", "0/3", failed_checks="must_include"),
    ]


def test_it_should_count_the_failed_cases_per_category(results, capsys):
    directory = make_round(results, failed_rows())

    _, out, _ = run(capsys, "categories", directory / "cases.csv")

    assert counts(out)["tool"] == 2
    assert counts(out)["tone"] == 1


def test_it_should_count_an_empty_category_as_none(results, capsys):
    directory = make_round(results, failed_rows())

    _, out, _ = run(capsys, "categories", directory / "cases.csv")

    assert counts(out)["(none)"] == 1


def test_it_should_sum_the_failed_cases_into_the_total(results, capsys):
    directory = make_round(results, failed_rows())

    _, out, _ = run(capsys, "categories", directory / "cases.csv")

    assert counts(out)["total"] == 4


def test_it_should_leave_a_passing_case_out_of_the_categories(results, capsys):
    directory = make_round(results, failed_rows())

    _, out, _ = run(capsys, "categories", directory / "cases.csv")

    assert "ignored-on-a-pass" not in out


def test_it_should_put_an_unable_case_on_its_own_line_apart_from_the_categories(
    results, capsys
):
    rows = [
        *failed_rows(),
        row("f", "2/3", unable_runs="1", unable_reasons="off_script:cpf"),
    ]
    directory = make_round(results, rows)

    _, out, _ = run(capsys, "categories", directory / "cases.csv")

    assert counts(out)["unable"] == 1
    assert counts(out)["total"] == 4
    assert counts(out)["(none)"] == 1


def test_it_should_skip_a_rerun_with_k_of_n_columns(results, capsys):
    rows = [row("a", "1/5", category="flaky", failed_checks="must_include")]
    directory = make_round(results, rows, runs=5)

    code, out, _ = run(capsys, "categories", directory / "cases.csv")

    assert code == 0
    assert "flaky" not in out
    assert counts(out).get("total", 0) == 0


def test_it_should_refuse_categories_for_a_round_with_a_pending_judge(results, capsys):
    directory = make_round(results, failed_rows(), pending=True)

    assert_refused(*run(capsys, "categories", directory / "cases.csv"))


def test_it_should_refuse_categories_for_a_reserved_round(reserved, capsys):
    directory = make_round(reserved, failed_rows())

    assert_refused(*run(capsys, "categories", directory / "cases.csv"))


# annotate


def notes_file(tmp_path: Path, notes: dict[str, dict[str, str]]) -> Path:
    path = tmp_path / "notes.json"
    path.write_text(json.dumps(notes), encoding="utf-8")
    return path


def annotate_rows() -> list[dict[str, str]]:
    return [
        row("a", "3/3"),
        row("b", "1/3", failed_checks="tool_called", cost_usd="0.07"),
        row("c", "0/3", failed_checks="must_include", human_label="kept"),
    ]


def test_it_should_write_the_critic_note_and_category_for_the_given_case(
    results, tmp_path, capsys
):
    directory = make_round(results, annotate_rows())
    notes = notes_file(
        tmp_path, {"b": {"first_failure_note": "wrong tool", "category": "tool"}}
    )

    code, _, _ = run(capsys, "annotate", directory / "cases.csv", notes)

    saved = {line["case_id"]: line for line in read_rows(directory / "cases.csv")}
    assert code == 0
    assert (saved["b"]["first_failure_note"], saved["b"]["category"]) == (
        "wrong tool",
        "tool",
    )


def test_it_should_write_the_owner_confirmed_human_label_for_the_given_case(
    results, tmp_path, capsys
):
    directory = make_round(results, annotate_rows())
    notes = notes_file(tmp_path, {"b": {"human_label": "bug"}})

    run(capsys, "annotate", directory / "cases.csv", notes)

    saved = {line["case_id"]: line for line in read_rows(directory / "cases.csv")}
    assert saved["b"]["human_label"] == "bug"


def test_it_should_keep_every_other_cell_when_annotating(results, tmp_path, capsys):
    directory = make_round(results, annotate_rows())
    before = read_rows(directory / "cases.csv")
    notes = notes_file(tmp_path, {"b": {"category": "tool"}})

    run(capsys, "annotate", directory / "cases.csv", notes)

    after = read_rows(directory / "cases.csv")
    assert after[0] == before[0]
    assert after[2] == before[2]
    assert {**after[1], "category": ""} == before[1]


def test_it_should_never_reorder_the_rows_when_annotating(results, tmp_path, capsys):
    directory = make_round(results, annotate_rows())
    notes = notes_file(tmp_path, {"c": {"category": "tone"}, "a": {"category": "x"}})

    run(capsys, "annotate", directory / "cases.csv", notes)

    saved = read_rows(directory / "cases.csv")
    assert [line["case_id"] for line in saved] == ["a", "b", "c"]


def test_it_should_keep_the_header_when_annotating(results, tmp_path, capsys):
    directory = make_round(results, annotate_rows())
    header = (directory / "cases.csv").read_text(encoding="utf-8").splitlines()[0]
    notes = notes_file(tmp_path, {"b": {"category": "tool"}})

    run(capsys, "annotate", directory / "cases.csv", notes)

    saved_header = (directory / "cases.csv").read_text(encoding="utf-8").splitlines()[0]
    assert saved_header == header


def test_it_should_reject_an_unknown_case_id_and_leave_the_file_untouched(
    results, tmp_path, capsys
):
    directory = make_round(results, annotate_rows())
    before = (directory / "cases.csv").read_bytes()
    notes = notes_file(
        tmp_path, {"b": {"category": "tool"}, "ghost": {"category": "tool"}}
    )

    code, _, err = run(capsys, "annotate", directory / "cases.csv", notes)

    assert code != 0
    assert "ghost" in err
    assert (directory / "cases.csv").read_bytes() == before


@pytest.mark.parametrize("column", ["k_of_3", "cost_usd", "failed_checks", "case_id"])
def test_it_should_reject_a_column_outside_the_three_annotation_columns(
    column, results, tmp_path, capsys
):
    directory = make_round(results, annotate_rows())
    before = (directory / "cases.csv").read_bytes()
    notes = notes_file(tmp_path, {"b": {column: "tampered"}})

    code, _, err = run(capsys, "annotate", directory / "cases.csv", notes)

    assert code != 0
    assert column in err
    assert (directory / "cases.csv").read_bytes() == before


def test_it_should_refuse_annotate_for_a_round_with_a_pending_judge(
    results, tmp_path, capsys
):
    directory = make_round(results, annotate_rows(), pending=True)
    before = (directory / "cases.csv").read_bytes()
    notes = notes_file(tmp_path, {"b": {"category": "tool"}})

    code, out, err = run(capsys, "annotate", directory / "cases.csv", notes)

    assert_refused(code, out, err)
    assert (directory / "cases.csv").read_bytes() == before


def test_it_should_refuse_annotate_for_a_reserved_round(reserved, tmp_path, capsys):
    directory = make_round(reserved, annotate_rows())
    before = (directory / "cases.csv").read_bytes()
    notes = notes_file(tmp_path, {"b": {"category": "tool"}})

    code, out, err = run(capsys, "annotate", directory / "cases.csv", notes)

    assert_refused(code, out, err)
    assert (directory / "cases.csv").read_bytes() == before


# report


def mixed_rows() -> list[dict[str, str]]:
    return [
        row("a", "3/3"),
        row("b", "2/3", unable_runs="1", unable_reasons="off_script:cpf"),
        row(
            "c",
            "0/3",
            failed_checks="must_include",
            unable_runs="2",
            unable_reasons="off_script:species;judge:tone",
        ),
        row("d", "2/3", unable_runs="1", unable_reasons="judge:escalation_judgment"),
        row("e", "3/3"),
    ]


MIXED_VERDICTS = [
    ("tone", "pass"),
    ("tone", "pass"),
    ("tone", "fail"),
    ("tone", "unable"),
    ("escalation_judgment", "pass"),
    ("escalation_judgment", "unable"),
]


def test_it_should_print_the_suite_pass_cubed_as_k_of_n_cases_with_the_wilson_interval(
    results, capsys
):
    directory = make_round(results, mixed_rows())

    _, out, _ = run(capsys, "report", directory)

    line = line_of(out, "pass^3")
    assert "2/5 cases" in line
    assert "0.12" in line
    assert "0.77" in line


def test_it_should_print_the_pass_rate_of_each_check_across_the_runs(
    results, tmp_path, capsys
):
    case = steady(
        checks=[
            {"type": "must_include", "value": "7 dias"},
            {"type": "must_not_include", "value": "caro"},
        ]
    )
    play(results, [case], Plan(["ok", "fail", "ok"]))
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "steady.yaml").write_text(
        yaml.safe_dump(case.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )

    _, out, _ = run(capsys, "report", results / "round-1", "--cases-dir", cases_dir)

    rates = dict(
        line.split(maxsplit=1) for line in section(out, "pass rate per check:")
    )
    assert "2/3" in rates["must_include"]
    assert "3/3" in rates["must_not_include"]


def test_it_should_split_the_unable_runs_into_off_script_and_judge(results, capsys):
    directory = make_round(results, mixed_rows())

    _, out, _ = run(capsys, "report", directory)

    assert "off_script unable runs: 2" in out.splitlines()
    assert "judge unable runs: 2" in out.splitlines()


def test_it_should_print_the_unable_rate_per_judge_criterion(results, capsys):
    directory = make_round(results, mixed_rows(), verdicts=MIXED_VERDICTS)

    _, out, _ = run(capsys, "report", directory)

    rates = dict(
        line.split(maxsplit=1) for line in section(out, "unable rate per criterion:")
    )
    assert "1/4" in rates["tone"]
    assert "1/2" in rates["escalation_judgment"]


def test_it_should_note_the_saturation_when_every_case_passes_three_of_three(
    results, capsys
):
    directory = make_round(results, [row("a"), row("b"), row("c")])

    _, out, _ = run(capsys, "report", directory)

    assert "saturat" in out.lower()


def test_it_should_not_note_saturation_when_a_case_misses_a_run(results, capsys):
    directory = make_round(results, mixed_rows())

    _, out, _ = run(capsys, "report", directory)

    assert "saturat" not in out.lower()


def test_it_should_say_the_three_runs_of_a_case_are_correlated(results, capsys):
    directory = make_round(results, mixed_rows())

    _, out, _ = run(capsys, "report", directory)

    assert "correlated" in out.lower()
    assert "optimistic" in out.lower()


def test_it_should_print_the_unable_reasons_next_to_the_case_k(results, capsys):
    directory = make_round(results, mixed_rows())

    _, out, _ = run(capsys, "report", directory)

    assert "2/3 (1 unable: off_script:cpf)" in line_of(out, "b ")
    assert "0/3 (2 unable: off_script:species, judge:tone)" in line_of(out, "c ")


def test_it_should_list_every_off_script_run_with_its_item(results, capsys):
    rows = [
        row(
            "a",
            "1/3",
            unable_runs="2",
            unable_reasons="off_script:cpf;off_script:order_number",
        ),
        row("b", "2/3", unable_runs="1", unable_reasons="off_script:species"),
        row("c", "2/3", unable_runs="1", unable_reasons="judge:tone"),
    ]
    directory = make_round(results, rows)

    _, out, _ = run(capsys, "report", directory)

    listed = [line.split() for line in section(out, "off_script runs:")]
    assert sorted(listed) == [["a", "cpf"], ["a", "order_number"], ["b", "species"]]


def test_it_should_report_a_rerun_as_k_of_n_per_case_with_its_interval(results, capsys):
    rows = [row("rerun-a", "4/5"), row("rerun-b", "5/5")]
    directory = make_round(results, rows, runs=5)

    _, out, _ = run(capsys, "report", directory)

    line = line_of(out, "rerun-a")
    assert "4/5" in line
    assert "0.38" in line
    assert "0.96" in line


def test_it_should_not_print_the_suite_pass_cubed_for_a_rerun(results, capsys):
    directory = make_round(results, [row("rerun-a", "4/5")], runs=5)

    _, out, _ = run(capsys, "report", directory)

    assert "pass^3" not in out


def test_it_should_refuse_report_for_a_round_with_a_pending_judge(results, capsys):
    directory = make_round(results, mixed_rows(), pending=True)

    assert_refused(*run(capsys, "report", directory))


def test_it_should_refuse_report_for_a_reserved_round(reserved, capsys):
    directory = make_round(reserved, mixed_rows())

    assert_refused(*run(capsys, "report", directory))


# report --previous


def previous_rows() -> list[dict[str, str]]:
    return [
        row("a", "3/3"),
        row("b", "3/3"),
        row("c", "2/3"),
        row("d", "0/3"),
        row("e", "3/3"),
    ]


def current_rows() -> list[dict[str, str]]:
    return [
        row("a", "3/3"),
        row("b", "2/3"),
        row("c", "3/3"),
        row("d", "1/3"),
        row("e", "3/3"),
        row("f", "3/3"),
    ]


@pytest.fixture
def paired(results, capsys) -> str:
    before = make_round(results, previous_rows(), round_id="round-1")
    after = make_round(results, current_rows(), round_id="round-2")
    return run(capsys, "report", after, "--previous", before)[1]


def test_it_should_count_the_cases_that_went_from_not_passing_to_passing(paired):
    assert "improved: 1" in paired.splitlines()


def test_it_should_count_the_cases_that_went_from_passing_to_not_passing(paired):
    assert "regressed: 1" in paired.splitlines()


def test_it_should_list_the_cases_at_three_of_three_in_both_rounds_as_graduation_candidates(
    paired,
):
    candidates = section(paired, "graduation candidates:")

    assert sorted(line.split()[0] for line in candidates) == ["a", "e"]


def test_it_should_refuse_a_comparison_of_rounds_with_different_runs(results, capsys):
    before = make_round(results, [row("a", "5/5")], round_id="round-1", runs=5)
    after = make_round(results, [row("a", "3/3")], round_id="round-2")

    assert_refused(*run(capsys, "report", after, "--previous", before))


def test_it_should_refuse_a_comparison_with_a_previous_round_whose_judge_is_pending(
    results, capsys
):
    before = make_round(results, previous_rows(), round_id="round-1", pending=True)
    after = make_round(results, current_rows(), round_id="round-2")

    assert_refused(*run(capsys, "report", after, "--previous", before))


def test_it_should_refuse_a_comparison_with_a_reserved_previous_round(
    results, reserved, capsys
):
    before = make_round(reserved, previous_rows(), round_id="round-1")
    after = make_round(results, current_rows(), round_id="round-2")

    assert_refused(*run(capsys, "report", after, "--previous", before))


# command line


def test_it_should_run_as_a_module_from_the_command_line(results):
    directory = make_round(results, failed_rows())

    csv_path = str(directory / "cases.csv")

    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pet_helper.evals.analysis", "categories", csv_path],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert done.returncode == 0
    assert counts(done.stdout)["total"] == 4
