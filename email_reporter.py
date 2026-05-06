"""
email_reporter.py — Daily Trading Report via Email + Discord
Sends a beautiful HTML email every evening so you wake up and read what the bot did.

Setup (.env) — choose ONE email method:

  METHOD 1 — Brevo API (recommended — works on all VPS, free 300 emails/day)
    BREVO_API_KEY = your-brevo-api-key
    Get free key: brevo.com → signup → SMTP & API → API Keys

  METHOD 2 — Gmail SMTP (only if your VPS allows port 465/587)
    GMAIL_SENDER       = bot@gmail.com
    GMAIL_APP_PASSWORD = xxxx xxxx xxxx xxxx

  BOTH methods:
    REPORT_EMAIL = your@gmail.com   (where to RECEIVE reports)
"""

import logging
import os
import smtplib
import json
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional, List, Dict
from zoneinfo import ZoneInfo

import requests

from utils import format_ist_timestamp, get_current_ist_time

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────────────────────
# HTML Email Template
# ─────────────────────────────────────────────────────────────────────────────

def _build_html_report(data: Dict) -> str:
    date        = data.get("date", "")
    pnl         = data.get("total_pnl", 0.0)
    trades      = data.get("trades", [])
    win_count   = data.get("wins", 0)
    loss_count  = data.get("losses", 0)
    total       = data.get("total_trades", 0)
    win_rate    = (win_count / total * 100) if total > 0 else 0
    capital     = data.get("starting_capital", 0)
    pnl_pct     = (pnl / capital * 100) if capital > 0 else 0
    login_ok    = data.get("login_ok", True)
    market_days = data.get("market_day", True)

    pnl_color   = "#00b894" if pnl >= 0 else "#d63031"
    pnl_sign    = "+" if pnl >= 0 else ""
    status_icon = "✅" if pnl >= 0 else "⚠️"

    trade_rows = ""
    for t in trades:
        t_pnl = t.get("pnl", 0)
        t_color = "#00b894" if t_pnl >= 0 else "#d63031"
        t_sign  = "+" if t_pnl >= 0 else ""
        trade_rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0">{t.get('symbol','')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0">{t.get('direction','')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0">₹{t.get('entry_price',0):,.2f}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0">₹{t.get('exit_price',0):,.2f}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0">{t.get('qty',0)}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;color:{t_color};font-weight:bold">{t_sign}₹{t_pnl:,.0f}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0">{t.get('reason','')}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:11px;color:#888">{t.get('entry_time','')} → {t.get('exit_time','')}</td>
        </tr>"""

    no_trades_msg = ""
    if not trades:
        reason = "No signals met the quality threshold (min score 72/100)" if market_day else "Market closed / holiday"
        no_trades_msg = f"""
        <tr><td colspan="8" style="padding:20px;text-align:center;color:#888">{reason}</td></tr>"""

    login_status = (
        '<span style="color:#00b894">✅ Groww login successful</span>'
        if login_ok else
        '<span style="color:#d63031">❌ Groww login failed — no orders placed</span>'
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background:#f8f9fa; margin:0; padding:20px; color:#2d3436; }}
  .card {{ background:#fff; border-radius:12px; padding:24px; margin-bottom:16px;
           box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
  .stat {{ display:inline-block; margin:0 24px 0 0; }}
  .stat-val {{ font-size:28px; font-weight:700; }}
  .stat-lbl {{ font-size:12px; color:#888; margin-top:2px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ background:#f8f9fa; padding:10px 12px; text-align:left;
        font-size:11px; color:#888; text-transform:uppercase; letter-spacing:.5px; }}
  .pill {{ display:inline-block; padding:2px 8px; border-radius:20px;
           font-size:11px; font-weight:600; }}
  .green {{ background:#e8f8f4; color:#00b894; }}
  .red {{ background:#fff0f0; color:#d63031; }}
</style>
</head>
<body>
<div style="max-width:700px;margin:0 auto">

  <!-- Header -->
  <div class="card" style="background:linear-gradient(135deg,#2d3436,#636e72);color:#fff">
    <div style="font-size:12px;opacity:.7;margin-bottom:4px">NSE MOMENTUM BOT — DAILY REPORT</div>
    <div style="font-size:22px;font-weight:700">{status_icon} {date}</div>
    <div style="margin-top:4px;font-size:13px;opacity:.8">{login_status}</div>
  </div>

  <!-- P&L Summary -->
  <div class="card">
    <div style="font-size:13px;color:#888;margin-bottom:16px">TODAY'S PERFORMANCE</div>
    <div>
      <div class="stat">
        <div class="stat-val" style="color:{pnl_color}">{pnl_sign}₹{pnl:,.0f}</div>
        <div class="stat-lbl">Net P&L</div>
      </div>
      <div class="stat">
        <div class="stat-val" style="color:{pnl_color}">{pnl_sign}{pnl_pct:.2f}%</div>
        <div class="stat-lbl">Return on Capital</div>
      </div>
      <div class="stat">
        <div class="stat-val">{total}</div>
        <div class="stat-lbl">Total Trades</div>
      </div>
      <div class="stat">
        <div class="stat-val" style="color:#00b894">{win_count}W</div>
        <div class="stat-lbl">Wins</div>
      </div>
      <div class="stat">
        <div class="stat-val" style="color:#d63031">{loss_count}L</div>
        <div class="stat-lbl">Losses</div>
      </div>
      <div class="stat">
        <div class="stat-val">{win_rate:.0f}%</div>
        <div class="stat-lbl">Win Rate</div>
      </div>
    </div>
  </div>

  <!-- Trade Log -->
  <div class="card">
    <div style="font-size:13px;color:#888;margin-bottom:16px">TRADE LOG</div>
    <table>
      <thead>
        <tr>
          <th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th>
          <th>Qty</th><th>P&L</th><th>Exit Reason</th><th>Time</th>
        </tr>
      </thead>
      <tbody>
        {trade_rows or no_trades_msg}
      </tbody>
    </table>
  </div>

  <!-- Footer -->
  <div style="text-align:center;font-size:11px;color:#aaa;padding:8px">
    NSE Momentum Bot · Auto-generated {format_ist_timestamp()} IST ·
    All trades MIS (intraday, no overnight positions)
  </div>

</div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Send via Gmail
# ─────────────────────────────────────────────────────────────────────────────

def _send_via_brevo(to_addr: str, subject: str, html_body: str, text_body: str = "") -> bool:
    api_key = os.getenv("BREVO_API_KEY", "")
    if not api_key:
        return False
    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={
                "sender": {"name": "KingTrades Bot", "email": "noreply@kingtrades.bot"},
                "to": [{"email": to_addr}],
                "subject": subject,
                "htmlContent": html_body,
                "textContent": text_body or subject,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info(f"[{format_ist_timestamp()}] Email sent via Brevo to {to_addr}")
            return True
        logger.warning(f"[{format_ist_timestamp()}] Brevo error {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"[{format_ist_timestamp()}] Brevo failed: {e}")
        return False


def _send_via_gmail(to_addr: str, from_addr: str, app_pwd: str,
                    subject: str, html_body: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = from_addr
        msg["To"]      = to_addr
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(from_addr, app_pwd)
            smtp.sendmail(from_addr, to_addr, msg.as_string())
        logger.info(f"[{format_ist_timestamp()}] Email sent via Gmail to {to_addr}")
        return True
    except Exception as e:
        logger.warning(f"[{format_ist_timestamp()}] Gmail SMTP failed: {e}")
        return False


def _send_email(to_addr: str, subject: str, html_body: str, text_body: str = "") -> bool:
    # Try Brevo first (works on all VPS — uses HTTPS port 443)
    if _send_via_brevo(to_addr, subject, html_body, text_body):
        return True
    # Fallback: Gmail SMTP (works if VPS allows port 465)
    from_addr = os.getenv("GMAIL_SENDER", to_addr)
    app_pwd   = os.getenv("GMAIL_APP_PASSWORD", "")
    if from_addr and app_pwd:
        return _send_via_gmail(to_addr, from_addr, app_pwd, subject, html_body)
    return False


def send_email_report(data: Dict) -> bool:
    to_addr = os.getenv("REPORT_EMAIL", "")
    if not to_addr:
        logger.debug("Email report skipped — REPORT_EMAIL not set")
        return False
    try:
        date    = data.get("date", get_current_ist_time().strftime("%d %b %Y"))
        pnl     = data.get("total_pnl", 0.0)
        sign    = "+" if pnl >= 0 else ""
        subject = f"[KingTrades] {sign}Rs.{pnl:,.0f} — {date}"
        return _send_email(to_addr, subject, _build_html_report(data))
    except Exception as e:
        logger.error(f"[{format_ist_timestamp()}] Email report failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Real-time trade alert via email (entry / exit / error)
# ─────────────────────────────────────────────────────────────────────────────

def send_alert(subject: str, body: str) -> None:
    """Instant email alert for trade entries, exits, errors. Fire-and-forget."""
    to_addr = os.getenv("REPORT_EMAIL", "")
    if not to_addr:
        return
    try:
        html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;padding:20px;color:#2d3436">
<div style="max-width:500px;background:#fff;border-radius:10px;padding:20px;
     box-shadow:0 2px 8px rgba(0,0,0,.07)">
<pre style="font-family:monospace;font-size:14px;white-space:pre-wrap;margin:0">{body}</pre>
<div style="margin-top:16px;font-size:11px;color:#aaa">{format_ist_timestamp()} IST · KingTrades Bot</div>
</div></body></html>"""
        _send_email(to_addr, f"[KingTrades] {subject}", html, body)

    except Exception:
        pass  # Never block trading for an alert failure


# ─────────────────────────────────────────────────────────────────────────────
# Send via Discord webhook
# ─────────────────────────────────────────────────────────────────────────────

def send_discord_report(data: Dict) -> bool:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return False

    try:
        pnl       = data.get("total_pnl", 0.0)
        trades    = data.get("trades", [])
        wins      = data.get("wins", 0)
        losses    = data.get("losses", 0)
        total     = data.get("total_trades", 0)
        capital   = data.get("starting_capital", 0)
        pnl_pct   = (pnl / capital * 100) if capital > 0 else 0
        date      = data.get("date", "")
        login_ok  = data.get("login_ok", True)
        sign      = "+" if pnl >= 0 else ""
        color     = 0x00b894 if pnl >= 0 else 0xd63031
        emoji     = "📈" if pnl >= 0 else "📉"

        trade_lines = []
        for t in trades:
            t_pnl = t.get("pnl", 0)
            t_sign = "+" if t_pnl >= 0 else ""
            trade_lines.append(
                f"`{t.get('symbol',''):<12}` {t.get('direction',''):<5} "
                f"₹{t.get('entry_price',0):>8,.1f} → ₹{t.get('exit_price',0):>8,.1f} "
                f"**{t_sign}₹{t_pnl:,.0f}** ({t.get('reason','')})"
            )

        trades_text = "\n".join(trade_lines) if trade_lines else "_No trades today — no signals met quality threshold_"

        embed = {
            "title": f"{emoji} KingTrades Daily Report — {date}",
            "color": color,
            "fields": [
                {"name": "Net P&L",     "value": f"**{sign}₹{pnl:,.0f}** ({sign}{pnl_pct:.2f}%)", "inline": True},
                {"name": "Trades",      "value": f"{total} ({wins}W / {losses}L)", "inline": True},
                {"name": "Win Rate",    "value": f"{(wins/total*100):.0f}%" if total else "—", "inline": True},
                {"name": "Groww Login", "value": "✅ OK" if login_ok else "❌ Failed", "inline": True},
                {"name": "Trade Log",   "value": trades_text[:1000], "inline": False},
            ],
            "footer": {"text": f"All trades MIS intraday · Generated {format_ist_timestamp()} IST"},
        }

        resp = requests.post(webhook, json={"embeds": [embed]}, timeout=10)
        if resp.status_code in (200, 204):
            logger.info(f"[{format_ist_timestamp()}] Discord report sent")
            return True
        logger.warning(f"[{format_ist_timestamp()}] Discord webhook: HTTP {resp.status_code}")
        return False

    except Exception as e:
        logger.error(f"[{format_ist_timestamp()}] Discord send failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main: send all reports
# ─────────────────────────────────────────────────────────────────────────────

def send_eod_reports(journal=None, risk_manager=None, login_ok: bool = True) -> None:
    """
    Call this at EOD (4 PM IST). Pulls trade data from journal,
    sends email + Discord reports.
    """
    now   = get_current_ist_time()
    date  = now.strftime("%d %b %Y")
    today = now.strftime("%Y-%m-%d")

    trades    = []
    total_pnl = 0.0
    wins      = 0
    losses    = 0
    capital   = 0.0

    # Pull from risk manager
    if risk_manager:
        try:
            capital   = risk_manager.daily_starting_capital
            total_pnl = risk_manager.state.daily_pnl
        except Exception:
            pass

    # Pull trade log from journal (SQLite)
    if journal:
        try:
            rows = journal.get_today_trades(today)
            for r in (rows or []):
                t_pnl = float(r.get("pnl", 0))
                total_pnl_row = t_pnl
                trades.append({
                    "symbol":      r.get("symbol", ""),
                    "direction":   r.get("direction", ""),
                    "entry_price": float(r.get("entry_price", 0)),
                    "exit_price":  float(r.get("exit_price", 0)),
                    "qty":         int(r.get("quantity", 0)),
                    "pnl":         t_pnl,
                    "reason":      r.get("exit_reason", ""),
                    "entry_time":  r.get("entry_time", "")[-8:-3] if r.get("entry_time") else "",
                    "exit_time":   r.get("exit_time", "")[-8:-3] if r.get("exit_time") else "",
                })
                if t_pnl >= 0:
                    wins += 1
                else:
                    losses += 1
        except Exception as e:
            logger.warning(f"[{format_ist_timestamp()}] Could not load journal trades: {e}")

    data = {
        "date":              date,
        "total_pnl":         total_pnl,
        "trades":            trades,
        "total_trades":      len(trades),
        "wins":              wins,
        "losses":            losses,
        "starting_capital":  capital,
        "login_ok":          login_ok,
        "market_day":        True,
    }

    # Save JSON snapshot for web/debugging
    try:
        Path("data").mkdir(exist_ok=True)
        Path(f"data/report_{today}.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass

    # Send
    email_ok   = send_email_report(data)
    discord_ok = send_discord_report(data)

    logger.info(
        f"[{format_ist_timestamp()}] EOD reports — "
        f"email={'sent' if email_ok else 'skipped'} "
        f"discord={'sent' if discord_ok else 'skipped'}"
    )
