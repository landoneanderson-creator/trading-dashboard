# Trade Signal Dashboard

A daily list of stock trade ideas, each with an entry, stop, target, and how long to hold.
You place the trades yourself; nothing here connects to a broker.

> **Not financial advice.** This is an automated research tool built on public data and simple rules
> that can and do fail. Past performance does not predict future results.

## What it looks for

| Setup | What triggers it | Stop / target | Starting hold |
|---|---|---|---|
| Insider cluster buy | 2+ company insiders bought shares on the open market in the last 30 days (SEC Form 4, code P, $25k+ each) | 2.5 / 5 ATR | 60 trading days |
| Insider buy | One insider bought $25k+ | 2.5 / 4 ATR | 40 trading days |
| Congress buy | A member of the U.S. House disclosed buying the stock in the last 45 days (periodic transaction reports) | 2.5 / 4 ATR | 40 trading days |
| Breakout | Close above the prior 20-day high on 1.5x average volume, above the 50-day average | 2 / 4 ATR | 15 trading days |
| Pullback | RSI(14) under 40 while the 50-day average is above the 200-day | 1.5 / 3 ATR | 10 trading days |

ATR is the stock's average daily price range over 14 days. **Grade A** ideas have insider or congressional buying *and*
chart confirmation (uptrend or price above its 20-day average). **Grade B** ideas have one signal only.
CEO/CFO purchases, more buyers, and bigger dollar amounts raise the score.

### Two filters on top

- **Routine vs. opportunistic insiders.** An insider who bought the same stock in the same calendar month
  in each of the last three years is a *routine* buyer (think of a director who buys every March). Research
  ([Cohen, Malloy and Pomorski, 2012](https://www.nber.org/digest/apr11/decoding-inside-information))
  found those buys predict nothing, so they're ignored. An insider who bought every year but *off* their
  usual schedule is *opportunistic*, and those buys predicted market-beating returns, so they score +5.
  First-time buyers are scored as before. Buying history comes from the SEC's free quarterly Form 3/4/5
  data sets (three years, cached in `data/insider_history.csv`, topped up when a new quarter is published).
- **Market regime.** When the S&P 500 (SPY) closes below its 200-day average, breakouts and pullbacks are
  paused because they fail far more often in falling markets. Insider and congressional ideas still show,
  with a warning to size smaller. The backtest and the trading simulation apply the same rule, and the
  TradingView indicator does too (it shades the chart red while the market is below its 200-day).

### How hold periods are chosen

Every Saturday the backtest replays each setup over ~2 years of daily prices and measures returns
5, 10, 15, 20, 40, and 60 trading days later. It compares those to the return of any random day for the
same stocks and picks the hold with the biggest edge per √days held. Calibrated holds are saved in
`data/hold_periods.json` and replace the defaults above. Insider setups need a few months of cached
Form 4 data before they can be calibrated.

Known limits: the stock list is today's (survivorship bias), Form 4s are public up to 2 business days
after the trade, entries assume the last close, and the backtest ignores costs and slippage.

## Data sources (all free)

- **SEC EDGAR** Form 4 filings for insider purchases (needs a contact User-Agent, see setup), plus the
  quarterly Form 3/4/5 data sets for three years of insider buying history
- **U.S. House Clerk** periodic transaction reports for congressional stock purchases. Only e-filed reports
  are parsed (scanned paper filings are skipped), members can report up to 45 days after a trade, and the
  Senate isn't covered because its disclosure site blocks automated requests from cloud servers
- **Yahoo Finance** daily prices, with **Stooq** as backup
- **Yahoo Finance RSS** headlines (shown for context, not scored)
- **TradingView** for charts: every idea links to its chart, and `pine/trade_signals.pine` draws the
  chart signals on your TradingView charts

## Setup

1. **Add the SEC contact secret.** Repo Settings → Secrets and variables → Actions → New repository
   secret. Name `SEC_USER_AGENT`, value like `trading-dashboard your-email@example.com`.
   The SEC blocks requests without contact info.
2. **Run it once.** Actions → *Refresh dashboard* → Run workflow, task `backtest`. The first run backfills
   10 days of Form 4 filings and takes roughly 30–60 minutes; later daily runs take a few minutes.
3. **View the dashboard.** The page is written to `docs/index.html` (ideas also in `docs/ideas.json`).
   Turn on GitHub Pages (Settings → Pages → Deploy from branch → `main` / `docs`) to get a URL.
   Pages on a private repo needs a paid GitHub plan.

After that it refreshes every weekday at 22:30 UTC, after the market close. Signals use daily bars, so
one refresh a day is all they need: check it in the evening and place orders the next morning.

### TradingView indicator

Open the Pine Editor in TradingView, paste `pine/trade_signals.pine`, and add it to a chart. It marks
breakouts (BO) and pullbacks (PB), shades uptrends, and draws ATR stop and target lines. Its alert
conditions work on any plan; webhook delivery needs a paid TradingView plan.

## Open it on your own computer (free)

GitHub keeps refreshing the data every weekday for free. To view it locally instead of on GitHub Pages:

1. Install [Python 3.12+](https://www.python.org/downloads/) and [Git](https://git-scm.com/downloads).
2. Once, in a terminal:
   ```bash
   git clone https://github.com/landoneanderson-creator/trading-dashboard.git
   cd trading-dashboard
   pip install -r requirements.txt
   ```
3. Any time you want to look:
   ```bash
   cd trading-dashboard
   python -m tradedash serve
   ```
   It pulls the latest results, opens http://localhost:8000 in your browser, and keeps pulling new
   results every 15 minutes while it runs (the page reloads itself). Add `--refresh` to
   rebuild the data on your own computer first (set `SEC_USER_AGENT` in your terminal for insider data).

## Running locally

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="trading-dashboard you@example.com"
python -m tradedash backtest   # optional, recalibrates hold periods
python -m tradedash daily      # writes docs/index.html
pytest
```

Edit `universe.txt` to change which stocks are scanned for chart setups (stocks with insider buying are
always added). Thresholds live in `tradedash/config.py`.
