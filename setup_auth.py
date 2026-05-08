"""
setup_auth.py — One-stop Groww auth setup and diagnostics.

Run on your VPS:  python3 setup_auth.py
"""
import os, sys, time, hashlib, uuid, json
import logging
logging.basicConfig(level=logging.WARNING)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests
import pyotp

SEP = "=" * 60

def step(n, msg): print(f"\n[{n}] {msg}")
def ok(msg):      print(f"    ✓  {msg}")
def fail(msg):    print(f"    ✗  {msg}")
def info(msg):    print(f"       {msg}")

print()
print(SEP)
print("  GROWW AUTH SETUP & DIAGNOSTICS")
print(SEP)

# ── Step 1: Show credentials status ──────────────────────────────
step(1, "Checking .env credentials")
email         = os.getenv("GROWW_EMAIL", "")
password      = os.getenv("GROWW_PASSWORD", "")
acct_totp     = os.getenv("GROWW_TOTP_SECRET", "")
api_key_totp  = os.getenv("GROWW_API_KEY_TOTP", "")
client_id     = os.getenv("GROWW_CLIENT_ID", "")
client_secret = os.getenv("GROWW_CLIENT_SECRET", "")

ok(f"GROWW_EMAIL:          {email or 'MISSING ← add to .env'}")
ok(f"GROWW_PASSWORD:       {'set (' + str(len(password)) + ' chars)' if password else 'MISSING'}")
ok(f"GROWW_TOTP_SECRET:    {'set' if acct_totp else 'MISSING ← account 2FA TOTP'}")
ok(f"GROWW_API_KEY_TOTP:   {'set' if api_key_totp else 'not set (optional)'}")
ok(f"GROWW_CLIENT_ID:      {client_id or 'not set (optional)'}")
ok(f"GROWW_CLIENT_SECRET:  {'set (' + str(len(client_secret)) + ' chars)' if client_secret else 'not set'}")

# ── Step 2: Get server IP ─────────────────────────────────────────
step(2, "Getting this server's public IP")
server_ip = None
for ip_url in ("https://api.ipify.org", "https://icanhazip.com", "https://ifconfig.me"):
    try:
        r = requests.get(ip_url, timeout=5)
        if r.status_code == 200 and "." in r.text:
            server_ip = r.text.strip()
            ok(f"Server IP: {server_ip}")
            break
    except Exception:
        pass
if not server_ip:
    fail("Could not determine server IP (internet access issue?)")

# ── Step 3: Test Trade API with Cloud API Key ─────────────────────
step(3, "Testing Groww Trade API (Cloud API Key)")
trade_token = None
if client_id:
    totp_secret_to_use = api_key_totp or acct_totp
    if totp_secret_to_use:
        remaining = 30 - (int(time.time()) % 30)
        if remaining < 5:
            info(f"Waiting {remaining+1}s for fresh TOTP window...")
            time.sleep(remaining + 1)
        totp_code = pyotp.TOTP(totp_secret_to_use).now()
        info(f"TOTP code: {totp_code} (using {'API_KEY_TOTP' if api_key_totp else 'GROWW_TOTP_SECRET'})")

        headers = {
            "x-request-id": str(uuid.uuid4()),
            "Authorization": f"Bearer {client_id}",
            "Content-Type": "application/json",
            "x-client-id": "growwapi",
            "x-client-platform": "growwapi-python-client",
            "x-client-platform-version": "1.5.0",
            "x-api-version": "1.0",
        }

        # Try all known key_type values (Groww changes API format periodically)
        totp_bodies = [
            {"key_type": "totp", "totp": totp_code},
            {"key_type": "TOTP", "totp": totp_code},
            {"key_type": "auth-totp", "totp": totp_code},
            {"totp": totp_code},
        ]
        for body in totp_bodies:
            if trade_token:
                break
            headers["x-request-id"] = str(uuid.uuid4())
            try:
                resp = requests.post(
                    "https://api.groww.in/v1/token/api/access",
                    json=body, headers=headers, timeout=15,
                )
                info(f"body={body} → HTTP {resp.status_code}: {resp.text[:200]}")
                if resp.status_code in (200, 201):
                    data = resp.json()
                    trade_token = data.get("token") or data.get("data", {}).get("token")
                    if trade_token:
                        ok(f"Trade API TOTP token! key_type={body.get('key_type','none')}")
            except Exception as e:
                info(f"Request error: {e}")

        # Also try via SDK directly
        if not trade_token:
            info("Trying SDK get_access_token directly...")
            try:
                from growwapi import GrowwAPI
                result = GrowwAPI.get_access_token(api_key=client_id, totp=totp_code)
                if isinstance(result, str) and len(result) > 20:
                    trade_token = result
                    ok(f"SDK TOTP token! ({len(result)} chars)")
                else:
                    info(f"SDK result: {str(result)[:200]}")
            except Exception as e:
                info(f"SDK error: {e}")

        # Try approval method with secret
        if not trade_token and client_secret:
            info("Trying approval method variants...")
            ts = int(time.time())
            # SDK checksum: sha256(secret + str(timestamp))
            checksum_sdk = hashlib.sha256(f"{client_secret}{ts}".encode()).hexdigest()
            # Alt: sha256(timestamp + secret)
            checksum_alt = hashlib.sha256(f"{ts}{client_secret}".encode()).hexdigest()
            approval_bodies = [
                {"key_type": "approval", "checksum": checksum_sdk, "timestamp": ts},
                {"key_type": "approval", "checksum": checksum_alt, "timestamp": ts},
                {"key_type": "secret", "checksum": checksum_sdk, "timestamp": ts},
            ]
            for body in approval_bodies:
                if trade_token:
                    break
                headers["x-request-id"] = str(uuid.uuid4())
                try:
                    resp = requests.post(
                        "https://api.groww.in/v1/token/api/access",
                        json=body, headers=headers, timeout=15,
                    )
                    info(f"approval body={body.get('key_type')} → HTTP {resp.status_code}: {resp.text[:200]}")
                    if resp.status_code in (200, 201):
                        data = resp.json()
                        trade_token = data.get("token")
                        if trade_token:
                            ok(f"Approval token! checksum variant={approval_bodies.index(body)}")
                except Exception as e:
                    info(f"Approval error: {e}")

            # Also try SDK approval method
            if not trade_token:
                try:
                    result = GrowwAPI.get_access_token(api_key=client_id, secret=client_secret)
                    if isinstance(result, str) and len(result) > 20:
                        trade_token = result
                        ok(f"SDK approval token! ({len(result)} chars)")
                    else:
                        info(f"SDK approval result: {str(result)[:200]}")
                except Exception as e:
                    info(f"SDK approval error: {e}")
    else:
        fail("No TOTP secret — set GROWW_API_KEY_TOTP or GROWW_TOTP_SECRET")
else:
    info("GROWW_CLIENT_ID not set — skipping Trade API test")
    info("Get Cloud API Key from: Groww app → Profile → Trade API → Cloud API Keys")

# ── Step 4: Test browser login (fallback) ────────────────────────
step(4, "Testing browser login (fallback)")
browser_token = None

# Check for saved token first
manual_file_paths = ["data/.manual_token.txt", "/opt/kingtrades/data/.manual_token.txt"]
for p in manual_file_paths:
    if os.path.exists(p):
        tok = open(p).read().strip()
        if tok and len(tok) > 30:
            ok(f"Found saved browser token ({len(tok)} chars) at {p}")
            browser_token = tok
            break

if not browser_token:
    if email and password and acct_totp:
        info("Attempting headless Chrome login...")
        try:
            from browser_auth import login_via_browser
            browser_token = login_via_browser(email, password, acct_totp)
            if browser_token:
                ok(f"Browser login SUCCESS! Token: {len(browser_token)} chars")
                # Save it
                os.makedirs("data", exist_ok=True)
                with open("data/.manual_token.txt", "w") as f:
                    f.write(browser_token)
                ok("Token saved to data/.manual_token.txt")
            else:
                fail("Browser login returned no token")
        except Exception as e:
            fail(f"Browser login error: {e}")
    else:
        fail("Need GROWW_EMAIL + GROWW_PASSWORD + GROWW_TOTP_SECRET for browser login")

# ── Step 5: Test SDK with whatever token we have ─────────────────
step(5, "Testing growwapi SDK")
best_token = trade_token or browser_token
if best_token:
    info(f"Using {'Trade API' if trade_token else 'browser'} token...")
    try:
        import io
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            from growwapi import GrowwAPI
            api = GrowwAPI(best_token)
        finally:
            sys.stdout = old
        ok("SDK initialized")

        # Try to get holdings
        try:
            holdings = api.get_holdings_for_user()
            ok(f"Holdings: {json.dumps(holdings, indent=2)[:500] if holdings else 'empty'}")
        except Exception as e:
            info(f"get_holdings_for_user(): {e}")

        # Try to get positions
        try:
            positions = api.get_positions_for_user()
            ok(f"Positions: {json.dumps(positions, indent=2)[:500] if positions else 'none (market closed?)'}")
        except Exception as e:
            info(f"get_positions_for_user(): {e}")

        # Try to get margin
        try:
            margin = api.get_available_margin_details()
            ok(f"Margin: {json.dumps(margin, indent=2)[:300] if margin else 'empty'}")
        except Exception as e:
            info(f"get_available_margin_details(): {e}")

    except Exception as e:
        fail(f"SDK error: {e}")
else:
    fail("No token available — fix auth first")

# ── Summary ──────────────────────────────────────────────────────
print()
print(SEP)
if trade_token:
    print("  ✓ READY — Trade API token working. Bot will trade live!")
elif browser_token:
    print("  ⚠  PARTIAL — Browser token only.")
    print("     Data feed works. Orders may fail if browser JWT not accepted by SDK.")
    if server_ip:
        print(f"")
        print(f"  TO FIX: Whitelist this server's IP in Groww Trade API settings:")
        print(f"  → Groww app → Profile → Trade API → Cloud API Keys")
        print(f"  → Edit key → Add IP: {server_ip}")
        print(f"  → Re-run: python3 setup_auth.py")
else:
    print("  ✗ AUTH FAILED — Bot cannot trade. Check .env and re-run.")
print(SEP)
print()
