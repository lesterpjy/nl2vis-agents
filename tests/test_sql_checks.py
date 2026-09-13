"""Tier 1: the six rewrite checks and the two flips, table-driven on the registered databases, no model. Each check has rows
where the natural SQL commits the fault and rows where it does not and the check must stay silent (tests/fixtures/sql-checks.md)."""

import re
from pathlib import Path

import pytest

from data_agents.agents import sql_checks
from data_agents.contracts import QueryResult
from data_agents.data.database import QueryFailed, ReadOnlyDatabase
from data_agents.data.sql_guard import SqlRejected

DATABASES = {"sakila": "registry/data/sakila.db", "chinook": "registry/data/chinook.db", "northwind_small": "registry/data/northwind_small.sqlite"}


def probe(database: str, sql: str) -> dict[str, sql_checks.Probe]:
    """Every probe that ran on the statement, through the read-only handle, keyed by check."""
    db = ReadOnlyDatabase(DATABASES[database])

    def execute(twin: str) -> QueryResult | None:
        try:
            return db.execute(twin)
        except (SqlRejected, QueryFailed):
            return None

    return {p.check: p for p in sql_checks.probes(sql, db.execute(sql), execute)}


def fired(database: str, sql: str, check: str) -> bool:
    found = probe(database, sql).get(check)
    return bool(found and found.fired)


# --- 1. a bare column beside an aggregate ---------------------------------------------------------------------------------

@pytest.mark.parametrize("database,sql,expected", [
    ("chinook", "SELECT Country, City, COUNT(*) AS n FROM customers GROUP BY Country", True),                          # cities vary inside a country
    ("chinook", "SELECT Country, City, COUNT(*) AS n FROM customers GROUP BY 1", True),                                # the ordinal is the same GROUP BY
    ("sakila", "SELECT strftime('%Y-%m', rental_date) AS month, rental_date, COUNT(*) AS n FROM rental GROUP BY month", True),  # an alias grouped, a raw column bare
    ("sakila", "SELECT a.first_name || ' ' || a.last_name AS actor, COUNT(*) AS films FROM actor a JOIN film_actor fa ON fa.actor_id = a.actor_id GROUP BY a.actor_id", False),  # determined by the key
    ("sakila", "SELECT c.name, f.title, MAX(f.length) FROM film f JOIN film_category fc ON fc.film_id = f.film_id JOIN category c ON c.category_id = fc.category_id GROUP BY c.name", False),  # SQLite's MIN/MAX rule
    ("sakila", "SELECT rating, COUNT(*) AS n FROM film GROUP BY rating", False),                                       # nothing bare
    ("sakila", "SELECT title, length FROM film ORDER BY length DESC LIMIT 3", False),                                   # not an aggregate query
    ("sakila", "SELECT store_id, SUM(amount) OVER (PARTITION BY store_id) FROM payment p JOIN staff s ON s.staff_id = p.staff_id LIMIT 3", False),  # a window is not an aggregate query
    # the key is grouped by one of its three spellings and the bare name is determined by it, so the answer is right
    ("sakila", "SELECT c.customer_id AS id, c.first_name, COUNT(*) AS n FROM customer c JOIN rental r ON r.customer_id = c.customer_id GROUP BY id", False),
    ("sakila", "SELECT f.film_id, f.title, COUNT(*) AS actors FROM film f JOIN film_actor fa ON fa.film_id = f.film_id GROUP BY 1", False),
    ("sakila", "SELECT strftime('%Y', rental_date) AS yr, strftime('%Y', rental_date) || ' total' AS tag, COUNT(*) AS n FROM rental GROUP BY strftime('%Y', rental_date)", False),  # determined by the grouped expression
    ("northwind_small", 'SELECT o.Id, o.ShipCity, COUNT(*) AS n FROM "Order" o JOIN OrderDetail d ON d.OrderId = o.Id GROUP BY o.Id', False),  # a table named for a keyword survives the rewrite
])
def test_a_bare_column_fires_only_where_the_group_holds_more_than_one_value(database, sql, expected):
    assert fired(database, sql, "bare_column") is expected


# --- 2. fan-out -------------------------------------------------------------------------------------------------------------

WRONG_KEY = ("SELECT c.name AS category, SUM(p.amount) AS revenue FROM category c JOIN film_category fc ON fc.category_id = c.category_id "
             "JOIN film f ON f.film_id = fc.film_id JOIN inventory i ON i.film_id = f.film_id JOIN rental r ON r.inventory_id = i.inventory_id "
             "JOIN payment p ON p.customer_id = r.customer_id GROUP BY c.name")
RIGHT_KEY = WRONG_KEY.replace("p.customer_id = r.customer_id", "p.rental_id = r.rental_id")


@pytest.mark.parametrize("database,sql,expected", [
    ("sakila", WRONG_KEY, True),
    ("sakila", WRONG_KEY.replace("SUM(p.amount)", "AVG(p.amount)"), True),                                             # AVG is reweighted the same way
    ("sakila", RIGHT_KEY, False),                                                                                      # one payment per rental
    ("sakila", "SELECT c.first_name, SUM(p.amount) AS spent FROM customer c JOIN payment p ON p.customer_id = c.customer_id JOIN rental r ON r.customer_id = c.customer_id GROUP BY c.customer_id", True),
    ("sakila", "SELECT store_id, SUM(amount) FROM payment p JOIN staff s ON s.staff_id = p.staff_id GROUP BY store_id", False),  # each payment has one staff row
    ("sakila", "SELECT SUM(amount) FROM payment", False),                                                              # no join, no class
    ("sakila", WRONG_KEY.replace("SUM(p.amount)", "COUNT(p.payment_id)"), False),                                      # COUNT belongs to the count flip
    ("sakila", WRONG_KEY.replace("SUM(p.amount)", "MAX(p.amount)"), False),                                            # MAX is not inflated by repetition
    ("sakila", "SELECT f.rating, SUM(fa.actor_id) AS x FROM film f JOIN film_actor fa ON fa.film_id = f.film_id GROUP BY f.rating", False),  # a composite key, matched one to one
    ("northwind_small", "SELECT m.LastName, SUM(e.Id) AS x FROM Employee e JOIN Employee m ON e.ReportsTo = m.Id GROUP BY m.LastName", False),  # a self-join: each row has one manager
    ("northwind_small", 'SELECT o.ShipCountry, SUM(d.Quantity) AS q FROM "Order" o JOIN OrderDetail d ON d.OrderId = o.Id GROUP BY o.ShipCountry', False),
])
def test_fan_out_fires_only_where_a_shown_group_repeats_the_measured_rows(database, sql, expected):
    assert fired(database, sql, "fan_out") is expected


def test_a_composite_key_is_counted_on_every_one_of_its_columns(tmp_path):
    """Keyed on the first two columns of a three-column key, two rows differing in the third count as one thing, and the check
    reports a fan-out that is not there: the sum is right and the retry would be false."""
    import sqlite3

    path = tmp_path / "quarters.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE region (id INTEGER PRIMARY KEY, name TEXT);"
                       "CREATE TABLE sales (region_id INT, year INT, quarter INT, amount REAL, PRIMARY KEY (region_id, year, quarter));"
                       "INSERT INTO region VALUES (1, 'North'), (2, 'South');"
                       "INSERT INTO sales VALUES (1, 2024, 1, 10.0), (1, 2024, 2, 20.0), (1, 2025, 1, 30.0), (2, 2024, 1, 40.0);")
    conn.commit()
    conn.close()
    db = ReadOnlyDatabase(path)
    sql = "SELECT r.name, SUM(s.amount) AS total FROM region r JOIN sales s ON s.region_id = r.id GROUP BY r.name"
    found = {p.check: p for p in sql_checks.probes(sql, db.execute(sql), db.execute)}["fan_out"]
    assert "s.region_id || ',' || s.year || ',' || s.quarter" in found.twin and not found.fired


def test_fan_out_reads_the_key_off_the_schema_and_falls_back_to_rowid():
    twin = probe("sakila", WRONG_KEY)["fan_out"].twin
    assert "COUNT(p.payment_id) AS __rows_p, COUNT(DISTINCT p.payment_id) AS __things_p" in twin
    keyless = probe("chinook", "SELECT a.Title, SUM(t.Milliseconds) FROM albums a JOIN tracks t ON t.AlbumId = a.AlbumId GROUP BY a.Title")["fan_out"]
    assert "COUNT(t.TrackId)" in keyless.twin and not keyless.fired


# --- 3. integer division ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("database,sql,expected", [
    ("sakila", "SELECT COUNT(*) * 100 / (SELECT COUNT(*) FROM film) AS pct FROM film WHERE rating = 'R'", True),        # 19 for 19.5
    ("sakila", "SELECT COUNT(*) / COUNT(DISTINCT actor_id) AS per_actor FROM film_actor", True),                        # 27 for 27.31
    ("sakila", "SELECT rating, SUM(length) / COUNT(*) AS avg_length FROM film GROUP BY rating", True),                  # SUM of an INT column over a count
    ("sakila", "SELECT 100.0 * COUNT(*) / (SELECT COUNT(*) FROM film) AS pct FROM film WHERE rating = 'R'", False),     # already a float
    ("sakila", "SELECT CAST(COUNT(*) AS REAL) / (SELECT COUNT(*) FROM film) AS pct FROM film WHERE rating = 'R'", False),
    ("sakila", "SELECT rating, AVG(length) / 60 AS hours FROM film GROUP BY rating", False),                            # AVG is real
    ("sakila", "SELECT rating, SUM(amount) / COUNT(*) FROM payment p JOIN staff s ON s.staff_id = p.staff_id JOIN film ON 1 = 0 GROUP BY rating", False),  # DECIMAL amount: not integer by type, silent rather than guessed
    ("sakila", "SELECT length / 30 * 30 AS band, COUNT(*) AS n FROM film GROUP BY band", False),                        # a bucket is a floor
    ("sakila", "SELECT title, length / 60 AS hours, length % 60 AS minutes FROM film LIMIT 5", False),                  # the modulo companion says floor
    ("sakila", "SELECT title, CAST(length / 60 AS INTEGER) AS hours FROM film LIMIT 5", False),                        # a cast to INTEGER says floor
    ("sakila", "SELECT film_id / 2 AS half, COUNT(*) FROM film GROUP BY film_id / 2 LIMIT 5", False),                  # grouped by the quotient itself
    ("sakila", "SELECT COUNT(*) / 5 AS fifths FROM film", False),                                                      # 1000 / 5 is exact: the twin agrees
    ("sakila", "SELECT COUNT(*) / CAST((SELECT COUNT(*) FROM film) AS REAL) AS pct FROM film WHERE rating = 'R'", False),  # the divisor is already real
    ("sakila", "SELECT SUM(length) / 60.0 AS hours FROM film", False),                                                  # a float literal divisor
])
def test_integer_division_fires_only_where_a_fraction_was_dropped_and_no_floor_was_asked_for(database, sql, expected):
    assert fired(database, sql, "integer_division") is expected


# --- 4. a whole day bounding instants ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("database,sql,expected", [
    ("sakila", "SELECT COUNT(*) FROM rental WHERE rental_date BETWEEN '2005-05-24' AND '2005-05-31'", True),
    ("sakila", "SELECT COUNT(*) FROM rental WHERE rental_date <= '2005-05-31'", True),
    ("sakila", "SELECT COUNT(*) FROM rental WHERE '2005-05-31' >= rental_date", True),                                 # the literal on the left
    ("sakila", "SELECT COUNT(*) FROM rental WHERE rental_date = '2005-05-31'", True),                                  # equality keeps only midnight
    ("sakila", "SELECT COUNT(*) FROM rental WHERE rental_date > '2005-05-31'", True),                                  # includes the day it means to exclude
    ("sakila", "SELECT COUNT(*) FROM rental WHERE rental_date < '2005-06-01'", False),                                 # already exclusive: not a member
    ("sakila", "SELECT COUNT(*) FROM rental WHERE date(rental_date) <= '2005-05-31'", False),                          # a function on the left is right
    ("sakila", "SELECT COUNT(*) FROM rental WHERE rental_date <= '2005-05-31 23:59:59'", False),                       # an instant, not a day
    ("chinook", "SELECT COUNT(*) FROM invoices WHERE InvoiceDate <= '2009-01-31'", False),                             # midnights only: the twin agrees
    ("northwind_small", "SELECT COUNT(*) FROM \"Order\" WHERE OrderDate <= '2012-07-31'", False),                            # dates without times
    ("sakila", "SELECT COUNT(*) FROM rental WHERE rental_date <= '2005-13-40'", False),                                # not a day at all
    ("northwind_small", "SELECT COUNT(*) FROM \"Order\" WHERE OrderDate BETWEEN '2012-07-01' AND '2012-07-31'", False),      # a column with no time part at all
    ("northwind_small", "SELECT COUNT(*) FROM \"Order\" WHERE OrderDate > '2012-07-31'", False),
    # chinook spells its midnights with a time, so a day bound does cut the day named: the rows differ and the check is right
    ("chinook", "SELECT COUNT(*) FROM invoices WHERE InvoiceDate = '2009-01-01'", True),
    ("chinook", "SELECT COUNT(*) FROM invoices WHERE InvoiceDate <= '2009-01-06'", True),
])
def test_a_day_bound_fires_only_where_the_column_holds_times_of_day(database, sql, expected):
    assert fired(database, sql, "date_bound") is expected


# --- 5. a filter that drops empties ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("database,sql,expected", [
    ("chinook", "SELECT Country, COUNT(*) FROM customers WHERE State != 'CA' GROUP BY Country", True),
    ("chinook", "SELECT Country, COUNT(*) FROM customers WHERE State <> 'CA' GROUP BY Country", True),
    ("chinook", "SELECT COUNT(*) FROM customers WHERE State NOT IN ('CA', 'NY')", True),
    ("chinook", "SELECT COUNT(*) FROM customers WHERE Company NOT LIKE '%Inc%'", True),                                # 49 customers have no company
    ("chinook", "SELECT Country, COUNT(*) FROM customers WHERE Country != 'USA' GROUP BY Country", False),             # no empty country
    ("chinook", "SELECT COUNT(*) FROM customers WHERE State != 'CA' AND State IS NOT NULL", False),                    # the SQL decided already
    ("chinook", "SELECT COUNT(*) FROM customers WHERE State IS NOT 'CA'", False),                                      # IS NOT keeps empties itself
    ("chinook", "SELECT COUNT(*) FROM customers WHERE Fax != ''", False),                                              # an emptiness filter is not a reading
    ("chinook", "SELECT COUNT(*) FROM customers c WHERE c.CustomerId NOT IN (SELECT CustomerId FROM invoices)", False),  # a subquery list is another fault
    ("chinook", "SELECT COUNT(*) FROM customers c JOIN employees e ON e.EmployeeId = c.SupportRepId WHERE c.State != e.State", False),  # column against column
    ("chinook", "SELECT COUNT(*) FROM customers WHERE State != 'CA' OR State IS NULL", False),                          # the empties are already kept
    ("northwind_small", "SELECT COUNT(*) FROM \"Order\" WHERE ShipRegion != 'RJ'", False),                                   # the column holds no empties
])
def test_a_negative_filter_fires_only_where_empties_were_dropped(database, sql, expected):
    assert fired(database, sql, "null_filter") is expected


# --- 6. numbers as text, sorted as text ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("database,sql,expected", [
    ("northwind_small", "SELECT LastName, Extension FROM Employee ORDER BY Extension DESC LIMIT 3", True),                   # '465' after '5176'
    ("northwind_small", "SELECT LastName, Extension FROM Employee ORDER BY 2 DESC LIMIT 3", True),                           # by ordinal
    ("northwind_small", "SELECT LastName, Extension AS ext FROM Employee ORDER BY ext DESC LIMIT 3", True),                  # by alias
    ("northwind_small", "SELECT LastName, Extension FROM Employee ORDER BY Extension", True),                                # no LIMIT: the order shown is still wrong
    ("sakila", "SELECT address, postal_code FROM address WHERE postal_code IS NOT NULL ORDER BY postal_code DESC LIMIT 5", False),  # equal width: same order
    ("sakila", "SELECT release_year, COUNT(*) FROM film GROUP BY release_year ORDER BY release_year", False),
    ("sakila", "SELECT title, length FROM film ORDER BY length DESC LIMIT 3", False),                                  # a number stored as a number
    ("sakila", "SELECT title FROM film ORDER BY title LIMIT 3", False),                                                # words are words
    ("sakila", "SELECT COUNT(*) FROM film ORDER BY rating", False),                                                    # the key is not projected
    ("northwind_small", "SELECT LastName, Extension FROM Employee ORDER BY CAST(Extension AS REAL) DESC LIMIT 3", False),    # already cast
    # five of the nine postal codes are numbers and four are British; the four shown are numbers, so the class is carried, but
    # the cast sorts 'EC2 7JR' as zero and pulls it into the answer, so the twin is not the same question and decides nothing
    ("northwind_small", "SELECT LastName, PostalCode FROM Employee WHERE PostalCode IS NOT NULL ORDER BY PostalCode LIMIT 4", False),
])
def test_a_text_sort_fires_only_where_the_numeric_order_differs(database, sql, expected):
    assert fired(database, sql, "text_sort") is expected


# --- the two flips, in the same shape ----------------------------------------------------------------------------------------

def test_the_grain_probe_reports_each_knob_and_reads_the_same_as_before():
    p = probe("sakila", "SELECT l.name, COUNT(f.film_id) AS films FROM language l LEFT JOIN film f ON f.language_id = l.language_id GROUP BY l.name")["grain"]
    assert p.fired and p.knobs == {"join": False, "count": True} and p.detail == "join"
    assert sql_checks.message(p).startswith("the answer depends on a decision the question has to settle: as written, the answer keeps rows with no match")


@pytest.mark.parametrize("sql", [
    "SELECT rating, COUNT(rating) AS n FROM film GROUP BY rating",   # the spelling the exclusion was written for
    "SELECT rating, COUNT(rating) AS n FROM film GROUP BY 1",        # by ordinal
    "SELECT f.rating AS r, COUNT(f.rating) AS n FROM film f GROUP BY r",  # by the projection's alias
    "SELECT rating, COUNT(f.rating) AS n FROM film f GROUP BY rating",    # qualified on one side only
])
def test_counting_the_grouped_column_is_a_tautology_however_the_group_by_spells_it(sql):
    """COUNT(DISTINCT g) grouped by g is 1 in every group, whichever way the GROUP BY names g, so there is no second reading
    and no retry: the answer as written is right."""
    assert not fired("sakila", sql, "grain")


def test_a_left_join_the_data_makes_inner_is_not_a_reading_either():
    assert not fired("sakila", "SELECT COUNT(*) AS n FROM film f LEFT JOIN language l ON l.language_id = f.language_id WHERE l.name = 'English'", "grain")
    assert fired("northwind_small", "SELECT m.LastName AS manager, COUNT(e.Id) AS reports FROM Employee m LEFT JOIN Employee e ON e.ReportsTo = m.Id GROUP BY m.Id", "grain")


def test_every_check_stays_out_of_a_statement_it_has_no_business_with():
    assert probe("sakila", "SELECT rating, COUNT(*) AS n FROM film GROUP BY rating") == {}
    assert sql_checks.probes("not sql at all", QueryResult(columns=[], rows=[]), lambda s: None) == []


def test_carries_reads_the_class_off_the_tree_alone():
    assert sql_checks.carries("null_filter", "SELECT 1 FROM t WHERE a != 'x'") and not sql_checks.carries("null_filter", "SELECT 1 FROM t WHERE a = 'x'")
    assert sql_checks.carries("date_bound", "SELECT 1 FROM t WHERE d <= '2005-05-31'") and not sql_checks.carries("date_bound", "SELECT 1 FROM t WHERE d < '2005-06-01'")
    assert sql_checks.carries("integer_division", "SELECT COUNT(a) / COUNT(*) FROM t") and not sql_checks.carries("integer_division", "SELECT 1.0 * COUNT(a) / COUNT(*) FROM t")
    assert sql_checks.carries("bare_column", "SELECT a, b, COUNT(*) FROM t GROUP BY a") and not sql_checks.carries("bare_column", "SELECT a, MAX(b) FROM t GROUP BY a")
    assert sql_checks.carries("fan_out", "SELECT SUM(a) FROM t JOIN u ON t.id = u.id") and not sql_checks.carries("fan_out", "SELECT SUM(a) FROM t")
    assert sql_checks.carries("text_sort", "SELECT a FROM t ORDER BY a") and not sql_checks.carries("text_sort", "SELECT a FROM t ORDER BY a || b")


# --- the document is the test data --------------------------------------------------------------------------------------------

FENCE = re.compile(r"^```(\w+)([^\n]*)\n(.*?)^```", re.S | re.M)


def document_cases() -> list[tuple[str, str, str, bool, str | None, str | None]]:
    """(database, check, sql, fires, twin, retry) for every marked query in tests/fixtures/sql-checks.md."""
    blocks = [(lang, info.split(), body.strip()) for lang, info, body in FENCE.findall(Path("tests/fixtures/sql-checks.md").read_text())]
    cases = []
    for lang, info, body in blocks:
        if lang == "sql" and len(info) == 3:
            cases.append([info[0], info[1], body, info[2] == "fires", None, None])
        elif info == ["twin"]:
            cases[-1][4] = body
        elif info == ["retry"]:
            cases[-1][5] = body
    return [tuple(c) for c in cases]


@pytest.mark.parametrize("database,check,sql,fires,twin,retry", document_cases(), ids=[f"{c[1]}-{'fires' if c[3] else 'silent'}-{i}" for i, c in enumerate(document_cases())])
def test_every_query_in_the_document_fires_or_stays_silent_as_it_claims(database, check, sql, fires, twin, retry):
    found = probe(database, sql).get(check)
    assert bool(found and found.fired) is fires
    if twin is not None:
        assert found is not None and found.twin == twin
    if retry is not None:
        assert found is not None and sql_checks.message(found) == retry


def test_the_document_demonstrates_all_eight_with_a_firing_and_a_silence_each():
    cases = document_cases()
    for check in sql_checks.CHECKS:
        assert any(c[1] == check and c[3] for c in cases) and any(c[1] == check and not c[3] for c in cases), check
    assert sum(1 for c in cases if c[1] == "grain" and c[3]) == 2  # the join flip and the count flip
