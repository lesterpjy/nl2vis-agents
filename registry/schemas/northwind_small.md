# northwind_small
Trading company: customers, orders, products, suppliers, employees.

## Gotchas
- The table name Order is a reserved word: always write it as "Order" (double quotes).
- Line revenue = OrderDetail.UnitPrice * Quantity * (1 - Discount); Order.Freight is shipping cost, not revenue.
- OrderDetail.Id is NOT a numeric PK; use OrderId and ProductId for joins.
- OrderDetail.Discount is a decimal (e.g., 0.25 means 25%; no units).
- All dates are stored as text like '2012-07-04'; use strftime('%Y', OrderDate) for years. Orders span 2012-07 to 2014-05.
- 'Discontinued' in Product is 0 (active) or 1 (discontinued), not boolean.
- Order.ShipVia refers to Shipper.Id (no declared foreign key). No foreign keys are declared anywhere; join on the Id columns named in each description.
- "Region" means different things: Customer.Region, Order.ShipRegion and Supplier.Region hold geographic areas (Western Europe, Scandinavia, ...); the Region table (Eastern, Western, Northern, Southern) is a sales-territory grouping reached through Territory and EmployeeTerritory. Ask which one when a question says "region".
- Employee.HireDate (2024-2026) and BirthDate are not on the same timeline as the orders (2012-2014); tenure or seniority against order dates is meaningless.

## Values
Stored spellings of small text columns; a filter must match them exactly (SQLite = is case-sensitive).
- Category.CategoryName: Beverages, Condiments, Confections, Dairy Products, Grains/Cereals, Meat/Poultry, Produce, Seafood
- Customer.Region: British Isles, Central America, Eastern Europe, North America, Northern Europe, Scandinavia, South America, Southern Europe, Western Europe
- Employee.TitleOfCourtesy: Dr., Mr., Mrs., Ms.
- Employee.City: Kirkland, London, Redmond, Seattle, Tacoma
- Employee.Region: British Isles, North America
- Employee.Country: UK, USA
- Order.ShipRegion: British Isles, Central America, Eastern Europe, North America, Northern Europe, Scandinavia, South America, Southern Europe, Western Europe
- Shipper.CompanyName: Federal Shipping, Speedy Express, United Package
- Supplier.Region: British Isles, Eastern Asia, NSW, North America, Northern Europe, Scandinavia, South America, South-East Asia, Southern Europe, Victoria, Western Europe
- Supplier.Country: Australia, Brazil, Canada, Denmark, Finland, France, Germany, Italy, Japan, Netherlands, Norway, Singapore, Spain, Sweden, UK, USA

## Tables
### Category (table, 8 rows)
Categories for products. Each row links via Id to Product.CategoryId and includes a name and description.
- `Id` INTEGER PK — Unique category id, integer; e.g. 1, 2, 3
- `CategoryName` VARCHAR(8000) — Category's name (see Values)
- `Description` VARCHAR(8000) — Category description

### Customer (table, 91 rows)
Customers who place orders. Use Id to join with Order.CustomerId.
- `Id` VARCHAR(8000) PK — Unique customer id, string; e.g. ALFKI, ANATR, ANTON
- `CompanyName` VARCHAR(8000) — Company's name; e.g. Alfreds Futterkiste, Ana Trujillo Emparedados y helados, Antonio Moreno Taquería
- `ContactName` VARCHAR(8000) — Contact person's name; e.g. Alejandra Camino, Alexander Feuer, Ana Trujillo
- `ContactTitle` VARCHAR(8000) — Contact person's title; e.g. Accounting Manager, Assistant Sales Agent, Assistant Sales Representative
- `Address` VARCHAR(8000) — Customer mailing address; e.g. 1 rue Alsace-Lorraine, 12 Orchestra Terrace, 120 Hanover Sq.
- `City` VARCHAR(8000) — Customer city; e.g. Aachen, Albuquerque, Anchorage
- `Region` VARCHAR(8000) — Customer region or state (see Values)
- `PostalCode` VARCHAR(8000) — Postal code. NULL: missing zipcode; e.g. 01-012, 01307, 02389-673
- `Country` VARCHAR(8000) — Customer country; e.g. Argentina, Austria, Belgium
- `Phone` VARCHAR(8000) — Customer phone number; e.g. (02) 201 24 67, (071) 23 67 22 20, (1) 123-5555
- `Fax` VARCHAR(8000) — Fax number. NULL: no fax; e.g. (02) 201 24 68, (071) 23 67 22 21, (1) 123-5556

### CustomerCustomerDemo (table, 0 rows)
(empty) Would relate customers and customer types, but contains no data in this database.
- `Id` VARCHAR(8000) PK — Customer id
- `CustomerTypeId` VARCHAR(8000) — Demographic type id

### CustomerDemographic (table, 0 rows)
(empty) Customer types/descriptions for CustomerCustomerDemo; not used due to empty data.
- `Id` VARCHAR(8000) PK — Demographic type id
- `CustomerDesc` VARCHAR(8000) — Demographic description

### Employee (table, 9 rows)
Employees (sales reps, etc.). Use Id to join with Order.EmployeeId and EmployeeTerritory.EmployeeId. Self-joins possible via ReportsTo (manager).
- `Id` INTEGER PK — Unique employee id, integer; e.g. 1, 2, 3
- `LastName` VARCHAR(8000) — Employee's last name; e.g. Buchanan, Callahan, Davolio
- `FirstName` VARCHAR(8000) — Employee's first name; e.g. Andrew, Anne, Janet
- `Title` VARCHAR(8000) — Job title; e.g. Inside Sales Coordinator, Sales Manager, Sales Representative
- `TitleOfCourtesy` VARCHAR(8000) — Courtesy (Mr., Ms., etc.) (see Values)
- `BirthDate` VARCHAR(8000) — Birth date. Text, format yyyy-mm-dd; e.g. 1969-09-19
- `HireDate` VARCHAR(8000) — Hire date. Text, format yyyy-mm-dd; e.g. 2024-04-01
- `Address` VARCHAR(8000) — Employee's address; e.g. 14 Garrett Hill, 4110 Old Redmond Rd., 4726 - 11th Ave. N.E.
- `City` VARCHAR(8000) — Employee's city (see Values)
- `Region` VARCHAR(8000) — Employee's region or state (see Values)
- `PostalCode` VARCHAR(8000) — Employee's postal code; e.g. 98033, 98052, 98105
- `Country` VARCHAR(8000) — Employee's country (see Values)
- `HomePhone` VARCHAR(8000) — Home phone number; e.g. (206) 555-1189, (206) 555-3412, (206) 555-8122
- `Extension` VARCHAR(8000) — Internal extension number; e.g. 2344, 3355, 3453
- `Photo` BLOB — NULL: not used
- `Notes` VARCHAR(8000) — Notes about employee
- `ReportsTo` INTEGER — Manager's EmployeeId. NULL: top level; e.g. 2, 5
- `PhotoPath` VARCHAR(8000) — Photo path or link

### EmployeeTerritory (table, 49 rows)
Assigns employees to territories by EmployeeId and TerritoryId. Many-to-many relationship.
- `Id` VARCHAR(8000) PK — Employee id and Territory id, slash-separated; e.g. 1/06897, 1/19713, 2/01581
- `EmployeeId` INTEGER — Employee id; e.g. 1, 2, 3
- `TerritoryId` VARCHAR(8000) — Territory id; e.g. 01581, 01730, 01833

### Order (table, 830 rows)
Sales orders. Id joins to OrderDetail.OrderId. CustomerId and EmployeeId link to Customer and Employee. Contains order and shipping info.
- `Id` INTEGER PK — Unique order id, integer; e.g. 10248, 10249, 10250
- `CustomerId` VARCHAR(8000) — Customer id; e.g. ALFKI, ANATR, ANTO
- `EmployeeId` INTEGER — Employee id who handled order; e.g. 1, 2, 3
- `OrderDate` VARCHAR(8000) — Order date. Text, format yyyy-mm-dd; e.g. 2012-07-04
- `RequiredDate` VARCHAR(8000) — Date order is required. yyyy-mm-dd; e.g. 2012-07-24
- `ShippedDate` VARCHAR(8000) — Shipment date. NULL: not shipped; e.g. 2012-07-10
- `ShipVia` INTEGER — Shipper id; e.g. 1, 2, 3
- `Freight` DECIMAL — Shipping cost, decimal currency; e.g. 0.02, 0.12, 0.14
- `ShipName` VARCHAR(8000) — Name for shipping address; e.g. Alfred's Futterkiste, Alfreds Futterkiste, Ana Trujillo Emparedados y helados
- `ShipAddress` VARCHAR(8000) — Shipping address; e.g. 1 rue Alsace-Lorraine, 1029 - 12th Ave. S., 12 Orchestra Terrace
- `ShipCity` VARCHAR(8000) — Shipping city; e.g. Aachen, Albuquerque, Anchorage
- `ShipRegion` VARCHAR(8000) — Shipping region or state (see Values)
- `ShipPostalCode` VARCHAR(8000) — Shipping postal code. NULL: missing; e.g. 01-012, 01307, 02389-673
- `ShipCountry` VARCHAR(8000) — Shipping country; e.g. Argentina, Austria, Belgium

### OrderDetail (table, 2155 rows)
Line items for orders. Joins to Order (OrderId) and Product (ProductId). UnitPrice and Quantity per item, with Discount as decimal (e.g., 0.1 for 10%).
- `Id` VARCHAR(8000) PK — Order id and Product id, slash-separated; e.g. 10248/11, 10248/42, 10248/72
- `OrderId` INTEGER — Order id; e.g. 10248, 10249, 10250
- `ProductId` INTEGER — Product id; e.g. 1, 2, 3
- `UnitPrice` DECIMAL — Price per unit, at order time; e.g. 2, 2.5, 3.6
- `Quantity` INTEGER — Number of units ordered; e.g. 1, 2, 3
- `Discount` DOUBLE — Fractional discount applied (0-1); e.g. 0.0, 0.01, 0.02

### Product (table, 77 rows)
Products for sale. CategoryId and SupplierId join to Category and Supplier tables. Stock, unit price, etc. 'Discontinued' is 0 or 1.
- `Id` INTEGER PK — Unique product id, integer; e.g. 1, 2, 3
- `ProductName` VARCHAR(8000) — Product's name; e.g. Alice Mutton, Aniseed Syrup, Boston Crab Meat
- `SupplierId` INTEGER — Supplier id; e.g. 1, 2, 3
- `CategoryId` INTEGER — Category id; e.g. 1, 2, 3
- `QuantityPerUnit` VARCHAR(8000) — Description of packaging, units per sale; e.g. 1 kg pkg., 10 - 200 g glasses, 10 - 4 oz boxes
- `UnitPrice` DECIMAL — Current unit price; e.g. 2.5, 4.5, 6
- `UnitsInStock` INTEGER — Units available in stock; e.g. 0, 3, 4
- `UnitsOnOrder` INTEGER — Units ordered, not yet received; e.g. 0, 10, 20
- `ReorderLevel` INTEGER — Stock threshold to reorder; e.g. 0, 5, 10
- `Discontinued` INTEGER — 1: no longer sold, 0: active; e.g. 0, 1

### Region (table, 4 rows)
Geographic regions for territories. Territory.RegionId joins to Region.Id.
- `Id` INTEGER PK — Region id; e.g. 1, 2, 3
- `RegionDescription` VARCHAR(8000) — Region's name or description

### Shipper (table, 3 rows)
Shipping companies. Used via Order.ShipVia.
- `Id` INTEGER PK — Unique shipper id; e.g. 1, 2, 3
- `CompanyName` VARCHAR(8000) — Shipper company name (see Values)
- `Phone` VARCHAR(8000) — Shipper company phone number; e.g. (503) 555-3199, (503) 555-9831, (503) 555-9931

### Supplier (table, 29 rows)
Companies that supply products. Join via Id to Product.SupplierId.
- `Id` INTEGER PK — Unique supplier id; e.g. 1, 2, 3
- `CompanyName` VARCHAR(8000) — Supplier's business name; e.g. Aux joyeux ecclésiastiques, Bigfoot Breweries, Cooperativa de Quesos 'Las Cabras'
- `ContactName` VARCHAR(8000) — Supplier's contact person; e.g. Anne Heikkonen, Antonio del Valle Saavedra, Beate Vileid
- `ContactTitle` VARCHAR(8000) — Contact person's title; e.g. Accounting Manager, Coordinator Foreign Markets, Export Administrator
- `Address` VARCHAR(8000) — Supplier address; e.g. 148 rue Chasseur, 170 Prince Edward Parade Hunter's Hill, 29 King's Way
- `City` VARCHAR(8000) — Supplier city; e.g. Ann Arbor, Annecy, Bend
- `Region` VARCHAR(8000) — Supplier region or state (see Values)
- `PostalCode` VARCHAR(8000) — Supplier postal code; e.g. 02134, 0512, 100
- `Country` VARCHAR(8000) — Supplier country (see Values)
- `Phone` VARCHAR(8000) — Supplier phone number; e.g. (0)2-953010, (010) 9984510, (02) 555-5914
- `Fax` VARCHAR(8000) — Fax number. NULL: none; e.g. (02) 555-4873, (03) 444-6588, (04721) 8714
- `HomePage` VARCHAR(8000) — Supplier homepage. NULL: none; e.g. #CAJUN.HTM#, #FORMAGGI.HTM#

### Territory (table, 53 rows)
Territories assigned to employees. RegionId joins to Region.Id.
- `Id` VARCHAR(8000) PK — Territory id, string; e.g. 01581, 01730, 01833
- `TerritoryDescription` VARCHAR(8000) — Territory's name or description
- `RegionId` INTEGER — Linked region id; e.g. 1, 2, 3

### ProductDetails_V (view, 77 rows)
View with detailed product info, joining Product, Category, and Supplier. Contains product, supplier, and category fields, no additional rows.
- `Id` INTEGER — Product id
- `ProductName` VARCHAR(8000) — Product name
- `SupplierId` INTEGER — Supplier id
- `CategoryId` INTEGER — Category id
- `QuantityPerUnit` VARCHAR(8000) — Description of packaging
- `UnitPrice` DECIMAL — Current unit price
- `UnitsInStock` INTEGER — Units in stock
- `UnitsOnOrder` INTEGER — Units on order
- `ReorderLevel` INTEGER — Reorder threshold
- `Discontinued` INTEGER — 1: discontinued, 0: not
- `CategoryName` VARCHAR(8000) — Category name
- `CategoryDescription` VARCHAR(8000) — Category description
- `SupplierName` VARCHAR(8000) — Supplier name
- `SupplierRegion` VARCHAR(8000) — Supplier region
