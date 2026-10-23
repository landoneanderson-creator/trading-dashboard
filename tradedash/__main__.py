"""Entry point: `python -m tradedash daily` or `python -m tradedash backtest`."""
from __future__ import annotations

import argparse
import datetime as dt
import json

import pandas as pd
import requests

from . import backtest, config, congress, dashboard, edgar, news, patterns, prices, signals, simulate


def load_universe() -> list[str]:
    lines = config.UNIVERSE_FILE.read_text().splitlines()
    return [l.strip().upper() for l in lines if l.strip() and not l.startswith("#")]


def insider_history(today: dt.date, refresh: bool = True) -> pd.DataFrame:
    """Past purchases used to spot routine buyers; downloads new quarters when they come out."""
    if refresh:
        try:
            return patterns.update_history(today)
        except Exception as exc:
            print(f"::warning::Insider history update failed, using cached history: {exc}")
    return patterns.load_history()


def daily(skip_insider: bool = False) -> None:
    today = dt.date.today()
    trades = edgar.load_trades()
    if not skip_insider:
        try:
            trades = edgar.update_cache(today)
        except Exception as exc:  # keep the dashboard building on cached insider data
            ua = config.SEC_USER_AGENT
            print(f"::warning::SEC EDGAR update failed, using cached insider data: {exc} "
                  f"(User-Agent has an email: {'@' in ua}, words: {len(ua.split())})")
    ctrades = congress.load_trades()
    if not skip_insider:
        try:
            ctrades = congress.update_cache(today)
        except Exception as exc:
            print(f"::warning::House disclosures update failed, using cached congress data: {exc}")
    trades = patterns.tag(trades, insider_history(today, refresh=not skip_insider))
    summary = edgar.summarize(trades, today)
    csummary = congress.summarize(ctrades, today)
    tickers = list(dict.fromkeys(load_universe() + summary["ticker"].tolist() + csummary["ticker"].tolist()))
    print(f"[daily] fetching prices for {len(tickers)} tickers ({len(summary)} with insider buying, "
          f"{len(csummary)} with congressional buying)")
    history = prices.get_many(tickers)
    ideas = signals.build_ideas(history, summary, csummary)
    market = signals.market_state(history)

    session = requests.Session()
    headlines = {t: news.headlines(t, session=session) for t in ideas.get("ticker", [])}
    report_file = config.DATA_DIR / "backtest_report.json"
    report = json.loads(report_file.read_text()) if report_file.exists() else None
    sim = json.loads(simulate.RESULTS_FILE.read_text()) if simulate.RESULTS_FILE.exists() else None
    dashboard.write(dashboard.render(ideas, headlines, report, simulation=sim, market=market))
    (config.DOCS_DIR / "ideas.json").write_text(ideas.to_json(orient="records", indent=2))
    print(f"[daily] wrote {len(ideas)} ideas")


def run_backtest() -> None:
    trades = patterns.tag(edgar.load_trades(), insider_history(dt.date.today()))
    ctrades = congress.load_trades()
    extra = trades["ticker"].dropna().unique().tolist() + ctrades["ticker"].dropna().unique().tolist()
    tickers = list(dict.fromkeys(load_universe() + extra))
    history = prices.get_many(tickers)
    report = backtest.run(history, trades, congress_trades=ctrades)
    calibrated = backtest.save_hold_periods(report)
    print(json.dumps(calibrated, indent=2))


def run_simulation() -> None:
    today = dt.date.today()
    insider = patterns.tag(simulate.load_history(today), insider_history(today))
    usable = edgar.summarize(insider, today, lookback_days=10_000)  # same filters the dashboard applies
    ctrades = congress.load_trades()
    tickers = list(dict.fromkeys(["SPY"] + load_universe() + usable["ticker"].tolist()
                                 + ctrades["ticker"].dropna().unique().tolist()))
    print(f"[simulate] fetching 3 years of prices for {len(tickers)} tickers")
    history = prices.get_many(tickers, pause=0.1, range_="3y")
    filed = pd.to_datetime(insider["filed"], errors="coerce").dropna()
    start = filed.min() if not filed.empty else pd.Timestamp(today - dt.timedelta(days=730))
    results = [simulate.run(history, insider, ctrades, start),
               simulate.run(history, insider, ctrades, start, grade_a_only=True)]
    simulate.save(results)
    print(json.dumps(results, indent=2, default=str))


def serve(port: int, refresh: bool) -> None:
    """Pull the latest results, optionally rebuild them here, and open the dashboard locally."""
    import functools
    import http.server
    import subprocess
    import webbrowser

    import threading
    import time

    def pull() -> None:
        if (config.ROOT / ".git").exists():
            subprocess.run(["git", "-C", str(config.ROOT), "pull", "--quiet"], check=False)

    def keep_updated() -> None:
        # GitHub refreshes the data hourly; fetch it every 15 minutes. The page reloads itself.
        while True:
            time.sleep(15 * 60)
            pull()

    pull()
    if refresh:
        daily()
    threading.Thread(target=keep_updated, daemon=True).start()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(config.DOCS_DIR))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://localhost:{port}/"
        print(f"Dashboard running at {url} (press Ctrl+C to stop)")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="tradedash")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daily", help="refresh data and rebuild the dashboard")
    d.add_argument("--skip-insider", action="store_true", help="use cached insider data only")
    sub.add_parser("backtest", help="recalibrate hold periods from price history")
    sub.add_parser("simulate", help="simulate trading the dashboard's rules on past data")
    s = sub.add_parser("serve", help="open the dashboard on this computer")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--refresh", action="store_true", help="rebuild the data here before opening")
    args = parser.parse_args()
    if args.cmd == "daily":
        daily(skip_insider=args.skip_insider)
    elif args.cmd == "serve":
        serve(args.port, args.refresh)
    elif args.cmd == "simulate":
        run_simulation()
    else:
        run_backtest()


if __name__ == "__main__":
    main()
