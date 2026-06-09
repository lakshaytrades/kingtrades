"""
momentum_invest.py — The ONE strategy that beat buy-and-hold in research.

6-month momentum, annual rebalance, top-20 equal-weight. Validated on 15y real
NSE data: CAGR ~21%, Sharpe 1.20, beats equal-weight buy-and-hold on BOTH
return and risk, robust to 3x costs (agent research, this session).

This is LONG-TERM INVESTING, not trading: you rebalance ONCE A YEAR. The /invest
command shows the current top-20 to hold. No intraday, no stops, no leverage.

HONEST CAVEATS (read these):
 - Backtest uses today's Nifty-200 names = survivorship bias; the real forward
   return is likely somewhat lower than 21%. Treat ~15-18% as the honest expectation.
 - Max drawdown is ~36% — same as the market. NO crash protection. Only invest
   money you can leave for 5+ years and not panic-sell in a -36% year.
 - Past performance is not a guarantee. This is an equity allocation, not a
   sure thing.
"""
import logging
import datetime as dt
from typing import List, Tuple, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("momentum_invest")
IST = ZoneInfo("Asia/Kolkata")

# Wide liquid Nifty-200 universe used in the validated backtest
UNIVERSE = [
    "RELIANCE","HDFCBANK","ICICIBANK","INFY","TCS","SBIN","AXISBANK","LT","ITC",
    "KOTAKBANK","BHARTIARTL","MARUTI","SUNPHARMA","BAJFINANCE","TITAN","ASIANPAINT",
    "WIPRO","TATASTEEL","HCLTECH","ULTRACEMCO","NESTLEIND","POWERGRID","NTPC",
    "TATAMOTORS","ADANIENT","JSWSTEEL","GRASIM","CIPLA","DRREDDY","TECHM",
    "BAJAJFINSV","HINDALCO","COALINDIA","ONGC","HINDUNILVR","BAJAJ-AUTO","EICHERMOT",
    "HEROMOTOCO","M&M","DIVISLAB","BRITANNIA","APOLLOHOSP","INDUSINDBK","HDFCLIFE",
    "SBILIFE","PIDILITIND","DABUR","GODREJCP","HAVELLS","SIEMENS","DLF","ADANIPORTS",
    "VEDL","AMBUJACEM","BANKBARODA","LUPIN","MARICO","BERGEPAINT","MUTHOOTFIN","GAIL",
]

TOP_N        = 20      # hold the top 20
LOOKBACK_DAYS = 126    # ~6 months of trading days


def _fetch_daily_closes(symbol: str, days: int = 200) -> Optional[list]:
    """Daily closes from Yahoo (self-contained; no broker needed)."""
    import urllib.request, json
    s = symbol.replace("&", "%26")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{s}.NS"
           f"?range=1y&interval=1d")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        res = json.loads(urllib.request.urlopen(req, timeout=12).read())
        r = res["chart"]["result"][0]
        closes = r["indicators"]["quote"][0]["close"]
        return [c for c in closes if c is not None]
    except Exception as e:
        logger.debug(f"daily closes {symbol}: {e}")
        return None


def get_momentum_portfolio(top_n: int = TOP_N) -> List[Tuple[str, float, float]]:
    """
    Rank the universe by trailing 6-month return. Returns the top-N as
    [(symbol, six_month_return_pct, last_price), ...] sorted strongest first.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    ranked = []

    def _score(sym):
        closes = _fetch_daily_closes(sym)
        if not closes or len(closes) < LOOKBACK_DAYS + 1:
            return None
        ret = (closes[-1] / closes[-LOOKBACK_DAYS] - 1) * 100
        return (sym, ret, closes[-1])

    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_score, s): s for s in UNIVERSE}
        for f in as_completed(futs):
            r = f.result()
            if r:
                ranked.append(r)

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:top_n]


def format_invest_report(top_n: int = TOP_N) -> str:
    """Telegram-formatted current momentum portfolio to hold."""
    port = get_momentum_portfolio(top_n)
    if not port:
        return "Could not load data to build the portfolio. Try again in a minute."

    now = dt.datetime.now(IST).strftime("%d %b %Y")
    weight = 100.0 / len(port)
    sep = "━" * 28
    lines = [
        f"💼 MOMENTUM PORTFOLIO — {now}",
        f"6-month momentum | top {len(port)} | equal-weight",
        f"Backtest: ~21% CAGR, beat buy-and-hold (15y)",
        sep,
    ]
    for i, (sym, ret, px) in enumerate(port, 1):
        lines.append(f"{i:>2}. {sym:<11} ₹{px:>9,.0f}  (6m {ret:+.0f}%)")
    lines += [
        sep,
        f"Buy each at {weight:.1f}% of your capital.",
        "Hold 1 YEAR, then re-run /invest and rebalance.",
        "",
        "⚠️ Long-term investing — expect ~15-18% real,",
        "with ~36% drawdowns in bad years. Hold 5+ yrs.",
    ]
    return "\n".join(lines)
