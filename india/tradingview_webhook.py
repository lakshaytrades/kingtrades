"""
tradingview_webhook.py — TradingView webhook receiver for PSEB India Bot

Listens on INDIA_WEBHOOK_PORT (default 8888) for POST /tv alerts.

TradingView alert message format (set in TradingView alert → "Message" field):
  {"symbol": "{{ticker}}", "action": "{{strategy.order.action}}", "qty": 10, "price": {{close}}, "message": "{{strategy.order.comment}}"}

Or simple text format:
  BUY RELIANCE 10 2850
  SELL TCS 5

Security: Set INDIA_WEBHOOK_SECRET in .env — include as ?secret=YOUR_SECRET in webhook URL.
Webhook URL to enter in TradingView: http://YOUR_VPS_IP:8888/tv?secret=YOUR_SECRET
"""

import json
import logging
import os
import queue
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("tv_webhook")
IST = ZoneInfo("Asia/Kolkata")

WEBHOOK_PORT   = int(os.getenv("INDIA_WEBHOOK_PORT",   "8888"))
WEBHOOK_SECRET = os.getenv("INDIA_WEBHOOK_SECRET", "")

# Thread-safe queue: pending orders from TradingView
pending_queue: queue.Queue = queue.Queue(maxsize=10)


def _parse_tv_payload(body: str) -> Optional[dict]:
    """
    Parse TradingView alert body into a pending order dict.
    Supports JSON format and plain-text "BUY RELIANCE 10 2850".
    Returns None on parse failure.
    """
    body = body.strip()
    if not body:
        return None

    # Try JSON first
    try:
        data = json.loads(body)
        symbol = str(data.get("symbol", data.get("ticker", ""))).upper()
        symbol = symbol.replace("NSE:", "").replace(".NS", "")
        action = str(data.get("action", data.get("side", ""))).upper()
        if action in ("BUY", "LONG"):
            direction = "LONG"
        elif action in ("SELL", "SHORT"):
            direction = "SHORT"
        else:
            return None
        qty    = int(float(data.get("qty", data.get("quantity", 0))))
        price  = float(data.get("price", data.get("close", 0)))
        msg    = str(data.get("message", data.get("comment", "")))[:200]
        if not symbol or qty <= 0:
            return None
        return {"symbol": symbol, "direction": direction, "qty": qty,
                "price": price, "message": msg, "source": "TradingView"}
    except (json.JSONDecodeError, Exception):
        pass

    # Try plain text: "BUY RELIANCE 10 2850" or "SELL TCS 5"
    try:
        parts = body.upper().split()
        if len(parts) < 3:
            return None
        action_txt, sym, qty_txt = parts[0], parts[1], parts[2]
        if action_txt in ("BUY", "LONG"):
            direction = "LONG"
        elif action_txt in ("SELL", "SHORT"):
            direction = "SHORT"
        else:
            return None
        qty = int(qty_txt)
        price = float(parts[3]) if len(parts) >= 4 else 0.0
        return {"symbol": sym, "direction": direction, "qty": qty,
                "price": price, "message": "TradingView alert", "source": "TradingView"}
    except Exception:
        return None


class _TVHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        # Validate secret token if configured
        if WEBHOOK_SECRET:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            provided = qs.get("secret", [""])[0]
            if provided != WEBHOOK_SECRET:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8", errors="replace")

        order = _parse_tv_payload(body)
        if order:
            try:
                pending_queue.put_nowait(order)
                logger.info(f"[TV] Queued: {order['direction']} {order['qty']} {order['symbol']} @ {order['price']}")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            except queue.Full:
                logger.warning("[TV] Queue full — ignoring alert")
                self.send_response(429)
                self.end_headers()
                self.wfile.write(b"Queue full")
        else:
            logger.warning(f"[TV] Could not parse body: {body[:200]}")
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Bad Request - could not parse")

    def do_GET(self):
        # Health check
        self.send_response(200)
        self.end_headers()
        now_ist = datetime.now(IST).strftime("%H:%M IST")
        self.wfile.write(f"PSEB India Webhook — {now_ist}".encode())

    def log_message(self, fmt, *args):
        logger.debug(f"[TV HTTP] {fmt % args}")


def start_webhook_server() -> threading.Thread:
    """Start webhook server in a daemon thread. Returns the thread."""
    server = HTTPServer(("0.0.0.0", WEBHOOK_PORT), _TVHandler)

    def _run():
        logger.info(f"[TV] Webhook server listening on port {WEBHOOK_PORT}")
        server.serve_forever()

    t = threading.Thread(target=_run, daemon=True, name="tv-webhook")
    t.start()
    return t
