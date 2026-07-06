"""
telegram_control.py — run the whole pilot from your PHONE. No SSH after setup.

WHAT IT IS
----------
A tiny always-on Telegram listener on the VPS. You message your bot; it does the
work and replies. Everything you used to SSH in for is now a chat command:

  /status            bot running? open positions? today's P&L?
  /token             refresh the Upstox token (runs auto_login_totp.py)
  /settoken <x>      fallback: paste the redirect URL (?code=...) OR a raw access
                     token from your phone browser — it exchanges/saves it
  /start             start the LIVE pilot (safe if already running)
  /kill              EMERGENCY: flatten all positions + stop the bot
  /unkill            clear the kill switch so the bot can start again
  /restart           graceful: kill -> wait for flat -> clear -> start fresh
  /update            git pull the latest bot code
  /log [n]           last n lines of today's pilot log (default 25)
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
  4. Message /status to your bot. From now on, phone only.

Also usable as a one-shot notifier from shell scripts:
  python3 india/telegram_control.py --notify "token refreshed OK"
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
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
KILL_FILE = _ROOT / "KILL"
LOCK_FILE = _ROOT / "logs" / ".telegram_control.lock"
BRANCH = "claude/nse-momentum-groww-bot-hvkv9"


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV)
    except Exception:
        pass


def _now() -> str:
    return datetime.now(IST).strftime("%H:%M:%S IST")


def _pilot_log() -> Path:
    return _ROOT / "logs" / f"live_pilot_{datetime.now(IST):%Y-%m-%d}.log"


def _pilot_pids() -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-f", "live_pilot.py"], capture_output=True,
                             text=True, timeout=10).stdout.split()
        return [int(p) for p in out if p.isdigit()]
    except Exception:
        return []


def _sh(cmd: list[str], timeout: int = 120) -> str:
    """Run ONE allowlisted command (never user text) and return combined output."""
    try:
        r = subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True, timeout=timeout)
        return ((r.stdout or "") + (r.stderr or "")).strip() or f"(exit {r.returncode}, no output)"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s: {' '.join(cmd[:3])}..."
    except Exception as e:
        return f"ERROR: {e}"


def _tail(path: Path, n: int) -> str:
    if not path.exists():
        return f"(no log yet today: {path.name})"
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-n:]) or "(empty)"


class Bot:
    def __init__(self, token: str, chat_id: str):
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.offset = 0

    def send(self, text: str):
        # Telegram hard-caps messages at 4096 chars — chunk long logs
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

    # ── commands ─────────────────────────────────────────────────────────────

    def handle(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()      # strip /cmd@BotName
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/help", "/start_menu"):
            return ("Commands:\n"
                    "/status — bot + positions + P&L\n"
                    "/token — auto-refresh Upstox token\n"
                    "/settoken <url|token> — manual token fallback\n"
                    "/start — start LIVE pilot\n"
                    "/kill — flatten ALL + stop (emergency)\n"
                    "/unkill — clear kill switch\n"
                    "/restart — safe kill->flat->start\n"
                    "/update — pull latest bot code\n"
                    "/log [n] — last n log lines")

        if cmd == "/status":
            pids = _pilot_pids()
            head = (f"⏱ {_now()}\n"
                    f"pilot: {'RUNNING (pid ' + ','.join(map(str, pids)) + ')' if pids else 'NOT running'}\n"
                    f"kill-switch: {'ON (KILL file present)' if KILL_FILE.exists() else 'off'}\n")
            log = _pilot_log()
            if log.exists():
                keep = [l for l in log.read_text(errors="replace").splitlines()
                        if re.search(r"ENTER|EXIT|P&L|open=|REPORT|LIMIT|RECONCILE", l)]
                head += "recent:\n" + "\n".join(keep[-10:] or ["(no trades yet)"])
            else:
                head += "(no pilot log yet today)"
            return head

        if cmd == "/token":
            self.send("refreshing token (takes ~15s) ...")
            out = _sh([sys.executable, str(_HERE / "auto_login_totp.py")], timeout=180)
            return out[-1500:]

        if cmd == "/settoken":
            if not arg:
                return ("usage: /settoken <redirect-url-with-?code=...>  OR  "
                        "/settoken <access-token>")
            return self._settoken(arg)

        if cmd == "/start":
            if _pilot_pids():
                return "pilot already RUNNING — /status to see it"
            if KILL_FILE.exists():
                return "kill switch is ON — /unkill first"
            subprocess.Popen(["nohup", "bash", str(_HERE / "run_pilot.sh"), "live"],
                             cwd=_ROOT, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return "starting LIVE pilot (₹5k cap) — /status in a minute"

        if cmd in ("/kill", "/stop"):
            KILL_FILE.touch()
            return ("KILL engaged — pilot will flatten all positions and stop "
                    "within ~45s. /status to confirm, /unkill to re-enable.")

        if cmd == "/unkill":
            KILL_FILE.unlink(missing_ok=True)
            return "kill switch cleared — /start to run again"

        if cmd == "/restart":
            self.send("restarting: engaging KILL and waiting for a clean stop ...")
            KILL_FILE.touch()
            for _ in range(24):                     # up to ~2 min for flatten+exit
                if not _pilot_pids():
                    break
                time.sleep(5)
            still = _pilot_pids()
            KILL_FILE.unlink(missing_ok=True)
            if still:
                return (f"pilot still running (pid {still}) after 2 min — NOT force-killing "
                        f"(positions could be mid-flatten). Check /log.")
            subprocess.Popen(["nohup", "bash", str(_HERE / "run_pilot.sh"), "live"],
                             cwd=_ROOT, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return "clean stop confirmed — fresh LIVE pilot starting. /status in a minute."

        if cmd == "/update":
            out = _sh(["git", "pull", "--ff-only", "origin", BRANCH], timeout=90)
            return f"git pull:\n{out[-1200:]}\n\n(restart to run the new code: /restart)"

        if cmd == "/log":
            n = int(arg) if arg.isdigit() else 25
            return _tail(_pilot_log(), min(n, 80))

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
            return f"token exchanged + saved ({tok[:10]}...). /start when ready."
        if len(arg) > 60 and " " not in arg:        # looks like a raw access token
            al._save_token(arg)
            return f"token saved ({arg[:10]}...). /start when ready."
        return "that looks like neither a redirect URL nor a token"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", type=str, default=None,
                    help="send one message and exit (for shell scripts)")
    args = ap.parse_args()
    _load_env()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env "
              "(see docstring for BotFather setup)")
        sys.exit(0 if args.notify else 1)           # notify mode: soft-fail
    bot = Bot(token, chat)

    if args.notify is not None:
        bot.send(args.notify)
        return

    # single-instance lock (a second copy would double-answer every command)
    (_ROOT / "logs").mkdir(exist_ok=True)
    lk = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another telegram_control.py is already running — exiting")
        sys.exit(0)

    print(f"[{_now()}] telegram control ONLINE (chat {chat})", flush=True)
    bot.send(f"🤖 control online {_now()} — /help for commands")
    while True:
        try:
            for text in bot.poll():
                print(f"[{_now()}] cmd: {text}", flush=True)
                bot.send(bot.handle(text))
        except KeyboardInterrupt:
            bot.send("control going offline (manual stop)")
            raise
        except Exception as e:
            print(f"[{_now()}] poll error: {e} — retrying in 10s", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
