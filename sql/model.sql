-- HX-50 data model. All transformation is done here; Python only runs the index logic.
-- Inputs (loaded by build.py): raw_prices(date, ticker, close, adj_close), raw_shares(ticker, shares),
--                              raw_excluded(ticker)  -- secondary share classes, never ranked or held
--
-- READING GUIDE (plain words): this file is a kitchen prep station. It takes the raw CSV data
-- and, step by step, produces clean tables the engine can use:
--   raw_prices -> prices -> calendar -> mcap -> ranked      (who is big, each day)
--                        -> returns                          (how much each stock moved, each day)
--   calendar   -> rebalance_dates                            (which days we re-pick stocks)
-- Each "CREATE TABLE x AS SELECT ..." means: run this query and save the answer as a table called x.

-- Start clean so the file can be re-run safely on every rebuild.
DROP TABLE IF EXISTS prices;
DROP TABLE IF EXISTS calendar;
DROP TABLE IF EXISTS mcap;
DROP TABLE IF EXISTS ranked;
DROP TABLE IF EXISTS returns;
DROP TABLE IF EXISTS rebalance_dates;

-- Clean prices: drop non-positive / null values, one row per (date, ticker).
-- close      = the real closing price (used for company size)
-- adj_close  = price adjusted for splits and dividends (used for returns)
CREATE TABLE prices AS
SELECT date, ticker, MAX(close) AS close, MAX(adj_close) AS adj_close
FROM raw_prices
WHERE close IS NOT NULL AND close > 0 AND adj_close IS NOT NULL AND adj_close > 0
GROUP BY date, ticker;
-- An index is like a book's index: it makes lookups by (ticker, date) fast.
CREATE INDEX ix_prices ON prices(ticker, date);

-- Trading calendar = dates where at least half of the usual universe has a price.
-- (protects against a stray partial row creating a fake trading day)
CREATE TABLE calendar AS
SELECT date FROM prices
GROUP BY date
HAVING COUNT(*) >= 0.5 * (SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM prices GROUP BY date))
ORDER BY date;

-- Market cap = split-adjusted close * shares outstanding (current snapshot, see NOTES.md D4).
-- ("Market cap" = company size = price of one share x number of shares.)
-- A name with no price on a date, or no share count, simply has no row => ineligible that day.
CREATE TABLE mcap AS
SELECT p.date, p.ticker, p.close * s.shares AS mcap
FROM prices p
JOIN raw_shares s ON s.ticker = p.ticker AND s.shares > 0   -- JOIN = attach each stock's share count; no count => dropped
JOIN calendar c ON c.date = p.date                           -- only keep real trading days
WHERE p.ticker NOT IN (SELECT ticker FROM raw_excluded);   -- secondary share classes (see hx50/dedupe.py)

-- Rank per date, 1 = largest. ticker is the tie-break so ranks are deterministic.
-- ROW_NUMBER() OVER (PARTITION BY date ...) = "for each date separately, line the stocks up
-- biggest-first and number them 1, 2, 3...".
CREATE TABLE ranked AS
SELECT date, ticker, mcap,
       ROW_NUMBER() OVER (PARTITION BY date ORDER BY mcap DESC, ticker) AS rk
FROM mcap;
CREATE INDEX ix_ranked ON ranked(date);

-- Daily total return from the adjusted close. LAG is per ticker over *its own* rows, so after a
-- missing day the next return spans the gap (equivalent to forward-filling the price).
-- LAG(x) = "the previous row's x for the same stock". So return = today / yesterday - 1.
CREATE TABLE returns AS
SELECT date, ticker, ret FROM (
  SELECT p.date, p.ticker,
         p.adj_close / LAG(p.adj_close) OVER (PARTITION BY p.ticker ORDER BY p.date) - 1.0 AS ret
  FROM prices p JOIN calendar c ON c.date = p.date
)
WHERE ret IS NOT NULL;   -- a stock's very first day has no "yesterday", so no return
CREATE INDEX ix_returns ON returns(date);

-- Rebalance date = first trading day of each (Monday-based) week, taken from the real calendar,
-- so a Monday holiday moves the rebalance to Tuesday automatically.
-- week_start = the Monday of that date's week (date arithmetic: step back to Monday).
-- Then MIN(date) per week = the first real trading day in that week.
-- To rebalance MONTHLY instead: group by strftime('%Y-%m', date) rather than week_start.
CREATE TABLE rebalance_dates AS
SELECT MIN(date) AS date FROM (
  SELECT date,
         date(date, '-' || ((CAST(strftime('%w', date) AS INTEGER) + 6) % 7) || ' days') AS week_start
  FROM calendar
)
GROUP BY week_start
ORDER BY 1;
