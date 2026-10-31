"""The LLM judge: prompt, slices, clients, verdict log, inline scorer and smoke (no real model)."""

import asyncio
import io
import json
import math
import os
import subprocess
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

import anthropic
import httpx2
import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog, read_eval_log
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target
from inspect_ai.solver import Generate, TaskState, solver
from pet_helper.evals.judge import (
    CRITERIA,
    CRITERION_PROMPTS,
    DEFAULT_JUDGE_MODEL,
    JUDGE_MAX_TOKENS,
    AnthropicBatchClient,
    AnthropicJudgeClient,
    BatchCollection,
    BatchItem,
    BatchNotEnded,
    FewShotExample,
    Judged,
    JudgeReplyError,
    Transcript,
    TranscriptTurn,
    Verdict,
    append_verdict,
    build_slice,
    current_verdicts,
    judge,
    judge_case,
    judge_prompt_sha256,
    judge_scorer,
    main,
)

from pet_helper.evals import judge as judge_module
from pet_helper.evals.budget import JUDGE_CALL_RESERVE_USD, RoundBudget
from pet_helper.evals.cases import EvalCase, JudgeEntry
from pet_helper.trace import BudgetExceeded, UnknownPriceError, cost_usd

SLEEP = 0.4
USAGE = {"input_tokens": 1200, "output_tokens": 150}
REPLY = {"critique": "Polite and clear.", "verdict": "pass"}
API_KEY = "sk-ant-test-key"
ANTHROPIC_USAGE = {
    "input_tokens": 25,
    "cache_creation_input_tokens": 10,
    "cache_read_input_tokens": 5,
    "output_tokens": 12,
}
BATCH_ID = "msgbatch_1"


@pytest.fixture(autouse=True)
def _judge_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY)
    monkeypatch.delenv("PET_HELPER_JUDGE_MODEL", raising=False)


# fakes


class FakeClient:
    """Judge client that records each call and sleeps like a network call."""

    def __init__(
        self,
        reply: dict | str | None = None,
        *,
        usage: dict | None = None,
        model: str = DEFAULT_JUDGE_MODEL,
        delay: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.reply = REPLY if reply is None else reply
        self.usage = USAGE if usage is None else usage
        self.model = model
        self.delay = delay
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        self.calls.append((system, user))
        time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return text, self.usage

    @property
    def prompt(self) -> str:
        system, user = self.calls[0]
        return f"{system}\n{user}"


class FakeBatchClient:
    model = DEFAULT_JUDGE_MODEL

    def __init__(self, *, ended_after_polls: int = 0, never_ends: bool = False):
        self.ended_after_polls = ended_after_polls
        self.never_ends = never_ends
        self.submitted: list[list[BatchItem]] = []
        self.collect_calls = 0

    def submit(self, items) -> str:
        self.submitted.append(list(items))
        return "msgbatch_fake"

    def collect(self, batch_id, item_ids) -> BatchCollection:
        self.collect_calls += 1
        if self.never_ends or self.collect_calls <= self.ended_after_polls:
            raise BatchNotEnded(batch_id)
        completions = {item_id: (json.dumps(REPLY), USAGE) for item_id in item_ids}
        return BatchCollection(completions=completions, failed={})


# transcript and cases


def make_transcript(escalation: str | None = None) -> Transcript:
    return Transcript(
        turns=[
            TranscriptTurn("USER-ONE", "REPLY-ONE", ("Shipping policy",)),
            TranscriptTurn("USER-TWO", "REPLY-TWO", ("Returns policy",)),
            TranscriptTurn("USER-THREE", "REPLY-THREE", ("Refund window",)),
        ],
        escalation=escalation,
    )


def make_case(judge_entries: list) -> EvalCase:
    return EvalCase(
        id="case-1",
        today="2026-10-31",
        turns=["USER-ONE", "USER-TWO", "USER-THREE"],
        judge=judge_entries,
    )


def make_verdict(verdict: str = "pass", critique: str = "Fine.") -> Verdict:
    return Verdict(
        critique=critique,
        verdict=verdict,
        usage=USAGE,
        cost_usd=cost_usd("anthropic", DEFAULT_JUDGE_MODEL, USAGE, strict=True),
    )


# the pure function


@pytest.mark.parametrize("expected", ["pass", "fail", "unable"])
def test_it_should_return_the_verdict_the_client_replied_with(expected):
    client = FakeClient({"critique": "Because.", "verdict": expected})

    result = judge("slice", "tone", client)

    assert result.verdict == expected


def test_it_should_keep_the_critique_of_the_reply():
    client = FakeClient({"critique": "Warm but verbose.", "verdict": "fail"})

    result = judge("slice", "tone", client)

    assert result.critique == "Warm but verbose."


def test_it_should_carry_the_usage_of_the_call_in_the_verdict():
    client = FakeClient(usage={"input_tokens": 7, "output_tokens": 3})

    result = judge("slice", "tone", client)

    assert result.usage == {"input_tokens": 7, "output_tokens": 3}


def test_it_should_make_one_call_for_one_criterion():
    client = FakeClient()

    judge("slice", "tone", client)

    assert len(client.calls) == 1


@pytest.mark.parametrize("criterion", CRITERIA)
def test_it_should_ask_for_the_critique_before_the_verdict(criterion):
    client = FakeClient()

    judge("slice", criterion, client)

    system, _ = client.calls[0]
    assert system.index("critique") < system.index("verdict")


@pytest.mark.parametrize("criterion", CRITERIA)
def test_it_should_send_the_slice_to_the_client(criterion):
    client = FakeClient()

    judge("SLICE-MARKER", criterion, client)

    assert "SLICE-MARKER" in client.prompt


@pytest.mark.parametrize("criterion", CRITERIA)
def test_it_should_put_the_instructions_of_the_criterion_in_the_prompt(criterion):
    client = FakeClient()

    judge("slice", criterion, client)

    assert CRITERION_PROMPTS[criterion] in client.prompt


def test_it_should_leave_the_instructions_of_other_criteria_out_of_the_prompt():
    client = FakeClient()

    judge("slice", "tone", client)

    others = [CRITERION_PROMPTS[name] for name in CRITERIA if name != "tone"]
    assert not any(text in client.prompt for text in others)


def test_it_should_reject_an_unknown_criterion_without_calling_the_client():
    client = FakeClient()

    with pytest.raises(ValueError):
        judge("slice", "politeness", client)

    assert client.calls == []


@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        "",
        '["a", "list"]',
        '{"critique": "no verdict"}',
        '{"verdict": "pass"}',
        '{"critique": "x", "verdict": "maybe"}',
        '{"critique": "x", "verdict": "PASS"}',
    ],
)
def test_it_should_raise_a_run_error_when_the_reply_cannot_be_parsed(reply):
    client = FakeClient(reply)

    with pytest.raises(JudgeReplyError):
        judge("slice", "tone", client)


# cost


def test_it_should_price_the_call_with_the_strict_cost_function():
    client = FakeClient()

    result = judge("slice", "tone", client)

    expected = cost_usd("anthropic", client.model, USAGE, strict=True)
    assert result.cost_usd == expected


def test_it_should_refuse_a_model_without_a_price():
    client = FakeClient(model="claude-unknown-9")

    with pytest.raises(UnknownPriceError):
        judge("slice", "tone", client)


# slices


def test_it_should_slice_tone_to_the_last_user_message_and_the_final_reply():
    transcript = make_transcript()

    result = build_slice("tone", transcript)

    assert "USER-THREE" in result and "REPLY-THREE" in result


def test_it_should_leave_earlier_turns_out_of_the_tone_slice():
    transcript = make_transcript()

    result = build_slice("tone", transcript)

    absent = ("USER-ONE", "REPLY-ONE", "USER-TWO", "REPLY-TWO")
    assert not any(marker in result for marker in absent)


def test_it_should_slice_escalation_judgment_to_the_whole_conversation():
    transcript = make_transcript(escalation="ESCALATED-FOR-REFUND")

    result = build_slice("escalation_judgment", transcript)

    for marker in ("USER-ONE", "REPLY-ONE", "USER-TWO", "REPLY-TWO", "USER-THREE"):
        assert marker in result


def test_it_should_put_the_escalation_outcome_in_the_escalation_judgment_slice():
    transcript = make_transcript(escalation="ESCALATED-FOR-REFUND")

    result = build_slice("escalation_judgment", transcript)

    assert "ESCALATED-FOR-REFUND" in result


def test_it_should_slice_dont_know_accuracy_to_the_question_reply_and_policy_titles_of_the_named_turn():
    transcript = make_transcript()

    result = build_slice("dont_know_accuracy", transcript, turn=2)

    for marker in ("USER-TWO", "REPLY-TWO", "Returns policy"):
        assert marker in result


def test_it_should_leave_the_other_turns_out_of_the_dont_know_accuracy_slice():
    transcript = make_transcript()

    result = build_slice("dont_know_accuracy", transcript, turn=2)

    absent = ("USER-ONE", "REPLY-ONE", "Shipping policy", "USER-THREE", "Refund window")
    assert not any(marker in result for marker in absent)


def test_it_should_use_the_only_turn_of_a_single_turn_conversation():
    transcript = Transcript(turns=[make_transcript().turns[0]])

    result = build_slice("dont_know_accuracy", transcript)

    for marker in ("USER-ONE", "REPLY-ONE", "Shipping policy"):
        assert marker in result


# calls per case


def test_it_should_make_two_calls_for_a_case_with_two_criteria():
    client = FakeClient()
    case = make_case(["tone", "escalation_judgment"])

    judge_case(case, make_transcript(), client)

    assert len(client.calls) == 2


def test_it_should_return_one_verdict_per_criterion_of_the_case():
    client = FakeClient()
    case = make_case(["tone", "escalation_judgment"])

    result = judge_case(case, make_transcript(), client)

    assert set(result) == {"tone", "escalation_judgment"}


def test_it_should_make_no_call_for_a_case_without_criteria():
    client = FakeClient()
    case = make_case([])

    result = judge_case(case, make_transcript(), client)

    assert client.calls == [] and result == {}


def test_it_should_judge_the_criteria_the_caller_passes_instead_of_the_case_ones():
    client = FakeClient()
    case = make_case(["tone"])

    result = judge_case(
        case, make_transcript(), client, criteria=["escalation_judgment"]
    )

    assert set(result) == {"escalation_judgment"}
    assert len(client.calls) == 1


def test_it_should_judge_the_turn_a_criterion_entry_names():
    client = FakeClient()
    case = make_case([{"criterion": "dont_know_accuracy", "turn": 2}])

    judge_case(case, make_transcript(), client)

    _, user = client.calls[0]
    assert "USER-TWO" in user and "USER-ONE" not in user


def test_it_should_accept_a_criterion_entry_in_the_caller_override():
    client = FakeClient()
    case = make_case([])

    judge_case(
        case,
        make_transcript(),
        client,
        criteria=[JudgeEntry(criterion="dont_know_accuracy", turn=3)],
    )

    _, user = client.calls[0]
    assert "USER-THREE" in user and "USER-TWO" not in user


# few-shot examples and the prompt hash


def make_example(**overrides) -> FewShotExample:
    fields = {
        "criterion": "tone",
        "slice": "EXAMPLE-SLICE",
        "critique": "EXAMPLE-CRITIQUE",
        "verdict": "fail",
    } | overrides
    return FewShotExample(**fields)


def test_it_should_put_the_few_shot_examples_of_the_criterion_in_the_prompt(
    monkeypatch,
):
    monkeypatch.setattr(judge_module, "FEW_SHOT_EXAMPLES", [make_example()])
    client = FakeClient()

    judge("slice", "tone", client)

    assert "EXAMPLE-SLICE" in client.prompt and "EXAMPLE-CRITIQUE" in client.prompt


def test_it_should_leave_the_few_shot_examples_of_other_criteria_out_of_the_prompt(
    monkeypatch,
):
    monkeypatch.setattr(
        judge_module,
        "FEW_SHOT_EXAMPLES",
        [make_example(criterion="escalation_judgment")],
    )
    client = FakeClient()

    judge("slice", "tone", client)

    assert "EXAMPLE-SLICE" not in client.prompt


def test_it_should_return_a_sha256_hex_digest_as_the_prompt_hash():
    result = judge_prompt_sha256()

    assert len(result) == 64 and int(result, 16) >= 0


def test_it_should_return_the_same_hash_for_the_same_prompt_text():
    first = judge_prompt_sha256()

    second = judge_prompt_sha256()

    assert first == second


def test_it_should_change_the_hash_when_the_prompt_text_changes(monkeypatch):
    before = judge_prompt_sha256()
    edited = {**CRITERION_PROMPTS, "tone": CRITERION_PROMPTS["tone"] + " Be strict."}

    monkeypatch.setattr(judge_module, "CRITERION_PROMPTS", edited)

    assert judge_prompt_sha256() != before


def test_it_should_change_the_hash_when_a_few_shot_example_is_added(monkeypatch):
    before = judge_prompt_sha256()

    monkeypatch.setattr(judge_module, "FEW_SHOT_EXAMPLES", [make_example()])

    assert judge_prompt_sha256() != before


def test_it_should_change_the_hash_when_a_few_shot_example_is_edited(monkeypatch):
    monkeypatch.setattr(judge_module, "FEW_SHOT_EXAMPLES", [make_example()])
    before = judge_prompt_sha256()

    monkeypatch.setattr(
        judge_module, "FEW_SHOT_EXAMPLES", [make_example(critique="Another take.")]
    )

    assert judge_prompt_sha256() != before


# the Anthropic client request


def _message(text: str, usage: dict | None = None) -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_JUDGE_MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": usage or ANTHROPIC_USAGE,
    }


def _sdk(handler) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=API_KEY,
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def _serving_messages(
    text: str = json.dumps(REPLY),
) -> tuple[anthropic.Anthropic, list]:
    payloads: list[dict] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        return httpx2.Response(200, json=_message(text))

    return _sdk(respond), payloads


def _payload_of_one_call(**client_kwargs) -> dict:
    sdk, payloads = _serving_messages()

    AnthropicJudgeClient(sdk, **client_kwargs).complete("SYSTEM", "USER")

    return payloads[0]


def test_it_should_default_the_judge_model_to_sonnet_5_5():
    payload = _payload_of_one_call()

    assert DEFAULT_JUDGE_MODEL == "claude-sonnet-5-5"
    assert payload["model"] == "claude-sonnet-5-5"


def test_it_should_take_the_judge_model_from_the_environment(monkeypatch):
    monkeypatch.setenv("PET_HELPER_JUDGE_MODEL", "claude-opus-5-5")

    payload = _payload_of_one_call()

    assert payload["model"] == "claude-opus-5-5"


def test_it_should_expose_the_configured_model_for_pricing(monkeypatch):
    monkeypatch.setenv("PET_HELPER_JUDGE_MODEL", "claude-opus-5-5")
    sdk, _ = _serving_messages()

    client = AnthropicJudgeClient(sdk)

    assert client.model == "claude-opus-5-5"


def test_it_should_request_medium_effort():
    payload = _payload_of_one_call()

    assert payload["output_config"]["effort"] == "medium"


def test_it_should_set_an_explicit_max_tokens():
    payload = _payload_of_one_call()

    assert payload["max_tokens"] == JUDGE_MAX_TOKENS


def test_it_should_ask_for_a_json_schema_structured_output():
    payload = _payload_of_one_call()

    assert payload["output_config"]["format"]["type"] == "json_schema"


def test_it_should_declare_the_critique_before_the_verdict_in_the_schema():
    payload = _payload_of_one_call()

    schema = payload["output_config"]["format"]["schema"]
    assert list(schema["properties"]) == ["critique", "verdict"]


def test_it_should_restrict_the_verdict_to_pass_fail_or_unable_in_the_schema():
    payload = _payload_of_one_call()

    schema = payload["output_config"]["format"]["schema"]
    assert sorted(schema["properties"]["verdict"]["enum"]) == ["fail", "pass", "unable"]
    assert sorted(schema["required"]) == ["critique", "verdict"]


@pytest.mark.parametrize("parameter", ["temperature", "top_p", "top_k", "tool_choice"])
def test_it_should_not_send_sampling_parameters_or_a_forced_tool_choice(parameter):
    payload = _payload_of_one_call()

    assert parameter not in payload


def test_it_should_send_the_system_prompt_and_the_slice_as_the_user_message():
    payload = _payload_of_one_call()

    assert payload["system"] == "SYSTEM"
    assert payload["messages"] == [{"role": "user", "content": "USER"}]


def test_it_should_return_the_reply_text_of_the_model():
    sdk, _ = _serving_messages()

    text, _ = AnthropicJudgeClient(sdk).complete("SYSTEM", "USER")

    assert json.loads(text) == REPLY


def test_it_should_report_the_usage_with_the_cache_tokens_inside_the_input_count():
    sdk, _ = _serving_messages()

    _, usage = AnthropicJudgeClient(sdk).complete("SYSTEM", "USER")

    assert usage == {
        "input_tokens": 40,
        "output_tokens": 12,
        "input_token_details": {"cache_read": 5, "cache_creation": 10},
    }


def test_it_should_judge_through_the_anthropic_client():
    sdk, _ = _serving_messages()

    result = judge("slice", "tone", AnthropicJudgeClient(sdk))

    assert result.verdict == "pass"
    assert result.cost_usd > 0


# the batch client


def _batch_object(status: str) -> dict:
    return {
        "id": BATCH_ID,
        "type": "message_batch",
        "processing_status": status,
        "request_counts": {
            "processing": 0 if status == "ended" else 3,
            "succeeded": 0,
            "errored": 0,
            "canceled": 0,
            "expired": 0,
        },
        "created_at": "2026-10-31T00:00:00Z",
        "expires_at": "2026-11-01T00:00:00Z",
        "ended_at": "2026-10-31T00:10:00Z" if status == "ended" else None,
        "cancel_initiated_at": None,
        "archived_at": None,
        "results_url": (
            f"https://api.anthropic.com/v1/messages/batches/{BATCH_ID}/results"
            if status == "ended"
            else None
        ),
    }


def _succeeded(custom_id: str) -> dict:
    result = {"type": "succeeded", "message": _message(json.dumps(REPLY))}
    return {"custom_id": custom_id, "result": result}


def _errored(custom_id: str) -> dict:
    error = {"type": "invalid_request_error", "message": "bad request"}
    result = {"type": "errored", "error": {"type": "error", "error": error}}
    return {"custom_id": custom_id, "result": result}


def _serving_batches(result_lines: list[dict], *, status: str = "ended"):
    submitted: list[dict] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/messages/batches":
            submitted.extend(json.loads(request.content)["requests"])
            return httpx2.Response(200, json=_batch_object("in_progress"))
        if path == f"/v1/messages/batches/{BATCH_ID}":
            return httpx2.Response(200, json=_batch_object(status))
        if path == f"/v1/messages/batches/{BATCH_ID}/results":
            body = "\n".join(json.dumps(line) for line in result_lines)
            return httpx2.Response(200, content=body.encode())
        return httpx2.Response(404)

    return AnthropicBatchClient(_sdk(respond)), submitted


ITEMS = [BatchItem("a", "SYSTEM-A", "USER-A"), BatchItem("b", "SYSTEM-B", "USER-B")]


def test_it_should_build_one_batch_request_per_item():
    client, submitted = _serving_batches([])

    client.submit(ITEMS)

    assert [request["custom_id"] for request in submitted] == ["a", "b"]


def test_it_should_return_the_batch_id_on_submit():
    client, _ = _serving_batches([])

    batch_id = client.submit(ITEMS)

    assert batch_id == BATCH_ID


def test_it_should_build_each_batch_request_with_the_same_parameters_as_the_sync_call():
    client, submitted = _serving_batches([])

    client.submit(ITEMS)

    params = submitted[0]["params"]
    assert params["model"] == DEFAULT_JUDGE_MODEL
    assert params["max_tokens"] == JUDGE_MAX_TOKENS
    assert params["output_config"]["effort"] == "medium"
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["system"] == "SYSTEM-A"
    assert params["messages"] == [{"role": "user", "content": "USER-A"}]


@pytest.mark.parametrize("parameter", ["temperature", "tool_choice"])
def test_it_should_not_send_sampling_parameters_or_a_forced_tool_choice_in_a_batch_request(
    parameter,
):
    client, submitted = _serving_batches([])

    client.submit(ITEMS)

    assert all(parameter not in request["params"] for request in submitted)


def test_it_should_map_the_collected_results_back_to_the_item_ids():
    client, _ = _serving_batches([_succeeded("b"), _succeeded("a")])

    collection = client.collect(BATCH_ID, ["a", "b"])

    assert set(collection.completions) == {"a", "b"}
    assert collection.failed == {}


def test_it_should_return_the_reply_text_and_usage_of_each_collected_item():
    client, _ = _serving_batches([_succeeded("a")])

    collection = client.collect(BATCH_ID, ["a"])

    text, usage = collection.completions["a"]
    assert json.loads(text) == REPLY
    assert usage["output_tokens"] == 12


def test_it_should_report_an_errored_item_instead_of_defaulting_it():
    client, _ = _serving_batches([_succeeded("a"), _errored("b")])

    collection = client.collect(BATCH_ID, ["a", "b"])

    assert "b" in collection.failed
    assert "b" not in collection.completions


def test_it_should_report_an_item_missing_from_the_results_instead_of_defaulting_it():
    client, _ = _serving_batches([_succeeded("a")])

    collection = client.collect(BATCH_ID, ["a", "b"])

    assert "b" in collection.failed
    assert "b" not in collection.completions


def test_it_should_raise_when_the_batch_has_not_ended():
    client, _ = _serving_batches([], status="in_progress")

    with pytest.raises(BatchNotEnded):
        client.collect(BATCH_ID, ["a"])


# judge.jsonl


def test_it_should_record_the_run_criterion_critique_verdict_usage_cost_and_hash(
    tmp_path,
):
    log = tmp_path / "judge.jsonl"
    verdict = make_verdict("fail", "Too curt.")

    append_verdict(log, "run-1", "tone", verdict)

    [line] = log.read_text().splitlines()
    record = json.loads(line)
    assert record["run_id"] == "run-1"
    assert record["criterion"] == "tone"
    assert record["critique"] == "Too curt."
    assert record["verdict"] == "fail"
    assert record["usage"] == USAGE
    assert Decimal(str(record["cost_usd"])) == verdict.cost_usd
    assert record["judge_prompt_sha256"] == judge_prompt_sha256()


def test_it_should_stamp_each_line_with_the_hash_it_was_produced_with(
    tmp_path, monkeypatch
):
    log = tmp_path / "judge.jsonl"
    first_hash = judge_prompt_sha256()
    append_verdict(log, "run-1", "tone", make_verdict())

    monkeypatch.setattr(judge_module, "FEW_SHOT_EXAMPLES", [make_example()])
    append_verdict(log, "run-2", "tone", make_verdict())

    first, second = (json.loads(line) for line in log.read_text().splitlines())
    assert first["judge_prompt_sha256"] == first_hash
    assert second["judge_prompt_sha256"] == judge_prompt_sha256()
    assert first["judge_prompt_sha256"] != second["judge_prompt_sha256"]


def test_it_should_supersede_an_earlier_record_of_the_same_run_and_criterion(tmp_path):
    log = tmp_path / "judge.jsonl"
    append_verdict(log, "run-1", "tone", make_verdict("fail"))
    append_verdict(log, "run-1", "tone", make_verdict("pass"))

    current = current_verdicts(log)

    assert current[("run-1", "tone")]["verdict"] == "pass"
    assert len(current) == 1


def test_it_should_keep_the_superseded_record_in_the_file(tmp_path):
    log = tmp_path / "judge.jsonl"
    append_verdict(log, "run-1", "tone", make_verdict("fail"))

    append_verdict(log, "run-1", "tone", make_verdict("pass"))

    assert len(log.read_text().splitlines()) == 2


def test_it_should_keep_the_records_of_other_criteria_and_runs(tmp_path):
    log = tmp_path / "judge.jsonl"
    append_verdict(log, "run-1", "tone", make_verdict())
    append_verdict(log, "run-1", "escalation_judgment", make_verdict())
    append_verdict(log, "run-2", "tone", make_verdict())

    current = current_verdicts(log)

    assert set(current) == {
        ("run-1", "tone"),
        ("run-1", "escalation_judgment"),
        ("run-2", "tone"),
    }


def test_it_should_read_no_verdicts_from_a_file_that_does_not_exist(tmp_path):
    assert current_verdicts(tmp_path / "judge.jsonl") == {}


def test_it_should_write_whole_lines_when_threads_append_together(tmp_path):
    log = tmp_path / "judge.jsonl"
    threads = [
        threading.Thread(
            target=append_verdict, args=(log, f"run-{i}", "tone", make_verdict())
        )
        for i in range(16)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(current_verdicts(log)) == 16


# the inline scorer


def make_scorer(client, log: Path, budget: RoundBudget | None = None, entries=None):
    case = make_case(["tone"] if entries is None else entries)
    transcript = make_transcript()
    return judge_scorer(
        client,
        log,
        budget or RoundBudget(Decimal(5)),
        load=lambda state: Judged(str(state.sample_id), case, transcript),
    )


def make_state(sample_id: str = "run-1") -> TaskState:
    return TaskState(
        model="mockllm/model", sample_id=sample_id, epoch=1, input="q", messages=[]
    )


def score_once(scorer_fn, sample_id: str = "run-1") -> Score:
    return asyncio.run(scorer_fn(make_state(sample_id), Target("")))


def test_it_should_score_a_pass_as_correct_with_the_critique(tmp_path):
    client = FakeClient({"critique": "Clear and kind.", "verdict": "pass"})

    result = score_once(make_scorer(client, tmp_path / "judge.jsonl"))

    assert result.value == CORRECT
    assert result.explanation == "Clear and kind."


def test_it_should_score_a_fail_as_incorrect_with_the_critique(tmp_path):
    client = FakeClient({"critique": "Cold and curt.", "verdict": "fail"})

    result = score_once(make_scorer(client, tmp_path / "judge.jsonl"))

    assert result.value == INCORRECT
    assert result.explanation == "Cold and curt."


def test_it_should_leave_an_unable_verdict_unscored_with_the_criterion_as_reason(
    tmp_path,
):
    client = FakeClient({"critique": "Cannot tell.", "verdict": "unable"})

    result = score_once(make_scorer(client, tmp_path / "judge.jsonl"))

    assert isinstance(result.value, float) and math.isnan(result.value)
    assert "tone" in str(result.reason)


@pytest.mark.parametrize("verdict", ["pass", "fail", "unable"])
def test_it_should_append_every_verdict_to_judge_jsonl(tmp_path, verdict):
    log = tmp_path / "judge.jsonl"
    client = FakeClient({"critique": "Because.", "verdict": verdict})

    score_once(make_scorer(client, log))

    assert current_verdicts(log)[("run-1", "tone")]["verdict"] == verdict


def test_it_should_judge_each_criterion_of_the_case_and_append_each_verdict(tmp_path):
    log = tmp_path / "judge.jsonl"
    client = FakeClient()

    score_once(make_scorer(client, log, entries=["tone", "escalation_judgment"]))

    assert len(client.calls) == 2
    assert set(current_verdicts(log)) == {
        ("run-1", "tone"),
        ("run-1", "escalation_judgment"),
    }


def test_it_should_score_incorrect_when_any_criterion_fails(tmp_path):
    client = FakeClient()
    replies = iter(["pass", "fail"])
    client.complete = lambda system, user: (  # type: ignore[method-assign]
        json.dumps({"critique": "x", "verdict": next(replies)}),
        USAGE,
    )

    result = score_once(
        make_scorer(
            client, tmp_path / "judge.jsonl", entries=["tone", "escalation_judgment"]
        )
    )

    assert result.value == INCORRECT


def test_it_should_never_score_correct_when_a_criterion_is_unable(tmp_path):
    client = FakeClient()
    replies = iter(["pass", "unable"])
    client.complete = lambda system, user: (  # type: ignore[method-assign]
        json.dumps({"critique": "x", "verdict": next(replies)}),
        USAGE,
    )

    result = score_once(
        make_scorer(
            client, tmp_path / "judge.jsonl", entries=["tone", "escalation_judgment"]
        )
    )

    assert result.value != CORRECT


def test_it_should_raise_a_run_error_and_record_nothing_when_the_reply_is_unparsable(
    tmp_path,
):
    log = tmp_path / "judge.jsonl"
    client = FakeClient("not json")

    with pytest.raises(JudgeReplyError):
        score_once(make_scorer(client, log))

    assert current_verdicts(log) == {}


def test_it_should_refuse_the_call_when_the_budget_cannot_cover_one_judge_call(
    tmp_path,
):
    log = tmp_path / "judge.jsonl"
    client = FakeClient()
    budget = RoundBudget(JUDGE_CALL_RESERVE_USD / 2)

    with pytest.raises(BudgetExceeded):
        score_once(make_scorer(client, log, budget))

    assert client.calls == [] and current_verdicts(log) == {}


def test_it_should_free_the_reserve_with_the_real_cost_after_a_call(tmp_path):
    budget = RoundBudget(JUDGE_CALL_RESERVE_USD * Decimal("1.5"))
    scorer_fn = make_scorer(FakeClient(), tmp_path / "judge.jsonl", budget)
    score_once(scorer_fn, "run-1")

    result = score_once(scorer_fn, "run-2")

    assert result.value == CORRECT


def test_it_should_keep_the_reserve_charged_when_the_call_fails(tmp_path):
    budget = RoundBudget(JUDGE_CALL_RESERVE_USD * Decimal("1.5"))
    failing = make_scorer(
        FakeClient(error=RuntimeError("connection reset")),
        tmp_path / "judge.jsonl",
        budget,
    )
    with pytest.raises(RuntimeError):
        score_once(failing, "run-1")
    healthy = make_scorer(FakeClient(), tmp_path / "judge.jsonl", budget)

    with pytest.raises(BudgetExceeded):
        score_once(healthy, "run-2")


# inline use inside Inspect


@solver
def noop():
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        return state

    return solve


def run_scored(tmp_path: Path, scorer_fn, samples: int) -> EvalLog:
    task = Task(
        dataset=[Sample(id=f"s{i}", input="q", target="a") for i in range(samples)],
        solver=noop(),
        scorer=scorer_fn,
    )

    [log] = eval(
        task,
        model="mockllm/model",
        log_dir=str(tmp_path / "logs"),
        fail_on_error=False,
        retry_on_error=0,
        max_samples=8,
        display="none",
    )

    return read_eval_log(log.location)


def test_it_should_judge_the_samples_in_parallel_threads(tmp_path):
    spans: list[tuple[float, float]] = []

    class SpanClient(FakeClient):
        def complete(self, system, user):
            start = time.monotonic()
            try:
                return super().complete(system, user)
            finally:
                spans.append((start, time.monotonic()))

    scorer_fn = make_scorer(SpanClient(delay=SLEEP), tmp_path / "judge.jsonl")

    run_scored(tmp_path, scorer_fn, 8)

    assert len(spans) == 8
    assert max(end for _, end in spans) - min(start for start, _ in spans) < 3 * SLEEP


def test_it_should_let_at_most_three_calls_through_a_budget_that_fits_three(tmp_path):
    log = tmp_path / "judge.jsonl"
    client = FakeClient(delay=SLEEP)
    budget = RoundBudget(JUDGE_CALL_RESERVE_USD * 3)
    scorer_fn = make_scorer(client, log, budget)

    result = run_scored(tmp_path, scorer_fn, 8)

    judged = [s for s in result.samples if s.scores]
    assert len(client.calls) == 3
    assert len(judged) == 3
    assert len(current_verdicts(log)) == 3


# the smoke command


@pytest.fixture
def answer(monkeypatch):
    def type_answer(typed: str) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(typed))

    return type_answer


def run_smoke(capsys, client=None, batch=None, **kwargs) -> tuple[int, str]:
    code = main(
        ["smoke"],
        client=client or FakeClient(),
        batch_client=batch or FakeBatchClient(),
        timeout_seconds=kwargs.pop("timeout_seconds", 5),
        poll_seconds=kwargs.pop("poll_seconds", 0),
        **kwargs,
    )
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_it_should_refuse_with_one_line_and_no_call_without_the_api_key(
    monkeypatch, answer, capsys
):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    answer("yes\n")
    client, batch = FakeClient(), FakeBatchClient()

    code, output = run_smoke(capsys, client, batch)

    assert code != 0
    assert len(output.strip().splitlines()) == 1
    assert "ANTHROPIC_API_KEY" in output
    assert client.calls == [] and batch.submitted == []


def test_it_should_refuse_a_run_without_the_api_key_from_the_command_line(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    done = subprocess.run(
        [sys.executable, "-m", "pet_helper.evals.judge", "smoke"],
        input="yes\n",
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert done.returncode != 0
    assert len((done.stdout + done.stderr).strip().splitlines()) == 1
    assert "ANTHROPIC_API_KEY" in done.stdout + done.stderr


def test_it_should_print_the_cost_estimate_before_asking_for_confirmation(
    answer, capsys
):
    answer("no\n")

    _, output = run_smoke(capsys)

    assert "US$" in output


@pytest.mark.parametrize("typed", ["\n", "no\n", "n\n", "maybe\n", "yess\n", ""])
def test_it_should_abort_without_a_call_on_any_answer_but_an_explicit_yes(
    answer, capsys, typed
):
    answer(typed)
    client, batch = FakeClient(), FakeBatchClient()

    run_smoke(capsys, client, batch)

    assert client.calls == [] and batch.submitted == []


def test_it_should_make_one_sync_call_and_one_batch_submit_on_yes(answer, capsys):
    answer("yes\n")
    client, batch = FakeClient(), FakeBatchClient()

    code, _ = run_smoke(capsys, client, batch)

    assert code == 0
    assert len(client.calls) == 1
    assert len(batch.submitted) == 1


def test_it_should_collect_the_batch_after_submitting_it(answer, capsys):
    answer("yes\n")
    batch = FakeBatchClient()

    run_smoke(capsys, batch=batch)

    assert batch.collect_calls >= 1


def test_it_should_poll_the_batch_until_it_ends(answer, capsys):
    answer("yes\n")
    batch = FakeBatchClient(ended_after_polls=2)

    code, _ = run_smoke(capsys, batch=batch)

    assert code == 0
    assert batch.collect_calls == 3


def test_it_should_exit_non_zero_naming_the_batch_when_it_does_not_end_in_time(
    answer, capsys
):
    answer("yes\n")
    batch = FakeBatchClient(never_ends=True)

    code, output = run_smoke(
        capsys, batch=batch, timeout_seconds=0.2, poll_seconds=0.01
    )

    assert code != 0
    assert "batch" in output.lower()


def test_it_should_print_the_verdict_the_usage_and_the_cost(answer, capsys):
    answer("yes\n")

    _, output = run_smoke(capsys)

    assert "pass" in output
    assert "1200" in output and "150" in output
    assert "cost" in output.lower()


def test_it_should_not_print_the_prompt_or_the_api_key(answer, capsys):
    answer("yes\n")
    client = FakeClient()

    _, output = run_smoke(capsys, client)

    system, user = client.calls[0]
    assert system not in output and user not in output
    assert API_KEY not in output


def test_it_should_stop_before_any_call_when_the_cap_cannot_cover_one(answer, capsys):
    answer("yes\n")
    client, batch = FakeClient(), FakeBatchClient()

    code, _ = run_smoke(capsys, client, batch, cap_usd=JUDGE_CALL_RESERVE_USD / 2)

    assert code != 0
    assert client.calls == [] and batch.submitted == []


def test_it_should_exit_non_zero_naming_the_parameter_when_the_provider_rejects_the_request(
    answer, capsys
):
    answer("yes\n")
    rejection = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "output_config.effort: value not supported by this model",
        },
    }
    sdk = _sdk(lambda request: httpx2.Response(400, json=rejection))

    code, output = run_smoke(capsys, AnthropicJudgeClient(sdk))

    assert code != 0
    assert "output_config.effort" in output
