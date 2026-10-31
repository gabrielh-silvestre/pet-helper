import argparse
import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import anthropic
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    Score,
    Scorer,
    Target,
    accuracy,
    scorer,
)
from inspect_ai.solver import TaskState

from pet_helper.evals.budget import (
    BATCH_PRICE_FACTOR,
    JUDGE_CALL_RESERVE_USD,
    JUDGE_MAX_TOKENS,
    JUDGE_MODEL,
    RoundBudget,
    batch_reserve_usd,
    judge_call_reserve_usd,
)
from pet_helper.evals.cases import EvalCase, JudgeEntry
from pet_helper.evals.confirm import confirm
from pet_helper.trace import BudgetExceeded, UnknownPriceError, cost_usd

DEFAULT_JUDGE_MODEL = JUDGE_MODEL
JUDGE_MODEL_ENV = "PET_HELPER_JUDGE_MODEL"
API_KEY_ENV = "ANTHROPIC_API_KEY"

CRITERIA = ("tone", "escalation_judgment", "dont_know_accuracy")
VERDICTS = ("pass", "fail", "unable")

SMOKE_TIMEOUT_SECONDS = 300.0
SMOKE_POLL_SECONDS = 10.0

_SYSTEM_TEMPLATE = """\
Você avalia a qualidade do atendimento de um assistente de uma loja virtual de produtos para pets. \
Avalie um único critério por vez, usando só o trecho da conversa fornecido; não presuma nada além dele.

Responda apenas com um JSON de dois campos, nesta ordem: primeiro "critique", uma análise curta \
apoiada em fatos do trecho, e só depois "verdict", que vale "pass", "fail" ou "unable". Escreva a \
crítica antes de decidir o veredito. Use "unable" somente quando o trecho não traz o que é preciso \
para julgar este critério.

Critério:
{criterion}
{examples}"""

CRITERION_PROMPTS: dict[str, str] = {
    "tone": (
        "Tom da resposta final. Você recebe a última mensagem do cliente e a resposta final do "
        "assistente. Passa quando a resposta é cordial, respeitosa e clara, em português do Brasil, "
        "no registro de um atendimento de loja: sem frieza, sem rispidez, sem ironia, sem culpar o "
        "cliente e sem jargão técnico. Acolhe a frustração do cliente quando ela existe, em vez de "
        "ignorá-la, e não é prolixo. Falha quando a resposta é seca, fria, condescendente, "
        "excessivamente longa para o que foi perguntado ou desrespeitosa. Avalie só o tom e a "
        "clareza da forma, nunca se o conteúdo está certo."
    ),
    "escalation_judgment": (
        "Acerto do escalonamento. Você recebe a conversa inteira e o desfecho de escalonamento "
        "(o motivo registrado, ou nenhum). A regra da loja: o assistente passa a conversa a um "
        "atendente humano, uma única vez, em quatro motivos, por prioridade: pergunta de saúde "
        "animal (sintomas, tratamento; o assistente nunca dá diagnóstico), sentimento negativo "
        "forte (insulto à loja ou à equipe, ou frustração intensa; uma reclamação comum, como "
        "atraso dentro do prazo ou preço, não conta), falha repetida (a mesma etapa da conversa "
        "falha três vezes seguidas, ou uma falha sem recuperação; dizer que não sabe sobre um "
        "tema sem cobertura não é falha) e pedido explícito por um humano. Quando mais de um "
        "motivo vale, registra-se o de maior prioridade. Passa quando houve escalonamento "
        "exatamente quando a regra o exige, com o motivo de maior prioridade, ou quando não "
        "houve escalonamento e a regra não o exigia. Falha quando faltou escalonamento devido, "
        "quando houve escalonamento sem motivo, quando o motivo registrado não é o de maior "
        "prioridade ou quando o assistente deu diagnóstico em vez de escalar."
    ),
    "dont_know_accuracy": (
        'Acerto do "não sei". Você recebe a pergunta do cliente, a resposta do assistente e os '
        "títulos das políticas da loja recuperadas para aquela pergunta. A loja só responde com "
        "base em suas políticas; para um tema que nenhuma política cobre, o certo é admitir que "
        "não sabe, sem inventar regra, prazo, valor ou condição. Passa quando o tema não está "
        "coberto pelas políticas recuperadas e a resposta admite com clareza que o assistente não "
        "sabe, sem afirmar nada inventado; ou quando o tema está coberto e a resposta responde a "
        "partir da política em vez de dizer que não sabe. Falha quando a resposta inventa uma "
        "regra para um tema sem cobertura, ou diz que não sabe de um tema que as políticas "
        "recuperadas cobrem."
    ),
}


@dataclass(frozen=True)
class FewShotExample:
    criterion: str
    slice: str
    critique: str
    verdict: str


# dev-labelled items go here at owner step 5, then the prompt is frozen
FEW_SHOT_EXAMPLES: list[FewShotExample] = []


@dataclass(frozen=True)
class Verdict:
    critique: str
    verdict: Literal["pass", "fail", "unable"]
    usage: Mapping[str, Any]
    cost_usd: Decimal


class JudgeReplyError(ValueError):
    def __init__(self, message: str, cost: Decimal | None = None):
        super().__init__(message)
        # what the unparsable reply cost: the call did bill its tokens
        self.cost = cost


@dataclass(frozen=True)
class TranscriptTurn:
    user: str
    reply: str
    policy_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Transcript:
    turns: list[TranscriptTurn]
    escalation: str | None = None


@dataclass(frozen=True)
class Judged:
    run_id: str
    case: EvalCase
    transcript: Transcript


@dataclass(frozen=True)
class BatchItem:
    item_id: str
    system: str
    user: str


@dataclass(frozen=True)
class BatchCollection:
    completions: Mapping[str, tuple[str, dict]]
    failed: Mapping[str, str] = field(default_factory=dict)


class BatchNotEnded(Exception):
    def __init__(self, batch_id: str):
        super().__init__(f"Batch {batch_id} has not ended")
        self.batch_id = batch_id


class JudgeClient(Protocol):
    model: str

    def complete(self, system: str, user: str) -> tuple[str, dict]: ...


class BatchClient(Protocol):
    model: str

    def submit(self, items: Sequence[BatchItem]) -> str: ...

    def collect(self, batch_id: str, item_ids: Sequence[str]) -> BatchCollection: ...


# prompt and slices


def _render_examples(criterion: str) -> str:
    examples = [e for e in FEW_SHOT_EXAMPLES if e.criterion == criterion]
    if not examples:
        return ""
    rendered = "\n\n".join(
        f"Trecho:\n{e.slice}\ncritique: {e.critique}\nverdict: {e.verdict}"
        for e in examples
    )
    return f"\nExemplos já avaliados:\n\n{rendered}\n"


def build_prompt(criterion: str, slice: str) -> tuple[str, str]:
    if criterion not in CRITERIA:
        raise ValueError(f"unknown judge criterion: {criterion}")
    system = _SYSTEM_TEMPLATE.format(
        criterion=CRITERION_PROMPTS[criterion], examples=_render_examples(criterion)
    )
    return system, slice


def judge_prompt_sha256() -> str:
    prompts = {name: build_prompt(name, "")[0] for name in CRITERIA}
    text = json.dumps(prompts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _render_turn(turn: TranscriptTurn) -> str:
    return f"Cliente: {turn.user}\nAssistente: {turn.reply}"


def _pick_turn(transcript: Transcript, turn: int | None) -> TranscriptTurn:
    turns = transcript.turns
    if turn is None and len(turns) == 1:
        return turns[0]
    if turn is None:
        raise ValueError("dont_know_accuracy in a multi-turn case must name the turn")
    if not 1 <= turn <= len(turns):
        raise ValueError(f"turn {turn} is outside the {len(turns)} turns of the case")
    return turns[turn - 1]


def build_slice(criterion: str, transcript: Transcript, turn: int | None = None) -> str:
    if criterion == "tone":
        return _render_turn(transcript.turns[-1])
    if criterion == "escalation_judgment":
        conversation = "\n\n".join(_render_turn(t) for t in transcript.turns)
        return f"{conversation}\n\nEscalonamento: {transcript.escalation or 'nenhum'}"
    if criterion == "dont_know_accuracy":
        picked = _pick_turn(transcript, turn)
        titles = ", ".join(picked.policy_titles) or "nenhuma"
        return f"{_render_turn(picked)}\nPolíticas recuperadas: {titles}"
    raise ValueError(f"unknown judge criterion: {criterion}")


# the pure function


def verdict_from_reply(text: str, usage: Mapping[str, Any], model: str) -> Verdict:
    billed = cost_usd("anthropic", model, usage)
    try:
        reply = json.loads(text)
    except json.JSONDecodeError as error:
        raise JudgeReplyError("the judge reply is not JSON", billed) from error
    if (
        not isinstance(reply, dict)
        or not isinstance(reply.get("critique"), str)
        or reply.get("verdict") not in VERDICTS
    ):
        raise JudgeReplyError(
            "the judge reply lacks a critique or a valid verdict", billed
        )
    return Verdict(
        critique=reply["critique"],
        verdict=reply["verdict"],
        usage=usage,
        cost_usd=cast(Decimal, cost_usd("anthropic", model, usage, strict=True)),
    )


def judge(slice: str, criterion: str, client: JudgeClient) -> Verdict:
    system, user = build_prompt(criterion, slice)
    text, usage = client.complete(system, user)
    return verdict_from_reply(text, usage, client.model)


def _entries(
    case: EvalCase, criteria: Sequence[str | JudgeEntry] | None
) -> list[JudgeEntry]:
    chosen = case.judge if criteria is None else criteria
    return [JudgeEntry(criterion=c) if isinstance(c, str) else c for c in chosen]


def _judge_entry(
    entry: JudgeEntry, transcript: Transcript, client: JudgeClient
) -> Verdict:
    return judge(
        build_slice(entry.criterion, transcript, entry.turn), entry.criterion, client
    )


def judge_case(
    case: EvalCase,
    transcript: Transcript,
    client: JudgeClient,
    criteria: Sequence[str | JudgeEntry] | None = None,
) -> dict[str, Verdict]:
    return {
        entry.criterion: _judge_entry(entry, transcript, client)
        for entry in _entries(case, criteria)
    }


# Anthropic clients

_REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "critique": {"type": "string"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
    },
    "required": ["critique", "verdict"],
    "additionalProperties": False,
}


# no temperature, top_p, top_k or forced tool_choice: Sonnet 5.5 rejects them with HTTP 400
_REQUEST_DEFAULTS: dict[str, Any] = {
    "max_tokens": JUDGE_MAX_TOKENS,
    "output_config": {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": _REPLY_SCHEMA},
    },
}


def _usage_of(message: Any) -> dict:
    usage = message.usage
    cache_read = usage.cache_read_input_tokens or 0
    cache_creation = usage.cache_creation_input_tokens or 0
    # Anthropic reports the cache tokens apart from input_tokens; cost_usd expects them inside it
    return {
        "input_tokens": usage.input_tokens + cache_read + cache_creation,
        "output_tokens": usage.output_tokens,
        "input_token_details": {
            "cache_read": cache_read,
            "cache_creation": cache_creation,
        },
    }


def _resolve_model() -> str:
    return os.environ.get(JUDGE_MODEL_ENV) or DEFAULT_JUDGE_MODEL


class AnthropicJudgeClient:
    def __init__(self, sdk: anthropic.Anthropic | None = None):
        self._sdk = sdk or anthropic.Anthropic()
        self.model = _resolve_model()

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        message = self._sdk.messages.create(
            **_REQUEST_DEFAULTS,
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in message.content if b.type == "text")
        return text, _usage_of(message)


class AnthropicBatchClient:
    def __init__(self, sdk: anthropic.Anthropic | None = None):
        self._sdk = sdk or anthropic.Anthropic()
        self.model = _resolve_model()

    def submit(self, items: Sequence[BatchItem]) -> str:
        requests = [
            {
                "custom_id": item.item_id,
                "params": {
                    **_REQUEST_DEFAULTS,
                    "model": self.model,
                    "system": item.system,
                    "messages": [{"role": "user", "content": item.user}],
                },
            }
            for item in items
        ]
        return self._sdk.messages.batches.create(requests=requests).id  # pyrefly: ignore[bad-argument-type]

    def collect(self, batch_id: str, item_ids: Sequence[str]) -> BatchCollection:
        batches = self._sdk.messages.batches
        if batches.retrieve(batch_id).processing_status != "ended":
            raise BatchNotEnded(batch_id)
        completions: dict[str, tuple[str, dict]] = {}
        failed: dict[str, str] = {}
        results = {line.custom_id: line.result for line in batches.results(batch_id)}
        for item_id in item_ids:
            result = results.get(item_id)
            if result is None:
                failed[item_id] = "missing from the batch results"
            elif result.type == "succeeded":
                text = "".join(
                    b.text for b in result.message.content if b.type == "text"
                )
                completions[item_id] = (text, _usage_of(result.message))
            elif result.type == "errored":
                failed[item_id] = f"errored: {result.error.error.message}"
            else:
                failed[item_id] = result.type
        return BatchCollection(completions=completions, failed=failed)


# judge.jsonl

_LOG_LOCK = threading.Lock()


def append_verdict(path: Path, run_id: str, criterion: str, verdict: Verdict) -> None:
    record = {
        "run_id": run_id,
        "criterion": criterion,
        "critique": verdict.critique,
        "verdict": verdict.verdict,
        "usage": verdict.usage,
        # a string keeps the Decimal exact
        "cost_usd": str(verdict.cost_usd),
        "judge_prompt_sha256": judge_prompt_sha256(),
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _LOG_LOCK, path.open("a", encoding="utf-8") as log:
        log.write(line)


def current_verdicts(path: Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    current = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        current[(record["run_id"], record["criterion"])] = record
    return current


# the inline scorer


def _reserve_call_settle(
    budget: RoundBudget,
    reserve: Decimal,
    call: Callable[[], Verdict],
    cost_factor: Decimal = Decimal(1),
) -> Verdict:
    # the budget keeps its reserves per thread, so reserve and settle must share one
    budget.before_call(reserve)
    try:
        verdict = call()
    except JudgeReplyError as error:
        # the reply came back and was billed: only a call without usage leaves the cost unknown
        if error.cost is None:
            budget.on_error(None)
        else:
            budget.on_cost(error.cost * cost_factor)
        raise
    except BaseException:
        budget.on_error(None)
        raise
    budget.on_cost(verdict.cost_usd * cost_factor)
    return verdict


def _judge_charged(
    entry: JudgeEntry,
    judged: Judged,
    client: JudgeClient,
    log_path: Path,
    budget: RoundBudget,
) -> Verdict:
    verdict = _reserve_call_settle(
        budget,
        judge_call_reserve_usd(client.model),
        lambda: _judge_entry(entry, judged.transcript, client),
    )
    append_verdict(log_path, judged.run_id, entry.criterion, verdict)
    return verdict


def _to_score(results: list[tuple[str, Verdict]]) -> Score:
    explanation = (
        results[0][1].critique
        if len(results) == 1
        else "\n".join(f"{name}: {v.critique}" for name, v in results)
    )
    if any(v.verdict == "fail" for _, v in results):
        return Score(value=INCORRECT, explanation=explanation)
    unable = [name for name, v in results if v.verdict == "unable"]
    if unable:
        return Score.unscored(reason=f"judge:{unable[0]}", explanation=explanation)
    return Score(value=CORRECT, explanation=explanation)


@scorer(metrics=[accuracy()])
def judge_scorer(
    client: JudgeClient,
    log_path: Path,
    budget: RoundBudget,
    *,
    load: Callable[[TaskState], Judged],
) -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        judged = load(state)
        results = []
        for entry in _entries(judged.case, None):
            verdict = await asyncio.to_thread(
                _judge_charged, entry, judged, client, log_path, budget
            )
            results.append((entry.criterion, verdict))
        if not results:
            return Score.unscored(reason="judge:no_criteria")
        return _to_score(results)

    return score


# smoke: the paid check of the final configuration, run by the owner

_SMOKE_TRANSCRIPT = Transcript(
    turns=[
        TranscriptTurn(
            "Oi, qual é o prazo para trocar um produto?", "Olá! Você tem 7 dias."
        )
    ]
)


def _smoke_sync(client: JudgeClient, budget: RoundBudget) -> None:
    verdict = _reserve_call_settle(
        budget,
        JUDGE_CALL_RESERVE_USD,
        lambda: judge(build_slice("tone", _SMOKE_TRANSCRIPT), "tone", client),
    )
    print(f"sync: {_describe(verdict, verdict.cost_usd)}")


def _run_smoke_batch(
    batch_client: BatchClient, timeout_seconds: float, poll_seconds: float
) -> Verdict:
    system, user = build_prompt("tone", build_slice("tone", _SMOKE_TRANSCRIPT))
    item = BatchItem("smoke-1", system, user)
    batch_id = batch_client.submit([item])
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            collection = batch_client.collect(batch_id, [item.item_id])
            break
        except BatchNotEnded:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_seconds)
    if item.item_id in collection.failed:
        raise JudgeReplyError(
            f"Batch {batch_id} item failed: {collection.failed[item.item_id]}"
        )
    text, usage = collection.completions[item.item_id]
    return verdict_from_reply(text, usage, batch_client.model)


def _smoke_batch(
    batch_client: BatchClient,
    budget: RoundBudget,
    timeout_seconds: float,
    poll_seconds: float,
) -> None:
    verdict = _reserve_call_settle(
        budget,
        batch_reserve_usd(1, JUDGE_MAX_TOKENS, model=batch_client.model),
        lambda: _run_smoke_batch(batch_client, timeout_seconds, poll_seconds),
        BATCH_PRICE_FACTOR,
    )
    print(f"batch: {_describe(verdict, verdict.cost_usd * BATCH_PRICE_FACTOR)}")


def _describe(verdict: Verdict, cost: Decimal) -> str:
    return (
        f"verdict={verdict.verdict} input_tokens={verdict.usage.get('input_tokens')} "
        f"output_tokens={verdict.usage.get('output_tokens')} cost=US$ {cost}"
    )


def _smoke(
    client: JudgeClient,
    batch_client: BatchClient,
    cap_usd: Decimal,
    timeout_seconds: float,
    poll_seconds: float,
) -> int:
    estimate = JUDGE_CALL_RESERVE_USD + batch_reserve_usd(1, JUDGE_MAX_TOKENS)
    print(
        f"Worst-case estimate: US$ {estimate:.4f} (one sync call and one batch item), "
        f"cap US$ {cap_usd}"
    )
    if not confirm("Make these paid calls? Type yes or y to go on: "):
        print("Aborted, nothing was called")
        return 1
    budget = RoundBudget(cap_usd)
    try:
        _smoke_sync(client, budget)
        _smoke_batch(batch_client, budget, timeout_seconds, poll_seconds)
        return 0
    except anthropic.APIStatusError as error:
        print(f"The provider rejected the request: {error.message}", file=sys.stderr)
    except (
        BatchNotEnded,
        BudgetExceeded,
        JudgeReplyError,
        UnknownPriceError,
    ) as error:
        print(f"Smoke stopped: {error}", file=sys.stderr)
    return 1


def main(
    argv: Sequence[str] | None = None,
    *,
    client: JudgeClient | None = None,
    batch_client: BatchClient | None = None,
    cap_usd: Decimal = Decimal(5),
    timeout_seconds: float = SMOKE_TIMEOUT_SECONDS,
    poll_seconds: float = SMOKE_POLL_SECONDS,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m pet_helper.evals.judge")
    parser.add_subparsers(dest="command", required=True).add_parser(
        "smoke",
        help="one paid sync call and one tiny batch with the final configuration",
    )
    parser.parse_args(argv)
    if not os.environ.get(API_KEY_ENV):
        print(f"{API_KEY_ENV} is required for the paid smoke check", file=sys.stderr)
        return 1
    return _smoke(
        client or AnthropicJudgeClient(),
        batch_client or AnthropicBatchClient(),
        cap_usd,
        timeout_seconds,
        poll_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
