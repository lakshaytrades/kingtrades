"""
telegram_control.py — Telegram bot for the DAILY UPSTOX TOKEN only. Nothing else.

Silent by design: it never messages you on its own — it only ANSWERS when you
send a token command. Commands:

  /token             refresh the Upstox token automatically (runs auto_login_totp.py)
  /settoken <x>      fallback: paste the redirect URL (?code=...) from a phone-browser
                     login, OR a raw access token — it exchanges/saves it to .env
  /help              this list

SECURITY
--------
* Only YOUR chat id (TELEGRAM_CHAT_ID) is obeyed; everyone else is ignored.
* Fixed command allowlist — nothing here executes arbitrary text as shell.
* The bot token lives in .env (chmod 600), never in code.

ONE-TIME SETUP (the last SSH you'll need)
-----------------------------------------
  1. On Telegram: talk to @BotFather -> /newbot -> copy the token.
     Then message your new bot once, and get your chat id from
     https://api.telegram.org/bot<TOKEN>/getUpdates  (the "chat":{"id": ...}).
  2. Add to .env:  TELEGRAM_BOT_TOKEN=...   TELEGRAM_CHAT_ID=...
  3. Start it now:          nohup python3 india/telegram_control.py >> logs/telegram_control.log 2>&1 &
     Survive reboots:       crontab -e  ->
       @reboot cd $HOME/kingtrades && nohup python3 india/telegram_control.py >> logs/telegram_control.log 2>&1 &
  4. Send /token to your bot whenever the day's token needs refreshing.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_ENV = _ROOT / ".env"
IST = ZoneInfo("Asia/Kolkata")
LOCK_FILE = _ROOT / "logs" / ".telegram_control.lock"


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV)
    except Exception:
        pass


def _now() -> str:
    return datetime.now(IST).strftime("%H:%M:%S IST")


def _pilot_pids() -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-f", "live_pilot.py"], capture_output=True,
                             text=True, timeout=10).stdout.split()
        return [int(p) for p in out if p.isdigit()]
    except Exception:
        return []


class Bot:
    def __init__(self, token: str, chat_id: str):
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.offset = 0

    def send(self, text: str):
        # Telegram hard-caps messages at 4096 chars — chunk long output
        for i in range(0, max(len(text), 1), 3900):
            try:
                requests.post(f"{self.api}/sendMessage",
                              json={"chat_id": self.chat_id, "text": text[i:i + 3900]},
                              timeout=15)
            except Exception as e:
                print(f"[{_now()}] send failed: {e}", flush=True)

    def poll(self):
        r = requests.get(f"{self.api}/getUpdates",
                         params={"offset": self.offset, "timeout": 50},
                         timeout=70)
        for u in r.json().get("result", []):
            self.offset = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message") or {}
            frm = str((msg.get("chat") or {}).get("id", ""))
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            if frm != self.chat_id:
                print(f"[{_now()}] IGNORED message from foreign chat {frm}", flush=True)
                continue
            yield text

    # ── commands (token only) ────────────────────────────────────────────────

    def handle(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()      # strip /cmd@BotName
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/help", "/start"):
            return ("Commands:\n"
                    "/token — auto-refresh the Upstox token\n"
                    "/settoken <url|token> — manual fallback (paste the redirect "
                    "URL from a phone-browser login, or a raw access token)\n"
                    "/run — fresh token + start LIVE trading (one step)")

        if cmd == "/token":
            self.send("refreshing token (takes ~15s) ...")
            try:
                r = subprocess.run([sys.executable, str(_HERE / "auto_login_totp.py")],
                                   cwd=_ROOT, capture_output=True, text=True, timeout=180)
                out = ((r.stdout or "") + (r.stderr or "")).strip()
            except subprocess.TimeoutExpired:
                out = "TIMEOUT after 180s"
            except Exception as e:
                out = f"ERROR: {e}"
            return (out or "(no output)")[-1500:]

        if cmd == "/settoken":
            if not arg:
                # no argument -> send the user THEIR login link + exact steps
                from urllib.parse import quote
                key = os.getenv("UPSTOX_API_KEY", "").strip()
                redir = os.getenv("UPSTOX_REDIRECT_URI", "").strip()
                if not key or not redir:
                    return "UPSTOX_API_KEY / UPSTOX_REDIRECT_URI missing in .env"
                url = ("https://api.upstox.com/v2/login/authorization/dialog"
                       f"?response_type=code&client_id={quote(key, safe='')}"
                       f"&redirect_uri={quote(redir, safe='')}")
                return ("Manual token — 3 steps:\n\n"
                        f"1. Tap this link and log in to Upstox:\n{url}\n\n"
                        "2. After login the browser lands on a page whose ADDRESS "
                        "contains ?code=... — copy that whole address (it may show "
                        "an error page; that's fine, only the address matters).\n\n"
                        "3. Send it back to me like:\n"
                        "/settoken https://...code=XXXX\n"
                        "(one space after /settoken)")
            return self._settoken(arg)

        if cmd == "/run":
            # run_pilot.sh refreshes the token itself first, then starts the pilot —
            # so this one command = "start trading with a fresh token".
            if _pilot_pids():
                return "pilot already RUNNING — nothing to do"
            if (_ROOT / "KILL").exists():
                (_ROOT / "KILL").unlink()           # explicit /run overrides a stale kill
            subprocess.Popen(["nohup", "bash", str(_HERE / "run_pilot.sh"), "live"],
                             cwd=_ROOT, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return ("starting: token refresh + LIVE pilot (₹5k cap). Give it ~60s. "
                    "It trades 9:30-14:00 IST and squares off 15:25 on its own.")

        return "unknown command — /help"

    def _settoken(self, arg: str) -> str:
        sys.path.insert(0, str(_HERE))
        import auto_login_totp as al
        if "code=" in arg:                          # redirect URL -> exchange the code
            code = (parse_qs(urlparse(arg).query).get("code") or [None])[0]
            if not code:
                return "couldn't find ?code= in that URL"
            r = requests.post(
                f"{al.API}/v2/login/authorization/token",
                data={"code": code,
                      "client_id": os.getenv("UPSTOX_API_KEY", ""),
                      "client_secret": os.getenv("UPSTOX_API_SECRET", ""),
                      "redirect_uri": os.getenv("UPSTOX_REDIRECT_URI", ""),
                      "grant_type": "authorization_code"},
                headers={"accept": "application/json",
                         "content-type": "application/x-www-form-urlencoded"},
                timeout=30)
            body = r.json()
            tok = body.get("access_token")
            if not tok:
                return f"exchange failed HTTP {r.status_code}: {str(body)[:200]}"
            al._save_token(tok)
            return f"token exchanged + saved ({tok[:10]}...)"
        if len(arg) > 60 and " " not in arg:        # looks like a raw access token
            al._save_token(arg)
            return f"token saved ({arg[:10]}...)"
        return "that looks like neither a redirect URL nor a token"


def main():
    _load_env()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env "
              "(see docstring for BotFather setup)")
        sys.exit(1)
    bot = Bot(token, chat)

    # single-instance lock (a second copy would double-answer every command)
    (_ROOT / "logs").mkdir(exist_ok=True)
    lk = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another telegram_control.py is already running — exiting")
        sys.exit(0)

    print(f"[{_now()}] telegram token bot ONLINE (chat {chat}) — silent until you message it",
          flush=True)
    while True:
        try:
            for text in bot.poll():
                print(f"[{_now()}] cmd: {text}", flush=True)
                bot.send(bot.handle(text))
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[{_now()}] poll error: {e} — retrying in 10s", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
