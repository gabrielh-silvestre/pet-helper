"""Flush and shutdown of the span pipeline, and the LangSmith tracer kept off."""

import logging
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from opentelemetry.sdk.trace.export import SpanExporter

from pet_helper.conversation import build_assistant
from pet_helper.evals import round as round_module
from pet_helper.gateway import FakeGateway
from pet_helper.trace import Tracer, turn_scope
from tests.unit.otel_fixtures import (
    KIT_DIR,
    SESSION_ID,
    count_shutdowns,
    file_lines,
    finish,
    mask,
    roots,
    run_turns,
    span_file,
    traced,
)
from tests.unit.otel_support import ScriptedChat, ScriptedGateway
from tests.unit.test_cli import (
    _configure_cli_env,  # noqa: F401  (autouse: keeps the CLI environment clean)
    _run_chat,
)
from tests.unit.test_eval_round import play
from tests.unit.test_eval_task import (
    Gateways,
    _isolated_environment,  # noqa: F401  (autouse: keeps the environment clean)
    make_case,
)

pytest_plugins = ["tests.unit.otel_fixtures"]
pytestmark = pytest.mark.usefixtures("_otel_clean")


@pytest.fixture
def results_root(tmp_path) -> Path:
    return tmp_path / "results"


class _FailingExporter(SpanExporter):
    def export(self, spans):
        raise RuntimeError("otlp is down")


@pytest.fixture
def _chat_in_tmp_path(
    tmp_path,
    monkeypatch,
    _configure_cli_env,  # noqa: F811
    _isolated_environment,  # noqa: F811
    _otel_clean,
):
    # the other fixtures chdir and drop the kit variable: this one runs last
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(KIT_DIR))


def _chat_span_files() -> list[Path]:
    return sorted(Path("traces", date.today().isoformat(), "spans").glob("*.jsonl"))


# chat


@pytest.mark.usefixtures("_chat_in_tmp_path")
def test_it_should_shut_the_tracer_down_when_the_chat_ends_at_end_of_input(monkeypatch):
    shutdowns = count_shutdowns(monkeypatch)

    _run_chat(monkeypatch, ["Olá"], FakeGateway(["Oi!"]))

    assert len(shutdowns) == 1


@pytest.mark.usefixtures("_chat_in_tmp_path")
def test_it_should_shut_the_tracer_down_when_the_chat_is_interrupted_while_idle(
    monkeypatch,
):
    shutdowns = count_shutdowns(monkeypatch)

    code = _run_chat(monkeypatch, [], FakeGateway(), exhausted=KeyboardInterrupt)

    assert (code, len(shutdowns)) == (130, 1)


@pytest.mark.usefixtures("_chat_in_tmp_path")
def test_it_should_shut_the_tracer_down_when_the_chat_is_interrupted_during_a_turn(
    monkeypatch,
):
    shutdowns = count_shutdowns(monkeypatch)

    code = _run_chat(monkeypatch, ["Olá"], FakeGateway([KeyboardInterrupt()]))

    assert (code, len(shutdowns)) == (130, 1)


@pytest.mark.usefixtures("_chat_in_tmp_path")
def test_it_should_keep_the_root_span_of_an_interrupted_turn_in_the_span_file(
    monkeypatch,
):
    _run_chat(monkeypatch, ["Olá"], FakeGateway([KeyboardInterrupt()]))

    (path,) = _chat_span_files()
    assert [line["parent_id"] for line in file_lines(path)].count(None) == 1


@pytest.mark.usefixtures("_chat_in_tmp_path")
def test_it_should_write_a_complete_span_file_for_a_chat_that_ends_normally(
    monkeypatch,
):
    _run_chat(monkeypatch, ["Olá"], ScriptedGateway(["Oi, tudo bem?"]))

    (path,) = _chat_span_files()
    names = [line["name"] for line in file_lines(path)]
    assert any(name.endswith(".chat") for name in names)


# round


def test_it_should_write_the_span_files_of_a_round_under_its_traces_folder(
    results_root,
):
    play(results_root, [make_case()], Gateways(), runs=1)

    files = list((results_root / "round-1" / "traces" / "spans").glob("*.jsonl"))
    assert len(files) == 1
    assert [line["parent_id"] for line in file_lines(files[0])].count(None) == 1


def test_it_should_shut_the_tracer_down_before_the_round_reads_the_traces(
    results_root, monkeypatch
):
    order: list[str] = []
    original_shutdown = Tracer.shutdown
    original_collect = round_module._collect

    def shutdown(self):
        order.append("shutdown")
        original_shutdown(self)

    def collect(*args, **kwargs):
        order.append("collect")
        return original_collect(*args, **kwargs)

    monkeypatch.setattr(Tracer, "shutdown", shutdown)
    monkeypatch.setattr(round_module, "_collect", collect)

    play(results_root, [make_case()], Gateways(), runs=1)

    assert order.index("shutdown") < order.index("collect")


# exporter failure


def test_it_should_not_raise_on_shutdown_when_an_exporter_fails(tmp_path):
    tracer, memory = traced(tmp_path, _FailingExporter())
    build_assistant(
        gateway=ScriptedGateway(["Olá!"]), tracer=tracer, kit_dir=KIT_DIR
    ).turn(SESSION_ID, "Oi")

    spans = finish(tracer, memory)

    assert len(roots(spans)) == 1


def test_it_should_keep_the_span_file_complete_when_an_exporter_fails(tmp_path):
    tracer, memory = traced(tmp_path, _FailingExporter())
    build_assistant(
        gateway=ScriptedGateway(["Olá!"]), tracer=tracer, kit_dir=KIT_DIR
    ).turn(SESSION_ID, "Oi")

    spans = finish(tracer, memory)

    assert len(file_lines(span_file(tmp_path))) == len(spans)


def test_it_should_log_the_error_of_a_failing_exporter(tmp_path, caplog):
    tracer, memory = traced(tmp_path, _FailingExporter())
    build_assistant(
        gateway=ScriptedGateway(["Olá!"]), tracer=tracer, kit_dir=KIT_DIR
    ).turn(SESSION_ID, "Oi")

    with caplog.at_level(logging.ERROR):
        finish(tracer, memory)

    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_it_should_deliver_the_spans_to_the_extra_exporters_on_shutdown(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway(["Olá!"]), "Oi")

    assert len(roots(spans)) == 1


# LangSmith own tracer


def test_it_should_create_a_langsmith_tracer_outside_a_turn_when_tracing_is_on(
    langsmith_on,
):
    ScriptedChat(script=[AIMessage("oi")]).invoke([HumanMessage("oi")])

    assert len(langsmith_on) > 0


def test_it_should_not_create_a_langsmith_tracer_inside_a_turn_scope(
    tmp_path, langsmith_on
):
    tracer = Tracer(tmp_path, mask=mask())

    with turn_scope(tracer, SESSION_ID, 1):
        ScriptedChat(script=[AIMessage("oi")]).invoke([HumanMessage("oi")])

    assert langsmith_on == []


def test_it_should_not_create_a_langsmith_tracer_for_a_full_assistant_turn(
    tmp_path, langsmith_on
):
    run_turns(tmp_path, ScriptedGateway(["Olá!"]), "Oi")

    assert langsmith_on == []


def test_it_should_not_create_a_langsmith_tracer_in_a_round(results_root, langsmith_on):
    play(results_root, [make_case()], Gateways(), runs=1)

    assert langsmith_on == []
