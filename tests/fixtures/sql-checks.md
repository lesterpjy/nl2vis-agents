# The eight SQL checks, demonstrated

Eight mechanical checks run once per Turn on the Analysis Agent's final SQL (`data_agents/agents/sql_checks.py`, called from
`finish_analysis`). Each names a fault that is **silent**: the SQL is valid, it returns rows, the chart is clean, and the number is
wrong. Each is decided from the SQL text, the parse tree or the result, and where the decision needs the data it is bought from
SQLite by a **one-edit rewrite of the parse tree** (the *twin*) executed through the same read-only handle and compared to the
original. No model judges anything; a retry fires only when the two derivations disagree, and a twin that does not run decides
nothing. Every SQL below was executed on the registered sakila, chinook and northwind_small databases through the read-only handle in
the session that wrote this file, and `tests/test_sql_checks.py` loads this file's queries and asserts that each fires or stays
silent as claimed, that the twin printed is the twin the code runs, and that the retry printed is the retry the model receives.

How to read a section: the fault in two sentences; a real question whose natural SQL commits it; that SQL, marked with the
database, the check and `fires`; the rewrite the check runs (`twin`); the two results with the numbers the databases returned;
what the retry says to the model, verbatim (`retry`); and one query where the check correctly stays silent. The retry's first
rung is printed; the second rung, where a check has one, is the same problem followed by the ladder's fixed escalation
(`analysis.ESCALATE`), which tells the model it may not finish with that answer and must ask the user for the decision, never for
SQL.

Rungs on the ladder (`analysis.LADDER`): **one** where the first message already names a legitimate way to finish with the same
answer, because the two derivations are two *readings* the question has to settle; **two** where it names a *defect*.

| check | class | decided by | rungs | counted over the dev slice (below) |
|---|---|---|---|---|
| `grain` (join flip) | an outer join that keeps rows with no match | twin: every outer join made inner; rows compared | 1 | 23 + 15 + 17 Turns carried a knob, fired 0 + 6 + 4 |
| `grain` (count flip) | a COUNT that could be COUNT(DISTINCT) | twin: the COUNT swapped; rows compared | 1 | (with the join flip above) |
| `bare_column` | a selected column neither aggregated nor grouped | twin: distinct values per group counted; more than one anywhere | 2 | 2 carried, 0 fired |
| `fan_out` | a SUM or AVG over a join that repeats the measured rows | twin: rows and distinct keys of the measured table per group | 1 | 24 carried, 23 ran, 4 fired (1 wrong of 3 scorable) |
| `integer_division` | two integers divided, fraction dropped | twin: numerator cast to REAL; rows compared | 1 | 0 carried |
| `date_bound` | a whole day bounding a column of instants | twin: bound moved to the next day, exclusive; rows compared | 2 | 0 carried |
| `null_filter` | `!=`, `NOT IN`, `NOT LIKE` dropping the rows with no value | twin: `OR col IS NULL`; rows compared | 1 | 11 carried, 0 fired |
| `text_sort` | numbers stored as text ordered as text | twin: `CAST(key AS REAL)` in the ORDER BY; key order compared | 2 | 28 ran, 0 fired |

---

## 1. The join flip (`grain`)

**The fault.** An outer join keeps the rows of the left table that have no match, as zeros or blanks; an inner join drops them.
Whether "films per language" includes the five languages with no films is a decision the question has to settle, and the SQL
as written has taken one side silently.

**Question (sakila):** *How many films are there per language?*

```sql sakila grain fires
SELECT l.name AS language, COUNT(f.film_id) AS films FROM language l LEFT JOIN film f ON f.language_id = l.language_id GROUP BY l.name
```

**The twin:** every outer join made inner.

```sql twin
SELECT l.name AS language, COUNT(f.film_id) AS films FROM language AS l JOIN film AS f ON f.language_id = l.language_id GROUP BY l.name
```

| as written (6 rows) | | the twin (1 row) | |
|---|---|---|---|
| English | 1000 | English | 1000 |
| French | 0 | | |
| German | 0 | | |
| Italian | 0 | | |
| Japanese | 0 | | |
| Mandarin | 0 | | |

**The retry, verbatim:**

```text retry
the answer depends on a decision the question has to settle: as written, the answer keeps rows with no match, as zeros or blanks: 6 rows (e.g. ['French', 0], ['German', 0], ['Italian', 0]); dropping them instead returns 1 row (e.g. ['English', 1000]). First look for the deciding word in the question itself: one that asks for distinct or unique things, for every row or occurrence, for all of something including those with none, or only for those that match. If it is there the question has decided: finish with the SQL that takes that reading and say the decision in the narrative, quoting the word. Ask a Clarification, naming both readings in these words, only when no word in the question settles it.
```

**Stays silent.** An anti-join is an outer join by construction: made inner, the twin returns no rows, which is not a reading
the question could mean, so nothing is said. *Which films are not in stock anywhere?* (42 rows.)

```sql sakila grain silent
SELECT f.title FROM film f LEFT JOIN inventory i ON i.film_id = f.film_id WHERE i.inventory_id IS NULL ORDER BY f.title
```

## 2. The count flip (`grain`)

**The fault.** `COUNT(x)` counts rows and `COUNT(DISTINCT x)` counts things; over a join that repeats rows the two differ, and
"how many genres does each playlist span" wants things while "how many tracks" wants rows. The SQL has taken a side.

**Question (chinook):** *How many genres does each playlist span?*

```sql chinook grain fires
SELECT p.Name AS playlist, COUNT(t.GenreId) AS genres FROM playlists p JOIN playlist_track pt ON pt.PlaylistId = p.PlaylistId JOIN tracks t ON t.TrackId = pt.TrackId GROUP BY p.Name ORDER BY genres DESC
```

```sql twin
SELECT p.Name AS playlist, COUNT(DISTINCT t.GenreId) AS genres FROM playlists AS p JOIN playlist_track AS pt ON pt.PlaylistId = p.PlaylistId JOIN tracks AS t ON t.TrackId = pt.TrackId GROUP BY p.Name ORDER BY genres DESC
```

| playlist | as written | the twin |
|---|---|---|
| Music | 6580 | 20 |
| 90’s Music | 1477 | 16 |
| TV Shows | 426 | 5 |
| … (12 rows) | | |

```text retry
the answer depends on a decision the question has to settle: as written, the answer counts every row: 12 rows (e.g. ['Music', 6580], ['90’s Music', 1477], ['TV Shows', 426]); counting each value once instead returns 12 rows (e.g. ['Music', 20], ['90’s Music', 16], ['TV Shows', 5]). First look for the deciding word in the question itself: one that asks for distinct or unique things, for every row or occurrence, for all of something including those with none, or only for those that match. If it is there the question has decided: finish with the SQL that takes that reading and say the decision in the narrative, quoting the word. Ask a Clarification, naming both readings in these words, only when no word in the question settles it.
```

**Stays silent.** Every film has one `film_id`, so `COUNT(DISTINCT film_id)` and `COUNT(film_id)` agree and the knob was a no-op.

```sql sakila grain silent
SELECT rating, COUNT(DISTINCT film_id) AS films FROM film GROUP BY rating ORDER BY films DESC
```

## 3. A bare column beside an aggregate (`bare_column`)

**The fault.** A selected column that is neither aggregated nor in the GROUP BY is filled by SQLite from *an arbitrary row of
each group* (SQLite's documentation, "Bare columns in an aggregate query"); the value shown is one of several and may change
between runs. The proposal was to decide this from the tree alone, with no rewrite. That has a non-empty false-retry list, and
the rewrite is what closes it: the idiomatic `GROUP BY a.actor_id` with `first_name || ' ' || last_name` beside it is bare by
the tree and right in every SQLite result, because the name is determined by the key. So the twin appends, per bare projection,
`COUNT(DISTINCT expr) + (COUNT(*) - COUNT(expr) > 0)`, the number of distinct values including NULL inside each group of the
answer, with the ORDER BY and LIMIT kept so no group outside the answer is judged. One everywhere: the value was determined and
nothing is said. More than one in any group shown: the number is arbitrary. The one case SQLite itself defines, a query whose
single aggregate is `MIN` or `MAX` (the bare columns come from the extreme row), is excluded structurally and never probed.

**Question (chinook):** *For each country, how many customers are there and which city are they in?*

```sql chinook bare_column fires
SELECT Country, City, COUNT(*) AS customers FROM customers GROUP BY Country ORDER BY customers DESC
```

```sql twin
SELECT Country, City, COUNT(*) AS customers, COUNT(DISTINCT City) + (COUNT(*) - COUNT(City) > 0) AS __bare_1 FROM customers GROUP BY Country ORDER BY customers DESC
```

| Country | City as written | customers | distinct cities in the group (twin) |
|---|---|---|---|
| USA | Mountain View | 13 | 12 |
| Canada | Montréal | 8 | 8 |
| France | Paris | 5 | 4 |
| Brazil | São José dos Campos | 5 | 4 |
| Germany | Stuttgart | 4 | 3 |
| United Kingdom | London | 3 | 2 |
| … (24 rows; 8 groups hold more than one city) | | | |

```text retry
City in that SQL is neither aggregated nor in the GROUP BY, so SQLite fills it from an arbitrary row of each group, and in 8 of the 24 groups shown the group holds more than one value (e.g. ['USA', 'Mountain View', 13] holds 12). Either add the column to the GROUP BY, if each of its values deserves a row of its own, or replace it with the aggregate that names the value the question wants (MAX, MIN, GROUP_CONCAT), or drop it.
```

**Stays silent, by the data.** *Which five actors appear in the most films?* The name is bare by the tree and determined by the
key: the twin's count is 1 in every group.

```sql sakila bare_column silent
SELECT a.first_name || ' ' || a.last_name AS actor, COUNT(*) AS films FROM actor a JOIN film_actor fa ON fa.actor_id = a.actor_id GROUP BY a.actor_id ORDER BY films DESC LIMIT 5
```

```sql twin
SELECT a.first_name || ' ' || a.last_name AS actor, COUNT(*) AS films, COUNT(DISTINCT a.first_name || ' ' || a.last_name) + (COUNT(*) - COUNT(a.first_name || ' ' || a.last_name) > 0) AS __bare_0 FROM actor AS a JOIN film_actor AS fa ON fa.actor_id = a.actor_id GROUP BY a.actor_id ORDER BY films DESC LIMIT 5
```

**Stays silent, by SQLite's rule.** *What is the longest film in each category?* One `MAX`, so `title` comes from the longest
film's row by definition; the class is absent and no twin runs.

```sql sakila bare_column silent
SELECT c.name AS category, f.title, MAX(f.length) AS longest FROM film f JOIN film_category fc ON fc.film_id = f.film_id JOIN category c ON c.category_id = fc.category_id GROUP BY c.name ORDER BY longest DESC
```

## 4. Fan-out inflating a sum or an average (`fan_out`)

**The fault.** A join that repeats the measured table's rows makes `SUM` count some of them more than once and reweights `AVG`;
joining `payment` on `customer_id` instead of `rental_id` repeats every payment once per rental of that customer, and the revenue
per category comes out 26 times too large. `COUNT` is not this check's: an inflated `COUNT(x)` is exactly what the count flip
already catches, and firing twice on one defect would be two retries for one fault.

**What is decidable and what is not.** Whether the measured table's rows are repeated inside a group of the answer is decidable:
the twin appends `COUNT(key)` and `COUNT(DISTINCT key)` of the measured table (its declared primary key; `rowid` where none is
declared; the columns joined with a comma for a composite key) under the same FROM, WHERE, GROUP BY, ORDER BY and LIMIT, and a
group with more rows than keys is the fan-out. Whether that repetition is *wrong* is not decidable: a dimension attribute summed
once per fact row (`SUM(f.rental_rate)` over rentals, an "average length of the films rented" taken per rental) is a reading the
question can mean, and on the dev slice VisEval's own gold took that reading in 2 of the 3 scorable firings. So the
proposed form, a defect retry that names the multiplying table and asks for the aggregate to be taken before the join, would
have fired falsely on correct answers and escalated them to a Clarification; the check ships instead in the grain probe's shape,
one rung, both readings in words, and the model decides by the question's words or asks. The multiplying table is not named,
because finding it needs one execution per join; the repeated table and the two counts are.

**Question (sakila):** *What is the total revenue per film category?* written with `payment` joined on the customer.

```sql sakila fan_out fires
SELECT c.name AS category, SUM(p.amount) AS revenue FROM category c JOIN film_category fc ON fc.category_id = c.category_id JOIN film f ON f.film_id = fc.film_id JOIN inventory i ON i.film_id = f.film_id JOIN rental r ON r.inventory_id = i.inventory_id JOIN payment p ON p.customer_id = r.customer_id GROUP BY c.name ORDER BY revenue DESC
```

```sql twin
SELECT c.name AS category, SUM(p.amount) AS revenue, COUNT(p.payment_id) AS __rows_p, COUNT(DISTINCT p.payment_id) AS __things_p FROM category AS c JOIN film_category AS fc ON fc.category_id = c.category_id JOIN film AS f ON f.film_id = fc.film_id JOIN inventory AS i ON i.film_id = f.film_id JOIN rental AS r ON r.inventory_id = i.inventory_id JOIN payment AS p ON p.customer_id = r.customer_id GROUP BY c.name ORDER BY revenue DESC
```

| category | revenue as written | payment rows (twin) | distinct payments (twin) |
|---|---|---|---|
| Sports | 138295.47 | 32653 | 14027 |
| Animation | 137116.48 | 32652 | 13562 |
| Action | 130684.74 | 31026 | 13851 |
| Family | 129048.71 | 30629 | 13562 |
| … (16 rows, all 16 repeated) | | | |

```text retry
the joins repeat rows of payment, so the sum or average over it counts some of them more than once: in 16 of the 16 groups shown (e.g. ['Sports', 138295.47]: 32653 rows over 14027 distinct payment rows). If each payment row should count once, aggregate payment before joining it to the rest, or join on the key that matches it to one row. If once per joined row is what the question means, finish again with the same SQL and say so in the narrative.
```

**Stays silent.** The same question joined on `rental_id`, which is the tier 2 tape's own final SQL: 1179 payment rows and 1179
distinct payments in Sports, revenue 5314.21, and so on down every group.

```sql sakila fan_out silent
SELECT c.name AS category, SUM(p.amount) AS revenue FROM payment p JOIN rental r ON p.rental_id = r.rental_id JOIN inventory i ON r.inventory_id = i.inventory_id JOIN film f ON i.film_id = f.film_id JOIN film_category fc ON f.film_id = fc.film_id JOIN category c ON fc.category_id = c.category_id GROUP BY c.name ORDER BY revenue DESC
```

## 5. Integer division (`integer_division`)

**The fault.** SQLite divides two integers as integers and drops the fraction, so a share of 19.5% is shown as 19 and an average
of 27.31 as 27. The prompt sentence that hoped the model would write `100.0 * COUNT(x) / COUNT(*)` is deleted, the way the join
rule was deleted for the grain check, and the check in code takes its place.

**What is decidable.** An operand is integer-typed by construction when it is an integer literal, a `COUNT` or `LENGTH`, a
`SUM`, `MIN` or `MAX` of one, an arithmetic of them, a scalar subquery of one, or a column whose declared type has INTEGER
affinity; anything else (a `DECIMAL` or `REAL` column, `AVG`, a float literal, a cast to `REAL`, a text) is not, and the check
stays silent rather than guess. The twin casts each such division's numerator to `REAL`; the same rows mean every division was
exact and nothing is said. A floor the SQL asks for is excluded structurally, because there the truncation is the point: a
quotient the query groups by (a length band), `x / k * k`, a `%` by the same divisor beside it, and a cast of the quotient to an
integer type. What remains is one reading (whole units meant for display), so the check has one rung and the retry names the
exit.

**Question (sakila):** *What percentage of films are rated R?*

```sql sakila integer_division fires
SELECT COUNT(*) * 100 / (SELECT COUNT(*) FROM film) AS pct_r FROM film WHERE rating = 'R'
```

```sql twin
SELECT CAST(COUNT(*) * 100 AS REAL) / (SELECT COUNT(*) FROM film) AS pct_r FROM film WHERE rating = 'R'
```

| as written | the twin |
|---|---|
| 19 | 19.5 |

```text retry
COUNT(*) * 100 / (SELECT COUNT(*) FROM film) divides two integers, and SQLite drops the fraction, so the numbers shown are truncated: as written 1 row (e.g. [19]); dividing exactly returns 1 row (e.g. [19.5]). If the question asks for a ratio, a share or an average, divide over a float (CAST(x AS REAL) / y, or 100.0 * x / y). If whole units are the point, finish again with the same SQL and say so in the narrative.
```

**Stays silent.** *How are films distributed by half-hour of length?* The quotient is a bucket the query groups by and is
multiplied back by its divisor: a floor by construction, so the class is absent and no twin runs.

```sql sakila integer_division silent
SELECT length / 30 * 30 AS band, COUNT(*) AS films FROM film GROUP BY band ORDER BY band
```

## 6. A whole day bounding a column of instants (`date_bound`)

**The fault.** `rental_date <= '2005-05-31'` compares `'2005-05-31 22:53:30'` with `'2005-05-31'` as text, and the longer string
sorts after the shorter one, so only the instants at midnight of 31 May survive and the rest of the day is dropped; the same
happens to `=`, to `>` (which includes the day it means to exclude) and to the upper bound of a `BETWEEN`. The twin moves each
such bound to the next day, exclusive (`< '2005-06-01'`, `>= '2005-06-01'`, or a `>= low AND < next` pair), and on a column of
dates or of midnights the rows are the same and nothing is said. Only a column compared with a literal of the shape
`YYYY-MM-DD` inside a WHERE is a member; `date(col) <= …` and `strftime(...)` on the left are not, and are already right.
Two rungs: a day cut at its midnight is a defect, and the escalation's question ("is 31 May included?") is one a user can answer.

**Question (sakila):** *How many rentals were made from 24 to 31 May 2005?*

```sql sakila date_bound fires
SELECT COUNT(*) AS rentals FROM rental WHERE rental_date BETWEEN '2005-05-24' AND '2005-05-31'
```

```sql twin
SELECT COUNT(*) AS rentals FROM rental WHERE (rental_date >= '2005-05-24' AND rental_date < '2005-06-01')
```

| as written | the twin |
|---|---|
| 993 | 1156 |

```text retry
the bound on rental_date compares instants with a whole day (2005-05-31), and the column holds times of day, so the day named is cut at its midnight: as written 1 row (e.g. [993]); taking whole days returns 1 row (e.g. [1156]). Bound a timestamp with the next day, exclusive (col < '2005-06-01'), or compare date(col) with the day.
```

**Stays silent, by construction.** Northwind's `OrderDate` holds days and no times, so no bound on it can cut one.

```sql northwind_small date_bound silent
SELECT COUNT(*) AS orders FROM "Order" WHERE OrderDate BETWEEN '2012-07-01' AND '2012-07-31'
```

**Stays silent, by the data.** Chinook spells its midnights with a time, so the bound below *would* cut 31 January — there is no
invoice on it, so the twin returns the same 6 invoices and nothing is said. Move the bound to a day that holds one and the same
check fires, which is the difference between a column that cannot be cut and a query that happens not to cut anything.

```sql chinook date_bound silent
SELECT COUNT(*) AS invoices FROM invoices WHERE InvoiceDate <= '2009-01-31'
```

```sql twin
SELECT COUNT(*) AS invoices FROM invoices WHERE InvoiceDate < '2009-02-01'
```

## 7. A filter that drops the rows with no value (`null_filter`)

**The fault.** `State != 'CA'` is NULL, not true, for a customer with no state on file, so the filter drops those rows; whether
"customers outside California" includes the 29 customers whose state is unknown is a reading the question has to settle. The
twin keeps them (`OR col IS NULL`); the same rows mean the column holds none, or the SQL already decided (`AND col IS NOT NULL`
makes the twin collapse to the original). Members: `!=` and `<>`, `NOT IN` over a literal list, `NOT LIKE`, each on a column
against values holding no column; a comparison with the empty string is an emptiness filter and is excluded. One rung, both
readings in words, exactly as the join and count flips do.

**Question (chinook):** *How many customers per country are outside California?*

```sql chinook null_filter fires
SELECT Country, COUNT(*) AS customers FROM customers WHERE State != 'CA' GROUP BY Country ORDER BY customers DESC
```

```sql twin
SELECT Country, COUNT(*) AS customers FROM customers WHERE (State <> 'CA' OR State IS NULL) GROUP BY Country ORDER BY customers DESC
```

| as written (7 rows) | | the twin (24 rows) | |
|---|---|---|---|
| USA | 10 | USA | 10 |
| Canada | 8 | Canada | 8 |
| Brazil | 5 | France | 5 |
| Netherlands | 1 | Brazil | 5 |
| Italy | 1 | Germany | 4 |
| Ireland | 1 | United Kingdom | 3 |
| … | | … | |

```text retry
the filter on State drops the rows where it is empty (NULL), which the question may mean to keep: as written 7 rows (e.g. ['USA', 10], ['Canada', 8], ['Brazil', 5]); keeping them returns 24 rows (e.g. ['USA', 10], ['Canada', 8], ['France', 5]). If the question means everything except the value named, keep the empties (OR col IS NULL). If it means only rows that have a value, finish again with the same SQL and say so in the narrative.
```

**Stays silent.** Every customer has a country, so the twin returns the same 23 rows.

```sql chinook null_filter silent
SELECT Country, COUNT(*) AS customers FROM customers WHERE Country != 'USA' GROUP BY Country ORDER BY customers DESC
```

## 8. Numbers stored as text, sorted as text (`text_sort`)

**The fault.** A text column holding numbers sorts alphabetically, so `'465'` comes after `'5176'` and a top-3 is the wrong
three. Decidable from the result: the ORDER BY names a projected column (by name, alias or ordinal) whose values in the answer
are all numbers, at least one of them stored as text. The twin sorts on `CAST(key AS REAL)` and the *sequence of the key's
values* is compared, not the rows, so tied keys in either order are the same answer. The answer is a window on the column and
the cast is not: where the twin's own key values are not all numbers, the cast has pulled a word in from outside the answer and
sorted it as zero, which is a different question, so the twin decides nothing. Two rungs: no question wants numbers in
alphabetical order.

**Question (northwind_small):** *Which three employees have the highest extensions?*

```sql northwind_small text_sort fires
SELECT LastName, Extension FROM Employee ORDER BY Extension DESC LIMIT 3
```

```sql twin
SELECT LastName, Extension FROM Employee ORDER BY CAST(Extension AS REAL) DESC LIMIT 3
```

| as written | | the twin | |
|---|---|---|---|
| Davolio | 5467 | Davolio | 5467 |
| Peacock | 5176 | Peacock | 5176 |
| King | 465 | Fuller | 3457 |

```text retry
ORDER BY Extension sorts text, and every value of it is a number, so '9' sorts after '10': as written the order is ['5467', '5176', '465']; sorted as numbers it is ['5467', '5176', '3457']. Sort on CAST(col AS REAL), and check the LIMIT again: the top rows are different.
```

**Stays silent.** The five sakila postal codes this answer shows are all five digits wide, so their alphabetical order is their
numeric order and the twin's key sequence is the same. (Not every postal code is: 65 of them are one to four digits, which is
why the check compares the answer's own key sequence and not the column.)

```sql sakila text_sort silent
SELECT address, postal_code FROM address WHERE postal_code IS NOT NULL ORDER BY postal_code DESC LIMIT 5
```

```sql twin
SELECT address, postal_code FROM address WHERE NOT postal_code IS NULL ORDER BY CAST(postal_code AS REAL) DESC LIMIT 5
```

**Stays silent, because the twin is not the same question.** Five of Northwind's nine employee postal codes are numbers and four
are British. The four this answer shows are numbers, so the class is carried — but the cast reads `EC2 7JR` as zero and pulls it
into the answer, so the twin sorts a column the question never asked about and decides nothing.

```sql northwind_small text_sort silent
SELECT LastName, PostalCode FROM Employee WHERE PostalCode IS NOT NULL ORDER BY PostalCode LIMIT 4
```

---

## Counted over the stored dev runs, $0

Every final SQL of three stored Dev Slice runs re-executed on its benchmark database with the eight probes, no model call
(`dev-250-grain`, 217 Turns with a final SQL; `joins-100`, 98; `schema-100`, 100). "Carries the class" is the population read
off the tree; "twin ran" the Turns where a probe executed; "fired" where it disagreed; "wrong" of those, under `correct`.

| check | carries the class | twin ran | fired | fired and wrong |
|---|---|---|---|---|
| `grain` | 23 / 15 / 17 | 23 / 15 / 17 | 0 / 6 / 4 | 0 / 5 / 2 |
| `bare_column` | 1 / 1 / 0 | 1 / 1 / 0 | 0 | |
| `fan_out` | 15 / 5 / 4 | 14 / 5 / 4 | 4 / 0 / 0 | 1 of 3 scorable |
| `integer_division` | 0 / 0 / 0 | | | |
| `date_bound` | 0 / 0 / 0 | | | |
| `null_filter` | 5 / 3 / 3 | 5 / 3 / 3 | 0 | |
| `text_sort` | 157 / 62 / 63 | 14 / 6 / 8 | 0 | |

Read: `grain` on `dev-250-grain` fired 0 because that run's stored final SQL is the answer *after* the probe's retry. The two
`bare_column` statements were the determined kind (a key grouped, its name beside it) and the data closed them; a third that the
first count held is now excluded off the tree, because the column it showed *is* the grouped one, written once qualified and
once not. `fan_out`'s
four firings are two weighted sums VisEval's gold also takes (`department.Num_Employees` once per management row,
`Manufacturers.Revenue` once per product), one `AVG` over repeated members that was wrong, and one unscorable; with one rung the
two correct answers cost one request each and stand. `integer_division` and `date_bound` have no member among 415 final
statements, which is the denominator and not the verdict: the prompt sentence that is now deleted was in
force on every one of them. `text_sort` ran 28 twins on numeric text keys (years as text, mostly) and every one sorted the same.
The tier 2 tapes: no probe fires on any of the twelve final statements, so every tape replays unchanged and the median requests
per Turn stays 3.
