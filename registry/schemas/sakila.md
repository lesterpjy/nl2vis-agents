# sakila
DVD rental store: films, actors, customers, rentals, payments.

## Gotchas
- Names and titles are stored in UPPERCASE (actor and customer first_name/last_name, film.title); category.name, language.name and city/country are Title Case. Filter with the stored spelling or LIKE: SQLite = is case-sensitive.
- last_update is a 2020 load timestamp on every row and carries no business meaning; use rental_date, return_date, payment_date, create_date for time questions.
- Money columns (rental_rate, replacement_cost, payment.amount) hold real numbers. Revenue = SUM(payment.amount); rental counts come from rental.
- Timestamps are stored as text like '2005-05-25 11:30:37.000'; use strftime('%Y-%m', col) for month buckets. Data covers 2005-2006.
- Payments are tracked in the 'payment' table (for revenue), and 'sales_by_film_category'/'sales_by_store' aggregate these.
- Many tables link by IDs only (e.g. customer.address_id); joins needed for readable outputs.
- Some FKs may allow NULL (e.g. film.original_language_id, address2, postal_code, picture, password); check for NULLs in reports.

## Values
Stored spellings of small text columns; a filter must match them exactly (SQLite = is case-sensitive).
- category.name: Action, Animation, Children, Classics, Comedy, Documentary, Drama, Family, Foreign, Games, Horror, Music, New, Sci-Fi, Sports, Travel
- customer.active: 0, 1
- film.rating: G, NC-17, PG, PG-13, R
- language.name: English, French, German, Italian, Japanese, Mandarin

## Tables
### actor (table, 200 rows)
Each row is one actor, with name fields and last_update for tracking changes.
- `actor_id` numeric PK — Unique actor identifier; e.g. 1, 2, 3
- `first_name` VARCHAR(45) — Actor's first name; e.g. ADAM, AL, ALAN
- `last_name` VARCHAR(45) — Actor's last name; e.g. AKROYD, ALLEN, ASTAIRE
- `last_update` TIMESTAMP — Last time actor row was updated; e.g. 2020-12-23 07:12:29

### address (table, 603 rows)
Each row is a physical address, linked to a city; used by customers, staff, and stores.
- `address_id` INT PK — Unique address identifier; e.g. 1, 2, 3
- `address` VARCHAR(50) — Street address and house number; e.g. 1 Valle de Santiago Avenue, 1001 Miyakonojo Lane, 1002 Ahmadnagar Manor
- `address2` VARCHAR(50) — Second address line, unused
- `district` VARCHAR(20) — Administrative district, part of address
- `city_id` INT — Related city identifier; e.g. 1, 2, 3
- `postal_code` VARCHAR(10) — Postal code, may be NULL; e.g. 1027, 10417, 10428
- `phone` VARCHAR(20) — Phone number for address
- `last_update` TIMESTAMP — Last time address row was updated; e.g. 2020-12-23 07:12:21
FK: city_id -> city.city_id

### category (table, 16 rows)
Each row is a film genre/category.
- `category_id` SMALLINT PK — Unique category identifier; e.g. 1, 2, 3
- `name` VARCHAR(25) — Film genre (see Values)
- `last_update` TIMESTAMP — Last time category row was updated; e.g. 2020-12-23 07:12:31

### city (table, 600 rows)
Each row is a city, linked to a country.
- `city_id` INT PK — Unique city identifier; e.g. 1, 2, 3
- `city` VARCHAR(50) — City name; e.g. A Corua (La Corua), Abha, Abu Dhabi
- `country_id` SMALLINT — Country identifier for city; e.g. 1, 2, 3
- `last_update` TIMESTAMP — Last time city row was updated; e.g. 2020-12-23 07:12:14
FK: country_id -> country.country_id

### country (table, 109 rows)
Each row is a country.
- `country_id` SMALLINT PK — Unique country identifier; e.g. 1, 2, 3
- `country` VARCHAR(50) — Country name; e.g. Afghanistan, Algeria, American Samoa
- `last_update` TIMESTAMP — Last time country row was updated; e.g. 2020-12-23 07:12:12

### customer (table, 599 rows)
Each row is a customer, with contact info, store, and address references.
- `customer_id` INT PK — Unique customer identifier; e.g. 1, 2, 3
- `store_id` INT — Store where customer is registered; e.g. 1, 2
- `first_name` VARCHAR(45) — Customer's first name; e.g. AARON, ADAM, ADRIAN
- `last_name` VARCHAR(45) — Customer's last name; e.g. ABNEY, ADAM, ADAMS
- `email` VARCHAR(50) — Customer's email address; e.g. AARON.SELBY@sakilacustomer.org, ADAM.GOOCH@sakilacustomer.org, ADRIAN.CLARY@sakilacustomer.org
- `address_id` INT — Customer's address identifier; e.g. 5, 6, 7
- `active` CHAR(1) — Customer active flag: 1 active, 0 not (see Values)
- `create_date` TIMESTAMP — Account creation timestamp; e.g. 2006-02-14 22:04:36.000
- `last_update` TIMESTAMP — Last time customer row was updated; e.g. 2020-12-23 07:15:11
FK: address_id -> address.address_id; store_id -> store.store_id

### film (table, 1000 rows)
Each row is a film, with details, pricing, and language information.
- `film_id` INT PK — Unique film identifier; e.g. 1, 2, 3
- `title` VARCHAR(255) — Film title; e.g. ACADEMY DINOSAUR, ACE GOLDFINGER, ADAPTATION HOLES
- `description` BLOB SUB_TYPE TEXT — Film description text
- `release_year` VARCHAR(4) — Film release year; e.g. 2006
- `language_id` SMALLINT — Film's language identifier; e.g. 1
- `original_language_id` SMALLINT — Original language. Always NULL
- `rental_duration` SMALLINT — Rental period in days; e.g. 3, 4, 5
- `rental_rate` DECIMAL(4,2) — Film rental price; e.g. 0.99, 2.99, 4.99
- `length` SMALLINT — Film length in minutes; e.g. 46, 47, 48
- `replacement_cost` DECIMAL(5,2) — Cost to replace lost film; e.g. 9.99, 10.99, 11.99
- `rating` VARCHAR(10) — MPAA rating (see Values)
- `special_features` VARCHAR(100) — Special features description; e.g. Behind the Scenes, Commentaries, Deleted Scenes
- `last_update` TIMESTAMP — Last time film row was updated; e.g. 2020-12-23 07:12:31
FK: original_language_id -> language.language_id; language_id -> language.language_id

### film_actor (table, 5462 rows)
Many-to-many link table: one row per actor-film appearance.
- `actor_id` INT PK — Linked actor identifier; e.g. 1, 2, 3
- `film_id` INT PK — Linked film identifier; e.g. 1, 2, 3
- `last_update` TIMESTAMP — Last time relation row was updated; e.g. 2020-12-23 07:13:43
FK: film_id -> film.film_id; actor_id -> actor.actor_id

### film_category (table, 1000 rows)
Link table assigning films to categories.
- `film_id` INT PK — Film identifier; e.g. 1, 2, 3
- `category_id` SMALLINT PK — Category identifier; e.g. 1, 2, 3
- `last_update` TIMESTAMP — Last time relation row was updated; e.g. 2020-12-23 07:14:58
FK: category_id -> category.category_id; film_id -> film.film_id

### film_text (table, 0 rows)
Text-only info for films (empty in sample).
- `film_id` SMALLINT PK — Film identifier
- `title` VARCHAR(255) — Film title
- `description` BLOB SUB_TYPE TEXT — Film description

### inventory (table, 4581 rows)
Each row is a physical film copy, located at a store.
- `inventory_id` INT PK — Unique copy identifier; e.g. 1, 2, 3
- `film_id` INT — Film identifier for this copy; e.g. 1, 2, 3
- `store_id` INT — Store where copy is located; e.g. 1, 2
- `last_update` TIMESTAMP — Last time inventory row was updated; e.g. 2020-12-23 07:12:45
FK: film_id -> film.film_id; store_id -> store.store_id

### language (table, 6 rows)
Each row is a film language.
- `language_id` SMALLINT PK — Unique language identifier; e.g. 1, 2, 3
- `name` CHAR(20) — Language name (see Values)
- `last_update` TIMESTAMP — Last time language row was updated; e.g. 2020-12-23 07:12:12

### payment (table, 16049 rows)
Each row is a payment made on a rental, giving the amount and customer/staff info.
- `payment_id` INT PK — Unique payment identifier; e.g. 1, 2, 3
- `customer_id` INT — Customer making payment; e.g. 1, 2, 3
- `staff_id` SMALLINT — Staff receiving payment; e.g. 1, 2
- `rental_id` INT — Rental related to payment. Nullable; e.g. 1, 2, 3
- `amount` DECIMAL(5,2) — Payment amount; e.g. 0, 0.99, 1.98
- `payment_date` TIMESTAMP — Payment transaction timestamp; e.g. 2005-05-24 22:53:30.000
- `last_update` TIMESTAMP — Last time payment row was updated; e.g. 2020-12-23 07:19:10
FK: staff_id -> staff.staff_id; customer_id -> customer.customer_id; rental_id -> rental.rental_id

### rental (table, 16044 rows)
Each row is a film being rented and returned; connects inventory, customer, staff.
- `rental_id` INT PK — Unique rental identifier; e.g. 1, 2, 3
- `rental_date` TIMESTAMP — Date and time rental began; e.g. 2005-05-24 22:53:30.000
- `inventory_id` INT — Copy identifier rented; e.g. 1, 2, 3
- `customer_id` INT — Customer renting the film; e.g. 1, 2, 3
- `return_date` TIMESTAMP — Return time. NULL if not returned; e.g. 2005-05-25 23:55:21.000
- `staff_id` SMALLINT — Staff processing rental; e.g. 1, 2
- `last_update` TIMESTAMP — Last time rental row was updated; e.g. 2020-12-23 07:15:20
FK: customer_id -> customer.customer_id; inventory_id -> inventory.inventory_id; staff_id -> staff.staff_id

### staff (table, 2 rows)
Each row is an employee, with contact and login info, store, and address references.
- `staff_id` SMALLINT PK — Unique staff identifier; e.g. 1, 2
- `first_name` VARCHAR(45) — Staff member's first name; e.g. Jon, Mike
- `last_name` VARCHAR(45) — Staff member's last name; e.g. Hillyer, Stephens
- `address_id` INT — Staff member's address id; e.g. 3, 4
- `picture` BLOB — Profile picture. Always NULL
- `email` VARCHAR(50) — Staff email address; e.g. Jon.Stephens@sakilastaff.com, Mike.Hillyer@sakilastaff.com
- `store_id` INT — Store where staff works; e.g. 1, 2
- `active` SMALLINT — Staff active flag: 1 if active; e.g. 1
- `username` VARCHAR(16) — Login name for staff; e.g. Jon, Mike
- `password` VARCHAR(40) — Staff login password
- `last_update` TIMESTAMP — Last time staff row was updated; e.g. 2020-12-23 07:12:31
FK: address_id -> address.address_id; store_id -> store.store_id

### store (table, 2 rows)
Each row is a DVD rental store location managed by a staff member.
- `store_id` INT PK — Store identifier; e.g. 1, 2
- `manager_staff_id` SMALLINT — Staff id of store manager; e.g. 1, 2
- `address_id` INT — Address id for store location; e.g. 1, 2
- `last_update` TIMESTAMP — Last time store row was updated; e.g. 2020-12-23 07:12:31
FK: address_id -> address.address_id; manager_staff_id -> staff.staff_id

### customer_list (view, 599 rows)
View: list of customers with readable name/address/city/country details; includes 'active' status as notes.
- `ID` INT — Customer identifier
- `name` ANY — Full name of customer
- `address` VARCHAR(50) — Customer's street address
- `zip_code` VARCHAR(10) — Customer's postal code
- `phone` VARCHAR(20) — Customer's phone number
- `city` VARCHAR(50) — Customer's city name
- `country` VARCHAR(50) — Customer's country name
- `notes` ANY — Account status note
- `SID` INT — Store identifier for customer

### film_list (view, 5462 rows)
View: film display with title, description, category, price, length, rating, actor names.
- `FID` INT — Film identifier
- `title` VARCHAR(255) — Film title
- `description` BLOB SUB_TYPE TEXT — Film description
- `category` VARCHAR(25) — Film category/genre name
- `price` DECIMAL(4,2) — Rental price of film
- `length` SMALLINT — Film length in minutes
- `rating` VARCHAR(10) — MPAA film rating
- `actors` ANY — List of stars in film

### sales_by_film_category (view, 16 rows)
View: shows total sales (sum of payments) per film category.
- `category` VARCHAR(25) — Film category name
- `total_sales` ANY — Total sales in each category

### sales_by_store (view, 2 rows)
View: shows total sales and manager names per store.
- `store_id` INT — Store identifier
- `store` ANY — Store address info or name
- `manager` ANY — Store manager's name
- `total_sales` ANY — Total store sales revenue

### staff_list (view, 2 rows)
View: readable list of staff with name, address, city, country, and store ID.
- `ID` SMALLINT — Staff identifier
- `name` ANY — Staff member's name
- `address` VARCHAR(50) — Staff street address
- `zip_code` VARCHAR(10) — Staff postal code
- `phone` VARCHAR(20) — Staff phone number
- `city` VARCHAR(50) — Staff city name
- `country` VARCHAR(50) — Staff country name
- `SID` INT — Store identifier staff works at
