import io
import logging
import asyncio
from typing import Optional
from telegram import Bot

# Basic logging setup
logger = logging.getLogger(__name__)

class TelegramAlerter:
    def __init__(self, bot_token: str, chat_id: str):
        """
        Initializes the Alerter with credentials provided by main.py
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        # Initialize the actual Telegram Bot object using the token
        try:
            self._bot = Bot(token=self.bot_token)
        except Exception as e:
            logger.error(f"Failed to initialize Telegram Bot: {e}")
            self._bot = None

    def _is_ready(self) -> bool:
        """Checks if both credentials and the bot object are present."""
        return bool(self.bot_token and self.chat_id and self._bot)

    def _send(
        self,
        text: str,
        image_buf: Optional[io.BytesIO] = None,
        parse_mode: str = "Markdown",
    ) -> bool:
        """
        Sends a message or photo to Telegram asynchronously.
        """
        if not self._is_ready():
            logger.warning("Telegram alerter not ready (missing token/chat_id) — alert skipped")
            return False

        try:
            async def _do_send():
                if image_buf:
                    image_buf.seek(0)
                    await self._bot.send_photo(
                        chat_id=self.chat_id,
                        photo=image_buf,
                        caption=text[:1024],
                        parse_mode=parse_mode,
                    )
                else:
                    await self._bot.send_message(
                        chat_id=self.chat_id,
                        text=text[:4096],
                        parse_mode=parse_mode,
                    )

            # Execution logic for Render/Background Workers
            try:
                # Check for an existing event loop
                loop = asyncio.get_running_loop()
                loop.create_task(_do_send()) 
                return True
            except RuntimeError: 
                # If no loop is running, create one
                asyncio.run(_do_send())
                return True

        except Exception as e:
            # Note: Ensure any custom timestamp functions are imported if needed
            logger.error(f"Telegram send failed: {e}", exc_info=True)
            return False

    def initialize(self) -> bool:
        """
        Used by main.py to verify the bot is ready to start scanning.
        """
        return self._is_ready()
