"""The LangSmith destination chosen by LANGSMITH_API_KEY, the attributes it reads and the rounds."""

import json
from collections import Counter
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from pet_helper.conversation import build_assistant
from pet_helper.gateway import FakeGateway
from pet_helper.trace import Tracer, otel
from tests.unit.otel_fixtures import (
    KIT_DIR,
    PII_TEXT,
    RAW_PII,
    SESSION_ID,
    all_strings,
    attribute,
    file_lines,
    mask,
    model_spans,
    roots,
    run_turns,
    span_file,
    tool_spans,
)
from tests.unit.otel_support import ScriptedGateway, tool_call
from tests.unit.test_cli import _run_chat
from tests.unit.test_eval_round import play
from tests.unit.test_eval_task import (
    Gateways,
    _isolated_environment,  # noqa: F401  (autouse: keeps the environment clean)
    make_case,
)

pytest_plugins = ["tests.unit.otel_fixtures"]
pytestmark = pytest.mark.usefixtures("_otel_clean")

ENDPOINT = "https://api.smith.langchain.com/otel/v1/traces"
API_KEY = "lsv2-test-key"
DEFAULT_PROJECT = "pet-helper"
DIGIT_SESSION = "52998224725"


class OtlpProbe:
    """Stands in for OTLPSpanExporter: records how it was built and what it was asked to send."""

    def __init__(self) -> None:
        self.built: list[dict] = []
        self.spans: list[ReadableSpan] = []
        self.failure: str | None = None
        self.attempts = 0

    def __call__(self, **kwargs) -> SpanExporter:
        self.built.append(kwargs)
        return _ProbeExporter(self)


class _ProbeExporter(SpanExporter):
    def __init__(self, probe: OtlpProbe) -> None:
        self._probe = probe

    def export(self, spans) -> SpanExportResult:
        self._probe.attempts += 1
        if self._probe.failure == "raises":
            raise RuntimeError("otlp is down")
        if self._probe.failure == "returns_failure":
            return SpanExportResult.FAILURE
        self._probe.spans.extend(spans)
        return SpanExportResult.SUCCESS


@pytest.fixture
def otlp(monkeypatch) -> OtlpProbe:
    probe = OtlpProbe()
    # the real exporter would post to the LangSmith endpoint
    monkeypatch.setattr(otel, "OTLPSpanExporter", probe, raising=False)
    return probe


@pytest.fixture
def api_key(monkeypatch, _otel_clean) -> None:
    # _otel_clean deletes the key: it has to run first, and a function mark would order it after
    monkeypatch.setenv("LANGSMITH_API_KEY", API_KEY)


@pytest.fixture
def results_root(tmp_path) -> Path:
    return tmp_path / "results"


def _policy_gateway() -> ScriptedGateway:
    return ScriptedGateway(
        [
            tool_call("search_policies", {"query": "devolução de ração"}),
            "Você pode devolver em 7 dias.\nFonte: Troca e devolução",
        ]
    )


def _chat_turn(tmp_path, text: str = "Oi", reply: str = "Olá!"):
    assistant = build_assistant(
        gateway=ScriptedGateway([reply]), trace_dir=tmp_path, kit_dir=KIT_DIR
    )
    answer = assistant.turn(SESSION_ID, text)
    assistant.close()
    return answer


def _span_processors(tracer: Tracer) -> list[type]:
    # the order of registration is the contract here and the SDK exposes it only on this tuple
    registered = tracer._provider._active_span_processor._span_processors
    return [
        type(item)
        for item in registered
        if isinstance(item, SimpleSpanProcessor | BatchSpanProcessor)
    ]


# LANGSMITH_API_KEY turns the OTLP exporter on


@pytest.mark.usefixtures("api_key")
def test_it_should_build_the_otlp_exporter_for_the_langsmith_endpoint_with_the_key(
    tmp_path, otlp
):
    _chat_turn(tmp_path)

    assert otlp.built == [
        {
            "endpoint": ENDPOINT,
            "timeout": 10,
            "headers": {"x-api-key": API_KEY, "Langsmith-Project": DEFAULT_PROJECT},
        }
    ]


@pytest.mark.usefixtures("api_key")
def test_it_should_send_the_project_named_in_langsmith_project(
    tmp_path, otlp, monkeypatch
):
    monkeypatch.setenv("LANGSMITH_PROJECT", "pet-helper-evals")

    _chat_turn(tmp_path)

    assert [call["headers"]["Langsmith-Project"] for call in otlp.built] == [
        "pet-helper-evals"
    ]


def test_it_should_build_no_otlp_exporter_without_the_key(tmp_path, otlp):
    _chat_turn(tmp_path)

    assert otlp.built == []


def test_it_should_build_no_otlp_exporter_when_the_key_is_blank(
    tmp_path, otlp, monkeypatch
):
    monkeypatch.setenv("LANGSMITH_API_KEY", "")

    _chat_turn(tmp_path)

    assert otlp.built == []


@pytest.mark.usefixtures("api_key")
def test_it_should_send_the_spans_of_a_turn_to_the_otlp_exporter(tmp_path, otlp):
    _chat_turn(tmp_path)

    assert len(roots(otlp.spans)) == 1


@pytest.mark.usefixtures("api_key")
def test_it_should_mask_the_pii_before_the_spans_reach_the_otlp_exporter(
    tmp_path, otlp
):
    _chat_turn(tmp_path, text=f"Oi, {PII_TEXT}")

    sent = "\n".join(text for span in otlp.spans for text in all_strings(span))
    assert (sent != "", [pii for pii in RAW_PII if pii in sent]) == (True, [])


@pytest.mark.usefixtures("api_key")
def test_it_should_keep_writing_the_local_span_file_when_the_key_is_set(tmp_path, otlp):
    _chat_turn(tmp_path)

    assert len(roots(otlp.spans)) == len(
        [line for line in file_lines(span_file(tmp_path)) if line["parent_id"] is None]
    )


@pytest.mark.usefixtures("api_key")
def test_it_should_register_the_otlp_processor_after_the_file_processor(tmp_path, otlp):
    tracer = Tracer(tmp_path, mask=mask())

    processors = _span_processors(tracer)
    tracer.shutdown()

    assert processors == [SimpleSpanProcessor, BatchSpanProcessor]


def test_it_should_register_only_the_file_processor_without_the_key(tmp_path, otlp):
    tracer = Tracer(tmp_path, mask=mask())

    processors = _span_processors(tracer)
    tracer.shutdown()

    assert processors == [SimpleSpanProcessor]


# a failing OTLP exporter


@pytest.mark.usefixtures("api_key")
@pytest.mark.parametrize("failure", ["raises", "returns_failure"])
def test_it_should_answer_the_turn_when_the_otlp_exporter_fails(
    tmp_path, otlp, failure
):
    otlp.failure = failure

    answer = _chat_turn(tmp_path, reply="Olá, tudo bem!")

    assert (answer.text, otlp.attempts > 0) == ("Olá, tudo bem!", True)


@pytest.mark.usefixtures("api_key")
@pytest.mark.parametrize("failure", ["raises", "returns_failure"])
def test_it_should_keep_the_span_file_complete_when_the_otlp_exporter_fails(
    tmp_path, otlp, failure
):
    otlp.failure = failure

    _chat_turn(tmp_path)

    lines = file_lines(span_file(tmp_path))
    assert ([line["parent_id"] for line in lines].count(None), otlp.attempts > 0) == (
        1,
        True,
    )


@pytest.mark.usefixtures("api_key")
def test_it_should_finish_a_round_when_the_otlp_exporter_fails(results_root, otlp):
    otlp.failure = "raises"

    play(results_root, [make_case()], Gateways(), runs=1)

    (path,) = (results_root / "round-1" / "traces" / "spans").glob("*.jsonl")
    assert (
        [line["parent_id"] for line in file_lines(path)].count(None),
        otlp.attempts > 0,
    ) == (1, True)


# langsmith.span.kind


def test_it_should_mark_the_turn_root_span_as_a_chain(tmp_path):
    spans = run_turns(tmp_path, _policy_gateway(), "Como devolvo uma ração?")

    (root,) = roots(spans)
    assert attribute(root, "langsmith.span.kind") == "chain"


def test_it_should_mark_every_model_span_as_an_llm(tmp_path):
    spans = run_turns(tmp_path, _policy_gateway(), "Como devolvo uma ração?")

    assert {attribute(span, "langsmith.span.kind") for span in model_spans(spans)} == {
        "llm"
    }


def test_it_should_mark_every_tool_span_as_a_tool(tmp_path):
    spans = run_turns(tmp_path, _policy_gateway(), "Como devolvo uma ração?")

    assert {attribute(span, "langsmith.span.kind") for span in tool_spans(spans)} == {
        "tool"
    }


# turn input and output as messages


def test_it_should_record_the_turn_input_as_a_json_list_of_one_user_message(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway(["Certo."]), "Quero ajuda")

    (root,) = roots(spans)
    assert json.loads(attribute(root, "gen_ai.input.messages")) == [
        {"role": "user", "content": "Quero ajuda"}
    ]


def test_it_should_record_the_turn_output_as_a_json_list_of_one_assistant_message(
    tmp_path,
):
    spans = run_turns(tmp_path, ScriptedGateway(["Olá, tudo bem!"]), "Oi")

    (root,) = roots(spans)
    assert json.loads(attribute(root, "gen_ai.output.messages")) == [
        {"role": "assistant", "content": "Olá, tudo bem!"}
    ]


def test_it_should_keep_the_input_messages_valid_json_with_the_pii_masked(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway(["Certo."]), f'Oi, "{PII_TEXT}"\n')

    (root,) = roots(spans)
    content = json.loads(attribute(root, "gen_ai.input.messages"))[0]["content"]
    assert [pii for pii in RAW_PII if pii in content] == []


def test_it_should_keep_the_output_messages_valid_json_with_the_pii_masked(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway([f'Anotei "{PII_TEXT}"\n']), "Oi")

    (root,) = roots(spans)
    content = json.loads(attribute(root, "gen_ai.output.messages"))[0]["content"]
    assert [pii for pii in RAW_PII if pii in content] == []


def test_it_should_put_the_input_and_output_messages_only_on_the_root_span(tmp_path):
    spans = run_turns(tmp_path, _policy_gateway(), "Como devolvo uma ração?")

    carriers = [
        span
        for span in spans
        if attribute(span, "gen_ai.input.messages") is not None
        or attribute(span, "gen_ai.output.messages") is not None
    ]
    assert carriers == roots(spans)


# session and turn as LangSmith metadata


def test_it_should_tag_each_root_span_with_the_session_and_turn_as_langsmith_metadata(
    tmp_path,
):
    spans = run_turns(tmp_path, ScriptedGateway(["Olá!", "De nada!"]), "Oi", "Obrigado")

    assert [
        (
            attribute(root, "langsmith.metadata.session_id"),
            attribute(root, "langsmith.metadata.turn"),
        )
        for root in roots(spans)
    ] == [(SESSION_ID, 1), (SESSION_ID, 2)]


def test_it_should_not_mangle_an_all_digit_session_id_in_the_langsmith_metadata(
    tmp_path,
):
    spans = run_turns(
        tmp_path, ScriptedGateway(["Olá!"]), "Oi", session_id=DIGIT_SESSION
    )

    (root,) = roots(spans)
    assert attribute(root, "langsmith.metadata.session_id") == DIGIT_SESSION


# rounds


@pytest.mark.usefixtures("api_key")
def test_it_should_send_the_spans_of_each_round_conversation_through_the_round_exporter(
    results_root, otlp
):
    play(results_root, [make_case("case-a"), make_case("case-b")], Gateways(), runs=1)

    in_files = {
        path.stem: len(file_lines(path))
        for path in (results_root / "round-1" / "traces" / "spans").glob("*.jsonl")
    }
    sent = Counter(
        attribute(span, "session_id")
        for span in otlp.spans
        if attribute(span, "session_id") is not None
    )
    assert (len(in_files), dict(sent)) == (2, in_files)


@pytest.mark.usefixtures("api_key")
def test_it_should_build_the_round_exporter_with_the_langsmith_settings(
    results_root, otlp
):
    play(results_root, [make_case()], Gateways(), runs=1)

    assert {
        (call["endpoint"], call["timeout"], call["headers"]["x-api-key"])
        for call in otlp.built
    } == {(ENDPOINT, 10, API_KEY)}


def test_it_should_send_nothing_from_a_round_without_the_key(results_root, otlp):
    play(results_root, [make_case()], Gateways(), runs=1)

    assert (otlp.built, otlp.spans) == ([], [])


# the endpoint: the LangSmith cloud by default, OTEL_EXPORTER_OTLP_TRACES_ENDPOINT when set

TRACES_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
CUSTOM_ENDPOINT = "https://collector.example.com:4318/custom/traces"


@pytest.mark.usefixtures("api_key")
def test_it_should_send_to_the_traces_endpoint_variable_exactly_as_written(
    tmp_path, otlp, monkeypatch
):
    monkeypatch.setenv(TRACES_ENDPOINT_ENV, CUSTOM_ENDPOINT)

    _chat_turn(tmp_path)

    assert [call["endpoint"] for call in otlp.built] == [CUSTOM_ENDPOINT]


@pytest.mark.usefixtures("api_key")
def test_it_should_keep_the_api_key_header_when_the_traces_endpoint_variable_is_set(
    tmp_path, otlp, monkeypatch
):
    monkeypatch.setenv(TRACES_ENDPOINT_ENV, CUSTOM_ENDPOINT)

    _chat_turn(tmp_path)

    assert [call["headers"]["x-api-key"] for call in otlp.built] == [API_KEY]


@pytest.mark.usefixtures("api_key")
def test_it_should_mask_the_pii_sent_to_the_traces_endpoint_variable(
    tmp_path, otlp, monkeypatch
):
    monkeypatch.setenv(TRACES_ENDPOINT_ENV, CUSTOM_ENDPOINT)

    _chat_turn(tmp_path, text=f"Oi, {PII_TEXT}")

    sent = "\n".join(text for span in otlp.spans for text in all_strings(span))
    assert (sent != "", [pii for pii in RAW_PII if pii in sent]) == (True, [])


@pytest.mark.usefixtures("api_key")
def test_it_should_ignore_langsmith_endpoint_and_keep_the_default_endpoint(
    tmp_path, otlp, monkeypatch
):
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://eu.api.smith.langchain.com")

    _chat_turn(tmp_path)

    assert [call["endpoint"] for call in otlp.built] == [ENDPOINT]


# the chat tells the user it is flushing before it closes

FLUSH_NOTICE = "flushing traces to LangSmith (Ctrl-C to skip)"


@pytest.fixture
def chat_session(monkeypatch, tmp_path, _otel_clean):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(KIT_DIR))


def _stderr_seen_at_shutdown(monkeypatch, capsys) -> list[str]:
    seen: list[str] = []
    original = Tracer.shutdown

    def spy(self):
        seen.append(capsys.readouterr().err)
        original(self)

    monkeypatch.setattr(Tracer, "shutdown", spy)
    return seen


@pytest.mark.usefixtures("api_key", "chat_session")
def test_it_should_print_one_flushing_line_on_stderr_before_closing_the_chat(
    monkeypatch, capsys, otlp
):
    seen = _stderr_seen_at_shutdown(monkeypatch, capsys)

    _run_chat(monkeypatch, [], FakeGateway([]))

    assert seen == [f"{FLUSH_NOTICE}\n"]


@pytest.mark.usefixtures("api_key", "chat_session")
def test_it_should_print_nothing_else_on_stderr_after_the_flushing_line(
    monkeypatch, capsys, otlp
):
    _stderr_seen_at_shutdown(monkeypatch, capsys)

    _run_chat(monkeypatch, [], FakeGateway([]))

    assert capsys.readouterr().err == ""


@pytest.mark.usefixtures("chat_session")
def test_it_should_print_nothing_on_stderr_when_the_chat_closes_without_a_key(
    monkeypatch, capsys, otlp
):
    _run_chat(monkeypatch, [], FakeGateway([]))

    assert capsys.readouterr().err == ""
