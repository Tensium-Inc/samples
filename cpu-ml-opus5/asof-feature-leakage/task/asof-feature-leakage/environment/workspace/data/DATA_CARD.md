# Data card — `transactions.parquet`

**Source.** UCI Machine Learning Repository, *Online Retail II* (CC BY 4.0). Real transactions
from a UK-based online giftware retailer. Not simulated, not resampled.

**Slice shipped here.** 594,423 invoice lines, 2009-12-01 .. 2011-06-30, 5,106 customers.

**Columns.**

| column | meaning |
|---|---|
| `Invoice` | invoice id. **Ids beginning `C` are credit notes** (returns/cancellations). |
| `StockCode` | product code |
| `Description` | free-text product name |
| `Quantity` | units; **negative on credit notes** |
| `InvoiceDate` | the timestamp printed on the invoice document |
| `Price` | unit price, GBP |
| `customer_id` | account id |
| `Country` | billing country |
| `is_credit` | convenience flag, `True` iff `Invoice` starts with `C` |

**Cleaning already applied.** Lines with no `customer_id` are dropped (they cannot be attributed
to an account). Lines with `Quantity == 0` are dropped. Nothing else is filtered — returns,
odd stock codes, and zero-price lines are all still here, as in the source.

**Known quirks in the source, unmodified.**

- Credit notes are separate documents. `InvoiceDate` on a credit note is the date the credit
  was **raised by the finance team**, which is not the date of the order it reverses.
- A credit note does not carry a reference to the invoice it offsets. Matching is by account
  and product.
- Some stock codes are non-product entries (`POST`, `M`, `BANK CHARGES`, …).
- The same product can appear on several lines of one invoice.
