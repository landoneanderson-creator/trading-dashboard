"""Entry point: `python -m tradedash daily` or `python -m tradedash backtest`."""
from __future__ import annotations

import argparse
import datetime as dt
import json

import requests

from . import backtest, config, dashboard, edgar, news, prices, signals


def load_universe() -> list[str]:
    lines = config.UNIVERSE_FILE.read_text().splitlines()
    return [l.strip().upper() for l in lines if l.strip() and not l.startswith("#")]


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
    summary = edgar.summarize(trades, today)
    tickers = list(dict.fromkeys(load_universe() + summary["ticker"].tolist()))
    print(f"[daily] fetching prices for {len(tickers)} tickers ({len(summary)} with insider buying)")
    history = prices.get_many(tickers)
    ideas = signals.build_ideas(history, summary)

    session = requests.Session()
    headlines = {t: news.headlines(t, session=session) for t in ideas.get("ticker", [])}
    report_file = config.DATA_DIR / "backtest_report.json"
    report = json.loads(report_file.read_text()) if report_file.exists() else None
    dashboard.write(dashboard.render(ideas, headlines, report))
    (config.DOCS_DIR / "ideas.json").write_text(ideas.to_json(orient="records", indent=2))
    print(f"[daily] wrote {len(ideas)} ideas")


def run_backtest() -> None:
    trades = edgar.load_trades()
    tickers = list(dict.fromkeys(load_universe() + trades["ticker"].dropna().unique().tolist()))
    history = prices.get_many(tickers)
    report = backtest.run(history, trades)
    calibrated = backtest.save_hold_periods(report)
    print(json.dumps(calibrated, indent=2))


def serve(port: int, refresh: bool) -> None:
    """Pull the latest results, optionally rebuild them here, and open the dashboard locally."""
    import functools
    import http.server
    import subprocess
    import webbrowser

    if (config.ROOT / ".git").exists():
        subprocess.run(["git", "-C", str(config.ROOT), "pull", "--quiet"], check=False)
    if refresh:
        daily()
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
    s = sub.add_parser("serve", help="open the dashboard on this computer")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--refresh", action="store_true", help="rebuild the data here before opening")
    args = parser.parse_args()
    if args.cmd == "daily":
        daily(skip_insider=args.skip_insider)
    elif args.cmd == "serve":
        serve(args.port, args.refresh)
    else:
        run_backtest()


if __name__ == "__main__":
    main()
