"""The span files: routed per session under spans/, one JSON line per span, beside the old JSONL sink."""

import pytest

from pet_helper.cli import main
from pet_helper.conversation import build_assistant
from pet_helper.trace.otel import SessionRoutingExporter
from tests.unit.otel_fixtures import (
    KIT_DIR,
    POLICY_REPLY,
    RAW_PII,
    SESSION_ID,
    attribute,
    file_lines,
    finish,
    make_span,
    mask,
    run_turns,
    span_file,
    traced,
)
from tests.unit.otel_support import ScriptedGateway, tool_call

pytest_plugins = ["tests.unit.otel_fixtures"]
pytestmark = pytest.mark.usefixtures("_otel_clean")

DIGIT_SESSION = "52998224725"


def _policy_gateway() -> ScriptedGateway:
    return ScriptedGateway(
        [tool_call("search_policies", {"query": "devolução de ração"}), POLICY_REPLY]
    )


def test_it_should_route_each_span_to_the_file_of_its_session(tmp_path):
    exporter = SessionRoutingExporter(tmp_path, mask())

    exporter.export(
        [
            make_span(name="a", session_id="session-a"),
            make_span(name="b", session_id="session-b", span_id=2),
        ]
    )

    names = {
        session: [line["name"] for line in file_lines(tmp_path / f"{session}.jsonl")]
        for session in ("session-a", "session-b")
    }
    assert names == {"session-a": ["a"], "session-b": ["b"]}


def test_it_should_route_by_the_raw_session_id_even_when_it_is_all_digits(tmp_path):
    exporter = SessionRoutingExporter(tmp_path, mask())

    exporter.export([make_span(session_id=DIGIT_SESSION)])

    assert [path.name for path in tmp_path.iterdir()] == [f"{DIGIT_SESSION}.jsonl"]


def test_it_should_write_one_compact_json_line_per_span(tmp_path):
    exporter = SessionRoutingExporter(tmp_path, mask())

    exporter.export(
        [
            make_span(name="a"),
            make_span(name="b", span_id=2),
            make_span(name="c", span_id=3),
        ]
    )

    lines = (tmp_path / "session-1.jsonl").read_text(encoding="utf-8").splitlines()
    assert [line.startswith("{") and line.endswith("}") for line in lines] == [True] * 3


def test_it_should_mask_pii_before_writing_the_session_file(tmp_path):
    exporter = SessionRoutingExporter(tmp_path, mask())

    exporter.export(
        [
            make_span(
                attributes={"custom.note": "maria.silva@example.com 529.982.247-25"}
            )
        ]
    )

    written = (tmp_path / "session-1.jsonl").read_text(encoding="utf-8")
    assert [pii for pii in RAW_PII if pii in written] == []


def test_it_should_append_to_the_session_file_across_exports(tmp_path):
    exporter = SessionRoutingExporter(tmp_path, mask())

    exporter.export([make_span(name="a")])
    exporter.export([make_span(name="b", span_id=2)])

    assert [line["name"] for line in file_lines(tmp_path / "session-1.jsonl")] == [
        "a",
        "b",
    ]


def test_it_should_write_the_spans_of_a_session_under_the_spans_folder(tmp_path):
    spans = run_turns(tmp_path, _policy_gateway(), "Como devolvo uma ração?")

    written = file_lines(span_file(tmp_path))
    assert len(written) == len(spans)


def test_it_should_write_every_span_of_a_turn_before_the_tracer_shuts_down(tmp_path):
    tracer, _ = traced(tmp_path)
    assistant = build_assistant(
        gateway=_policy_gateway(), tracer=tracer, kit_dir=KIT_DIR
    )

    assistant.turn(SESSION_ID, "Como devolvo uma ração?")

    written = file_lines(span_file(tmp_path))
    assert [line["parent_id"] for line in written].count(None) == 1


def test_it_should_keep_the_spans_of_two_sessions_in_separate_files(tmp_path):
    tracer, memory = traced(tmp_path)
    assistant = build_assistant(
        gateway=ScriptedGateway(["Olá A!", "Olá B!"]), tracer=tracer, kit_dir=KIT_DIR
    )

    assistant.turn("session-a", "Oi A")
    assistant.turn("session-b", "Oi B")
    finish(tracer, memory)

    sessions = {
        session: {
            line["attributes"]["session_id"]
            for line in file_lines(span_file(tmp_path, session))
        }
        for session in ("session-a", "session-b")
    }
    assert sessions == {"session-a": {"session-a"}, "session-b": {"session-b"}}


def test_it_should_keep_writing_the_old_jsonl_sink_beside_the_span_files(tmp_path):
    run_turns(tmp_path, _policy_gateway(), "Como devolvo uma ração?")

    kinds = {line["event"] for line in file_lines(tmp_path / f"{SESSION_ID}.jsonl")}
    assert {"turn.start", "model.call", "tool.call", "turn.end"} <= kinds


def test_it_should_leave_only_the_sink_and_the_spans_folder_without_a_langsmith_key(
    tmp_path,
):
    run_turns(tmp_path, ScriptedGateway(["Olá!"]), "Oi")

    assert {path.name for path in tmp_path.iterdir()} == {
        f"{SESSION_ID}.jsonl",
        "spans",
    }


def test_it_should_answer_with_the_scripted_reply_when_only_the_file_exporter_runs(
    tmp_path,
):
    tracer, _ = traced(tmp_path)
    assistant = build_assistant(
        gateway=ScriptedGateway(["Olá!"]), tracer=tracer, kit_dir=KIT_DIR
    )

    reply = assistant.turn(SESSION_ID, "Oi")

    assert reply.text == "Olá!"


def test_it_should_let_the_metrics_command_ignore_the_spans_folder(tmp_path, capsys):
    run_turns(tmp_path, _policy_gateway(), "Como devolvo uma ração?")

    code = main(["metrics", str(tmp_path)])

    assert (code, SESSION_ID in capsys.readouterr().out) == (0, True)


def test_it_should_tag_every_exported_span_with_the_session_and_the_turn(tmp_path):
    spans = run_turns(tmp_path, _policy_gateway(), "Como devolvo uma ração?")

    assert {
        (attribute(span, "session_id"), attribute(span, "turn")) for span in spans
    } == {(SESSION_ID, 1)}
