import csv
import json
from pathlib import Path

import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant
from inspect_ai.scorer import CORRECT, Score, accuracy, scorer
from inspect_ai.solver import solver

from pet_helper.catalog import Catalog
from pet_helper.conversation import build_assistant
from pet_helper.gateway import FakeGateway
from pet_helper.guard import Guard
from pet_helper.trace import Tracer, find_raw_pii, summarize

KIT_DIR = Path(__file__).resolve().parents[2] / "kit"
HAIKU = ("anthropic", "claude-haiku-5-5")
OLLAMA = ("ollama", "llama3.2")
START = "2026-10-31T12:00:00+00:00"
END = "2026-10-31T12:00:02.500000+00:00"
LATER_START = "2026-10-31T12:05:00+00:00"
LATER_END = "2026-10-31T12:05:01+00:00"

RAW_CPF = "000.000.001-91"
RAW_EMAIL = "ana.souza@example.com"
RAW_PHONE = "(47) 99123-4567"


@pytest.fixture(autouse=True)
def _trace_content_on(monkeypatch):
    monkeypatch.delenv("PET_HELPER_TRACE_CONTENT", raising=False)


def _event(kind, session_id="s1", turn=1, ts=START, **fields):
    return {
        "ts": ts,
        "session_id": session_id,
        "turn": turn,
        "event": kind,
        "trace_id": f"{session_id}:{turn}",
        **fields,
    }


def _model_call(
    model=HAIKU,
    session_id="s1",
    turn=1,
    ttft=0.3,
    duration_ms=1300.0,
    input_tokens=100,
    output_tokens=10,
    cache_read=0,
    cache_write=0,
    cost=0.001,
    fallback=False,
    **extra,
):
    provider, name = model
    return _event(
        "model.call",
        session_id=session_id,
        turn=turn,
        span_id="3f2b8c1e-9a47-4d6e-8b05-7c1d2e3f4a5b",
        parent_span_id=None,
        **{
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": name,
            "gen_ai.response.time_to_first_chunk": ttft,
            "duration_ms": duration_ms,
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "gen_ai.usage.cache_read.input_tokens": cache_read,
            "gen_ai.usage.cache_creation.input_tokens": cache_write,
            "pet_helper.fallback": fallback,
            "pet_helper.cost_usd": cost,
        },
        **extra,
    )


def _tool_call(name, status="ok", session_id="s1", turn=1):
    return _event(
        "tool.call",
        session_id=session_id,
        turn=turn,
        **{"gen_ai.tool.name": name, "duration_ms": 5.0, "pet_helper.status": status},
    )


def _write_session(directory: Path, session_id: str, events: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.jsonl"
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return path


def _summary(tmp_path: Path, **sessions: list[dict]):
    paths = [
        _write_session(tmp_path, session_id, events)
        for session_id, events in sessions.items()
    ]
    return summarize(paths)


def _haiku_calls(tmp_path: Path, **per_call: list):
    """One session whose model calls vary a single field across `per_call`."""
    ((field, values),) = per_call.items()
    events = [_model_call(**{field: value}) for value in values]
    return _summary(tmp_path, s1=events).models[HAIKU]


def test_it_should_group_calls_by_provider_and_model(tmp_path):
    events = [_model_call(HAIKU), _model_call(HAIKU), _model_call(OLLAMA)]

    table = _summary(tmp_path, s1=events)

    assert {key: row.calls for key, row in table.models.items()} == {
        HAIKU: 2,
        OLLAMA: 1,
    }


def test_it_should_count_a_failed_model_call_without_usage(tmp_path):
    failed = _model_call(
        ttft=None,
        input_tokens=None,
        output_tokens=None,
        cache_read=None,
        cache_write=None,
        cost=None,
        **{"error.type": "ModelUnavailable"},
    )

    table = _summary(tmp_path, s1=[_model_call(), failed])

    assert (table.models[HAIKU].calls, table.models[HAIKU].input_tokens) == (2, 100)


def test_it_should_report_the_median_time_to_first_chunk_in_seconds(tmp_path):
    row = _haiku_calls(tmp_path, ttft=[0.2, 0.4, 0.6])

    assert row.ttft_p50 == pytest.approx(0.4)


def test_it_should_report_the_only_time_to_first_chunk_as_both_percentiles(tmp_path):
    row = _haiku_calls(tmp_path, ttft=[0.25])

    assert (row.ttft_p50, row.ttft_p95) == (pytest.approx(0.25), pytest.approx(0.25))


def test_it_should_keep_the_p95_time_to_first_chunk_within_the_observed_range(
    tmp_path,
):
    row = _haiku_calls(tmp_path, ttft=[0.2, 0.4, 0.6])

    assert 0.4 <= row.ttft_p95 <= 0.6


def test_it_should_skip_calls_without_a_first_chunk_in_the_percentiles(tmp_path):
    row = _haiku_calls(tmp_path, ttft=[0.2, None, 0.6])

    assert row.ttft_p50 == pytest.approx(0.4)


def test_it_should_count_the_calls_without_a_first_chunk(tmp_path):
    row = _haiku_calls(tmp_path, ttft=[0.2, None, None])

    assert row.ttft_missing == 2


def test_it_should_report_the_median_tokens_per_second_after_the_first_chunk(
    tmp_path,
):
    events = [
        _model_call(ttft=0.5, duration_ms=1500.0, output_tokens=tokens)
        for tokens in (10, 20, 30)
    ]

    table = _summary(tmp_path, s1=events)

    assert table.models[HAIKU].tps_median == pytest.approx(20.0)


def test_it_should_leave_calls_without_output_tokens_out_of_the_tps(tmp_path):
    events = [
        _model_call(ttft=0.5, duration_ms=1500.0, output_tokens=20),
        _model_call(ttft=None, duration_ms=300.0, output_tokens=None),
    ]

    table = _summary(tmp_path, s1=events)

    assert table.models[HAIKU].tps_median == pytest.approx(20.0)


def test_it_should_sum_the_input_tokens_per_model(tmp_path):
    row = _haiku_calls(tmp_path, input_tokens=[100, 300])

    assert row.input_tokens == 400


def test_it_should_sum_the_output_tokens_per_model(tmp_path):
    row = _haiku_calls(tmp_path, output_tokens=[10, 30])

    assert row.output_tokens == 40


def test_it_should_sum_the_cache_read_tokens_per_model(tmp_path):
    row = _haiku_calls(tmp_path, cache_read=[0, 200])

    assert row.cache_read_tokens == 200


def test_it_should_sum_the_cache_write_tokens_per_model(tmp_path):
    row = _haiku_calls(tmp_path, cache_write=[50, 70])

    assert row.cache_write_tokens == 120


def test_it_should_sum_the_cost_per_model(tmp_path):
    row = _haiku_calls(tmp_path, cost=[0.001, 0.0025])

    assert row.cost_usd == pytest.approx(0.0035)


def test_it_should_leave_unpriced_calls_out_of_the_cost_sum(tmp_path):
    row = _haiku_calls(tmp_path, cost=[0.001, None])

    assert row.cost_usd == pytest.approx(0.001)


def test_it_should_report_the_cache_hit_rate_as_cache_read_over_input_tokens(
    tmp_path,
):
    events = [
        _model_call(input_tokens=100, cache_read=0),
        _model_call(input_tokens=300, cache_read=200),
    ]

    table = _summary(tmp_path, s1=events)

    assert table.models[HAIKU].cache_hit_rate == pytest.approx(0.5)


def test_it_should_count_the_calls_of_each_tool(tmp_path):
    events = [
        _tool_call("get_order"),
        _tool_call("get_order"),
        _tool_call("search_policies"),
    ]

    table = _summary(tmp_path, s1=events)

    assert {name: row.calls for name, row in table.tools.items()} == {
        "get_order": 2,
        "search_policies": 1,
    }


def test_it_should_report_the_error_rate_of_each_tool(tmp_path):
    events = [
        _tool_call("get_order", "error"),
        _tool_call("get_order"),
        _tool_call("get_order"),
        _tool_call("get_order"),
        _tool_call("search_policies"),
    ]

    table = _summary(tmp_path, s1=events)

    assert (
        table.tools["get_order"].error_rate,
        table.tools["search_policies"].error_rate,
    ) == (pytest.approx(0.25), 0)


def test_it_should_count_the_fallback_calls(tmp_path):
    events = [_model_call(fallback=True)] + [_model_call() for _ in range(3)]

    table = _summary(tmp_path, s1=events)

    assert table.fallback_count == 1


def test_it_should_report_the_fallback_rate_over_all_model_calls(tmp_path):
    events = [_model_call(fallback=True)] + [_model_call() for _ in range(3)]

    table = _summary(tmp_path, s1=events)

    assert table.fallback_rate == pytest.approx(0.25)


def test_it_should_count_the_escalations_by_reason_across_sessions(tmp_path):
    first = [
        _event("escalation", **{"pet_helper.escalation_reason": "explicit_request"})
    ]
    second = [
        _event(
            "escalation",
            session_id="s2",
            **{"pet_helper.escalation_reason": "explicit_request"},
        )
    ]
    third = [
        _event(
            "escalation",
            session_id="s3",
            **{"pet_helper.escalation_reason": "animal_health_question"},
        )
    ]

    table = _summary(tmp_path, s1=first, s2=second, s3=third)

    assert table.escalations == {"explicit_request": 2, "animal_health_question": 1}


def test_it_should_sum_the_cost_of_each_conversation(tmp_path):
    table = _summary(
        tmp_path,
        s1=[_model_call(cost=0.001), _model_call(cost=0.002)],
        s2=[_model_call(session_id="s2", cost=0.01)],
    )

    assert {sid: row.cost_usd for sid, row in table.conversations.items()} == {
        "s1": pytest.approx(0.003),
        "s2": pytest.approx(0.01),
    }


def test_it_should_sum_the_turn_latency_of_a_conversation_in_seconds(tmp_path):
    events = [
        _event("turn.start", turn=1, ts=START),
        _event("turn.end", turn=1, ts=END),
        _event("turn.start", turn=2, ts=LATER_START),
        _event("turn.end", turn=2, ts=LATER_END),
    ]

    table = _summary(tmp_path, s1=events)

    assert table.conversations["s1"].latency_s == pytest.approx(3.5)


# find_raw_pii: plain regex over a results directory


def _write_trace(round_dir: Path, text: str) -> Path:
    return _write_session(
        round_dir / "traces",
        "s1",
        [_event("turn.start", **{"gen_ai.input.messages": text})],
    )


def _write_judge(round_dir: Path, text: str) -> Path:
    path = round_dir / "judge.jsonl"
    path.write_text(
        json.dumps(
            {"run_id": "r1", "criterion": "tone", "critique": text, "verdict": "pass"}
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_cases(round_dir: Path, text: str) -> Path:
    path = round_dir / "cases.csv"
    with path.open("w", encoding="utf-8", newline="") as sink:
        writer = csv.writer(sink)
        writer.writerow(["case_id", "suite", "k_of_3", "first_failure_note"])
        writer.writerow(["c1", "dev", "2/3", text])
    return path


WRITERS = {
    "trace": _write_trace,
    "judge": _write_judge,
    "cases": _write_cases,
}


@pytest.mark.parametrize("location", WRITERS)
@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("000.000.001-91", "cpf"),
        ("00000000191", "cpf"),
        ("ana.souza@example.com", "email"),
        ("(47) 99123-4567", "phone"),
        ("+55 47 99123-4567", "phone"),
    ],
    ids=["formatted-cpf", "bare-cpf", "email", "phone", "international-phone"],
)
def test_it_should_flag_raw_pii_in_each_results_file(tmp_path, location, raw, kind):
    written = WRITERS[location](tmp_path, f"Contato: {raw}, obrigado.")

    findings = find_raw_pii(tmp_path)

    assert {finding.path for finding in findings} == {written}
    assert kind in {finding.kind for finding in findings}


@pytest.mark.parametrize("location", WRITERS)
@pytest.mark.parametrize(
    "masked",
    ["Meu CPF é cpf#a8b41994.", "Meu CPF é [CPF informado].", "[e-mail] e [telefone]"],
    ids=["pseudonym", "placeholder", "contact-placeholders"],
)
def test_it_should_not_flag_masked_values(tmp_path, location, masked):
    WRITERS[location](tmp_path, masked)

    assert find_raw_pii(tmp_path) == []


def test_it_should_not_flag_the_numbers_of_an_ordinary_trace(tmp_path):
    events = [
        _model_call(duration_ms=1234.5678, input_tokens=12345, cost=0.00123456),
        _tool_call("get_order"),
    ]
    _write_session(tmp_path / "traces", "s1", events)

    assert find_raw_pii(tmp_path) == []


def test_it_should_return_nothing_for_a_directory_without_files(tmp_path):
    assert find_raw_pii(tmp_path) == []


# the Inspect log is read with read_eval_log, so Sample input, messages and scores count


@solver
def _replying(text):
    async def solve(state, generate):
        state.messages.append(ChatMessageAssistant(content=text))
        return state

    return solve


@scorer(metrics=[accuracy()])
def _explaining(explanation):
    async def score(state, target):
        return Score(value=CORRECT, explanation=explanation)

    return score


def _write_inspect_log(
    directory: Path,
    *,
    question="Qual o prazo de devolução?",
    reply="O prazo é de 7 dias.",
    explanation="resposta correta",
) -> Path:
    task = Task(
        dataset=[Sample(id="c1", input=question, target="7 dias")],
        solver=_replying(reply),
        scorer=_explaining(explanation),
    )

    eval(task, model="mockllm/model", log_dir=str(directory), display="none")

    [log_file] = directory.glob("*.eval")
    return log_file


@pytest.mark.parametrize(
    "field",
    ["question", "reply", "explanation"],
    ids=["sample-input", "message", "score"],
)
def test_it_should_flag_raw_pii_in_the_inspect_log(tmp_path, field):
    log_file = _write_inspect_log(tmp_path, **{field: f"Meu CPF é {RAW_CPF}."})

    findings = find_raw_pii(tmp_path)

    assert {finding.path for finding in findings} == {log_file}
    assert "cpf" in {finding.kind for finding in findings}


def test_it_should_not_flag_a_masked_inspect_log(tmp_path):
    _write_inspect_log(
        tmp_path,
        question="Meu CPF é cpf#a8b41994.",
        reply="Anotei o [CPF informado].",
        explanation="[e-mail] e [telefone] mascarados",
    )

    assert find_raw_pii(tmp_path) == []


# end to end: a traced conversation passes the scan


def _trace_conversation(trace_dir: Path, text: str) -> None:
    mask = Guard(Catalog(KIT_DIR / "catalog.csv")).mask
    assistant = build_assistant(
        gateway=FakeGateway(["Posso ajudar com isso."]),
        tracer=Tracer(trace_dir, mask=mask),
        kit_dir=KIT_DIR,
    )

    assistant.turn("session-pii", text)


PII_TEXT = f"Meu CPF é {RAW_CPF}, e-mail {RAW_EMAIL}, telefone {RAW_PHONE}."


def test_it_should_pass_the_scan_for_a_conversation_traced_with_the_guard_mask(
    tmp_path,
):
    _trace_conversation(tmp_path / "traces", PII_TEXT)

    assert find_raw_pii(tmp_path) == []


def test_it_should_keep_the_masked_text_of_the_conversation_in_the_trace(tmp_path):
    _trace_conversation(tmp_path / "traces", PII_TEXT)

    lines = (tmp_path / "traces" / "session-pii.jsonl").read_text().splitlines()
    turn_start = next(
        event for event in map(json.loads, lines) if event["event"] == "turn.start"
    )
    assert turn_start["gen_ai.input.messages"].startswith("Meu CPF é cpf#")


def test_it_should_flag_the_same_conversation_traced_with_an_identity_mask(tmp_path):
    assistant = build_assistant(
        gateway=FakeGateway(["Posso ajudar com isso."]),
        tracer=Tracer(tmp_path / "traces", mask=lambda text: text),
        kit_dir=KIT_DIR,
    )

    assistant.turn("session-pii", PII_TEXT)

    assert {finding.kind for finding in find_raw_pii(tmp_path)} >= {
        "cpf",
        "email",
        "phone",
    }
