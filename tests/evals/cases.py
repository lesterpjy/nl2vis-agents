"""Tier 2 cases: database, Turns, golden SQL. Correctness is result-set equality on the same database, not SQL text."""

from pydantic import BaseModel
from pydantic_evals import Case, Dataset

from data_agents.contracts import AnalysisResult, ChartSpec, Clarification, Intent, TurnMetrics, Unanswerable


class EvalInputs(BaseModel):
    name: str
    database: str
    turns: list[str]  # every Turn runs in one Session; a Clarification answer is the next Turn


class Golden(BaseModel):
    sql: str = ""  # empty: nothing in the schema answers the question, so abstaining is the correct answer
    intents: list[Intent] = []  # empty: not checked (a single number has no meaningful intent)
    clarify: bool = False  # the first Turn must end in a Clarification, the last must not
    chart: bool = True  # a chart is expected; False for single-value results (never a one-bar chart)


class EvalOutput(BaseModel):
    result: AnalysisResult | Clarification | Unanswerable
    asked: list[bool]  # per Turn
    charted: bool
    spec: ChartSpec | None  # what the last Turn charted; the Vega-Lite spec is rebuilt from it deterministically
    metrics: list[TurnMetrics]


def case(name: str, database: str, turns: str | list[str], sql: str = "", **golden) -> Case:
    turns = [turns] if isinstance(turns, str) else turns
    return Case(name=name, inputs=EvalInputs(name=name, database=database, turns=turns), expected_output=Golden(sql=sql, **golden))


CASES = [
    case("sakila_category_revenue", "sakila", "How much revenue does each film category bring in?",
         "SELECT c.name, SUM(p.amount) FROM payment p JOIN rental r ON r.rental_id = p.rental_id JOIN inventory i ON i.inventory_id = r.inventory_id "
         "JOIN film_category fc ON fc.film_id = i.film_id JOIN category c ON c.category_id = fc.category_id GROUP BY c.name",
         intents=["comparison"]),
    case("chinook_revenue_per_year", "chinook", "How did invoice revenue develop per year?",
         "SELECT strftime('%Y', InvoiceDate), SUM(Total) FROM invoices GROUP BY 1", intents=["trend"]),
    case("northwind_small_shipper_share", "northwind_small", "What percentage of orders does each shipper handle?",
         'SELECT s.CompanyName, 100.0 * COUNT(*) / (SELECT COUNT(*) FROM "Order") FROM "Order" o JOIN Shipper s ON s.Id = o.ShipVia GROUP BY s.CompanyName',
         intents=["share"]),
    case("sakila_rating_distribution", "sakila", "How are films distributed across ratings?",
         "SELECT rating, COUNT(*) FROM film GROUP BY rating", intents=["distribution"]),
    case("chinook_top_genres_followup", "chinook", ["How many tracks does each genre have?", "Only the top 3"],
         "SELECT g.Name, COUNT(*) AS n FROM tracks t JOIN genres g ON g.GenreId = t.GenreId GROUP BY g.Name ORDER BY n DESC LIMIT 3",
         intents=["comparison"]),
    case("chinook_compare_periods_clarifies", "chinook", ["Compare sales between the two periods", "2012 versus 2013, total invoice revenue"],
         "SELECT strftime('%Y', InvoiceDate) AS year, SUM(Total) FROM invoices WHERE year IN ('2012', '2013') GROUP BY 1",
         intents=["comparison"], clarify=True),
    case("sakila_r_rated_count", "sakila", "How many films are rated R?",  # do-not-ask: a sensible default exists; single value: no chart
         "SELECT COUNT(*) FROM film WHERE rating = 'R'", chart=False),
    case("northwind_small_category_revenue_by_year", "northwind_small", "Revenue per product category for each year",
         "SELECT c.CategoryName, strftime('%Y', o.OrderDate), SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) FROM OrderDetail od "
         'JOIN "Order" o ON o.Id = od.OrderId JOIN Product p ON p.Id = od.ProductId JOIN Category c ON c.Id = p.CategoryId GROUP BY 1, 2',
         intents=["comparison", "trend"]),
    # Value linking: the literal must match the stored spelling (names are UPPERCASE), or be probed first.
    case("sakila_actor_films_case_mismatch", "sakila", "How many films has Penelope Guiness appeared in?",
         "SELECT COUNT(*) FROM film_actor fa JOIN actor a ON a.actor_id = fa.actor_id WHERE a.first_name = 'PENELOPE' AND a.last_name = 'GUINESS'", chart=False),
    # Value linking: no such category; a confident zero is the wrong answer, a Clarification naming real categories is the right one.
    case("sakila_missing_category_clarifies", "sakila", ["How much revenue do Thriller films bring in?", "Horror then"],
         "SELECT SUM(p.amount) FROM payment p JOIN rental r ON r.rental_id = p.rental_id JOIN inventory i ON i.inventory_id = r.inventory_id "
         "JOIN film_category fc ON fc.film_id = i.film_id JOIN category c ON c.category_id = fc.category_id WHERE c.name = 'Horror'", clarify=True, chart=False),
    # Chartable shape: the natural SQL splits the actor's name over two columns, and no bar can be labelled with both.
    case("sakila_top_actors", "sakila", "Which five actors appear in the most films?",
         "SELECT a.first_name || ' ' || a.last_name AS actor, COUNT(*) AS film_count FROM film_actor fa JOIN actor a ON a.actor_id = fa.actor_id "
         "GROUP BY a.actor_id ORDER BY film_count DESC LIMIT 5", intents=["comparison"]),
    # Abstention: Sakila has actors but no directors, and no answer from the user would change that. A Clarification is as wrong here as a proxy.
    case("sakila_no_director_data_abstains", "sakila", "Which director has the most films in the catalogue?", chart=False),
]

dataset: Dataset[EvalInputs, EvalOutput, None] = Dataset(name="tier2", cases=CASES)
