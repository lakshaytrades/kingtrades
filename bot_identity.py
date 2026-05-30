"""
bot_identity.py — KING: KingTrades Intelligence & Navigation Generator

This file defines the identity, philosophy, and operational charter of the KING system.
Every trade KING makes is built on 8 legendary trading frameworks used by the greatest
traders of all time, filtered through 26 institutional-grade gates, scored by ML, and
executed with institutional-grade risk management.

Target: 70–80% win rate | 1% avg daily return | <6% max drawdown | Sharpe > 4.0
"""

BOT_NAME        = "KING"
BOT_FULL_NAME   = "KING — KingTrades Intelligence & Navigation Generator"
BOT_VERSION     = "v14.0"
BOT_AUTHOR      = "lakshaytrades"
BOT_TAGLINE     = "26 gates. 8 legendary frameworks. ML-scored. Top-1% precision."
BOT_TARGET_WR   = "70–80%"
BOT_TARGET_RET  = "1.0% avg/day"
BOT_SHARPE_TGT  = "> 4.0"
BOT_MAX_DD      = "< 6%"

TRADING_PHILOSOPHY = """
╔══════════════════════════════════════════════════════════════════════════╗
║            K · I · N · G  —  lakshaytrades Trading System              ║
║        KingTrades Intelligence & Navigation Generator  v14.0            ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Target: 70-80% WR  |  1% avg/day  |  Sharpe >4  |  DD <6%            ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  KING operates on one rule: only trade when ALL evidence agrees.        ║
║  Not one signal. Not two. Every major framework must confirm.           ║
║                                                                          ║
║  BUILT ON 8 LEGENDARY TRADING FRAMEWORKS:                               ║
║                                                                          ║
║  1. Jesse Livermore    — Pivotal points. Never fight the tape.          ║
║  2. Mark Minervini     — VCP. Stage 2 only. SEPA methodology.          ║
║  3. William O'Neil     — CANSLIM. Cup & Handle. Institutional RS.      ║
║  4. Nicolas Darvas     — Box theory. 52wk highs. Volume confirmation.  ║
║  5. Richard Wyckoff    — Volume tells the truth. Accumulation/markup.  ║
║  6. Stan Weinstein     — Stage analysis. 30-week MA. Never buy Stage 1.║
║  7. Turtle Traders     — 20-day breakouts. ATR sizing. Systematic.     ║
║  8. George Soros       — Reflexivity. Trend + narrative = explosion.   ║
║                                                                          ║
║  PLUS: AQR/RenTec/Citadel institutional strategies (v10.0+)            ║
║        GradientBoosting ML gate (P(win) ≥ 60%, Gate 26)                ║
║        Q-learning RL agent that improves with every trade               ║
║        EOD walk-forward optimization (trains nightly)                   ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

STARTUP_BANNER = f"""
╔══════════════════════════════════════════════════════════════╗
║   K I N G  —  lakshaytrades  |  {BOT_VERSION:<27}  ║
║   {BOT_TAGLINE:<60}  ║
║   WR Target: {BOT_TARGET_WR:<8}  Daily: {BOT_TARGET_RET:<12}  Sharpe: {BOT_SHARPE_TGT:<7}  ║
╚══════════════════════════════════════════════════════════════╝
"""

FRAMEWORK_CREDITS = {
    "livermore":  "Jesse Livermore — Reminiscences of a Stock Operator (1923)",
    "minervini":  "Mark Minervini — Trade Like a Stock Market Wizard (2013)",
    "oneil":      "William O'Neil — How to Make Money in Stocks (1988)",
    "darvas":     "Nicolas Darvas — How I Made $2,000,000 in the Stock Market (1960)",
    "wyckoff":    "Richard Wyckoff — The Richard D. Wyckoff Method (1931)",
    "weinstein":  "Stan Weinstein — Secrets for Profiting in Bull and Bear Markets (1988)",
    "turtle":     "Richard Dennis / William Eckhardt — The Original Turtle Trading Rules (1983)",
    "soros":      "George Soros — The Alchemy of Finance (1987)",
}

def print_banner() -> None:
    """Print KING startup banner to console."""
    print(STARTUP_BANNER)
    print(TRADING_PHILOSOPHY)

def get_telegram_header() -> str:
    """One-line header for Telegram alerts."""
    return f"👑 <b>KING</b> ({BOT_VERSION}) — lakshaytrades"

def get_framework_tagline(framework: str) -> str:
    return FRAMEWORK_CREDITS.get(framework, "")
