"""
mtf_analysis.py — HONEST economics of using MTF (leverage) on the swing strategy.

MTF (Margin Trading Facility) lets you hold delivery positions with borrowed
money and pay interest per day held. Unlike the intraday bot (where MTF made no
sense — it's a delivery product), MTF genuinely fits a 2-4 day swing: you only
pay interest for the few days a position is open.

This models what MTF does to the VALIDATED base strategy (rev:buy-5%-day:
+1.49%/mo, 60% WR, 4.9% DD, PF 1.53, ~5 trades/mo) at leverage 1x..4x:
  net_ret = L * base_ret  -  interest_on_borrowed  -  extra_brokerage
  drawdown ~ L * base_dd            (leverage multiplies losses 1:1)
Interest is charged only for days actually held (the key MTF-for-swing insight).

  python3 india/mtf_analysis.py
  python3 india/mtf_analysis.py --rate 0.18 --hold-days 3
"""
from __future__ import annotations
import argparse

# validated base strategy (SWING_SYSTEM.md)
BASE_RET_MO   = 0.0149     # +1.49%/mo on unleveraged capital
BASE_DD       = 0.049      # 4.9% backtest max drawdown
TRADES_MO     = 5
SLOTS         = 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=0.16,
                    help="MTF annual interest on borrowed (Upstox ~16-18%%)")
    ap.add_argument("--hold-days", type=float, default=2.5,
                    help="avg calendar days a position is held")
    ap.add_argument("--live-haircut", type=float, default=0.7,
                    help="live-vs-backtest haircut on the edge (0.7 = expect 70%%)")
    args = ap.parse_args()

    # fraction of the month capital is actually deployed (=> interest is only
    # paid on that fraction). trades/mo * hold_days, spread over ~30 cal days,
    # capped at full deployment.
    deployed_frac = min(TRADES_MO * args.hold_days / 30.0, 1.0)

    print("=" * 74)
    print("  MTF ON THE SWING STRATEGY — honest leverage economics")
    print(f"  base +{BASE_RET_MO*100:.2f}%/mo, {BASE_DD*100:.1f}% DD | "
          f"interest {args.rate*100:.0f}%/yr | avg hold {args.hold_days}d | "
          f"deployed ~{deployed_frac*100:.0f}% of month")
    print("=" * 74)
    print(f"  {'lever':>6} {'exposure':>10} {'gross/mo':>9} {'interest':>9} "
          f"{'NET/mo':>8} {'≈DD live':>9}  verdict")

    for L in (1.0, 2.0, 3.0, 4.0):
        gross = L * BASE_RET_MO
        borrowed = (L - 1.0)                      # as multiple of capital
        # interest is paid only for days actually held: annual rate, scaled to
        # a month, times the fraction of the month the borrowed money is deployed
        interest_mo = borrowed * args.rate / 12.0 * deployed_frac
        net = gross - interest_mo
        dd_live = L * BASE_DD * 2.0               # 2x haircut: backtest DD -> live
        verdict = ("solid" if L == 1 else
                   "OK if edge proven" if dd_live < 0.20 else
                   "BREAKS 10-20% kill-rule" if dd_live < 0.35 else
                   "account-threatening")
        print(f"  {L:>5.1f}x {L:>9.0%} {gross*100:>+8.2f}% {interest_mo*100:>8.2f}% "
              f"{net*100:>+7.2f}% {dd_live*100:>8.1f}%  {verdict}")

    # live-haircut view on the 2x case (the only sane MTF level)
    L = 2.0
    gross = L * BASE_RET_MO * args.live_haircut
    interest_mo = (L - 1) * args.rate / 12.0 * deployed_frac
    net = gross - interest_mo
    print("\n  REALITY CHECK — 2x MTF with a live haircut "
          f"({args.live_haircut:.0%} of backtest edge):")
    print(f"    net ~{net*100:+.2f}%/mo  (vs {BASE_RET_MO*args.live_haircut*100:+.2f}%/mo "
          f"unleveraged) — DD ~{L*BASE_DD*2*100:.0f}% live")
    print(f"    If the live edge is ZERO, 2x MTF LOSES the interest "
          f"(~{interest_mo*100:.2f}%/mo) — leverage amplifies a wrong bet too.")

    print("\n" + "=" * 74)
    print("  VERDICT")
    print("=" * 74)
    print("  * MTF DOES fit short-hold swing (interest only for days held) — at 2x")
    print("    it can lift ~1.5%/mo toward ~2.4%/mo IF the edge holds live.")
    print("  * BUT leverage multiplies the DRAWDOWN 1:1 (2x -> ~20% live) and")
    print("    amplifies a WRONG bet — and this edge is NOT yet confirmed live.")
    print("  * PROFESSIONAL PATH: run the base strategy UNLEVERAGED for 1-2 months")
    print("    first. Only after it's live-profitable, add at most 2x MTF. Above")
    print("    2x the drawdown breaks your own kill-rules.")
    print("=" * 74)


if __name__ == "__main__":
    main()
