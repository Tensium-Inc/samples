# Data card

## trips.parquet

New York City Taxi and Limousine Commission green taxi trip records, published monthly as
open public records at https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page.
Not simulated and not resampled.

Slice shipped here: 104,003 trips, 2023-12-31 to 2024-02-29.

Hidden validation and terminal slices use official March and November 2024 records around
the America/New_York daylight-saving boundaries. March source SHA-256:
43dedd66dd556acfc38da679a174b6adc32e01dd6dccad14bdfdf1521a14abed. November source
SHA-256: fde255b43d4158124921b7c085d9abf15de5c5a9128c2ce0fb4e2372bc1e0d4a.

| column | meaning |
|---|---|
| `lpep_pickup_datetime` | trip start timestamp |
| `lpep_dropoff_datetime` | trip end timestamp |
| `PULocationID` | pickup taxi zone identifier |
| `DOLocationID` | dropoff taxi zone identifier |
| `RatecodeID` | fare rate class in force for the trip |
| `trip_distance` | metered distance, miles |
| `passenger_count` | passengers reported by the driver |
| `payment_type` | payment method code |
| `trip_type` | street hail or dispatch |
| `fare_amount` | metered fare, USD |
| `total_amount` | total charged, USD |

Cleaning applied: trips with a non-positive or greater-than-six-hour elapsed time are
dropped, and trips with non-positive `trip_distance` are dropped. Nothing else is filtered.

Known characteristics of the source, unmodified:

- TLC publishes each month independently. Column storage types are not stable across
  months: identifier columns appear as 32-bit and 64-bit integers in different releases,
  and any column that carries a missing value in a release is published as floating point.
- `RatecodeID` is published as a floating point column and is absent for a share of trips.
- Identifier and code columns include special rows; the lookup tables are authoritative for
  how each one resolves. Every `LocationID` published in `taxi_zone_lookup.csv` resolves
  through its own row, and `rate_codes.csv` lists code 99 alongside the ordinary codes.
- TLC timestamps are published as local wall times without an offset. On the fall-back
  boundary, a repeated 01:00 value does not identify which instant occurred.

## taxi_zone_lookup.csv

The official TLC taxi zone table, distributed alongside the trip records. `LocationID`
is the surrogate key referenced by `PULocationID` and `DOLocationID`. A row's `Borough` or
`service_zone` may be blank; where it is, the published value falls back to that row's `Zone`
label. An identifier still without a value after that fallback, or one absent from the table,
has no zone.

## rate_codes.csv

Rate class codebook transcribed from the TLC trip record data dictionary published with
the green taxi records. Code 99 denotes a trip whose rate was not reported; the TLC publishes
such trips with the `RatecodeID` column absent. A code outside this codebook has no rate class.
