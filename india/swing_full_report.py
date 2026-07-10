"""
swing_full_report.py — ONE report, EVERY situation: MTF leverage x edge-strength
x capital x market regime, all from the validated base strategy.

Base (validated on live market data, new_edge_lab 2026-07-08, rev:buy-5%-day):
  +1.49%/mo | 60% WR | 4.9% backtest DD | PF 1.53 | ~5 trades/mo | ~2.5-day hold
This does NOT need a token — it stress-projects the validated edge across every
leverage/regime so you can see the whole risk surface before committing money.

  python3 india/swing_full_report.py
"""
from __future__ import annotations
import argparse

BASE_RET_MO = 0.0149
BASE_DD     = 0.049
WR          = 0.60
TRADES_MO   = 5
HOLD_DAYS   = 2.5
LEVS        = [1.0, 1.25, 1.5, 1.75, 2.0]     # 2.0 = hard cap in the bot


def interest_mo(L, rate, hold_days):
    deployed = min(TRADES_MO * hold_days / 30.0, 1.0)
    return (L - 1.0) * rate / 12.0 * deployed


def net_mo(L, rate, haircut, hold_days=HOLD_DAYS):
    return L * BASE_RET_MO * haircut - interest_mo(L, rate, hold_days)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=0.16, help="MTF interest %/yr")
    ap.add_argument("--capital", type=float, default=1000000, help="account size ₹")
    args = ap.parse_args()
    R = args.rate

    def line(w="="): print(w * 78)

    line(); print("  SWING FULL REPORT — every situation (validated base +1.49%/mo)")
    print(f"  MTF interest {R*100:.0f}%/yr | hold {HOLD_DAYS}d | {TRADES_MO} trades/mo | "
          f"WR {WR*100:.0f}%"); line()

    # 1) LEVERAGE x EDGE-STRENGTH (the core risk surface)
    print("\n  1) NET RETURN/MONTH — leverage vs how much of the edge survives live")
    print("     (backtests overstate; 'live 70%' is the realistic planning case)")
    print(f"     {'lever':>6} {'full 100%':>10} {'live 70%':>10} {'weak 50%':>10} "
          f"{'edge=0':>9} {'≈DD live':>9}")
    for L in LEVS:
        row = [f"{net_mo(L,R,h)*100:>+9.2f}%" for h in (1.0, 0.7, 0.5)]
        z = interest_mo(L, R, HOLD_DAYS)      # edge=0 -> you just pay interest
        dd = L * BASE_DD * 2.0
        print(f"     {L:>5.2f}x {row[0]:>10} {row[1]:>10} {row[2]:>10} "
              f"{-z*100:>+8.2f}% {dd*100:>8.1f}%")
    print("     Read: at edge=0, leverage only LOSES (interest). Leverage is a")
    print("     multiplier on a REAL edge — prove it live before turning it up.")

    # 2) ₹/MONTH by CAPITAL x LEVERAGE (live-70% planning case)
    print(f"\n  2) ₹/MONTH by account size x leverage (realistic live-70% case, "
          f"interest {R*100:.0f}%)")
    caps = [100000, 200000, 500000, 1000000]
    print(f"     {'capital':>10} " + " ".join(f"{str(L)+'x':>10}" for L in LEVS))
    for c in caps:
        cells = " ".join(f"₹{c*net_mo(L,R,0.7)/1:>8,.0f}"[:10].rjust(10) for L in LEVS)
        print(f"     ₹{c:>8,} " + " ".join(
            f"₹{c*net_mo(L,R,0.7):>8,.0f}" for L in LEVS))

    # 3) MARKET-REGIME situations (per month, at chosen capital)
    print(f"\n  3) SITUATION MONTHS on ₹{args.capital:,.0f} — what each leverage does")
    C = args.capital
    regimes = [("great month (+3%)", 0.03), ("normal (+1.5%)", 0.015),
               ("flat (0%)", 0.0), ("bad month (-2%)", -0.02),
               ("drawdown month (-5%)", -0.05)]
    print(f"     {'regime':<22} " + " ".join(f"{str(L)+'x':>11}" for L in LEVS))
    for name, base_r in regimes:
        cells = []
        for L in LEVS:
            r = L * base_r - interest_mo(L, R, HOLD_DAYS)
            cells.append(f"₹{C*r:>9,.0f}")
        print(f"     {name:<22} " + " ".join(f"{x:>11}" for x in cells))
    print("     A -5% base month at 2x = -10% + interest — that's your worst-case.")

    # 4) INTEREST sensitivity on the 2x case
    print("\n  4) INTEREST SENSITIVITY (2x MTF, live-70% edge)")
    print(f"     {'rate/yr':>8} {'net/mo':>9} {'vs 1x':>10}")
    base1x = net_mo(1.0, R, 0.7)
    for rt in (0.12, 0.16, 0.18, 0.24):
        n = net_mo(2.0, rt, 0.7)
        print(f"     {rt*100:>7.0f}% {n*100:>+8.2f}% {(n-base1x)*100:>+9.2f}%")

    # 4b) THE DEPLOYMENT GRID — ₹1/2/5/10L x (1x, 2x), everything in one place
    print("\n  5) DEPLOYMENT GRID — ₹1L/2L/5L/10L at 1x and 2x MTF "
          f"(WR 60%, live-70% edge, {R*100:.0f}% interest)")
    grid_caps = [100000, 200000, 500000, 1000000]
    print(f"     {'capital':>9} | {'1x ₹/mo':>9} {'1x DD':>9} {'1x /yr':>9} | "
          f"{'2x ₹/mo':>9} {'2x DD':>10} {'2x /yr':>9}")
    for c in grid_caps:
        n1 = net_mo(1.0, R, 0.7); n2 = net_mo(2.0, R, 0.7)
        dd1 = c * 1.0 * BASE_DD * 2.0; dd2 = c * 2.0 * BASE_DD * 2.0
        y1 = c * ((1 + n1) ** 12 - 1); y2 = c * ((1 + n2) ** 12 - 1)
        print(f"     ₹{c:>7,} | ₹{c*n1:>7,.0f} ₹{-dd1:>7,.0f} ₹{y1:>7,.0f} | "
              f"₹{c*n2:>7,.0f} ₹{-dd2:>8,.0f} ₹{y2:>7,.0f}")
    print("     DD = worst drawdown you must sit through (money, not %). 2x doubles")
    print("     both the yearly gain AND the drawdown. WR is 60% at every size/lever.")

    # 5) VERDICT
    print("\n" + "=" * 78)
    print("  VERDICT — the whole surface in three lines")
    print("=" * 78)
    print("  * 1x (no MTF): +1.0%/mo live-realistic, ~10% DD — DEPLOY THIS FIRST.")
    print("  * 2x MTF: ~+1.5-2.0%/mo IF the edge holds, but ~20% DD and it loses")
    print("    money if the edge is weak/zero. Turn on ONLY after live proof.")
    print("  * >2x: blocked in code — drawdown breaks the kill-rules. Non-negotiable.")
    print("=" * 78)


if __name__ == "__main__":
    main()
