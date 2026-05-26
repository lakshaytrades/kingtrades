#!/usr/bin/env python3
"""
emergency_close.py — Emergency close all Alpaca crypto positions.

Run this script to immediately stop all losses when the bot has large or
unmanaged open positions.

Usage:
    python emergency_close.py          # interactive — shows positions, asks confirm
    python emergency_close.py --force  # close without confirmation (use with care)
    python emergency_close.py --audit  # show positions only, don't close anything
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _fmt(val) -> str:
    try:
        return f"${float(val):,.2f}"
    except Exception:
        return str(val)


def main():
    force  = "--force" in sys.argv
    audit  = "--audit" in sys.argv

    try:
        from alpaca.trading.client import TradingClient

        api_key    = os.getenv("ALPACA_API_KEY", "")
        api_secret = os.getenv("ALPACA_SECRET_KEY", "")
        base_url   = os.getenv("ALPACA_BASE_URL", "")
        paper      = base_url.lower().find("paper") >= 0 or not base_url

        if not api_key or not api_secret:
            print("ERROR: ALPACA_API_KEY / ALPACA_SECRET_KEY not set in .env")
            sys.exit(1)

        client = TradingClient(api_key, api_secret, paper=paper)

        # ── Account summary ────────────────────────────────────────────────
        acct    = client.get_account()
        equity  = float(getattr(acct, "equity", 0) or 0)
        daily   = float(getattr(acct, "equity", 0) or 0) - float(getattr(acct, "last_equity", equity) or equity)
        print(f"\nAlpaca account ({'PAPER' if paper else 'LIVE'})")
        print(f"  Equity:       {_fmt(equity)}")
        print(f"  Daily change: {_fmt(daily)}")

        # ── Open positions ─────────────────────────────────────────────────
        positions = client.get_all_positions()

        if not positions:
            print("\nNo open positions found. Nothing to close.\n")
            return

        total_mv  = 0.0
        total_pnl = 0.0
        print(f"\nOpen positions ({len(positions)}):")
        print(f"  {'Symbol':<12}{'Side':<8}{'Qty':<14}{'Market Value':<16}{'Unrealised P&L'}")
        print("  " + "-" * 64)
        for p in positions:
            sym  = str(getattr(p, "symbol", "?"))
            side = str(getattr(p, "side", "?"))
            qty  = float(getattr(p, "qty", 0) or 0)
            mv   = float(getattr(p, "market_value", 0) or 0)
            pnl  = float(getattr(p, "unrealized_pl", 0) or 0)
            total_mv  += abs(mv)
            total_pnl += pnl
            pnl_sign   = "+" if pnl >= 0 else ""
            print(f"  {sym:<12}{side:<8}{qty:<14.6f}{_fmt(mv):<16}{pnl_sign}{_fmt(pnl)}")

        print("  " + "-" * 64)
        pnl_sign = "+" if total_pnl >= 0 else ""
        print(f"  {'TOTAL':<12}{'':<8}{'':<14}{_fmt(total_mv):<16}{pnl_sign}{_fmt(total_pnl)}")
        print()

        if audit:
            print("Audit mode — no positions closed.\n")
            return

        # ── Confirm and close ──────────────────────────────────────────────
        if not force:
            confirm = input("Close ALL positions immediately? (yes/no): ").strip().lower()
            if confirm != "yes":
                print("Cancelled — no positions closed.\n")
                return

        print("\nClosing all positions...")
        client.close_all_positions(cancel_orders=True)
        print("✅ All positions closed. Open orders also cancelled.\n")

        # ── Verify ─────────────────────────────────────────────────────────
        import time
        time.sleep(2)
        remaining = client.get_all_positions()
        if remaining:
            print(f"⚠️  {len(remaining)} position(s) still showing — may be mid-fill, re-run in 30s")
        else:
            print("✅ Verified: 0 open positions remaining.\n")

    except ImportError:
        print("ERROR: alpaca-py not installed. Run: pip install alpaca-py")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
