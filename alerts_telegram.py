# telegram_listener.py  (or paste into main.py)

import asyncio
import logging
import threading
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from alerts_telegram import TelegramAlerter   # your class

logger = logging.getLogger(__name__)

telegram_alerter: Optional[TelegramAlerter] = None

# ====================== COMMAND HANDLERS ======================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ *KingTrades NSE Momentum Bot is ONLINE!*\n\n"
        "Use /status for current bot & trading status.\n"
        "Use /help for all commands.",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Available commands:\n"
        "/start — Check if bot is alive\n"
        "/status — Show trading status & positions\n"
        "/help — This message\n\n"
        "Alerts will be sent automatically on signals, EOD, etc."
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if telegram_alerter:
        # You can extend this to call send_status if risk_manager is accessible
        await update.message.reply_text("📊 Fetching status... Check Render logs for full details.")
        # Example: telegram_alerter.send_status(risk_manager)  # pass risk_manager if needed
    else:
        await update.message.reply_text("⚠️ Telegram alerter not initialized.")

# ====================== START LISTENER ======================

def start_telegram_listener(bot_token: str, chat_id: str):
    """Start Telegram listener in background thread (non-blocking)."""
    global telegram_alerter

    telegram_alerter = TelegramAlerter(bot_token, chat_id)

    async def run_polling():
        app = (
            Application.builder()
            .token(bot_token)
            .concurrent_updates(True)
            .build()
        )

        # Register handlers
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("status", status_command))

        logger.info("Starting Telegram long polling...")

        await app.initialize()
        await app.start()
        await app.updater.start_polling(
            poll_interval=1.0,
            timeout=30,
            drop_pending_updates=True,
            allowed_updates=["message"]
        )

        # Keep running
        await asyncio.Event().wait()  # runs forever until stopped

    def run_in_background():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_polling())
        except Exception as e:
            logger.error(f"Telegram listener crashed: {e}", exc_info=True)
        finally:
            loop.close()

    # Start in daemon thread so it doesn't block Render shutdown
    thread = threading.Thread(target=run_in_background, daemon=True, name="TelegramListener")
    thread.start()

    logger.info("✅ Telegram listener started in background thread (commands enabled)")
    return thread
