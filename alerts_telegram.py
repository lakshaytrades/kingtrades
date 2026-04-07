import io
import logging
import asyncio
from typing import Optional

# Setup a basic logger if it's not defined elsewhere
logger = logging.getLogger(__name__)

class TelegramAlerter:
    def __init__(self, bot, chat_id):
        self._bot = bot
        self.chat_id = chat_id

    def _is_ready(self) -> bool:
        # Ensure this method exists or logic is handled
        return self._bot is not None

    def _send(
        self,
        text: str,
        image_buf: Optional[io.BytesIO] = None,
        parse_mode: str = "Markdown",
    ) -> bool:
        if not self._is_ready():
            logger.debug("Telegram alerter not ready — alert skipped")
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

            # Fire-and-forget logic
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_do_send()) 
                logger.debug(f"Telegram alert scheduled: {text[:100]}...")
                return True
            except RuntimeError: 
                # Fallback if no loop is running
                asyncio.run(_do_send())
                return True

        except Exception as e:
            # Note: Ensure format_ist_timestamp() is imported or defined
            logger.error(f"Telegram send failed: {e}", exc_info=True)
            return False
