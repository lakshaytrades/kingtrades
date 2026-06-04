"""
auth_dhan.py — Dhan API authentication
Dhan uses a long-lived access token (no TOTP needed).
Token is set in DHAN_ACCESS_TOKEN env var and refreshed manually
from the Dhan portal when it expires (~30 days).
"""
import logging
import os
from typing import Optional

logger = logging.getLogger("auth_dhan")


def get_dhan_client():
    """
    Initialize and return a DhanHQ client instance.
    Returns None if credentials are missing or SDK not installed.
    Token is read from env at runtime — never stored on disk.
    """
    client_id    = os.getenv("DHAN_CLIENT_ID", "")
    access_token = os.getenv("DHAN_ACCESS_TOKEN", "")

    if not client_id or not access_token:
        logger.error("DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN not set in .env")
        return None

    try:
        from dhanhq import dhanhq
        client = dhanhq(client_id, access_token)
        logger.info(f"Dhan client ready (client_id: {client_id[:6]}***)")
        return client
    except ImportError:
        logger.error("dhanhq not installed — run: pip install dhanhq")
        return None
    except Exception as e:
        logger.error(f"Dhan client init failed: {e}")
        return None


def verify_connection(client) -> bool:
    """Ping Dhan API to confirm credentials are valid."""
    if client is None:
        return False
    try:
        result = client.get_fund_limits()
        if result and result.get("status") == "success":
            limits = result.get("data", {})
            balance = limits.get("availabelBalance", 0)
            logger.info(f"Dhan connected — available balance: ₹{balance:,.2f}")
            return True
        logger.warning(f"Dhan fund_limits unexpected response: {result}")
        return False
    except Exception as e:
        logger.error(f"Dhan connection verify failed: {e}")
        return False
