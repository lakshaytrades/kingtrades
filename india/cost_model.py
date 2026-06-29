"""
cost_model.py — exact Groww intraday equity round-trip cost calculator.

WHY
---
The whole deploy decision now hinges on ONE number: the real round-trip cost.
strategy_lab showed RANGE_BREAK_HOLD on mid-caps is:
    -8.83%/mo at 0.45% cost   (modeled, conservative)
    +5.12%/mo at 0.20% cost   (break-even ~0.27%)
The engine's COST_RT_PCT=0.0045 assumes ~0.1% slippage AND percentage brokerage.
But Groww caps intraday brokerage at Rs.20/order, so on a decent position size the
brokerage % collapses and the true all-in cost can fall well below 0.45%.

This computes the EXACT all-in round-trip cost (% of position value) for a given
position size and slippage, using Groww's published intraday equity fee schedule,
so we know which side of the break-even line you're really on.

This does NOT change the engine constant (COST_RT_PCT stays 0.0045 per CLAUDE.md).
It tells you the realistic --cost to pass to strategy_lab for an honest re-test.

Usage:
  python3 india/cost_model.py --value 100000 --slippage 0.05
  python3 india/cost_model.py --value 200000 --slippage 0.08
"""

from __future__ import annotations
import argparse

# Groww intraday equity fee schedule (NSE), as published 2024-25.
BROKERAGE_PCT   = 0.001       # 0.1% per order ...
BROKERAGE_CAP   = 20.0        # ... capped at Rs.20 per executed order
STT_SELL        = 0.00025     # 0.025% on SELL side only (intraday)
EXCH_TXN        = 0.0000297   # NSE transaction charge, each side
SEBI            = 0.000001    # Rs.10 per crore = 0.0001%, each side
STAMP_BUY       = 0.00003     # 0.003% on BUY side only
GST             = 0.18        # 18% on (brokerage + exchange txn + SEBI)


def round_trip_cost(value: float, slippage_pct_per_side: float) -> dict:
    """Return rupee + percentage breakdown of a round-trip intraday trade."""
    brokerage = 2 * min(BROKERAGE_PCT * value, BROKERAGE_CAP)   # buy + sell
    stt       = STT_SELL * value
    exch      = EXCH_TXN * value * 2
    sebi      = SEBI * value * 2
    stamp     = STAMP_BUY * value
    gst       = GST * (brokerage + exch + sebi)
    slippage  = (slippage_pct_per_side / 100.0) * value * 2       # both sides
    fees      = brokerage + stt + exch + sebi + stamp + gst
    total     = fees + slippage
    return {
        "brokerage": brokerage, "stt": stt, "exch": exch, "sebi": sebi,
        "stamp": stamp, "gst": gst, "slippage": slippage,
        "fees_only": fees, "total": total,
        "fees_pct": fees / value * 100,
        "total_pct": total / value * 100,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--value", type=float, default=100000, help="position value Rs")
    ap.add_argument("--slippage", type=float, default=0.05,
                    help="slippage %% per side (liquid names ~0.03-0.08)")
    args = ap.parse_args()

    print("=" * 64)
    print(f"  Groww intraday round-trip cost — position Rs.{args.value:,.0f}")
    print(f"  slippage assumption: {args.slippage:.2f}% per side")
    print("=" * 64)
    c = round_trip_cost(args.value, args.slippage)
    print(f"  Brokerage (2x, Rs.20 cap) : Rs.{c['brokerage']:>8.2f}")
    print(f"  STT (sell)                : Rs.{c['stt']:>8.2f}")
    print(f"  Exchange txn (2x)         : Rs.{c['exch']:>8.2f}")
    print(f"  SEBI (2x)                 : Rs.{c['sebi']:>8.2f}")
    print(f"  Stamp (buy)               : Rs.{c['stamp']:>8.2f}")
    print(f"  GST (18%)                 : Rs.{c['gst']:>8.2f}")
    print(f"  Slippage (2x)             : Rs.{c['slippage']:>8.2f}")
    print("  " + "-" * 40)
    print(f"  Fees only   : Rs.{c['fees_only']:>8.2f}   ({c['fees_pct']:.3f}%)")
    print(f"  ALL-IN total: Rs.{c['total']:>8.2f}   ({c['total_pct']:.3f}%)  <- round-trip cost")
    print("=" * 64)
    print(f"\n  -> Re-test the strategy at YOUR real cost:")
    print(f"     python3 india/strategy_lab.py --cache midcap_cache.pkl "
          f"--cost {c['total_pct']/100:.4f}")
    print(f"\n  Sweep position sizes (fees % drops as size rises, until Rs.20 cap binds):")
    print(f"  {'Value':>10} {'Fees%':>8} {'+slip':>8} {'All-in%':>9}")
    for v in [25_000, 50_000, 100_000, 200_000, 300_000, 500_000]:
        cc = round_trip_cost(v, args.slippage)
        print(f"  {v:>10,} {cc['fees_pct']:>7.3f}% {args.slippage*2:>6.2f}% "
              f"{cc['total_pct']:>8.3f}%")
    print("\n  Note: bigger positions => lower fee %, but bigger absolute risk.")
    print("  RANGE_BREAK_HOLD on mid-caps was +5.1%/mo at 0.20% and -8.8%/mo at 0.45%.")
    print("  If your all-in lands <0.27%, re-test says profit; if >0.27%, it loses.")


if __name__ == "__main__":
    main()
