"""nvBench 2.0's scoring rule, re-expressed from `code/evaluation/evaluation.py` and `utils.py` in HKUSTDial/nvBench-2.0
(main, evaluation.py dated 2025-11-13). The repository carries no licence, so the rule is restated here rather than copied,
function for function, and the tests pin each behaviour the original has.

Their rule: a predicted chart matches a gold chart when the two JSON objects are equal ignoring key order (`deep_compare_charts`:
dicts need the same key count and every value equal; lists the same length and every element found somewhere in the other),
after both sides have their x and y swapped where their metadata says the swap is canonical (`reverse_axes_if_needed`) and
their keys re-ordered (`normalize_chart_order`, which also drops a channel or filter that is not a dict). Per query, the top K
predictions are matched one-to-one against the gold set greedily in order; Precision@K is matches over the K predictions,
Recall@K matches over the gold, F1@K their harmonic mean, and Hit@K whether anything matched. The dataset number is the mean over
queries.
"""

import copy

CHANNELS = ["x", "y", "theta", "color", "size"]
PROPERTIES = ["field", "aggregate", "bin", "sort"]
OPERATORS = ["equal", "lt", "lte", "gt", "gte", "range", "oneOf", "valid"]


def matches(chart1, chart2) -> bool:
    if isinstance(chart1, dict) and isinstance(chart2, dict):
        return len(chart1) == len(chart2) and all(k in chart2 and matches(v, chart2[k]) for k, v in chart1.items())
    if isinstance(chart1, list) and isinstance(chart2, list):
        return len(chart1) == len(chart2) and all(any(matches(x, y) for y in chart2) for x in chart1)
    return chart1 == chart2


def reverse_axes(chart: dict, types: dict[str, str]) -> dict:
    """x and y swapped where both are quantitative and x's field sorts after y's, or where a bar, line or boxplot carries a
    quantitative x against a non-quantitative y. `types` is their `type_by_field`: a field name to its Vega-Lite type."""
    x, y = chart.get("encoding", {}).get("x"), chart.get("encoding", {}).get("y")
    if not (isinstance(x, dict) and isinstance(y, dict) and x.get("field") and y.get("field")):
        return chart
    xf, yf = x["field"], y["field"]
    xf, yf = (xf[0] if isinstance(xf, list) else xf), (yf[0] if isinstance(yf, list) else yf)
    if not (isinstance(xf, str) and isinstance(yf, str)):
        return chart
    xt, yt = types.get(xf), types.get(yf)
    both = xt == "quantitative" and yt == "quantitative" and xf > yf
    upright = chart.get("mark") in ("bar", "line", "boxplot") and xt == "quantitative" and yt != "quantitative"
    if both or upright:
        chart["encoding"]["x"], chart["encoding"]["y"] = y, x
    return chart


def normalize(chart: dict) -> dict:
    out = {}
    if "mark" in chart:
        out["mark"] = chart["mark"]
    if "encoding" in chart:
        out["encoding"] = {}
        for channel in CHANNELS + [c for c in chart["encoding"] if c not in CHANNELS]:
            spec = chart["encoding"].get(channel)
            if not isinstance(spec, dict):
                continue
            out["encoding"][channel] = {p: spec[p] for p in PROPERTIES if p in spec} | {k: v for k, v in spec.items() if k not in PROPERTIES}
    if "transform" in chart:
        out["transform"] = []
        for transform in chart["transform"]:
            condition = transform.get("filter")
            if isinstance(condition, dict):
                out["transform"].append({"filter": {op: condition[op] for op in OPERATORS if op in condition}
                                         | {k: v for k, v in condition.items() if k not in OPERATORS}})
    return out | {k: v for k, v in chart.items() if k not in ("mark", "encoding", "transform")}


def preprocess(charts: list, types: dict[str, str]) -> list:
    """Their swap edits the chart in place; here it edits a copy, so a stored prediction reads as it was made."""
    return [normalize(reverse_axes(copy.deepcopy(c), types)) if isinstance(c, dict) else None for c in charts]


def evaluate(predicted: list, gold: list, types: dict[str, str], k: int) -> dict[str, float]:
    """One query's Hit, Recall, Precision and F1 at K, their way: predictions matched one-to-one against the gold, greedily."""
    predicted, gold = preprocess(predicted, types)[:k], preprocess(gold, types)
    matched_gold: set[int] = set()
    hits = 0
    for y in predicted:
        for i, g in enumerate(gold):
            if i not in matched_gold and matches(y, g):
                hits += 1
                matched_gold.add(i)
                break
    recall = hits / len(gold) if gold else 0.0
    precision = hits / len(predicted) if predicted else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    return {"hit": float(hits > 0), "recall": recall, "precision": precision, "f1": f1}
