import json
import re
import threading
from pathlib import Path

import pytest
from genai_prices import Usage, calc_price

from pet_helper.catalog import Catalog
from pet_helper.guard import Guard
from pet_helper.trace import NullTracer, Tracer, UnknownPriceError, cost_usd

CATALOG_CSV = Path(__file__).resolve().parents[2] / "kit" / "catalog.csv"
CONTENT_ENV = "PET_HELPER_TRACE_CONTENT"
INPUT_FIELD = "gen_ai.input.messages"
OUTPUT_FIELD = "gen_ai.output.messages"
THREADS = 8
EVENTS_PER_THREAD = 25
PRICED_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5"]
RAW_PII = [
    "529.982.247-25",
    "52998224725",
    "maria.silva@example.com",
    "(11) 98888-7777",
]


def _identity(text: str) -> str:
    return text


def _lines(path: Path) -> list[str]:
    # bytes, not read_text: universal newlines would turn a stray \r into a line break
    return path.read_bytes().decode("utf-8").split("\n")[:-1]


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in _lines(path)]


def _expected_price(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> float:
    usage = Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )
    return float(calc_price(usage, model, provider_id="anthropic").total_price)


def _langchain_usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> dict:
    # LangChain usage_metadata: input_tokens already includes cache read and write
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_token_details": {
            "cache_read": cache_read,
            "cache_creation": cache_write,
        },
    }


@pytest.fixture
def guard() -> Guard:
    return Guard(Catalog(CATALOG_CSV))


def test_it_should_append_one_json_line_with_the_event_envelope(tmp_path):
    tracer = Tracer(tmp_path, mask=_identity)

    tracer.event(
        "model.call", session_id="s1", turn=2, **{"gen_ai.request.model": "m-1"}
    )

    [record] = _records(tmp_path / "s1.jsonl")
    assert record["session_id"] == "s1"
    assert record["turn"] == 2
    assert record["event"] == "model.call"
    assert record["trace_id"] == "s1:2"
    assert record["gen_ai.request.model"] == "m-1"
    assert "ts" in record


def test_it_should_keep_the_span_ids_it_is_given(tmp_path):
    tracer = Tracer(tmp_path, mask=_identity)

    tracer.event(
        "tool.call", session_id="s1", turn=1, span_id="run-b", parent_span_id="run-a"
    )

    [record] = _records(tmp_path / "s1.jsonl")
    assert record["span_id"] == "run-b"
    assert record["parent_span_id"] == "run-a"


def test_it_should_append_to_the_session_file_across_events(tmp_path):
    tracer = Tracer(tmp_path, mask=_identity)

    tracer.event("turn.start", session_id="s1", turn=1)
    tracer.event("turn.end", session_id="s1", turn=1)

    events = [record["event"] for record in _records(tmp_path / "s1.jsonl")]
    assert events == ["turn.start", "turn.end"]


def test_it_should_write_one_file_per_session(tmp_path):
    tracer = Tracer(tmp_path, mask=_identity)

    tracer.event("turn.start", session_id="s1", turn=1)
    tracer.event("turn.start", session_id="s2", turn=1)

    assert sorted(path.name for path in tmp_path.glob("*.jsonl")) == [
        "s1.jsonl",
        "s2.jsonl",
    ]


def test_it_should_create_the_trace_directory_when_it_is_missing(tmp_path):
    trace_dir = tmp_path / "traces" / "2026-11-03"
    tracer = Tracer(trace_dir, mask=_identity)

    tracer.event("turn.start", session_id="s1", turn=1)

    assert (trace_dir / "s1.jsonl").is_file()


def test_it_should_refuse_to_be_built_without_a_mask_function(tmp_path):
    with pytest.raises((TypeError, ValueError)):
        Tracer(tmp_path)  # type: ignore[call-arg]


def test_it_should_refuse_to_be_built_with_a_none_mask_function(tmp_path):
    with pytest.raises((TypeError, ValueError)):
        Tracer(tmp_path, mask=None)  # type: ignore[arg-type]


def test_it_should_write_nothing_from_a_null_tracer(tmp_path):
    tracer = NullTracer()

    tracer.event("turn.start", session_id="s1", turn=1, **{INPUT_FIELD: "oi"})

    assert list(tmp_path.iterdir()) == []


def test_it_should_write_text_through_the_injected_mask_function(tmp_path):
    tracer = Tracer(tmp_path, mask=lambda text: text.replace("secret", "[redacted]"))

    tracer.event(
        "turn.start", session_id="s1", turn=1, **{INPUT_FIELD: "the secret word"}
    )

    [record] = _records(tmp_path / "s1.jsonl")
    assert record[INPUT_FIELD] == "the [redacted] word"


def test_it_should_mask_text_nested_in_dicts_and_lists(tmp_path):
    tracer = Tracer(tmp_path, mask=lambda text: text.replace("secret", "[redacted]"))

    tracer.event(
        "tool.call",
        session_id="s1",
        turn=1,
        **{"gen_ai.tool.call.arguments": {"outer": [{"inner": "a secret"}]}},
    )

    [record] = _records(tmp_path / "s1.jsonl")
    assert record["gen_ai.tool.call.arguments"] == {
        "outer": [{"inner": "a [redacted]"}]
    }


def test_it_should_write_a_dated_model_id_without_masking_it(tmp_path, guard):
    tracer = Tracer(tmp_path, mask=guard.mask)

    tracer.event(
        "model.call",
        session_id="s1",
        turn=1,
        **{"gen_ai.response.model": "claude-haiku-4-5-20251001"},
    )

    [record] = _records(tmp_path / "s1.jsonl")
    assert record["gen_ai.response.model"] == "claude-haiku-4-5-20251001"


def test_it_should_still_mask_a_free_field_that_looks_like_a_model_id(tmp_path):
    tracer = Tracer(tmp_path, mask=lambda text: text.replace("2025", "[masked]"))

    tracer.event("model.call", session_id="s1", turn=1, note="claude-2025")

    [record] = _records(tmp_path / "s1.jsonl")
    assert record["note"] == "claude-[masked]"


@pytest.mark.parametrize("raw", RAW_PII)
def test_it_should_not_write_personal_data_masked_by_the_guard(tmp_path, guard, raw):
    tracer = Tracer(tmp_path, mask=guard.mask)

    tracer.event(
        "turn.start",
        session_id="s1",
        turn=1,
        **{INPUT_FIELD: [{"role": "user", "parts": [f"contato: {raw}"]}]},
    )

    assert raw not in (tmp_path / "s1.jsonl").read_text(encoding="utf-8")


def test_it_should_write_a_cpf_as_the_guard_pseudonym(tmp_path, guard):
    tracer = Tracer(tmp_path, mask=guard.mask)

    tracer.event(
        "turn.start", session_id="s1", turn=1, **{INPUT_FIELD: "CPF 529.982.247-25"}
    )

    [record] = _records(tmp_path / "s1.jsonl")
    assert re.search(r"cpf#[0-9a-f]{8}", record[INPUT_FIELD]) is not None


def test_it_should_write_free_text_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(CONTENT_ENV, raising=False)
    tracer = Tracer(tmp_path, mask=_identity)

    tracer.event(
        "turn.start", session_id="s1", turn=1, **{INPUT_FIELD: "quero devolver"}
    )

    [record] = _records(tmp_path / "s1.jsonl")
    assert record[INPUT_FIELD] == "quero devolver"


@pytest.mark.parametrize("field", [INPUT_FIELD, OUTPUT_FIELD])
def test_it_should_write_only_the_length_of_free_text_when_content_is_off(
    tmp_path, monkeypatch, field
):
    monkeypatch.setenv(CONTENT_ENV, "0")
    text = "frase privada com exatamente 37 chars"
    tracer = Tracer(tmp_path, mask=_identity)

    tracer.event("turn.end", session_id="s1", turn=1, **{field: text})

    [record] = _records(tmp_path / "s1.jsonl")
    assert "frase" not in (tmp_path / "s1.jsonl").read_text(encoding="utf-8")
    assert str(len(text)) in json.dumps(record[field])


def test_it_should_keep_non_text_fields_when_content_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv(CONTENT_ENV, "0")
    tracer = Tracer(tmp_path, mask=_identity)

    tracer.event(
        "model.call",
        session_id="s1",
        turn=1,
        **{"gen_ai.request.model": "claude-haiku-5-5", "gen_ai.usage.input_tokens": 12},
    )

    [record] = _records(tmp_path / "s1.jsonl")
    assert record["gen_ai.request.model"] == "claude-haiku-5-5"
    assert record["gen_ai.usage.input_tokens"] == 12


def test_it_should_cut_a_long_field_and_say_how_much_was_cut(tmp_path):
    original = "a" * 500_000
    tracer = Tracer(tmp_path, mask=_identity)

    tracer.event("turn.end", session_id="s1", turn=1, **{OUTPUT_FIELD: original})

    [record] = _records(tmp_path / "s1.jsonl")
    value = record[OUTPUT_FIELD]
    marker = re.search(r"\[truncated (\d+) chars\]$", value)
    assert marker is not None
    assert len(value[: marker.start()].rstrip()) + int(marker.group(1)) == len(original)


def test_it_should_truncate_after_masking(tmp_path):
    original = "z" * 500_000
    tracer = Tracer(tmp_path, mask=lambda text: text.replace("z", "zz"))

    tracer.event("turn.end", session_id="s1", turn=1, **{OUTPUT_FIELD: original})

    [record] = _records(tmp_path / "s1.jsonl")
    value = record[OUTPUT_FIELD]
    marker = re.search(r"\[truncated (\d+) chars\]$", value)
    assert marker is not None
    masked_length = 2 * len(original)
    assert len(value[: marker.start()].rstrip()) + int(marker.group(1)) == masked_length


def test_it_should_not_mark_a_short_field_as_truncated(tmp_path):
    tracer = Tracer(tmp_path, mask=_identity)

    tracer.event(
        "turn.end", session_id="s1", turn=1, **{OUTPUT_FIELD: "resposta curta"}
    )

    [record] = _records(tmp_path / "s1.jsonl")
    assert record[OUTPUT_FIELD] == "resposta curta"


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r"])
def test_it_should_keep_one_line_when_a_field_embeds_a_fake_log_line(
    tmp_path, separator
):
    fake_line = json.dumps({"event": "turn.end", "session_id": "forged", "turn": 99})
    text = f"oi{separator}{fake_line}{separator}tchau"
    tracer = Tracer(tmp_path, mask=_identity)

    tracer.event("turn.start", session_id="s1", turn=1, **{INPUT_FIELD: text})

    lines = _lines(tmp_path / "s1.jsonl")
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "turn.start"


def test_it_should_not_interleave_lines_written_from_eight_threads(tmp_path):
    tracer = Tracer(tmp_path, mask=_identity)
    payload = "x" * 20_000
    barrier = threading.Barrier(THREADS)

    def write(worker: int) -> None:
        barrier.wait()
        for sequence in range(EVENTS_PER_THREAD):
            tracer.event(
                "model.call",
                session_id="shared",
                turn=1,
                **{"pet_helper.worker": worker, "pet_helper.sequence": sequence},
                **{OUTPUT_FIELD: payload},
            )

    threads = [threading.Thread(target=write, args=(w,)) for w in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = _records(tmp_path / "shared.jsonl")
    written = {(r["pet_helper.worker"], r["pet_helper.sequence"]) for r in records}
    assert len(records) == THREADS * EVENTS_PER_THREAD
    assert len(written) == THREADS * EVENTS_PER_THREAD


@pytest.mark.parametrize("model", PRICED_MODELS)
def test_it_should_price_input_tokens_like_genai_prices(model):
    usage = _langchain_usage(input_tokens=100_000)

    cost = cost_usd("anthropic", model, usage)

    assert float(cost) > 0
    assert float(cost) == pytest.approx(_expected_price(model, input_tokens=100_000))


@pytest.mark.parametrize("model", PRICED_MODELS)
def test_it_should_price_output_tokens_like_genai_prices(model):
    usage = _langchain_usage(output_tokens=50_000)

    cost = cost_usd("anthropic", model, usage)

    assert float(cost) > 0
    assert float(cost) == pytest.approx(_expected_price(model, output_tokens=50_000))


@pytest.mark.parametrize("model", PRICED_MODELS)
def test_it_should_price_cache_read_tokens_like_genai_prices(model):
    usage = _langchain_usage(input_tokens=80_000, cache_read=60_000)

    cost = cost_usd("anthropic", model, usage)

    expected = _expected_price(model, input_tokens=80_000, cache_read=60_000)
    assert float(cost) == pytest.approx(expected)


@pytest.mark.parametrize("model", PRICED_MODELS)
def test_it_should_price_cache_write_tokens_like_genai_prices(model):
    usage = _langchain_usage(input_tokens=80_000, cache_write=60_000)

    cost = cost_usd("anthropic", model, usage)

    expected = _expected_price(model, input_tokens=80_000, cache_write=60_000)
    assert float(cost) == pytest.approx(expected)


@pytest.mark.parametrize("model", PRICED_MODELS)
def test_it_should_price_a_mixed_usage_like_genai_prices(model):
    usage = _langchain_usage(
        input_tokens=90_000, output_tokens=3_000, cache_read=50_000, cache_write=20_000
    )

    cost = cost_usd("anthropic", model, usage)

    expected = _expected_price(
        model,
        input_tokens=90_000,
        output_tokens=3_000,
        cache_read=50_000,
        cache_write=20_000,
    )
    assert float(cost) == pytest.approx(expected)


@pytest.mark.parametrize("strict", [False, True])
def test_it_should_cost_nothing_for_ollama(strict):
    usage = _langchain_usage(input_tokens=10_000, output_tokens=2_000)

    cost = cost_usd("ollama", "llama3.1", usage, strict=strict)

    assert cost == 0


def test_it_should_raise_for_an_unknown_model_in_strict_mode():
    usage = _langchain_usage(input_tokens=10, output_tokens=10)

    with pytest.raises(UnknownPriceError):
        cost_usd("anthropic", "claude-never-released-9", usage, strict=True)


def test_it_should_return_no_cost_for_an_unknown_model_outside_strict_mode():
    usage = _langchain_usage(input_tokens=10, output_tokens=10)

    cost = cost_usd("anthropic", "claude-never-released-9", usage)

    assert cost is None


def test_it_should_do_nothing_when_a_null_tracer_shuts_down():
    assert NullTracer().shutdown() is None
