"""Sessions and Turns in the System Store: a Session is a row holding its message history; each Turn is a row."""

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest

from data_agents.contracts import AnalysisResult, ChartSpec, Clarification, SessionSummary, TurnMetrics, Unanswerable
from data_agents.system import store


class Session(BaseModel):
    id: str
    user: str
    database: str
    turns: int = 0


class StoredChart(BaseModel):
    """What a Turn drew. The Chart Spec is the record; the Vega-Lite built from it under one renderer is its cache, so reopening
    a Session is a read, and a renderer change (a fingerprint that no longer matches) restyles history deliberately, from the spec."""

    spec: ChartSpec
    vega_lite: dict
    renderer: str


class StoredTurn(BaseModel):
    """One Turn as the store kept it, for a client reopening a Session: what was asked, what came back, and what was drawn."""

    n: int
    question: str
    result: AnalysisResult | Clarification | Unanswerable
    chart: StoredChart | None = None


RESULTS = {"AnalysisResult": AnalysisResult, "Clarification": Clarification, "Unanswerable": Unanswerable}


class SessionStore:
    def __init__(self, path: Path | None = None):
        self.conn = store.connect(path)  # no path: the registry directory's own store, shared with Users and the Registry

    def create(self, user: str, database: str) -> Session:
        session = Session(id=uuid.uuid4().hex[:8], user=user, database=database)
        with self.conn:
            self.conn.execute("INSERT INTO sessions (id, user, database, created_at) VALUES (?, ?, ?, ?)",
                              (session.id, user, database, datetime.now(UTC).isoformat()))
        return session

    def get(self, session_id: str) -> Session:
        row = self.conn.execute("SELECT id, user, database, turns FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"no session {session_id}")
        return Session(id=row[0], user=row[1], database=row[2], turns=row[3])

    def delete(self, session_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def list_sessions(self, user: str) -> list[SessionSummary]:
        rows = self.conn.execute(
            "SELECT s.id, s.database, s.turns, s.created_at, (SELECT question FROM turns WHERE session_id = s.id ORDER BY n DESC LIMIT 1) "
            "FROM sessions s WHERE s.user = ? AND s.turns > 0 ORDER BY s.created_at DESC", (user,)).fetchall()  # a Session nobody asked anything in is not history
        return [SessionSummary(id=r[0], database=r[1], turns=r[2], created_at=r[3], last_question=r[4]) for r in rows]

    def turns_of(self, session_id: str) -> list[StoredTurn]:
        rows = self.conn.execute("SELECT n, question, result_kind, result, chart_spec, vega_lite, renderer FROM turns WHERE session_id = ? ORDER BY n",
                                 (session_id,)).fetchall()
        return [StoredTurn(n=r[0], question=r[1], result=RESULTS[r[2]].model_validate_json(r[3]),
                           chart=StoredChart(spec=ChartSpec.model_validate_json(r[4]), vega_lite=json.loads(r[5] or "{}"), renderer=r[6] or "") if r[4] else None)
                for r in rows]

    def turns_without_charts(self) -> list[tuple[Session, StoredTurn]]:
        """Every answered Turn that kept no Chart Spec, with its Session: history recorded before specs were kept."""
        rows = self.conn.execute("SELECT s.id, s.user, s.database, s.turns, t.n, t.question, t.result FROM turns t JOIN sessions s ON s.id = t.session_id "
                                 "WHERE t.result_kind = 'AnalysisResult' AND t.chart_spec IS NULL ORDER BY s.created_at, t.n").fetchall()
        return [(Session(id=r[0], user=r[1], database=r[2], turns=r[3]), StoredTurn(n=r[4], question=r[5], result=AnalysisResult.model_validate_json(r[6])))
                for r in rows]

    def attach_chart(self, session_id: str, n: int, chart: StoredChart) -> None:
        with self.conn:
            self.conn.execute("UPDATE turns SET chart_spec = ?, vega_lite = ?, renderer = ? WHERE session_id = ? AND n = ?",
                              (chart.spec.model_dump_json(), json.dumps(chart.vega_lite), chart.renderer, session_id, n))

    def last_chart(self, session_id: str) -> StoredChart | None:
        """What the Session's latest charted Turn kept. Read on its own, and not with `last_turn`, because a Vega-Lite spec
        carries the rows and every Turn asks for the Turn before it."""
        row = self.conn.execute("SELECT chart_spec, vega_lite, renderer FROM turns WHERE session_id = ? AND chart_spec IS NOT NULL ORDER BY n DESC LIMIT 1",
                                (session_id,)).fetchone()
        return StoredChart(spec=ChartSpec.model_validate_json(row[0]), vega_lite=json.loads(row[1] or "{}"), renderer=row[2] or "") if row else None

    def history(self, session_id: str) -> list[ModelMessage]:
        row = self.conn.execute("SELECT messages FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return ModelMessagesTypeAdapter.validate_json(row[0]) if row and row[0] else []

    def last_turn(self, session_id: str) -> StoredTurn | None:
        """What the Session's last Turn asked and got: the router runs only when that was an answer, never after a Clarification."""
        row = self.conn.execute("SELECT n, question, result_kind, result FROM turns WHERE session_id = ? ORDER BY n DESC LIMIT 1", (session_id,)).fetchone()
        return StoredTurn(n=row[0], question=row[1], result=RESULTS[row[2]].model_validate_json(row[3])) if row else None

    def last_result(self, session_id: str) -> AnalysisResult:
        row = self.conn.execute("SELECT result FROM turns WHERE session_id = ? AND result_kind = 'AnalysisResult' ORDER BY n DESC LIMIT 1",
                                (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"session {session_id} has no analysis result yet")
        return AnalysisResult.model_validate_json(row[0])

    def record_turn(self, session: Session, question: str, messages: list[ModelMessage] | None,
                    result: AnalysisResult | Clarification | Unanswerable, metrics: TurnMetrics, chart: StoredChart | None = None) -> None:
        """One Turn, in one transaction. `messages` None leaves the history as it was: a chart-only Turn asked the model nothing."""
        session.turns += 1
        with self.conn:
            if messages is None:
                self.conn.execute("UPDATE sessions SET turns = ? WHERE id = ?", (session.turns, session.id))
            else:
                self.conn.execute("UPDATE sessions SET turns = ?, messages = ? WHERE id = ?", (session.turns, dump_history(messages), session.id))
            self.conn.execute(
                "INSERT INTO turns (session_id, n, created_at, question, result_kind, result, metrics, chart_spec, vega_lite, renderer) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session.id, session.turns, datetime.now(UTC).isoformat(), question, type(result).__name__, result.model_dump_json(),
                 metrics.model_dump_json(), chart.spec.model_dump_json() if chart else None, json.dumps(chart.vega_lite) if chart else None,
                 chart.renderer if chart else None))


def dump_history(messages: list[ModelMessage]) -> bytes:
    """Drop the instructions before storing: they are 79% of a Session row, and every stored copy is a stale Schema Document.

    Safe because the agent flow takes instructions from the current run's request parameters; the copy in history is only a
    fallback for direct Model.request() callers, which we are not.
    """
    stored = [replace(m, instructions=None) if isinstance(m, ModelRequest) else m for m in messages]
    return ModelMessagesTypeAdapter.dump_json(stored)
