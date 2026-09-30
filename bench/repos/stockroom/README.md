# stockroom

A small inventory and pricing module: a product catalog, an append-only stock ledger, tiered and coupon pricing with tax, orders that reserve and ship stock, and CSV import and export. Standard library only.

    from decimal import Decimal
    from stockroom import Product, Store, Tier, Unit

    store = Store(tax_percent="8.25")
    store.add_product(Product("PEN-1", "Pen", Unit.EACH, Decimal("1.50"), tiers=(Tier(10, Decimal("5")),)))
    store.receive("PEN-1", 100)
    order = store.create_order([("PEN-1", 12)])
    store.reserve_order(order.id)
    store.fulfil_order(order.id)
    print(store.billable(order.id).total)

## Modules

| Module | Job |
|---|---|
| `money.py` | `Decimal` parsing, the one rounding function, percentages, formatting |
| `catalog.py` | SKUs, units, quantity tiers, products, the catalog |
| `ledger.py` | append-only stock movements |
| `inventory.py` | on hand, reserved and available, derived from the ledger |
| `pricing.py` | tiers, coupons, the discount cap, tax, quotes |
| `orders.py` | create, reserve, fulfil, cancel, billable quote |
| `csvio.py` | products import, stock export |
| `store.py` | `Store`, the facade over the rest |
| `errors.py` | the exception types |

## Rules

Percentages are in percent units: `Decimal("8.25")` means 8.25 %.

1. **Money is `Decimal`.** Floats and bools are rejected with `MoneyError`. A price is non-negative and has at most two decimal places; a price with more decimals is an error, never rounded silently.
2. **One rounding function.** `money.round_money` rounds half up (halves away from zero) to whole cents. List price times quantity is exact and needs no rounding; rounding happens once per discount amount and once per tax amount, at the order line.
3. **Order of operations on a line.** gross = list price x quantity; discount = round(gross x combined percent); net = gross - discount; tax = round(net x tax percent) for taxable products and 0 otherwise; total = net + tax. Tax is therefore charged on the discounted amount.
4. **Tier choice.** A product's tiers are `(min_qty, percent)` steps with `min_qty` of 2 or more and strictly rising percentages. The tier with the largest `min_qty` not above the quantity applies to every unit of the line. Below the first tier there is no tier discount.
5. **Coupons add to tiers, and the sum is capped.** The combined discount percent is tier percent plus coupon percent, limited to 40. Coupon codes are case-insensitive.
6. **Totals are sums of lines.** A quote's subtotal, discount, net, tax and total are sums of the line amounts, with no further rounding. A quote with no lines is all zeros.
7. **SKUs.** A SKU is letters and digits in groups separated by single hyphens, at most 20 characters. It is case-insensitive and stored in upper case. A SKU can be added to the catalog once.
8. **The ledger is append-only.** Movements are numbered from 1 in the order recorded. Receive, ship, reserve and release quantities are positive whole numbers. An adjustment is a non-zero signed whole number and needs a reason.
9. **Stock levels are derived.** on hand = received + adjusted - shipped; reserved = reserved - released; available = on hand - reserved. Nothing is stored except the ledger.
10. **Refused stock operations record nothing.** Shipping directly or reserving more than is available raises `InsufficientStock`. An adjustment may not leave fewer units on hand than are reserved.
11. **Creating an order.** Repeated SKUs merge into one line (quantities add up, in order of first appearance) before pricing, so tiers see the merged quantity. `Store.quote` merges the same way, so a quote and the order made from the same items agree. The order keeps a copy of each product as it was, so later price changes do not affect it. Creating an order does not touch stock. An invalid item means no order is created.
12. **Reserving is all or nothing.** If any line cannot be covered by available stock, `InsufficientStock` is raised and nothing is reserved. Only a new order can be reserved.
13. **Fulfilling.** Fulfilling ships reserved units: everything outstanding by default, or the given per-SKU quantities. Asking for more than is still reserved for a SKU (or for a SKU with nothing outstanding) raises `OverFulfilment` and ships nothing. A partial fulfillment leaves the order `PARTIALLY_FULFILLED`; when nothing is outstanding it is `FULFILLED`.
14. **Cancelling.** A new, reserved or partly fulfilled order can be cancelled; a fulfilled or cancelled one cannot. Units still reserved are released. Units already shipped stay shipped and are not put back into stock.
15. **Billing.** A fulfilled order is billed as quoted. A cancelled order is billed only for the units that shipped, priced again as if that were the whole order, so tiers follow the shipped quantity. An order that is still open has no bill.
16. **CSV import columns.** A products file has exactly the columns `sku,name,unit,price,taxable,tiers`, in any order. `unit` is one of `each`, `pair`, `box`, `case`, `kg`; `taxable` is `true` or `false`; `tiers` is empty or `min_qty:percent` steps separated by `;`, such as `10:5;50:12.5`. A file that is empty, not UTF-8, not parseable as CSV (for example a field over the csv module's size limit) or has other columns raises `CsvFormatError`.
17. **Bad CSV rows.** A bad row is skipped and reported as a `RowError` with its line number in the file (the header is line 1); good rows are still imported. A row is bad if a field is invalid, it has too few or too many fields, or its SKU appeared earlier in the file or is already in the catalog. Import never overwrites an existing product.
18. **CSV export.** The stock file has the columns `sku,name,unit,on_hand,reserved,available,stock_value`, one row per product sorted by SKU, `\n` line endings. `stock_value` is on-hand units times list price with two decimals.

## Tests

    python -m pytest -q tests

Each rule above is covered by at least one test whose docstring names it (`R1` to `R18`).
