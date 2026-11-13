# HX-50 notes

**Data**
- Sources I looked at: Yahoo Finance (`yfinance`), Stooq, SEC EDGAR. I used yfinance: it gives prices and share counts from one place, with no key.
- Stooq now blocks scripts (it returns a web page, not data), so I could not compare prices with it. I checked the yfinance data myself instead: every stock has prices and shares, and there are no odd jumps except MRNA (+177% on 2026-08-19, kept as is).
- Universe: today's S&P 500 (500 companies). Period: 2026-03-19 to 2026-10-19 (148 trading days, 31 rebalances).
- **Limit:** share counts are today's numbers (fetched 2026-10-20), not historical. So past market caps are only approximate, and using today's S&P list adds survivorship bias.

**Decisions I made**
- Yahoo gives every share class of a company the full company share count, so Alphabet showed up twice (GOOGL and GOOG) in the top 10. I keep one share class per company (kept GOOGL, FOXA, NWS).
- Market cap = price x shares. Returns use adjusted prices (dividends included).
- Rebalance on the first trading day of each week, using that day's closing ranks. That day's return still belongs to the old holdings; the new equal weights start after the close.
- Missing price on a rebalance day: the stock cannot be picked that day. Missing return for a held stock: counted as 0 that day.
- Turnover = half of the total weight change (one-way). I split it in two: **membership** (stocks added or dropped) and **reweight** (resetting kept stocks back to 2%). So turnover is never zero, even when no stock changes.
- Weights drift between rebalances, so they are 2% each only on rebalance days.

**Why the level never jumps**
The level only changes by the portfolio's daily return. A rebalance changes the weights, not the level. Tests (21, run with `pytest`) check: the level matches an independent recalculation, weights always sum to 100%, no top-10 stock is ever held, and the plain index is exactly ranks 11-60 on every rebalance day.

**Stabilising (R5)**
- Rule: a member stays while its rank is 70 or better (the buffer is 10). Open spots go to the best-ranked stocks not already held. The top-10 exclusion stays strict.
- Result, stabilised vs plain: turnover per rebalance 3.2% vs 6.5%; stocks added 19 vs 71; stocks re-added within 4 weeks of being dropped 4 vs 41; total return 17.2% vs 16.0% (about 17.0% vs 15.6% after 10 bps trading cost).
- The return gap is only one short period and changes a lot with buffer size, so I do not claim the buffer adds return.
- Trade-off: less trading, but the index is not exactly ranks 11-60 (members can sit at 61-70).
- Why 10: turnover goes 6.5% (buffer 0), 3.9% (5), 3.2% (10), 2.6% (20). Most of the saving is already there at 10.
- Not used: a buffer at the rank 10/11 edge (`top_buffer`). It cuts additions (19 to 10) but would hold top-10 stocks, which the client does not want.

**At 10 years and 5,000 stocks**
Use columnar storage (Parquet/DuckDB), rebuild only from the first changed date, store prices and share counts as they were known at each date, add a security master for share classes, delistings and spin-offs, and add data checks.

**AI tools**
I used Claude (Anthropic) to plan the approach and write the code, tests and these notes. I ran everything and can explain it.
