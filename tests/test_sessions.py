"""The Session Store's own queries over a temporary store; no model, no orchestrator."""

import pytest

from data_agents.contracts import AnalysisResult, Clarification, QueryResult, TurnMetrics
from data_agents.system.sessions import SessionStore

METRICS = TurnMetrics(latency_ms={})
RESULT = AnalysisResult(intent="comparison", sql="SELECT 1", narrative="n", table=QueryResult(columns=["a"], rows=[[1]]))


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


def test_list_sessions_shows_only_my_own_newest_first(store):
    mine, other = store.create("alice", "sakila"), store.create("bob", "northwind_small")
    later, empty = store.create("alice", "chinook"), store.create("alice", "northwind_small")
    store.record_turn(mine, "How much revenue per category?", [], RESULT, METRICS)
    store.record_turn(mine, "Only the top 3", [], RESULT, METRICS)
    store.record_turn(later, "Revenue per year?", [], RESULT, METRICS)
    store.record_turn(other, "Who ships the most?", [], RESULT, METRICS)

    listed = store.list_sessions("alice")
    assert [s.id for s in listed] == [later.id, mine.id]  # created_at descending
    assert [s.database for s in listed] == ["chinook", "sakila"]
    assert listed[1].turns == 2 and listed[1].last_question == "Only the top 3"
    assert empty.id not in [s.id for s in listed]  # a Session nobody asked anything in is not history
    assert [s.id for s in store.list_sessions("bob")] == [other.id]
    assert store.list_sessions("nobody") == []


def test_a_clarification_turn_still_counts_and_shows(store):
    session = store.create("alice", "chinook")
    store.record_turn(session, "Compare sales between the two periods", [], Clarification(question="Which two?"), METRICS)
    summary = store.list_sessions("alice")[0]
    assert summary.turns == 1 and summary.last_question == "Compare sales between the two periods"
