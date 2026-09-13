"""The results table of one run, written from its rows and nothing else.

Every verdict row the run carries is a column, grouped by the case's hardness and by whatever facets the dataset's `score`
returned; the first pass and the answer after the simulated user's reply are two blocks, never one. The caveats that belong
beside a number live with the number where it is published (the README), not here.
"""

from collections import Counter

from tests.bench.scoring import COSTS, metrics


def table(scored: list[dict], which: str = "run") -> str:
    lines = [f"**Set: {which}.**\n"]
    lines += _blocks(scored, "first pass")
    if any(r.get("answered") for r in scored):
        lines.append(f"_Asked a Clarification on {pct(scored, 'asked')}; the simulated user answered {pct(scored, 'answered')} of those._\n")
        lines += _blocks(after(scored), "after the simulated user's reply")
    lines.append(_totals(scored))
    return "\n".join(lines)


def after(scored: list[dict]) -> list[dict]:
    """The rows of the answer each case ends with: the reply Turn's where one was played, the first pass's otherwise."""
    return [r.get("after") or r for r in scored]


def _blocks(rows: list[dict], condition: str) -> list[str]:
    names = metrics(rows)
    groups = [("hardness", lambda r: r["hardness"])] if any(r.get("hardness") for r in rows) else []
    groups += [(facet, (lambda r, f=facet: r.get("facets", {}).get(f, "-"))) for facet in _facets(rows)]
    out = [f"**{condition}**\n"]
    for name, key in groups:
        out += [f"| {name} | n | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
        for value in sorted({key(r) for r in rows}):
            group = [r for r in rows if key(r) == value]
            out.append(f"| {value} | {len(group)} | " + " | ".join(pct(group, m) for m in names) + " |")
        out.append("")
    out.append(f"**{len(rows)} cases, {condition}**: " + ", ".join(f"{m} {pct(rows, m)}" for m in names) + "\n")
    return out


def _facets(rows: list[dict]) -> list[str]:
    return sorted({facet for r in rows for facet in r.get("facets", {})})


def _totals(scored: list[dict]) -> str:
    cost = sum(r.get("cost_usd", 0) for r in scored)
    requests = sum(r.get("requests", 0) for r in scored)
    marks = Counter(r["mark"] for r in after(scored))
    by = Counter(r.get("scored_by") for r in scored)
    parts = [f"the case's own gold SQL on {by['gold sql']}"]
    if by["no gold"]:
        parts.append(f"{by['no gold']} with no executable gold, excluded from `ex` and `correct`")
    if by["gold failed"]:
        parts.append(f"{by['gold failed']} whose gold SQL does not run, counted and not scored")
    errors = sum("error" in r for r in scored)
    return (f"_Scored by {', '.join(parts)}; {errors} Turn error{'s' if errors != 1 else ''}._ ${cost:.3f} · {requests} model calls · "
            f"marks {dict(marks)} · cost rows {', '.join(COSTS)}")


def pct(rows: list[dict], key: str) -> str:
    """Over the cases the metric applies to: a None means the question did not ask, and counting those as passes once turned
    124 of 542 into 732 of 1,150. A numeric row prints its mean, as the papers that define one do."""
    scored = [r for r in rows if r.get(key) is not None]
    if not scored:
        return "-"
    if any(isinstance(r[key], float) for r in scored):
        return f"{100 * sum(r[key] for r in scored) / len(scored):.1f}"
    hits = sum(bool(r[key]) for r in scored)
    return f"{hits}/{len(scored)} ({100 * hits / len(scored):.0f}%)"
