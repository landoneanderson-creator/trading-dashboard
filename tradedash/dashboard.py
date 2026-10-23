"""Render the ranked ideas as a static HTML page (docs/index.html, served by GitHub Pages)."""
from __future__ import annotations

import datetime as dt
import html

import pandas as pd

from . import config

SETUP_LABELS = {
    "insider_cluster": "Insider cluster buy",
    "insider_buy": "Insider buy",
    "congress_buy": "Congress buy",
    "breakout": "Breakout",
    "pullback": "Pullback in uptrend",
}

DISCLAIMER = (
    "This dashboard is an automated research tool, not financial advice. Signals come from public data "
    "and simple rules that can and do fail. Past performance does not predict future results. Do your own "
    "research, size positions so a stop-out is affordable, and make your own decisions."
)

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--text:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--accent:#2f6fde;
--a:#1f8a4c;--b:#8a6d1f;--stop:#c0392b;--warn-bg:#fff6e0;--warn-line:#e8c66a}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--text:#ecebe6;--muted:#a3a29b;
--line:#33332f;--accent:#7aa7ff;--a:#5fd08f;--b:#e0c063;--stop:#ff7b6b;--warn-bg:#2a2414;--warn-line:#6b5a25}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}h1{font-size:24px;margin:0 0 4px}
.muted{color:var(--muted)}.warn{background:var(--warn-bg);border:1px solid var(--warn-line);border-radius:8px;
padding:10px 14px;margin:16px 0;font-size:13px}
.cards{display:grid;gap:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.top{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 14px}.tk{font-size:20px;font-weight:700}
.grade{font-weight:700;border-radius:6px;padding:1px 8px;font-size:13px;border:1px solid currentColor}
.gA{color:var(--a)}.gB{color:var(--b)}.plan{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));
gap:8px;margin:10px 0}.plan div{font-size:13px}.plan b{display:block;font-size:16px;font-variant-numeric:tabular-nums}
.stop b{color:var(--stop)}ul{margin:6px 0 0;padding-left:18px}a{color:var(--accent)}
.news{font-size:13px;margin-top:8px}table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line)}
.scroll{overflow-x:auto}
"""


def _tv_link(ticker: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol={html.escape(ticker.replace('.', '-'))}"


def _card(idea: pd.Series, news: list[dict]) -> str:
    t = html.escape(idea["ticker"])
    reasons = "".join(f"<li>{html.escape(r)}</li>" for r in idea["reasons"])
    news_html = ""
    if news:
        links = "".join(f'<li><a href="{html.escape(n["link"])}" rel="noopener" target="_blank">'
                        f'{html.escape(n["title"])}</a></li>' for n in news)
        news_html = f'<div class="news"><span class="muted">Recent headlines</span><ul>{links}</ul></div>'
    risk = idea["entry"] - idea["stop"]
    reward = idea["target"] - idea["entry"]
    return f"""
<div class="card">
  <div class="top">
    <span class="tk">{t}</span>
    <span class="grade g{idea['grade']}">Grade {idea['grade']}</span>
    <span>{SETUP_LABELS.get(idea['setup'], idea['setup'])}</span>
    <span class="muted">score {idea['score']}</span>
    <a href="{_tv_link(idea['ticker'])}" target="_blank" rel="noopener">Open in TradingView</a>
  </div>
  <div class="plan">
    <div>Entry (last close)<b>${idea['entry']:,.2f}</b></div>
    <div class="stop">Stop<b>${idea['stop']:,.2f}</b></div>
    <div>Target<b>${idea['target']:,.2f}</b></div>
    <div>Hold<b>{idea['hold_days']} trading days</b></div>
    <div>Exit by<b>{idea['exit_by']}</b></div>
    <div>Reward / risk<b>{reward / risk:.1f} : 1</b></div>
  </div>
  <ul>{reasons}</ul>{news_html}
</div>"""


def _backtest_table(report: dict | None) -> str:
    if not report:
        return "<p class='muted'>No backtest yet. It runs weekly; hold periods use defaults until then.</p>"
    rows = []
    for setup, r in report.items():
        best = r.get("best_horizon")
        row = next((x for x in r["by_horizon"] if x["horizon"] == best), None)
        if row:
            rows.append(f"<tr><td>{SETUP_LABELS.get(setup, setup)}</td><td>{best} days</td><td>{row['n']}</td>"
                        f"<td>{row['avg_return']:.1%}</td><td>{row['win_rate']:.0%}</td>"
                        f"<td>{row['edge_vs_baseline']:+.1%}</td></tr>")
        else:
            rows.append(f"<tr><td>{SETUP_LABELS.get(setup, setup)}</td><td colspan=5 class='muted'>"
                        f"not enough history yet</td></tr>")
    return ("<div class='scroll'><table><tr><th>Setup</th><th>Best hold</th><th>Signals</th><th>Avg return</th>"
            "<th>Win rate</th><th>Edge vs. any day</th></tr>" + "".join(rows) + "</table></div>")


def _simulation_table(results: list[dict] | None) -> str:
    if not results:
        return "<p class='muted'>No simulation yet.</p>"
    rows = "".join(
        f"<tr><td>{html.escape(r['variant'])}</td><td>{r['start']} to {r['end']}</td>"
        f"<td>{r['total_return']:+.1%}</td><td>{r['annual_return']:+.1%}</td><td>{r['max_drawdown']:.1%}</td>"
        f"<td>{r['overall'].get('trades', 0)}</td><td>{r['overall'].get('win_rate', 0):.0%}</td>"
        f"<td>{r['benchmark_return']:+.1%}</td></tr>" for r in results)
    return ("<div class='scroll'><table><tr><th>Strategy</th><th>Period</th><th>Total return</th><th>Per year</th>"
            "<th>Worst drop</th><th>Trades</th><th>Win rate</th><th>S&amp;P 500 (SPY)</th></tr>" + rows +
            "</table></div><p class='muted'>Buys the top-ranked ideas at the next open, 10 equal positions at most, "
            "sells at the stop, target or end of the hold, 0.1% costs per trade. Past results don't predict future ones.</p>")


def render(ideas: pd.DataFrame, news: dict[str, list[dict]], report: dict | None,
           generated: dt.datetime | None = None, simulation: list[dict] | None = None) -> str:
    generated = generated or dt.datetime.now(dt.timezone.utc)
    if ideas.empty:
        body = "<p>No setups passed the filters today.</p>"
    else:
        body = "".join(_card(row, news.get(row["ticker"], [])) for _, row in ideas.iterrows())
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="900"><title>Trade Signal Dashboard</title><style>{CSS}</style></head>
<body><main>
<h1>Trade Signal Dashboard</h1>
<div class="muted">Updated {generated:%Y-%m-%d %H:%M} UTC after the market close. Ranked by grade, then score.</div>
<div class="warn"><b>Not financial advice.</b> {DISCLAIMER}</div>
<p class="muted">Grade A: insider or congressional buying confirmed by the chart. Grade B: one signal only.
Stops and targets are multiples of the stock's average daily range (ATR).</p>
<div class="cards">{body}</div>
<h2>What following the dashboard would have returned</h2>
{_simulation_table(simulation)}
<h2>How the hold periods were chosen</h2>
{_backtest_table(report)}
<p class="muted" style="margin-top:24px">Data: SEC EDGAR Form 4 filings, U.S. House trade disclosures, Yahoo Finance / Stooq daily prices,
Yahoo Finance headlines. Charts: TradingView.</p>
</main></body></html>"""


def write(page: str) -> None:
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (config.DOCS_DIR / "index.html").write_text(page)
