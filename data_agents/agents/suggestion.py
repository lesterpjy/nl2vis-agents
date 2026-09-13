"""One question to put in the box: the first one for a database, or the next one after an answer.

A convenience, never part of a Turn. It runs outside the Analysis Agent's budget, it fails silently, and the user reads what it
wrote before anything runs. Its input is table and column names and the answer's own narrative — never result rows.
"""

import os

from pydantic_ai import Agent

from data_agents.agents.model_settings import SETTINGS

INSTRUCTIONS = """You write one short question a person would ask of a database, to fill in a text box for them.

One question, under fifteen words, answerable by a single SQL query over the tables you are shown, about something a chart can
carry: a count, a total, an average, a share, a change over time. Never name a table or a column; write the way a person
speaks. Return the question alone, with no preamble, no quotation marks and no explanation."""

# A convenience must not hold a page open for the two minutes a Turn is allowed.
agent = Agent(os.environ.get("SUGGESTION_MODEL", "openai:gpt-4.1-mini"), instructions=INSTRUCTIONS,
              model_settings=SETTINGS | {"timeout": 10.0}, retries=1, defer_model_check=True, name="suggestion_agent")

_first: dict[tuple[str, str], str] = {}  # one call per database and schema per process: a database registered again from another file is asked afresh


def _ask(prompt: str) -> str:
    try:
        return agent.run_sync(prompt).output.strip().strip('"').splitlines()[0]
    except Exception:
        return ""  # no suggestion is a fine outcome; a page must never fail because one could not be written


def first_question(database: str, schema: str) -> str:
    if not _first.get((database, schema)):
        _first[database, schema] = _ask(f"The database is called {database}. Its tables and columns:\n{schema}\n\nWrite the first question to ask it.")
    return _first[database, schema]


def next_question(question: str, narrative: str, columns: list[str]) -> str:
    return _ask(f"Someone asked: {question}\nThe answer was: {narrative}\nThe answer's columns: {', '.join(columns)}\n\n"
                "Write the question they would naturally ask next, refining or extending that answer.")


def answer_to(question: str, clarification: str) -> str:
    """The Turn ended in a Clarification, so what belongs in the box is an answer to it, not another question."""
    return _ask(f"Someone asked: {question}\nThe analyst asked back: {clarification}\n\n"
                "Write what they would reply. Answer the analyst's question and nothing else — pick one of the readings or "
                "values it offers, in a few words. This is a reply, so it need not be a question.")
