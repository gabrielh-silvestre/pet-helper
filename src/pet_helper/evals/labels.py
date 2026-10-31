import argparse
import hashlib
import json
import os
import random
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import partial
from operator import itemgetter
from pathlib import Path
from typing import Any

import anthropic

from pet_helper.evals import judge as judge_module
from pet_helper.evals.budget import (
    BATCH_PRICE_FACTOR,
    JUDGE_CALL_RESERVE_USD,
    JUDGE_MAX_TOKENS,
    BudgetExceeded,
    RoundBudget,
    batch_reserve_usd,
)
from pet_helper.evals.confirm import confirm
from pet_helper.evals.judge import (
    API_KEY_ENV,
    CRITERIA,
    AnthropicBatchClient,
    AnthropicJudgeClient,
    BatchClient,
    BatchItem,
    JudgeClient,
    JudgeReplyError,
    Transcript,
    build_prompt,
    build_slice,
    judge,
    judge_prompt_sha256,
    verdict_from_reply,
    wait_for_batch,
)
from pet_helper.evals.judge import _reserve_call_settle as reserve_call_settle
from pet_helper.evals.round import JUDGE_CALL_ESTIMATE_USD
from pet_helper.evals.round import _read_runs as read_runs
from pet_helper.evals.stats import wilson_interval
from pet_helper.trace import UnknownPriceError, read_jsonl

_HELD_OUT = "held_out"
# natural and seeded items each, per criterion: about 50 items in all
_GROUP_CAP = 25
_DEV_SHARE = 0.4
_BATCH_TIMEOUT_SECONDS = 3600.0
_BATCH_POLL_SECONDS = 10.0
_STOPS = (
    BudgetExceeded,
    UnknownPriceError,
    anthropic.APIStatusError,
    anthropic.APIConnectionError,
)

_TONE_SEEDS = (
    ("dismissive", "Isso não é comigo, procure outro canal."),
    ("dismissive", "Já respondi isso, não tenho mais o que dizer."),
    ("dismissive", "Não sei e não me interessa, vire-se."),
    ("rude", "Pare de reclamar, o problema é seu."),
    ("rude", "Você não leu as regras? Não me incomode com isso."),
    ("rude", "Que pergunta boba, leia o site antes de perguntar."),
)
_INVENTED_ANSWERS = (
    "Pode sim, a loja garante essa condição por 90 dias, sem nenhum custo.",
    "Claro, esse benefício vale para qualquer pedido e é aprovado na hora.",
    "Sim, a loja cobre isso integralmente, é só pedir pelo chat.",
)
_FALSE_DONT_KNOW = (
    "Não sei responder isso, desculpe.",
    "Não sei informar, não tenho essa informação.",
)
_NO_HANDOFF_REPLY = "Entendi, posso resolver isso por aqui mesmo."
_HANDOFF_REPLY = "Vou encaminhar seu atendimento para um atendente humano."


class _Refused(Exception):
    pass


@dataclass(frozen=True)
class _Source:
    criterion: str
    case_id: str
    transcript: Transcript
    turn: int | None


@dataclass(frozen=True)
class _Row:
    criterion: str
    group: str
    case_id: str
    human: str
    verdict: str


# files


def _labels(directory: Path) -> Path:
    return directory / "labels"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path) if path.is_file() else []


def _dump_jsonl(rows: Sequence[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def _append_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(_dump_jsonl(rows))


def _read_pool(labels: Path) -> list[dict[str, Any]]:
    pool = _read_jsonl(labels / "pool.jsonl")
    if not pool:
        raise _Refused("there is no pool, run pool first")
    return pool


def _read_key(labels: Path) -> dict[str, dict[str, Any]]:
    return json.loads((labels / "key.json").read_text(encoding="utf-8"))


def _write_key(labels: Path, key: dict[str, dict[str, Any]]) -> None:
    # replaced whole, so an interrupted write never leaves a half-written key
    temp = labels / "key.json.tmp"
    temp.write_text(
        json.dumps(key, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temp, labels / "key.json")


def _floor(labels: Path, *, required: bool) -> dict[str, dict[str, float]]:
    path = labels / "floor.json"
    if not path.is_file():
        if required:
            raise _Refused(
                "write labels/floor.json ({criterion: {tpr, tnr}}) before the test split"
            )
        return {}
    floor = json.loads(path.read_text(encoding="utf-8"))
    missing = [c for c in CRITERIA if not {"tpr", "tnr"} <= set(floor.get(c, {}))]
    if required and missing:
        raise _Refused(f"labels/floor.json lacks the floor of {', '.join(missing)}")
    return floor


def _open_round(directory: Path) -> None:
    if _HELD_OUT in directory.resolve().parts:
        raise _Refused(f"{directory}: a held-out round is never labelled")
    info = directory / "round.json"
    if not info.is_file():
        raise _Refused(f"{directory}: not a round directory")
    if json.loads(info.read_text(encoding="utf-8"))["judge_pending"]:
        raise _Refused(f"{directory}: the judge is pending, collect it first")


# seed


def _sources(directory: Path) -> list[_Source]:
    seen: set[tuple[str, str, int | None, str]] = set()
    sources = []
    for run in read_runs(directory):
        if run.transcript is None:
            continue
        for entry in run.entries:
            # identical final answers within a case are one item
            marker = (
                run.case_id,
                entry.criterion,
                entry.turn,
                run.transcript.turns[-1].reply,
            )
            if marker not in seen:
                seen.add(marker)
                sources.append(
                    _Source(entry.criterion, run.case_id, run.transcript, entry.turn)
                )
    return sources


def _with_reply(transcript: Transcript, index: int, reply: str) -> Transcript:
    turns = list(transcript.turns)
    turns[index] = replace(turns[index], reply=reply)
    return replace(transcript, turns=turns)


def _seeded(source: _Source, rng: random.Random) -> tuple[str, Transcript]:
    transcript = source.transcript
    last = len(transcript.turns) - 1
    if source.criterion == "tone":
        kind, reply = rng.choice(_TONE_SEEDS)
        return kind, _with_reply(transcript, last, reply)
    if source.criterion == "escalation_judgment":
        if transcript.escalation:
            removed = _with_reply(transcript, last, _NO_HANDOFF_REPLY)
            return "handoff_removed", replace(removed, escalation=None)
        added = _with_reply(transcript, last, _HANDOFF_REPLY)
        return "handoff_added", replace(added, escalation="explicit_request")
    turn = (source.turn or 1) - 1
    if transcript.turns[turn].policy_titles:
        return "false_dont_know", _with_reply(
            transcript, turn, rng.choice(_FALSE_DONT_KNOW)
        )
    return "invented_answer", _with_reply(
        transcript, turn, rng.choice(_INVENTED_ANSWERS)
    )


def _seed(directory: Path, seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311  (templates, not secrets: a fixed seed must reproduce them)
    rows = []
    for source in _sources(directory):
        kind, transcript = _seeded(source, rng)
        rows.append(
            {
                "criterion": source.criterion,
                "case_id": source.case_id,
                "slice": build_slice(source.criterion, transcript, source.turn),
                "seeded": True,
                "seed_kind": kind,
            }
        )
    labels = _labels(directory)
    labels.mkdir(exist_ok=True)
    (labels / "seeds.jsonl").write_text(_dump_jsonl(rows), encoding="utf-8")
    print(f"Wrote {len(rows)} seeded items to {labels / 'seeds.jsonl'}")


# pool


def _item_id(item: dict[str, Any]) -> str:
    text = "|".join(item[k] for k in ("criterion", "group", "case_id", "slice"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _capped(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    kept, counts = [], Counter()
    for item in sorted(items, key=itemgetter("id")):
        slot = (item["criterion"], item["group"])
        if item["group"] == "owner_written" or counts[slot] < _GROUP_CAP:
            counts[slot] += 1
            kept.append(item)
    return kept


def _splits(items: Sequence[dict[str, Any]]) -> dict[str, str]:
    # a whole case goes to one side, taken in hash order until dev holds its share
    sizes = Counter(item["case_id"] for item in items)
    quota = _DEV_SHARE * len(items)
    splits, dev = {}, 0
    for case_id in sorted(sizes, key=lambda c: hashlib.sha256(c.encode()).hexdigest()):
        splits[case_id] = "dev" if dev < quota else "test"
        if splits[case_id] == "dev":
            dev += sizes[case_id]
    return splits


def _pool(directory: Path) -> None:
    labels = _labels(directory)
    if (labels / "pool.jsonl").exists():
        raise _Refused("labels/pool.jsonl exists, delete it and key.json to rebuild")
    if not (labels / "seeds.jsonl").is_file():
        raise _Refused("there are no seeds, run seed first")
    items: list[dict[str, Any]] = [
        {
            "criterion": s.criterion,
            "case_id": s.case_id,
            "slice": build_slice(s.criterion, s.transcript, s.turn),
            "group": "natural",
            "seeded": False,
        }
        for s in _sources(directory)
    ]
    items += [
        {
            "criterion": row["criterion"],
            "case_id": row["case_id"],
            "slice": row["slice"],
            "group": "seeded",
            "seeded": True,
            "seed_kind": row["seed_kind"],
        }
        for row in _read_jsonl(labels / "seeds.jsonl")
    ]
    items += [
        {
            "criterion": row["criterion"],
            "case_id": f"owner-{number}",
            "slice": row["slice"],
            "group": "owner_written",
            "seeded": False,
        }
        for number, row in enumerate(_read_jsonl(labels / "owner.jsonl"))
    ]
    for item in items:
        item["id"] = _item_id(item)
    kept = _capped(list({item["id"]: item for item in items}.values()))
    splits = _splits(kept)
    fields = ("criterion", "seeded", "group", "case_id", "seed_kind")
    key = {
        item["id"]: {
            "split": splits[item["case_id"]],
            **{k: item[k] for k in fields if k in item},
        }
        for item in kept
    }
    pool = [{k: item[k] for k in ("id", "criterion", "slice")} for item in kept]
    (labels / "pool.jsonl").write_text(_dump_jsonl(pool), encoding="utf-8")
    _write_key(labels, key)
    counts = Counter(item["criterion"] for item in kept)
    print(f"Pooled {len(kept)} items into {labels / 'pool.jsonl'}")
    print("\n".join(f"{name}: {counts[name]} items" for name in CRITERIA))


# judge-pool


def _test_items_to_judge(
    labels: Path,
    key: dict[str, dict[str, Any]],
    items: Sequence[dict[str, Any]],
    force: bool,
) -> list[dict[str, Any]]:
    current = judge_prompt_sha256()
    runs = [r for r in _read_jsonl(labels / "judge-runs.jsonl") if r["split"] == "test"]
    pending = [i for i in items if key[i["id"]].get("judge_prompt_sha256") != current]
    if any(r["judge_prompt_sha256"] == current for r in runs):
        if not pending:
            raise _Refused("the test split was already read with this judge prompt")
    elif runs and not force:
        raise _Refused(
            "the test split was read with another judge prompt, "
            "--force-new-validation records a new validation"
        )
    return pending


def _judge_sync(
    items: Sequence[dict[str, Any]],
    client: JudgeClient,
    budget: RoundBudget,
    store: Callable[[str, str], None],
) -> tuple[dict[str, str], Decimal, str | None]:
    verdicts: dict[str, str] = {}
    cost = Decimal(0)
    for item in items:
        call = partial(judge, item["slice"], item["criterion"], client)
        try:
            verdict = reserve_call_settle(budget, JUDGE_CALL_RESERVE_USD, call)
        except JudgeReplyError as error:
            cost += error.cost or Decimal(0)
            continue
        except _STOPS as error:
            return verdicts, cost, str(error)
        store(item["id"], verdict.verdict)
        verdicts[item["id"]] = verdict.verdict
        cost += verdict.cost_usd
    return verdicts, cost, None


def _judge_batch(
    items: Sequence[dict[str, Any]],
    client: BatchClient,
    budget: RoundBudget,
    store: Callable[[str, str], None],
    poll_seconds: float,
    timeout_seconds: float,
) -> tuple[dict[str, str], Decimal, str | None]:
    ids = [item["id"] for item in items]
    try:
        budget.before_call(
            batch_reserve_usd(len(items), JUDGE_MAX_TOKENS, model=client.model)
        )
        batch_id = client.submit(
            [
                BatchItem(item["id"], *build_prompt(item["criterion"], item["slice"]))
                for item in items
            ]
        )
        collection = wait_for_batch(
            client, batch_id, ids, timeout_seconds, poll_seconds
        )
        if collection is None:
            budget.on_error(None)
            return {}, Decimal(0), f"batch {batch_id} did not end in time"
    except _STOPS as error:
        return {}, Decimal(0), str(error)
    verdicts: dict[str, str] = {}
    cost = Decimal(0)
    stop = None
    for item_id, (text, usage) in collection.completions.items():
        try:
            verdict = verdict_from_reply(text, usage, client.model)
        except JudgeReplyError:
            continue
        except _STOPS as error:
            stop = str(error)
            break
        store(item_id, verdict.verdict)
        verdicts[item_id] = verdict.verdict
        cost += verdict.cost_usd * BATCH_PRICE_FACTOR
    budget.on_cost(cost)
    return verdicts, cost, stop


def _judge_pool(
    directory: Path,
    split: str,
    mode: str,
    force: bool,
    *,
    client: JudgeClient | None,
    batch_client: BatchClient | None,
    cap_usd: Decimal,
    poll_seconds: float,
    timeout_seconds: float,
) -> int:
    labels = _labels(directory)
    key = _read_key(labels)
    items = [i for i in _read_pool(labels) if key[i["id"]]["split"] == split]
    if not items:
        raise _Refused(f"the pool has no item in the {split} split")
    if split == "test":
        _floor(labels, required=True)
        items = _test_items_to_judge(labels, key, items, force)
    if (client if mode == "sync" else batch_client) is None and not os.environ.get(
        API_KEY_ENV
    ):
        raise _Refused(f"{API_KEY_ENV} is required for the paid judge calls")

    count = len(items)
    if mode == "sync":
        typical, worst = JUDGE_CALL_ESTIMATE_USD * count, JUDGE_CALL_RESERVE_USD * count
    else:
        typical = JUDGE_CALL_ESTIMATE_USD * count * BATCH_PRICE_FACTOR
        worst = batch_reserve_usd(count, JUDGE_MAX_TOKENS)
    print(
        f"Estimate for {count} items ({mode}): US$ {typical:.4f} typical, "
        f"US$ {worst:.4f} worst case, cap US$ {cap_usd}"
    )
    if not confirm("Make these paid calls? Type yes to go on: "):
        print("Aborted, nothing was called")
        return 1

    def store(item_id: str, verdict: str) -> None:
        key[item_id]["verdict"] = verdict
        key[item_id]["judge_prompt_sha256"] = judge_prompt_sha256()
        _write_key(labels, key)

    budget = RoundBudget(cap_usd)
    if mode == "sync":
        verdicts, cost, stop = _judge_sync(
            items, client or AnthropicJudgeClient(), budget, store
        )
    else:
        verdicts, cost, stop = _judge_batch(
            items,
            batch_client or AnthropicBatchClient(),
            budget,
            store,
            poll_seconds,
            timeout_seconds,
        )
    if verdicts:
        _append_jsonl(
            labels / "judge-runs.jsonl",
            [
                {
                    "split": split,
                    "judge_prompt_sha256": judge_prompt_sha256(),
                    "forced": force,
                    "items": len(verdicts),
                    "complete": len(verdicts) == count,
                    "cost_usd": str(cost),
                }
            ],
        )
    print(f"Judged {len(verdicts)} of {count} items, cost US$ {cost}")
    if stop:
        print(f"Judging stopped: {stop}", file=sys.stderr)
        return 1
    return 0


# present and record


def _present(directory: Path, count: int) -> None:
    labels = _labels(directory)
    recorded = {row["id"] for row in _read_jsonl(labels / "human.jsonl")}
    pending = [i for i in _read_pool(labels) if i["id"] not in recorded][:count]
    if not pending:
        print("No unlabelled items left")
    for item in pending:
        print(f"[{item['id']}] {item['criterion']}\n{item['slice']}\n")


def _record(directory: Path, labels_json: Path) -> None:
    labels = _labels(directory)
    known = {item["id"] for item in _read_pool(labels)}
    seen = {row["id"] for row in _read_jsonl(labels / "human.jsonl")}
    batch = json.loads(labels_json.read_text(encoding="utf-8"))
    if not isinstance(batch, list) or not all(isinstance(e, dict) for e in batch):
        raise _Refused(f"{labels_json}: expected a list of {{id, label}} objects")
    for entry in batch:
        if entry.get("label") not in ("pass", "fail"):
            raise _Refused(f"item {entry.get('id')}: the label must be pass or fail")
        if entry.get("id") not in known:
            raise _Refused(f"item {entry.get('id')}: not in the pool")
        if entry["id"] in seen:
            raise _Refused(f"item {entry['id']}: already labelled")
        seen.add(entry["id"])
    rows = [{"id": e["id"], "label": e["label"]} for e in batch]
    _append_jsonl(labels / "human.jsonl", rows)
    print(f"Recorded {len(rows)} labels")


# tpr-tnr


def _rate(rows: Sequence[_Row], prefix: str, group: str, label: str, floor: str) -> str:
    # unable verdicts are coverage, not a hit or a miss
    judged = [
        r
        for r in rows
        if r.group == group and r.human == label and r.verdict != "unable"
    ]
    if not judged:
        return f"{prefix} undefined"
    hits = sum(r.verdict == label for r in judged)
    low, high = wilson_interval(hits, len(judged))
    cases = len({r.case_id for r in judged})
    return (
        f"{prefix} {hits}/{len(judged)} items, {cases} cases, "
        f"wilson95 {low:.2f}-{high:.2f}, floor {floor}"
    )


def _criterion_lines(
    criterion: str, rows: Sequence[_Row], floor: dict[str, float]
) -> list[str]:
    tpr = f"{floor['tpr']:.2f}" if floor else "none"
    tnr = f"{floor['tnr']:.2f}" if floor else "none"
    lines = [_rate(rows, f"{criterion} natural TPR", "natural", "pass", tpr)]
    if any(r.group == "natural" and r.human == "fail" for r in rows):
        lines.append(_rate(rows, f"{criterion} natural TNR", "natural", "fail", tnr))
    else:
        lines.append(f"{criterion} natural TNR seeded only")
    seeded = [r for r in rows if r.group == "seeded"]
    if seeded:
        lines.append(_rate(rows, f"{criterion} seeded TNR", "seeded", "fail", tnr))
        weak = sum(r.human == "pass" for r in seeded)
        lines.append(f"{criterion} weak seeds {weak}")
    if any(r.group == "owner_written" for r in rows):
        lines.append(
            _rate(rows, f"{criterion} owner_written TNR", "owner_written", "fail", tnr)
        )
    unable = sum(r.verdict == "unable" for r in rows)
    lines.append(f"{criterion} unable {unable}/{len(rows)} items")
    return lines


def _tpr_tnr(directory: Path, split: str) -> None:
    labels = _labels(directory)
    floor = _floor(labels, required=split == "test")
    key = _read_key(labels)
    slices = {item["id"]: item["slice"] for item in _read_pool(labels)}
    human = {row["id"]: row["label"] for row in _read_jsonl(labels / "human.jsonl")}
    in_split = {i: entry for i, entry in key.items() if entry["split"] == split}
    if not any("verdict" in entry for entry in in_split.values()):
        raise _Refused(f"no judge run covered the {split} split, run judge-pool first")
    if split == "test" and any("verdict" not in entry for entry in in_split.values()):
        raise _Refused("some test items still wait for a verdict, finish judge-pool")
    # few-shot items are in the prompt, so they would inflate the dev metrics
    shots = (
        {(e.criterion, e.slice) for e in judge_module.FEW_SHOT_EXAMPLES}
        if split == "dev"
        else set()
    )
    rows = [
        _Row(e["criterion"], e["group"], e["case_id"], human[i], e["verdict"])
        for i, e in in_split.items()
        if i in human and "verdict" in e and (e["criterion"], slices[i]) not in shots
    ]
    lines = []
    for criterion in CRITERIA:
        chosen = [r for r in rows if r.criterion == criterion]
        if chosen:
            lines += _criterion_lines(criterion, chosen, floor.get(criterion, {}))
    print("\n".join(lines))


# the command line


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pet_helper.evals.labels")
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help)
        sub.add_argument("round_dir", type=Path)
        return sub

    command("seed", "write the seeded failures").add_argument(
        "--seed", type=int, default=0
    )
    command("pool", "assemble the blind pool and its key")
    judged = command("judge-pool", "judge the pool items of a split (paid)")
    judged.add_argument("--split", choices=("dev", "test"), required=True)
    judged.add_argument("--judge", choices=("sync", "batch"), default="sync")
    judged.add_argument("--force-new-validation", action="store_true")
    command("present", "print the next unlabelled items").add_argument(
        "--n", type=int, required=True
    )
    command("record", "store human labels").add_argument("labels_json", type=Path)
    command("tpr-tnr", "report the judge against the human labels").add_argument(
        "--split", choices=("dev", "test"), required=True
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client: JudgeClient | None = None,
    batch_client: BatchClient | None = None,
    cap_usd: Decimal = Decimal(5),
    poll_seconds: float = _BATCH_POLL_SECONDS,
    timeout_seconds: float = _BATCH_TIMEOUT_SECONDS,
) -> int:
    args = _parser().parse_args(argv)
    try:
        _open_round(args.round_dir)
        match args.command:
            case "seed":
                _seed(args.round_dir, args.seed)
            case "pool":
                _pool(args.round_dir)
            case "judge-pool":
                return _judge_pool(
                    args.round_dir,
                    args.split,
                    args.judge,
                    args.force_new_validation,
                    client=client,
                    batch_client=batch_client,
                    cap_usd=cap_usd,
                    poll_seconds=poll_seconds,
                    timeout_seconds=timeout_seconds,
                )
            case "present":
                _present(args.round_dir, args.n)
            case "record":
                _record(args.round_dir, args.labels_json)
            case "tpr-tnr":
                _tpr_tnr(args.round_dir, args.split)
    except (_Refused, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
