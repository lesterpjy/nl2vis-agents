"""Reductions over stored Turn streams: what each deterministic check did, crossed with whether the answer was right.

One reducer, two callers: `tests/bench/checks.py` reduces a benchmark run, where `correct` is known,
and the product reduces the Session Store, where it is not. A check that never fires across the dev slice and the shape grid
is dead weight; one that fires often and is never fixed is a prompt problem wearing a check's clothes; and one that fires only
on answers that were already right is not the wrongness signal it might be mistaken for. Nothing here puts question
text, SQL or rows into its output.
"""

from data_agents.contracts import QueryResult
from data_agents.agents.analysis import unordered_cut
from data_agents.agents.grain import twins
from data_agents.agents.sql_checks import CHECKS, carries
from data_agents.charts.chartable import no_measure, split_identity


def _final_sql(events: list[dict]) -> str:
    """Off the last Turn: a case the benchmark's simulated user answered holds the asking Turn and the reply's on one stream."""
    return next((e["result"].get("sql", "") for e in reversed(events) if e["kind"] == "turn_finished"), "")


def _knobs(events: list[dict]) -> list[str]:
    detail = next((e.get("detail", "") for e in events if e["kind"] == "check_fired" and e["check"] == "grain"), "")
    return detail.split(", ") if detail else []


def probes(turns: list[dict]) -> tuple[int, int, int]:
    """Turns the grain probe ran on, Turns where the twin disagreed (the check's firings), and how many of those answers were
    wrong where the run knows; a Clarification counts as wrong here, which is the runner's business to answer."""
    ran = [t for t in turns if any(e["kind"] == "grain_probed" for e in t["events"])]
    noted = [t for t in ran if any(e["kind"] == "grain_probed" and not e["agreed"] for e in t["events"])]
    return len(ran), len(noted), sum(t.get("correct") is False for t in noted)


def probed(turns: list[dict]) -> dict[str, tuple[int, int, int]]:
    """Per rewrite check: Turns its twin ran on, Turns where it disagreed, and how many of those answers were wrong where known.
    The population always beside the firing: a check is judged by the size of the failure it prevents, counted over the Turns
    that carried its class, because a count with no denominator says nothing."""
    out = {}
    for check in CHECKS:
        if check == "grain":
            continue
        ran = [t for t in turns if any(e["kind"] == "check_probed" and e["check"] == check for e in t["events"])]
        noted = [t for t in ran if any(e["kind"] == "check_probed" and e["check"] == check and not e["agreed"] for e in t["events"])]
        out[check] = (len(ran), len(noted), sum(t.get("correct") is False for t in noted))
    return out


def _reached(events: list[dict], kind: str) -> bool:
    return any(e["kind"] == kind for e in events)


# check -> what "the next attempt fixed it" means, given the Turn's events and its final answer table.
FIXED = {
    "zero_row": lambda events, answer: bool(answer and answer["rows"]),
    "split_identity": lambda events, answer: bool(answer) and not split_identity(QueryResult(**answer)),
    "no_measure": lambda events, answer: bool(answer) and not no_measure(QueryResult(**answer)),
    "unordered_limit": lambda events, answer: bool(answer) and not unordered_cut(_final_sql(events)),
    # The grain check hands back a decision, not a defect: "fixed" means the model rewrote the query so the knob that fired is
    # gone. Keeping the SQL and saying why is a legitimate answer too, counted as standing; asking is a Clarification, not an answer.
    "grain": lambda events, answer: bool(answer) and not set(_knobs(events)) & set(twins(_final_sql(events))),
    # The six rewrite checks likewise: the class is gone from the final SQL (a bare column grouped or aggregated, a sum taken
    # before the join, a float in the division, an exclusive bound, an IS NULL kept, a cast in the ORDER BY) or the answer stands.
    **{check: (lambda check: lambda events, answer: bool(answer) and not carries(check, _final_sql(events)))(check)
       for check in CHECKS if check != "grain"},
    "sql_rejected": lambda events, answer: _reached(events, "rows_fetched"),
    "sql_failed": lambda events, answer: _reached(events, "rows_fetched"),
    "visualization_retry": lambda events, answer: _reached(events, "chart_ready"),
    "columns_exist": lambda events, answer: _reached(events, "chart_ready"),
    "chart_reads": lambda events, answer: _reached(events, "chart_ready"),
    "reshape_requested": lambda events, answer: _reached(events, "chart_ready"),
    # A skip is the residual by definition: the Turn explains itself instead of drawing, which is the intended end, not a failure.
    "unreadable_skip": lambda events, answer: False,
    "unbuildable_skip": lambda events, answer: False,
    "reshape_skip": lambda events, answer: False,
    "single_value_skip": lambda events, answer: False,
    "empty_skip": lambda events, answer: False,
}
SKIPS = {"single-value result": "single_value", "the result is empty": "empty"}  # streams stored before skips carried a code


def _skip(e: dict) -> tuple[str, str]:
    code = e.get("code") or SKIPS.get(e["reason"], "unreadable")
    return f"{code}_skip", (e.get("detail") or e["reason"])[:60]


def firings(events: list[dict]) -> list[tuple[str, str]]:
    """Every check that fired in one Turn, as (check, detail). The guard and the driver are one event and two checks."""
    out = []
    for e in events:
        if e["kind"] == "check_fired":
            out.append((e["check"], e.get("detail", "")))
        elif e["kind"] == "sql_failed":
            out.append(("sql_rejected" if "rejected" in e["reason"].lower() else "sql_failed", e["reason"][:60]))
        elif e["kind"] == "reshape_requested":
            out.append(("reshape_requested", e["instruction"][:60]))
        elif e["kind"] == "chart_skipped":  # five different ends share one event, and they mean different things
            out.append(_skip(e))
        elif e["kind"] == "model_call_done" and e["stage"] == "visualization" and e["requests"] > 1:
            out.append(("visualization_retry", f"{e['requests']} requests"))
    # Both validators name themselves, so the retry they caused is theirs and not a second, anonymous row beside them.
    if {"columns_exist", "chart_reads"} & {check for check, _ in out}:
        out = [(check, detail) for check, detail in out if check != "visualization_retry"]
    return out


def checks(turns: list[dict]) -> dict[str, dict[str, int]]:
    """Per check: fired, self-corrected, ended with no chart, and fired on a wrong answer where the run knows what was right."""
    rows: dict[str, dict[str, int]] = {}
    for t in turns:
        for check in {check for check, _ in firings(t["events"])}:
            row = rows.setdefault(check, {"fired": 0, "fixed": 0, "no_chart": 0, "wrong": 0})
            row["fired"] += 1
            row["fixed"] += bool(FIXED.get(check, lambda *_: False)(t["events"], t.get("answer")))
            row["no_chart"] += not t.get("charted")
            row["wrong"] += t.get("correct") is False
    return rows


def crossed(turns: list[dict]) -> dict[str, int]:
    """The check layer as a wrongness signal: of the wrong answers, how many fired anything; of the Turns that fired, how many
    were right anyway. Only meaningful where `correct` is known, which is a benchmark run and never production."""
    scored = [t for t in turns if t.get("correct") is not None]
    fired = [t for t in scored if firings(t["events"])]
    wrong = [t for t in scored if t["correct"] is False]
    return {"scored": len(scored), "wrong": len(wrong), "wrong_fired": sum(bool(firings(t["events"])) for t in wrong),
            "fired": len(fired), "fired_correct": sum(bool(t["correct"]) for t in fired)}


def report(turns: list[dict]) -> str:
    turns = [t for t in turns if t.get("events")]
    rows, cross = checks(turns), crossed(turns)
    statements = sum(e["kind"] == "sql_written" for t in turns for e in t["events"])
    charts = sum(e["kind"] in ("chart_ready", "chart_skipped") for t in turns for e in t["events"])
    known = cross["scored"] > 0
    lines = [f"**Correction layers over {len(turns)} Turns**, {statements} SQL statements and {charts} charts — the denominators, "
             "because a zero means nothing without the population it is zero over.\n",
             "| check | fired | fired/Turn | self-corrected | ended with no chart |" + (" on a wrong answer |" if known else ""),
             "|---|---|---|---|---|" + ("---|" if known else "")]
    for check in sorted(rows, key=lambda c: -rows[c]["fired"]):
        r = rows[check]
        lines.append(f"| {check} | {r['fired']} | {r['fired'] / len(turns):.2f} | {r['fixed']}/{r['fired']} ({100 * r['fixed'] / r['fired']:.0f}%) "
                     f"| {r['no_chart']}/{r['fired']} |" + (f" {r['wrong']}/{r['fired']} |" if known else ""))
    for dead in sorted(set(FIXED) - set(rows)):
        lines.append(f"| {dead} | 0 | 0.00 | - | - |" + (" - |" if known else ""))
    ran, noted, noted_wrong = probes(turns)
    lines.append(f"\nThe grain probe ran on {ran} Turns ({100 * ran / max(1, len(turns)):.0f}%) and the twin disagreed on {noted}"
                 + (f", {noted_wrong} of which were not correct answers." if known else "."))
    for check, (ran, noted, noted_wrong) in probed(turns).items():
        lines.append(f"`{check}` ran its twin on {ran} Turns ({100 * ran / max(1, len(turns)):.0f}%) and it disagreed on {noted}"
                     + (f", {noted_wrong} of which were not correct answers." if known else "."))
    if known:
        lines.append(f"\n{cross['wrong']} answers were wrong and **{cross['wrong_fired']}** of them fired any check "
                     f"({100 * cross['wrong_fired'] / max(1, cross['wrong']):.0f}%); {cross['fired']} Turns fired a check and "
                     f"{cross['fired_correct']} of those answers were right anyway. The layer refuses malformed output; wrongness is "
                     "invisible to it except where the two coincide.")
    return "\n".join(lines)
