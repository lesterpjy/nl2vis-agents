"""Tier 2 cases, one test each. Replayed from tapes by default; `pytest --live` calls the model and re-records."""

import pytest

from tests.evals.cases import CASES
from tests.evals.runner import evaluate


@pytest.mark.parametrize("name", [c.name for c in CASES])
def test_case(name, live):
    report = evaluate(live, names=[name])
    case = report.cases[0]
    failed = [k for k, a in case.assertions.items() if not a.value]
    assert not failed, f"{name} failed {failed}; sql={case.attributes.get('sql')!r}"
