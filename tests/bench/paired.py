"""Two benchmark runs over the same cases, compared case by case.

Paired comparison is the whole reason the sample is seeded: it cancels question difficulty, so the variance of the *difference*
is set by the cases that disagree rather than by the spread of the set. The same reduction answers two questions with one
table — the noise floor, when both runs are the same configuration, and whether a change moved anything, when they are not.

The discordant pairs are what McNemar's test reads, so they are printed as `b` and `c` rather than as two accuracies: a
difference smaller than the discordance is not a result, however different the two totals look.
"""

import argparse
import json
from pathlib import Path


def metrics(before: list[dict], after: list[dict]) -> list[str]:
    """Every key both runs carry whose values are all bool or None, in the first run's order. So a scoring change pairs on the
    rows it kept, and a dataset with rows of its own pairs without a change here."""
    rows = before + after
    return [k for k in before[0] if all(k in r and isinstance(r[k], (bool, type(None))) for r in rows)] if rows else []


def compare(before: list[dict], after: list[dict]) -> dict[str, tuple[int, int, int]]:
    """Per metric: how many cases both runs scored, and the two discordant counts (lost, gained)."""
    second = {r["id"]: r for r in after}
    out = {}
    for metric in metrics(before, after):
        pairs = [(r[metric], second[r["id"]][metric]) for r in before
                 if r["id"] in second and r[metric] is not None and second[r["id"]][metric] is not None]
        out[metric] = (len(pairs), sum(a and not b for a, b in pairs), sum(b and not a for a, b in pairs))
    return out


def report(before: list[dict], after: list[dict], names: tuple[str, str] = ("A", "B")) -> str:
    scored = compare(before, after)
    lines = [f"**Paired over the cases both runs scored.** {names[0]} → {names[1]}.\n",
             f"| metric | n | {names[0]} | {names[1]} | lost | gained | discordant |", "|---|---|---|---|---|---|---|"]
    second = {r["id"]: r for r in after}
    for metric, (n, lost, gained) in scored.items():
        if not n:
            continue
        a = sum(bool(r[metric]) for r in before if r["id"] in second and r[metric] is not None and second[r["id"]][metric] is not None)
        lines.append(f"| {metric} | {n} | {a} | {a - lost + gained} | {lost} | {gained} | {lost + gained} ({100 * (lost + gained) / n:.0f}%) |")
    worst = max((lost + gained for _, lost, gained in scored.values()), default=0)
    lines.append(f"\nThe widest discordance is **{worst}** cases, so a difference of fewer than about that many is noise "
                 "until it is repeated, whatever the two totals say.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two benchmark runs case by case.")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--after-reply", action="store_true", help="pair the answers the simulated user's reply led to, not the first passes")
    args = parser.parse_args()
    runs = [json.loads(path.read_text()) for path in (args.before, args.after)]
    if args.after_reply:
        from tests.bench.table import after
        runs = [after(rows) for rows in runs]
    print(report(*runs, (args.before.stem, args.after.stem)))


if __name__ == "__main__":
    main()
