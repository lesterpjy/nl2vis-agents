"""Reply to the Clarifications of a stored run from its own Sessions, with no rerun of the rest.

A run's Sessions that asked are still in the Session Store with their message history, so the simulated user's second Turn
can be played from each first Turn: about a cent a case instead of the $2.20 the question Turns cost. A Session that has since
been replied to is revived as a fresh Session holding only its first Turn, so the reply is paired on the same first Turn as
any earlier reply was. Every other row is carried over unchanged, so the file this writes pairs against the run it came from
and against anything that run paired against. Announce the spend before running it, as for any live batch.
"""

import argparse
import json
from pathlib import Path

# The runner names the benchmark registry before `data_agents` binds its own at import, so it comes first (see runner.py).
from tests.bench.runner import BENCH, USER, adapter_named, line, play
from data_agents.contracts import TurnMetrics  # noqa: E402
from data_agents.system.sessions import Session, SessionStore  # noqa: E402
from pydantic_ai.messages import ModelRequest, UserPromptPart  # noqa: E402
from tests.bench.case import Case  # noqa: E402
from tests.bench.rescore import rescore, turns  # noqa: E402
from tests.bench.table import table  # noqa: E402


def find(store: SessionStore, case: Case) -> Session:
    """The Session that asked: the latest one on the case's Database whose first Turn was its question and ended in a Clarification."""
    row = store.conn.execute(
        "SELECT s.id FROM sessions s JOIN turns t ON t.session_id = s.id AND t.n = 1 "
        "WHERE s.user = ? AND s.database = ? AND t.question = ? AND t.result_kind = 'Clarification' ORDER BY s.created_at DESC LIMIT 1",
        (USER, case.database, case.question)).fetchone()
    if row is None:
        raise KeyError(f"no stored Session asked case {case.id}")
    return store.get(row[0])


def revive(store: SessionStore, session: Session) -> Session:
    """A fresh Session holding the first Turn alone: its question, its result and the messages up to the second user prompt."""
    messages = store.history(session.id)
    prompts = [i for i, m in enumerate(messages) if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)]
    first = store.turns_of(session.id)[0]
    fresh = store.create(session.user, session.database)
    store.record_turn(fresh, first.question, messages[: prompts[1]] if len(prompts) > 1 else messages, first.result, TurnMetrics(latency_ms={}))
    return fresh


def main() -> None:
    parser = argparse.ArgumentParser(description="Play the simulated user's reply into the stored Sessions of a run.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--dataset", required=True, help="the adapter module under tests/bench/")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, default=BENCH / "bench-sessions.db")
    args = parser.parse_args()
    adapter = adapter_named(args.dataset)
    cases = {c.id: c for c in adapter.cases("all")}
    rows = rescore(json.loads(args.results.read_text()), adapter, cases)  # first passes, under today's rules
    adapter.register([cases[r["id"]] for r in rows])
    store, out = SessionStore(args.sessions), []
    for i, row in enumerate(rows, 1):
        case = cases[row["id"]]
        if not row["asked"]:
            out.append(row)
            continue
        first = {k: v for k, v in row.items() if k not in ("after", "asked", "answered", "reply")} | {"events": turns(row["events"])[0]}
        out.append(play(adapter, store, revive(store, find(store, case)), case, first))
        args.out.write_text(json.dumps(out, indent=1))  # after every case, as `runner.run` does
        print(f"{i:>3}/{len(rows)} {line(case, out[-1])}", flush=True)
    args.out.write_text(json.dumps(out, indent=1))
    print(table(out, args.results.stem + " + replies"))


if __name__ == "__main__":
    main()
