"""
web_dashboard.py — SataVector Web Dashboard
Lightweight Flask single-page dashboard showing live positions, P&L, signals,
and AdaptiveBrain state. Runs in a background daemon thread on port 8080.

Usage:
    from web_dashboard import WebDashboard

    def my_state_provider() -> dict:
        return { ... }   # see WebDashboard docstring for schema

    dashboard = WebDashboard(state_provider=my_state_provider, port=8080)
    dashboard.start()
    ...
    dashboard.stop()
"""

import json
import logging
import threading
from datetime import datetime
from typing import Callable, Dict, Any, Optional
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, Response

# ── suppress Flask/Werkzeug startup & request logs ───────────────────────────
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)
logging.getLogger("flask.app").setLevel(logging.ERROR)

ET = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────────────────
# HTML Template
# ─────────────────────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="5">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SataVector Dashboard</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg:       #0d1117;
      --surface:  #161b22;
      --border:   #30363d;
      --text:     #e6edf3;
      --muted:    #8b949e;
      --green:    #3fb950;
      --red:      #f85149;
      --yellow:   #d29922;
      --blue:     #58a6ff;
      --purple:   #bc8cff;
      --radius:   6px;
      --font:     "Segoe UI", system-ui, -apple-system, sans-serif;
    }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: var(--font);
      font-size: 14px;
      line-height: 1.5;
      min-height: 100vh;
    }}

    /* ── Header ─────────────────────────────────────────────── */
    .header {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 14px 24px;
      display: flex;
      align-items: center;
      gap: 32px;
      flex-wrap: wrap;
    }}
    .header .logo {{
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0.5px;
      color: var(--blue);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 12px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.4px;
      text-transform: uppercase;
    }}
    .badge-active   {{ background: rgba(63,185,80,.15);  color: var(--green);  border: 1px solid rgba(63,185,80,.3);  }}
    .badge-paused   {{ background: rgba(210,153,34,.15); color: var(--yellow); border: 1px solid rgba(210,153,34,.3); }}
    .badge-closed   {{ background: rgba(248,81,73,.15);  color: var(--red);    border: 1px solid rgba(248,81,73,.3);  }}
    .badge-live     {{ background: rgba(63,185,80,.15);  color: var(--green);  border: 1px solid rgba(63,185,80,.3);  }}
    .badge-paper    {{ background: rgba(88,166,255,.15); color: var(--blue);   border: 1px solid rgba(88,166,255,.3); }}
    .dot {{
      width: 7px; height: 7px;
      border-radius: 50%;
      background: currentColor;
      animation: pulse 1.8s infinite;
    }}
    @keyframes pulse {{
      0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.35; }}
    }}
    .header-time {{
      margin-left: auto;
      color: var(--muted);
      font-size: 13px;
      font-variant-numeric: tabular-nums;
    }}

    /* ── Layout ──────────────────────────────────────────────── */
    .main {{
      padding: 20px 24px;
      display: grid;
      gap: 20px;
    }}
    .grid-2 {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 20px;
    }}

    /* ── Card ────────────────────────────────────────────────── */
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      overflow: hidden;
    }}
    .card-header {{
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--muted);
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .card-header .count {{
      background: var(--border);
      color: var(--text);
      border-radius: 10px;
      padding: 1px 8px;
      font-size: 11px;
    }}
    .card-body {{ padding: 16px; }}

    /* ── Stats row ───────────────────────────────────────────── */
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 12px;
    }}
    .stat-box {{
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 12px 14px;
    }}
    .stat-label {{
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 4px;
    }}
    .stat-value {{
      font-size: 20px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }}

    /* ── Tables ──────────────────────────────────────────────── */
    .tbl-wrap {{ overflow-x: auto; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      font-variant-numeric: tabular-nums;
    }}
    thead th {{
      text-align: left;
      padding: 8px 10px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }}
    tbody tr {{
      border-bottom: 1px solid rgba(48,54,61,.6);
      transition: background 0.1s;
    }}
    tbody tr:last-child {{ border-bottom: none; }}
    tbody tr:hover {{ background: rgba(255,255,255,.03); }}
    td {{
      padding: 9px 10px;
      white-space: nowrap;
    }}

    /* ── Colour helpers ──────────────────────────────────────── */
    .green  {{ color: var(--green);  }}
    .red    {{ color: var(--red);    }}
    .yellow {{ color: var(--yellow); }}
    .blue   {{ color: var(--blue);   }}
    .muted  {{ color: var(--muted);  }}

    /* ── Direction pill ──────────────────────────────────────── */
    .dir-long  {{ background: rgba(63,185,80,.15);  color: var(--green);  border-radius: 4px; padding: 2px 7px; font-size: 11px; font-weight: 600; }}
    .dir-short {{ background: rgba(248,81,73,.15);  color: var(--red);    border-radius: 4px; padding: 2px 7px; font-size: 11px; font-weight: 600; }}

    /* ── Grade pill ──────────────────────────────────────────── */
    .grade-a  {{ color: var(--green);  font-weight: 700; }}
    .grade-b  {{ color: var(--blue);   font-weight: 700; }}
    .grade-c  {{ color: var(--yellow); font-weight: 700; }}
    .grade-d  {{ color: var(--red);    font-weight: 700; }}

    /* ── Signal pass/reject ──────────────────────────────────── */
    .pass-dot   {{ width: 8px; height: 8px; border-radius: 50%; background: var(--green); display: inline-block; }}
    .reject-dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--red);   display: inline-block; }}

    /* ── Brain card ──────────────────────────────────────────── */
    .brain-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
      gap: 10px;
    }}
    .brain-item {{
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 10px 12px;
      text-align: center;
    }}
    .brain-item .label {{ font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 4px; }}
    .brain-item .value {{ font-size: 16px; font-weight: 700; }}

    /* ── Empty state ─────────────────────────────────────────── */
    .empty {{
      text-align: center;
      padding: 28px 16px;
      color: var(--muted);
      font-size: 13px;
    }}

    /* ── Footer ──────────────────────────────────────────────── */
    .footer {{
      text-align: center;
      padding: 14px;
      color: var(--muted);
      font-size: 11px;
      border-top: 1px solid var(--border);
      margin-top: 8px;
    }}
  </style>
</head>
<body>

<!-- ── Header ─────────────────────────────────────────────────────────────── -->
<header class="header">
  <span class="logo">&#9650; SataVector</span>

  <span class="badge {status_class}">
    <span class="dot"></span>
    {bot_status}
  </span>

  <span class="badge {live_class}">
    {live_label}
  </span>

  <span class="header-time">
    ET&nbsp;&nbsp;{current_et}
  </span>
</header>

<!-- ── Main ───────────────────────────────────────────────────────────────── -->
<div class="main">

  <!-- Today's Stats -->
  <div class="card">
    <div class="card-header">Today&#8217;s Performance</div>
    <div class="card-body">
      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-label">Trades</div>
          <div class="stat-value">{trades}</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Wins</div>
          <div class="stat-value green">{wins}</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Win Rate</div>
          <div class="stat-value {wr_class}">{win_rate:.1f}%</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Total P&amp;L</div>
          <div class="stat-value {pnl_class}">{total_pnl_sign}${total_pnl_abs:.2f}</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Day P&amp;L%</div>
          <div class="stat-value {day_pct_class}">{day_pct_sign}{day_pct:.2f}%</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Open Positions -->
  <div class="card">
    <div class="card-header">
      Open Positions
      <span class="count">{position_count}</span>
    </div>
    <div class="tbl-wrap">
      {positions_html}
    </div>
  </div>

  <!-- Brain + Signals row -->
  <div class="grid-2">

    <!-- AdaptiveBrain -->
    <div class="card">
      <div class="card-header">AdaptiveBrain State</div>
      <div class="card-body">
        <div class="brain-grid">
          <div class="brain-item">
            <div class="label">Min Score</div>
            <div class="value blue">{brain_min_score:.2f}</div>
          </div>
          <div class="brain-item">
            <div class="label">Size Mult</div>
            <div class="value {size_mult_class}">{brain_size_mult:.2f}x</div>
          </div>
          <div class="brain-item">
            <div class="label">Win Streak</div>
            <div class="value green">{streak_wins}</div>
          </div>
          <div class="brain-item">
            <div class="label">Loss Streak</div>
            <div class="value red">{streak_losses}</div>
          </div>
          <div class="brain-item">
            <div class="label">Paused</div>
            <div class="value {brain_paused_class}">{brain_paused_label}</div>
          </div>
        </div>
        {brain_reason_html}
      </div>
    </div>

    <!-- Recent Signals -->
    <div class="card">
      <div class="card-header">
        Recent Signals
        <span class="count">{signal_count}</span>
      </div>
      <div class="tbl-wrap">
        {signals_html}
      </div>
    </div>

  </div>

</div>

<footer class="footer">
  Auto-refreshes every 5 seconds &middot; SataVector &copy; 2026
</footer>

</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pnl_class(value: float) -> str:
    return "green" if value >= 0 else "red"


def _sign(value: float) -> str:
    return "+" if value >= 0 else ""


def _grade_class(grade: str) -> str:
    g = grade.upper()
    if g.startswith("A"):
        return "grade-a"
    if g.startswith("B"):
        return "grade-b"
    if g.startswith("C"):
        return "grade-c"
    return "grade-d"


def _render_positions(positions: list) -> str:
    if not positions:
        return '<div class="empty">No open positions</div>'

    rows = []
    for p in positions:
        pnl      = p.get("pnl", 0.0)
        pnl_pct  = p.get("pnl_pct", 0.0)
        pc       = _pnl_class(pnl)
        pp       = _pnl_class(pnl_pct)
        direction = p.get("direction", "LONG").upper()
        dir_cls  = "dir-long" if direction in ("LONG", "BUY") else "dir-short"
        grade    = p.get("grade", "B")
        age_min  = p.get("age_min", 0.0)

        rows.append(f"""
        <tr>
          <td><strong>{p.get('symbol','—')}</strong></td>
          <td><span class="{dir_cls}">{direction}</span></td>
          <td>${p.get('entry', 0.0):.2f}</td>
          <td>${p.get('ltp', 0.0):.2f}</td>
          <td class="{pc}">{_sign(pnl)}${abs(pnl):.2f}</td>
          <td class="{pp}">{_sign(pnl_pct)}{pnl_pct:.2f}%</td>
          <td class="muted">${p.get('sl', 0.0):.2f}</td>
          <td class="muted">${p.get('t1', 0.0):.2f}</td>
          <td class="{_grade_class(grade)}">{grade}</td>
          <td class="muted">{age_min:.0f}m</td>
        </tr>""")

    return f"""
    <table>
      <thead>
        <tr>
          <th>Symbol</th><th>Dir</th><th>Entry</th><th>LTP</th>
          <th>P&amp;L $</th><th>P&amp;L %</th><th>SL</th><th>T1</th>
          <th>Grade</th><th>Age</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>"""


def _render_signals(signals: list) -> str:
    if not signals:
        return '<div class="empty">No signals yet</div>'

    rows = []
    for s in signals:
        passed  = s.get("passed", False)
        dot_cls = "pass-dot" if passed else "reject-dot"
        status  = '<span class="green">PASS</span>' if passed else '<span class="red">REJECT</span>'
        score   = s.get("score", 0.0)
        sc      = _pnl_class(score - 0.5)     # >0.5 = greenish
        reason  = s.get("reason", "")
        ts      = s.get("ts", "")
        symbol  = s.get("symbol", "—")

        rows.append(f"""
        <tr>
          <td><span class="{dot_cls}"></span></td>
          <td><strong>{symbol}</strong></td>
          <td>{status}</td>
          <td class="{sc}">{score:.2f}</td>
          <td class="muted" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;">{reason}</td>
          <td class="muted">{ts}</td>
        </tr>""")

    return f"""
    <table>
      <thead>
        <tr><th></th><th>Symbol</th><th>Status</th><th>Score</th><th>Reason</th><th>Time</th></tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>"""


def _render_dashboard(state: Dict[str, Any]) -> str:
    # ── header ────────────────────────────────────────────────────────────
    bot_status   = str(state.get("bot_status", "UNKNOWN")).upper()
    live_trading = bool(state.get("live_trading", False))

    status_map = {"ACTIVE": "badge-active", "PAUSED": "badge-paused", "CLOSED": "badge-closed"}
    status_class = status_map.get(bot_status, "badge-paused")

    live_class = "badge-live" if live_trading else "badge-paper"
    live_label = "LIVE TRADING" if live_trading else "PAPER MODE"

    now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S %Z")

    # ── stats ─────────────────────────────────────────────────────────────
    stats      = state.get("stats", {})
    trades     = stats.get("trades", 0)
    wins       = stats.get("wins", 0)
    win_rate   = stats.get("win_rate", 0.0)
    total_pnl  = stats.get("total_pnl", 0.0)
    day_pct    = stats.get("day_pct", 0.0)

    wr_class       = "green" if win_rate >= 55 else ("yellow" if win_rate >= 45 else "red")
    pnl_class_     = _pnl_class(total_pnl)
    day_pct_class_ = _pnl_class(day_pct)
    total_pnl_sign = _sign(total_pnl)
    total_pnl_abs  = abs(total_pnl)
    day_pct_sign   = _sign(day_pct)

    # ── positions ─────────────────────────────────────────────────────────
    positions      = state.get("positions", [])
    positions_html = _render_positions(positions)
    position_count = len(positions)

    # ── brain ─────────────────────────────────────────────────────────────
    brain          = state.get("brain", {})
    brain_min_score  = float(brain.get("min_score", 0.60))
    brain_size_mult  = float(brain.get("size_mult", 1.0))
    streak_wins      = int(brain.get("streak_wins", 0))
    streak_losses    = int(brain.get("streak_losses", 0))
    brain_paused     = bool(brain.get("paused", False))
    brain_reason     = str(brain.get("reason", ""))

    size_mult_class   = "green" if brain_size_mult >= 1.0 else "yellow"
    brain_paused_class = "red" if brain_paused else "green"
    brain_paused_label = "YES" if brain_paused else "NO"

    brain_reason_html = ""
    if brain_reason:
        brain_reason_html = (
            f'<div style="margin-top:12px;padding:8px 12px;background:var(--bg);'
            f'border-radius:var(--radius);border:1px solid var(--border);'
            f'font-size:12px;color:var(--yellow);">'
            f'&#9888; {brain_reason}</div>'
        )

    # ── signals ───────────────────────────────────────────────────────────
    recent_signals = state.get("recent_signals", [])[-10:]
    signals_html   = _render_signals(recent_signals)
    signal_count   = len(recent_signals)

    return _HTML_TEMPLATE.format(
        status_class=status_class,
        bot_status=bot_status,
        live_class=live_class,
        live_label=live_label,
        current_et=now_et,
        trades=trades,
        wins=wins,
        win_rate=win_rate,
        wr_class=wr_class,
        pnl_class=pnl_class_,
        total_pnl_sign=total_pnl_sign,
        total_pnl_abs=total_pnl_abs,
        day_pct_class=day_pct_class_,
        day_pct_sign=day_pct_sign,
        day_pct=day_pct,
        position_count=position_count,
        positions_html=positions_html,
        brain_min_score=brain_min_score,
        brain_size_mult=brain_size_mult,
        size_mult_class=size_mult_class,
        streak_wins=streak_wins,
        streak_losses=streak_losses,
        brain_paused_class=brain_paused_class,
        brain_paused_label=brain_paused_label,
        brain_reason_html=brain_reason_html,
        signal_count=signal_count,
        signals_html=signals_html,
    )


# ─────────────────────────────────────────────────────────────────────────────
# WebDashboard class
# ─────────────────────────────────────────────────────────────────────────────

class WebDashboard:
    """
    Lightweight Flask web dashboard for the SataVector bot.

    Parameters
    ----------
    state_provider : callable
        Zero-argument callable that returns a state dict with the following
        schema (all keys optional — missing values use safe defaults)::

            {
                "bot_status":      str,   # "ACTIVE" | "PAUSED" | "CLOSED"
                "live_trading":    bool,
                "positions":       list[dict],
                "stats":           dict,
                "brain":           dict,
                "recent_signals":  list[dict],
            }

    port : int
        TCP port to listen on (default 8080).
    """

    def __init__(self, state_provider: Callable[[], Dict[str, Any]], port: int = 8080) -> None:
        self._state_provider = state_provider
        self._port           = port
        self._thread: Optional[threading.Thread] = None
        self._app            = self._build_app()

    # ── Flask app factory ─────────────────────────────────────────────────

    def _build_app(self) -> Flask:
        app = Flask(__name__)
        app.logger.setLevel(logging.ERROR)   # type: ignore[attr-defined]

        dashboard = self   # closure reference

        @app.route("/", methods=["GET"])
        def index() -> Response:
            try:
                state = dashboard._state_provider()
            except Exception:
                state = {}
            html = _render_dashboard(state)
            return Response(html, mimetype="text/html")

        @app.route("/api/state", methods=["GET"])
        def api_state() -> Response:
            try:
                state = dashboard._state_provider()
            except Exception as exc:
                state = {"error": str(exc)}
            return jsonify(state)

        @app.route("/health", methods=["GET"])
        def health() -> Response:
            return jsonify({"status": "ok"})

        return app

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start Flask in a daemon thread (non-blocking)."""
        if self._thread is not None and self._thread.is_alive():
            return   # already running

        def _run() -> None:
            # use_reloader=False is required inside a thread
            self._app.run(
                host="0.0.0.0",
                port=self._port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )

        self._thread = threading.Thread(target=_run, daemon=True, name="web-dashboard")
        self._thread.start()

    def stop(self) -> None:
        """
        Signal the daemon thread to stop.

        Flask's built-in development server has no clean shutdown hook when
        run without the reloader.  Because the thread is a daemon, it will be
        torn down automatically when the main process exits.  This method is
        provided for completeness and future extensibility (e.g. swap to
        waitress/gunicorn).
        """
        # Nothing to do — daemon thread exits with the main process.
        # Subclasses or integration tests may override this.
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Standalone smoke-test  (python web_dashboard.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time as _time

    _DEMO_STATE: Dict[str, Any] = {
        "bot_status":   "ACTIVE",
        "live_trading": True,
        "positions": [
            {
                "symbol": "AAPL", "direction": "LONG",
                "entry": 189.50, "ltp": 191.30,
                "pnl": 360.00, "pnl_pct": 0.95,
                "sl": 187.00, "t1": 194.00,
                "grade": "A+", "age_min": 23.0,
            },
            {
                "symbol": "NVDA", "direction": "SHORT",
                "entry": 875.00, "ltp": 868.20,
                "pnl": 680.00, "pnl_pct": 0.78,
                "sl": 882.00, "t1": 858.00,
                "grade": "B", "age_min": 11.5,
            },
            {
                "symbol": "TSLA", "direction": "LONG",
                "entry": 212.10, "ltp": 209.80,
                "pnl": -230.00, "pnl_pct": -1.08,
                "sl": 208.00, "t1": 218.00,
                "grade": "C", "age_min": 44.0,
            },
        ],
        "stats": {
            "trades": 7, "wins": 5,
            "win_rate": 71.4,
            "total_pnl": 810.00,
            "day_pct": 1.62,
        },
        "brain": {
            "min_score":    0.68,
            "size_mult":    1.25,
            "streak_wins":  3,
            "streak_losses": 0,
            "paused":       False,
            "reason":       "",
        },
        "recent_signals": [
            {"symbol": "MSFT",  "passed": True,  "score": 0.82, "reason": "MTF aligned + vol surge",  "ts": "09:47"},
            {"symbol": "AMZN",  "passed": False, "score": 0.51, "reason": "Score below min threshold", "ts": "09:52"},
            {"symbol": "GOOGL", "passed": True,  "score": 0.75, "reason": "ORB breakout confirmed",    "ts": "10:03"},
            {"symbol": "META",  "passed": False, "score": 0.44, "reason": "Market paused — circuit",   "ts": "10:11"},
            {"symbol": "SPY",   "passed": True,  "score": 0.79, "reason": "VWAP reclaim + RSI",        "ts": "10:18"},
        ],
    }

    def demo_provider() -> Dict[str, Any]:
        return _DEMO_STATE

    print(f"Starting SataVector dashboard on http://0.0.0.0:8080 — Ctrl+C to stop")
    dash = WebDashboard(state_provider=demo_provider, port=8080)
    dash.start()

    try:
        while True:
            _time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")
