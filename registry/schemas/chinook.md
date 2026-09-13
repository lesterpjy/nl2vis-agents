# chinook
Digital music store: artists, albums, tracks, customers, invoices.

## Gotchas
- invoices.Total is the invoice revenue, but invoice_items.UnitPrice times Quantity gives the track-level sale which may be summed for invoice breakdowns.
- playlist_track uses a composite primary key (PlaylistId, TrackId).
- employees.ReportsTo is a self-referencing FK for hierarchy.
- customers.SupportRepId joins to employees.EmployeeId.
- InvoiceDate is stored as text like '2009-01-01 00:00:00'; use strftime('%Y', InvoiceDate). Invoices span 2009-2013. Total and UnitPrice are real numbers.
- Track sales: invoice_items -> tracks -> albums -> artists, and tracks -> genres. Table names are plural and lowercase; column names are PascalCase.

## Values
Stored spellings of small text columns; a filter must match them exactly (SQLite = is case-sensitive).
- employees.Title: General Manager, IT Manager, IT Staff, Sales Manager, Sales Support Agent
- employees.City: Calgary, Edmonton, Lethbridge

## Tables
### albums (table, 347 rows)
Each row is an album with its title and references the artist who created it (ArtistId). Used to list or filter tracks and albums by artist.
- `AlbumId` INTEGER PK — Album unique ID, numeric; e.g. 1, 2, 3
- `Title` NVARCHAR(160) — Album title, text; e.g. ...And Justice For All, A Matter of Life and Death, A Real Dead One
- `ArtistId` INTEGER — Artist unique ID for album; e.g. 1, 2, 3
FK: ArtistId -> artists.ArtistId

### artists (table, 275 rows)
Each row is an artist or band, identified by name. Used to look up details about the creator or link artists to albums.
- `ArtistId` INTEGER PK — Artist unique ID, numeric; e.g. 1, 2, 3
- `Name` NVARCHAR(120) — Artist or band name; e.g. A Cor Do Som, AC/DC, Aaron Goldberg

### customers (table, 59 rows)
Each row is a customer. Contains contact and address info, and the SupportRepId of the employee assigned to them.
- `CustomerId` INTEGER PK — Customer unique ID, numeric; e.g. 1, 2, 3
- `FirstName` NVARCHAR(40) — Customer first name; e.g. Aaron, Alexandre, Astrid
- `LastName` NVARCHAR(20) — Customer last name; e.g. Almeida, Barnett, Bernard
- `Company` NVARCHAR(80) — Company name; NULL if no company; e.g. Apple Inc., Banco do Brasil S.A., Google Inc.
- `Address` NVARCHAR(70) — Street address; e.g. 1 Infinite Loop, 1 Microsoft Way, 1033 N Park Ave
- `City` NVARCHAR(40) — City; e.g. Amsterdam, Bangalore, Berlin
- `State` NVARCHAR(40) — State; NULL if unavailable; e.g. AB, AZ, BC
- `Country` NVARCHAR(40) — Country; e.g. Argentina, Australia, Austria
- `PostalCode` NVARCHAR(10) — Mail/post code; NULL if unavailable; e.g. 00-358, 00192, 00530
- `Phone` NVARCHAR(24) — Phone number; NULL if unavailable; e.g. +1 (204) 452-6452, +1 (212) 221-3546, +1 (312) 332-3232
- `Fax` NVARCHAR(24) — Fax number; NULL if unavailable; e.g. +1 (212) 221-4679, +1 (408) 996-1011, +1 (425) 882-8081
- `Email` NVARCHAR(60) — Customer email address; e.g. aaronmitchell@yahoo.ca, alero@uol.com.br, astrid.gruber@apple.at
- `SupportRepId` INTEGER — Employee ID for assigned support rep; e.g. 3, 4, 5
FK: SupportRepId -> employees.EmployeeId

### employees (table, 8 rows)
Each row is an employee. Includes job title, reports-to for organizational structure, and contact info. Support representatives are referenced by customers.
- `EmployeeId` INTEGER PK — Employee unique ID, numeric; e.g. 1, 2, 3
- `LastName` NVARCHAR(20) — Employee last name; e.g. Adams, Callahan, Edwards
- `FirstName` NVARCHAR(20) — Employee first name; e.g. Andrew, Jane, Laura
- `Title` NVARCHAR(30) — Job title (see Values)
- `ReportsTo` INTEGER — Employee manager's ID; NULL for top manager; e.g. 1, 2, 6
- `BirthDate` DATETIME — Birth date and time; e.g. 1947-09-19 00:00:00
- `HireDate` DATETIME — Hiring date and time; e.g. 2002-04-01 00:00:00
- `Address` NVARCHAR(70) — Street address; e.g. 1111 6 Ave SW, 11120 Jasper Ave NW, 5827 Bowness Road NW
- `City` NVARCHAR(40) — City (see Values)
- `State` NVARCHAR(40) — State; e.g. AB
- `Country` NVARCHAR(40) — Country; e.g. Canada
- `PostalCode` NVARCHAR(10) — Mail/post code; e.g. T1H 1Y8, T1K 5N8, T2P 2T3
- `Phone` NVARCHAR(24) — Phone number; e.g. +1 (403) 246-9887, +1 (403) 262-3443, +1 (403) 263-4423
- `Fax` NVARCHAR(24) — Fax number; e.g. +1 (403) 246-9899, +1 (403) 262-3322, +1 (403) 262-6712
- `Email` NVARCHAR(60) — Email address; e.g. andrew@chinookcorp.com, jane@chinookcorp.com, laura@chinookcorp.com
FK: ReportsTo -> employees.EmployeeId

### genres (table, 25 rows)
Each row is a genre of music. Used to categorize tracks.
- `GenreId` INTEGER PK — Genre unique ID, numeric; e.g. 1, 2, 3
- `Name` NVARCHAR(120) — Genre name; e.g. Alternative, Alternative & Punk, Blues

### invoice_items (table, 2240 rows)
Each row is a single line of an invoice, indicating which track was purchased, its price at that sale, and quantity. Used to detail invoice purchases and calculate revenue per track or invoice.
- `InvoiceLineId` INTEGER PK — Invoice line unique ID; e.g. 1, 2, 3
- `InvoiceId` INTEGER — Invoice unique ID for line; e.g. 1, 2, 3
- `TrackId` INTEGER — Track unique ID sold; e.g. 1, 2, 3
- `UnitPrice` NUMERIC(10,2) — Item price per unit; e.g. 0.99, 1.99
- `Quantity` INTEGER — Number units sold for track; e.g. 1
FK: TrackId -> tracks.TrackId; InvoiceId -> invoices.InvoiceId

### invoices (table, 412 rows)
Each row is a sales invoice. Contains billing info, date, customer reference, and total for the invoice.
- `InvoiceId` INTEGER PK — Invoice unique ID; e.g. 1, 2, 3
- `CustomerId` INTEGER — Customer unique ID on invoice; e.g. 1, 2, 3
- `InvoiceDate` DATETIME — Date and time of invoice; e.g. 2009-01-01 00:00:00
- `BillingAddress` NVARCHAR(70) — Billing street address; e.g. 1 Infinite Loop, 1 Microsoft Way, 1033 N Park Ave
- `BillingCity` NVARCHAR(40) — Billing city; e.g. Amsterdam, Bangalore, Berlin
- `BillingState` NVARCHAR(40) — Billing state; NULL if unavailable; e.g. AB, AZ, BC
- `BillingCountry` NVARCHAR(40) — Billing country; e.g. Argentina, Australia, Austria
- `BillingPostalCode` NVARCHAR(10) — Billing mail/post code; NULL if unavailable; e.g. 00-358, 00192, 00530
- `Total` NUMERIC(10,2) — Total amount for invoice; e.g. 0.99, 1.98, 1.99
FK: CustomerId -> customers.CustomerId

### media_types (table, 5 rows)
Each row is a type of media for a track (e.g., audio file format). Used to describe the format of tracks.
- `MediaTypeId` INTEGER PK — Media type unique ID; e.g. 1, 2, 3
- `Name` NVARCHAR(120) — Media type name or format; e.g. AAC audio file, MPEG audio file, Protected AAC audio file

### playlist_track (table, 8715 rows)
Links tracks to playlists. Each row is a track in a playlist (many-to-many), with both IDs as the composite PK.
- `PlaylistId` INTEGER PK — Playlist unique ID; e.g. 1, 3, 5
- `TrackId` INTEGER PK — Track unique ID in playlist; e.g. 1, 2, 3
FK: TrackId -> tracks.TrackId; PlaylistId -> playlists.PlaylistId

### playlists (table, 18 rows)
Each row is a named playlist. Used to group tracks together.
- `PlaylistId` INTEGER PK — Playlist unique ID; e.g. 1, 2, 3
- `Name` NVARCHAR(120) — Playlist name; e.g. 90’s Music, Audiobooks, Brazilian Music

### tracks (table, 3503 rows)
Each row is a track/song. Includes names, composer, duration, size, price, album, genre, and media type references.
- `TrackId` INTEGER PK — Track unique ID; e.g. 1, 2, 3
- `Name` NVARCHAR(200) — Track name; e.g. #1 Zero, #9 Dream, 'Round Midnight
- `AlbumId` INTEGER — Album unique ID for track; e.g. 1, 2, 3
- `MediaTypeId` INTEGER — Media type unique ID for track; e.g. 1, 2, 3
- `GenreId` INTEGER — Genre unique ID for track; e.g. 1, 2, 3
- `Composer` NVARCHAR(220) — Composer(s); NULL if unknown; e.g. A. Jamal, A.Bouchard/J.Bouchard/S.Pearlman, A.Isbell/A.Jones/O.Redding
- `Milliseconds` INTEGER — Track length in milliseconds; e.g. 1071, 4884, 6373
- `Bytes` INTEGER — File size in bytes; e.g. 38747, 161266, 211997
- `UnitPrice` NUMERIC(10,2) — Unit sale price for track; e.g. 0.99, 1.99
FK: MediaTypeId -> media_types.MediaTypeId; GenreId -> genres.GenreId; AlbumId -> albums.AlbumId
